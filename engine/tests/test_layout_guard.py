from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout_guard as guard
import babeldoc.const
_test_cache = tempfile.TemporaryDirectory(prefix="pagebridge-layout-tests-")
babeldoc.const.CACHE_FOLDER = Path(_test_cache.name)
from babeldoc.format.pdf.document_il import (
    Box, Cropbox, GraphicState, Page, PdfCharacter, PdfFont, PdfFormula, PdfLine, PdfParagraph,
    PdfParagraphComposition, PdfStyle, PdfXobject, VisualBbox,
)
from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
from babeldoc.format.pdf.document_il.utils.formular_helper import is_formulas_start_char


def character(text="\x1f", glyph=31, font_id="F1", x=0, y=0, xobj_id=-1):
    return PdfCharacter(char_unicode=text, pdf_character_id=glyph,
                        pdf_style=PdfStyle(font_id=font_id, font_size=8,
                                           graphic_state=GraphicState()),
                        box=Box(x, y, x + 8, y + 8),
                        visual_bbox=VisualBbox(Box(x, y, x + 8, y + 8)),
                        advance=8, vertical=False, xobj_id=xobj_id,
                        render_order=17)


def page(chars, name="ABCDEF+Webdings"):
    return Page(pdf_font=[PdfFont(font_id="F1", name=name)], pdf_character=chars)


def toc_row(label="Introduction", page_number="42", y=500, right=500, dots=40):
    text = label + "." * dots + " " + page_number
    widths = ([5] * len(label)
              + [(right - 10 - len(label) * 5 - (len(page_number) + 1) * 5) / dots] * dots
              + [5] * (len(page_number) + 1))
    chars, x = [], 10
    for value, width in zip(text, widths):
        item = character(value, ord(value), font_id="F2", x=x, y=y)
        item.box.x2 = x + width
        item.visual_bbox.box.x2 = x + width
        chars.append(item)
        x += width
    return PdfParagraph(pdf_paragraph_composition=[
        PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))],
        layout_id=1, layout_label="text", vertical=False, debug_id="toc-" + page_number), chars


def line_paragraph(texts, label="plain text", y=500, size=11):
    comps = []
    for row, text in enumerate(texts):
        chars = [character(c,ord(c),font_id="F2",x=10+i*5,y=y-row*14)
                 for i,c in enumerate(text)]
        for char in chars:
            char.pdf_style.font_size=size
            char.box.x2=char.box.x+5
            char.visual_bbox.box.x2=char.box.x2
            char.box.y2=char.box.y+size
            char.visual_bbox.box.y2=char.box.y2
        comps.append(PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars)))
    p=PdfParagraph(layout_label=label,vertical=False,xobj_id=-1,
                   pdf_paragraph_composition=comps)
    object.__new__(ParagraphFinder).update_paragraph_data(p,update_unicode=True)
    return p


