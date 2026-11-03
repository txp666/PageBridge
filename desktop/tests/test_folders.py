import copy
from pathlib import Path
import sys
import tempfile
import unittest

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

    def test_invalid_and_duplicate_rename_leave_disk_unchanged(self):
        before = self.store.path.read_bytes()
        for value in ("", "  ", "x" * 101, "bad\nname", "bad\x00name", None, 123, "reports"):
            with self.subTest(value=value), self.assertRaises(AppError):
                self.store.rename_folder(self.folder["id"], value)
            self.assertEqual(self.store.path.read_bytes(), before)
        # The duplicate-name check excludes the folder being renamed.
        self.assertEqual(self.store.rename_folder(self.folder["id"], " 阅读示例 "), self.folder)

    def test_delete_unfiles_documents_and_preserves_files_and_state(self):
        first, second, third = [self.document(name) for name in ("first", "second", "third")]
        self.store.move_to_folder(first["id"], self.folder["id"])
        self.store.move_to_folder(second["id"], self.folder["id"])
        self.store.move_to_folder(third["id"], self.other["id"])
        self.store.set_favorite(first["id"], True)
        self.store.trash(second["id"])
        self.store._change(first["id"], lambda d: d.update(status="running"))
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
        reloaded = DocumentStore(self.store.root)
        self.assertEqual(reloaded.documents(), expected)
        self.assertEqual(reloaded.folders(), [self.other])


if __name__ == "__main__":
    unittest.main()
