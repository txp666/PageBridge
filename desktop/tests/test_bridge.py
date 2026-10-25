import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "数据 library"
        self.bridge = Path(__file__).resolve().parents[1] / "bridge.py"
        self.source = self.root / "原文 demo.pdf"
        with pymupdf.open() as pdf:
            for i in range(2):
                pdf.new_page().insert_text((30, 60), f"PageBridge demo page {i + 1}")
            pdf.save(self.source)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, action, **params):
        request = dict(action=action, dataRoot=str(self.data), **params)
        result = subprocess.run([sys.executable, "-B", "-u", str(self.bridge)],
                                input=(json.dumps(request, ensure_ascii=False) + "\n").encode(), capture_output=True, timeout=15)
        response = json.loads(result.stdout)
        self.assertEqual(result.stderr, b"")
        return response

    def test_import_render_export_restart_protocol(self):
        imported = self.call("import", path=str(self.source))["result"]
        self.assertEqual(imported["page_count"], 2)
        listed = self.call("list")["result"]
        self.assertEqual([d["id"] for d in listed], [imported["id"]])
        image = self.call("render", document_id=imported["id"], variant="original", page=1, width=800)["result"]
        self.assertEqual(image["page_count"], 2)
        self.assertTrue(Path(image["path"]).read_bytes().startswith(b"\x89PNG"))
        thumbnail = self.call("render", document_id=imported["id"], page=0, width=140)["result"]
        self.assertEqual(thumbnail["width"], 140)
        self.assertEqual(pymupdf.Pixmap(thumbnail["path"]).width, 140)
        exported = self.root / "导出.pdf"
        self.assertTrue(self.call("export", document_id=imported["id"], kind="original", destination=str(exported))["ok"])
        self.assertEqual(exported.read_bytes(), self.source.read_bytes())

    def test_folder_persistence_across_processes(self):
        doc = self.call("import", path=str(self.source))["result"]
        folder = self.call("create_folder", name="手册")["result"]
        self.call("move", document_id=doc["id"], folder_id=folder["id"])
        self.call("favorite", document_id=doc["id"], value=True)
        fetched = self.call("get", documentId=doc["id"])["result"]
        self.assertEqual(fetched["folder_id"], folder["id"])
        self.assertTrue(fetched["favorite"])

    @unittest.skipUnless(os.name != "nt" or os.environ.get("PAGEBRIDGE_TEST_WINDOWS_CREDENTIALS") == "1",
                         "Bridge profile writes use real Windows credentials; explicit interactive integration only")
    def test_profile_persistence_across_processes(self):
        profile = self.call("new_profile", provider="ollama")["result"]
        self.call("save_profile", profile=profile)
        profiles = self.call("profiles")["result"]
        self.assertEqual(profiles["active_id"], profile["id"])

    def test_short_lived_list_keeps_active_attempt(self):
        doc = self.call("import", path=str(self.source))["result"]
        attempt = self.call("new_attempt", document_id=doc["id"])["result"]
        self.call("mark_running", document_id=doc["id"], attempt_id=attempt["attempt_id"])
        self.assertEqual(self.call("list")["result"][0]["status"], "running")
        self.assertEqual(self.call("recover")["result"][0]["status"], "interrupted")

    def test_invalid_render_and_export_return_json_error(self):
        doc = self.call("import", path=str(self.source))["result"]
        self.assertFalse(self.call("render", document_id=doc["id"], page=99, width=800)["ok"])
        self.assertFalse(self.call("export", document_id=doc["id"], kind="original", destination=doc["input_path"])["ok"])

    @unittest.skipIf(os.name == "nt", "Mac-specific protection")
    def test_existing_mac_library_is_rejected_without_reading(self):
        self.data = Path.home() / "Library/Application Support/PageBridge"
        response = self.call("list")
        self.assertFalse(response["ok"])
        self.assertIn("Mac", response["error"])


if __name__ == "__main__":
    unittest.main()
