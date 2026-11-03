import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from pagebridge.core import AppError, DocumentStore, _result_staging_directory, sha256


def pdf_file(path, pages=1):
    with pymupdf.open() as pdf:
        for _ in range(pages):
            pdf.new_page().insert_text((30, 40), "PageBridge verification PDF")
        pdf.save(path)
    return path


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = pdf_file(self.root / "example 中文.pdf")
        self.store = DocumentStore(self.root / "library")

    def tearDown(self):
        self.temporary.cleanup()

    def imported(self):
        return self.store.import_pdf(self.source)

    def test_import_copy_dedup_and_independent_storage(self):
        first = self.imported()
        second = self.imported()
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["input_path"], str(self.source))
        self.assertEqual(sha256(first["input_path"]), sha256(self.source))
        self.assertEqual(DocumentStore(self.store.root).documents(), [first])

    def test_page_info_preserves_rotation_crop_and_mixed_sizes_without_rendering(self):
        mixed = self.root / "mixed-pages.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page(width=612, height=792)
            pdf.new_page(width=400, height=800).set_rotation(90)
            pdf.new_page(width=600, height=900).set_cropbox(pymupdf.Rect(0, 0, 300, 500))
            pdf.save(mixed)
        doc = self.store.import_pdf(mixed)
        with patch("pymupdf.Page.get_pixmap", side_effect=AssertionError("Geometry must not rasterize")):
            info = self.store.page_info(doc["id"])
        self.assertEqual(info, {"page_count": 3, "pages": [
            {"page": 0, "width": 612.0, "height": 792.0, "rotation": 0},
            {"page": 1, "width": 800.0, "height": 400.0, "rotation": 90},
            {"page": 2, "width": 300.0, "height": 500.0, "rotation": 0}]})

    def test_invalid_pdf_rolls_back_new_directory(self):
        self.source.write_text("not a pdf")
        with self.assertRaises(AppError):
            self.imported()
        self.assertEqual(self.store.documents(), [])
        self.assertEqual(list((self.store.root / "documents").iterdir()), [])

    def test_busy_mutations_rejected_and_short_lived_reader_does_not_recover(self):
        doc = self.imported()
        self.store.new_attempt(doc["id"])
        with self.assertRaises(AppError):
            self.store.trash(doc["id"])
        self.assertEqual(DocumentStore(self.store.root).get(doc["id"])["status"], "running")
        self.assertEqual(DocumentStore(self.store.root, recover_interrupted=True).get(doc["id"])["status"], "interrupted")

    def test_safe_export(self):
        doc = self.imported()
        destination = self.root / "export.pdf"
        self.store.export_pdf(doc["id"], "original", destination)
        self.assertEqual(sha256(destination), doc["input_sha256"])
        with self.assertRaises(AppError):
            self.store.export_pdf(doc["id"], "original", self.store.root / "bad.pdf")

    def test_windows_result_stage_uses_inherited_directory_creation(self):
        with patch("pagebridge.core.os.name", "nt"), patch("pagebridge.core.tempfile.mkdtemp", side_effect=AssertionError("Windows must not request the mode-0700 temp ACL")):
            first = _result_staging_directory(self.root)
            second = _result_staging_directory(self.root)
        self.assertNotEqual(first, second)
        self.assertEqual(first.parent, self.root)
        self.assertTrue(first.is_dir() and first.name.startswith(".commit-"))

    @unittest.skipIf(os.name == "nt", "POSIX directory mode")
    def test_posix_result_stage_keeps_private_mode(self):
        stage = _result_staging_directory(self.root)
        self.assertEqual(stage.stat().st_mode & 0o777, 0o700)

    def test_symlink_export_cannot_overwrite_input(self):
        doc = self.imported()
        link = self.root / "link.pdf"
        try:
            link.symlink_to(doc["input_path"])
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaises(AppError):
            self.store.export_pdf(doc["id"], "original", link)
        self.assertEqual(sha256(doc["input_path"]), doc["input_sha256"])

    def test_corrupt_library_preserved(self):
        self.store.path.write_text('{"version":99}')
        before = self.store.path.read_bytes()
        with self.assertRaises(AppError):
            DocumentStore(self.store.root)
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_redirected_attempt_directory_rejected(self):
        doc = self.imported()
        external = self.root / "external"
        external.mkdir()
        attempts = self.store.root / "documents" / doc["id"] / "attempts"
        try:
            attempts.symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(AppError, "重定向"):
            self.store.new_attempt(doc["id"])
        self.assertEqual(list(external.iterdir()), [])



if __name__ == "__main__":
    unittest.main()
