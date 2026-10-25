import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from pagebridge.core import AppError, DocumentStore, _result_staging_directory, atomic_json, sha256


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

    def result(self, doc, wrong_pages=False):
        attempt, directory = self.store.new_attempt(doc["id"])
        self.store.mark_running(doc["id"], attempt)
        result = directory / "result"
        result.mkdir()
        mono = pdf_file(result / "translated.pdf")
        dual = pdf_file(result / "bilingual.pdf", 1 if wrong_pages else 2)
        manifest = dict(protocolVersion=1, jobId="job-1", attemptId=attempt, sourceSHA256=doc["input_sha256"], sourcePages=1,
                        translatedSHA256=sha256(mono), bilingualSHA256=sha256(dual),
                        qualityGuard=dict(guardInstalled=True, layoutGuard={"guardInstalled": True},
                                          preparedParagraphs=1, validatedParagraphs=1, unresolvedCount=0))
        atomic_json(result / "manifest.json", manifest)
        return attempt, dict(type="result", jobId="job-1", attemptId=attempt, translatedPath=str(mono), bilingualPath=str(dual))

    def test_import_copy_dedup_and_independent_storage(self):
        first = self.imported()
        second = self.imported()
        self.assertEqual(first["id"], second["id"])
        self.assertNotEqual(first["input_path"], str(self.source))
        self.assertEqual(sha256(first["input_path"]), sha256(self.source))
        self.assertEqual(DocumentStore(self.store.root).documents(), [first])
        first["name"] = "changed outside store"
        self.assertEqual(self.store.get(first["id"])["name"], self.source.name)

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

    def test_page_info_rejects_missing_translation_and_invalid_variant(self):
        doc = self.imported()
        with self.assertRaisesRegex(AppError, "译文"):
            self.store.page_info(doc["id"], "translated")
        with self.assertRaisesRegex(AppError, "变体"):
            self.store.page_info(doc["id"], "../../outside.pdf")
        attempt, event = self.result(doc)
        self.store.commit_result(doc["id"], attempt, event)
        self.assertEqual(self.store.page_info(doc["id"], "translated")["page_count"], 1)

    def test_invalid_pdf_rolls_back_new_directory(self):
        self.source.write_text("not a pdf")
        with self.assertRaises(AppError):
            self.imported()
        self.assertEqual(self.store.documents(), [])
        self.assertEqual(list((self.store.root / "documents").iterdir()), [])

    def test_folders_favorite_trash_restore_persist(self):
        doc = self.imported()
        folder = self.store.create_folder("报告")
        self.store.move_to_folder(doc["id"], folder["id"])
        self.store.set_favorite(doc["id"], True)
        self.store.trash(doc["id"])
        self.store.trash(doc["id"], False)
        restored = DocumentStore(self.store.root).get(doc["id"])
        self.assertEqual(restored["folder_id"], folder["id"])
        self.assertTrue(restored["favorite"])
        self.assertFalse(restored["trashed"])
        with self.assertRaises(AppError):
            self.store.create_folder("报告")

    def test_busy_mutations_rejected_and_short_lived_reader_does_not_recover(self):
        doc = self.imported()
        attempt, _ = self.store.new_attempt(doc["id"])
        self.store.mark_running(doc["id"], attempt)
        with self.assertRaises(AppError):
            self.store.trash(doc["id"])
        self.assertEqual(DocumentStore(self.store.root, recover_interrupted=False).get(doc["id"])["status"], "running")
        self.assertEqual(DocumentStore(self.store.root).get(doc["id"])["status"], "interrupted")

    def test_success_commit_and_safe_export(self):
        doc = self.imported()
        attempt, event = self.result(doc)
        updated = self.store.commit_result(doc["id"], attempt, event)
        self.assertEqual(updated["status"], "completed")
        destination = self.root / "export.pdf"
        self.store.export_pdf(doc["id"], "translated", destination)
        self.assertEqual(sha256(destination), sha256(updated["translated_path"]))
        with self.assertRaises(AppError):
            self.store.export_pdf(doc["id"], "translated", self.store.root / "bad.pdf")

    def test_changed_source_and_wrong_page_count_rejected(self):
        doc = self.imported()
        attempt, event = self.result(doc, wrong_pages=True)
        with self.assertRaisesRegex(AppError, "页数"):
            self.store.commit_result(doc["id"], attempt, event)
        self.store.finish_failure(doc["id"], "failed")
        attempt, event = self.result(doc)
        Path(doc["input_path"]).write_bytes(b"changed")
        with self.assertRaisesRegex(AppError, "原文"):
            self.store.commit_result(doc["id"], attempt, event)

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

    def test_escape_and_bad_manifest_rejected(self):
        doc = self.imported()
        attempt, event = self.result(doc)
        escaped = dict(event, translatedPath=str(self.source))
        with self.assertRaisesRegex(AppError, "路径"):
            self.store.commit_result(doc["id"], attempt, escaped)
        manifest_path = Path(event["translatedPath"]).parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["qualityGuard"]["unresolvedCount"] = 1
        atomic_json(manifest_path, manifest)
        with self.assertRaisesRegex(AppError, "清单"):
            self.store.commit_result(doc["id"], attempt, event)

    def test_failed_commit_does_not_overwrite_previous_result(self):
        doc = self.imported()
        attempt, event = self.result(doc)
        previous = self.store.commit_result(doc["id"], attempt, event)
        attempt2, event2 = self.result(previous)
        def fail_library_save(path, value):
            if Path(path) == self.store.path:
                raise OSError("disk full")
            return atomic_json(path, value)

        with patch("pagebridge.core.atomic_json", side_effect=fail_library_save):
            with self.assertRaises(OSError):
                self.store.commit_result(doc["id"], attempt2, event2)
        self.assertEqual(self.store.get(doc["id"])["translated_path"], previous["translated_path"])
        self.assertFalse((self.store.root / "documents" / doc["id"] / "results" / attempt2).exists())
        failed = self.store.finish_failure(doc["id"], "部分段落未通过译文完整性校验")
        self.assertIn("上次成功", failed["last_error"])

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

    def test_manifest_job_identity_rejected(self):
        doc = self.imported()
        attempt, event = self.result(doc)
        event["jobId"] = "wrong-job"
        with self.assertRaisesRegex(AppError, "清单"):
            self.store.commit_result(doc["id"], attempt, event)


if __name__ == "__main__":
    unittest.main()
