import concurrent.futures
import copy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import translation_guard as g
import babeldoc.const
_context_test_cache = tempfile.TemporaryDirectory(prefix="pagebridge-context-tests-")
babeldoc.const.CACHE_FOLDER = Path(_context_test_cache.name)
from babeldoc.format.pdf.document_il import (Box, PdfCharacter, PdfFormula,
    PdfParagraphComposition, PdfSameStyleCharacters, PdfStyle)


def source_compositions(parts):
    compositions, offset = [], 0
    for formula, text in parts:
        chars = [PdfCharacter(char_unicode=c, box=Box(offset+i, 0, offset+i+1, 10),
                 pdf_style=PdfStyle(font_id="body", font_size=10)) for i, c in enumerate(text)]
        value = PdfFormula(pdf_character=chars) if formula else PdfSameStyleCharacters(pdf_character=chars)
        compositions.append(PdfParagraphComposition(**{"pdf_formula" if formula else "pdf_same_style_characters": value}))
        offset += len(text)
    return compositions


def para(text, box, label="plain text"):
    return NS(unicode=text, box=NS(x=box[0], y=box[1], x2=box[2], y2=box[3]),
              pdf_style=NS(font_id="body", font_size=10), layout_label=label,
              vertical=False, xobj_id=None, pdf_paragraph_composition=source_compositions([(False, text)]))


def page():
    return NS(pdf_paragraph=[
        para("While the measured variations become larger, the general tendency", (50, 50, 290, 240)),
        para("remains: the network responds before it drifts away from the critical point.", (310, 210, 550, 240)),
        para("A separate paragraph gives another result for the same model.", (310, 50, 550, 200)),
    ])


