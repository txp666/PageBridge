import base64
import copy
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pymupdf as fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from reading_background import add_page_background, coalesce_text_redactions


class NativeBackgroundTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def make_source(self, origin=False, hidden=False):
        path = self.root / 'original.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page(width=200, height=160)
            page.draw_rect(page.rect, color=None, fill=(.2, .55, .8))
            page.draw_rect(fitz.Rect(130, 15, 190, 65), color=(.7, .1, .2), fill=(.3, .8, .2))
            page.insert_text((20, 35), 'Hello world', fontsize=12, render_mode=3 if hidden else 0)
            page.insert_text((20, 100), 'www.example.org', fontsize=8)
            page.draw_line((10, 120), (190, 120), width=.3, color=(0, 0, 0))
            if origin:
                pdf.xref_set_key(page.xref, 'MediaBox', '[18 18 218 178]')
                pdf.xref_set_key(page.xref, 'CropBox', '[0 0 218 178]')
            pdf.save(path)
        return path

    def metadata(self, path):
        with fitz.open(path) as pdf:
            page = pdf[0]
            origin = page.rect.tl
            spans = page.get_texttrace()
            hello = fitz.Rect(spans[0]['bbox']) + (-origin.x, -origin.y, -origin.x, -origin.y)
            url = fitz.Rect(spans[1]['bbox']) + (-origin.x, -origin.y, -origin.x, -origin.y)
            return dict(pageNumber=1, width=page.rect.width, height=page.rect.height, blocks=[
                dict(id='p00001-b00001', kind='heading', sourceText='Hello world', translatedText='你好世界',
                     status='translated', bbox=list(hello + (-2, -2, 90, 20))),
                dict(id='p00001-b00002', kind='text', sourceText='www.example.org', translatedText='www.example.org',
                     status='translated', bbox=list(url)),
            ])

    def raster(self, path):
        with fitz.open(path) as pdf:
            page = pdf[0]
            scale = min(1800 / page.rect.width, 2400 / page.rect.height)
            return page.get_pixmap(matrix=fitz.Matrix(scale, scale).pretranslate(-page.rect.x0, -page.rect.y0), alpha=False)

    def test_preserves_artwork_pixels_and_tightens_oversized_heading(self):
        path = self.make_source()
        digest = hashlib.sha256(path.read_bytes()).digest()
        page = self.metadata(path)
        before = self.raster(path)
        add_page_background(path, page)
        after = fitz.Pixmap(base64.b64decode(page['backgroundImage']))
        self.assertEqual(page['backgroundBlocks'], ['p00001-b00001'])
        self.assertLess(page['blocks'][0]['bbox'][2], 100)
        self.assertEqual((before.width, before.height), (after.width, after.height))
        # The large green decoration falls inside the old heading's rectangle.
        # Its pixels and the fine horizontal line must survive unchanged.
        for x, y in [(150, 30), (135, 60), (10, 10), (80, 120), (50, 98)]:
            self.assertEqual(before.pixel(x * 9, y * 9), after.pixel(x * 9, y * 9))
        self.assertNotEqual(before.samples, after.samples)
        self.assertEqual(hashlib.sha256(path.read_bytes()).digest(), digest)
        self.assertEqual(page['blocks'][0]['foregroundColor'], [0, 0, 0])

    def test_nonzero_origin_matches_page_local_raster(self):
        path = self.make_source(origin=True)
        page = self.metadata(path)
        add_page_background(path, page)
        raster = fitz.Pixmap(base64.b64decode(page['backgroundImage']))
        self.assertEqual((raster.width, raster.height), (1800, 1440))
        self.assertGreaterEqual(page['blocks'][0]['bbox'][0], 0)
        self.assertEqual(page['backgroundBlocks'], ['p00001-b00001'])

    def test_hidden_ocr_is_not_treated_as_removable_visible_text(self):
        path = self.make_source(hidden=True)
        page = self.metadata(path)
        add_page_background(path, page)
        # Only a visible URL remains, and it needs no translation; the hidden
        # OCR paragraph cannot be erased from a scanned source image.
        self.assertEqual(page.get('backgroundBlocks'), [])

    def test_unchanged_text_never_gets_erased_or_resized(self):
        path = self.make_source()
        page = self.metadata(path)
        page['blocks'][0]['translatedText'] = 'Hello world'
        add_page_background(path, page)
        self.assertNotIn('backgroundImage', page)
        self.assertNotIn('backgroundBlocks', page)

    def test_coalescing_keeps_unselected_gaps_numbers_and_separate_lines(self):
        class Page:
            def get_texttrace(self):
                return [{'chars': [(ord('2'), 0, None, (20, 0, 25, 10))]}]

        boxes = list(map(fitz.Rect, [(0, 0, 5, 10), (5, 0, 10, 10),
                                    (15, 0, 20, 10), (20, 0, 25, 10),
                                    (25, 0, 30, 10), (30, 0, 35, 10), (0, 11, 5, 21)]))
        merged = coalesce_text_redactions(Page(), boxes)
        self.assertEqual([list(r) for r in merged], [[0, 0, 10, 10], [15, 0, 20, 10],
                                                   [20, 0, 25, 10], [25, 0, 35, 10], [0, 11, 5, 21]])
        self.assertEqual(sum(r.get_area() for r in merged), sum(r.get_area() for r in boxes))

    def test_coalescing_matches_per_glyph_pixels_and_block_metadata(self):
        path = self.make_source()
        merged = self.metadata(path)
        individual = copy.deepcopy(merged)
        with patch('reading_background.coalesce_text_redactions', side_effect=lambda _, rects: rects):
            add_page_background(path, individual)
        add_page_background(path, merged)
        self.assertEqual(merged, individual)

    def test_mixed_color_heading_retains_readable_source_runs(self):
        path = self.root / 'mixed-heading.pdf'
        with fitz.open() as pdf:
            page = pdf.new_page(width=200, height=100)
            page.draw_rect(fitz.Rect(10, 15, 55, 35), fill=(0, 0, 0), color=None)
            page.insert_text((15, 29), 'Part I', fontsize=10, color=(1, 1, 1))
            page.insert_text((65, 29), 'Certification', fontsize=10, color=(0, 0, 0))
            pdf.save(path)
        metadata = dict(pageNumber=1, width=200, height=100, blocks=[dict(
            id='p00001-b00001', kind='heading', status='translated',
            sourceText='Part I Certification', translatedText='第一部分 证明', bbox=[10, 15, 180, 35])])
        before = self.raster(path)
        add_page_background(path, metadata)
        after = fitz.Pixmap(base64.b64decode(metadata['backgroundImage']))
        self.assertEqual(metadata['backgroundBlocks'], [])
        self.assertEqual(before.samples, after.samples)
        self.assertEqual(metadata['blocks'][0]['translatedText'], '第一部分 证明')


if __name__ == '__main__':
    unittest.main()
