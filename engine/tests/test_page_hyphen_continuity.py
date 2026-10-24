import copy
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import translation_guard as g
from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfFormula, PdfParagraphComposition, PdfSameStyleCharacters, PdfStyle, VisualBbox


PREFIX = 'This earlier complete sentence remains on its own page and explains how the source example should be read and understood. '
HEAD = 'If you turn to the web, you have to be very careful, because the frontiers between data visualization, info-'
LEFT = PREFIX + HEAD
RIGHT = ('graphics, design, and art are becoming thinner and thinner. '
         'There are many online examples whose design serves a different purpose.')


def paragraph(text, box, label='plain text'):
    style = PdfStyle(font_id='body', font_size=10)
    width = (box[2]-box[0])/max(1, len(text))
    y = box[3]-10 if box[0] < 100 else box[1]
    chars = [PdfCharacter(char_unicode=c, pdf_style=style,
        box=Box(box[0]+i*width, y, box[0]+(i+1)*width, y+10),
        visual_bbox=VisualBbox(Box(box[0]+i*width, y, box[0]+(i+1)*width, y+10)),
        xobj_id=0, pdf_character_id=ord(c)) for i, c in enumerate(text)]
    composition = PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(
        pdf_character=chars, pdf_style=style))
    return NS(unicode=text, box=Box(*box), pdf_style=style, layout_label=label,
        vertical=False, xobj_id=0, first_line_indent=False,
        pdf_paragraph_composition=[composition])


def document():
    font = NS(font_id='body', xref_id=15, name='SourceBody', encoding_length=1,
              bold=0, italic=0, monospace=0, serif=1)
    def page(number, paragraphs, forms=()):
        return NS(page_number=number, mediabox=NS(box=Box(0, 0, 600, 800)),
            pdf_paragraph=paragraphs, pdf_font=[copy.copy(font)], pdf_form=list(forms),
            pdf_figure=[], pdf_character=[])
    left = paragraph(LEFT, (400, 200, 560, 320))
    right = paragraph(RIGHT, (50, 580, 210, 700))
    lp = page(4, [paragraph('Earlier body prose occupies another column on this page.', (50, 200, 210, 320)),
                  left, paragraph('Figure 5. A floating caption follows.', (50, 100, 560, 180), 'figure_caption')])
    middle = page(5, [paragraph('Figure 6. This page has only a figure caption.', (50, 100, 560, 180), 'figure_caption')], [NS()])
    rp = page(6, [right, paragraph('Other body prose occupies the next column on this page.', (250, 300, 410, 700))])
    return NS(page=[lp, middle, rp]), left, right


def snapshot(docs):
    return [(id(p), p.unicode, id(p.box), id(p.pdf_paragraph_composition),
        [(id(c), c.char_unicode, tuple(getattr(c.box, k) for k in ('x','y','x2','y2')), c.xobj_id, c.pdf_character_id,
          c.formula_layout_id, id(c.pdf_style))
         for comp in p.pdf_paragraph_composition for c in comp.pdf_same_style_characters.pdf_character])
        for page in docs.page for p in page.pdf_paragraph]


