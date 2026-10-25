import copy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from pagebridge.core import AppError, DocumentStore, sha256


class FolderManagementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.store = DocumentStore(self.root / "library")
        self.folder = self.store.create_folder("阅读示例")
        self.other = self.store.create_folder("Reports")

    def tearDown(self):
        self.temporary.cleanup()

    def document(self, name):
        path = self.root / (name + ".pdf")
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((30, 40), name)
            pdf.save(path)
        return self.store.import_pdf(path)

    def test_rename_preserves_identity_documents_and_reloads(self):
        document = self.document("first")
        self.store.move_to_folder(document["id"], self.folder["id"])
        before = self.store.documents()
        updated = self.store.rename_folder(self.folder["id"], "  研究记录  ")
        self.assertEqual(updated, {"id": self.folder["id"], "name": "研究记录"})
        self.assertEqual(self.store.documents(), before)
        updated["name"] = "external mutation"
        reloaded = DocumentStore(self.store.root)
        self.assertEqual(reloaded.folders()[0]["name"], "研究记录")
        self.assertEqual(reloaded.documents(), before)
        self.assertEqual(self.store.rename_folder(self.folder["id"], "研究记录")["id"], self.folder["id"])

    def test_invalid_and_duplicate_rename_leave_disk_unchanged(self):
        before = self.store.path.read_bytes()
        for value in ("", "  ", "x" * 101, "bad\nname", "bad\x00name", None, 123, "reports"):
            with self.subTest(value=value), self.assertRaises(AppError):
                self.store.rename_folder(self.folder["id"], value)
            self.assertEqual(self.store.path.read_bytes(), before)

    def test_delete_unfiles_documents_and_preserves_files_and_state(self):
        first, second, third = [self.document(name) for name in ("first", "second", "third")]
        self.store.move_to_folder(first["id"], self.folder["id"])
        self.store.move_to_folder(second["id"], self.folder["id"])
        self.store.move_to_folder(third["id"], self.other["id"])
        self.store.set_favorite(first["id"], True)
        self.store.trash(second["id"])
        original = Path(first["input_path"])
        translated = original.parent / "translated.pdf"
        translated.write_bytes(original.read_bytes())
        self.store._change(first["id"], lambda d: d.update(translated_path=str(translated), status="running"))
        before = self.store.documents()
        hashes = {str(p): sha256(p) for p in self.store.root.rglob("*.pdf")}
        self.assertEqual(self.store.delete_folder(self.folder["id"]), {"deleted_id": self.folder["id"]})
        expected = copy.deepcopy(before)
        for document in expected:
            if document["folder_id"] == self.folder["id"]:
                document["folder_id"] = None
        self.assertEqual(self.store.documents(), expected)
        self.assertEqual(self.store.folders(), [self.other])
        self.assertEqual({str(p): sha256(p) for p in self.store.root.rglob("*.pdf")}, hashes)
        reloaded = DocumentStore(self.store.root, recover_interrupted=False)
        self.assertEqual(reloaded.documents(), expected)
        self.assertEqual(reloaded.folders(), [self.other])

    def test_missing_folder_does_not_change_library(self):
        before = self.store.path.read_bytes()
        identifier = str(uuid.uuid4())
        with self.assertRaises(AppError):
            self.store.rename_folder(identifier, "new")
        with self.assertRaises(AppError):
            self.store.delete_folder(identifier)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_failed_atomic_write_keeps_memory_and_disk_unchanged(self):
        document = self.document("first")
        self.store.move_to_folder(document["id"], self.folder["id"])
        disk, folders, documents = self.store.path.read_bytes(), self.store.folders(), self.store.documents()
        for operation in (lambda: self.store.rename_folder(self.folder["id"], "new"),
                          lambda: self.store.delete_folder(self.folder["id"])):
            with patch("pagebridge.core.atomic_json", side_effect=OSError("disk unavailable")), self.assertRaises(OSError):
                operation()
            self.assertEqual(self.store.path.read_bytes(), disk)
            self.assertEqual(self.store.folders(), folders)
            self.assertEqual(self.store.documents(), documents)


if __name__ == "__main__":
    unittest.main()