class LayoutGuardTests(unittest.TestCase):
    def setUp(self):
        guard._counters.clear()

    def test_known_marker_preserves_original_glyph_and_graphics(self):
        bullet = character()
        original_fields = {slot: getattr(bullet, slot) for slot in bullet.__slots__
                           if slot != "char_unicode"}
        fixture = page([bullet])
        self.assertEqual(guard.normalize_page_markers(fixture), 1)
        self.assertEqual(bullet.char_unicode, "•")
        for slot, value in original_fields.items():
            self.assertIs(getattr(bullet, slot), value)
        # This marker must become a protected original glyph, not generated text.
        self.assertTrue(is_formulas_start_char(
            bullet.char_unicode, SimpleNamespace(has_char=lambda text: True),
            SimpleNamespace(formular_char_pattern=None)))
        self.assertEqual(guard.normalize_page_markers(fixture), 0)
        self.assertEqual(guard.snapshot()["normalizedWebdingsBullets"], 1)

    def test_other_fonts_control_codes_and_empty_glyph_are_untouched(self):
        cases = [("Helvetica", "\x1f", 31), ("Wingdings", "\x1f", 31),
                 ("Webdings-Regular", "\x1f", 31), ("Webdings", "\x03", 31),
                 ("Webdings", "\x1f", 3), ("Webdings", "x", 31),
                 ("Webdings", "\x1e", 31)]
        for name, text, glyph in cases:
            with self.subTest(name=name, text=text, glyph=glyph):
                item = character(text, glyph)
                self.assertEqual(guard.normalize_page_markers(page([item], name)), 0)
                self.assertEqual(item.char_unicode, text)

    def test_xobject_font_override_prevents_false_font_match(self):
        inherited = character(xobj_id=7)
        override = character(xobj_id=8)
        fixture = page([inherited, override])
        fixture.pdf_xobject = [PdfXobject(xobj_id=7), PdfXobject(
            xobj_id=8, pdf_font=[PdfFont(font_id="F1", name="Helvetica")])]
        self.assertEqual(guard.normalize_page_markers(fixture), 1)
        self.assertEqual(inherited.char_unicode, "•")
        self.assertEqual(override.char_unicode, "\x1f")

    def test_real_spacing_and_list_split_preserve_two_long_items(self):
        # The bad source ToUnicode maps both the empty glyph 3 and square 31
        # to U+001F. Only the square must survive whitespace cleanup.
        first = [character(glyph=3, x=8), character(x=0),
                 character("A", glyph=65, font_id="F2", x=16),
                 character("B", glyph=66, font_id="F2", x=600)]
        second = [character(glyph=3, x=8, y=-20), character(x=0, y=-20),
                  character("C", glyph=67, font_id="F2", x=16, y=-20),
                  character("D", glyph=68, font_id="F2", x=600, y=-20)]
        fixture = page(first + second)
        guard.normalize_page_markers(fixture)
        paragraph = PdfParagraph(pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=first)),
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=second))],
            layout_id=1, layout_label="text")
        finder = object.__new__(ParagraphFinder)
        finder.translation_config = SimpleNamespace(split_short_lines=False)
        finder.process_paragraph_spacing(paragraph)
        lines = [comp.pdf_line.pdf_character for comp in paragraph.pdf_paragraph_composition]
        self.assertEqual([chars[0].char_unicode for chars in lines], ["•", "•"])
        paragraphs = [paragraph]
        finder.process_independent_paragraphs(paragraphs, median_width=650)
        self.assertEqual(len(paragraphs), 2)
        self.assertIs(paragraphs[0].pdf_paragraph_composition[0].pdf_line.pdf_character[0], first[1])
        self.assertIs(paragraphs[1].pdf_paragraph_composition[0].pdf_line.pdf_character[0], second[1])
        self.assertEqual(sum(len(p.pdf_paragraph_composition[0].pdf_line.pdf_character)
                             for p in paragraphs), 6)

    def test_process_page_wrapper_normalizes_before_upstream(self):
        item = character()
        fixture = page([item])
        seen = []
        def upstream(finder, argument):
            seen.append(argument.pdf_character[0].char_unicode)
            return "done"
        with patch.object(guard, "_original_process_page", upstream):
            self.assertEqual(guard._guard_process_page(object(), fixture), "done")
        self.assertEqual(seen, ["•"])

    def test_install_is_idempotent(self):
        from babeldoc.format.pdf.document_il.utils import formular_helper
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        with patch.object(guard, "_installed", False), \
             patch.object(guard, "_original_process_page", None), \
             patch.object(guard, "_original_is_formulas_font", None), \
             patch.object(guard, "_original_process_translatable_formulas", None), \
             patch.object(guard, "_original_is_translatable_formula", None), \
             patch.object(guard, "_original_process_comma_formulas", None), \
             patch.object(guard, "_original_should_split_formula", None), \
             patch.object(StylesAndFormulas, "process_translatable_formulas", lambda self, page: None), \
             patch.object(StylesAndFormulas, "is_translatable_formula", lambda self, formula: False), \
             patch.object(StylesAndFormulas, "process_comma_formulas", lambda self, page: None), \
             patch.object(StylesAndFormulas, "should_split_formula", lambda self, formula: False), \
             patch.object(formular_helper, "is_formulas_font", lambda name, pattern: False), \
             patch.object(ParagraphFinder, "process_page", lambda self, page: None):
            original = ParagraphFinder.process_page
            original_font = formular_helper.is_formulas_font
            guard.install()
            guard.install()
            self.assertIs(guard._original_process_page, original)
            self.assertIs(guard._original_is_formulas_font, original_font)
            self.assertIs(formular_helper.is_formulas_font, guard._guard_is_formulas_font)
            self.assertIs(ParagraphFinder.process_page, guard._guard_process_page)
            self.assertTrue(guard.snapshot()["guardInstalled"])

    def test_toc_tail_moves_original_glyph_sequence_and_coordinates_unchanged(self):
        first, first_chars = toc_row()
        second, second_chars = toc_row("Next section", "58", y=470)
        fixture = page([])
        fixture.pdf_paragraph = [first, second]
        tails = first_chars[len("Introduction"):] + second_chars[len("Next section"):]
        originals = [(c, c.pdf_character_id, c.pdf_style, c.box,
                      (c.box.x, c.box.y, c.box.x2, c.box.y2), c.render_order) for c in tails]
        finder = object.__new__(ParagraphFinder)
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 2)
        self.assertEqual([p.unicode for p in fixture.pdf_paragraph], ["Introduction", "Next section"])
        self.assertEqual("".join(c.char_unicode for c in fixture.pdf_character), "." * 40 + " 42" + "." * 40 + " 58")
        for current, (original, glyph, style, box, coordinates, order) in zip(fixture.pdf_character, originals):
            self.assertIs(current, original)
            self.assertEqual(current.pdf_character_id, glyph)
            self.assertIs(current.pdf_style, style)
            self.assertIs(current.box, box)
            self.assertEqual((current.box.x, current.box.y, current.box.x2, current.box.y2), coordinates)
            self.assertEqual(current.render_order, order)
        remaining = [c for p in fixture.pdf_paragraph for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character]
        self.assertCountEqual(map(id, remaining + fixture.pdf_character), map(id, first_chars + second_chars))
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)
        self.assertEqual(guard.snapshot()["preservedTOCRows"], 2)

    def test_toc_body_cannot_expand_right_over_preserved_leaders(self):
        first, original_chars = toc_row()
        second, _ = toc_row("Next section", "58", y=470)
        fixture = page([])
        fixture.cropbox = Cropbox(Box(0, 0, 600, 800))
        fixture.pdf_paragraph = [first, second]
        finder = object.__new__(ParagraphFinder)
        guard.preserve_toc_leaders(finder, fixture)
        leader_x = original_chars[len("Introduction")].box.x
        typesetter = object.__new__(Typesetting)
        typesetter.is_cjk = True
        self.assertLessEqual(typesetter.get_max_right_space(first.box, fixture), leader_x)
        seen_right_edges = []
        # Force the real upstream shrink/expand loop through every recovery path.
        def does_not_fit(units, box, scale, line_skip, paragraph, english_break):
            seen_right_edges.append(box.x2)
            return [], False
        typesetter._layout_typesetting_units = does_not_fit
        typesetter._find_optimal_scale_and_layout(first, fixture, [], use_english_line_break=False)
        self.assertGreater(len(seen_right_edges), 10)
        self.assertLessEqual(max(seen_right_edges), leader_x)

    def test_toc_preserves_overlapping_spaces_and_complete_fi_ligature(self):
        first_label = "About This Basic Brochure "
        second_label = "Verification of Intrinsic Safety "
        first, first_chars = toc_row(first_label, "6")
        second, second_chars = toc_row(second_label, "19", y=470)
        # Real PDF shape: the otherwise empty space box extends 2.7pt into
        # the first leader dot. That space must move intact with the tail.
        for label, chars in ((first_label, first_chars), (second_label, second_chars)):
            space = chars[len(label) - 1]
            space.box.x2 += 2.7
            space.visual_bbox.box.x2 += 2.7
        # Real PDF shape: "fi" is one CID 368, not two character objects.
        ligature = second_chars[4]
        ligature.char_unicode = "fi"
        ligature.pdf_character_id = 368
        ligature.box.x2 = second_chars[5].box.x2
        ligature.visual_bbox.box.x2 = second_chars[5].visual_bbox.box.x2
        del second_chars[5]
        first_tail = first_chars[len(first_label) - 1:]
        second_tail = second_chars[len(second_label) - 2:]
        originals = first_chars + second_chars
        coordinates = [(c.box.x, c.box.y, c.box.x2, c.box.y2) for c in originals]
        text_before = ["".join(c.char_unicode for c in chars)
                       for chars in (first_chars, second_chars)]
        fixture = page([])
        fixture.pdf_paragraph = [first, second]
        finder = object.__new__(ParagraphFinder)
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 2)
        self.assertEqual([p.unicode for p in fixture.pdf_paragraph],
                         [first_label.rstrip(), second_label.rstrip()])
        self.assertEqual(fixture.pdf_character, first_tail + second_tail)
        self.assertEqual([c.pdf_character_id for c in fixture.pdf_character],
                         [c.pdf_character_id for c in first_tail + second_tail])
        first_body = first.pdf_paragraph_composition[0].pdf_line.pdf_character
        second_body = second.pdf_paragraph_composition[0].pdf_line.pdf_character
        self.assertIs(second_body[4], ligature)
        self.assertEqual(ligature.pdf_character_id, 368)
        self.assertEqual(["".join(c.char_unicode for c in chars)
                          for chars in (first_body + first_tail, second_body + second_tail)], text_before)
        self.assertCountEqual(map(id, first_body + second_body + fixture.pdf_character), map(id, originals))
        self.assertEqual([(c.box.x, c.box.y, c.box.x2, c.box.y2) for c in originals], coordinates)
        self.assertTrue("".join(c.char_unicode for c in first_tail).endswith(" 6"))
        self.assertTrue("".join(c.char_unicode for c in second_tail).endswith(" 19"))

    def test_toc_rejects_split_inside_glyph_and_visible_text_overlap(self):
        finder = object.__new__(ParagraphFinder)
        for malformed in ("glyph_boundary", "visible_overlap"):
            with self.subTest(malformed=malformed):
                first, chars = toc_row("Guide", "6")
                second, _ = toc_row("Introduction", "19", y=470)
                if malformed == "glyph_boundary":
                    chars[4].char_unicode = "e."
                    chars[4].box.x2 = chars[5].box.x2
                    chars[4].visual_bbox.box.x2 = chars[5].visual_bbox.box.x2
                    del chars[5]
                else:
                    chars[4].visual_bbox.box.x2 = chars[5].box.x + 2.7
                original_ids = [id(c) for c in chars]
                fixture = page([])
                fixture.pdf_paragraph = [first, second]
                self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)
                self.assertEqual([id(c) for c in first.pdf_paragraph_composition[0].pdf_line.pdf_character], original_ids)
                self.assertEqual(fixture.pdf_character, [])

    def test_non_toc_ellipses_and_ambiguous_rows_are_untouched(self):
        finder = object.__new__(ParagraphFinder)
        lone, chars = toc_row("He waited", "10")
        fixture = page([])
        fixture.pdf_paragraph = [lone]
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)
        self.assertEqual(lone.pdf_paragraph_composition[0].pdf_line.pdf_character, chars)
        for dots in (3, 11):
            first, _ = toc_row("Ordinary prose", "10", dots=dots)
            second, _ = toc_row("More prose", "20", y=470, dots=dots)
            fixture.pdf_paragraph = [first, second]
            self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)
        first, _ = toc_row()
        second, second_chars = toc_row(y=470)
        second.pdf_paragraph_composition.append(PdfParagraphComposition(
            pdf_line=PdfLine(pdf_character=[character("X", x=0, y=450)])))
        fixture.pdf_paragraph = [first, second]
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)
        second.pdf_paragraph_composition.pop()
        second_chars[-1].box.y -= 20  # One PdfLine holding two physical lines.
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)

    def test_toc_requires_repeated_right_edge_and_textual_label(self):
        finder = object.__new__(ParagraphFinder)
        first, _ = toc_row()
        second, _ = toc_row("Section", "9", y=470, right=400)
        fixture = page([])
        fixture.pdf_paragraph = [first, second]
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)
        first, _ = toc_row("123", "1")
        second, _ = toc_row("456", "2", y=470)
        fixture.pdf_paragraph = [first, second]
        self.assertEqual(guard.preserve_toc_leaders(finder, fixture), 0)

    def index_fixture(self):
        fixture = page([])
        for index, letter in enumerate("ABC"):
            heading = PdfParagraph(layout_label="title", vertical=False,
                pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(
                    pdf_character=[character(letter, ord(letter), y=700-index*100)]))])
            fixture.pdf_paragraph.append(heading)
            for row in range(3):
                item, _ = toc_row(letter + " entry", "30, 40" if row == 0 else "53",
                                  y=680-index*100-row*20)
                fixture.pdf_paragraph.append(item)
        return fixture

    def test_index_moves_every_original_glyph_without_reflow_or_duplication(self):
        fixture = self.index_fixture()
        chars = [c for p in fixture.pdf_paragraph for comp in p.pdf_paragraph_composition
                 for c in comp.pdf_line.pdf_character]
        original = [(c.pdf_character_id, c.pdf_style, c.box, c.render_order) for c in chars]
        self.assertTrue(guard.preserve_alphabetical_index(fixture))
        self.assertEqual(fixture.pdf_paragraph, [])
        self.assertEqual([id(c) for c in fixture.pdf_character], [id(c) for c in chars])
        self.assertEqual([(c.pdf_character_id, c.pdf_style, c.box, c.render_order)
                          for c in fixture.pdf_character], original)
        self.assertIn("30, 40", "".join(c.char_unicode for c in chars))
        self.assertIn("53", "".join(c.char_unicode for c in chars))
        self.assertEqual(guard.snapshot()["preservedIndexPages"], 1)
        self.assertEqual(guard.snapshot()["preservedIndexReferenceLines"], 9)
        self.assertFalse(guard.preserve_alphabetical_index(fixture))

    def test_index_does_not_capture_normal_toc_or_mixed_prose(self):
        for mode in ("no_letter_headings", "too_few_entries", "prose"):
            with self.subTest(mode=mode):
                fixture = self.index_fixture()
                if mode == "no_letter_headings":
                    fixture.pdf_paragraph = [p for p in fixture.pdf_paragraph if p.layout_label != "title"]
                elif mode == "too_few_entries":
                    fixture.pdf_paragraph.pop()
                    fixture.pdf_paragraph.pop()
                else:
                    prose = "This is a normal explanatory paragraph about the behaviour of the system. " * 4
                    fixture.pdf_paragraph.append(PdfParagraph(layout_label="plain text", vertical=False,
                        pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(
                            pdf_character=[character(c, ord(c)) for c in prose]))]))
                before = list(fixture.pdf_paragraph)
                self.assertFalse(guard.preserve_alphabetical_index(fixture))
                self.assertEqual(fixture.pdf_paragraph, before)
                self.assertEqual(fixture.pdf_character, [])

    def test_index_rejects_unfamiliar_composition_and_duplicate_character_objects(self):
        for mode in ("composition", "duplicate"):
            with self.subTest(mode=mode):
                fixture = self.index_fixture()
                if mode == "composition":
                    fixture.pdf_paragraph[0].pdf_paragraph_composition.append(PdfParagraphComposition())
                else:
                    fixture.pdf_character.append(fixture.pdf_paragraph[0].pdf_paragraph_composition[0].pdf_line.pdf_character[0])
                before = list(fixture.pdf_paragraph)
                self.assertFalse(guard.preserve_alphabetical_index(fixture))
                self.assertEqual(fixture.pdf_paragraph, before)

    def caption_fixture(self):
        body=line_paragraph(["Figure 16. Current modulation (Manchester II coding"],"figure_caption",size=8)
        closing=line_paragraph([")"],"figure_caption",size=8)
        anchor=body.pdf_paragraph_composition[0].pdf_line.pdf_character[-1]
        char=closing.pdf_paragraph_composition[0].pdf_line.pdf_character[0]
        char.box.x=anchor.box.x2-0.1
        char.box.x2=char.box.x+2.664
        char.visual_bbox.box.x=char.box.x+0.27
        char.visual_bbox.box.x2=char.box.x2-0.54
        fixture=page([])
        fixture.pdf_paragraph=[body,closing]
        return fixture,body,closing,char

    def test_caption_closer_joins_original_glyph_to_unique_physical_line(self):
        fixture,body,closing,char=self.caption_fixture()
        before=[c for p in fixture.pdf_paragraph for comp in p.pdf_paragraph_composition
                for c in comp.pdf_line.pdf_character]
        values=[(c.pdf_character_id,c.box.x,c.box.y,c.box.x2,c.box.y2,c.pdf_style,c.render_order) for c in before]
        finder=object.__new__(ParagraphFinder)
        self.assertEqual(guard.merge_caption_closers(finder,fixture),1)
        after=body.pdf_paragraph_composition[0].pdf_line.pdf_character
        self.assertEqual([id(c) for c in after],[id(c) for c in before])
        self.assertEqual([(c.pdf_character_id,c.box.x,c.box.y,c.box.x2,c.box.y2,c.pdf_style,c.render_order) for c in after],values)
        self.assertEqual(body.unicode,"Figure 16. Current modulation (Manchester II coding)")
        self.assertEqual(fixture.pdf_paragraph,[body])
        self.assertEqual(guard.merge_caption_closers(finder,fixture),0)

    def test_caption_closer_rejects_ambiguous_or_unrelated_fragments(self):
        for mode in ('far','baseline','font','size','label','balanced','ambiguous','xobject'):
            with self.subTest(mode=mode):
                fixture,body,closing,char=self.caption_fixture()
                if mode=='far':char.box.x+=20
                elif mode=='baseline':char.box.y-=14
                elif mode=='font':char.pdf_style.font_id='F3'
                elif mode=='size':char.pdf_style.font_size=9
                elif mode=='label':closing.layout_label='plain text'
                elif mode=='balanced':body.pdf_paragraph_composition[0].pdf_line.pdf_character[-2].char_unicode=')'
                elif mode=='ambiguous':
                    import copy
                    fixture.pdf_paragraph.insert(0,copy.deepcopy(body))
                elif mode=='xobject':closing.xobj_id=8
                before=list(fixture.pdf_paragraph)
                self.assertEqual(guard.merge_caption_closers(object.__new__(ParagraphFinder),fixture),0)
                self.assertEqual(fixture.pdf_paragraph,before)

    def test_two_trailing_inequalities_preserve_every_glyph_and_block_prose_expansion(self):
        paragraph=line_paragraph(["The terminator must comply with these limits:",
                                  "90 Ω < R < 100 Ω","0 μF < c < 2.2 μF"])
        fixture=page([])
        fixture.cropbox=Cropbox(Box(0,0,600,800))
        fixture.pdf_paragraph=[paragraph]
        original=[c for comp in paragraph.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
        tail=[c for comp in paragraph.pdf_paragraph_composition[1:] for c in comp.pdf_line.pdf_character]
        values=[(c.pdf_character_id,c.box.x,c.box.y,c.box.x2,c.box.y2,c.pdf_style,c.render_order) for c in tail]
        self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),2)
        self.assertEqual([id(c) for c in fixture.pdf_character],[id(c) for c in tail])
        self.assertEqual([(c.pdf_character_id,c.box.x,c.box.y,c.box.x2,c.box.y2,c.pdf_style,c.render_order) for c in tail],values)
        head=paragraph.pdf_paragraph_composition[0].pdf_line.pdf_character
        self.assertCountEqual([id(c) for c in head+fixture.pdf_character],[id(c) for c in original])
        self.assertEqual(paragraph.unicode,"The terminator must comply with these limits:")
        barrier=max(c.box.y2 for c in tail)
        self.assertGreater(paragraph.box.y,barrier)
        self.assertEqual(object.__new__(Typesetting).get_max_bottom_space(paragraph.box,fixture),barrier)
        self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),0)

    def calculation_fixture(self):
        paragraph=line_paragraph(['This method results in the following values:',
            'Uo= 30 V','Io= 30 V / 2 kΩ = 15 mA','Po= 0.25 × Uo× Io= 112.5 mW'])
        tail_comps=paragraph.pdf_paragraph_composition[1:]
        for comp in tail_comps:
            for char in comp.pdf_line.pdf_character:
                if char.char_unicode=='o':
                    char.pdf_style.font_size=6.38
                    char.box.y-=3.62
                    char.box.y2=char.box.y+6.38
                    char.visual_bbox.box.y=char.box.y
                    char.visual_bbox.box.y2=char.box.y2
        # The real source parser places all three physical rows in one PdfLine.
        tail=[c for comp in tail_comps for c in comp.pdf_line.pdf_character]
        paragraph.pdf_paragraph_composition=paragraph.pdf_paragraph_composition[:1]+[
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=tail))]
        fixture=page([]);fixture.cropbox=Cropbox(Box(0,0,600,800));fixture.pdf_paragraph=[paragraph]
        return fixture,paragraph,tail

    def test_merged_calculation_rows_preserve_original_subscript_geometry(self):
        fixture,paragraph,tail=self.calculation_fixture()
        before=[(id(c),c.char_unicode,c.pdf_character_id,c.pdf_style,c.box.x,c.box.y,c.box.x2,c.box.y2)
                for c in tail]
        self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),3)
        after=[(id(c),c.char_unicode,c.pdf_character_id,c.pdf_style,c.box.x,c.box.y,c.box.x2,c.box.y2)
               for c in fixture.pdf_character]
        self.assertEqual(after,before)
        self.assertEqual(paragraph.unicode,'This method results in the following values:')
        self.assertEqual(len([c for c in fixture.pdf_character if c.char_unicode=='o']),5)
        self.assertGreater(paragraph.box.y,max(c.box.y2 for c in tail))
        self.assertEqual(object.__new__(Typesetting).get_max_bottom_space(paragraph.box,fixture),
                         max(c.box.y2 for c in tail))
        self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),0)

    def test_calculation_rows_reject_false_baselines_or_unbound_small_letters(self):
        for mode in ('wrong_font','script_far','script_baseline','script_next_row','body_on_row',
                     'wrong_left_edge','normal_glyph_next_row'):
            with self.subTest(mode=mode):
                fixture,paragraph,tail=self.calculation_fixture()
                small=next(c for c in tail if c.char_unicode=='o')
                if mode=='wrong_font':small.pdf_style.font_id='F3'
                elif mode=='script_far':small.box.x+=8
                elif mode=='script_baseline':small.box.y+=3.62
                elif mode=='script_next_row':small.box.y-=14
                elif mode=='body_on_row':tail[-1].char_unicode='the result'
                elif mode=='wrong_left_edge':
                    char=next(c for c in tail if c.char_unicode=='I');char.box.x+=4
                elif mode=='normal_glyph_next_row':
                    char=next(c for c in tail if c.char_unicode=='3');char.box.y-=14
                before=list(paragraph.pdf_paragraph_composition)
                self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),0)
                self.assertEqual(paragraph.pdf_paragraph_composition,before)
                self.assertEqual(fixture.pdf_character,[])

    def test_calculation_protection_requires_two_complete_separate_rows(self):
        for texts in (['The calculated limits follow:','U = 30 V'],
                      ['The calculated limits follow:','U = 30 V I = 15 mA'],
                      ['The calculated limits follow:','U = 30 V','If I = 15 mA'],
                      ['The calculated limits follow:','U = 30 V','I = 15 mA','Use these values.'],
                      ['The calculated limits follow:','U = 30','I = 15']):
            with self.subTest(texts=texts):
                paragraph=line_paragraph(texts);fixture=page([]);fixture.pdf_paragraph=[paragraph]
                self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),0)
                self.assertEqual(fixture.pdf_character,[])

    def test_trailing_equations_do_not_capture_prose_inline_or_ambiguous_rows(self):
        cases=[['The limit follows:','90 Ω < R < 100 Ω'],
               ['The limit follows:','90 Ω < R < 100 Ω 0 μF < c < 2.2 μF'],
               ['The limit follows:','90 Ω < R < 100 Ω','If c < 2.2 μF > 0 μF'],
               ['The limit follows:','90 Ω < R < 100 Ω','0 μF < c < 2.2 μF','A further explanation.']]
        for texts in cases:
            paragraph=line_paragraph(texts)
            fixture=page([]);fixture.pdf_paragraph=[paragraph]
            before=list(paragraph.pdf_paragraph_composition)
            self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),0)
            self.assertEqual(paragraph.pdf_paragraph_composition,before)
            self.assertEqual(fixture.pdf_character,[])
        for mode in ('misaligned','overlap','duplicate','label'):
            paragraph=line_paragraph(['The limit follows:','90 Ω < R < 100 Ω','0 μF < c < 2.2 μF'])
            fixture=page([]);fixture.pdf_paragraph=[paragraph]
            last=paragraph.pdf_paragraph_composition[-1].pdf_line.pdf_character
            if mode=='misaligned':
                for c in last:c.box.x+=10;c.box.x2+=10
            elif mode=='overlap':
                for c in last:c.box.y+=14;c.box.y2+=14
            elif mode=='duplicate':fixture.pdf_character.append(last[0])
            elif mode=='label':paragraph.layout_label='figure_caption'
            self.assertEqual(guard.preserve_trailing_equations(object.__new__(ParagraphFinder),fixture),0)

    def test_legacy_math_font_allowlist_is_exact_and_keeps_upstream_behavior(self):
        with patch.object(guard, '_original_is_formulas_font', return_value=False) as original:
            for name in guard._LEGACY_MATH_FONTS:
                self.assertTrue(guard._guard_is_formulas_font(name, None))
                self.assertTrue(guard._guard_is_formulas_font('ABCDEF+'+name, None))
            for name in ('AdvP49811', 'AdvP403A40', 'AdvP40319B', 'AdvP41461E',
                         'AdvP49812', 'AdvP4041BD', 'AdvP40798-Regular',
                         'advp40798', 'ABCDE+AdvP40798', 'Helvetica'):
                self.assertFalse(guard._guard_is_formulas_font(name, None))
            self.assertTrue(original.called)
        with patch.object(guard, '_original_is_formulas_font', return_value=True) as original:
            self.assertTrue(guard._guard_is_formulas_font('CustomFont', 'Custom.*'))
            original.assert_called_once_with('CustomFont', 'Custom.*')

    def test_legacy_math_font_keeps_prefix_and_following_body_digit_in_formula(self):
        from dataclasses import asdict
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        from babeldoc.format.pdf.document_il.utils import formular_helper
        item=object.__new__(StylesAndFormulas)
        item.font_mapper=SimpleNamespace(has_char=lambda text: True)
        item.translation_config=SimpleNamespace(formular_char_pattern=None)
        for prefix in ('{', '2'):
            chars=[character(prefix,ord(prefix),font_id='M',x=0),
                   character('1',49,font_id='B',x=8),
                   character(' ',32,font_id='B',x=16),
                   character('p',112,font_id='B',x=24)]
            before=[asdict(c) for c in chars]
            fixture=Page(pdf_font=[PdfFont(font_id='M',name='ABCDEF+AdvP7DA6'),
                                   PdfFont(font_id='B',name='AdvP49811')])
            with patch.object(guard, '_original_is_formulas_font', return_value=False), \
                 patch.object(formular_helper,'is_formulas_font',guard._guard_is_formulas_font):
                fonts,_=formular_helper.collect_page_formula_font_ids(fixture,None)
                tagged,_=item._classify_characters_in_composition(
                    PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars)),fonts,False,0)
            self.assertEqual([v[1] for v in tagged],[True,True,True,False])
            self.assertTrue(all(a is b[0] for a,b in zip(chars,tagged)))
            self.assertEqual([asdict(c) for c in chars],before)

    def test_numeric_looking_legacy_symbol_is_not_demoted_to_plain_text(self):
        from dataclasses import asdict
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        original_process=StylesAndFormulas.process_translatable_formulas
        original_predicate=StylesAndFormulas.is_translatable_formula
        for name,override,protected in [('AdvP7DA6',None,True),
                                        ('AdvP497E2',None,False),
                                        ('AdvP7DA6','AdvP49811',False)]:
            with self.subTest(name=name,override=override):
                chars=[character('2',50,font_id='M'),character('1',49,font_id='B',x=8)]
                formula=PdfFormula(pdf_character=chars,y_offset=0,is_corner_mark=False)
                paragraph=PdfParagraph(xobj_id=7,pdf_paragraph_composition=[
                    PdfParagraphComposition(pdf_formula=formula)])
                fixture=Page(pdf_font=[PdfFont(font_id='M',name=name),PdfFont(font_id='B',name='AdvP49811')],
                             pdf_paragraph=[paragraph])
                if override:fixture.pdf_xobject=[PdfXobject(xobj_id=7,pdf_font=[PdfFont(font_id='M',name=override)])]
                before=[asdict(c) for c in chars]
                with patch.object(guard,'_original_process_translatable_formulas',original_process), \
                     patch.object(guard,'_original_is_translatable_formula',original_predicate), \
                     patch.object(StylesAndFormulas,'is_translatable_formula',guard._guard_is_translatable_formula):
                    guard._guard_process_translatable_formulas(object.__new__(StylesAndFormulas),fixture)
                comp=paragraph.pdf_paragraph_composition[0]
                self.assertIs(comp.pdf_formula,formula if protected else None)
                self.assertEqual([asdict(c) for c in chars],before)
                self.assertFalse(hasattr(guard._formula_context,'protected'))

    def test_formula_context_is_restored_when_upstream_raises(self):
        fixture=Page()
        guard._formula_context.protected={123}
        try:
            with patch.object(guard,'_original_process_translatable_formulas',side_effect=ValueError('stop')):
                with self.assertRaises(ValueError):guard._guard_process_translatable_formulas(object(),fixture)
            self.assertEqual(guard._formula_context.protected,{123})
        finally:
            del guard._formula_context.protected

    def test_encoded_angle_bracket_is_not_split_as_a_normal_comma(self):
        from dataclasses import asdict
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        original_process=StylesAndFormulas.process_comma_formulas
        original_predicate=StylesAndFormulas.should_split_formula
        for name,protected in [('AdvP7DA6',True),('AdvP49811',False)]:
            chars=[character('x',120,font_id='B'),character(',',44,font_id='M',x=8),
                   character('y',121,font_id='B',x=16)]
            formula=PdfFormula(pdf_character=chars,line_id=0)
            paragraph=PdfParagraph(xobj_id=-1,pdf_paragraph_composition=[
                PdfParagraphComposition(pdf_formula=formula)])
            fixture=Page(pdf_font=[PdfFont(font_id='M',name=name),PdfFont(font_id='B',name='AdvP49811')],
                         pdf_paragraph=[paragraph])
            before=[asdict(c) for c in chars]
            with patch.object(guard,'_original_process_comma_formulas',original_process), \
                 patch.object(guard,'_original_should_split_formula',original_predicate), \
                 patch.object(StylesAndFormulas,'should_split_formula',guard._guard_should_split_formula):
                guard._guard_process_comma_formulas(object.__new__(StylesAndFormulas),fixture)
            self.assertEqual(len(paragraph.pdf_paragraph_composition),1 if protected else 3)
            if protected:self.assertIs(paragraph.pdf_paragraph_composition[0].pdf_formula,formula)
            self.assertEqual([asdict(c) for c in chars],before)
            self.assertFalse(hasattr(guard._formula_context,'encoded_commas'))

    def copyright_fixture(self):
        texts=[('Copyright: © 2014 Authors, B',500),
               ('ornholdt. This open-access article uses the Creative Commons License, which permit',500),
               ('s',500),('unrestricted use, distribution, an',490.7),
               ('d reproduction if the source is credited.',490.7)]
        paragraphs=[]; previous_y=None; x=10
        for index,(text,y) in enumerate(texts):
            if y!=previous_y:x=10
            p=line_paragraph([text],label='fallback_line' if index in (0,2,3) else 'plain text',y=y,size=7)
            for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character:
                c.box.x+=x-10;c.box.x2+=x-10
                c.visual_bbox.box.x+=x-10;c.visual_bbox.box.x2+=x-10
            x=p.pdf_paragraph_composition[0].pdf_line.pdf_character[-1].box.x2
            previous_y=y;paragraphs.append(p)
        body=line_paragraph(['This following introduction must be translated.'],y=460)
        fixture=page([]);fixture.pdf_paragraph=paragraphs+[body]
        return fixture,paragraphs,body

    def test_copyright_preserves_split_word_fragments_and_all_original_glyphs(self):
        from dataclasses import asdict
        fixture,paragraphs,body=self.copyright_fixture()
        chars=[c for p in paragraphs for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
        before=[asdict(c) for c in chars]
        self.assertEqual(guard.preserve_copyright_blocks(fixture),1)
        self.assertEqual(fixture.pdf_paragraph,[body])
        self.assertEqual([id(c) for c in fixture.pdf_character],[id(c) for c in chars])
        self.assertEqual([asdict(c) for c in fixture.pdf_character],before)
        self.assertIn('Bornholdt',''.join(c.char_unicode for c in fixture.pdf_character))
        self.assertIn('and reproduction',''.join(c.char_unicode for c in fixture.pdf_character))
        self.assertEqual(guard.preserve_copyright_blocks(fixture),0)

    def test_copyright_rejects_unanchored_disconnected_ambiguous_or_partial_blocks(self):
        for mode in ('anchor','gap','font','xobject','ambiguous','duplicate','partial','license','vertical','nan'):
            with self.subTest(mode=mode):
                fixture,paragraphs,body=self.copyright_fixture()
                first=paragraphs[0].pdf_paragraph_composition[0].pdf_line.pdf_character
                second=paragraphs[1].pdf_paragraph_composition[0].pdf_line.pdf_character
                if mode=='anchor':first[0].char_unicode='X'
                elif mode=='gap':
                    for c in second:c.box.x+=20;c.box.x2+=20
                elif mode=='font':
                    for c in second:c.pdf_style.font_id='OTHER'
                elif mode=='xobject':paragraphs[1].xobj_id=7
                elif mode=='ambiguous':
                    import copy
                    fixture.pdf_paragraph.append(copy.deepcopy(paragraphs[1]))
                elif mode=='duplicate':fixture.pdf_character.append(first[0])
                elif mode=='partial':
                    paragraphs[1].pdf_paragraph_composition.extend(body.pdf_paragraph_composition)
                elif mode=='license':
                    for c in second:c.char_unicode='x'
                elif mode=='vertical':paragraphs[1].vertical=True
                elif mode=='nan':second[0].box.x=float('nan')
                before=list(fixture.pdf_paragraph)
                self.assertEqual(guard.preserve_copyright_blocks(fixture),0)
                self.assertEqual(fixture.pdf_paragraph,before)

class SubscriptOffsetTests(unittest.TestCase):
    @staticmethod
    def fixture(space=True):
        base=character('C',67,font_id='F2',x=100,y=739.3701)
        base.pdf_style.font_size=11
        base.box.x2=107; base.box.y2=750.3701
        base.visual_bbox.box=Box(100,739,107,747)
        script=character('o',111,font_id='F1',x=107.15,y=735.7401)
        script.pdf_style.font_size=6.38
        script.box.x2=110.72;script.box.y2=742.1201
        script.visual_bbox.box=Box(107.15,735.643527734375,110.72,739.17308828125)
        letters=[script]
        if space:
            blank=character(' ',32,font_id='F3',x=110.8,y=739.3701)
            blank.pdf_style.font_size=11
            blank.visual_bbox.box=Box(110.8,732.9461,114,743.9461)
            letters.append(blank)
        formula=PdfFormula(pdf_character=letters,x_offset=0,y_offset=0,x_advance=0,is_corner_mark=True,
            box=Box(107.15,732.9461 if space else 735.643527734375,114,743.9461))
        paragraph=PdfParagraph(vertical=False,xobj_id=-1,pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[base])),
            PdfParagraphComposition(pdf_formula=formula)])
        fixture=Page(pdf_paragraph=[paragraph],pdf_font=[
            PdfFont(font_id='F1',name='ABCDEF+HelveticaWorld-Regular'),
            PdfFont(font_id='F2',name='UVWXYZ+HelveticaWorld-Regular'),
            PdfFont(font_id='F3',name='Different-Body-Space')])
        return fixture,paragraph,base,script,formula

    def test_exact_source_baseline_scaled_and_original_objects_unchanged(self):
        from dataclasses import asdict
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        for space in (False,True):
            fixture,p,base,script,f=self.fixture(space)
            before=[(c,asdict(c)) for c in f.pdf_character]
            self.assertEqual(guard.repair_subscript_offsets(fixture),1)
            self.assertAlmostEqual(f.y_offset,f.box.y-base.box.y)
            self.assertIs(p.pdf_paragraph_composition[-1].pdf_formula,f)
            self.assertEqual(guard.repair_subscript_offsets(fixture),0)
            unit=TypesettingUnit(formular=f)
            for scale in (.5,1,1.5):
                for repeat in range(2):
                    relocated=unit.relocate(200,300,scale).formular
                    self.assertAlmostEqual(relocated.pdf_character[0].box.y-300,
                                           (script.box.y-base.box.y)*scale)
                    self.assertLess(relocated.pdf_character[0].box.y,300)
            for (original,state),current in zip(before,f.pdf_character):
                self.assertIs(current,original);self.assertEqual(asdict(current),state)

    def test_correct_existing_offset_and_compound_formulas_unchanged(self):
        from dataclasses import asdict
        for mode in ('correct','compound','whole'):
            fixture,p,base,script,f=self.fixture()
            if mode=='correct':f.y_offset=f.box.y-base.box.y
            elif mode=='compound':f.pdf_character.append(character('=',61,font_id='F2',x=115,y=739.3701))
            else:
                f.pdf_character.insert(0,base)
                p.pdf_paragraph_composition=p.pdf_paragraph_composition[1:]
            before=asdict(f)
            self.assertEqual(guard.repair_subscript_offsets(fixture),0)
            self.assertEqual(asdict(f),before)

    def test_superscript_keeps_its_positive_source_direction(self):
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        fixture,p,base,script,f=self.fixture(False)
        script.box.y=743;script.box.y2=749.38
        script.visual_bbox.box=Box(script.box.x,742.9,script.box.x2,746.5)
        f.box=Box(script.box.x,742.9,script.box.x2,746.5)
        f.y_offset=f.box.y-base.box.y
        offset=f.y_offset
        self.assertEqual(guard.repair_subscript_offsets(fixture),0)
        self.assertEqual(f.y_offset,offset)
        self.assertGreater(TypesettingUnit(formular=f).relocate(0,0,1).formular.pdf_character[0].box.y,0)

    def test_ambiguous_or_unproven_geometry_is_untouched(self):
        import copy
        for mode in ('font','unknown','xobject','paragraph-xobject','word','ambiguous','far',
                     'vertical','nan','size','ordinary-baseline','corner-false','curve','multi-letter'):
            fixture,p,base,script,f=self.fixture()
            if mode=='font':fixture.pdf_font[0].name='Different-Regular'
            elif mode=='unknown':fixture.pdf_font=[]
            elif mode=='xobject':script.xobj_id=4
            elif mode=='paragraph-xobject':p.xobj_id=4
            elif mode=='word':p.pdf_paragraph_composition[0].pdf_line.pdf_character.insert(0,character('a',97,x=93,y=base.box.y))
            elif mode=='ambiguous':
                other=copy.deepcopy(base)
                p.pdf_paragraph_composition.insert(0,PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[other,character(' ',32,x=90)])))
            elif mode=='far':script.box.x+=100
            elif mode=='vertical':script.vertical=True
            elif mode=='nan':script.box.y=float('nan')
            elif mode=='size':script.pdf_style.font_size=11
            elif mode=='ordinary-baseline':script.box.y=base.box.y
            elif mode=='corner-false':f.is_corner_mark=False
            elif mode=='curve':f.pdf_curve=[object()]
            elif mode=='multi-letter':script.char_unicode='max'
            self.assertEqual(guard.repair_subscript_offsets(fixture),0,mode)
            self.assertEqual(f.y_offset,0,mode)

    def test_bare_native_formula_without_corner_flag_and_wrapper_order(self):
        fixture,p,base,script,f=self.fixture(False);f.is_corner_mark=None
        seen=[]
        def upstream(self,page):seen.append(f.y_offset);return 'completed'
        with patch.object(guard,'_original_styles_process_page',upstream):
            self.assertEqual(guard._guard_styles_process_page(object(),fixture),'completed')
        self.assertEqual(seen,[0]);self.assertAlmostEqual(f.y_offset,f.box.y-base.box.y)

    def test_narrow_i_after_and_does_not_bind_to_word_final_d(self):
        fixture,p,base,script,f=self.fixture(False)
        base.char_unicode='I';base.box.x=104; base.box.x2=107
        prefix=[]
        for value,x in zip('and ',(88,93,98,103)):
            c=character(value,ord(value),font_id='F2',x=x,y=base.box.y)
            c.pdf_style.font_size=11;c.box.x2=x+4
            prefix.append(c)
        # d is within the broad horizontal tolerance but belongs to a word.
        p.pdf_paragraph_composition[0].pdf_line.pdf_character=prefix+[base]
        self.assertEqual(guard.repair_subscript_offsets(fixture),1)
        self.assertAlmostEqual(f.y_offset,f.box.y-base.box.y)

