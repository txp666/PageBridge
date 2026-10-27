import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reading_tables import refine_native_tables


class NativeTableTests(unittest.TestCase):
    def test_native_labels_keep_value_columns_out_of_model_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "table.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=600, height=300)
                for x, y, text in [(40, 45, "Asset values"), (330, 70, "2023"), (430, 70, "2024"),
                                   (40, 100, "Corporate and other"), (330, 100, "1,543"), (430, 100, "1,041"),
                                   (40, 125, "Equity securities"), (330, 125, "271,588"), (430, 125, "261,910"),
                                   (40, 150, "Liabilities"), (330, 150, "(234)"), (430, 150, "$76")]:
                    page.insert_text((x, y), text, fontsize=10)
                pdf.save(path)
            source = {"pageNumber": 1, "width": 600, "height": 300, "blocks": [
                dict(id="p00001-b00001", kind="table", sourceText="Corporate | 261,910 | wrong row", status="preserved", bbox=[30, 50, 520, 160]),
            ]}
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            refine_native_tables(path, 1, source)
            table = source["blocks"][0]
            labels = source["blocks"][1:]
            self.assertEqual(table["sourceType"], "native_table")
            self.assertEqual(table["status"], "preserved")
            self.assertNotIn("wrong row", table["sourceText"])
            rows = table["sourceText"].splitlines()
            self.assertTrue(any("Corporate" in row and "1,543" in row and "261,910" not in row for row in rows))
            self.assertTrue(any("Equity" in row and "261,910" in row for row in rows))
            self.assertEqual([label["sourceText"] for label in labels], ["Corporate and other", "Equity securities", "Liabilities"])
            self.assertTrue(all(label["kind"] == "text" and label["sourceType"] == "table_cell" for label in labels))
            self.assertTrue(all(label["bbox"][2] < 330 for label in labels))
            self.assertTrue(all(not any(c.isnumeric() for c in label["sourceText"]) for label in labels))
            corporate_context = labels[0]["translationContext"]
            self.assertIn("2023", corporate_context)
            self.assertIn("2024", corporate_context)
            self.assertIn("Corporate  and  other  1,543  1,041", corporate_context)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_repeated_labels_at_distinct_coordinates_and_clipped_last_row_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "repeated-labels.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=500, height=400)
                for top in (40, 200):
                    page.insert_text((40, top), "Accident year", fontsize=10)
                    page.insert_text((350, top), "2024", fontsize=10)
                    page.insert_text((40, top + 40), "Losses and ALAE incurred", fontsize=10)
                    page.insert_text((350, top + 40), "84,467", fontsize=10)
                pdf.save(path)
            with pymupdf.open(path) as pdf:
                originals = pdf[0].search_for("Losses and ALAE incurred")
            data = {"blocks": [
                dict(id="p00001-b00001", kind="table", sourceText="first", status="preserved", bbox=[30, 25, 450, 90]),
                dict(id="p00001-b00002", kind="table", sourceText="second", status="preserved", bbox=[30, 185, 450, originals[1].y0 + 1]),
            ]}
            refine_native_tables(path, 1, data)
            labels = [b for b in data["blocks"] if b.get("sourceType") == "table_cell" and b["sourceText"] == "Losses and ALAE incurred"]
            self.assertEqual(len(labels), 2)
            self.assertNotEqual(labels[0]["id"], labels[1]["id"])
            for label, original in zip(labels, originals):
                self.assertEqual(label["bbox"], list(original))
                self.assertIn("2024", label["translationContext"])
                self.assertIn("Losses  and  ALAE  incurred  84,467", label["translationContext"])
            self.assertIn("84,467", data["blocks"][1]["sourceText"])

    def test_table_context_is_bounded_and_retains_current_native_row(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "long-table.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=2200, height=600)
                for row in range(20):
                    text = "Header " * 35 if row < 5 else ("Reserve label " * 30 if row < 19 else "Final liability")
                    page.insert_text((30, 30 + row * 25), text, fontsize=8)
                    page.insert_text((2000, 30 + row * 25), str(2020 + row), fontsize=8)
                pdf.save(path)
            data = {"blocks": [dict(id="p00001-b00001", kind="table", sourceText="old", status="preserved", bbox=[20, 15, 2100, 520])]}
            refine_native_tables(path, 1, data)
            labels = [b for b in data["blocks"] if b.get("sourceType") == "table_cell"]
            self.assertTrue(all(0 < len(b["translationContext"]) <= 1200 for b in labels))
            final = next(b for b in labels if b["sourceText"] == "Final liability")
            self.assertIn("Final  liability  2039", final["translationContext"])
            self.assertIn("Header", final["translationContext"])

    def test_existing_caption_is_not_duplicated_when_table_box_overlaps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "caption.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=300, height=220)
                page.insert_text((40, 55), "Financial assets", fontsize=10)
                page.insert_text((40, 85), "Cash", fontsize=10)
                page.insert_text((210, 85), "1234", fontsize=10)
                pdf.save(path)
            data = {"blocks": [
                dict(id="p00001-b00001", kind="heading", sourceText="Financial assets", status="preserved", bbox=[38, 40, 150, 59]),
                dict(id="p00001-b00002", kind="table", sourceText="old table", status="preserved", bbox=[30, 38, 270, 100]),
            ]}
            refine_native_tables(path, 1, data)
            self.assertEqual([b["sourceText"] for b in data["blocks"] if b.get("sourceType") == "table_cell"], ["Cash"])

    def test_nonzero_visible_origin_yields_page_local_label_box(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "offset.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=630, height=810)
                page.insert_text((80, 120), "Equity", fontsize=10)
                page.insert_text((350, 120), "261,910", fontsize=10)
                pdf.xref_set_key(page.xref, "MediaBox", "[18 18 612 792]")
                pdf.xref_set_key(page.xref, "CropBox", "[0 0 630 810]")
                pdf.save(path)
            data = {"blocks": [dict(id="p00001-b00001", kind="table", sourceText="old", status="preserved", bbox=[40, 80, 500, 130])]}
            refine_native_tables(path, 1, data)
            label = next(b for b in data["blocks"] if b.get("sourceType") == "table_cell")
            with pymupdf.open(path) as pdf:
                page = pdf[0]
                expected = pymupdf.Rect(page.get_text("words")[0][:4])
                expected += (-page.rect.x0, -page.rect.y0, -page.rect.x0, -page.rect.y0)
            for actual, value in zip(label["bbox"], expected):
                self.assertAlmostEqual(actual, value, places=4)

    def test_image_only_table_keeps_ocr_source_and_has_no_inferred_cells(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scan.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=300, height=220)
                page.draw_rect((30, 30, 270, 180), color=(0, 0, 0))
                pdf.save(path)
            data = {"blocks": [dict(id="p00001-b00001", kind="table", sourceText="OCR table", status="preserved", bbox=[30, 30, 270, 180])]}
            before = copy.deepcopy(data)
            refine_native_tables(path, 1, data)
            self.assertEqual(data, before)

    def test_hidden_ocr_table_stays_source_even_with_visible_page_header(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "hidden-ocr.pdf"
            with pymupdf.open() as pdf:
                page = pdf.new_page(width=300, height=220)
                page.insert_text((20, 20), "Visible header", fontsize=10)
                page.insert_text((40, 75), "Insurance assets", fontsize=10, render_mode=3)
                page.insert_text((220, 75), "1234", fontsize=10, render_mode=3)
                page.draw_rect((30, 30, 270, 180), color=(0, 0, 0))
                pdf.save(path)
            data = {"blocks": [dict(id="p00001-b00001", kind="table", sourceText="OCR table", status="preserved", bbox=[30, 30, 270, 180])]}
            before = copy.deepcopy(data)
            refine_native_tables(path, 1, data)
            self.assertEqual(data, before)


if __name__ == "__main__":
    unittest.main()