class SourceContinuityTests(unittest.TestCase):
    def test_source_proof_keeps_content_geometry_and_objects_identical(self):
        p = page()
        before = [(q.unicode, q.box, q.pdf_paragraph_composition) for q in p.pdf_paragraph]
        self.assertEqual(g._capture_source_continuity(NS(page=[p])), 1)
        left, right, other = p.pdf_paragraph
        self.assertEqual(g._source_context_for_paragraph(left).following, right.unicode)
        self.assertEqual(g._source_context_for_paragraph(right).previous, left.unicode)
        self.assertIsNone(g._source_context_for_paragraph(other))
        for q, (text, box, comps) in zip(p.pdf_paragraph, before):
            self.assertEqual(q.unicode, text)
            self.assertIs(q.box, box)
            self.assertIs(q.pdf_paragraph_composition, comps)

    def test_ambiguous_or_nonprose_boundaries_are_excluded(self):
        mutations = {
            "sentence": lambda p: setattr(p.pdf_paragraph[0], "unicode", p.pdf_paragraph[0].unicode + "."),
            "colon_to_list": lambda p: setattr(p.pdf_paragraph[0], "unicode", p.pdf_paragraph[0].unicode + ":"),
            "uppercase": lambda p: setattr(p.pdf_paragraph[1], "unicode", "Remains: another independent heading follows here."),
            "list": lambda p: setattr(p.pdf_paragraph[1], "unicode", "1. remains: a numbered list is not a sentence continuation."),
            "different_font": lambda p: setattr(p.pdf_paragraph[1].pdf_style, "font_id", "caption"),
            "different_size": lambda p: setattr(p.pdf_paragraph[1].pdf_style, "font_size", 8),
            "vertical": lambda p: setattr(p.pdf_paragraph[1], "vertical", True),
            "wrong_order": lambda p: p.pdf_paragraph.reverse(),
            "heading_between": lambda p: p.pdf_paragraph.insert(1, para("New section heading changes the topic", (310, 245, 550, 260), "title")),
            "caption_between": lambda p: p.pdf_paragraph.insert(1, para("Figure 1. A caption is not part of the source sentence.", (50, 245, 550, 260), "figure_caption")),
            "overlap": lambda p: setattr(p.pdf_paragraph[1].box, "x", 250),
            "wide_gap": lambda p: setattr(p.pdf_paragraph[1].box, "x", 400),
            "different_width": lambda p: setattr(p.pdf_paragraph[1].box, "x2", 450),
            "not_left_bottom": lambda p: p.pdf_paragraph.append(para("Another source body paragraph sits below this one.", (50, 20, 290, 45))),
            "not_right_top": lambda p: p.pdf_paragraph.append(para("Another source body paragraph sits above this one.", (310, 250, 550, 280))),
            "different_object": lambda p: setattr(p.pdf_paragraph[1], "xobj_id", 3),
            "nan": lambda p: setattr(p.pdf_paragraph[1].box, "y", float("nan")),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                p = page(); mutate(p)
                self.assertEqual(g._capture_source_continuity(NS(page=[p])), 0)

    def test_source_snapshot_not_later_translation(self):
        p = page(); g._capture_source_continuity(NS(page=[p]))
        left, right, _ = p.pdf_paragraph
        saved = g._source_context_for_paragraph(right)
        left.unicode = "This is no longer source data."
        self.assertNotIn("no longer", saved.previous)
        with self.assertRaises(AttributeError):
            saved.previous = "mutated"

    def test_slot_backed_paragraphs_and_later_documents_do_not_keep_stale_context(self):
        class SlottedParagraph:
            __slots__ = ("unicode", "box", "pdf_style", "layout_label", "vertical",
                         "xobj_id", "pdf_paragraph_composition")
        p = page()
        real = []
        for original in p.pdf_paragraph:
            target = SlottedParagraph()
            for name in SlottedParagraph.__slots__:
                setattr(target, name, getattr(original, name))
            real.append(target)
        p.pdf_paragraph = real
        self.assertEqual(g._capture_source_continuity(NS(page=[p])), 1)
        self.assertIsNotNone(g._source_context_for_paragraph(real[1]))
        self.assertFalse(hasattr(real[1], g._CONTINUITY_ATTR))
        self.assertEqual(g._capture_source_continuity(NS(page=[])), 0)
        self.assertIsNone(g._source_context_for_paragraph(real[1]))

    def test_context_is_bounded_at_word_boundaries(self):
        text = " ".join("token" + str(i) for i in range(200))
        for tail in (True, False):
            fragment = g._context_fragment(text, tail=tail)
            self.assertLessEqual(len(fragment), 360)
            self.assertLessEqual(len(fragment.split()), 48)
            self.assertTrue(all(word in text.split() for word in fragment.split()))
        self.assertTrue(g._context_fragment(text, tail=True).endswith("token199"))

    def test_literal_engine_syntax_never_becomes_other_fragments_context(self):
        tokens = ("{v1}", "{ V 12 }", "{PBKEEP_0001}", "{PBKEEPX_0002}",
                  "<style id='2'>", "</style>", '<span id="3">', "<风格 id='4'>")
        for token in tokens:
            for side in (0, 1):
                with self.subTest(token=token, side=side):
                    p = page()
                    # Keep the same unfinished source ending and initial word.
                    p.pdf_paragraph[side].unicode = p.pdf_paragraph[side].unicode.replace("the ", token + " the ", 1)
                    p.pdf_paragraph[side].pdf_paragraph_composition = source_compositions([(False, p.pdf_paragraph[side].unicode)])
                    self.assertEqual(g._capture_source_continuity(NS(page=[p])), 1)
                    self.assertIsNone(g._source_context_for_paragraph(p.pdf_paragraph[1 - side]))
            self.assertEqual(g._source_continuity_prompt(g._SourceContinuity(previous=token)), "")
            self.assertEqual(g._source_continuity_prompt(g._SourceContinuity(following=token)), "")
        self.assertFalse(g._context_has_engine_syntax("The exponent {3=2 and ordinary variable v1 are source glyphs."))

    def test_real_formula_glyphs_are_excluded_without_joining_its_sides(self):
        p = page()
        left_parts = [(False, "The relationship "), (True, "F = ma"),
                      (False, " is measured, but the general tendency")]
        right_parts = [(False, "remains: the network responds before the value "), (True, "K~9"),
                       (False, " becomes large in another formula sentence.")]
        for target, parts in zip(p.pdf_paragraph, (left_parts, right_parts)):
            target.unicode = "".join(text for _, text in parts)
            target.pdf_paragraph_composition = source_compositions(parts)
        source_formulas = [comp.pdf_formula for q in p.pdf_paragraph for comp in q.pdf_paragraph_composition if comp.pdf_formula]
        self.assertIn("F = ma", g._context_fragment(p.pdf_paragraph[0].unicode, tail=True))
        self.assertEqual(g._capture_source_continuity(NS(page=[p])), 1)
        previous = g._source_context_for_paragraph(p.pdf_paragraph[1]).previous
        following = g._source_context_for_paragraph(p.pdf_paragraph[0]).following
        self.assertEqual(previous, "is measured, but the general tendency")
        self.assertEqual(following, "remains: the network responds before the value")
        prompt = g._source_continuity_prompt(g._SourceContinuity(previous, following))
        for leaked in ("F = ma", "K~9", "The relationship", "another formula sentence"):
            self.assertNotIn(leaked, prompt)
        self.assertIs(p.pdf_paragraph[0].pdf_paragraph_composition[1].pdf_formula, source_formulas[0])
        self.assertIs(p.pdf_paragraph[1].pdf_paragraph_composition[1].pdf_formula, source_formulas[1])

    def test_formula_at_edge_disables_only_that_direction(self):
        p = page()
        parts = [(False, "The relationship has been measured as "), (True, "F = ma")]
        p.pdf_paragraph[0].unicode = "".join(text for _, text in parts)
        p.pdf_paragraph[0].pdf_paragraph_composition = source_compositions(parts)
        self.assertEqual(g._source_prose_context_fragment(p.pdf_paragraph[0], tail=True), "")
        self.assertEqual(g._capture_source_continuity(NS(page=[p])), 1)
        self.assertIsNone(g._source_context_for_paragraph(p.pdf_paragraph[1]))
        self.assertIsNotNone(g._source_context_for_paragraph(p.pdf_paragraph[0]))

    def test_changed_spacing_unknown_composition_and_partial_words_fail_closed(self):
        p = page().pdf_paragraph[0]
        p.unicode = p.unicode.replace("general tendency", "generaltendency")
        self.assertEqual(g._source_prose_context_fragment(p, tail=True), "")
        p = page().pdf_paragraph[0]
        p.pdf_paragraph_composition.insert(0, object())
        self.assertEqual(g._source_prose_context_fragment(p, tail=True), "")
        parts = [(False, "The model estimates "), (True, "m"), (False, "ass in this general tendency")]
        p = para("".join(t for _, t in parts), (50, 50, 290, 240))
        p.pdf_paragraph_composition = source_compositions(parts)
        self.assertEqual(g._source_prose_context_fragment(p, tail=True), "")

    def test_multiple_formulas_never_rejoin_disjoint_prose(self):
        parts = [(False, "prefix before first formula "), (True, "F = ma"),
                 (False, " middle prose should not leak "), (True, "E = mc2"),
                 (False, " final source tendency continues")]
        p = para("".join(t for _, t in parts), (50, 50, 290, 240))
        p.pdf_paragraph_composition = source_compositions(parts)
        self.assertEqual(g._source_prose_context_fragment(p, tail=True), "final source tendency continues")
        self.assertEqual(g._source_prose_context_fragment(p, tail=False), "prefix before first formula")

    def test_formula_character_reused_as_prose_is_ambiguous(self):
        parts = [(True, "F"), (False, " source text would otherwise continue")]
        p = para("".join(t for _, t in parts), (50, 50, 290, 240))
        p.pdf_paragraph_composition = source_compositions(parts)
        formula_char = p.pdf_paragraph_composition[0].pdf_formula.pdf_character[0]
        p.pdf_paragraph_composition[1].pdf_same_style_characters.pdf_character.insert(0, formula_char)
        p.unicode = "FF source text would otherwise continue"
        self.assertEqual(g._source_prose_context_fragment(p, tail=True), "")

    def test_batch_context_matches_exact_prepared_order_and_ids(self):
        p = page(); g._capture_source_continuity(NS(page=[p]))
        ti = NS(unicode="remains: actual input {v1} stays in this fragment")
        token = g._continuity_batch.set([])
        try:
            g._prepare_source_continuity(p.pdf_paragraph[1], ti)
            data = json.dumps([{"id": 7, "input": ti.unicode, "layout_label": "text"}])
            before = data
            hint = g._source_batch_context_prompt(data)
            self.assertIn('"input_item_id": 7', hint)
            self.assertIn("the general tendency", hint)
            self.assertNotIn("{v1}", hint)
            self.assertIn("Translate only that fragment", hint)
            self.assertEqual(data, before)
            self.assertEqual(g._source_batch_context_prompt(data.replace("actual input", "changed input")), "")
            self.assertEqual(g._source_batch_context_prompt("[]"), "")
        finally:
            g._continuity_batch.reset(token)

    def test_context_survives_rich_style_reduction_without_new_formula_objects(self):
        class TI:
            def __init__(self, text, placeholders, base_style):
                self.unicode, self.placeholders, self.base_style = text, placeholders, base_style
        ti = TI("A source fragment without a style wrapper.", [], object())
        context = g._SourceContinuity(previous="The source preceding this fragment")
        setattr(ti, g._CONTINUITY_ATTR, context)
        reduced, count = g._without_rich_wrappers(ti)
        self.assertEqual(count, 0)
        self.assertEqual(reduced.unicode, ti.unicode)
        self.assertIs(getattr(reduced, g._CONTINUITY_ATTR), context)

    def test_single_prompt_includes_readonly_context_and_intact_target(self):
        ti = NS(unicode="remains: {v1}")
        setattr(ti, g._CONTINUITY_ATTR, g._SourceContinuity(previous="the general tendency"))
        original = lambda self, text, *args, **kwargs: "TARGET:" + text
        with patch.dict(g._originals, single_prompt_context=original):
            prompt = g._guard_single_prompt_context(None, ti.unicode, translate_input=ti)
        self.assertIn("the general tendency", prompt)
        self.assertTrue(prompt.endswith("TARGET:remains: {v1}"))
        self.assertEqual(prompt.count("{v1}"), 1)

    def test_batch_context_is_isolated_between_threads_and_reset_after_error(self):
        import threading
        barrier = threading.Barrier(2)
        def run(self, label):
            g._continuity_batch.get().append((label, g._SourceContinuity(previous=label)))
            barrier.wait(timeout=5)
            return g._source_batch_context_prompt(json.dumps([{"id": 0, "input": label}]))
        with patch.dict(g._originals, batch_paragraph_context=run):
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                futures = [pool.submit(g._guard_batch_paragraph_context, None, label) for label in ("one", "two")]
                results = [f.result() for f in futures]
        self.assertIn('"previous_source_fragment": "one"', results[0])
        self.assertNotIn('"previous_source_fragment": "two"', results[0])
        self.assertIn('"previous_source_fragment": "two"', results[1])
        self.assertIsNone(g._continuity_batch.get())
        def fail(*a, **k): raise ValueError("fake request failure")
        with patch.dict(g._originals, batch_paragraph_context=fail):
            with self.assertRaises(ValueError): g._guard_batch_paragraph_context(None)
        self.assertIsNone(g._continuity_batch.get())


if __name__ == "__main__":
    unittest.main()
