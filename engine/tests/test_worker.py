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
from unittest.mock import patch
from types import SimpleNamespace

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
    def test_child_shim_publishes_quality_before_finish_with_original_arguments(self):
        import translation_guard
        import pdf_links
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = SimpleNamespace(translation=SimpleNamespace(output=str(root)))
            input_pdf = root / "input.pdf"
            cancel, logs = object(), object()
            sent = []
            def send(event):
                self.assertTrue((root / "translation-quality.json").exists())
                sent.append(event)
            def upstream(actual_settings, actual_file, pipe, actual_cancel, actual_logs):
                self.assertIs(actual_settings, settings)
                self.assertEqual(actual_file, input_pdf)
                self.assertIs(actual_cancel, cancel)
                self.assertIs(actual_logs, logs)
                pipe.send({"type": "finish"})
            with patch.object(worker, "_UPSTREAM_TRANSLATE_WRAPPER", upstream), patch.object(translation_guard, "install"), patch.object(pdf_links, 'configure') as configure:
                worker.guarded_translate_wrapper(settings, input_pdf, SimpleNamespace(send=send), cancel, logs)
                configure.assert_called_once_with(input_pdf)
                self.assertTrue((root / 'link-mapping.json').is_file())
            self.assertEqual(sent, [{"type": "finish"}])

    def test_quality_gate_requires_subprocess_report_without_pending_paragraphs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "translation-quality.json"
            with self.assertRaises(worker.WorkerError):
                worker.read_quality_report(path)
            for content in ("invalid", "{}", '{"guardInstalled":true,"unresolvedCount":1}',
                            '{"guardInstalled":false,"unresolvedCount":0}'):
                path.write_text(content)
                with self.assertRaises(worker.WorkerError):
                    worker.read_quality_report(path)
            path.write_text('{"guardInstalled":true,"layoutGuard":{"guardInstalled":true},"unresolvedCount":0,"preparedParagraphs":2,"validatedParagraphs":2}')
            self.assertEqual(worker.read_quality_report(path)["unresolvedCount"], 0)

    def test_pdf_artifacts_include_corrupted_tag_variants(self):
        for text in ("<span id='2'>文本</span>", "<st\x03yle id='7'>文本", "<风格 id='3'>", "</place>", "<stylid='27'>", "<st yle id='3'>", '&lt;span id="9"&gt;文本', '＜span id="9"＞文本'):
            self.assertGreater(worker.artifact_marker_count(text), 0, text)
        self.assertEqual(worker.artifact_marker_count("0 < x < 5, Uo = (U1 + U2) / 2"), 0)

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

    def test_upstream_translation_client_does_not_follow_307(self):
        # Exercise the pinned translator's actual SDK client, without its
        # factory's extra "Hello" request or any language-model inference.
        from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
        import openai
        with api_server(status=307, redirect=True) as (port, calls):
            data = request()
            data.update(sourceLanguage="en", targetLanguage="zh-CN", pages=None)
            data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
            settings = worker.build_settings(data, Path(tempfile.gettempdir()))
            settings.validate_settings()
            translator = OpenAITranslator(settings, None)
            try:
                self.assertFalse(translator.client._client.follow_redirects)
                self.assertEqual(translator.client.timeout, 10.0)
                with self.assertRaises(openai.APIStatusError) as caught:
                    translator.client.chat.completions.create(model="fixture", messages=[{"role": "user", "content": "fixture-body-do-not-redirect"}])
                self.assertEqual(caught.exception.status_code, 307)
                self.assertEqual(len(calls), 1)
            finally:
                translator.client.close()

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

    def test_pdf_validation_rejects_escape_corruption_and_count(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid = root / "valid.pdf"
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((50, 50), "Test document")
                pdf.save(valid)
            self.assertEqual(worker.validate_pdf(valid, root, 1), valid.resolve())
            with self.assertRaises(worker.WorkerError):
                worker.validate_pdf(valid, root, 2)
            with self.assertRaises(worker.WorkerError):
                worker.validate_pdf(valid, root / "other", 1)

    def test_subprocess_protocol_and_no_secret_echo(self):
        data = request()
        data["service"]["baseURL"] = "https://user:fixture-secret-do-not-log@example.com"
        result = subprocess.run([sys.executable, str(Path(worker.__file__))], input=json.dumps(data) + "\n", text=True, capture_output=True, timeout=20)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("fixture-secret", result.stdout + result.stderr)
        lines = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(lines[-1]["type"], "error")
        self.assertEqual(lines[-1]["jobId"], data["jobId"])

    def test_sigterm_cancels_inflight_probe(self):
        release = threading.Event()
        with api_server(delay=release) as (port, _):
            data = request()
            data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
            process = subprocess.Popen([sys.executable, str(Path(worker.__file__))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
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

    def test_sigterm_cancels_actual_translation_subprocess(self):
        # Real official engine process and real PDF, delayed local HTTP fixture.
        # This validates cancellation, not translation quality or model success.
        import pymupdf
        release = threading.Event()
        with tempfile.TemporaryDirectory() as directory, api_server(delay=release) as (port, calls):
            root = Path(directory)
            source = root / "original.pdf"
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((60, 60), "The pressure sensor measures water pressure.")
                pdf.save(source)
            source_hash = worker.sha256(source)
            data = request("translate")
            data.update(inputPath=str(source), outputDirectory=str(root / "output"), sourceLanguage="en", targetLanguage="zh-CN", pages=None)
            data["service"]["baseURL"] = f"http://127.0.0.1:{port}/v1"
            process = subprocess.Popen([sys.executable, str(Path(worker.__file__))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                process.stdin.write(json.dumps(data) + "\n")
                process.stdin.flush()
                deadline = time.monotonic() + 30
                while not calls and process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertTrue(calls, "Translation subprocess did not reach local fixture")
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 130)
                events = [json.loads(line) for line in stdout.splitlines()]
                self.assertEqual(events[-1]["stage"], "cancelled")
                self.assertFalse(any(event["type"] == "result" for event in events))
                self.assertFalse((root / "output" / ("result-" + data["attemptId"])).exists())
                self.assertEqual(worker.sha256(source), source_hash)
                self.assertNotIn("fixture-secret", stdout + stderr)
            finally:
                release.set()
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()


if __name__ == "__main__":
    unittest.main()
