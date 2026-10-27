"""Reject nontranslatable selected pages before loading any translation backend."""
import asyncio
import builtins
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker


class UpstreamReached(Exception):
    pass


class InputTextPreflightTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "mixed.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 40), "Readable first page.")
            # Raster content represents a scan without accidentally adding text.
            image = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 8, 8))
            image.clear_with(120)
            pdf.new_page().insert_image(pymupdf.Rect(40, 40, 200, 200), pixmap=image)
            pdf.new_page()
            pdf.new_page().insert_text((40, 40), "Readable final page.")
            pdf.save(self.source)
        self.output = self.root / "output"

    def request(self, pages, source=None):
        return dict(inputPath=str(source or self.source), outputDirectory=str(self.output),
                    attemptId="preflight-test", jobId="preflight-job", pages=pages)

    @contextmanager
    def intercept_upstream(self):
        actual_import = builtins.__import__
        reached = []

        def guarded_import(name, *args, **kwargs):
            if name == "pdf2zh_next.high_level":
                reached.append(name)
                raise UpstreamReached()
            return actual_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded_import), \
             patch.object(worker, "build_settings") as settings:
            yield reached, settings

    def reject_before_upstream(self, request, message):
        original = Path(request["inputPath"]).read_bytes()
        output = io.StringIO()
        with self.intercept_upstream() as (reached, settings):
            with self.assertRaisesRegex(worker.WorkerError, message):
                asyncio.run(worker.translate(request, worker.Events(output, request)))
            self.assertEqual(reached, [])
            settings.assert_not_called()
        self.assertFalse(self.output.exists())
        self.assertEqual(Path(request["inputPath"]).read_bytes(), original)
        stages = [json.loads(line)["stage"] for line in output.getvalue().splitlines()]
        self.assertEqual(stages, ["preflight"])

    def test_image_only_selection_is_rejected_even_when_other_pages_have_text(self):
        self.reject_before_upstream(self.request("2"), "所选页面没有可读取的文字")

    def test_blank_and_image_only_range_is_rejected(self):
        for selection in ("3", "2-3", "3,2,2"):
            with self.subTest(selection=selection):
                self.reject_before_upstream(self.request(selection), "所选页面没有可读取的文字")

    def test_mixed_ranges_and_whole_document_reach_backend_with_normalized_pages(self):
        for selection, expected in ((None, None), ("1", "1"), ("2,4", "2,4"),
                                    ("3-4, 1,1", "1,3,4"), ("1-4", "1,2,3,4")):
            with self.subTest(selection=selection):
                request = self.request(selection)
                output = io.StringIO()
                with self.intercept_upstream() as (reached, settings):
                    with self.assertRaises(UpstreamReached):
                        asyncio.run(worker.translate(request, worker.Events(output, request)))
                    self.assertEqual(reached, ["pdf2zh_next.high_level"])
                    settings.assert_not_called()
                self.assertEqual(request["pages"], expected)
                stages = [json.loads(line)["stage"] for line in output.getvalue().splitlines()]
                self.assertEqual(stages, ["preflight", "prepare"])
                self.assertEqual(list(self.output.iterdir()), [])

    def test_whole_scanned_document_keeps_clear_existing_message(self):
        scanned = self.root / "scanned.pdf"
        with pymupdf.open(self.source) as mixed, pymupdf.open() as pdf:
            pdf.insert_pdf(mixed, from_page=1, to_page=2)
            pdf.save(scanned)
        self.reject_before_upstream(self.request(None, scanned), "PDF 没有可读取的文字，暂不支持纯扫描件")

    def test_invalid_page_range_is_rejected_before_text_extraction(self):
        for selection in ("5", "0", "2-1", ""):
            with self.subTest(selection=selection), \
                 patch.object(pymupdf.Page, "get_text", side_effect=AssertionError("text extraction must follow page validation")):
                self.reject_before_upstream(self.request(selection), "页码")


if __name__ == "__main__":
    unittest.main()
