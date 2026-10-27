import copy
import base64
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from pagebridge.core import AppError, DocumentStore, atomic_json, sha256
from pagebridge import structured


class StructuredReadingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "original.pdf"
        with pymupdf.open() as pdf:
            for i in range(3):
                pdf.new_page().insert_text((30, 40), f"Source page {i + 1}: 10 mV")
            field = pymupdf.Widget(); field.field_name = "original-field"; field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            field.rect = pymupdf.Rect(30, 60, 120, 90); field.field_value = "kept"
            pdf[0].add_widget(field)
            pdf.save(self.source)
        self.store = DocumentStore(self.root / "library")
        self.doc = self.store.import_pdf(self.source)

    def tearDown(self):
        self.temporary.cleanup()

    def prepare(self, partial=False):
        attempt, directory = self.store.new_attempt(self.doc["id"])
        self.store.mark_running(self.doc["id"], attempt)
        output = directory / "result"; output.mkdir()
        artifact = dict(schemaVersion=1, artifactType=structured.ARTIFACT_TYPE, sourceSHA256=self.doc["input_sha256"], pageCount=3,
                        sourceLanguage="en", targetLanguage="zh", selectedPages=[2], status="partial" if partial else "completed",
                        pages=[dict(pageNumber=2, width=595, height=842, blocks=[
                            dict(id="p2-b1", kind="text", sourceText="Source page 2: 10 mV", translatedText="第 2 页：10 mV", status="translated", bbox=[25, 20, 200, 60]),
                            dict(id="p2-b2", kind="table", sourceText="MIN TYP MAX", translatedText=None if partial else "最小 典型 最大", status="source_fallback" if partial else "translated", bbox=[30, 60, 200, 120])])])
        atomic_json(output / "reading.json", artifact)
        manifest = dict(protocolVersion=1, jobId="job-1", attemptId=attempt, artifactType=structured.ARTIFACT_TYPE,
                        sourceSHA256=self.doc["input_sha256"], sourcePages=3, readingSHA256=sha256(output / "reading.json"))
        atomic_json(output / "manifest.json", manifest)
        event = dict(type="result", jobId="job-1", attemptId=attempt, artifactType=structured.ARTIFACT_TYPE,
                     readingPath=str(output / "reading.json"), manifestPath=str(output / "manifest.json"), readingSHA256=manifest["readingSHA256"])
        return attempt, event, artifact

    def save_modified(self, event, artifact):
        atomic_json(event["readingPath"], artifact)
        event["readingSHA256"] = sha256(event["readingPath"])
        manifest = json.loads(Path(event["manifestPath"]).read_text()); manifest["readingSHA256"] = event["readingSHA256"]
        atomic_json(event["manifestPath"], manifest)

    def test_partial_commit_restart_and_original_widgets_preserved(self):
        original_hash = sha256(self.doc["input_path"])
        attempt, event, _ = self.prepare(partial=True)
        result = self.store.commit_result(self.doc["id"], attempt, event)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["reading_failed_blocks"], 1)
        self.assertIsNone(result["translated_path"])
        self.assertEqual(sha256(self.doc["input_path"]), original_hash)
        with pymupdf.open(self.doc["input_path"]) as pdf:
            self.assertEqual(next(pdf[0].widgets()).field_value, "kept")
        restored = DocumentStore(self.store.root)
        page = structured.page_result(restored, dict(document_id=self.doc["id"], page=1))
        self.assertEqual(page["blocks"][0]["translatedText"], "第 2 页：10 mV")
        self.assertEqual(page["blocks"][1]["status"], "source_fallback")
        self.assertEqual(structured.page_result(restored, dict(document_id=self.doc["id"], page=0))["page_status"], "not_selected")

    def test_separate_artifact_preserves_pdf_results_and_failed_retry(self):
        self.store._change(self.doc["id"], lambda d: d.update(translated_path=d["input_path"], bilingual_path=d["input_path"]))
        attempt, event, _ = self.prepare()
        result = self.store.commit_result(self.doc["id"], attempt, event)
        self.assertEqual(result["translated_path"], self.doc["input_path"])
        self.assertEqual(result["preferred_artifact"], "reading")
        old_path = result["reading_path"]
        next_attempt, _ = self.store.new_attempt(self.doc["id"])
        self.store.mark_running(self.doc["id"], next_attempt)
        self.store.finish_failure(self.doc["id"], "Cancelled", cancelled=True)
        self.assertEqual(self.store.get(self.doc["id"])["reading_path"], old_path)
        self.assertTrue(Path(old_path).exists())

    def test_source_font_size_is_response_only_and_preserves_committed_artifact(self):
        attempt, event, artifact = self.prepare()
        result = self.store.commit_result(self.doc["id"], attempt, event)
        before = Path(result["reading_path"]).read_bytes()
        source_before = sha256(self.doc["input_path"])
        page = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        self.assertAlmostEqual(page["blocks"][0]["sourceFontSize"], 11)
        self.assertNotIn("sourceFontSize", page["blocks"][1])
        self.assertEqual(Path(result["reading_path"]).read_bytes(), before)
        self.assertEqual(sha256(self.doc["input_path"]), source_before)
        self.assertNotIn("sourceFontSize", artifact["pages"][0]["blocks"][0])

    def test_source_font_size_uses_region_and_character_weighted_median(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 80), "Large", fontsize=48)
            page.insert_text((50, 140), "body text has many more characters", fontsize=12)
            page.insert_text((50, 210), "Annual losses", fontsize=8)
            blocks = [dict(kind="heading", bbox=[40, 20, 580, 96]),
                      dict(kind="text", bbox=[40, 20, 580, 160]),
                      dict(kind="text", sourceType="table_cell", bbox=[40, 190, 250, 220]),
                      dict(kind="text", bbox=None), dict(kind="figure", bbox=[40, 20, 580, 160]),
                      dict(kind="text", bbox=[40, 300, 580, 340], sourceFontSize=99)]
            structured._add_source_font_sizes(page, blocks)
            self.assertEqual([b.get("sourceFontSize") for b in blocks], [48, 12, 8, None, None, None])
            page.set_rotation(90)
            structured._add_source_font_sizes(page, blocks)
            self.assertTrue(all("sourceFontSize" not in b for b in blocks))

    def test_source_font_size_matches_cropped_page_coordinates_and_scan_fallback(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((100, 140), "Cropped source", fontsize=18)
            page.set_cropbox(pymupdf.Rect(50, 50, 550, 700))
            blocks = [dict(kind="text", bbox=[40, 60, 260, 130])]
            structured._add_source_font_sizes(page, blocks)
            self.assertEqual(blocks[0]["sourceFontSize"], 18)
            blank = pdf.new_page()
            structured._add_source_font_sizes(blank, blocks)
            self.assertNotIn("sourceFontSize", blocks[0])

    def test_failed_page_is_committed_without_hiding_other_pages(self):
        attempt, event, artifact = self.prepare(partial=True)
        artifact["selectedPages"] = [1, 2]
        artifact["pages"].insert(0, dict(pageNumber=1, width=595, height=842, status="source_fallback", error="本页解析未完成。", blocks=[]))
        self.save_modified(event, artifact)
        self.store.commit_result(self.doc["id"], attempt, event)
        first = structured.page_result(self.store, dict(document_id=self.doc["id"], page=0))
        self.assertEqual(first["page_status"], "source_fallback")
        self.assertEqual(len(structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))["blocks"]), 2)

    def test_original_region_render_uses_owned_pdf_not_model_paths(self):
        attempt, event, artifact = self.prepare()
        artifact["pages"][0]["blocks"][1]["imagePath"] = "/arbitrary/untrusted/image.png"
        self.save_modified(event, artifact)
        self.store.commit_result(self.doc["id"], attempt, event)
        result = structured.block_image(self.store, dict(document_id=self.doc["id"], block_id="p2-b2"))
        self.assertTrue(Path(result["path"]).is_relative_to(self.store.root / "preview-cache"))
        self.assertTrue(Path(result["path"]).read_bytes().startswith(b"\x89PNG"))
        self.assertEqual(result["page"], 1)

    def test_native_background_survives_commit_restart_without_restoring_old_paths(self):
        attempt, event, artifact = self.prepare()
        with pymupdf.open(self.doc["input_path"]) as pdf:
            png = pdf[1].get_pixmap(matrix=pymupdf.Matrix(.2, .2)).tobytes("png")
        page = artifact["pages"][0]
        page.update(backgroundImage=base64.b64encode(png).decode(), backgroundBlocks=["p2-b1"],
                    decorations=[{"unused": "old paths must not be repainted over the clean background"}])
        self.save_modified(event, artifact)
        self.store.commit_result(self.doc["id"], attempt, event)
        reopened = DocumentStore(self.store.root)
        result = structured.page_result(reopened, dict(document_id=self.doc["id"], page=1))
        self.assertEqual(Path(result["backgroundImage"]).read_bytes(), png)
        self.assertTrue(Path(result["backgroundImage"]).is_relative_to(reopened.root / "preview-cache"))
        self.assertEqual(result["backgroundBlocks"], ["p2-b1"])
        self.assertIsNone(result["decorationsImage"])
        self.assertIsNone(result["decorations_error"])
        self.assertEqual(sha256(self.source), sha256(self.doc["input_path"]))

    def test_background_rejects_invalid_images_and_unknown_block_references(self):
        attempt, event, artifact = self.prepare()
        with pymupdf.open(self.doc["input_path"]) as pdf:
            png = pdf[1].get_pixmap(matrix=pymupdf.Matrix(.1, .1)).tobytes("png")
        for encoded, ids in [(base64.b64encode(b'not a PNG' * 10).decode(), ["p2-b1"]),
                             (base64.b64encode(png).decode(), ["unknown"]),
                             (base64.b64encode(png).decode(), ["p2-b1", "p2-b1"])]:
            with self.subTest(ids=ids):
                artifact["pages"][0].update(backgroundImage=encoded, backgroundBlocks=ids)
                self.save_modified(event, artifact)
                with self.assertRaises(AppError):
                    self.store.commit_result(self.doc["id"], attempt, event)

    def test_parser_code_kind_preserved_and_original_region_available(self):
        attempt, event, artifact = self.prepare()
        code = '{"greeting": "hello", "items": [1, 2]}\n'
        artifact["pages"][0]["blocks"] = [dict(id="p2-code", kind="code", sourceText=code,
                                              status="preserved", bbox=[25, 20, 200, 60])]
        self.save_modified(event, artifact)
        self.store.commit_result(self.doc["id"], attempt, event)
        page = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        self.assertEqual(page["blocks"][0]["kind"], "code")
        self.assertEqual(page["blocks"][0]["status"], "preserved")
        self.assertEqual(page["blocks"][0]["sourceText"], code)
        self.assertNotIn("translatedText", page["blocks"][0])
        image = structured.block_image(self.store, dict(document_id=self.doc["id"], block_id="p2-code"))
        self.assertTrue(Path(image["path"]).read_bytes().startswith(b"\x89PNG"))

    def test_page_response_keeps_background_color_and_unpositioned_text(self):
        attempt, event, artifact = self.prepare()
        artifact["pages"][0]["blocks"][0]["backgroundColor"] = [245, 240, 225]
        artifact["pages"][0]["blocks"][0].update(inlineMath=[dict(token="[[PBM_test_0001]]", latex="x^2", bbox=[40, 20, 55, 30])],
                                                 sourceTemplate="Source [[PBM_test_0001]]", translatedTemplate="译文 [[PBM_test_0001]]")
        artifact["pages"][0]["blocks"][1].pop("bbox")
        self.save_modified(event, artifact)
        self.store.commit_result(self.doc["id"], attempt, event)
        page = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        self.assertEqual((page["width"], page["height"]), (595, 842))
        self.assertEqual(page["blocks"][0]["backgroundColor"], [245, 240, 225])
        self.assertEqual(page["blocks"][0]["inlineMath"][0]["latex"], "x^2")
        self.assertEqual(page["blocks"][0]["translatedTemplate"], "译文 [[PBM_test_0001]]")
        self.assertNotIn("bbox", page["blocks"][1])
        self.assertEqual(page["blocks"][1]["translatedText"], "最小 典型 最大")
        untranslated = structured.page_result(self.store, dict(document_id=self.doc["id"], page=2))
        self.assertEqual(untranslated["page_status"], "not_selected")
        self.assertEqual((untranslated["width"], untranslated["height"]), (595, 842))
        self.assertEqual(untranslated["decorations"], [])
        self.assertIsNone(untranslated["decorationsImage"])

    def test_stroke_restore_uses_original_pixels_without_rectangle_fill(self):
        source = self.root / "border.pdf"
        with pymupdf.open(self.source) as pdf:
            pdf[1].draw_rect(pymupdf.Rect(20, 20, 200, 100), color=(1, 0, 0), width=.6)
            pdf.save(source)
        self.doc = self.store.import_pdf(source)
        attempt, event, artifact = self.prepare()
        decoration = dict(commands=[dict(op="M", points=[20, 20]), dict(op="L", points=[200, 20]),
                                    dict(op="L", points=[200, 100]), dict(op="L", points=[20, 100]), dict(op="Z", points=[])],
                          width=.6, dash=[], dashPhase=0, lineCap=0, lineJoin=0, color=[0, 0, 255], opacity=.5)
        artifact["pages"][0]["decorations"] = [decoration]
        self.save_modified(event, artifact); self.store.commit_result(self.doc["id"], attempt, event)
        result = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        self.assertIsNone(result["decorations_error"])
        self.assertEqual(result["decorations"], [decoration])
        self.assertTrue(Path(result["decorationsImage"]).is_relative_to(self.store.root / "preview-cache"))
        image = pymupdf.Pixmap(result["decorationsImage"])
        self.assertEqual(image.n, 4)
        scale = 2400 / 842
        # Only the stroke is restored; its metadata color/opacity cannot replace
        # the source's red pixels or repaint the rectangle interior.
        red = image.pixel(round(20 * scale), round(60 * scale))
        self.assertGreater(red[0], 240); self.assertLess(red[1], 50); self.assertGreater(red[3], 240)
        self.assertEqual(image.pixel(round(80 * scale), round(80 * scale))[3], 0)
        self.assertEqual(structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))["decorationsImage"], result["decorationsImage"])

    def test_stroke_restore_cache_tracks_artifact_and_bad_paths_are_local(self):
        def commit(commands):
            attempt, event, artifact = self.prepare()
            artifact["pages"][0]["decorations"] = [dict(commands=commands, width=.4, dash=[], dashPhase=0, lineCap=0, lineJoin=0, color=[0, 0, 0], opacity=1)]
            self.save_modified(event, artifact); self.store.commit_result(self.doc["id"], attempt, event)
            return structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        first = commit([dict(op="M", points=[10, 20]), dict(op="L", points=[100, 20])])
        second = commit([dict(op="M", points=[10, 21]), dict(op="L", points=[100, 21])])
        self.assertNotEqual(first["decorationsImage"], second["decorationsImage"])
        malformed = commit([dict(op="C", points=[1, 2])])
        self.assertIsNone(malformed["decorationsImage"])
        self.assertIn("边框", malformed["decorations_error"])
        self.assertEqual(malformed["blocks"][0]["translatedText"], "第 2 页：10 mV")

    def test_fill_boundaries_restore_only_the_original_border_band(self):
        source = self.root / "filled-border.pdf"
        with pymupdf.open(self.source) as pdf:
            pdf[1].draw_rect(pymupdf.Rect(20, 20, 200, 100), color=None, fill=(0, .2, 1))
            pdf[1].draw_rect(pymupdf.Rect(20.775, 20.775, 199.225, 99.225), color=None, fill=(1, 1, 1))
            pdf.save(source)
        self.doc = self.store.import_pdf(source)
        attempt, event, artifact = self.prepare()
        boundaries = []
        for left, top, right, bottom in [(20, 20, 200, 100), (20.775, 20.775, 199.225, 99.225)]:
            boundaries.append(dict(kind="fill_boundary", width=0, dash=[], dashPhase=0, lineCap=0, lineJoin=0, color=[0, 51, 255], opacity=1,
                                   commands=[dict(op="M", points=[left, top]), dict(op="L", points=[right, top]),
                                             dict(op="L", points=[right, bottom]), dict(op="L", points=[left, bottom]), dict(op="Z", points=[])]))
        artifact["pages"][0]["decorations"] = boundaries
        self.save_modified(event, artifact); self.store.commit_result(self.doc["id"], attempt, event)
        result = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        self.assertIsNone(result["decorations_error"])
        image = pymupdf.Pixmap(result["decorationsImage"]); scale = 2400 / 842
        border = image.pixel(round(20.4 * scale), round(65 * scale))
        self.assertGreater(border[2], 240); self.assertGreater(border[3], 240)
        self.assertEqual(image.pixel(round(80 * scale), round(80 * scale))[3], 0)
        self.assertEqual(image.pixel(round(10 * scale), round(65 * scale))[3], 0)

    def test_moved_inline_math_excludes_old_strokes_but_keeps_failed_and_display_math(self):
        source = self.root / "fraction-lines.pdf"
        with pymupdf.open(self.source) as pdf:
            pdf[1].draw_line(pymupdf.Point(40, 45), pymupdf.Point(80, 45), color=(0, 0, 0), width=.7)
            pdf[1].draw_line(pymupdf.Point(120, 45), pymupdf.Point(160, 45), color=(0, 0, 0), width=.7)
            pdf.save(source)
        self.doc = self.store.import_pdf(source)
        for status in ("translated", "source_fallback"):
            with self.subTest(status=status):
                attempt, event, artifact = self.prepare(partial=status == "source_fallback")
                page = artifact["pages"][0]
                page["blocks"][0].update(status=status, inlineMath=[dict(token="[[M1]]", latex="a/b", bbox=[38, 35, 82, 48])])
                page["blocks"][1].update(kind="formula", status="preserved", bbox=[115, 30, 165, 55])
                page["decorations"] = [dict(commands=[dict(op="M", points=[left, 45]), dict(op="L", points=[right, 45])],
                                            width=.7, dash=[], dashPhase=0, lineCap=0, lineJoin=0, color=[0, 0, 0], opacity=1)
                                       for left, right in [(40, 80), (120, 160)]]
                self.save_modified(event, artifact); self.store.commit_result(self.doc["id"], attempt, event)
                result = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
                self.assertIsNone(result["decorations_error"])
                image = pymupdf.Pixmap(result["decorationsImage"]); scale = 2400 / 842
                inline_alpha = image.pixel(round(60 * scale), round(45 * scale))[3]
                self.assertEqual(inline_alpha, 0 if status == "translated" else 255)
                self.assertGreater(image.pixel(round(140 * scale), round(45 * scale))[3], 240)

    def test_dense_decoration_page_batches_pdf_stream_updates(self):
        attempt, event, artifact = self.prepare()
        artifact["pages"][0]["decorations"] = [
            dict(commands=[dict(op="M", points=[20, 20 + index]), dict(op="L", points=[200, 20 + index])],
                 width=.4 if index % 2 else 1, dash=[2, 1] if index % 3 else [], dashPhase=0,
                 lineCap=index % 3, lineJoin=index % 3, color=[0, 0, 0], opacity=1)
            for index in range(250)]
        self.save_modified(event, artifact); self.store.commit_result(self.doc["id"], attempt, event)
        # Some PyMuPDF builds expose a different public Shape alias from the
        # class returned by Page.new_shape(); instrument the actual class.
        with pymupdf.open() as probe:
            shape_type = type(probe.new_page().new_shape())
        original = shape_type.commit; commits = []
        def tracked(shape, *args, **kwargs):
            commits.append(1)
            return original(shape, *args, **kwargs)
        with mock.patch.object(shape_type, "commit", tracked):
            result = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        self.assertIsNone(result["decorations_error"])
        self.assertTrue(Path(result["decorationsImage"]).exists())
        # Cost must depend on logical rendering stages, not the path count.
        self.assertGreater(len(commits), 0)
        self.assertLessEqual(len(commits), 3)

    def test_decoration_and_block_pixels_align_with_nonzero_origin_and_rotation(self):
        # The first box pair reproduces a published report whose displayed page
        # starts at (18, 18). At the preview scale it produces a one-pixel width
        # difference if the original and mask are rounded in different frames.
        geometries = [("offset", 0, None), ("offset-rotated", 90, None),
                      ("cropped-rotated", 270, pymupdf.Rect(30, 40, 580, 730))]
        for name, rotation, crop, coordinate_space in [
                (name + "-" + space, rotation, crop, space)
                for name, rotation, crop in geometries for space in ("legacy", "page")]:
            with self.subTest(name=name):
                source = self.root / (name + ".pdf")
                with pymupdf.open() as pdf:
                    for _ in range(3):
                        pdf.new_page(width=630, height=810)
                    original = pdf[1]
                    original.draw_line((80, 120), (260, 120), color=(1, 0, 0), width=2)
                    original.draw_line((120, 170), (170, 170), color=(0, 0, 0), width=2)
                    original.draw_rect(pymupdf.Rect(310, 300, 370, 360), color=None, fill=(0, 0, 1))
                    if crop is None:
                        pdf.xref_set_key(original.xref, "MediaBox", "[18 18 612 792]")
                        pdf.xref_set_key(original.xref, "CropBox", "[0 0 630 810]")
                    else:
                        original.set_cropbox(crop)
                    original = pdf.reload_page(original)
                    original.set_rotation(rotation)
                    pdf.save(source)
                self.doc = self.store.import_pdf(source)
                original_hash = sha256(self.doc["input_path"])
                attempt, event, artifact = self.prepare()
                page = artifact["pages"][0]
                with pymupdf.open(source) as pdf:
                    original = pdf[1]
                    rect, rotation_matrix = original.rect, original.rotation_matrix
                    origin = rect.tl
                    drawings = original.get_drawings()
                    native_lines = [(item[1] * rotation_matrix, item[2] * rotation_matrix)
                                    for drawing in drawings[:2] for item in drawing["items"] if item[0] == "l"]
                    formula_box = (drawings[1]["rect"] * rotation_matrix) + (-4, -4, 4, 4)
                    formula_box += (-origin.x, -origin.y, -origin.x, -origin.y)
                    blue_box = drawings[2]["rect"] * rotation_matrix
                    blue_box += (-origin.x, -origin.y, -origin.x, -origin.y)
                    path_origin = origin if coordinate_space == "page" else pymupdf.Point(0, 0)
                    page.update(width=rect.width, height=rect.height,
                                decorations=[dict(commands=[dict(op="M", points=list(start - path_origin)), dict(op="L", points=list(end - path_origin))],
                                                  width=2, dash=[], dashPhase=0, lineCap=0, lineJoin=0, color=[0, 0, 0], opacity=1)
                                             for start, end in native_lines])
                    if coordinate_space == "page":
                        page["decorationsCoordinateSpace"] = "page"
                    page["blocks"][0].update(bbox=list(formula_box), inlineMath=[dict(token="[[M1]]", latex="a/b", bbox=list(formula_box))])
                    page["blocks"][1].update(bbox=list(blue_box))
                    scale = min(1800 / rect.width, 2400 / rect.height)
                    matrix = pymupdf.Matrix(scale, scale)
                    raster = original.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
                self.save_modified(event, artifact)
                self.store.commit_result(self.doc["id"], attempt, event)
                restored = DocumentStore(self.store.root)
                result = structured.page_result(restored, dict(document_id=self.doc["id"], page=1))
                self.assertIsNone(result["decorations_error"])
                image = pymupdf.Pixmap(result["decorationsImage"])
                self.assertEqual((image.width, image.height), (raster.width, raster.height))
                for index, (start, end) in enumerate(native_lines):
                    point = ((start + end) / 2) * matrix - pymupdf.Point(raster.x, raster.y)
                    pixel = image.pixel(round(point.x), round(point.y))
                    if index == 0:
                        self.assertGreater(pixel[0], 240)
                        self.assertLess(pixel[1], 30)
                        self.assertGreater(pixel[3], 240)
                    else:
                        self.assertEqual(pixel[3], 0, "Moved inline formula must not leave its native fraction bar")
                block = structured.block_image(restored, dict(document_id=self.doc["id"], block_id="p2-b2"))
                blue = pymupdf.Pixmap(block["path"])
                self.assertEqual((round(block["width"]), round(block["height"])), (60, 60))
                self.assertEqual(blue.pixel(4, 4), (0, 0, 255))
                self.assertEqual(blue.pixel(blue.width // 2, blue.height // 2), (0, 0, 255))
                self.assertEqual(sha256(self.doc["input_path"]), original_hash)

    def test_page_geometry_matches_original_raster_for_rotated_crop(self):
        rotated = self.root / "rotated-crop.pdf"
        with pymupdf.open() as pdf:
            for _ in range(3):
                pdf.new_page(width=320, height=240).insert_text((40, 50), "Source")
            pdf[0].set_cropbox(pymupdf.Rect(20, 10, 300, 230))
            pdf[0].set_rotation(90)
            pdf.save(rotated)
        self.doc = self.store.import_pdf(rotated)
        attempt, event, artifact = self.prepare()
        artifact["pages"][0].update(width=320, height=240)
        self.save_modified(event, artifact)
        self.store.commit_result(self.doc["id"], attempt, event)
        page = structured.page_result(self.store, dict(document_id=self.doc["id"], page=0))
        self.assertEqual((page["width"], page["height"]), (220, 280))
        self.assertEqual(page["page_status"], "not_selected")
        bridge = Path(__file__).resolve().parents[1] / "bridge.py"
        request = dict(action="render", dataRoot=str(self.store.root), document_id=self.doc["id"], variant="original", page=0, width=660)
        result = subprocess.run([sys.executable, "-B", str(bridge)], input=(json.dumps(request) + "\n").encode(), capture_output=True, timeout=15)
        response = json.loads(result.stdout)
        self.assertTrue(response["ok"], response)
        raster = pymupdf.Pixmap(response["result"]["path"])
        self.assertEqual((raster.width, raster.height), (page["width"] * 3, page["height"] * 3))
        # A selected page uses the same source geometry, regardless of the
        # original file's different page sizes or the selected page number.
        translated = structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))
        self.assertEqual((translated["width"], translated["height"]), (320, 240))

    def test_task_identity_source_hash_and_output_path_checked(self):
        attempt, event, _ = self.prepare()
        for changes in ({"attemptId": "stale"}, {"readingPath": str(self.source)}, {"readingSHA256": "0" * 64}):
            with self.subTest(changes=changes), self.assertRaises(AppError):
                self.store.commit_result(self.doc["id"], attempt, dict(event, **changes))
        Path(self.doc["input_path"]).write_bytes(b"modified")
        with self.assertRaisesRegex(AppError, "原文"):
            self.store.commit_result(self.doc["id"], attempt, event)

    def test_structural_validation_rejects_wrong_pages_and_duplicate_ids(self):
        attempt, event, artifact = self.prepare()
        for change in (lambda d: d.update(selectedPages=[3]),
                       lambda d: d["pages"][0]["blocks"][1].update(id="p2-b1"),
                       lambda d: d["pages"][0]["blocks"][0].update(bbox=[0, 0, float("nan"), 10])):
            candidate = copy.deepcopy(artifact); change(candidate)
            # allow_nan only for this deliberately malformed protocol fixture.
            Path(event["readingPath"]).write_text(json.dumps(candidate))
            with self.assertRaises(AppError):
                structured.read_artifact(event["readingPath"], self.doc)

    def test_real_bridge_reads_page_and_crop_after_restart(self):
        attempt, event, _ = self.prepare(partial=True)
        self.store.commit_result(self.doc["id"], attempt, event)
        bridge = Path(__file__).resolve().parents[1] / "bridge.py"
        for action, extra in [("reading_page", {"page": 1}), ("reading_block_image", {"block_id": "p2-b2"})]:
            request = dict(action=action, dataRoot=str(self.store.root), document_id=self.doc["id"], **extra)
            result = subprocess.run([sys.executable, "-B", str(bridge)], input=(json.dumps(request) + "\n").encode(), capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stderr, b"")
            response = json.loads(result.stdout)
            self.assertTrue(response["ok"], response)
            if action == "reading_page":
                self.assertEqual(response["result"]["blocks"][1]["status"], "source_fallback")
                self.assertEqual((response["result"]["width"], response["result"]["height"]), (595, 842))
            else:
                self.assertTrue(Path(response["result"]["path"]).exists())

    def test_new_attempt_rpc_does_not_load_or_mutate_reading_retry_state(self):
        attempt, event, _ = self.prepare(partial=True)
        committed = self.store.commit_result(self.doc["id"], attempt, event)
        bridge = Path(__file__).resolve().parents[1] / "bridge.py"
        request = dict(action="new_attempt", dataRoot=str(self.store.root), document_id=self.doc["id"],
                       outputMode="reading", retryBlockIds=["p2-b2"])
        result = subprocess.run([sys.executable, "-B", str(bridge)], input=(json.dumps(request) + "\n").encode(), capture_output=True, timeout=15)
        response = json.loads(result.stdout)
        self.assertTrue(response["ok"], response)
        self.assertTrue(Path(response["result"]["directory"]).is_dir())
        restored = DocumentStore(self.store.root).get(self.doc["id"])
        self.assertEqual(restored["reading_path"], committed["reading_path"])
        self.assertEqual(restored["status"], "partial")

    def test_post_commit_tampering_is_not_silently_read(self):
        attempt, event, _ = self.prepare()
        result = self.store.commit_result(self.doc["id"], attempt, event)
        Path(result["reading_path"]).write_text("{}")
        with self.assertRaisesRegex(AppError, "发生变化"):
            structured.page_result(self.store, dict(document_id=self.doc["id"], page=1))


if __name__ == "__main__":
    unittest.main()
