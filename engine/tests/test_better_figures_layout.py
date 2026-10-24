"""Source glyph/geometry proof for the better-figures list regressions."""
import copy
from dataclasses import asdict
import unittest

try:
    from .test_layout_guard import (
        guard as g, line_paragraph, page, ParagraphFinder,
        PdfParagraphComposition, PdfLine, PdfFormula,
    )
except ImportError:
    from test_layout_guard import (
        guard as g, line_paragraph, page, ParagraphFinder,
        PdfParagraphComposition, PdfLine, PdfFormula,
    )


class BetterFiguresLayout(unittest.TestCase):
    def setUp(self):
        g._hanging_indents.clear()
        g._counters.clear()
        self.finder = object.__new__(ParagraphFinder)

    def fixture(self, count=3, merged=True):
        # Capture-shaped geometry: the legacy bullet's baseline is lower and
        # its 16 pt font much larger than the 9 pt body. Its visible glyph is a
        # dot although ToUnicode reports N. The original glyph is never edited.
        titles = ['Sequential', 'Diverging', 'Qualitative'][:count]
        p = line_paragraph([text for title in titles for text in
                            ('N ' + title + ': category description', 'continued category description.')], size=9)
        for index, comp in enumerate(p.pdf_paragraph_composition):
            chars = comp.pdf_line.pdf_character
            y = 500 - index * 10.65
            for c in chars:
                c.box.y = c.visual_bbox.box.y = y
                c.box.y2 = c.visual_bbox.box.y2 = y + 9
            if index % 2 == 0:
                marker = chars[0]
                marker.pdf_style.font_id = 'F1'
                marker.pdf_style.font_size = 16
                marker.pdf_character_id = 78
                marker.box.y = marker.visual_bbox.box.y = y - 1.8
                marker.box.y2 = marker.visual_bbox.box.y2 = y + 14.2
                marker.box.x2 = marker.visual_bbox.box.x2 = marker.box.x + 4.5
                for c in chars[2:]:
                    c.box.x += .4; c.box.x2 += .4
                    c.visual_bbox.box.x += .4; c.visual_bbox.box.x2 += .4
            else:
                for c in chars:
                    c.box.x += 10.4; c.box.x2 += 10.4
                    c.visual_bbox.box.x += 10.4; c.visual_bbox.box.x2 += 10.4
        self.finder.update_paragraph_data(p, update_unicode=True)
        fixture = page([], name='ABCDEF+AdvP7CA8')
        if merged:
            fixture.pdf_paragraph = [p]
        else:
            fixture.pdf_paragraph = []
            for index in range(count):
                item = copy.copy(p)
                item.pdf_paragraph_composition = p.pdf_paragraph_composition[index * 2:index * 2 + 2]
                self.finder.update_paragraph_data(item, update_unicode=True)
                fixture.pdf_paragraph.append(item)
        return fixture

    @staticmethod
    def chars(fixture):
        return [c for p in fixture.pdf_paragraph for co in p.pdf_paragraph_composition
                for c in g._composition_characters(co)]

    def test_repeated_legacy_native_markers_split_merged_source_without_rewriting_glyphs(self):
        fixture = self.fixture()
        before = self.chars(fixture)
        fields = [asdict(c) for c in before]
        self.assertEqual(len(g._source_legacy_bullet_markers(fixture)), 3)
        self.assertEqual(g.split_structured_entries(self.finder, fixture), 2)
        self.assertEqual([p.unicode.split(':')[0] for p in fixture.pdf_paragraph],
                         ['N Sequential', 'N Diverging', 'N Qualitative'])
        after = self.chars(fixture)
        self.assertEqual([id(c) for c in before], [id(c) for c in after])
        self.assertEqual(fields, [asdict(c) for c in after])
        self.assertEqual(len(g._hanging_indents), 3)
        for _, indent in g._hanging_indents.values():
            self.assertAlmostEqual(indent, 10.4)

    def test_already_separated_source_items_receive_only_proven_hanging_indents(self):
        fixture = self.fixture(merged=False)
        originals = fixture.pdf_paragraph[:]
        before = self.chars(fixture)
        self.assertEqual(g.split_structured_entries(self.finder, fixture), 0)
        self.assertEqual([id(p) for p in originals], [id(p) for p in fixture.pdf_paragraph])
        self.assertEqual([id(c) for c in before], [id(c) for c in self.chars(fixture)])
        self.assertEqual(len(g._hanging_indents), 3)

    def test_font_letter_or_single_marker_alone_never_proves_a_list(self):
        for mode in ('font', 'cid', 'unicode', 'body-font', 'body-size', 'horizontal-gap',
                     'marker-column', 'body-column', 'inline', 'single', 'code', 'formula'):
            fixture = self.fixture(count=2)
            p = fixture.pdf_paragraph[0]
            marker = p.pdf_paragraph_composition[2].pdf_line.pdf_character[0]
            body = p.pdf_paragraph_composition[2].pdf_line.pdf_character[2]
            if mode == 'font': fixture.pdf_font[0].name = 'ABCDEF+AdvP7CA9'
            elif mode == 'cid': marker.pdf_character_id = 79
            elif mode == 'unicode': marker.char_unicode = 'M'
            elif mode == 'body-font': body.pdf_style.font_id = 'F1'
            elif mode == 'body-size': body.pdf_style.font_size = 16
            elif mode == 'horizontal-gap': body.box.x += 40
            elif mode == 'marker-column': marker.box.x += 2
            elif mode == 'body-column':
                p.pdf_paragraph_composition[3].pdf_line.pdf_character[0].box.x += 2
            elif mode == 'inline': marker.box.y = body.box.y = 500
            elif mode == 'single': p.pdf_paragraph_composition = p.pdf_paragraph_composition[:2]
            elif mode == 'code': p.layout_label = 'code'
            elif mode == 'formula':
                f = PdfFormula(pdf_character=p.pdf_paragraph_composition[2].pdf_line.pdf_character[:1])
                p.pdf_paragraph_composition[2] = PdfParagraphComposition(pdf_formula=f)
            before = self.chars(fixture)
            with self.subTest(mode=mode):
                self.assertEqual(g._source_legacy_bullet_markers(fixture), {})
                self.assertEqual(g.split_structured_entries(self.finder, fixture), 0)
                self.assertEqual([id(c) for c in before], [id(c) for c in self.chars(fixture)])
                if mode == 'formula': self.assertIs(p.pdf_paragraph_composition[2].pdf_formula, f)

    def test_continuation_date_does_not_cancel_explicit_consecutive_references(self):
        p = line_paragraph(['5. First author (2007) reference.', '6. Second author (2008) reference.',
                            '7. Third author (2011) reference. Accessed', '12 August 2014.'])
        for c in p.pdf_paragraph_composition[-1].pdf_line.pdf_character:
            c.box.x += 15; c.box.x2 += 15
            c.visual_bbox.box.x += 15; c.visual_bbox.box.x2 += 15
        fixture = page([]); fixture.pdf_paragraph = [p]
        before = self.chars(fixture)
        self.assertEqual(g.split_structured_entries(self.finder, fixture), 2)
        self.assertEqual([p.unicode.split('.')[0] for p in fixture.pdf_paragraph], ['5', '6', '7'])
        self.assertIn('12 August 2014.', fixture.pdf_paragraph[-1].unicode)
        self.assertEqual([id(c) for c in before], [id(c) for c in self.chars(fixture)])

    def test_mixed_punctuation_list_keeps_its_unpunctuated_item(self):
        p = line_paragraph(['5. First source.', '6. Second source.', '7 Third source.'])
        fixture = page([]); fixture.pdf_paragraph = [p]
        self.assertEqual(g.split_structured_entries(self.finder, fixture), 2)
        self.assertEqual(len(fixture.pdf_paragraph), 3)
        self.assertTrue(fixture.pdf_paragraph[-1].unicode.startswith('7 Third'))

    def test_date_shape_without_complete_source_access_relation_is_not_excluded(self):
        for mode in ('no-accessed', 'invalid-calendar', 'wrong-column', 'extra-text'):
            p = line_paragraph(['5. First source.', '6. Second source. Accessed', '12 August 2014.'])
            if mode == 'no-accessed': p.pdf_paragraph_composition[1] = line_paragraph(['6. Second source.'], y=486).pdf_paragraph_composition[0]
            if mode == 'invalid-calendar': p.pdf_paragraph_composition[-1] = line_paragraph(['31 February 2014.'], y=472).pdf_paragraph_composition[0]
            if mode == 'extra-text': p.pdf_paragraph_composition[-1] = line_paragraph(['12 August 2014 extra.'], y=472).pdf_paragraph_composition[0]
            if mode != 'wrong-column':
                for c in p.pdf_paragraph_composition[-1].pdf_line.pdf_character:
                    c.box.x += 15; c.box.x2 += 15
                    c.visual_bbox.box.x += 15; c.visual_bbox.box.x2 += 15
            fixture = page([]); fixture.pdf_paragraph = [p]
            with self.subTest(mode=mode):
                self.assertEqual(g.split_structured_entries(self.finder, fixture), 0)

    def test_reference_numbers_still_require_consecutiveness_and_alignment(self):
        for mode in ('nonconsecutive', 'column', 'dates-only'):
            texts = ['5. First source.', '6. Second source.', '12 August 2014.']
            if mode == 'nonconsecutive': texts[1] = '8. Second source.'
            if mode == 'dates-only': texts = ['12 August 2014.', '19 September 2015.']
            p = line_paragraph(texts)
            if mode == 'column':
                for c in p.pdf_paragraph_composition[1].pdf_line.pdf_character:
                    c.box.x += 8; c.box.x2 += 8
                    c.visual_bbox.box.x += 8; c.visual_bbox.box.x2 += 8
            fixture = page([]); fixture.pdf_paragraph = [p]
            with self.subTest(mode=mode):
                self.assertEqual(g.split_structured_entries(self.finder, fixture), 0)


if __name__ == '__main__':
    unittest.main()