class ScriptLineBindingTests(unittest.TestCase):
    def setUp(self):
        guard._script_bindings.clear()

    @staticmethod
    def fixture():
        import pymupdf
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        fixture,p,base,script,f=SubscriptOffsetTests.fixture(False)
        base.char_unicode='I'
        # Compound placeholder like the real P39 source: subscript + '= 97'.
        for offset,value in enumerate(' = 97 '):
            c=character(value,ord(value),font_id='F2',x=111+offset*5,y=base.box.y)
            c.pdf_style.font_size=11
            c.box.x2=c.box.x+5;c.box.y2=c.box.y+11
            c.visual_bbox.box=Box(c.box.x,c.box.y,c.box.x2,c.box.y2)
            f.pdf_character.append(c)
        f.box.x2=f.pdf_character[-1].box.x2;f.box.y2=base.box.y+11
        f.y_offset=f.box.y-base.box.y
        font=pymupdf.Font('helv')
        def text(value):
            return TypesettingUnit(unicode=value,font=font,original_font=fixture.pdf_font[1],
                    font_size=11,style=base.pdf_style,xobj_id=-1)
        units=[text('.'),text('.'),text('.'),text('I'),TypesettingUnit(formular=f)]
        typesetter=object.__new__(Typesetting)
        typesetter.font_mapper=SimpleNamespace(base_font=font)
        return fixture,p,base,script,f,units,typesetter

    def test_real_layout_keeps_compound_script_with_base_at_all_scales_and_fallback(self):
        from dataclasses import asdict
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        fixture,p,base,script,f,units,t=self.fixture()
        originals=[(c,asdict(c)) for c in f.pdf_character]
        state=asdict(f)
        self.assertEqual(guard.register_script_bindings(fixture),1)
        self.assertEqual(guard.register_script_bindings(fixture),0)
        old=guard._original_layout_typesetting_units or Typesetting._layout_typesetting_units
        for scale in (.5,1,1.5):
            for english in (True,False):
                with patch.object(guard,'_original_layout_typesetting_units',old):
                    # Narrow test column must fit the entire atom on the next line.
                    bound_width=guard._bound_typesetting_units(units)[-1].width*scale
                    box=Box(0,0,bound_width+1,100)
                    # Add enough punctuation to force the same boundary at this width.
                    prefix=[units[0]]*int((box.x2-2*units[-2].width*scale-1)/(units[0].width*scale))
                    before,_=old(t,prefix+units[-2:],box,scale,1.5,p,english)
                    self.assertNotAlmostEqual(before[-2].box.y,
                            before[-1].formular.pdf_character[0].box.y-(script.box.y-base.box.y)*scale)
                    laid,fit=guard._guard_layout_typesetting_units(t,prefix+units[-2:],box,scale,1.5,p,english)
                self.assertTrue(fit)
                self.assertTrue(all(isinstance(u,TypesettingUnit) for u in laid))
                self.assertAlmostEqual(laid[-1].formular.pdf_character[0].box.y-laid[-2].box.y,
                                       (script.box.y-base.box.y)*scale)
                self.assertGreaterEqual(laid[-2].box.x,box.x)
                self.assertLessEqual(laid[-1].box.x2,box.x2+.01)
                expected=units[-1].relocate(laid[-2].box.x2,laid[-2].box.y,scale)
                self.assertEqual(asdict(laid[-1].formular),asdict(expected.formular))
        self.assertEqual(asdict(f),state)
        for (original,state),current in zip(originals,f.pdf_character):
            self.assertIs(current,original);self.assertEqual(asdict(current),state)

    def test_binding_rejects_unproven_source_geometry_and_preserves_whole_formulas(self):
        import copy
        for mode in ('unknown','face','xobj','word','superscript','far','ambiguous','vertical','nan','wrong-offset','whole','multiline'):
            fixture,p,base,script,f,units,t=self.fixture()
            if mode=='unknown':fixture.pdf_font=[]
            elif mode=='face':fixture.pdf_font[0].name='Different'
            elif mode=='xobj':script.xobj_id=7
            elif mode=='word':p.pdf_paragraph_composition[0].pdf_line.pdf_character.insert(0,character('d'))
            elif mode=='superscript':script.box.y=base.box.y+3
            elif mode=='far':script.box.x+=100
            elif mode=='ambiguous':p.pdf_paragraph_composition.insert(0,PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[copy.deepcopy(base),character(' ')])))
            elif mode=='vertical':p.vertical=True
            elif mode=='nan':f.x_offset=float('nan')
            elif mode=='wrong-offset':f.y_offset=0
            elif mode=='whole':f.pdf_character.insert(0,base);p.pdf_paragraph_composition=p.pdf_paragraph_composition[1:]
            elif mode=='multiline':f.pdf_character[-1].box.y-=20
            self.assertEqual(guard.register_script_bindings(fixture),0,mode)
            self.assertEqual(guard._bound_typesetting_units(units),units,mode)

    def test_target_identity_font_size_binding_and_word_boundary_are_required(self):
        import copy
        fixture,p,base,script,f,units,t=self.fixture()
        guard.register_script_bindings(fixture)
        self.assertIsInstance(guard._bound_typesetting_units(units)[-1],guard._BoundScriptUnit)
        for mode in ('clone','different-base','word','font','size','xobj'):
            _,_,_,_,_,u,_=self.fixture()
            # Use exact registered formula unless intentionally testing a clone.
            u[-1].formular=copy.deepcopy(f) if mode=='clone' else f
            if mode=='different-base':u[-2].unicode='U'
            elif mode=='word':u[-3].unicode='a'
            elif mode=='font':u[-2].original_font=PdfFont(name='Other')
            elif mode=='size':u[-2].font_size=9
            elif mode=='xobj':u[-2].xobj_id=7
            self.assertEqual(guard._bound_typesetting_units(u),u,mode)

    def test_layout_exception_does_not_mutate_source_or_leave_temporary_units(self):
        from dataclasses import asdict
        fixture,p,base,script,f,units,t=self.fixture()
        guard.register_script_bindings(fixture)
        before=asdict(f);identities=[id(u) for u in units]
        with patch.object(guard,'_original_layout_typesetting_units',side_effect=ValueError('layout')):
            with self.assertRaises(ValueError):guard._guard_layout_typesetting_units(t,units,Box(0,0,100,100),1,1.5,p)
        self.assertEqual(asdict(f),before);self.assertEqual([id(u) for u in units],identities)

    def test_bound_atom_wider_than_column_requests_smaller_scale(self):
        fixture,p,base,script,f,units,t=self.fixture()
        guard.register_script_bindings(fixture)
        with patch.object(guard,'_original_layout_typesetting_units') as upstream:
            result=guard._guard_layout_typesetting_units(t,units,Box(0,0,10,100),1,1.5,p,False)
        self.assertEqual(result,([],False));upstream.assert_not_called()


