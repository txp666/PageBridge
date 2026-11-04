import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import structured_reading as reading
import mineru_bridge as bridge


class Events:
    def __init__(self):
        self.items = []
    def emit(self, kind, **fields):
        self.items.append(dict(type=kind, **fields))


def blocks_page(number=1):
    return {"pageNumber": number, "width": 612, "height": 792, "status": "completed", "blocks": [
        {"id": f"p{number:05d}-b00001", "kind": "text", "sourceText": "First complete paragraph.", "status": "preserved", "bbox": [10, 20, 100, 80]},
        {"id": f"p{number:05d}-b00002", "kind": "text", "sourceText": "Second complete paragraph.", "status": "preserved"},
        {"id": f"p{number:05d}-b00003", "kind": "formula", "sourceText": "x = y^2", "status": "preserved", "bbox": [10, 90, 100, 120]},
    ]}


class ReadingUnitTests(unittest.TestCase):
    def test_inline_formulas_have_distinct_tokens_and_only_actual_boxes(self):
        raw = {"pages": [{"page_idx": 0, "blocks": [{"type": "text", "bbox": [0, 0, 1, 1], "content": [
            {"type": "text", "content": "Compare "},
            {"type": "equation_inline", "content": "x", "bbox": [0.1, 0.2, 0.2, 0.3]},
            {"type": "text", "content": " with "},
            {"type": "equation_inline", "content": "x", "bbox": [0.3, 0.2, 0.4, 0.3]},
            {"type": "equation_inline", "content": "y"},
        ]}]}]}
        block = reading.normalized_page(raw, 1, 600, 800)["blocks"][0]
        self.assertEqual(block["sourceText"], "Compare $x$ with $x$$y$")
        first, second = block["inlineMath"]
        self.assertNotEqual(first["token"], second["token"])
        self.assertEqual(first["bbox"], [60, 160, 120, 240])
        self.assertEqual(block["sourceTemplate"], f"Compare {first['token']} with {second['token']}$y$")
        self.assertEqual(block, reading.normalized_page(raw, 1, 600, 800)["blocks"][0])

    def test_pinned_bridge_captures_real_spans_and_preserves_original_behavior(self):
        called = []
        def original(page_model_list, block_lines, page_size):
            called.append(page_model_list)
            if page_model_list == "fail":
                raise ValueError("upstream")
            return "original result"
        module = SimpleNamespace(_apply_block_content_and_line_metadata=original)
        spans = [SimpleNamespace(type="inline_equation", content="x", bbox=(10, 20, 30, 40)),
                 SimpleNamespace(type="text", content="not a formula", bbox=(40, 20, 90, 40))]
        with patch.dict(sys.modules, {"mineru.backend.analysis.pdf.text": SimpleNamespace(content=module)}):
            with bridge.capture_inline_geometry() as formulas:
                self.assertEqual(module._apply_block_content_and_line_metadata([], {0: [SimpleNamespace(spans=spans)]}, (100, 200)), "original result")
                self.assertEqual(formulas, [{"latex": "x", "bbox": [0.1, 0.1, 0.3, 0.2]}])
                with self.assertRaisesRegex(ValueError, "upstream"):
                    module._apply_block_content_and_line_metadata("fail", {}, (100, 200))
            self.assertIs(module._apply_block_content_and_line_metadata, original)
            module._apply_block_content_and_line_metadata = lambda changed_api: None
            with self.assertRaisesRegex(RuntimeError, "unsupported_inline_geometry_interface"):
                with bridge.capture_inline_geometry():
                    self.fail("An incompatible pinned interface must not silently lose geometry")
        self.assertEqual(called, [[], "fail"])

    def test_bridge_binds_repeated_latex_once_in_its_source_region(self):
        formula = lambda: {"type": "equation_inline", "content": "x"}
        first, second, third, unavailable = [formula() for _ in range(4)]
        data = {"pages": [{"blocks": [
            {"type": "text", "bbox": [0.5, 0, 1, 1], "content": [first, second]},
            {"type": "text", "bbox": [0, 0, 0.5, 1], "content": [third, unavailable]},
        ]}]}
        boxes = [[0.1, 0.1, 0.2, 0.2], [0.6, 0.1, 0.7, 0.2], [0.8, 0.1, 0.9, 0.2]]
        bridge.attach_inline_geometry(data, [{"latex": "x", "bbox": box} for box in boxes])
        self.assertEqual([first["bbox"], second["bbox"], third["bbox"]], [boxes[1], boxes[2], boxes[0]])
        self.assertNotIn("bbox", unavailable)

    def test_native_strokes_and_fill_edges_keep_curves_dashes_crop_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paths.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=300, height=200)
                page.draw_line((30, 40), (120, 40), color=(1, 0, 0), width=0.5, dashes="[3 2] 1")
                page.draw_rect((40, 60, 100, 100), color=(0, 0, 1), fill=(1, 1, 0), width=1)
                page.draw_bezier((30, 120), (70, 130), (90, 110), (120, 120), color=(0, 1, 0), width=0.25)
                page.draw_rect((130, 70, 180, 100), color=None, fill=(1, 0, 0))
                page.set_cropbox((20, 10, 280, 190)); page.set_rotation(90)
                pdf.save(path)
            before = reading.sha256(path)
            result = {}
            reading.add_decorations(path, 1, result)
            paths = [path for path in result["decorations"] if path.get("kind", "stroke") == "stroke"]
            boundaries = [path for path in result["decorations"] if path.get("kind") == "fill_boundary"]
            self.assertEqual(len(paths), 3)
            self.assertEqual(len(boundaries), 2)
            self.assertTrue(all(path["width"] == 0 and path["commands"][-1]["op"] == "Z" for path in boundaries))
            self.assertEqual(paths[0]["commands"], [{"op": "M", "points": [150, 10]}, {"op": "L", "points": [150, 100]}])
            self.assertEqual((paths[0]["width"], paths[0]["dash"], paths[0]["dashPhase"]), (0.5, [3, 2], 1))
            self.assertEqual(paths[0]["color"], [255, 0, 0])
            self.assertIn("Z", [command["op"] for command in paths[1]["commands"]])
            self.assertEqual(paths[2]["commands"][1], {"op": "C", "points": [60, 50, 80, 70, 70, 100]})
            self.assertEqual(reading.sha256(path), before)
            missing = {}
            reading.add_decorations(Path(folder) / "missing.pdf", 1, missing)
            self.assertEqual(missing, {})

    def test_fill_boundaries_restore_thin_frame_without_covering_interior(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "filled-frame.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=200, height=120)
                page.draw_rect((20, 20, 180, 90), color=None, fill=(0, 0.5, 1))
                page.draw_rect((20.75, 20.75, 179.25, 89.25), color=None, fill=(0.9, 1, 1))
                pdf.save(path)
            result = {}; reading.add_decorations(path, 1, result)
            self.assertEqual(len(result["decorations"]), 2)
            self.assertTrue(all(d["kind"] == "fill_boundary" for d in result["decorations"]))
            # Render exactly the client's fixed 1pt edge mask. Only original
            # pixels under this mask are restored; translated interior stays clear.
            with pymupdf.open() as mask:
                page = mask.new_page(width=200, height=120)
                page.draw_rect(page.rect, color=None, fill=(0, 0, 0))
                for decoration in result["decorations"]:
                    shape = page.new_shape(); start = previous = None
                    for command in decoration["commands"]:
                        if command["op"] == "M":
                            start = previous = tuple(command["points"])
                        elif command["op"] == "L":
                            current = tuple(command["points"]); shape.draw_line(previous, current); previous = current
                        elif command["op"] == "Z":
                            shape.draw_line(previous, start)
                    shape.finish(color=(1, 1, 1), width=1, closePath=False); shape.commit()
                pixels = page.get_pixmap(matrix=pymupdf.Matrix(8, 8), colorspace=pymupdf.csRGB)
                self.assertEqual(pixels.pixel(100 * 8, 60 * 8), (0, 0, 0))
                # The full .75pt bottom blue band is covered by the two true edges.
                self.assertTrue(all(pixels.pixel(100 * 8, y)[0] == 255 for y in range(714, 720)))

    def test_normalization_preserves_physical_page_coordinates_and_absence(self):
        raw = {"pages": [{"page_idx": 2, "blocks": [
            {"type": "paragraph_title", "bbox": [0.1, 0.2, 0.7, 0.3], "content": "Heading"},
            {"type": "index", "bbox": [0.1, 0.3, 0.9, 0.5], "content": [
                {"type": "text", "content": [{"type": "text", "content": "First entry"}, {"type": "text", "content": " 7"}]},
                {"type": "list", "content": [{"type": "text", "content": "Nested entry 8"}]},
                {"type": "text", "content": "Last entry 9"}]},
            {"type": "table", "content": [{"type": "table_body", "content": "<table><tr><td>Label</td><td>3.0</td></tr></table><script>not text</script>"}]},
            {"type": "equation", "bbox": [0.1, 0.5, 0.5, 0.6], "content": "x=y"},
            {"type": "code", "bbox": [0.1, 0.6, 0.9, 0.8], "content": [
                {"type": "code_body", "content": "GET /index.html HTTP/1.1\nHost: example.org"}]},
            {"type": "code", "content": "if (x < 3) return x;"},
            {"type": "list", "content": [{"type": "text", "content": "No parent or child coordinates"}]},
        ]}]}
        page = reading.normalized_page(raw, 3, 600, 800)
        self.assertEqual(page["pageNumber"], 3)
        self.assertEqual(page["blocks"][0]["bbox"], [60, 160, 420, 240])
        self.assertEqual(page["blocks"][1]["bbox"], [60, 240, 540, 400])
        self.assertEqual(page["blocks"][1]["kind"], "list")
        self.assertEqual(page["blocks"][1]["sourceText"], "First entry 7\nNested entry 8\nLast entry 9")
        self.assertEqual(page["blocks"][2]["sourceText"], "Label | 3.0 |")
        self.assertNotIn("html", page["blocks"][2])
        self.assertEqual(page["blocks"][3]["kind"], "formula")
        self.assertEqual(page["blocks"][4]["kind"], "code")
        self.assertEqual(page["blocks"][4]["sourceText"], "GET /index.html HTTP/1.1\nHost: example.org")
        self.assertEqual(page["blocks"][4]["status"], "preserved")
        self.assertEqual(page["blocks"][4]["bbox"], [60, 480, 540, 640])
        self.assertEqual(page["blocks"][5]["kind"], "code")
        self.assertEqual(page["blocks"][6]["kind"], "list")
        self.assertNotIn("bbox", page["blocks"][6])
        self.assertTrue(all(b["id"].startswith("p00003-") for b in page["blocks"]))
        with self.assertRaises(ValueError):
            reading.normalized_page(raw, 1, 600, 800)

    def test_range_and_empty_parse_are_explicit(self):
        self.assertEqual(reading.selected_pages("3,1-2", 4), [1, 2, 3])
        with self.assertRaises(reading.ReadingError):
            reading.selected_pages("5", 4)
        page = reading.normalized_page({"pages": [{"page_idx": 0, "blocks": []}]}, 1, 600, 800)
        self.assertEqual(page["status"], "source_fallback")
        self.assertEqual(page["blocks"], [])

    def test_parser_environment_isolated_from_app_and_credentials(self):
        secrets = {"PYTHONHOME": "app/python", "PYTHONPATH": "app/modules", "VIRTUAL_ENV": "app/venv",
                   "OPENAI_API_KEY": "secret", "SOME_API_TOKEN": "secret", "HF_TOKEN": "secret",
                   "AWS_SECRET_ACCESS_KEY": "secret", "MINERU_REMOTE_URL": "https://external.invalid"}
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, secrets):
            root = Path(folder).resolve()
            env = reading.runtime_environment(root)
        self.assertTrue(all(key not in env for key in secrets))
        self.assertEqual(env["MINERU_MODEL_SOURCE"], "local")
        self.assertEqual(env["HF_HUB_OFFLINE"], "1")
        self.assertEqual(Path(env["UV_PYTHON_INSTALL_DIR"]), root / "python")

    def test_model_integrity_detects_same_size_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            path = root / "weights.bin"; path.write_bytes(b"known")
            model = [{"path": "weights.bin", "bytes": 5, "sha256": hashlib.sha256(b"known").hexdigest()}]
            with patch.object(reading, "model_files", return_value=model):
                self.assertTrue(reading.models_available(root, verify=True))
                path.write_bytes(b"other")
                self.assertFalse(reading.models_available(root, verify=True))

    def test_background_sampling_uses_page_geometry_and_failure_is_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "colored.pdf"
            boxes = []
            with pymupdf.open() as pdf:
                for rotated in (False, True):
                    page = pdf.new_page(width=400, height=300)
                    page.draw_rect(pymupdf.Rect(50, 90, 350, 250), color=None, fill=(0.2, 0.6, 0.8))
                    page.insert_text((90, 150), "Source text remains unchanged.")
                    box = pymupdf.Rect(70, 110, 330, 230)
                    if rotated:
                        page.set_cropbox(pymupdf.Rect(20, 30, 380, 270))
                        page.set_rotation(90)
                        box = pymupdf.Rect(box.x0 - 20, box.y0 - 30, box.x1 - 20, box.y1 - 30) * page.rotation_matrix
                    boxes.append(list(box))
                pdf.save(path)
            before = reading.sha256(path)
            for number, box in enumerate(boxes, 1):
                page = {"blocks": [
                    {"kind": "text", "bbox": box},
                    {"kind": "table", "bbox": box},
                    {"kind": "list"},
                ]}
                reading.add_background_colors(path, number, page)
                self.assertEqual(page["blocks"][0]["backgroundColor"], [51, 153, 204])
                self.assertNotIn("backgroundColor", page["blocks"][1])
                self.assertNotIn("backgroundColor", page["blocks"][2])
            self.assertEqual(reading.sha256(path), before)
            fallback = {"blocks": [{"kind": "heading", "bbox": boxes[0]}]}
            reading.add_background_colors(Path(folder) / "missing.pdf", 1, fallback)
            self.assertEqual(fallback["blocks"][0]["backgroundColor"], [255, 255, 255])

    def test_native_index_lines_keep_indents_numbers_and_font_spans_together(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "index.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=600, height=300)
                page.insert_text((50, 70), "1.2.3", fontsize=10)
                page.insert_text((130, 70), "Risk Management", fontsize=10)
                page.insert_text((540, 70), "12", fontsize=10)
                x = 50
                for text, font in (("Categories for ", "helv"), ("GOVERN", "hebo"), (" function.", "helv")):
                    page.insert_text((x, 120), text, fontsize=10, fontname=font)
                    x += pymupdf.get_text_length(text, fontname=font, fontsize=10)
                page.insert_text((540, 120), "22", fontsize=10)
                page.insert_text((50, 170), "   8.1.  Character Encoding  . . . . . . . .   9", fontsize=10, fontname="cour")
                page.insert_text((50, 220), "Figures 2 and 3", fontsize=10)
                pdf.save(path)
            before = reading.sha256(path)
            raw = {"pages": [{"page_idx": 0, "blocks": [{"type": "index", "bbox": [0.05, 0.1, 0.95, 0.8],
                "content": [{"type": "text", "content": "Parser index text"}]}]}]}
            result = reading.normalized_page(raw, 1, 600, 300)
            reading.refine_list_geometry(path, 1, result)
            blocks = result["blocks"]
            titles = [block for block in blocks if not reading.is_numeric_fragment(block["sourceText"])]
            self.assertEqual([block["sourceText"] for block in titles],
                             ["Risk Management", "Categories for GOVERN function.", "Character Encoding", "Figures 2 and 3"])
            self.assertEqual([block["sourceText"] for block in blocks if reading.is_numeric_fragment(block["sourceText"])],
                             ["1.2.3", "12", "22", "8.1.", "9"])
            self.assertAlmostEqual(titles[0]["bbox"][0], 130)
            self.assertAlmostEqual(titles[2]["bbox"][0], 50 + len("   8.1.  ") * 6)
            self.assertAlmostEqual(titles[2]["bbox"][2], titles[2]["bbox"][0] + len("Character Encoding") * 6)
            self.assertTrue(all("_nativeListType" not in block and block.get("bbox") for block in blocks))
            self.assertEqual(len({block["id"] for block in blocks}), len(blocks))
            self.assertEqual(reading.sha256(path), before)

    def test_scanned_list_keeps_full_text_without_guessing_line_boxes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scan.pdf"
            with pymupdf.open() as pdf:
                pdf.new_page(width=600, height=300).draw_rect(pymupdf.Rect(50, 50, 500, 250), fill=(0.8, 0.9, 1))
                pdf.save(path)
            raw = {"pages": [{"page_idx": 0, "blocks": [{"type": "list", "bbox": [0.1, 0.2, 0.9, 0.8],
                "content": [{"type": "text", "content": "First complete item."},
                            {"type": "text", "content": "Second complete item."}]}]}]}
            result = reading.normalized_page(raw, 1, 600, 300)
            reading.refine_list_geometry(path, 1, result)
            self.assertEqual(len(result["blocks"]), 1)
            self.assertEqual(result["blocks"][0]["sourceText"], "First complete item.\nSecond complete item.")
            self.assertNotIn("bbox", result["blocks"][0])
            self.assertNotIn("_nativeListType", result["blocks"][0])


class ReadingFlowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.source = self.root / "input.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((72, 72), "Source page one.")
            pdf.new_page().insert_text((72, 72), "Source page two.")
            pdf.save(self.source)
        self.request = {"jobId": "job", "attemptId": "first", "inputPath": str(self.source),
                        "outputDirectory": str(self.root / "outputs"), "pages": "1", "sourceLanguage": "en", "targetLanguage": "zh",
                        "service": {"kind": "openai", "provider": "custom", "baseURL": "https://example.test/v1",
                                    "apiKey": "test-secret-never-persist", "model": "test", "timeoutSeconds": 10, "concurrency": 2}}
        self.original_client = httpx.AsyncClient

    async def asyncTearDown(self):
        self.folder.cleanup()

    def mock_client(self, handler):
        return patch.object(httpx, "AsyncClient", side_effect=lambda **kwargs: self.original_client(
            transport=httpx.MockTransport(handler), **kwargs))

    def response(self, request, text="完整中文译文。", finish="stop"):
        return httpx.Response(200, request=request, json={"choices": [{"finish_reason": finish, "message": {"content": text}}]})

    async def run_fixture(self, handler, pages=None, request=None):
        events = Events()
        with patch.object(reading, "parse_pages", new=AsyncMock(return_value=copy.deepcopy(pages or [blocks_page()]))), self.mock_client(handler):
            await reading.translate(request or self.request, events)
        result = events.items[-1]
        return result, json.loads(Path(result["readingPath"]).read_text()), events

    async def test_ignored_glossary_term_gets_one_revision_request(self):
        # A local model translated a keep-as-written title term anyway.
        calls = []
        def handler(request):
            payload = json.loads(request.content); calls.append(payload)
            if payload["messages"][-1]["role"] == "user" and payload["messages"][-1]["content"].startswith("Your translation"):
                return self.response(request, "第一段 complete 中文。")
            return self.response(request, "第一段完整中文。")
        page = blocks_page(); page["blocks"] = page["blocks"][:1]
        _, document, _ = await self.run_fixture(handler, pages=[page], request=dict(self.request, glossary="complete\n"))
        self.assertIn("complete → complete (keep", calls[0]["messages"][0]["content"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(document["pages"][0]["blocks"][0]["translatedText"], "第一段 complete 中文。")

    async def test_one_failed_block_keeps_other_translation_formula_and_code(self):
        calls = []
        def handler(request):
            payload = json.loads(request.content); calls.append(payload)
            return self.response(request, "" if payload["messages"][-1]["content"].startswith("Second") else "第一段中文。")
        before = reading.sha256(self.source)
        page = blocks_page()
        source_code = "GET /index.html HTTP/1.1\nHost: example.org"
        code_page = reading.normalized_page({"pages": [{"page_idx": 0, "blocks": [
            {"type": "code", "content": [{"type": "code_body", "content": source_code}]}
        ]}]}, 1, 612, 792)
        code = code_page["blocks"][0]
        code["id"] = "p00001-b00004"
        page["blocks"].append(code)
        page["blocks"].extend([
            {"id": "p00001-b00005", "kind": "text", "sourceText": "6", "status": "preserved"},
            {"id": "p00001-b00006", "kind": "list", "sourceText": "1.2.3", "status": "preserved"},
        ])
        result, document, events = await self.run_fixture(handler, pages=[page])
        self.assertEqual(result["status"], "partial")
        self.assertEqual((result["translatedBlocks"], result["failedBlocks"], result["preservedBlocks"]), (1, 1, 4))
        self.assertEqual(document["pageCount"], 2)
        self.assertEqual(document["selectedPages"], [1])
        blocks = document["pages"][0]["blocks"]
        self.assertEqual(blocks[0]["translatedText"], "第一段中文。")
        self.assertNotIn("translatedText", blocks[1])
        self.assertEqual(blocks[2]["sourceText"], "x = y^2")
        self.assertEqual(blocks[3]["kind"], "code")
        self.assertEqual(blocks[3]["sourceText"], source_code)
        self.assertEqual(blocks[3]["status"], "preserved")
        self.assertNotIn("translatedText", blocks[3])
        self.assertTrue(all(block["status"] == "preserved" and "translatedText" not in block for block in blocks[4:]))
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(source_code not in payload["messages"][-1]["content"] for payload in calls))
        self.assertEqual(before, reading.sha256(self.source))
        self.assertEqual(reading.sha256(result["readingPath"]), result["readingSHA256"])
        for path in Path(result["readingPath"]).parent.glob("*.json"):
            self.assertNotIn("test-secret-never-persist", path.read_text())
        self.assertEqual(sum(event["type"] == "result" for event in events.items), 1)

    async def test_block_retry_does_not_reparse_or_replace_other_blocks(self):
        def handler(request):
            text = json.loads(request.content)["messages"][-1]["content"]
            return self.response(request, "" if text.startswith("Second") else "原有第一段译文。")
        original, document, _ = await self.run_fixture(handler)
        retry = dict(self.request, attemptId="retry", readingPath=original["readingPath"], retryBlockIds=["p00001-b00002"])
        calls = []
        def recovered(request):
            calls.append(json.loads(request.content))
            return self.response(request, "修复的第二段译文。")
        events = Events()
        with patch.object(reading, "parse_pages", new=AsyncMock(side_effect=AssertionError("Retry must not parse"))), self.mock_client(recovered):
            await reading.translate(retry, events)
        result = events.items[-1]
        updated = json.loads(Path(result["readingPath"]).read_text())
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(calls), 1)
        self.assertEqual(updated["pages"][0]["blocks"][0], document["pages"][0]["blocks"][0])
        self.assertEqual(updated["pages"][0]["blocks"][1]["translatedText"], "修复的第二段译文。")
        self.assertEqual(json.loads(Path(original["readingPath"]).read_text()), document)

    async def test_failed_page_does_not_discard_other_page(self):
        pages = [{"pageNumber": 1, "width": 612, "height": 792, "status": "source_fallback", "error": "本页解析失败。", "blocks": []}, blocks_page(2)]
        result, document, _ = await self.run_fixture(lambda request: self.response(request), pages=pages, request=dict(self.request, pages=None))
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["failedPages"], 1)
        self.assertEqual(document["pages"][1]["blocks"][0]["status"], "translated")

    async def test_inline_math_complete_passage_translation_and_retry_keep_attachments(self):
        page = blocks_page()
        block = page["blocks"][0]
        block["sourceText"] = "Mean $x$ and variance $x$ belong to this complete paragraph."
        spans = [{"type": "text", "content": "Mean "},
                 {"type": "equation_inline", "content": "x", "bbox": [0.1, 0.1, 0.2, 0.2]},
                 {"type": "text", "content": " and variance "},
                 {"type": "equation_inline", "content": "x", "bbox": [0.3, 0.1, 0.4, 0.2]},
                 {"type": "text", "content": " belong to this complete paragraph."}]
        reading.add_inline_math(block, spans, 612, 792)
        tokens = [formula["token"] for formula in block["inlineMath"]]
        calls = []
        def handler(request):
            passage = json.loads(request.content)["messages"][-1]["content"]
            calls.append(passage)
            return self.response(request, "均值" + tokens[0] + "与方差" + tokens[1] + "属于此完整段落。" if passage.startswith("Mean") else "第二段。")
        result, document, _ = await self.run_fixture(handler, pages=[page])
        translated = document["pages"][0]["blocks"][0]
        self.assertEqual(calls[0], block["sourceTemplate"])
        self.assertEqual(translated["translatedText"], "均值$x$与方差$x$属于此完整段落。")
        self.assertEqual(translated["inlineMath"], block["inlineMath"])
        self.assertTrue(all(translated["translatedTemplate"].count(token) == 1 for token in tokens))
        retry = dict(self.request, attemptId="math-retry", readingPath=result["readingPath"], retryBlockIds=[block["id"]])
        with patch.object(reading, "parse_pages", new=AsyncMock(side_effect=AssertionError("No reparse"))), self.mock_client(handler):
            events = Events(); await reading.translate(retry, events)
        updated = json.loads(Path(events.items[-1]["readingPath"]).read_text())
        self.assertEqual(updated["pages"][0]["blocks"], document["pages"][0]["blocks"])

    async def test_missing_or_duplicated_math_token_fails_only_that_passage(self):
        page = blocks_page()
        block = page["blocks"][0]
        block.update(sourceText="Keep $x$ intact.", sourceTemplate="Keep [[PBM_example_0001]] intact.",
                     translatedTemplate="stale [[PBM_example_0001]]", translatedText="stale $x$",
                     inlineMath=[{"token": "[[PBM_example_0001]]", "latex": "x", "bbox": [10, 20, 30, 40]}])
        for mode, reply in (("missing", "完整文字但无公式"), ("duplicate", "[[PBM_example_0001]]和[[PBM_example_0001]]")):
            def handler(request):
                passage = json.loads(request.content)["messages"][-1]["content"]
                return self.response(request, reply if passage.startswith("Keep") else "第二段完成。")
            result, document, _ = await self.run_fixture(handler, pages=[page], request=dict(self.request, attemptId=mode))
            failed, successful, _ = document["pages"][0]["blocks"]
            self.assertEqual(result["status"], "partial")
            self.assertEqual((result["failedBlocks"], result["translatedBlocks"]), (1, 1))
            self.assertEqual(failed["status"], "source_fallback")
            self.assertNotIn("translatedTemplate", failed)
            self.assertNotIn("translatedText", failed)
            self.assertEqual(failed["inlineMath"], block["inlineMath"])
            self.assertEqual(successful["translatedText"], "第二段完成。")

    async def test_explicit_page_number_is_preserved_without_text_heuristics(self):
        page = reading.normalized_page({"pages": [{"page_idx": 0, "blocks": [
            {"type": "page_number", "content": "i", "bbox": [0.49, 0.88, 0.51, 0.91]},
            {"type": "text", "content": "i", "bbox": [0.1, 0.2, 0.3, 0.3]},
        ]}]}, 1, 612, 792)
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return self.response(request, "我")
        result, document, _ = await self.run_fixture(handler, pages=[page])
        label, ordinary = document["pages"][0]["blocks"]
        self.assertEqual(label["sourceType"], "page_number")
        self.assertEqual(label["kind"], "text")
        self.assertEqual(label["status"], "preserved")
        self.assertEqual(label["sourceText"], "i")
        self.assertNotIn("translatedText", label)
        self.assertNotIn("sourceType", ordinary)
        self.assertEqual(ordinary["translatedText"], "我")
        self.assertEqual(len(calls), 1)
        self.assertEqual((result["translatedBlocks"], result["preservedBlocks"]), (1, 1))

    async def test_redirect_and_truncated_reply_stay_local_failures(self):
        calls = []
        def handler(request):
            calls.append(str(request.url))
            text = json.loads(request.content)["messages"][-1]["content"]
            if text.startswith("First"):
                return httpx.Response(302, request=request, headers={"Location": "https://other.test/key"})
            return self.response(request, "截断的译文", finish="length")
        result, document, _ = await self.run_fixture(handler)
        self.assertEqual(result["failedBlocks"], 2)
        self.assertEqual(calls, ["https://example.test/v1/chat/completions"] * 2)
        self.assertNotIn("translatedText", document["pages"][0]["blocks"][1])

    async def test_source_mutation_prevents_commit(self):
        def handler(request):
            with self.source.open("ab") as stream:
                stream.write(b"\n% changed")
            return self.response(request)
        with self.assertRaises(reading.ReadingError):
            await self.run_fixture(handler)
        self.assertEqual(list((self.root / "outputs").glob("result-*")), [])

    async def test_cancellation_does_not_publish_partial_output(self):
        entered = asyncio.Event()
        async def handler(request):
            entered.set()
            await asyncio.Event().wait()
        with patch.object(reading, "parse_pages", new=AsyncMock(return_value=[blocks_page()])), self.mock_client(handler):
            task = asyncio.create_task(reading.translate(self.request, Events()))
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(list((self.root / "outputs").iterdir()), [])

    async def test_parser_timeout_stops_its_descendants(self):
        import psutil
        try:
            psutil.pids()
        except OSError:
            self.skipTest("Process enumeration is restricted by this test sandbox")
        child_pid = self.root / "child.pid"
        script = ("import pathlib,subprocess,sys,time; "
                  "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                  "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(60)")
        with self.assertRaises(asyncio.TimeoutError):
            await reading.run_process([sys.executable, "-c", script, child_pid],
                                      env=reading.runtime_environment(self.root / "runtime"), timeout=1)
        pid = int(child_pid.read_text())
        if psutil.pid_exists(pid):
            self.assertIn(psutil.Process(pid).status(), (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD))

    async def test_install_uses_independent_managed_python(self):
        root = self.root / "runtime"
        events = Events()
        process = AsyncMock(return_value=0)
        with patch.object(reading, "runtime_directory", return_value=root), patch.object(reading, "runtime_ready", return_value=False), \
             patch.object(reading, "uv_executable", return_value="uv"), patch.object(reading, "run_process", process), \
             patch.object(reading, "models_available", side_effect=[False, True]):
            await reading.ensure_runtime(events)
        commands = [call.args[0] for call in process.call_args_list]
        self.assertIn("--managed-python", commands[0])
        self.assertNotIn(sys.executable, commands[0])
        self.assertEqual(process.call_args_list[0].kwargs["env"]["UV_PYTHON_INSTALL_DIR"], str(root / "python"))
        self.assertTrue((root / "ready.json").is_file())

    async def test_stalled_model_download_moves_to_next_source(self):
        # A blocked source can hang forever without writing a byte (Hugging Face in mainland China).
        root = self.root / "runtime"
        weights = root / "home/models/weights.bin"
        manifest = [{"path": "home/models/weights.bin", "bytes": 7, "sha256": hashlib.sha256(b"weights").hexdigest()}]
        real = reading.run_process
        sources = []
        async def kit(command, **kwargs):
            sources.append(command[-1])
            script = ("import time; time.sleep(60)" if command[-1] == "modelscope" else
                      f"import pathlib; p = pathlib.Path({str(weights)!r}); p.parent.mkdir(parents=True); p.write_bytes(b'weights')")
            return await real([sys.executable, "-c", script], **kwargs)
        with patch.object(reading, "MODEL_STALL_SECONDS", 1), patch.object(reading, "model_files", return_value=manifest), \
             patch.object(reading, "run_process", kit):
            await asyncio.wait_for(reading.download_models(root, reading.runtime_environment(root, offline=False), Events()), 20)
            self.assertTrue(reading.models_available(root, verify=True))
        self.assertEqual(sources, ["modelscope", "huggingface"])


# Known Basic/ONNX OCR capability, without content-scoring heuristics.
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

    async def test_real_unicef_selected_pages_have_no_supported_native_arabic_layer(self):
        source = Path(__file__).resolve().parents[2] / "validation/representative20-20261027/corpus/20-unicef-child-rights-ar.pdf"
        if not source.exists():
            self.skipTest("Public validation PDF is not included in source checkout")
        selected = [7, 9, 12, 38, 46]
        self.assertEqual(reading.unsupported_arabic_ocr_pages(source, selected, "ar"), set(selected))


# Regression cases for opaque formula markers, not translation quality scoring.
class FormulaPlaceholderTests(unittest.IsolatedAsyncioTestCase):
    async def translate(self, source, reply, **metadata):
        block = dict(id="p00001-b00001", kind="text", sourceText=source, status="preserved", **metadata)
        calls = []
        def handler(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200, request=request, json={
                "choices": [{"finish_reason": "stop", "message": {"content": reply}}]})
        service = dict(provider="custom", kind="openai", model="test", baseURL="https://example.test/v1",
                       apiKey="test-only", concurrency=1)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await reading.translate_block(client, block, service, "fr", "zh")
        self.assertEqual(len(calls), 1)
        return block, calls[0]

    async def test_observed_plain_text_leaks_keep_source_without_priming_markers(self):
        # Exact source/reply pairs from INRS physical p19 and NASA physical p8.
        for source, reply in (("- masse des colis ;", "- [[PBM_1]]"),
                              ("FY24 Flight Manifest", "[[PBM_...]]")):
            with self.subTest(source=source):
                block, payload = await self.translate(source, reply, translatedText="stale",
                                                     translatedTemplate="stale [[PBM_1]]")
                self.assertEqual(payload["messages"][1]["content"], source)
                self.assertNotIn("PBM", payload["messages"][0]["content"])
                self.assertEqual(block["status"], "source_fallback")
                self.assertEqual(block["sourceText"], source)
                self.assertNotIn("translatedText", block)
                self.assertNotIn("translatedTemplate", block)

    async def test_plain_text_translation_does_not_use_stale_template(self):
        block, payload = await self.translate("- masse des colis ;", "- 包裹重量；",
                                             sourceTemplate="stale [[PBM_1]]")
        self.assertEqual(payload["messages"][1]["content"], "- masse des colis ;")
        self.assertNotIn("PBM", payload["messages"][0]["content"])
        self.assertEqual(block["status"], "translated")
        self.assertEqual(block["translatedText"], "- 包裹重量；")
        self.assertNotIn("translatedTemplate", block)

    async def test_actual_math_uses_only_its_exact_markers_and_restores_readable_text(self):
        token = "[[PBM_abc123_0001]]"
        formulas = [dict(token=token, latex="x^2", bbox=[10, 20, 30, 40])]
        block, payload = await self.translate("Mean $x^2$ remains.", f"均值{token}保留。",
                                             inlineMath=copy.deepcopy(formulas), sourceTemplate=f"Mean {token} remains.")
        self.assertIn(token, payload["messages"][0]["content"])
        self.assertNotIn("[[PBM_...]]", payload["messages"][0]["content"])
        self.assertEqual(block["translatedTemplate"], f"均值{token}保留。")
        self.assertEqual(block["translatedText"], "均值$x^2$保留。")
        self.assertEqual(block["inlineMath"], formulas)

    async def test_unknown_marker_cannot_hide_beside_a_complete_real_formula(self):
        token = "[[PBM_abc123_0001]]"
        block, _ = await self.translate("Mean $x$.", f"均值{token}，以及[[PBM_2]]。",
                                       inlineMath=[dict(token=token, latex="x", bbox=[10, 20, 30, 40])],
                                       sourceTemplate=f"Mean {token}.")
        self.assertEqual(block["status"], "source_fallback")
        self.assertNotIn("translatedText", block)
        self.assertNotIn("translatedTemplate", block)

    async def test_unfinished_marker_is_not_displayed(self):
        for reply in ("未知[[PBM_2", "未知[[PBM_2]\n另一行"):
            with self.subTest(reply=reply):
                block, _ = await self.translate("Plain text.", reply)
                self.assertEqual(block["status"], "source_fallback")
                self.assertNotIn("translatedText", block)

    async def test_literal_marker_in_source_is_data_not_an_invented_attachment(self):
        block, _ = await self.translate("The literal string [[PBM_example]] appears twice: [[PBM_example]].",
                                       "字面字符串[[PBM_example]]出现两次：[[PBM_example]]。")
        self.assertEqual(block["status"], "translated")
        self.assertNotIn("translatedTemplate", block)
        self.assertNotIn("inlineMath", block)

    async def test_table_context_is_not_sent_with_the_target_fragment(self):
        context = "Previous row contains 3091 and a different description."
        block, payload = await self.translate("de lithium)", "锂的)", sourceType="table_cell", translationContext=context)
        self.assertEqual(block["status"], "translated")
        self.assertEqual(payload["messages"][-1]["content"], "<SOURCE>\nde lithium)\n</SOURCE>")
        self.assertEqual(len(payload["messages"]), 2)
        self.assertNotIn(context, json.dumps(payload))
        self.assertNotIn("3091", json.dumps(payload))
        self.assertIn("do not expand abbreviations", payload["messages"][0]["content"])
        self.assertIn("Month names", payload["messages"][0]["content"])
        self.assertNotIn("PBM", payload["messages"][0]["content"])


# Native paths and parser boxes must share the displayed page's local origin.
class DecorationCoordinatesTests(unittest.TestCase):
    def test_native_paths_remove_visible_origin_after_rotation(self):
        with tempfile.TemporaryDirectory() as folder:
            for rotation in (0, 90, 180, 270):
                for cropped in (False, True):
                    with self.subTest(rotation=rotation, cropped=cropped):
                        path = Path(folder) / f"source-{rotation}-{cropped}.pdf"
                        with pymupdf.open() as pdf:
                            page = pdf.new_page(width=630, height=810)
                            page.draw_line((80, 120), (260, 120), color=(1, 0, 0), width=2)
                            page.draw_rect((310, 300, 370, 360), color=None, fill=(0, 0, 1))
                            if cropped:
                                page.set_cropbox(pymupdf.Rect(30, 40, 580, 730))
                            else:
                                pdf.xref_set_key(page.xref, "MediaBox", "[18 18 612 792]")
                                pdf.xref_set_key(page.xref, "CropBox", "[0 0 630 810]")
                            page = pdf.reload_page(page)
                            page.set_rotation(rotation)
                            pdf.save(path)
                        digest = hashlib.sha256(path.read_bytes()).hexdigest()
                        result = {}
                        reading.add_decorations(path, 1, result)
                        self.assertEqual(result["decorationsCoordinateSpace"], "page")
                        with pymupdf.open(path) as pdf:
                            page = pdf[0]
                            origin = page.rect.tl
                            line = page.get_drawings()[0]["items"][0]
                            expected = [point * page.rotation_matrix - origin for point in line[1:3]]
                        commands = result["decorations"][0]["commands"]
                        self.assertEqual(commands, [dict(op="M", points=list(expected[0])),
                                                    dict(op="L", points=list(expected[1]))])
                        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
