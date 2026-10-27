"""Known Basic/ONNX OCR capability, without content-scoring heuristics."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import structured_reading as reading


class Events:
    def __init__(self):
        self.items = []

    def emit(self, kind, **fields):
        self.items.append(dict(type=kind, **fields))


class ArabicOCRCapabilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.pdf"
        self.request = {"jobId": "arabic-capability", "attemptId": "new", "inputPath": str(self.source),
                        "outputDirectory": str(self.root / "output"), "sourceLanguage": "ar", "targetLanguage": "zh",
                        "service": {"kind": "openai", "provider": "custom", "baseURL": "https://example.test/v1",
                                    "apiKey": "fixture-secret", "model": "fixture", "timeoutSeconds": 10, "concurrency": 2}}
        self.original_client = httpx.AsyncClient

    def make_pdf(self, kinds):
        with pymupdf.open() as pdf:
            for kind in kinds:
                page = pdf.new_page(width=300, height=200)
                if kind == "native":
                    # The bundled shaping font extracts Arabic presentation forms.
                    page.insert_htmlbox(pymupdf.Rect(20, 20, 280, 100), '<p dir="rtl">مرحبا</p>')
                    page.insert_text((20, 150), "English text around the Arabic letter is allowed.")
                elif kind == "scan":
                    with pymupdf.open() as image_pdf:
                        image_page = image_pdf.new_page(width=300, height=200)
                        image_page.insert_htmlbox(pymupdf.Rect(20, 20, 280, 100), '<p dir="rtl">مرحبا</p>')
                        png = image_page.get_pixmap().tobytes("png")
                    page.insert_image(page.rect, stream=png)
                elif kind == "digits":
                    page.insert_htmlbox(pymupdf.Rect(20, 20, 280, 100), '<p dir="rtl">١٢٣،</p>')
                else:
                    page.insert_text((20, 50), "Latin-only text layer.")
            pdf.save(self.source)

    def mock_client(self, calls):
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, request=request, json={"choices": [
                {"finish_reason": "stop", "message": {"content": "测试译文"}}]})
        return patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs:
                            self.original_client(transport=httpx.MockTransport(handler), **kwargs))

    async def fake_parser(self, command, **kwargs):
        number = int(command[command.index("--page") + 1])
        folder = Path(command[command.index("--output") + 1]); folder.mkdir()
        raw = {"pages": [{"page_idx": number - 1, "blocks": [
            {"type": "text", "bbox": [0.1, 0.1, 0.8, 0.4], "content": "One parsed paragraph."}]}]}
        (folder / "middle_json.json").write_text(json.dumps(raw))
        return 0

    async def run_translation(self, request=None, parser=None):
        calls, events = [], Events()
        with (patch.object(reading, "ensure_runtime", new=AsyncMock(return_value=self.root)) as setup,
              patch.object(reading, "run_process", new=AsyncMock(side_effect=parser or self.fake_parser)) as parse,
              self.mock_client(calls)):
            await reading.translate(request or self.request, events)
        document = json.loads(Path(events.items[-1]["readingPath"]).read_text())
        return document, setup, parse, calls

    async def test_arabic_scan_skips_runtime_parser_and_model_and_keeps_empty_fallback(self):
        self.make_pdf(["scan", "latin"])
        original = reading.sha256(self.source)
        document, setup, parse, calls = await self.run_translation()
        setup.assert_not_awaited(); parse.assert_not_awaited(); self.assertEqual(calls, [])
        self.assertEqual((document["status"], document["failedPages"], document["translatedBlocks"]), ("partial", 2, 0))
        for page in document["pages"]:
            self.assertEqual(page["status"], "source_fallback")
            self.assertEqual(page["blocks"], [])
            self.assertIn("Basic/ONNX", page["error"])
            self.assertIn("不支持阿拉伯语扫描页", page["error"])
            self.assertNotIn("backgroundImage", page)
        self.assertEqual(reading.sha256(self.source), original)

    async def test_native_arabic_presentation_forms_keep_existing_parser_and_model_path(self):
        self.make_pdf(["native"])
        with pymupdf.open(self.source) as pdf:
            self.assertTrue(any("\ufb50" <= c <= "\ufeff" for c in pdf[0].get_text()))
        document, setup, parse, calls = await self.run_translation()
        setup.assert_awaited_once(); parse.assert_awaited_once(); self.assertEqual(len(calls), 1)
        self.assertEqual(document["pages"][0]["blocks"][0]["status"], "translated")

    async def test_english_scan_keeps_existing_parser_and_model_path(self):
        self.make_pdf(["scan"])
        document, setup, parse, calls = await self.run_translation(dict(self.request, sourceLanguage="en"))
        setup.assert_awaited_once(); parse.assert_awaited_once(); self.assertEqual(len(calls), 1)
        self.assertEqual(document["status"], "completed")

    async def test_mixed_arabic_document_only_parses_page_with_native_letters(self):
        self.make_pdf(["scan", "native", "digits"])
        document, setup, parse, calls = await self.run_translation()
        setup.assert_awaited_once(); parse.assert_awaited_once(); self.assertEqual(len(calls), 1)
        command = parse.await_args.args[0]
        self.assertEqual(command[command.index("--page") + 1], 2)
        self.assertEqual([page["status"] for page in document["pages"]], ["source_fallback", "completed", "source_fallback"])

    async def test_retry_old_bad_ocr_removes_all_unsupported_overlays_without_calls(self):
        self.make_pdf(["scan", "latin"])
        pages = [{"pageNumber": number, "width": 300, "height": 200, "status": "completed",
                  "backgroundImage": "stale-image", "backgroundBlocks": [f"p{number:05d}-b00001"],
                  "decorations": [{"stale": True}], "blocks": [
                      {"id": f"p{number:05d}-b00001", "kind": "text", "sourceText": "bogus OCR",
                       "translatedText": "错误叠字", "status": "translated", "bbox": [20, 20, 200, 80]}]}
                 for number in (1, 2)]
        prior = {"schemaVersion": 1, "artifactType": reading.ARTIFACT_TYPE,
                 "sourceSHA256": reading.sha256(self.source), "pageCount": 2, "selectedPages": [1, 2],
                 "sourceLanguage": "ar", "targetLanguage": "zh", "pages": pages}
        path = self.root / "prior.json"; path.write_text(json.dumps(prior))
        old_hash = reading.sha256(path)
        request = dict(self.request, readingPath=str(path), retryBlockIds=["p00001-b00001"])
        document, setup, parse, calls = await self.run_translation(request)
        setup.assert_not_awaited(); parse.assert_not_awaited(); self.assertEqual(calls, [])
        self.assertEqual(document["failedPages"], 2)
        for page in document["pages"]:
            self.assertEqual(page["blocks"], [])
            self.assertNotIn("backgroundImage", page)
            self.assertNotIn("decorations", page)
        self.assertEqual(reading.sha256(path), old_hash)

    async def test_real_unicef_selected_pages_have_no_supported_native_arabic_layer(self):
        source = Path(__file__).resolve().parents[2] / "validation/representative20-20261027/corpus/20-unicef-child-rights-ar.pdf"
        if not source.exists():
            self.skipTest("Public validation PDF is not included in source checkout")
        selected = [7, 9, 12, 38, 46]
        self.assertEqual(reading.unsupported_arabic_ocr_pages(source, selected, "ar"), set(selected))


if __name__ == "__main__":
    unittest.main()