class AdjacentFormulaOffsetTests(unittest.TestCase):
    @staticmethod
    def fixture(base_text='U',number='10',tail='.5 V'):
        base=character(base_text,ord(base_text),font_id='B',x=410,y=717.9106)
        base.pdf_style.font_size=11;base.box.x2=419.3;base.box.y2=728.9106
        base.visual_bbox.box=Box(410,717.7,419.3,725.9)
        script=character('o',111,font_id='S',x=419.6631,y=714.2806)
        script.pdf_style.font_size=6.38;script.box.x2=423.22952;script.box.y2=720.6606
        script.visual_bbox.box=Box(419.8998578125,714.184027734375,422.993285546875,717.71358828125)
        chars=[]
        for i,c in enumerate('= '+number):
            item=character(c,ord(c),font_id='B',x=423.535+i*5.5,y=717.9106)
            item.pdf_style.font_size=11;item.box.x2=item.box.x+5.5;item.box.y2=item.box.y+11
            item.visual_bbox.box=Box(item.box.x,711.4866 if c==' ' else 717.9106,item.box.x2,725.645)
            chars.append(item)
        f=PdfFormula(pdf_character=chars,box=Box(423.535,711.4866,chars[-1].box.x2,725.645),
                     x_offset=.7459,y_offset=-2.794,x_advance=0)
        p=PdfParagraph(vertical=False,xobj_id=-1,pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[base,script])),
            PdfParagraphComposition(pdf_formula=f)])
        fixture=Page(pdf_paragraph=[p],pdf_font=[PdfFont(font_id='B',name='ABCDEF+HelveticaWorld-Regular'),
                                               PdfFont(font_id='S',name='UVWXYZ+HelveticaWorld-Regular')])
        return fixture,p,base,script,f,tail

    def test_all_three_measurement_fragments_and_decimal_tails_share_body_baseline(self):
        import pymupdf
        from dataclasses import asdict
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        from babeldoc.format.pdf.document_il.utils.formular_helper import update_formula_data
        old=guard._original_layout_typesetting_units or Typesetting._layout_typesetting_units
        for base_name,number,tail in [('U','10','.5 V'),('I','17','.1 mA'),('P','45',' mW')]:
            fixture,p,base,script,f,tail=self.fixture(base_name,number,tail)
            state=asdict(f);identities=list(f.pdf_character)
            self.assertEqual(guard.repair_script_following_formula_offsets(fixture),1)
            self.assertAlmostEqual(f.y_offset,-6.424)
            self.assertEqual(guard.repair_script_following_formula_offsets(fixture),0)
            state['y_offset']=f.y_offset
            self.assertEqual(asdict(f),state)
            for a,b in zip(identities,f.pdf_character):self.assertIs(a,b)
            small=PdfFormula(pdf_character=[script]);update_formula_data(small)
            small.x_offset=small.box.x-base.box.x2;small.y_offset=small.box.y-base.box.y
            font=pymupdf.Font('helv')
            def text(c):return TypesettingUnit(unicode=c,font=font,original_font=fixture.pdf_font[0],
                                               font_size=11,style=base.pdf_style,xobj_id=-1)
            units=[text(base_name),TypesettingUnit(formular=small),TypesettingUnit(formular=f)]+[text(c) for c in tail]
            t=object.__new__(Typesetting);t.font_mapper=SimpleNamespace(base_font=font)
            for scale in (.5,1,1.5):
                out,fit=old(t,units,Box(0,0,500,100),scale,1.5,p,False)
                self.assertTrue(fit)
                body_y=out[0].box.y
                for c in out[2].formular.pdf_character:self.assertAlmostEqual(c.box.y,body_y)
                for u in out[3:]:self.assertAlmostEqual(u.render()[0][0].box.y,body_y)
                self.assertAlmostEqual(out[1].formular.pdf_character[0].box.y-body_y,-3.63*scale)
            self.assertEqual(asdict(f),state)

    def test_nonmatching_formulas_geometry_and_already_correct_offsets_unchanged(self):
        import copy
        from dataclasses import asdict
        for mode in ('correct','compound','whole','crossline','superscript','face','unknown','xobj',
                     'ambiguous','nan','curve','word','far','other-offset','script-formula'):
            fixture,p,base,script,f,_=self.fixture()
            if mode=='correct':f.y_offset=f.box.y-base.box.y
            elif mode=='compound':f.pdf_character[-1].char_unicode='L'
            elif mode=='whole':f.pdf_character[:0]=[base,script];p.pdf_paragraph_composition=p.pdf_paragraph_composition[1:]
            elif mode=='crossline':f.pdf_character[0].box.y-=14
            elif mode=='superscript':script.box.y=base.box.y+3.63
            elif mode=='face':fixture.pdf_font[1].name='Different-Regular'
            elif mode=='unknown':fixture.pdf_font=[]
            elif mode=='xobj':f.pdf_character[0].xobj_id=7
            elif mode=='ambiguous':p.pdf_paragraph_composition.insert(0,PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[copy.deepcopy(base),character(' ')])))
            elif mode=='nan':f.box.y=float('inf')
            elif mode=='curve':f.pdf_curve=[object()]
            elif mode=='word':p.pdf_paragraph_composition[0].pdf_line.pdf_character.insert(0,character('a'))
            elif mode=='far':f.pdf_character[0].box.x+=50
            elif mode=='other-offset':f.y_offset=1.23
            elif mode=='script-formula':
                p.pdf_paragraph_composition[0].pdf_line.pdf_character=[base]
                p.pdf_paragraph_composition.insert(1,PdfParagraphComposition(pdf_formula=PdfFormula(pdf_character=[script])))
            offset=f.y_offset;chars=list(f.pdf_character)
            self.assertEqual(guard.repair_script_following_formula_offsets(fixture),0,mode)
            self.assertEqual(f.y_offset,offset,mode)
            for a,b in zip(chars,f.pdf_character):self.assertIs(a,b)


