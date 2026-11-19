import asyncio
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2] / "engine")]
import pymupdf
from structured_reading import installation_lock
from pagebridge.core import AppError, DocumentStore, _result_staging_directory, sha256
from pagebridge import cache_storage


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

    def test_cache_cleanup_keeps_recoverable_readings_and_checks_running_jobs(self):
        document = self.imported()
        attempt, temporary = self.store.new_attempt(document["id"])
        (temporary / "raw.json").write_text("{}")
        with self.assertRaises(AppError):
            cache_storage.clear(self.store, "taskFiles")
        self.store.finish_failure(document["id"], "fixture")
        available = temporary.parent / str(uuid.uuid4()) / "result-saved"
        available.mkdir(parents=True)
        draft = available / "reading.json"
        draft.write_text("{}")
        redundant = temporary.parent / str(uuid.uuid4())
        saved = temporary.parent.parent / "results" / redundant.name
        for copy in (redundant / "result-saved", saved):
            copy.mkdir(parents=True)
            (copy / "reading.json").write_bytes(b"identical")
            (copy / "background-1.png").write_bytes(b"identical background")
        with patch.dict(os.environ, {"PAGEBRIDGE_MINERU_RUNTIME": str(self.root / "empty-mineru")}):
            usage = cache_storage.usage(self.store)
            result = cache_storage.clear(self.store, "taskFiles")
        self.assertEqual(usage["pendingReadings"]["count"], 1)
        self.assertEqual(usage["taskFiles"]["count"], 2)
        self.assertEqual(result, {"removed": 2, "failed": 0})
        self.assertFalse(temporary.exists())
        self.assertFalse(redundant.exists())
        self.assertTrue((saved / "background-1.png").is_file())
        self.assertTrue(draft.is_file())

    def test_mineru_cache_is_shared_with_the_parser_and_respects_the_install_lock(self):
        runtime = (self.root / "shared" / "mineru" / "4.0.12").resolve()
        model = runtime / "home" / "model.onnx"
        download = runtime / "uv-cache" / "package.whl"
        for fixture in (model, download):
            fixture.parent.mkdir(parents=True)
            fixture.write_bytes(b"fixture")

        async def while_installing():
            async with installation_lock(runtime):
                return await asyncio.to_thread(cache_storage.clear, self.store, "minerUDownloads")

        with patch.dict(os.environ, {"PAGEBRIDGE_MINERU_RUNTIME": ""}), \
                patch.object(cache_storage, "runtime_directory", return_value=runtime):
            usage = cache_storage.usage(self.store)
            self.assertEqual(usage["minerU"]["bytes"], 7)
            self.assertEqual(usage["minerU"]["location"], str(runtime))
            self.assertEqual(usage["minerUDownloads"]["bytes"], 7)
            self.assertEqual(asyncio.run(while_installing()), {"removed": 0, "failed": 1})
            self.assertEqual(cache_storage.clear(self.store, "minerUDownloads"), {"removed": 1, "failed": 0})
        self.assertTrue(model.is_file())
        self.assertFalse(download.exists())

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
