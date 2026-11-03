import asyncio
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import signal
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker


def request(action="probe"):
    return {"protocolVersion": 1, "jobId": "test-job", "attemptId": "test-attempt", "action": action,
            "service": {"kind": "openai", "baseURL": "http://127.0.0.1:12345/v1", "apiKey": "fixture-secret-do-not-log",
                        "model": "fixture", "timeoutSeconds": 10, "concurrency": 1}}


@contextmanager
def api_server(status=200, content="控制阀调节水流。", redirect=False, delay=None):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            if delay is not None:
                delay.wait(15)
            self.send_response(status)
            if redirect:
                self.send_header("Location", "http://127.0.0.1:1/leak")
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(json.dumps({"choices": [{"message": {"content": content}}]}).encode())
            except (BrokenPipeError, ConnectionResetError):
                pass
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_port, calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class WorkerTests(unittest.TestCase):
    def test_invalid_or_secret_urls_are_rejected(self):
        for url in ("http://remote.example/v1", "https://user:secret@example.com/v1", "https://example.com/v1?key=secret", "file:///tmp/api", "https://example.com/v1/chat/completions"):
            data = request()
            data["service"]["baseURL"] = url
            with self.assertRaises(worker.WorkerError):
                worker.parse_request(json.dumps(data).encode())

    def test_ollama_is_loopback_and_normalized(self):
        data = request()
        data["service"].update(kind="ollama", baseURL="http://[::1]:11434", apiKey="")
        result = worker.parse_request(json.dumps(data).encode())
        self.assertEqual(result["service"]["baseURL"], "http://[::1]:11434/v1")
        for base in ("http://localhost:11434", "http://localhost:11434/api", "http://localhost:11434/v1/"):
            data["service"]["baseURL"] = base
            self.assertEqual(worker.parse_request(json.dumps(data).encode())["service"]["baseURL"], "http://localhost:11434/v1")
        data["service"]["baseURL"] = "https://remote.example"
        with self.assertRaises(worker.WorkerError):
            worker.parse_request(json.dumps(data).encode())

    def test_pages_reject_out_of_bounds_and_normalize(self):
        self.assertEqual(worker.page_selection("3-4, 1,3", 4), "1,3,4")
        for value in ("", "0", "2-1", "1-5", "1.5", "1;4"):
            with self.assertRaises(worker.WorkerError):
                worker.page_selection(value, 4)

    def test_single_terminal_and_sequence(self):
        output = io.StringIO()
        events = worker.Events(output, request())
        events.emit("ready")
        events.emit("error", message="Failed")
        events.emit("result")
        actual = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([item["sequence"] for item in actual], [1, 2])
        self.assertEqual(actual[-1]["type"], "error")

    def test_success_includes_verified_version(self):
        output = io.StringIO()
        events = worker.Events(output, request())
        events.engine_version = worker.installed_versions()
        events.emit("result", message="完成")
        self.assertEqual(json.loads(output.getvalue())["engineVersion"], worker.installed_versions())

    def test_probe_uses_real_response_and_no_provider_text_leak(self):
        with api_server(content="控制阀调节水流。 fixture-secret-do-not-log") as (port, calls):
            data = request()
            data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
            output = io.StringIO()
            asyncio.run(worker.probe(data, worker.Events(output, data)))
            self.assertEqual(calls[0][0], "/v1/chat/completions")
            self.assertNotIn("fixture-secret", output.getvalue())
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])["type"], "result")

    def test_probe_rejects_redirect_or_nontranslation(self):
        for status, content, redirect in ((302, "", True), (200, "I cannot translate", False)):
            with api_server(status, content, redirect) as (port, _):
                data = request()
                data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
                with self.assertRaises(worker.WorkerError):
                    asyncio.run(worker.probe(data, worker.Events(io.StringIO(), data)))

    def test_subprocess_protocol_and_no_secret_echo(self):
        data = request()
        data["service"]["baseURL"] = "https://user:fixture-secret-do-not-log@example.com"
        result = subprocess.run([sys.executable, str(Path(worker.__file__))], input=json.dumps(data) + "\n", text=True, encoding="utf-8", capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(lines[-1]["type"], "error")
        self.assertEqual(lines[-1]["jobId"], data["jobId"])

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX process-group signal test; Windows uses stdin cancellation")
    def test_sigterm_cancels_inflight_probe(self):
        release = threading.Event()
        with api_server(delay=release) as (port, _):
            data = request()
            data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
            process = subprocess.Popen([sys.executable, str(Path(worker.__file__))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            try:
                process.stdin.write(json.dumps(data) + "\n")
                process.stdin.flush()
                first = json.loads(process.stdout.readline())
                second = json.loads(process.stdout.readline())
                self.assertEqual(first["type"], "ready")
                self.assertEqual(second["stage"], "probe")
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 130)
                terminal = json.loads(stdout.splitlines()[-1])
                self.assertEqual(terminal["stage"], "cancelled")
                self.assertNotIn('"type": "result"', stdout)
                self.assertNotIn("fixture-secret", stdout + stderr)
            finally:
                release.set()
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()



class CrossPlatformProtocolTests(unittest.TestCase):
    def test_raw_input_preserves_coalesced_lines_and_bounds_input(self):
        with tempfile.TemporaryFile() as stream:
            stream.write(b'{"first":1}\n{"second":2}\n')
            stream.seek(0)
            reader = worker.ProtocolInput(stream.fileno())
            self.assertEqual(json.loads(reader.readline()), {"first": 1})
            self.assertEqual(json.loads(reader.readline()), {"second": 2})
            self.assertEqual(reader.readline(), b"")
            stream.seek(0)
            stream.write(b"x" * (worker.MAX_REQUEST_BYTES + 100))
            stream.seek(0)
            self.assertEqual(len(worker.ProtocolInput(stream.fileno()).readline()), worker.MAX_REQUEST_BYTES + 1)

    def test_windows_run_does_not_register_unavailable_signal_handlers(self):
        async def exercise():
            loop = asyncio.get_running_loop()
            data = request()
            with patch.object(worker.sys, "platform", "win32"), \
                    patch.object(loop, "add_signal_handler") as handler, \
                    patch.object(worker, "probe", new_callable=AsyncMock) as probe:
                await worker.run(data, worker.Events(io.StringIO(), data))
                handler.assert_not_called()
                probe.assert_awaited_once()
        asyncio.run(exercise())

    def test_control_cancel_ignores_wrong_attempt_then_cancels_inflight_probe(self):
        release = threading.Event()
        with api_server(delay=release) as (port, _):
            data = request()
            data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
            process = subprocess.Popen([sys.executable, str(Path(worker.__file__))], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            try:
                process.stdin.write(json.dumps(data) + "\n")
                process.stdin.flush()
                self.assertEqual(json.loads(process.stdout.readline())["type"], "ready")
                self.assertEqual(json.loads(process.stdout.readline())["stage"], "probe")
                control = {"protocolVersion": 1, "jobId": data["jobId"], "attemptId": "wrong-attempt", "action": "cancel"}
                process.stdin.write(json.dumps(control) + "\n")
                process.stdin.flush()
                time.sleep(0.1)
                self.assertIsNone(process.poll())
                control["attemptId"] = data["attemptId"]
                stdout, stderr = process.communicate(json.dumps(control) + "\n", timeout=10)
                self.assertEqual(process.returncode, 130)
                self.assertEqual(json.loads(stdout.splitlines()[-1])["stage"], "cancelled")
                self.assertNotIn('"type": "result"', stdout)
                self.assertNotIn("fixture-secret", stdout + stderr)
            finally:
                release.set()
                if process.poll() is None:
                    process.kill()
                    process.wait()

    def test_completed_worker_exits_while_host_keeps_stdin_open(self):
        with api_server() as (port, _):
            data = request()
            data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
            process = subprocess.Popen([sys.executable, str(Path(worker.__file__))], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
            try:
                process.stdin.write(json.dumps(data) + "\n")
                process.stdin.flush()
                events = [json.loads(process.stdout.readline()) for _ in range(3)]
                self.assertEqual(events[-1]["type"], "result")
                self.assertEqual(process.wait(timeout=10), 0)
                self.assertEqual(process.stderr.read(), "")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stdin.close()
                process.stdout.close()
                process.stderr.close()

# Reading-mode requests share the worker protocol.
def envelope(**fields):
    return dict(protocolVersion=1, jobId='reading-job', attemptId='reading-attempt', **fields)


def fixture_path(*parts):
    return str(Path(tempfile.gettempdir()).resolve().joinpath('pagebridge-reading-protocol', *parts))


def reading_request(**fields):
    data=envelope(action='translate', inputPath=fixture_path('source.pdf'), outputDirectory=fixture_path('reading-output'),
                  service=dict(kind='openai',baseURL='https://example.com/v1',model='model',apiKey='fixture-key',timeoutSeconds=30,concurrency=2))
    data.update(fields)
    return worker.parse_request(json.dumps(data).encode())


class ReadingProtocolTests(unittest.TestCase):
    def test_preparation_needs_no_service_or_credentials(self):
        result=worker.parse_request(json.dumps(envelope(action='prepare_reading')).encode())
        self.assertEqual(result['action'],'prepare_reading')
        self.assertNotIn('service',result)

    def test_retry_is_bound_to_reading_artifact_and_explicit_blocks(self):
        result=reading_request(readingPath=fixture_path('previous','reading.json'),retryBlockIds=['p0001-b0002'])
        self.assertEqual(result['retryBlockIds'],['p0001-b0002'])
        for changes in (dict(readingPath='relative.json',retryBlockIds=['p1-b2']),
                        dict(readingPath=fixture_path('reading.json'),retryBlockIds=[]),
                        dict(readingPath=fixture_path('reading.json'),retryBlockIds=['p1-b2','p1-b2']),
                        dict(readingPath=fixture_path('reading.json'),retryBlockIds=['../bad']),
                        dict(retryBlockIds=['p1-b2'])):
            with self.subTest(changes=changes),self.assertRaises(worker.WorkerError):
                reading_request(**changes)


# Offline provider routing/parameter regressions; never contact a cloud account.
class ProviderSettingsTests(unittest.TestCase):
    def service(self, provider="deepSeek", **updates):
        defaults = {"deepSeek": ("https://api.deepseek.com", "deepseek-flash"),
                    "alibaba": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
                    "siliconFlow": ("https://api.siliconflow.cn/v1", "deepseek-ai/DeepSeek-V4-Flash"),
                    "openAI": ("https://api.openai.com/v1", "gpt-6-luna"),
                    "custom": ("https://gateway.example/v1", "model")}
        endpoint, model = defaults[provider]
        value = {"kind": "openai", "provider": provider, "baseURL": endpoint, "model": model,
                 "apiKey": "fixture-not-real", "timeoutSeconds": 120, "concurrency": 2}
        value.update(updates)
        return value

    def test_legacy_provider_inference_never_rewrites_destination(self):
        for provider in ("deepSeek", "alibaba", "siliconFlow", "openAI", "custom"):
            raw = self.service(provider); del raw["provider"]
            result = worker.normalized_service(raw)
            self.assertEqual(result["provider"], provider)
            self.assertEqual(result["baseURL"], raw["baseURL"])
        raw = self.service("custom", baseURL="https://api.deepseek.com.evil.example/v1")
        del raw["provider"]
        self.assertEqual(worker.normalized_service(raw)["provider"], "custom")

    def test_spoofed_provider_cannot_apply_to_other_endpoint(self):
        for raw in (self.service(baseURL="https://other.example/v1"), (self.service() | {"provider": "unknown"}),
                    self.service(kind="ollama", baseURL="http://localhost:11434")):
            with self.assertRaises(worker.WorkerError):
                worker.normalized_service(raw)

    def test_no_control_characters_in_model_or_key(self):
        for update in ({"model": "bad\nmodel"}, {"apiKey": "fixture\tkey"}, {"apiKey": "fixture\rkey"}):
            with self.assertRaises(worker.WorkerError):
                worker.normalized_service(self.service(**update))

    def test_probe_modern_openai_uses_completion_limit(self):
        service = worker.normalized_service(self.service("openAI"))
        options = worker.provider_request_options(service, probe=True)
        self.assertEqual(options, {"max_completion_tokens": 1024, "reasoning_effort": "none"})
        custom = worker.normalized_service(self.service("custom", model="gpt-6-luna"))
        self.assertEqual(worker.provider_request_options(custom, probe=True), {"max_tokens": 1024})

    def test_known_presets_disable_thinking(self):
        for provider, expected in [("deepSeek", {"thinking": {"type": "disabled"}}), ("alibaba", {"enable_thinking": False}), ("siliconFlow", {"enable_thinking": False})]:
            service = worker.normalized_service(self.service(provider))
            self.assertEqual(worker.provider_request_options(service), expected)

    def test_ollama_disables_presence_penalty_and_custom_keeps_own_options(self):
        ollama = worker.normalized_service({"kind": "ollama", "baseURL": "http://localhost:11434/v1", "model": "fixture"})
        self.assertEqual(worker.provider_request_options(ollama), {"reasoning_effort": "none", "presence_penalty": 0, "max_tokens": 8192})
        custom = worker.normalized_service({"kind": "openai", "baseURL": "http://localhost:11434/v1", "model": "fixture", "apiKey": "fixture"})
        self.assertEqual(worker.provider_request_options(custom), {})

    def test_unknown_alibaba_models_keep_their_own_options(self):
        service = worker.normalized_service(self.service("alibaba", model="glm-5.3"))
        self.assertEqual(worker.provider_request_options(service), {})


if __name__ == "__main__":
    unittest.main()
