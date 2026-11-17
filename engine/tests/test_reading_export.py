import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import unittest
import uuid

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from outline_fixture import make_outline_pdf, outline_metadata
from reading_background import add_page_background
import reading_export
from structured_reading import ReadingError, refine_list_geometry

DEMO = Path(__file__).resolve().parents[2] / "demos/pdfs/better-figures.pdf"
CHINESE = re.compile(r"[㐀-鿿]")


class Events:
    def emit(self, kind, **fields):
        pass


def native_blocks(path, number):
    with pymupdf.open(path) as pdf:
        return {"".join(span["text"] for line in block["lines"] for span in line["spans"]): list(block["bbox"])
                for block in pdf[number - 1].get_text("dict")["blocks"] if block["type"] == 0}


def draft(path, pages, folder, digest=None):
    """A committed-shape reading draft whose translated blocks use real native geometry."""
    document_pages = []
    with pymupdf.open(path) as pdf:
        count = pdf.page_count
        for number, blocks in pages.items():
            page = {"pageNumber": number, "width": pdf[number - 1].rect.width, "height": pdf[number - 1].rect.height,
                    "status": "completed", "blocks": [dict(block, id=f"p{number:05d}-b{index:05d}")
                                                      for index, block in enumerate(blocks, 1)]}
            add_page_background(path, page)
            document_pages.append(page)
    reading = Path(folder) / "reading.json"
    reading.write_text(json.dumps({"schemaVersion": 1, "artifactType": "structured-reading",
                                   "sourceSHA256": digest or hashlib.sha256(Path(path).read_bytes()).hexdigest(),
                                   "pageCount": count, "selectedPages": list(pages), "pages": document_pages}))
    return reading


def export(source, reading, output, kind):
    asyncio.run(reading_export.export({"inputPath": str(source), "readingPath": str(reading),
                                       "outputPath": str(output), "kind": kind}, Events()))


class ReadingExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_pdf_first_line_follows_the_source_indent(self):
        source = self.root / "indent-source.pdf"
        with pymupdf.open() as pdf:
            pdf.new_page(width=300, height=300)
            pdf.save(source)
        passage = "第一段正文足够长，可以在此栏内自动换行并继续阅读。\n第二段从这里开始。"
        def block(top, text, **fields):
            return dict({"kind": "text", "bbox": [20, top, 180, top + 65], "sourceText": "x",
                         "status": "translated", "translatedText": text}, **fields)
        blocks = [block(10, passage, firstLineIndent=18), block(95, "顶格正文同样可以翻译。"),
                  block(165, "标签", sourceType="table_cell")]
        output = self.root / "translated.pdf"
        export(source, draft(source, {1: blocks}, self.root), output, "translated")
        with pymupdf.open(output) as pdf:
            spans = [line["spans"][0] for item in pdf[0].get_text("dict")["blocks"] for line in item.get("lines", [])]
        self.assertGreater(len(spans), 4)  # At least one line actually wrapped.
        for span in spans:
            first = span["text"].startswith(("第一段", "第二段"))
            self.assertAlmostEqual(span["origin"][0], 38 if first else 20, delta=0.1)

    def test_translated_page_replaces_only_drawn_text_and_keeps_artwork(self):
        native = native_blocks(DEMO, 2)
        heading = next(text for text in native if text.startswith("Rule 5"))
        paragraph = next(text for text in native if text.startswith("Any plotting library"))
        preserved = next(text for text in native if text.startswith("Rule 6"))
        reading = draft(DEMO, {2: [
            {"kind": "heading", "bbox": native[heading], "sourceText": heading, "status": "translated", "translatedText": "规则五：不要相信默认设置"},
            {"kind": "text", "bbox": native[paragraph], "sourceText": paragraph, "status": "translated",
             "translatedText": "任何绘图库或软件都带有一组默认设置。终端用户没有指定任何设置时，就会使用这些默认值。"},
            {"kind": "text", "bbox": native[preserved], "sourceText": preserved, "status": "preserved"},
        ]}, self.root)
        styles = json.loads(reading.read_text())["pages"][0]["blocks"]
        self.assertEqual([block["sourceBold"] for block in styles[:2]], [True, False])
        output = self.root / "translated.pdf"
        export(DEMO, reading, output, "translated")
        with pymupdf.open(DEMO) as original, pymupdf.open(output) as result:
            self.assertEqual(result.page_count, original.page_count)
            text = result[1].get_text()
            self.assertIn("不要相信默认设置", text.replace("\n", ""))
            self.assertIn("任何绘图库", text)
            self.assertNotIn("Trust the Defaults", text.replace("\n", " "))
            self.assertNotIn("Any plotting library", text.replace("\n", " "))
            self.assertIn("Rule 6: Use Color Effectively", text)
            self.assertEqual([info["bbox"] for info in result[1].get_image_info()],
                             [info["bbox"] for info in original[1].get_image_info()])
            self.assertEqual(result[0].get_text(), original[0].get_text())

    def test_bilingual_follows_each_selected_original_with_its_translation(self):
        source = self.root / "source.pdf"
        with pymupdf.open() as pdf:
            for number in range(1, 4):
                page = pdf.new_page()
                page.insert_text((72, 72), f"Source page {number} explains the valve.", fontsize=12)
                # Redaction removes a link over translated text (and left a stale link list behind).
                page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 76), "uri": "https://example.org/"})
            pdf.save(source)
        blocks = native_blocks(source, 1)
        reading = draft(source, {number: [{"kind": "text", "bbox": next(iter(blocks.values())),
                                           "sourceText": f"Source page {number} explains the valve.",
                                           "status": "translated", "translatedText": f"第 {number} 页说明阀门。"}]
                                 for number in (1, 3)}, self.root)
        output = self.root / "bilingual.pdf"
        export(source, reading, output, "bilingual")
        with pymupdf.open(output) as result:
            texts = [page.get_text() for page in result]
            self.assertEqual([len(page.get_links()) for page in result], [1] * 5)
        self.assertEqual([bool(CHINESE.search(text)) for text in texts], [False, True, False, False, True])
        self.assertEqual([texts[index].strip() for index in (0, 2, 3)],
                         [f"Source page {number} explains the valve." for number in (1, 2, 3)])
        self.assertIn("第 3 页", texts[4])

    def test_hybrid_export_clears_bitmap_words_and_keeps_selectable_translation(self):
        source = self.root / "hybrid.pdf"
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=300, height=180)
            page.insert_text((24, 50), "DISCLAIMER", fontsize=24)
            page.insert_text((24, 95), "Keep this source footer", fontsize=10)
            page.draw_rect(pymupdf.Rect(240, 20, 280, 60), fill=(.2, .7, .3), color=None)
            image = page.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")
            page.insert_image(page.rect, stream=image)
            pdf.save(source)
        native = native_blocks(source, 1)
        text = next(text for text in native if text.startswith("DISCLAIMER"))
        reading = draft(source, {1: [dict(kind="heading", bbox=native[text], sourceText=text,
                                         translatedText="免责声明", status="translated", backgroundColor=[255, 255, 255])]}, self.root)
        metadata = json.loads(reading.read_text())["pages"][0]
        self.assertEqual(metadata.get("backgroundFillBlocks"), [metadata["blocks"][0]["id"]])
        output = self.root / "hybrid-translated.pdf"
        export(source, reading, output, "translated")
        with pymupdf.open(source) as original, pymupdf.open(output) as result:
            self.assertIn("免责声明", result[0].get_text().replace("\n", ""))
            self.assertNotIn("DISCLAIMER", result[0].get_text())
            self.assertIn("Keep this source footer", result[0].get_text())
            before, after = original[0].get_pixmap(), result[0].get_pixmap()
            # The old heading's right-hand letters are outside the short Chinese
            # title. Their pixels must be blank, not merely absent from extraction.
            self.assertEqual(after.color_count(clip=pymupdf.IRect(160, 30, 180, 51)), 1)
            for point in [(250, 30), (30, 90), (30, 10)]:
                self.assertEqual(before.pixel(*point), after.pixel(*point))

    def test_outline_translation_preserves_the_photo_and_shared_form(self):
        artwork = make_outline_pdf(self.root / "outline.pdf", photo=True, extra_font=True)
        source = self.root / "two-page-form.pdf"
        with pymupdf.open(artwork) as graphic, pymupdf.open() as pdf:
            for _ in range(2):
                page = pdf.new_page(width=graphic[0].rect.width, height=graphic[0].rect.height)
                page.show_pdf_page(page.rect, graphic, 0)
            pdf.save(source)
        blocks = outline_metadata(source)["blocks"]
        reading = draft(source, {1: blocks}, self.root)
        page = json.loads(reading.read_text())["pages"][0]
        self.assertEqual(page["backgroundBlocks"], [blocks[0]["id"]])
        output = self.root / "translated.pdf"
        export(source, reading, output, "translated")
        with pymupdf.open(source) as original, pymupdf.open(output) as result:
            self.assertEqual(result.page_count, 2)
            self.assertIn("译文", result[0].get_text())
            self.assertIn("Keep this source footer", result[0].get_text())
            self.assertNotIn("Agi Agi", result[0].get_text())
            self.assertEqual(result[1].get_pixmap().samples, original[1].get_pixmap().samples)
            before, after = original[0].get_pixmap(), result[0].get_pixmap()
            self.assertNotEqual(after.pixel(95, 31), before.pixel(95, 31))
            for point in [(100, 70), (150, 50), (260, 35)]:
                self.assertEqual(after.pixel(*point), before.pixel(*point))

    def test_vector_list_marker_stays_left_of_the_translated_body(self):
        outlined = make_outline_pdf(self.root / "outline.pdf")
        source = self.root / "vector-list.pdf"
        marker = pymupdf.Rect(13, 39, 20, 46)
        with pymupdf.open(outlined) as pdf:
            pdf[0].draw_rect(marker, fill=(0, 0, 0), color=None)
            pdf[0].insert_text((24, 90), "1 Agi Agi")
            pdf.save(source)
        block = dict(outline_metadata(source)["blocks"][0], kind="text", bbox=[12, 28, 100, 56],
                     sourceText="1 Agi Agi", status="preserved")
        numbered = dict(id="p00001-b00002", kind="text", bbox=[12, 75, 100, 94], sourceText="1 Agi Agi")
        refine_list_geometry(source, 1, {"blocks": [block, numbered]})
        self.assertEqual(block["sourceText"], "Agi Agi")
        self.assertEqual(numbered["sourceText"], "1 Agi Agi")
        self.assertAlmostEqual(block["bbox"][0], 24)

        # An existing draft can still have the parser's original, wider box.
        block.update(bbox=[12, 28, 100, 56], status="translated", translatedText="列表译文")
        reading = draft(source, {1: [block]}, self.root)
        page = json.loads(reading.read_text())["pages"][0]
        self.assertAlmostEqual(page["blocks"][0]["bbox"][0], 24)
        self.assertEqual(page["backgroundBlocks"], [block["id"]])
        output = self.root / "translated.pdf"
        export(source, reading, output, "translated")
        with pymupdf.open(source) as original, pymupdf.open(output) as result:
            text_box, = result[0].search_for("列表译文")
            self.assertGreaterEqual(text_box.x0, 24)
            self.assertEqual(result[0].get_pixmap(clip=marker).samples, original[0].get_pixmap(clip=marker).samples)

    def test_unverified_artwork_stays_on_its_original_page(self):
        source = make_outline_pdf(self.root / "ambiguous.pdf", artwork=True)
        reading = draft(source, {1: outline_metadata(source)["blocks"]}, self.root)
        page = json.loads(reading.read_text())["pages"][0]
        self.assertEqual(page["backgroundBlocks"], [])
        self.assertNotIn("backgroundImage", page)
        output = self.root / "translated.pdf"
        export(source, reading, output, "translated")
        with pymupdf.open(source) as original, pymupdf.open(output) as result:
            self.assertEqual(result.page_count, original.page_count)
            self.assertEqual(result[0].get_pixmap().samples, original[0].get_pixmap().samples)

    def test_flat_scan_keeps_positioned_translation_and_clears_hidden_ocr(self):
        source = self.root / "scan.pdf"
        with pymupdf.open() as pdf, pymupdf.open() as raster:
            photo = raster.new_page(width=300, height=180)
            photo.insert_text((24, 50), "Scanned paragraph", fontsize=16)
            page = pdf.new_page(width=300, height=180)
            page.insert_image(page.rect, stream=photo.get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png"))
            page.insert_text((24, 50), "Scanned paragraph", fontsize=16, render_mode=3)
            pdf.save(source)
        box = [22, 30, 185, 56]
        reading = draft(source, {1: [dict(kind="text", bbox=box, sourceText="Scanned paragraph",
                                         status="translated", translatedText="扫描段落")]}, self.root)
        page = json.loads(reading.read_text())["pages"][0]
        self.assertEqual(page["backgroundFillBlocks"], [page["blocks"][0]["id"]])
        output = self.root / "translated.pdf"
        export(source, reading, output, "translated")
        with pymupdf.open(output) as result:
            self.assertIn("扫描段落", result[0].get_text().replace("\n", ""))
            self.assertNotIn("Scanned paragraph", result[0].get_text())
            self.assertEqual(result[0].get_pixmap().color_count(clip=pymupdf.IRect(125, 30, 170, 56)), 1)

    def test_draft_of_another_source_is_rejected(self):
        blocks = native_blocks(DEMO, 2)
        text = next(iter(blocks))
        reading = draft(DEMO, {2: [{"kind": "text", "bbox": blocks[text], "sourceText": text, "status": "translated",
                                    "translatedText": "译文"}]}, self.root, digest="0" * 64)
        output = self.root / "translated.pdf"
        with self.assertRaisesRegex(ReadingError, "阅读稿与当前原文不匹配"):
            export(DEMO, reading, output, "translated")
        self.assertEqual(sorted(path.name for path in self.root.iterdir()), ["reading.json"])

    @unittest.skipUnless(hasattr(os, "killpg"), "POSIX process-group signal test; Windows uses stdin cancellation")
    def test_sigterm_during_a_one_page_export_writes_nothing(self):
        # The cancel used to be dispatched only after the export had resumed and saved the file.
        native = native_blocks(DEMO, 2)
        text = next(text for text in native if text.startswith("Any plotting library"))
        reading = draft(DEMO, {2: [{"kind": "text", "bbox": native[text], "sourceText": text, "status": "translated",
                                    "translatedText": "任何绘图库都带有一组默认设置。"}]}, self.root)
        output = self.root / "translated.pdf"
        request = {"protocolVersion": 1, "jobId": str(uuid.uuid4()), "attemptId": str(uuid.uuid4()), "action": "export_reading",
                   "inputPath": str(DEMO), "readingPath": str(reading), "outputPath": str(output), "kind": "translated"}
        process = subprocess.Popen([sys.executable, str(Path(reading_export.__file__).with_name("worker.py"))],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        try:
            process.stdin.write(json.dumps(request) + "\n")
            process.stdin.flush()
            self.assertEqual(json.loads(process.stdout.readline())["type"], "ready")
            os.killpg(process.pid, signal.SIGTERM)
            stdout = process.communicate(timeout=20)[0]
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        self.assertEqual(process.returncode, 130)
        self.assertEqual(json.loads(stdout.splitlines()[-1])["stage"], "cancelled")
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
