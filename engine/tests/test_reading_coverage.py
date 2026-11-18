from pathlib import Path
import sys
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reading_coverage import reconcile_native_text


def parsed(page, *regions):
    blocks = [dict(id=f"p00001-b{n:05d}", kind=kind, sourceText=text, bbox=bbox, status="preserved")
              for n, (kind, text, bbox) in enumerate(regions, 1)]
    return dict(pageNumber=1, width=page.rect.width, height=page.rect.height, blocks=blocks,
                status="completed" if blocks else "source_fallback")


class NativeCoverageTests(unittest.TestCase):
    def test_missing_column_and_spacing_use_independent_source_lines(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page(width=400, height=300)
            page.insert_text((15, 40), "Optical density")
            page.insert_textbox((210, 26, 392, 88), "Missing paragraph\ncontinues here.")
            page.insert_text((210, 120), "Next paragraph.")
            result = parsed(page, ("heading", "O p t i c a l  d e n s i t y", [14, 20, 150, 43]),
                            ("text", "Next paragraph.", [208, 100, 380, 124]))
            reconcile_native_text(page, result)
            left, recovered, right = result["blocks"]
            self.assertEqual(left["sourceText"], "Optical density")
            self.assertEqual((recovered["sourceType"], recovered["sourceText"]),
                             ("native_text", "Missing paragraph continues here."))
            self.assertGreaterEqual(recovered["bbox"][0], 210)
            self.assertEqual([b["id"] for b in (left, right)], ["p00001-b00001", "p00001-b00002"])
            reconcile_native_text(page, result)
            self.assertEqual(len(result["blocks"]), 3)

    def test_owned_regions_and_hidden_search_text_are_not_added(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            for y, text in [(45, "Pressure"), (75, "x + y"), (105, "print(x)"), (135, "Hidden search text")]:
                page.insert_text((30, y), text, render_mode=3 if y == 135 else 0)
            page.insert_text((30, 165), "Label over diagram")
            result = parsed(page, ("table", "", [18, 24, 210, 50]), ("formula", "", [18, 54, 210, 80]),
                            ("code", "", [18, 84, 210, 110]), ("figure", "", [18, 144, 220, 180]))
            reconcile_native_text(page, result)
            self.assertEqual([b["sourceText"] for b in result["blocks"] if b.get("sourceType") == "native_text"],
                             ["Label over diagram"])

    def test_empty_parse_and_unpositioned_blocks(self):
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((40, 40), "Actual words")
            result = parsed(page)
            reconcile_native_text(page, result)
            self.assertEqual((result["status"], result["blocks"][0]["sourceText"]), ("completed", "Actual words"))
            unpositioned = parsed(page, ("text", "Actual words", None))
            reconcile_native_text(page, unpositioned)
            self.assertEqual(len(unpositioned["blocks"]), 1)