class StructuredEntryTests(unittest.TestCase):
    def setUp(self):
        guard._hanging_indents.clear()
        guard._counters.clear()
        self.finder=object.__new__(ParagraphFinder)

    def test_numbered_physical_rows_split_inside_one_upstream_line(self):
        p=line_paragraph(['1 First item with detail.', '2 Second item with detail.', '3 Third item.'])
        glyphs=[c for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
        p.pdf_paragraph_composition=[PdfParagraphComposition(pdf_line=PdfLine(pdf_character=glyphs))]
        fixture=page([]);fixture.pdf_paragraph=[p]
        self.assertEqual(guard.split_structured_entries(self.finder,fixture),2)
        self.assertEqual([p.unicode for p in fixture.pdf_paragraph],['1 First item with detail.','2 Second item with detail.','3 Third item.'])
        after=[c for p in fixture.pdf_paragraph for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
        self.assertEqual([id(c) for c in glyphs],[id(c) for c in after])
        self.assertEqual(len(guard._hanging_indents),3)

    def test_nonconsecutive_numbers_and_ordinary_prose_untouched(self):
        for texts in [['2 results increased','5 results decreased'],['2024 study reports facts','2025 study reports more'],['Body one','Body two']]:
            p=line_paragraph(texts);fixture=page([]);fixture.pdf_paragraph=[p]
            self.assertEqual(guard.split_structured_entries(self.finder,fixture),0)
            self.assertIs(fixture.pdf_paragraph[0],p)

    def test_author_bibliography_splits_with_source_hanging_indent(self):
        p=line_paragraph(['Smith, A., 2024. Paper title','Journal 12, 10-20.','Jones, B., 2025. Other paper','Journal 13, 20-30.'])
        for index in [1,3]:
            for c in p.pdf_paragraph_composition[index].pdf_line.pdf_character:
                c.box.x+=12;c.box.x2+=12;c.visual_bbox.box.x+=12;c.visual_bbox.box.x2+=12
        fixture=page([]);fixture.pdf_paragraph=[p]
        self.assertEqual(guard.split_structured_entries(self.finder,fixture),1)
        self.assertEqual([v[1] for v in guard._hanging_indents.values()],[12,12])
        self.assertTrue(fixture.pdf_paragraph[1].unicode.startswith('Jones'))

    def test_author_mentions_without_hanging_geometry_are_not_references(self):
        p=line_paragraph(['Smith, A., 2024 reports this','Jones, B., 2025 reports that'])
        fixture=page([]);fixture.pdf_paragraph=[p]
        self.assertEqual(guard.split_structured_entries(self.finder,fixture),0)

    def test_existing_bullet_registers_continuation_column(self):
        p=line_paragraph(['• First bullet entry','continuation detail'])
        for c in p.pdf_paragraph_composition[1].pdf_line.pdf_character:
            c.box.x+=8;c.box.x2+=8;c.visual_bbox.box.x+=8;c.visual_bbox.box.x2+=8
        fixture=page([]);fixture.pdf_paragraph=[p]
        self.assertEqual(guard.split_structured_entries(self.finder,fixture),0)
        self.assertEqual(guard._hanging_indents[id(p)][1],8)
        self.assertFalse(p.first_line_indent)

    def test_shift_rendered_formula_preserves_vertical_offsets_and_original(self):
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        c=character('o',x=20,y=30)
        f=PdfFormula(pdf_character=[c],box=Box(20,30,28,38),x_offset=1,y_offset=-3,x_advance=8)
        unit=TypesettingUnit(formular=f)
        moved=unit.relocate(100,200,.8)
        before_y=moved.formular.pdf_character[0].box.y
        before_x=moved.formular.pdf_character[0].box.x
        guard._shift_rendered_unit_x(moved,-10)
        self.assertAlmostEqual(moved.formular.pdf_character[0].box.y,before_y)
        self.assertAlmostEqual(moved.formular.pdf_character[0].box.x,before_x-10)
        self.assertEqual(c.box,Box(20,30,28,38))
        self.assertAlmostEqual(moved.formular.y_offset,-2.4)

    def test_layout_uses_continuation_width_and_only_shifts_first_line(self):
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        p=PdfParagraph();guard._hanging_indents[id(p)]=(p,12)
        units=[TypesettingUnit(char=character('A',x=0,y=0))]
        def upstream(self,grouped,box,scale,line_skip,paragraph,english):
            self.assertion=(box.x,box.x2)
            return [grouped[0].relocate(box.x,100,1),grouped[0].relocate(box.x,80,1)],True
        obj=SimpleNamespace()
        with patch.object(guard,'_original_layout_typesetting_units',upstream):
            out,fit=guard._guard_layout_typesetting_units(obj,units,Box(0,0,100,120),1,1.5,p)
        self.assertEqual(obj.assertion,(12,100));self.assertTrue(fit)
        self.assertEqual(out[0].box.x,0);self.assertEqual(out[1].box.x,12)

    def test_first_line_script_offsets_do_not_end_hanging_shift(self):
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        for first_formula in [False,True]:
            with self.subTest(first_formula=first_formula):
                p=PdfParagraph();guard._hanging_indents[id(p)]=(p,12)
                normal=TypesettingUnit(char=character('A',x=0,y=0))
                c=character('2',x=0,y=0)
                formula=TypesettingUnit(formular=PdfFormula(pdf_character=[c],box=Box(0,0,8,8),x_offset=0,y_offset=4,x_advance=8))
                units=[formula,normal,normal] if first_formula else [normal,formula,normal]
                def upstream(self,grouped,box,scale,line_skip,paragraph,english):
                    return [grouped[i].relocate(box.x+i*10,100,1) for i in range(3)]+[grouped[2].relocate(box.x,80,1)],True
                with patch.object(guard,'_original_layout_typesetting_units',upstream):
                    out,fit=guard._guard_layout_typesetting_units(object(),units,Box(0,0,100,120),1,1.5,p)
                self.assertTrue(fit);self.assertEqual([u.box.x for u in out],[0,10,20,12])
                self.assertEqual(out[0 if first_formula else 1].formular.pdf_character[0].box.y,104)


class CaptionFragmentTests(unittest.TestCase):
    def fixture(self):
        prefix=line_paragraph(['Figure 9'],label='fallback_line',size=8)
        body=line_paragraph(['. Example values are added u'],label='figure_caption',size=8)
        tail=line_paragraph(['p'],label='fallback_line',size=8)
        for p,offset in [(body,40),(tail,180)]:
            for comp in p.pdf_paragraph_composition:
                for c in comp.pdf_line.pdf_character:
                    c.box.x+=offset;c.box.x2+=offset;c.visual_bbox.box.x+=offset;c.visual_bbox.box.x2+=offset
        fixture=page([]);fixture.pdf_paragraph=[prefix,body,tail]
        return fixture,prefix,body,tail

    def test_caption_prefix_and_tiny_word_tail_join_without_glyph_replacement(self):
        f,a,b,c=self.fixture();finder=object.__new__(ParagraphFinder)
        before=[id(c) for p in f.pdf_paragraph for comp in p.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
        self.assertEqual(guard.merge_caption_fragments(finder,f),2)
        self.assertEqual(f.pdf_paragraph,[b]);self.assertEqual(b.unicode,'Figure 9. Example values are added up')
        after=[id(c) for comp in b.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
        self.assertEqual(before,after)
        self.assertEqual(guard.merge_caption_fragments(finder,f),0)

    def test_ambiguous_or_unrelated_neighbors_not_joined(self):
        import copy
        f,a,b,c=self.fixture();f.pdf_paragraph.append(copy.deepcopy(c))
        self.assertEqual(guard.merge_caption_fragments(object.__new__(ParagraphFinder),f),1)
        self.assertTrue(b.unicode.endswith(' u'))
        f,a,b,c=self.fixture();c.pdf_paragraph_composition[0].pdf_line.pdf_character[0].box.y-=10
        self.assertEqual(guard.merge_caption_fragments(object.__new__(ParagraphFinder),f),1)


if __name__ == "__main__":
    unittest.main()
