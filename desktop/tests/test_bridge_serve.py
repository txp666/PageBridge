"""Cross-request identity, freshness and framing for the local RPC session."""
from contextlib import contextmanager
import io
import json
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bridge
import pymupdf


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.data = self.root / "文档库"
        self.source = self.root / "公开测试.pdf"
        with pymupdf.open() as pdf:
            for index in range(2):
                pdf.new_page().insert_text((50, 50), f"Public synthetic page {index + 1}")
            pdf.save(self.source)
        self.process = None

    def tearDown(self):
        if self.process is not None:
            if self.process.poll() is None:
                self.process.kill()
            self.process.wait(timeout=10)
            for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
                stream.close()
        self.temporary.cleanup()

    def envelope(self, action, **values):
        return dict(action=action, protocolVersion=1, requestId=str(uuid.uuid4()),
                    dataRoot=str(self.data), **values)

    def start(self):
        self.process = subprocess.Popen([sys.executable, "-B", "-u", bridge.__file__],
                                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.responses = queue.Queue()

        def collect():
            while line := self.process.stdout.readline(1_048_577):
                self.responses.put(line)

        self.reader = threading.Thread(target=collect, daemon=True)
        self.reader.start()
        self.assertEqual(self.send(self.envelope("serve"))["result"], {"mode": "serve"})
        return self.process.pid

    def send(self, request):
        self.process.stdin.write((json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()
        raw = self.responses.get(timeout=20)
        response = json.loads(raw)
        self.assertEqual(response["requestId"], request["requestId"])
        self.assertEqual(response["protocolVersion"], 1)
        self.assertNotIn(b"fixture-private-key", raw)
        return response

    def test_reuses_process_and_observes_external_library_changes(self):
        pid = self.start()
        doc = self.send(self.envelope("import", path=str(self.source)))["result"]
        geometry = self.send(self.envelope("page_info", document_id=doc["id"]))["result"]
        self.assertEqual(geometry["page_count"], 2)
        self.assertEqual([p["page"] for p in geometry["pages"]], [0, 1])
        image = self.send(self.envelope("render", document_id=doc["id"], page=1, width=800))["result"]
        self.assertTrue(Path(image["path"]).read_bytes().startswith(b"\x89PNG"))
        self.send(self.envelope("reading", document_id=doc["id"], page=2))
        external = subprocess.run([sys.executable, "-B", "-u", bridge.__file__],
                                  input=json.dumps(dict(action="rename", dataRoot=str(self.data),
                                                        document_id=doc["id"], name="外部更新.pdf")).encode(),
                                  capture_output=True, timeout=20)
        self.assertTrue(json.loads(external.stdout)["ok"])
        latest = self.send(self.envelope("get", document_id=doc["id"]))["result"]
        self.assertEqual(latest["current_page"], 2)
        self.assertEqual(latest["name"], "外部更新.pdf")
        self.assertEqual(self.process.pid, pid)
        self.assertIsNone(self.process.poll())
        self.process.stdin.close()
        self.assertEqual(self.process.wait(timeout=10), 0)
        self.assertEqual(self.process.stderr.read(), b"")

    def test_application_errors_do_not_poison_next_request(self):
        self.start()
        doc = self.send(self.envelope("import", path=str(self.source)))["result"]
        self.assertFalse(self.send(self.envelope("render", document_id=doc["id"], page=99))["ok"])
        listed = self.send(self.envelope("list"))["result"]
        self.assertEqual([item["id"] for item in listed], [doc["id"]])

    def test_session_rejects_other_roots_and_nonlocal_actions_without_secret_echo(self):
        self.start()
        foreign = self.envelope("create_folder", name="should not exist")
        foreign["dataRoot"] = str(self.root / "other")
        self.assertFalse(self.send(foreign)["ok"])
        self.assertFalse((self.root / "other").exists())
        for action in ("probe", "save_profile", "profiles", "translate", "serve"):
            self.assertFalse(self.send(self.envelope(action, apiKey="fixture-private-key"))["ok"])
        self.assertEqual(self.send(self.envelope("list"))["result"], [])

    def test_invalid_or_overlong_frame_ends_session_before_following_write(self):
        for invalid in (b'{"bad":"\xff"}\n', b"x" * 65537 + b"\n", b'[]\n'):
            with self.subTest(frame=invalid[:12]):
                handshake = self.envelope("serve")
                after = self.envelope("create_folder", name="must not run")
                payload = json.dumps(handshake).encode() + b"\n" + invalid + json.dumps(after).encode() + b"\n"
                result = subprocess.run([sys.executable, "-B", "-u", bridge.__file__], input=payload,
                                        capture_output=True, timeout=20)
                replies = [json.loads(line) for line in result.stdout.splitlines()]
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(replies), 2)
                self.assertIsNone(replies[-1]["requestId"])
                self.assertFalse(replies[-1]["ok"])
                self.assertEqual(result.stderr, b"")
        state = self.data / "desktop-library.json"
        self.assertFalse(state.exists())

    def test_truncated_frame_does_not_execute_a_complete_json_without_newline(self):
        payload = (json.dumps(self.envelope("serve")) + "\n" +
                   json.dumps(self.envelope("create_folder", name="must not run"))).encode()
        result = subprocess.run([sys.executable, "-B", "-u", bridge.__file__], input=payload,
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.data / "desktop-library.json").exists())

    def test_response_bound_is_checked_before_any_bytes_are_written(self):
        output = io.BytesIO()
        with patch.object(bridge, "PROTOCOL_FD", None), patch.object(sys, "stdout", type("Out", (), {"buffer": output})()):
            with self.assertRaises(bridge.AppError):
                bridge.emit({"ok": True, "result": "x" * bridge.MAX_RESPONSE_BYTES})
        self.assertEqual(output.getvalue(), b"")

    def test_provider_network_request_holds_no_library_lock(self):
        locked = []

        @contextmanager
        def lock(_):
            locked.append(True)
            try:
                yield
            finally:
                locked.pop()

        class Services:
            def __init__(self, _):
                self.unused = None

        def probe(profile, key):
            self.assertEqual(locked, [])
            self.assertEqual(key, "fixture-private-key")
            return {"connected": True}

        with patch.object(bridge, "library_lock", lock), patch.object(bridge, "ServiceStore", Services), patch.object(bridge, "probe_service", probe):
            self.assertEqual(bridge.dispatch(dict(action="probe", profile={"id": "fixture"},
                                                  apiKey="fixture-private-key"), self.data), {"connected": True})


if __name__ == "__main__":
    unittest.main()