class PageHyphenContinuityTests(unittest.TestCase):
    def test_native_hyphen_pair_is_read_only(self):
        docs, left, right = document()
        before = snapshot(docs)
        self.assertEqual(g._source_page_hyphen_continuations(docs), [(left, right)])
        self.assertEqual(snapshot(docs), before)

    def test_rehome_keeps_all_native_glyph_fields_body_order_and_target_boxes(self):
        import layout_guard
        docs, left, right = document()
        chars = lambda p: layout_guard._definition_chars(p)
        before_chars = chars(left)+chars(right)
        before = snapshot(docs)
        boxes = (left.box, right.box)
        owner = NS(translation_config=NS(skip_translation=False, min_text_length=5))
        self.assertEqual(g._rehome_page_hyphen_sentence_heads(owner, docs), 1)
        self.assertEqual(left.unicode, PREFIX.rstrip())
        self.assertEqual(right.unicode, HEAD+RIGHT)
        self.assertEqual([id(c) for c in chars(left)+chars(right)], [id(c) for c in before_chars])
        after = snapshot(docs)
        old_fields = {row[0]: row for p in before for row in p[-1]}
        new_fields = {row[0]: row for p in after for row in p[-1]}
        self.assertEqual(old_fields, new_fields)
        self.assertIs(left.box, boxes[0]); self.assertIs(right.box, boxes[1])
        self.assertTrue(layout_guard.source_sentence_was_rejoined(left))
        self.assertTrue(layout_guard.source_sentence_was_rejoined(right))
        self.assertFalse(layout_guard.source_sentence_was_rejoined(copy.copy(right)))
        self.assertEqual(g._rehome_page_hyphen_sentence_heads(owner, docs), 0)

    def test_citation_formula_keeps_its_original_owner_and_glyphs(self):
        import layout_guard
        docs, left, right = document()
        end = right.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[-1]
        chars = []
        for i, text in enumerate('[9]'):
            char = copy.copy(end)
            char.char_unicode = text; char.pdf_character_id = ord(text)
            char.box = Box(end.box.x2+i*3, end.box.y, end.box.x2+(i+1)*3, end.box.y2)
            char.visual_bbox = VisualBbox(copy.copy(char.box))
            chars.append(char)
        formula = PdfFormula(pdf_character=chars)
        right.pdf_paragraph_composition.append(PdfParagraphComposition(pdf_formula=formula))
        right.unicode += '[9]'
        owner = NS(translation_config=NS(skip_translation=False, min_text_length=5))
        original_order = layout_guard._definition_chars(left)+layout_guard._definition_chars(right)
        formula_fields = [(id(c), c.char_unicode, c.pdf_character_id, c.box) for c in chars]
        self.assertEqual(g._rehome_page_hyphen_sentence_heads(owner, docs), 1)
        self.assertIs(right.pdf_paragraph_composition[-1].pdf_formula, formula)
        self.assertFalse(any(c.pdf_formula for c in left.pdf_paragraph_composition))
        self.assertEqual(formula_fields, [(id(c), c.char_unicode, c.pdf_character_id, c.box) for c in formula.pdf_character])
        self.assertEqual([id(c) for c in original_order],
                         [id(c) for c in layout_guard._definition_chars(left)+layout_guard._definition_chars(right)])

    def test_intervening_content_and_ambiguous_geometry_or_glyphs_fail_closed(self):
        mutations = {
            'mislabelled_body_on_float_page': lambda d,l,r: setattr(d.page[1].pdf_paragraph[0], 'layout_label', 'abandon'),
            'mislabelled_body_after_left': lambda d,l,r: setattr(d.page[0].pdf_paragraph[-1], 'layout_label', 'abandon'),
            'body_on_float_page': lambda d,l,r: setattr(d.page[1].pdf_paragraph[0], 'layout_label', 'plain text'),
            'title_on_float_page': lambda d,l,r: setattr(d.page[1].pdf_paragraph[0], 'layout_label', 'title'),
            'unknown_on_float_page': lambda d,l,r: setattr(d.page[1].pdf_paragraph[0], 'layout_label', 'unknown'),
            'no_figure': lambda d,l,r: setattr(d.page[1], 'pdf_form', []),
            'free_text': lambda d,l,r: setattr(d.page[1], 'pdf_character', [NS()]),
            'missing_page': lambda d,l,r: setattr(d.page[1], 'page_number', 9),
            'different_media': lambda d,l,r: setattr(d.page[2].mediabox.box, 'x2', 610),
            'later_title': lambda d,l,r: setattr(d.page[0].pdf_paragraph[-1], 'layout_label', 'title'),
            'overlapping_caption': lambda d,l,r: setattr(d.page[0].pdf_paragraph[-1].box, 'y2', 230),
            'new_right_paragraph': lambda d,l,r: setattr(r, 'first_line_indent', True),
            'wrong_font_resource': lambda d,l,r: setattr(d.page[2].pdf_font[0], 'xref_id', 99),
            'changed_face': lambda d,l,r: setattr(d.page[2].pdf_font[0], 'name', 'OtherFont'),
            'no_hyphen': lambda d,l,r: setattr(l, 'unicode', l.unicode[:-1]),
            'uppercase_head': lambda d,l,r: setattr(r, 'unicode', r.unicode.capitalize()),
            'changed_word': lambda d,l,r: setattr(l, 'unicode', l.unicode.replace('info-', 'other-')),
            'formula_glyph': lambda d,l,r: setattr(r.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[0], 'formula_layout_id', 5),
            'generated_glyph': lambda d,l,r: setattr(r.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[0], 'pdf_character_id', None),
            'wrong_object': lambda d,l,r: setattr(r, 'xobj_id', 2),
            'wrong_end': lambda d,l,r: setattr(l.box, 'x2', 570),
            'nonfinite': lambda d,l,r: setattr(l.box, 'x', float('nan')),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                docs, left, right = document()
                mutate(docs, left, right)
                self.assertEqual(g._source_page_hyphen_continuations(docs), [])

    def test_rehome_requires_complete_plain_sentence_and_does_not_cross_documents(self):
        import layout_guard
        docs, left, right = document()
        owner = NS(translation_config=NS(skip_translation=True, min_text_length=5))
        before = snapshot(docs)
        self.assertEqual(g._rehome_page_hyphen_sentence_heads(owner, docs), 0)
        self.assertEqual(snapshot(docs), before)
        owner.translation_config.skip_translation=False
        self.assertEqual(g._rehome_page_hyphen_sentence_heads(owner, docs), 1)
        next_docs, next_left, next_right = document()
        self.assertFalse(layout_guard.source_sentence_was_rejoined(next_right))
        # An unfinished paragraph without a complete previous sentence cannot
        # have its only source sentence moved off the original page.
        next_left.unicode=HEAD
        self.assertEqual(g._rehome_page_hyphen_sentence_heads(owner, next_docs), 0)

    def test_real_margin_footer_is_allowed_but_large_or_central_abandon_is_not(self):
        docs, left, right = document()
        footer = paragraph('Journal | Volume 10 | 2014', (50, 20, 210, 30), 'abandon')
        docs.page[1].pdf_paragraph.append(footer)
        self.assertEqual(g._source_page_hyphen_continuations(docs), [(left, right)])
        footer.box.y=150; footer.box.y2=160
        self.assertEqual(g._source_page_hyphen_continuations(docs), [])
        footer.box.y=20; footer.box.y2=70
        self.assertEqual(g._source_page_hyphen_continuations(docs), [])


if __name__ == '__main__':
    unittest.main()
