import json
import hashlib
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import translation_guard as guard
# BabelDOC initializes SQLite during import; keep these offline tests independent
# of the user's translation cache without changing HOME or loading a model.
import babeldoc.const
_test_cache = tempfile.TemporaryDirectory(prefix="pagebridge-guard-tests-")
babeldoc.const.CACHE_FOLDER = Path(_test_cache.name)
from babeldoc.format.pdf.document_il.midend.il_translator import (
    ILTranslator, FormulaPlaceholder, RichTextPlaceholder, ParagraphTranslateTracker,
)
from babeldoc.format.pdf.document_il import (PdfCharacter, PdfFormula, PdfSameStyleCharacters, PdfStyle,
                                           Box, VisualBbox, PdfParagraphComposition, PdfLine)


def fixture():
    formula = FormulaPlaceholder(1, PdfFormula(), "{v1}", r"\{\s*v\s*1\s*\}")
    style = RichTextPlaceholder(2, PdfSameStyleCharacters(pdf_style=PdfStyle()),
                               "<style id='2'>", "</style>",
                               r"<\s*style\s*id\s*=\s*'\s*2\s*'\s*>", r"<\s*/\s*style\s*>")
    ti = ILTranslator.TranslateInput("Flow {v1} <style id='2'>rate</style>.", [formula, style], PdfStyle())
    return ti


def translator(responses=()):
    instance = object.__new__(ILTranslator)
    instance._formula_placeholder_pattern = re.compile(r"\{\s*v\s*\d+\s*\}", re.I)
    instance._style_left_placeholder_pattern = re.compile(r"<\s*style\s*id\s*=\s*'\s*\d+\s*'\s*>", re.I)
    instance._style_right_placeholder_pattern = re.compile(r"<\s*/\s*style\s*>", re.I)
    pending = iter(responses)
    calls = []
    def llm(prompt, **kwargs):
        calls.append((prompt, kwargs))
        value = next(pending)
        if isinstance(value, Exception):
            raise value
        return value
    instance.translate_engine = SimpleNamespace(llm_translate=llm)
    instance.translation_config = SimpleNamespace(lang_out="zh")
    instance.generate_prompt_for_llm = lambda text, **kwargs: "Translate: " + text
    instance.calc_token_count = len
    instance.calls = calls
    return instance


def paragraph(ti):
    return SimpleNamespace(unicode=ti.unicode, pdf_paragraph_composition=[object()],
                           pdf_style=ti.base_style, layout_label="text")


def short_connector_fixture(text="with"):
    base = PdfStyle(font_id="F5", font_size=8.9663)
    chars = [PdfCharacter(char_unicode=c, pdf_character_id=ord(c), pdf_style=base,
                          box=Box(58 + index * 4, 522, 62 + index * 4, 531))
             for index, c in enumerate(text)]
    ti = ILTranslator.TranslateInput(text, [], base)
    para = paragraph(ti)
    para.layout_label = "plain text"
    para.vertical = False
    para.xobj_id = None
    para.debug_id = "source-connector"
    para.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=chars))]
    return ti, para


def captured_rich_script_fixture(unit=False):
    """Minimal original glyphs from final source P14/P44 (no generated glyph IDs)."""
    fonts = {"C2_2": SimpleNamespace(name="DSIHYM+HelveticaWorld-Regular"),
             "TT0": SimpleNamespace(name="VGHLQC+HelveticaWorld-Regular")}
    def glyph(text, cid, size, box, visual=None):
        return PdfCharacter(char_unicode=text, pdf_character_id=cid,
                            pdf_style=PdfStyle(font_id="TT0", font_size=size), box=Box(*box),
                            visual_bbox=VisualBbox(box=Box(*(visual or box))))
    if not unit:
        base = PdfStyle(font_id="C2_2", font_size=11)
        base_chars = [glyph("L",76,11,(384.3744,533.2,390.4904,544.2))]
        script = glyph("i",105,6.38,(390.8556,529.5699,392.2592,535.9499),
                       (391.276156640625,529.5699,391.836898828125,534.14929453125))
        rich = RichTextPlaceholder(2, PdfSameStyleCharacters(pdf_style=script.pdf_style,pdf_character=[script]),
                                  "<style id='2'>","</style>",r"<style id='2'>",r"</style>")
        bullet = FormulaPlaceholder(1,PdfFormula(pdf_character=[PdfCharacter(char_unicode="•")]),"{v1}",r"\{v1\}")
        measure = FormulaPlaceholder(4,PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in "= 100 μ"]),"{v4}",r"\{v4\}")
        ti = ILTranslator.TranslateInput("{v1}Internal inductance L<style id='2'>i</style> {v4}H",[bullet,rich,measure],base)
        source_chars = base_chars+[script]
    else:
        base = PdfStyle(font_id="TT0",font_size=11)
        base_chars = [glyph("m",109,11,(356.6298,379.3661,365.7598,390.3661)),
                      glyph("m",109,11,(365.9028,379.3661,375.0328,390.3661))]
        script = glyph("2",50,6.38,(376.0844,383.0001,379.63168,389.3801),
                       (376.249507421875,383.0001,379.31801328125,387.4860375))
        rich = RichTextPlaceholder(1,PdfSameStyleCharacters(pdf_style=script.pdf_style,pdf_character=[script]),
                                  "<style id='1'>","</style>",r"<style id='1'>",r"</style>")
        second = glyph("2",50,6.38,(464.1375,383.0001,467.68478,389.3801))
        original = FormulaPlaceholder(3,PdfFormula(pdf_character=[second],x_offset=1.0478,y_offset=3.63),"{v3}",r"\{v3\}")
        ti = ILTranslator.TranslateInput("The earth connection cross-section (copper) is: 2 x min. 1.5 mm <style id='1'>2</style> or 1 x min. 4 mm{v3}",[rich,original],base)
        source_chars = base_chars+[script,glyph("m",109,11,(444.6867,379.3701,453.8167,390.3701)),
                                  glyph("m",109,11,(453.9597,379.3701,463.0897,390.3701)),second]
    para = paragraph(ti)
    para.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=source_chars))]
    return ti,para,fonts,script,base_chars


def atomic_prose_fixture():
    base = PdfStyle(font_id="TT1", font_size=11)
    formulae = []
    for index in (1, 2):
        chars = [PdfCharacter(char_unicode="o", pdf_style=PdfStyle(font_id="TT1", font_size=6.38))]
        if index == 2:
            chars.append(PdfCharacter(char_unicode=" ", pdf_style=PdfStyle(font_id="TT1", font_size=11)))
        formulae.append(FormulaPlaceholder(index, PdfFormula(pdf_character=chars), f"{{v{index}}}", rf"\{{v{index}\}}"))
    return ILTranslator.TranslateInput(
        "The curve describes the maximum inductance L{v1}which may be connected to an apparatus with a given I{v2}without affecting intrinsic safety.",
        formulae, base)


def prime_variable_fixture():
    """P54 source shape: six ordinary C primes and four rich small subscripts."""
    base = PdfStyle(font_id="TT0", font_size=11)
    placeholders = []
    for number, value in ((1, "•"), (2, "= "), (5, "+ 0"), (8, "= "), (11, "+ ")):
        chars = [PdfCharacter(char_unicode=c, pdf_style=base) for c in value]
        placeholders.append(FormulaPlaceholder(number, PdfFormula(pdf_character=chars), f"{{v{number}}}", rf"\{{v{number}\}}"))
    for number, value in ((3, "wire/wire"), (6, "wire/shield"), (9, "wire/wire"), (12, "wire/shield")):
        style = PdfStyle(font_id="TT0", font_size=6.38)
        composition = PdfSameStyleCharacters(pdf_style=style,
            pdf_character=[PdfCharacter(char_unicode=c, pdf_style=style) for c in value])
        placeholders.append(RichTextPlaceholder(number, composition,
            f"<style id='{number}'>", "</style>", rf"<style id='{number}'>", r"</style>"))
    placeholders.sort(key=lambda p: p.id)
    return ILTranslator.TranslateInput(
        "{v1}Taking account of the shield, capacitance per unit length is calculated as follows: "
        "C’ {v2}C’<style id='3'>wire/wire</style> {v5}.5 x C’<style id='6'>wire/shield</style>, "
        "if the bus cable is free of potential or C’ {v8}C’<style id='9'>wire/wire</style> "
        "{v11}C’<style id='12'>wire/shield</style>, if the shield is connected to the power supply by a pole",
        placeholders, base)


def descriptor_relation_fixture(base_letter="L"):
    base = PdfStyle(font_id="TT0", font_size=11)
    placeholders = []
    for number, value in ((1, "• "), (4, "> "), (7, "+ ")):
        chars = [PdfCharacter(char_unicode=c, pdf_style=base) for c in value]
        placeholders.append(FormulaPlaceholder(number, PdfFormula(pdf_character=chars), f"{{v{number}}}", rf"\{{v{number}\}}"))
    for number, value in ((2, "o"), (5, "cable"), (8, "i")):
        style = PdfStyle(font_id="TT0", font_size=6.38)
        composition = PdfSameStyleCharacters(pdf_style=style,
            pdf_character=[PdfCharacter(char_unicode=c, pdf_style=style) for c in value])
        placeholders.append(RichTextPlaceholder(number, composition,
            f"<style id='{number}'>", "</style>", rf"<style id='{number}'>", r"</style>"))
    placeholders.sort(key=lambda p: p.id)
    return ILTranslator.TranslateInput(
        "{v1}" + base_letter + "<style id='2'>o</style> {v4}" + base_letter
        + "<style id='5'>cable</style> {v7}total " + base_letter + "<style id='8'>i</style>",
        placeholders, base)


def percentage_reference_fixture():
    base = PdfStyle(font_id="TT1", font_size=11)
    placeholders = []
    for number, value in ((1, "•"), (2, "i"), (4, "i"), (6, "> 1"),
                          (7, "o"), (9, "o"), (11, "o"), (13, "o")):
        style = PdfStyle(font_id="TT1", font_size=6.38 if value in ("i", "o") else 11)
        chars = [PdfCharacter(char_unicode=c, pdf_style=style) for c in value]
        placeholders.append(FormulaPlaceholder(number, PdfFormula(pdf_character=chars),
                                               f"{{v{number}}}", rf"\{{v{number}\}}"))
    return ILTranslator.TranslateInput(
        "{v1}If the sum of all L{v2} and C{v4} in each circuit is {v6}% of L{v7} and C{v9}, "
        "there is a mixed circuit with increased ignition hazard. If this is the case, "
        "the output values L{v11} and C{v13} must be reduced by 50 %.", placeholders, base)


PERCENTAGE_REFERENCE_RESPONSE = (
    "{v1}如果每个电路中所有L{v2}和C{v4}的总和为L{v7}和C{v9}的{v6}%，"
    "则存在具有增加点火危险性的混合电路。在这种情况下，输出值L{v11}和C{v13}必须降低50%。")


def variable_measurement_fixture():
    base = PdfStyle(font_id="TT0", font_size=11)
    small = PdfStyle(font_id="TT0", font_size=6.38)
    placeholders = []
    for number, value in ((1, "o "), (2, "o = 30 ")):
        chars = [PdfCharacter(char_unicode=c, pdf_style=small if i == 0 else base) for i, c in enumerate(value)]
        placeholders.append(FormulaPlaceholder(number, PdfFormula(pdf_character=chars), f"{{v{number}}}", rf"\{{v{number}\}}"))
    return ILTranslator.TranslateInput(
        "Assuming it relates to the previously mentioned interconnection to an application in Zone 1 with the necessary ib level of protection, "
        "then the safety factor of 1.5 must be applied. This means that we determine C{v1}not for voltage U{v2}V, but for 45 V.", placeholders, base)


VARIABLE_MEASUREMENT_RESPONSE = (
    "假设其涉及前述与Zone 1中应用程序的互连，且具备必要的ib级保护，则必须应用1.5的安全系数。"
    "这意味着我们不是针对电压U{v2}V确定C{v1}，而是针对45 V进行确定。")


def numbered_affiliation_fixture():
    base = PdfStyle(font_id="F2", font_size=7.9702)
    placeholders = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode="´", pdf_style=base)]),
                                       f"{{v{i}}}", rf"\{{v{i}\}}") for i in range(1, 5)]
    return ILTranslator.TranslateInput(
        "1 Departamento de Cristalografı{v1}a, Instituto de Quı{v2}mica Fı{v3}sica Rocasolano, "
        "Consejo Superior de Investigaciones Cientı{v4}ficas (CSIC), Madrid, Spain, "
        "2 Raytheon Information Solutions, Silver Spring, Maryland, United States of America", placeholders, base)


def accented_word_fixture():
    base = PdfStyle(font_id="F6", font_size=8.9663)
    metric = PdfStyle(font_id="F9", font_size=8.9663)
    subscript = PdfStyle(font_id="F9", font_size=6.2762)
    values = [(1, [PdfCharacter(char_unicode=c, pdf_style=metric if i < 3 else subscript) for i, c in enumerate("AUCMR")]),
              (2, [PdfCharacter(char_unicode="¨", pdf_style=base)]),
              (3, [PdfCharacter(char_unicode="´", pdf_style=base)])]
    placeholders = [FormulaPlaceholder(i, PdfFormula(pdf_character=chars), f"{{v{i}}}", rf"\{{v{i}\}}") for i, chars in values]
    return ILTranslator.TranslateInput(
        "Figure 16 shows the mean {v1}scores for ARACNE, CLR and RegnANN ona synthetic Barabasi network "
        "and on a synthetic Erdo{v2}s-Re{v3}nyi network, 100 nodes and SLC data generation, varying data ratio.", placeholders, base)


ACCENTED_WORD_RESPONSE = (
    "图16展示了ARACNE、CLR和RegnANN在具有100个节点的合成Barabasi网络以及合成Erdo{v2}s-Re{v3}nyi网络上，"
    "针对SLC数据生成且数据比率变化的情况下的平均{v1}得分。")


class TranslationGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.install()

    def setUp(self):
        guard._states.clear()
        guard._counters.clear()

    def test_aliases_restore_only_known_style_ids(self):
        ti = fixture()
        for tag in ("style", "span", "风格", "样式"):
            with self.subTest(tag=tag):
                self.assertEqual(guard.normalize_output(ti, f'流量 {{ v 1 }} <{tag} id="2">速率</{tag}>。'),
                                 "流量 {v1} <style id='2'>速率</style>。")

    def test_independent_variable_and_assigned_measurement_real_response(self):
        ti = variable_measurement_fixture()
        self.assertTrue(guard._safe_variable_measurement_permutation(ti, ti.unicode, VARIABLE_MEASUREMENT_RESPONSE))
        self.assertEqual(guard.normalize_output(ti, VARIABLE_MEASUREMENT_RESPONSE), VARIABLE_MEASUREMENT_RESPONSE)
        parsed = translator().parse_translate_output(ti, VARIABLE_MEASUREMENT_RESPONSE)
        formulas = [c.pdf_formula for c in parsed if c.pdf_formula]
        self.assertIs(formulas[0], ti.placeholders[1].formula)
        self.assertIs(formulas[1], ti.placeholders[0].formula)

    def test_assigned_measurement_does_not_allow_changed_base_unit_or_script(self):
        ti = variable_measurement_fixture()
        for bad in (VARIABLE_MEASUREMENT_RESPONSE.replace("C{v1}", "U{v1}"),
                    VARIABLE_MEASUREMENT_RESPONSE.replace("U{v2}V", "C{v2}V"),
                    VARIABLE_MEASUREMENT_RESPONSE.replace("U{v2}V", "U{v2}A"),
                    VARIABLE_MEASUREMENT_RESPONSE.replace("U{v2}V", "U {v2}V"),
                    VARIABLE_MEASUREMENT_RESPONSE.replace("C{v1}", "C"),
                    VARIABLE_MEASUREMENT_RESPONSE.replace("C{v1}", "C{v1}{v1}")):
            with self.subTest(bad=bad), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, bad)
        # The assignment's base/unit also remain bound when ID order is unchanged.
        for bad in (ti.unicode.replace("U{v2}V", "C{v2}V"), ti.unicode.replace("U{v2}V", "U{v2}A")):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "assigned-variable-pairing"):
                guard.normalize_output(ti, bad)

    def test_assigned_measurement_order_stays_strict_in_math_or_other_sentences(self):
        ti = variable_measurement_fixture()
        for gap in (" + ", " = ", " multiplied by ", " less than ", "。随后计算", " / ", "(x, "):
            bad = VARIABLE_MEASUREMENT_RESPONSE.replace("V确定C", "V" + gap + "C")
            with self.subTest(gap=gap), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
                guard.normalize_output(ti, bad)
        for source in ("C{v1} + U{v2}V", ti.unicode.replace("not for voltage", "plus the voltage"),
                       ti.unicode.replace("C{v1}not for voltage", "f(C{v1}, the voltage")):
            changed = ILTranslator.TranslateInput(source, ti.placeholders, ti.base_style)
            self.assertFalse(guard._safe_variable_measurement_permutation(changed, source, VARIABLE_MEASUREMENT_RESPONSE))
        ti.placeholders[1].formula.pdf_character[0].pdf_style = ti.base_style
        self.assertFalse(guard._safe_variable_measurement_permutation(ti, ti.unicode, VARIABLE_MEASUREMENT_RESPONSE))

    def test_assigned_measurement_reorder_requires_source_relation_not_ratio_or_difference(self):
        for relation in ("ratio of", "difference between"):
            ti = variable_measurement_fixture()
            ti.unicode = ("For the following analysis of the electrical system, we compute the " + relation
                          + " C{v1} to the assigned voltage U{v2}V while keeping all other assumptions unchanged.")
            output = "在以下电气系统分析中，我们计算U{v2}V与所需变量C{v1}的比值，其余假设保持不变。"
            with self.subTest(relation=relation):
                self.assertFalse(guard._safe_variable_measurement_permutation(ti, ti.unicode, output))
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
                    guard.normalize_output(ti, output)
                # Even a fluent valid-clause-shaped output cannot substitute
                # an independent determining relation for a source ratio.
                self.assertFalse(guard._safe_variable_measurement_permutation(ti, ti.unicode, VARIABLE_MEASUREMENT_RESPONSE))

    def test_assigned_measurement_reorder_keeps_not_for_but_for_direction(self):
        ti = variable_measurement_fixture()
        for bad in (
            VARIABLE_MEASUREMENT_RESPONSE.replace("不是针对", "是针对").replace("而是针对", "而不是针对"),
            VARIABLE_MEASUREMENT_RESPONSE.replace("而是针对45 V", "而是针对30 V"),
            VARIABLE_MEASUREMENT_RESPONSE.replace("不是针对电压U{v2}V确定C{v1}", "不是针对C{v1}确定电压U{v2}V"),
            VARIABLE_MEASUREMENT_RESPONSE.replace("V确定C", "V与变量C").replace("，而是针对45 V进行确定", "的比值，而是针对45 V进行确定"),
            VARIABLE_MEASUREMENT_RESPONSE.replace("不是针对电压", "不是针对电压的倒数"),
        ):
            with self.subTest(bad=bad):
                self.assertFalse(guard._safe_variable_measurement_permutation(ti, ti.unicode, bad))
                if guard._FORMULA.findall(bad) != guard._FORMULA.findall(ti.unicode):
                    with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
                        guard.normalize_output(ti, bad)
        for source in (ti.unicode.replace("not for voltage", "for voltage").replace("but for 45 V", "not for 45 V"),
                       ti.unicode.replace("determine", "do not determine"),
                       ti.unicode.replace("but for 45 V", "but for the ratio to 45 V")):
            changed = ILTranslator.TranslateInput(source, ti.placeholders, ti.base_style)
            self.assertFalse(guard._safe_variable_measurement_permutation(changed, source, VARIABLE_MEASUREMENT_RESPONSE))

    def test_numbered_postal_affiliations_preserve_original_accent_compositions(self):
        ti = numbered_affiliation_fixture()
        self.assertTrue(guard._preserve_publication_metadata(ti, "plain text"))
        para = paragraph(ti)
        original = para.pdf_paragraph_composition
        with patch.dict(guard._originals, pre=lambda *args, **kwargs: (ti.unicode, ti)):
            self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker()), (None, None))
        self.assertIs(para.pdf_paragraph_composition, original)
        self.assertEqual(guard.snapshot()["counters"]["preservedMetadataParagraphs"], 1)

    def test_numbered_postal_affiliation_rejects_prose_math_and_heading(self):
        ti = numbered_affiliation_fixture()
        self.assertFalse(guard._preserve_publication_metadata(ti, "title"))
        for source in (ti.unicode + ". We studied the network.", ti.unicode.replace("Madrid", "we measured"),
                       ti.unicode.replace("2 Raytheon", "3 Raytheon"), ti.unicode.replace("United States of America", "Results")):
            changed = ILTranslator.TranslateInput(source, ti.placeholders, ti.base_style)
            self.assertFalse(guard._preserve_publication_metadata(changed, "plain text"))
        for char, size in (("=", 7.9702), ("´", 4.5), ("2", 7.9702)):
            ti.placeholders[0].formula.pdf_character[0] = PdfCharacter(char_unicode=char, pdf_style=PdfStyle(font_id="F2", font_size=size))
            self.assertFalse(guard._preserve_publication_metadata(ti, "plain text"))

    def test_source_accent_word_movement_keeps_spelling_and_original_objects(self):
        ti = accented_word_fixture()
        self.assertEqual([word for _, _, word in guard._accent_word_spans(ti, ti.unicode)], ["Erdo{v2}s-Re{v3}nyi"])
        self.assertEqual(guard.normalize_output(ti, ACCENTED_WORD_RESPONSE), ACCENTED_WORD_RESPONSE)
        parsed = translator().parse_translate_output(ti, ACCENTED_WORD_RESPONSE)
        formulas = [c.pdf_formula for c in parsed if c.pdf_formula]
        for actual, index in zip(formulas, (1, 2, 0)):
            self.assertIs(actual, ti.placeholders[index].formula)

    def test_accent_word_gate_rejects_changed_names_accents_or_real_math_order(self):
        ti = accented_word_fixture()
        for bad in (ACCENTED_WORD_RESPONSE.replace("Erdo", "Ardo"),
                    ACCENTED_WORD_RESPONSE.replace("{v2}s-Re{v3}", "{v3}s-Re{v2}"),
                    ACCENTED_WORD_RESPONSE.replace("Erdo{v2}s-Re{v3}nyi", "Erdos-Renyi"),
                    ACCENTED_WORD_RESPONSE.replace("Erdo", "XErdo"),
                    ACCENTED_WORD_RESPONSE.replace("{v2}", "{v2}{v2}")):
            with self.subTest(bad=bad), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, bad)
        extra = FormulaPlaceholder(4, PdfFormula(pdf_character=[PdfCharacter(char_unicode="x=2")]), "{v4}", r"\{v4\}")
        ti.placeholders.append(extra)
        ti.unicode += " The next mathematical quantity is {v4}."
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
            guard.normalize_output(ti, "数学量{v4}。" + ACCENTED_WORD_RESPONSE)

    def test_accent_word_recovery_uses_whole_word_without_changing_glyphs(self):
        ti = accented_word_fixture()
        instance = translator(["图16显示了合成{PBKEEP_0001}网络的平均{v1}得分，包含100个节点。"])
        recovered, output, _ = guard._whole_paragraph_translation(instance, ti, ParagraphTranslateTracker())
        self.assertIn("Erdo{v2}s-Re{v3}nyi", output)
        self.assertNotIn("Erdo{v2}", instance.calls[0][0])
        self.assertIn("{PBKEEP_0001}", instance.calls[0][0])
        for old, new in zip(ti.placeholders, recovered.placeholders):
            self.assertIs(old.formula, new.formula)

    def test_accent_word_recognizer_excludes_primes_small_scripts_and_short_atoms(self):
        ti = accented_word_fixture()
        for source in ("F{v2}xy", "X{v2}", "Sin{v2}x", "{v2}Erdos", "Erdo{v2}", "Erdo{v2}(x)", "E{v2}xy"):
            self.assertEqual(guard._accent_word_spans(ti, source), [])
        for style in (PdfStyle(font_id="F6", font_size=5), PdfStyle(font_id="F9", font_size=8.9663)):
            ti.placeholders[1].formula.pdf_character[0].pdf_style = style
            self.assertEqual(guard._accent_word_spans(ti, ti.unicode), [])

    def test_unmatched_parenthesis_protection_respects_formula_contained_pairs(self):
        token = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode="(x")]), "{v1}", r"\{v1\}")
        ti = ILTranslator.TranslateInput("The source contains {v1}) and a definition (the number of", [token], PdfStyle())
        spans = guard._unmatched_parenthesis_spans(ti, ti.unicode)
        self.assertEqual([ti.unicode[a:b] for a, b, _ in spans], ["("])
        self.assertEqual(spans[0][0], ti.unicode.rfind("("))
        ti.unicode = "The source contains {v1} and the next expression (y)."
        self.assertEqual(guard._unmatched_parenthesis_spans(ti, ti.unicode), [])

    def test_unmatched_literal_recovery_preserves_boundary_without_accepting_closer(self):
        ti = ILTranslator.TranslateInput("The density is explained (the ratio of the number of", [], PdfStyle())
        instance = translator(["密度的说明{PBKEEP_0001}数量之比，其中数量为"])
        _, output, _ = guard._whole_paragraph_translation(instance, ti, ParagraphTranslateTracker())
        self.assertEqual(output, "密度的说明(数量之比，其中数量为")
        self.assertNotIn("(the ratio", instance.calls[0][0])
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "parenthesis-boundary-changed"):
            guard.normalize_output(ti, output + ")")
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "parenthesis-boundary-changed"):
            guard.normalize_output(ti, "密度的说明(（即数量之比）的变化。")
        self.assertIn("Do not complete the truncated sentence", instance.calls[0][0])
        # A deleted complete pair elsewhere cannot disguise a duplicate here.
        paired = ILTranslator.TranslateInput("Source (note) refers to a density (the ratio of", [], PdfStyle())
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "parenthesis-boundary-changed"):
            guard.normalize_output(paired, "原文注释说明密度(（数量之比）")
        instance = translator(["原文注释说明密度{PBKEEP_0001}（数量之比）"])
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "parenthesis-boundary-changed"):
            guard._whole_paragraph_translation(instance, paired, ParagraphTranslateTracker())

    def test_math_primes_preserve_binding_and_allow_typographic_variants(self):
        ti = prime_variable_fixture()
        output = ti.unicode.replace("C’", "C'")
        self.assertEqual(guard.normalize_output(ti, output), output)
        for bad in (output.replace("C' {v2}", "C {v2}"),
                    output.replace("C' {v2}", "D' {v2}"),
                    output.replace("C' {v2}", "C'' {v2}"),
                    output.replace("C'<style id='3'>", "D'<style id='3'>"),
                    output.replace("C'<style id='3'>", "C<style id='3'>"),
                    output.replace("wire/shield</style>", "wire/shield</style>'", 1),
                    output.replace("wire/wire</style>", "wire/wire'</style>", 1),
                    output.replace("wire/shield", "线/屏蔽", 1)):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "mathematical-prime-variable-changed"):
                guard.normalize_output(ti, bad)

    def test_math_prime_recovery_keeps_source_subscript_styles_and_formula_identity(self):
        ti = prime_variable_fixture()
        instance = translator([
            "{v1}考虑屏蔽层：{PBKEEP_0001} {v2}{PBKEEP_0002} {v5}.5 x {PBKEEP_0003}，"
            "电缆无电位；或 {PBKEEP_0004} {v8}{PBKEEP_0005} {v11}{PBKEEP_0006}，屏蔽层连接电源。"])
        recovered, output, removed = guard._whole_paragraph_translation(instance, ti, ParagraphTranslateTracker())
        self.assertEqual(removed, 0)
        self.assertEqual(len(guard._rich(recovered.placeholders)), 4)
        self.assertEqual(output.count("C’"), 6)
        for before, after in zip(guard._rich(ti.placeholders), guard._rich(recovered.placeholders)):
            self.assertIs(before.composition, after.composition)
        parsed = instance.parse_translate_output(recovered, output)
        self.assertEqual([c.pdf_formula for c in parsed if c.pdf_formula],
                         [p.formula for p in guard._formula(ti.placeholders)])
        prompt = instance.calls[0][0]
        self.assertNotIn("wire/shield", prompt)
        self.assertNotIn("<style", prompt)
        self.assertEqual(len(re.findall(r"\{PBKEEP_\d+\}", prompt)), 6)

    def test_math_prime_gate_ignores_ordinary_apostrophes_and_quoted_words(self):
        for source, output in (
            ("The author's result doesn't depend on O’Neill's method.", "该作者的结果不依赖奥尼尔的方法。"),
            ("The author calls this letter 'C'.", "作者将此字母称为C。"),
            ("The author's variable C’ = 2 doesn't depend on the quoted letter 'C'.", "作者的变量C′ = 2不依赖所引字母C。"),
        ):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle(font_size=11, font_id="TT0"))
            self.assertEqual(guard.normalize_output(ti, output), output)

    def test_math_prime_gate_checks_multiple_primes_without_treating_text_as_math(self):
        ti = ILTranslator.TranslateInput("The derivative f′′ = 2 is constant.", [], PdfStyle())
        self.assertEqual(guard.normalize_output(ti, "导数f'' = 2为常数。"), "导数f'' = 2为常数。")
        for bad in ("导数f' = 2为常数。", "导数g'' = 2为常数。", "导数f''' = 2为常数。"):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "mathematical-prime-variable-changed"):
                guard.normalize_output(ti, bad)

    def test_pure_descriptor_relations_preserve_every_original_composition(self):
        for base_letter in ("L", "C"):
            ti = descriptor_relation_fixture(base_letter)
            self.assertTrue(guard._pure_descriptor_relation(ti))
            para = paragraph(ti)
            original = para.pdf_paragraph_composition
            instance = translator()
            with patch.dict(guard._originals, pre=lambda *args, **kwargs: (ti.unicode, ti)):
                self.assertEqual(instance.pre_translate_paragraph(para, ParagraphTranslateTracker()), (None, None))
            self.assertIs(para.pdf_paragraph_composition, original)
            self.assertEqual(para.unicode, ti.unicode)
            self.assertEqual(instance.calls, [])
        self.assertEqual(guard.snapshot()["counters"]["preservedDescriptorRelations"], 2)

    def test_descriptor_relation_rule_rejects_prose_normal_size_and_unknown_modifiers(self):
        for change in (lambda t: setattr(t, "unicode", "The cable must satisfy " + t.unicode),
                       lambda t: setattr(t, "unicode", t.unicode + " when the nodes are connected."),
                       lambda t: setattr(t, "unicode", t.unicode.replace("total", "average")),
                       lambda t: setattr(t, "unicode", t.unicode.replace("{v7}total ", "")),
                       lambda t: setattr(t.placeholders[3].composition.pdf_character[0].pdf_style, "font_size", 11)):
            ti = descriptor_relation_fixture()
            change(ti)
            self.assertFalse(guard._pure_descriptor_relation(ti))
            self.assertFalse(guard._pure_technical_expression(ti))
        plain = ILTranslator.TranslateInput("Lo > Lcable + total Li", [], PdfStyle(font_size=11))
        self.assertFalse(guard._pure_descriptor_relation(plain))

    def test_contextual_trunk_and_virtuous_cycle_mistranslations_retry(self):
        cases = (("High-Power Trunk Concept", "大功率车体概念", "大功率干线概念", "high-power-trunk-mistranslated"),
                 ("The feedback produces a virtuous cycle.", "反馈产生美德循环。", "反馈产生良性循环。", "virtuous-cycle-mistranslated"))
        for source, bad, good, category in cases:
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            instance = translator([good])
            para = paragraph(ti)
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError, category):
                guard._check_body_translation(instance, para, ti, bad)
            instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, bad)
            self.assertEqual(para.unicode, good)
            self.assertEqual(len(instance.calls), 1)
            self.assertIn("Technical terminology:", instance.calls[0][0])
            self.assertTrue(instance.calls[0][0].endswith(source))

    def test_contextual_term_rules_allow_negation_and_quoted_mistranslation_discussion(self):
        cases = (("High-power trunk concept", "大功率干线不是车体。"),
                 ("High-power trunk concept", "不能译成“车体”，应译为干线。"),
                 ("High-power trunk concept", "“车体”这一译法不正确，应为干线。"),
                 ("The virtuous cycle is useful.", "良性循环不应译为‘美德循环’。"),
                 ("The virtuous cycle is useful.", "“美德循环”不是正确译法，应为良性循环。"),
                 ("The virtuous cycle is useful.", "这里曾错译为“美德循环”，应为良性循环。"),
                 ("The trunk is mounted in the vehicle chassis.", "行李厢装在车体内。"),
                 ("The high-power trunk is mounted in a vehicle chassis.", "大功率行李厢装在车体内。"),
                 ("A literal moral virtue cycle is discussed.", "这里讨论美德循环。"),
                 ("Manchester encoding is line coding that receives the clock signal when encrypting.", "曼彻斯特编码是在加密时接收时钟信号的线路编码。"))
        for source, output in cases:
            with self.subTest(output=output):
                ti = ILTranslator.TranslateInput(source, [], PdfStyle())
                guard._check_body_translation(translator(), paragraph(ti), ti, output)

    def test_recorded_percentage_reference_order_preserves_all_original_formula_objects(self):
        ti = percentage_reference_fixture()
        output = PERCENTAGE_REFERENCE_RESPONSE
        self.assertEqual(guard.normalize_output(ti, output), output)
        instance = translator()
        parsed = instance.parse_translate_output(ti, output)
        by_id = {str(p.id): p.formula for p in ti.placeholders}
        actual = [c.pdf_formula for c in parsed if c.pdf_formula]
        expected = [by_id[number] for number in ("1", "2", "4", "7", "9", "6", "11", "13")]
        self.assertEqual(len(actual), len(expected))
        self.assertTrue(all(a is b for a, b in zip(actual, expected)))
        para = paragraph(ti)
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, output)
        self.assertEqual(para.unicode, output)
        self.assertEqual(instance.calls, [])
        counts = guard.snapshot()["counters"]
        self.assertEqual(counts["percentageReferenceOrderAdjustments"], 1)
        self.assertNotIn("atomicVariableOrderAdjustments", counts)

    def test_percentage_reference_order_rejects_cross_group_or_changed_binding(self):
        ti = percentage_reference_fixture()
        output = PERCENTAGE_REFERENCE_RESPONSE
        for bad in (output.replace("L{v7}和C{v9}", "C{v7}和L{v9}"),
                    output.replace("L{v7}和C{v9}", "C{v9}和L{v7}"),
                    output.replace("L{v7}和C{v9}", "L{v11}和C{v13}").replace("输出值L{v11}和C{v13}", "输出值L{v7}和C{v9}"),
                    output.replace("L{v7}和C{v9}", "L{v7}+C{v9}"),
                    output.replace("的{v6}%", "的{v6}"),
                    output.replace("的{v6}%", "的{v6}{v6}%"),
                    output.replace("的{v6}%", "的1%"),
                    output.replace("的{v6}%", "的{v6}% + D"),
                    output.replace("所有L{v2}和C{v4}", "所有C{v4}和L{v2}")):
            with self.subTest(bad=bad), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, bad)

    def test_percentage_reference_rule_requires_source_prose_threshold_and_real_small_scripts(self):
        for mode in ("arithmetic_threshold", "normal_font", "source_expression", "no_is", "standalone"):
            ti = percentage_reference_fixture()
            if mode == "arithmetic_threshold":
                ti.placeholders[3].formula.pdf_character.append(PdfCharacter(char_unicode="+ 2", pdf_style=ti.base_style))
            elif mode == "normal_font":
                ti.placeholders[4].formula.pdf_character[0].pdf_style.font_size = 11
            elif mode == "source_expression":
                ti.unicode = ti.unicode.replace("C{v9},", "C{v9} + D,")
            elif mode == "no_is":
                ti.unicode = ti.unicode.replace("is {v6}", "multiplies {v6}")
            elif mode == "standalone":
                ti.unicode = "{v1}L{v2} C{v4} {v6}% of L{v7} and C{v9} L{v11} C{v13}"
            with self.subTest(mode=mode), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, PERCENTAGE_REFERENCE_RESPONSE)

    def test_percentage_reference_rule_does_not_relocate_unrelated_styles(self):
        ti = percentage_reference_fixture()
        extra = fixture().placeholders[1]
        extra.id = 20
        extra.left_placeholder = "<style id='20'>"
        ti.placeholders.append(extra)
        ti.unicode += " <style id='20'>Important</style>"
        valid = PERCENTAGE_REFERENCE_RESPONSE + " <style id='20'>重要</style>"
        self.assertEqual(guard.normalize_output(ti, valid), valid)
        bad = "<style id='20'>重要</style> " + PERCENTAGE_REFERENCE_RESPONSE
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard.normalize_output(ti, bad)

    def test_unknown_broken_tags_rejected_even_without_placeholders(self):
        ti = ILTranslator.TranslateInput("Scientific translation.", [], PdfStyle())
        for output in ("正文</place>", "<stylid='27'>正文</styl>", "<st yle id='3'>正文</style>",
                       "正文<span>额外</span>", "<script>内容</script>", "正文{v7}"):
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, output)

    def test_encoded_new_tags_and_formulas_are_rejected(self):
        ti = ILTranslator.TranslateInput("Scientific translation.", [], PdfStyle())
        for output in ('这个结果很有用。&lt;span id="9"&gt;残留&lt;/span&gt;',
                       '＜span id="9"＞残留＜/span＞',
                       '正文&amp;lt;span&amp;gt;残留&amp;lt;/span&amp;gt;',
                       '正文&#60;style id="9"&#62;残留&#60;/style&#62;',
                       '正文｛ｖ９｝', '正文&#123;v9&#125;'):
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, output)

    def test_source_encoded_code_is_preserved_without_unescaping(self):
        for syntax in ('&lt;span&gt;', '＜span＞', '&amp;lt;span&amp;gt;', '&#123;v9&#125;'):
            with self.subTest(syntax=syntax):
                ti = ILTranslator.TranslateInput("Literal code " + syntax, [], PdfStyle())
                output = "字面代码 " + syntax
                self.assertEqual(guard.normalize_output(ti, output), output)
                with self.assertRaises(guard.PlaceholderIntegrityError):
                    guard.normalize_output(ti, output + syntax)

    def test_translated_recovery_instructions_are_rejected(self):
        ti = ILTranslator.TranslateInput("A university department.", [], PdfStyle())
        for output in ("某大学。这是来自科学PDF的一段文本运行。", "某大学。仅翻译此运行。",
                       "某大学。保留所有数字、单位、引用和数学符号。",
                       "某大学。不添加任何标签、占位符、JSON。", "某大学。文本片段保留规则：",
                       "某大学。整段恢复规则：保留所有标记。", "某大学。下面进行整段恢复。",
                       "某大学。保留每个数学撇号及其变量。", "Preserve each mathematical prime and its variable.",
                       "Some university. Whole-paragraph recovery rules: copy every token.",
                       "A university. This is one text run from a scientific PDF."):
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, output)

    def test_source_text_about_translation_instructions_is_not_censored(self):
        ti = ILTranslator.TranslateInput("The instruction says: translate only this run.", [], PdfStyle())
        self.assertEqual(guard.normalize_output(ti, "指令说明：仅翻译此运行。"), "指令说明：仅翻译此运行。")

    def test_missing_duplicate_unknown_and_reordered_formulas_rejected(self):
        ti = fixture()
        second = FormulaPlaceholder(4, PdfFormula(), "{v4}", r"\{\s*v\s*4\s*\}")
        ti.placeholders.append(second)
        ti.unicode += " {v4}"
        for output in ("流量 <style id='2'>速率</style> {v4}",
                       "流量 {v1}{v1} <style id='2'>速率</style>{v4}",
                       "流量 {v1} <style id='2'>速率</style>{v99}",
                       "流量 {v4} <style id='2'>速率</style>{v1}"):
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, output)

    def test_nested_and_unbalanced_style_rejected(self):
        ti = fixture()
        for output in ("流量 {v1}<style id='2'>速率", "流量 {v1}<span id='2'>速率</style>",
                       "流量 {v1}<style id='2'><style id='2'>速率</style></style>",
                       "流量 {v1}<style id='99'>速率</style>"):
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, output)

    def test_formula_inside_relocated_style_is_rejected(self):
        # Upstream regex would consume the formula as style text then delete it.
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard.normalize_output(fixture(), "流量 <style id='2'>{v1}速率</style>")

    def test_literal_source_html_and_placeholder_examples_are_preserved(self):
        ti = fixture()
        ti.unicode += " Example <span>HTML</span> and {v99}."
        ti.original_placeholder_tokens = {"{v99}": 1}
        output = "流量 {v1} <style id='2'>速率</style>。示例 <span>HTML</span> 和 {v99}。"
        instance = translator()
        compositions = instance.parse_translate_output(ti, output)
        text = "".join(c.pdf_same_style_unicode_characters.unicode for c in compositions
                       if c.pdf_same_style_unicode_characters)
        self.assertIn("<span>HTML</span>", text)
        self.assertIn("{v99}", text)
        self.assertIs(next(c.pdf_formula for c in compositions if c.pdf_formula), ti.placeholders[0].formula)

    def test_changed_literal_example_is_rejected(self):
        ti = ILTranslator.TranslateInput("Use <span>literal</span>.", [], PdfStyle())
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard.normalize_output(ti, "使用 <style>字面量</style>。")

    def test_source_formula_collision_is_rejected(self):
        ti = fixture()
        ti.original_placeholder_tokens = {"{v1}": 1}
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard.normalize_output(ti, "流量 {v1}<style id='2'>速率</style>")

    def test_multiline_style_is_restored_and_formula_identity_preserved(self):
        ti = fixture()
        instance = translator()
        result = instance.parse_translate_output(ti, "流量 {v1}<style id='2'>速\n率</style>")
        self.assertEqual([c.pdf_formula for c in result if c.pdf_formula], [ti.placeholders[0].formula])
        styled = [c.pdf_same_style_unicode_characters for c in result if c.pdf_same_style_unicode_characters]
        self.assertIn("速 率", [c.unicode for c in styled])

    def test_retry_validates_before_atomic_commit(self):
        ti = fixture()
        instance = translator(["流量 {v1}<span id=\"2\">速率</span>。"])
        para = paragraph(ti)
        tracker = ParagraphTranslateTracker()
        instance.post_translate_paragraph(para, tracker, ti, "流量 {v99}<style id='2'>速率</style>。")
        self.assertEqual(len(instance.calls), 1)
        self.assertTrue(instance.calls[0][0].endswith(ti.unicode))
        self.assertLess(instance.calls[0][0].index("Placeholder repair rules"), instance.calls[0][0].index("Translate: "))
        self.assertIn("<style id='2'>", para.unicode)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)
        self.assertEqual(guard.snapshot()["counters"]["repairedParagraphs"], 1)

    def test_actual_babeldoc_prompt_keeps_source_last_for_both_recovery_paths(self):
        ti = fixture()
        instance = translator(["流量 {v1}<style id='2'>速率</style>。", "流量 {v1} 速率。"])
        instance._cached_glossaries = []
        instance.translation_config.add_formula_placehold_hint = False
        instance.generate_prompt_for_llm = ILTranslator.generate_prompt_for_llm.__get__(instance, ILTranslator)
        tracker = ParagraphTranslateTracker()
        guard._retry_translation(instance, ti, tracker, 0)
        guard._whole_paragraph_translation(instance, ti, tracker)
        for call, expected_source in zip(instance.calls, (ti.unicode, "Flow {v1} rate.")):
            prompt = call[0]
            self.assertTrue(prompt.endswith("Now translate the following text:\n\n" + expected_source))
            self.assertEqual(prompt.count("Now translate the following text:"), 1)
            self.assertLess(prompt.index("rules:"), prompt.index("Now translate the following text:"))

    def test_whole_paragraph_recovery_preserves_formulas_without_word_fragments(self):
        ti = fixture()
        instance = translator(["错误{v8}", "错误{v9}", "流量 {v1} 速率。"])
        para = paragraph(ti)
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "错误{v7}")
        self.assertEqual(para.unicode, "流量 {v1} 速率。")
        self.assertEqual(len(instance.calls), 3)
        self.assertTrue(instance.calls[2][0].endswith("Flow {v1} rate."))
        self.assertLess(instance.calls[2][0].index("Whole-paragraph recovery rules"), instance.calls[2][0].index("Translate: "))
        self.assertEqual(guard.snapshot()["counters"]["richTextFallbackParagraphs"], 1)
        self.assertIs(next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula), ti.placeholders[0].formula)

    def test_failed_recovery_keeps_original_and_pending_gate(self):
        ti = fixture()
        instance = translator([RuntimeError("secret not to be reported"), RuntimeError("secret")])
        para = paragraph(ti)
        original = para.pdf_paragraph_composition
        with self.assertRaises(guard.PlaceholderIntegrityError):
            instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "错误{v7}")
        self.assertEqual(para.unicode, ti.unicode)
        self.assertIs(para.pdf_paragraph_composition, original)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 1)
        self.assertNotIn("secret", json.dumps(guard.snapshot()))
        rejected = guard.snapshot()["unresolvedParagraphs"][0]
        self.assertEqual(rejected["sourceParagraphSHA256"], hashlib.sha256(ti.unicode.encode()).hexdigest())
        self.assertEqual(rejected["layoutLabel"], "text")
        self.assertNotIn(ti.unicode, json.dumps(guard.snapshot()))

    def test_batch_failure_cleared_only_after_successful_single_fallback(self):
        ti = fixture()
        instance = translator([RuntimeError("failed"), RuntimeError("failed")])
        para = paragraph(ti)
        tracker = ParagraphTranslateTracker()
        with self.assertRaises(guard.PlaceholderIntegrityError):
            instance.post_translate_paragraph(para, tracker, ti, "错误{v7}")
        instance.post_translate_paragraph(para, tracker, ti, "流量 {v1}<style id='2'>速率</style>。")
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)

    def test_pre_tracks_api_failure_even_if_post_is_never_called(self):
        ti = fixture()
        instance = translator()
        para = paragraph(ti)
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            instance.pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {})
        self.assertEqual(guard.snapshot()["unresolvedCount"], 1)
        with tempfile.TemporaryDirectory() as directory:
            guard.write_report(Path(directory) / "quality.json")
            report = json.loads((Path(directory) / "quality.json").read_text())
            self.assertTrue(report["guardInstalled"])
            self.assertEqual(report["preparedParagraphs"], 1)
            self.assertFalse(report["rawDocumentTextStored"])

    def test_skipped_pre_is_not_counted(self):
        with patch.dict(guard._originals, pre=lambda *a, **k: (None, None)):
            translator().pre_translate_paragraph(paragraph(fixture()), ParagraphTranslateTracker(), {}, {})
        self.assertEqual(guard.snapshot()["preparedParagraphs"], 0)

    def test_pre_exception_is_pending_and_later_skip_cannot_hide_it(self):
        instance = translator()
        para = paragraph(fixture())
        def fail(*args, **kwargs):
            raise RuntimeError("private source text")
        with patch.dict(guard._originals, pre=fail), self.assertRaises(RuntimeError):
            instance.pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {})
        with patch.dict(guard._originals, pre=lambda *a, **k: (None, None)):
            instance.pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {})
        report = guard.snapshot()
        self.assertEqual(report["unresolvedCount"], 1)
        self.assertEqual(report["unresolvedReasons"], {"paragraph-preparation-failed": 1})
        self.assertNotIn("private", json.dumps(report))

    def test_verbatim_long_body_does_not_pass_as_translated(self):
        source = "The scientific method requires independent evidence and careful experimental verification."
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        instance = translator([source, source, source])
        with self.assertRaises(guard.PlaceholderIntegrityError):
            instance.post_translate_paragraph(paragraph(ti), ParagraphTranslateTracker(), ti, source)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 1)

    def test_verbatim_natural_language_title_is_rejected_but_name_is_allowed(self):
        for source in ("Advancing AI for Science", "Results and Discussion"):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            para = paragraph(ti)
            para.layout_label = "title"
            instance = translator([source, source, source])
            with self.subTest(source=source), self.assertRaises(guard.PlaceholderIntegrityError):
                instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, source)
        name_ti = ILTranslator.TranslateInput("Wenhui Zhang", [], PdfStyle())
        name = paragraph(name_ti)
        name.layout_label = "title"
        translator().post_translate_paragraph(name, ParagraphTranslateTracker(), name_ti, name_ti.unicode)
        self.assertEqual(name.unicode, "Wenhui Zhang")

    def test_english_paraphrase_or_style_changes_cannot_hide_untranslated_prose(self):
        source = "In the long run archipelagic research may obscure the broader picture."
        output = "Over time this research can obscure the larger picture."
        for label in ("plain", "plain text", "text", "paragraph", "list", "list_item", "unknown"):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            para = paragraph(ti)
            para.layout_label = label
            with self.subTest(label=label), self.assertRaises(guard.PlaceholderIntegrityError):
                translator([output, output, output]).post_translate_paragraph(para, ParagraphTranslateTracker(), ti, output)
        ti = fixture()
        ti.unicode = source + " {v1}<style id='2'>rate</style>"
        para = paragraph(ti)
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard._check_body_translation(translator(), para, ti, source + ' {v1}<style id="2">rate</style>')
        reference = paragraph(ILTranslator.TranslateInput(source, [], PdfStyle()))
        reference.layout_label = "reference"
        translator().post_translate_paragraph(reference, ParagraphTranslateTracker(),
                                             ILTranslator.TranslateInput(source, [], PdfStyle()), source)
        self.assertEqual(reference.unicode, source)

    def test_fixed_academic_labels_do_not_expand_into_article_summary(self):
        for source, expected in (("ARTICLE INFO", "文章信息"), ("ABSTRACT", "摘要"), ("Keywords:", "关键词：")):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            para = paragraph(ti)
            instance = translator()
            instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "科学研究中的人工智能概")
            self.assertEqual(para.unicode, expected)
            self.assertEqual(instance.calls, [])

    def test_safety_classifications_cannot_be_translated_or_swapped(self):
        for source, output in (("Use Zone 1/Division 2 applications.", "用于1区/2区应用。"),
                               ("Class I, Div. 1", "I类1类"),
                               ("Zone 0 and 1/Division 1 and 2", "Zone 0 and 1/Division 2 and 1"),
                               ("Zone 2, Div. 2", "Zone 2, Div. 1"),
                               ("Division 2", "Division 2 Division 2"),
                               ("Zone 1/Division 2", "Zone 1, Division 2")):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            with self.subTest(source=source), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, output)
        ti = ILTranslator.TranslateInput("Use Zone\u00a01/Division\u00a02 applications.", [], PdfStyle())
        self.assertEqual(guard.normalize_output(ti, "用于 Zone 1/Division 2 应用。"), "用于 Zone 1/Division 2 应用。")

    def test_classification_recovery_preserves_crossing_style_and_formula_objects(self):
        ti = fixture()
        ti.placeholders[0].formula.pdf_character = [PdfCharacter(char_unicode="1")]
        # Style boundaries and a formula glyph may interrupt the literal. Copy
        # its original source range, rather than joining or rewriting tokens.
        ti.unicode = "Use <style id='2'>Zone</style> {v1}/Division 2 applications."
        instance = translator(["用于 {PBKEEP_0001} 应用。"])
        plain, output, removed = guard._whole_paragraph_translation(instance, ti, ParagraphTranslateTracker())
        self.assertEqual(output, "用于 Zone {v1}/Division 2 应用。")
        self.assertEqual(len(instance.calls), 1)
        self.assertEqual(removed, 1)
        self.assertTrue(instance.calls[0][0].endswith("Use {PBKEEP_0001} applications."))
        compositions = instance.parse_translate_output(plain, output)
        self.assertIs(next(c.pdf_formula for c in compositions if c.pdf_formula), ti.placeholders[0].formula)

    def test_unrelated_class_word_and_mathematics_are_not_safety_identifiers(self):
        ti = ILTranslator.TranslateInput("A class of methods uses x < 2 and division by 3.", [], PdfStyle())
        self.assertEqual(guard.normalize_output(ti, "这类方法采用 x < 2 以及除以 3 的运算。"),
                         "这类方法采用 x < 2 以及除以 3 的运算。")

    def test_whitespace_style_only_is_removed_before_first_request(self):
        ti = fixture()
        ti.placeholders[1].composition.pdf_character = [PdfCharacter(char_unicode=" ")]
        ti.unicode = "Advancing<style id='2'> </style>AI for science {v1}"
        original_formula = ti.placeholders[0]
        para = paragraph(ti)
        original_compositions = para.pdf_paragraph_composition
        tracker = ParagraphTranslateTracker()
        instance = translator()
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            text, prepared = instance.pre_translate_paragraph(para, tracker, {}, {})
        self.assertEqual(text, "Advancing AI for science {v1}")
        self.assertEqual(prepared.placeholders, [original_formula])
        self.assertIs(prepared.base_style, ti.base_style)
        self.assertIs(para.pdf_paragraph_composition, original_compositions)
        self.assertEqual(guard.snapshot()["counters"]["whitespaceStyleWrappersRemoved"], 1)
        self.assertEqual(instance.calls, [])
        # Actual emphasized text is not removed by this normalization.
        original = fixture()
        normalized, removed = guard._without_rich_wrappers(original, whitespace_only=True)
        self.assertEqual(normalized.unicode, original.unicode)
        self.assertEqual(removed, 0)

    def test_publication_dates_and_author_lists_keep_original_compositions(self):
        for source in ("Received 9 May 2025; Received in revised form 18 November 2025; Accepted 20 November 2025",
                       "Available online 2 December 2025"):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            para = paragraph(ti)
            compositions = para.pdf_paragraph_composition
            with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
                self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
            self.assertIs(para.pdf_paragraph_composition, compositions)
        marker = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode="∗")]), "{v1}", r"\{v1\}")
        authors = ILTranslator.TranslateInput("Alice Smith a,b,{v1}, Bob Brown a,b, Carol Jones a", [marker], PdfStyle())
        self.assertTrue(guard._preserve_publication_metadata(authors))
        self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput("Received 9 May 2025 is the date of the experiment and we discuss its implications.", [], PdfStyle())))
        self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput("Neural Networks a,b, Deep Learning a,b, and our Methods {v1}", [marker], PdfStyle())))

    def test_opaque_classification_tokens_cannot_be_dropped_or_duplicated(self):
        ti = ILTranslator.TranslateInput("Use Zone 1/Division 2 applications.", [], PdfStyle())
        for output in ("用于1区/2区应用。", "用于 {PBKEEP_0001} {PBKEEP_0001} 应用。", "用于 {PBKEEP_0002} 应用。"):
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard._whole_paragraph_translation(translator([output]), ti, ParagraphTranslateTracker())

    def test_marked_affiliation_addresses_keep_original_compositions(self):
        rich = fixture().placeholders[1]
        rich.composition.pdf_character = [PdfCharacter(char_unicode="a"), PdfCharacter(char_unicode=" ")]
        formula = FormulaPlaceholder(3, PdfFormula(pdf_character=[PdfCharacter(char_unicode="b")]), "{v3}", r"\{v3\}")
        ti = ILTranslator.TranslateInput(
            "<style id='2'>a </style>Artificial Intelligence Laboratory, London, UK "
            "{v3}University of Example, Paris, France", [rich, formula], PdfStyle())
        para = paragraph(ti)
        compositions = para.pdf_paragraph_composition
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
        self.assertIs(para.pdf_paragraph_composition, compositions)
        self.assertEqual(guard.snapshot()["counters"]["preservedMetadataParagraphs"], 1)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)
        for source in (
            "Corresponding author at: " + ti.unicode,
            ti.unicode + ", we describe our results here.",
            ti.unicode + " {v9}",
            ti.unicode.replace("Laboratory, London, UK", "Laboratory, London, UK, finds strong results"),
            ti.unicode.replace("<style id='2'>a </style>", "a ").replace("{v3}", "b "),
        ):
            with self.subTest(source=source):
                self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput(source, ti.placeholders, ti.base_style)))

    def test_numbered_url_and_email_footnotes_keep_original_compositions(self):
        marker = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode="4")]), "{v1}", r"\{v1\}")
        for source in (
            "{v1} https://enamine.net/compound-libraries/ai-enabled-libraries",
            "{v1} https://example.org/the/from/learning/artificial/intelligence/research/and/methods",
            "{v1} the.from.learning.artificial.intelligence.methods@example.org",
        ):
            with self.subTest(source=source):
                ti = ILTranslator.TranslateInput(source, [marker], PdfStyle())
                para = paragraph(ti)
                para.layout_label = "abandon"
                compositions = para.pdf_paragraph_composition
                instance = translator()
                with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
                    self.assertEqual(instance.pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
                self.assertIs(para.pdf_paragraph_composition, compositions)
                self.assertEqual(instance.calls, [])
                guard._check_body_translation(instance, para, ti, source)

    def test_mixed_prose_and_links_still_require_chinese_prose(self):
        link = "https://example.org/the/from/learning/artificial/intelligence/research"
        source = "The method improves the accuracy of scientific experiments and evaluates all results. " + link
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        para = paragraph(ti)
        self.assertFalse(guard._preserve_publication_metadata(ti))
        for output in (source, source + "/中文", "The method improves accuracy. " + link):
            with self.subTest(output=output), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "untranslated-prose-paragraph"):
                guard._check_body_translation(translator(), para, ti, output)
        guard._check_body_translation(translator(), para, ti, "该方法提高科学实验准确度并评估所有结果。 " + link)
        # A title must be judged by its words, not the English words in its URL.
        title = ILTranslator.TranslateInput("Scientific methods " + link, [], PdfStyle())
        para.layout_label = "title"
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "untranslated-prose-paragraph"):
            guard._check_body_translation(translator(), para, title, title.unicode)
        self.assertFalse(guard._preserve_publication_metadata(title))

    def test_classification_literals_do_not_count_as_english_prose(self):
        source = "Zone 1/Division 2, Zone 2/Division 1; Class I, Div. 1; Class II, Div. 2"
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        self.assertEqual(re.findall(r"[A-Za-z]{2,}", guard._prose_text(ti, source)), [])
        guard._check_body_translation(translator(), paragraph(ti), ti, source)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "protected-classification-changed"):
            guard.normalize_output(ti, source.replace("Division 2", "Division 3", 1))

    def test_mixed_chinese_ordinary_english_fragments_must_retry(self):
        for source, bad, good in (
            ("In all other cases, the original values can be used in the calculations.",
             "In 其他所有情况下，原始值可用于计算。", "在其他所有情况下，原始值可用于计算。"),
            ("Multiple grounding is permissible if the circuit is divided into galvanically isolated subcircuits.",
             "若电路分为galvanically隔离的子电路，则允许多点接地。", "若电路分为电气隔离的子电路，则允许多点接地。"),
            ("FISCO is almost exclusively used in Europe; Entity is mainly used in North America.",
             "FISCO几乎exclusively用于欧洲；Entity主要用于北美。", "FISCO几乎仅用于欧洲；Entity主要用于北美。"),
            ("The output must be connected to the power supply with care.",
             "输出必须connected to the power supply，小心操作。", "输出必须小心地连接到电源。"),
        ):
            with self.subTest(source=source):
                ti = ILTranslator.TranslateInput(source, [], PdfStyle())
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "untranslated-prose-fragment"):
                    guard._check_body_translation(translator(), paragraph(ti), ti, bad)
                guard._check_body_translation(translator(), paragraph(ti), ti, good)

    def test_source_network_rewiring_residue_retries_whole_paragraph(self):
        ti = fixture()
        ti.unicode = "The adaptive network uses a rewiring mechanism <style id='2'>and rewiring</style> changes {v1}."
        good = "自适应网络采用重连机制<style id='2'>，重连</style>改变{v1}。"
        bad = "自适应网络采用re wiring机制<style id='2'>，rewiring</style>改变{v1}。"
        instance = translator([good])
        para = paragraph(ti)
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, bad)
        self.assertEqual(para.unicode, good)
        self.assertEqual(len(instance.calls), 1)
        self.assertIn("rewiring is the ordinary network operation 重连", instance.calls[0][0])
        self.assertIs(next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula), ti.placeholders[0].formula)
        for residual in ("rewiring", "re wiring", "re\nwiring", "re-wiring"):
            with self.subTest(residual=residual), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "untranslated-prose-fragment"):
                guard._check_body_translation(instance, para, ti, "网络采用" + residual + "机制。")

    def test_network_rewiring_gate_keeps_source_identifiers_and_nonprose(self):
        opaque = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in "rewiring"]), "{v1}", r"\{v1\}")
        for source, output, placeholders in (
            ("Rewiring Networks developed this method.", "Rewiring Networks开发了此方法。", []),
            ("The network uses the formula {v1}.", "该网络使用公式{v1}。", [opaque]),
            ("The network data are at https://example.org/rewiring.", "网络数据位于https://example.org/rewiring。", []),
            ("The network uses a rewiring_id identifier.", "网络使用rewiring_id标识符。", []),
        ):
            ti = ILTranslator.TranslateInput(source, placeholders, PdfStyle())
            self.assertFalse(guard._network_rewiring_prose(ti))
            guard._check_body_translation(translator(), paragraph(ti), ti, output)
        ti = ILTranslator.TranslateInput("The network uses a rewiring mechanism.", [], PdfStyle())
        for label in ("code", "formula", "reference"):
            para = paragraph(ti)
            para.layout_label = label
            guard._check_body_translation(translator(), para, ti, "网络使用rewiring机制。")
        instance = translator()
        instance.translation_config.lang_out = "de"
        guard._check_body_translation(instance, paragraph(ti), ti, "网络使用rewiring机制。")

    def test_real_short_connectors_bypass_only_length_gate_and_translate(self):
        for source, expected in (("with", "其中"), ("and", "且")):
            ti, para = short_connector_fixture(source)
            instance = translator()
            instance.support_llm_translate = True
            instance.translation_config.disable_rich_text_translate = False
            instance.translation_config.min_text_length = 5
            original_config = instance.translation_config
            tracker = ParagraphTranslateTracker()
            self.assertEqual(guard._originals["pre"](instance, para, tracker, {}, {}), (None, None))
            text, prepared = instance.pre_translate_paragraph(para, tracker, {}, {})
            self.assertEqual(text, source)
            self.assertEqual(prepared.placeholders, [])
            instance.post_translate_paragraph(para, tracker, prepared, source)
            self.assertEqual(para.unicode, expected)
            self.assertIs(instance.translation_config, original_config)
            self.assertEqual(instance.translation_config.min_text_length, 5)
            self.assertIs(para.pdf_paragraph_composition[0].pdf_same_style_unicode_characters.pdf_style, ti.base_style)
            self.assertEqual(instance.calls, [])
        self.assertEqual(guard.snapshot()["counters"]["shortProseConnectorsPrepared"], 2)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)

    def test_short_connector_exception_excludes_names_code_formulas_and_scripts(self):
        for source in ("With", "AND", "And", "with:", "and/or", "with x", "In", "at"):
            ti, para = short_connector_fixture(source)
            self.assertIsNone(guard._short_prose_connector(para))
            self.assertIsNone(guard._fixed_label_translation(translator(), ti, para))
        for label in ("title", "code", "formula", "isolate_formula", "fallback_line", "reference"):
            ti, para = short_connector_fixture()
            para.layout_label = label
            self.assertIsNone(guard._short_prose_connector(para))
        ti, para = short_connector_fixture()
        chars = para.pdf_paragraph_composition[0].pdf_line.pdf_character
        para.pdf_paragraph_composition = [PdfParagraphComposition(pdf_formula=PdfFormula(pdf_character=chars))]
        self.assertIsNone(guard._short_prose_connector(para))
        for mutation in ("script", "baseline", "formula_layout", "vertical", "mismatched_text", "multiple_lines"):
            ti, para = short_connector_fixture()
            chars = para.pdf_paragraph_composition[0].pdf_line.pdf_character
            if mutation == "script":
                chars[0].pdf_style = PdfStyle(font_id="F5", font_size=5)
            elif mutation == "baseline":
                chars[0].box.y += 3
            elif mutation == "formula_layout":
                chars[0].formula_layout_id = 1
            elif mutation == "vertical":
                para.vertical = True
            elif mutation == "mismatched_text":
                para.unicode = "and"
            elif mutation == "multiple_lines":
                para.pdf_paragraph_composition *= 2
            with self.subTest(mutation=mutation):
                self.assertIsNone(guard._short_prose_connector(para))
        ti, para = short_connector_fixture()
        instance = translator()
        instance.translation_config.lang_out = "fr"
        self.assertIsNone(guard._fixed_label_translation(instance, ti, para))
        with patch.dict(guard._originals, pre=lambda *a, **k: (None, None)):
            self.assertEqual(instance.pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
        formula_ti = ILTranslator.TranslateInput("with {v1}", fixture().placeholders[:1], ti.base_style)
        self.assertIsNone(guard._fixed_label_translation(translator(), formula_ti, para))

    def test_short_connectors_are_translated_before_real_batch_page_filter(self):
        from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
        from babeldoc.format.pdf.document_il.midend.il_translator import DocumentTranslateTracker
        connectors = [short_connector_fixture(word)[1] for word in ("with", "and")]
        # Full-document formula/style parsing changes the second source line to
        # same-style original glyphs before the production batch length filter.
        run = connectors[1].pdf_paragraph_composition[0].pdf_line
        connectors[1].pdf_paragraph_composition = [PdfParagraphComposition(
            pdf_same_style_characters=PdfSameStyleCharacters(pdf_style=connectors[1].pdf_style,
                                                            pdf_character=run.pdf_character))]
        formula = PdfFormula(pdf_character=[PdfCharacter(char_unicode="x"), PdfCharacter(char_unicode="=")])
        equation = SimpleNamespace(unicode="x=", layout_label="formula", vertical=False, debug_id="equation",
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=formula)])
        source_body = short_connector_fixture("The original network text remains pending.")[1]
        page = SimpleNamespace(pdf_paragraph=connectors + [equation, source_body], pdf_font=[], pdf_xobject=[])
        docs = SimpleNamespace(page=[page])
        engine = translator()
        engine.support_llm_translate = True
        engine.translation_config.min_text_length = 5
        engine.translation_config.disable_rich_text_translate = False
        engine.translation_config.raise_if_cancelled = lambda: None
        context = SimpleNamespace(first_paragraph=None, recent_title_paragraph=None)
        engine.translation_config.shared_context_cross_split_part = context
        batch = object.__new__(ILTranslatorLLMOnly)
        batch.il_translator = engine
        batch.translation_config = engine.translation_config
        batch.shared_context_cross_split_part = context
        batch.mid = 0
        batch.calc_token_count = len
        submissions = []
        executor = SimpleNamespace(submit=lambda function, *args, **kwargs: submissions.append(args[0]))
        def upstream(self, document):
            self.process_page(page, executor, SimpleNamespace(advance=lambda *args: None),
                              DocumentTranslateTracker().new_page(), executor, set())
        with patch.dict(guard._originals, batch_translate=upstream):
            batch.translate(docs)
        self.assertEqual([p.unicode for p in connectors], ["其中", "且"])
        self.assertEqual([[p.unicode for p in item.paragraphs] for item in submissions], [[source_body.unicode]])
        self.assertIs(equation.pdf_paragraph_composition[0].pdf_formula, formula)
        self.assertEqual(guard.snapshot()["counters"]["shortProseConnectorsBeforeBatch"], 2)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)
        self.assertEqual(engine.calls, [])
        self.assertEqual(engine.translation_config.min_text_length, 5)

    def test_batch_short_connector_preprocessing_respects_other_targets(self):
        from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
        _, para = short_connector_fixture()
        batch = object.__new__(ILTranslatorLLMOnly)
        batch.translation_config = SimpleNamespace(lang_out="fr")
        docs = SimpleNamespace(page=[SimpleNamespace(pdf_paragraph=[para])])
        observed = []
        with patch.dict(guard._originals, batch_translate=lambda self, value: observed.append(value)):
            batch.translate(docs)
        self.assertEqual(para.unicode, "with")
        self.assertEqual(observed, [docs])
        self.assertEqual(guard.snapshot()["preparedParagraphs"], 0)

    def test_complete_journal_footers_preserve_source_date_roles_and_formula_objects(self):
        values = ((1, " 2014 | "), (2, "9 | "), (3, "4 | "))
        placeholders = [FormulaPlaceholder(index, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in text]),
                                           f"{{v{index}}}", rf"\{{v{index}\}}") for index, text in values]
        ti = ILTranslator.TranslateInput("April{v1}Volume {v2}Issue {v3}e93090", placeholders, PdfStyle())
        para = paragraph(ti)
        para.layout_label = "abandon"
        original = para.pdf_paragraph_composition
        self.assertTrue(guard._preserve_publication_metadata(ti, para.layout_label))
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
        self.assertIs(para.pdf_paragraph_composition, original)
        self.assertEqual([p.formula.pdf_character[0].char_unicode for p in placeholders], [" ", "9", "4"])
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)
        for source in ("April 2014 | Volume 9 | Issue 4 | e93090", "October2026|Volume12|Issue10|e1001111"):
            self.assertTrue(guard._preserve_publication_metadata(ILTranslator.TranslateInput(source, [], PdfStyle())))

    def test_journal_footer_preservation_requires_complete_metadata_shape(self):
        footer = "April 2014 | Volume 9 | Issue 4 | e93090"
        for source in ("We read " + footer, footer + " and discuss the network.", footer.replace("April", "Results"),
                       footer.replace("Volume 9 | Issue 4", "Issue 4 | Volume 9"), footer.replace("e93090", "RegnANN"),
                       "April 2014 describes Volume 9 and Issue 4 in this article."):
            with self.subTest(source=source):
                self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput(source, [], PdfStyle())))

    def test_initial_transport_cutoff_stays_pending_until_complete_retry(self):
        ti = fixture()
        good = "流量 {v1}<style id='2'>速率</style>。"
        instance = translator([ValueError("translation-response-incomplete"), good])
        instance.support_llm_translate = True
        instance.use_as_fallback = False
        instance.translation_config.raise_if_cancelled = lambda: None
        instance.generate_prompt_for_llm = lambda *args, **kwargs: "Translate: " + args[0]
        para = paragraph(ti)
        para.debug_id = "transport-cutoff"
        original = para.pdf_paragraph_composition
        with patch.dict(guard._originals, pre=lambda *args, **kwargs: (ti.unicode, ti)):
            with self.assertLogs("babeldoc.format.pdf.document_il.midend.il_translator", level="ERROR"):
                instance.translate_paragraph(para, SimpleNamespace(), pbar=SimpleNamespace(advance=lambda: None), tracker=ParagraphTranslateTracker(), page_font_map={}, xobj_font_map={})
            self.assertEqual(len(instance.calls), 1)
            self.assertEqual(para.unicode, ti.unicode)
            self.assertIs(para.pdf_paragraph_composition, original)
            self.assertEqual(guard.snapshot()["unresolvedCount"], 1)
            instance.translate_paragraph(para, SimpleNamespace(), pbar=SimpleNamespace(advance=lambda: None), tracker=ParagraphTranslateTracker(), page_font_map={}, xobj_font_map={})
        self.assertEqual(para.unicode, good)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)
        self.assertIs(next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula), ti.placeholders[0].formula)

    def test_recovery_transport_cutoff_restores_source_and_fails_closed(self):
        ti = fixture()
        instance = translator([ValueError("translation-response-incomplete"), ValueError("translation-response-incomplete")])
        para = paragraph(ti)
        original = para.pdf_paragraph_composition
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "unresolved-paragraph"):
            instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "错误{v9}")
        self.assertEqual(para.unicode, ti.unicode)
        self.assertIs(para.pdf_paragraph_composition, original)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 1)
        self.assertIn("translation-request-failed", guard.snapshot()["unresolvedReasons"])
        self.assertEqual(len(instance.calls), 2)

    def test_fragment_check_excludes_identifiers_formulas_names_and_metadata(self):
        formula = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in "galvanically"]), "{v1}", r"\{v1\}")
        cases = (
            ("The element In is used with the University of Cambridge instrument.",
             "使用元素In和University of Cambridge的仪器。", []),
            ("The protocol PROFIBUS MBP-IS connects FOUNDATION Fieldbus H1 devices in Zone 1/Division 2.",
             "协议PROFIBUS MBP-IS连接Zone 1/Division 2中的FOUNDATION Fieldbus H1设备。", []),
            ("The source formula {v1} remains opaque and must be retained.",
             "源公式{v1}保持不透明并必须保留。", [formula]),
            ("The instrument's model is EXCLUSIVELY and its measured value is 4 mA.",
             "仪器型号是EXCLUSIVELY，测量值为4 mA。", []),
            ("Use https://example.org/exclusively and the method from the site.",
             "使用https://example.org/exclusively以及该站点的方法。", []),
        )
        for source, output, placeholders in cases:
            with self.subTest(source=source):
                ti = ILTranslator.TranslateInput(source, placeholders, PdfStyle())
                guard._check_body_translation(translator(), paragraph(ti), ti, output)
        ti = ILTranslator.TranslateInput("This research was exclusively performed by the authors.", [], PdfStyle())
        para = paragraph(ti)
        para.layout_label = "reference"
        guard._check_body_translation(translator(), para, ti, "这项研究 exclusively performed by the authors.")
        non_chinese = translator()
        non_chinese.translation_config.lang_out = "de"
        guard._check_body_translation(non_chinese, paragraph(ti), ti, "该研究exclusively由作者完成。")

    def test_english_fragment_retry_preserves_formula_object(self):
        ti = fixture()
        ti.unicode = "The circuits are galvanically isolated <style id='2'>and use</style> {v1}."
        bad = "电路是galvanically隔离的<style id='2'>并使用</style>{v1}。"
        good = "电路采用电气隔离<style id='2'>并使用</style>{v1}。"
        engine = translator([good])
        para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, bad)
        self.assertEqual(para.unicode, good)
        self.assertEqual(len(engine.calls), 1)
        self.assertIn("galvanically isolated means 电气隔离", engine.calls[0][0])
        self.assertTrue(engine.calls[0][0].endswith(ti.unicode))
        self.assertIs(next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula), ti.placeholders[0].formula)

    def test_numbered_figure_and_table_references_translate_without_touching_names(self):
        ti = ILTranslator.TranslateInput("Figure 9. The resulting circuit values are shown in Table 7.", [], PdfStyle())
        for bad in ("Figure 9. 所得电路值见表7。", "图9。所得电路值见Table 7。", "图9。resulting电路值见表7。"):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "untranslated-prose-fragment"):
                guard._check_body_translation(translator(), paragraph(ti), ti, bad)
        guard._check_body_translation(translator(), paragraph(ti), ti, "图9。所得电路值见表7。")
        named = ILTranslator.TranslateInput("Figure Eight provides annotations for Table Mountain research.", [], PdfStyle())
        guard._check_body_translation(translator(), paragraph(named), named, "Figure Eight为Table Mountain研究提供标注。")

    def test_common_short_prose_headings_require_translation_but_names_do_not(self):
        for text in ("Assumptions", "Parameters", "Installation Requirements", "Grounding", "Contents"):
            with self.subTest(text=text):
                ti = ILTranslator.TranslateInput(text, [], PdfStyle())
                para = paragraph(ti)
                para.layout_label = "title"
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "untranslated-prose-paragraph"):
                    guard._check_body_translation(translator(), para, ti, text)
        for text in ("FISCO", "Entity", "RegnANN", "PROFIBUS MBP"):
            ti = ILTranslator.TranslateInput(text, [], PdfStyle())
            para = paragraph(ti)
            para.layout_label = "title"
            guard._check_body_translation(translator(), para, ti, text)

    def test_prose_counts_and_decimals_cannot_be_omitted_or_changed(self):
        ti = ILTranslator.TranslateInput("KNN was trained for 10000 epochs during 1975–94 with an error of 0.22.", [], PdfStyle())
        good = "KNN在1975–94年间训练10000轮，误差为0.22。"
        guard._check_body_translation(translator(), paragraph(ti), ti, good)
        for bad in (good.replace("10000轮", ""), good.replace("0.22", "0.23"), good+"10000", good.replace("10000", "一万")):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "prose-numeric-literal-changed"):
                guard._check_body_translation(translator(), paragraph(ti), ti, bad)
        self.assertIn("10000 (1 occurrence(s))", guard._contextual_term_hint(translator(), ti))

    def test_prose_number_check_ignores_opaque_objects_and_keeps_single_digit_words(self):
        formula = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in "10000"]), "{v1}", r"\{v1\}")
        ti = ILTranslator.TranslateInput("Compare 2 cases with {v1}, 45 mW, 5BLC and (Smith, 2023).", [formula], PdfStyle())
        output = "比较两种情况，包括{v1}、45 mW、5BLC和(Smith, 2023)。"
        self.assertEqual(guard._prose_numeric_literals(ti, ti.unicode), {})
        guard._check_body_translation(translator(), paragraph(ti), ti, output)
        # The existing independent contracts still reject a changed measurement.
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "measurement-value-or-unit-changed"):
            guard.normalize_output(ti, output.replace("45 mW", "46 mW"))

    def test_source_order_grammar_advice_keeps_unproven_permutations_rejected(self):
        formulas = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode="T")]),
                                      f"{{v{i}}}", rf"\{{v{i}\}}") for i in range(1, 5)]
        ti = ILTranslator.TranslateInput("The average size{v1}of avalanches that have a certain duration {v2} is measured.", formulas[:2], PdfStyle())
        hint = guard._source_order_grammar_hint(ti)
        self.assertIn("平均大小{v1}", hint)
        self.assertIn("持续时间为{v2}", hint)
        self.assertEqual(guard.normalize_output(ti, "持续时间为{v2}的雪崩平均大小{v1}。"),
                         "持续时间为{v2}的雪崩平均大小{v1}。")
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
            guard.normalize_output(ti, "持续时间为{v2}，平均大小为{v1}。")
        self.assertEqual(guard.normalize_output(ti, "平均大小{v1}，对应于持续时间为{v2}的雪崩。"), "平均大小{v1}，对应于持续时间为{v2}的雪崩。")
        # No formula declarations, unrelated ratio wording or repeated tokens
        # provide the positive source structure required for the advice.
        for source, placeholders in ((ti.unicode, []), ("The ratio {v1} to {v2} is measured.", formulas[:2]),
                                     (ti.unicode+" {v1}", formulas[:2])):
            self.assertEqual(guard._source_order_grammar_hint(ILTranslator.TranslateInput(source, placeholders, PdfStyle())), "")

    def test_source_threshold_and_forecast_hints_keep_roles_in_original_order(self):
        formulas = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode="T")]),
                                      f"{{v{i}}}", rf"\{{v{i}\}}") for i in (3, 4)]
        sources = (
            ("Each node will increase its individual activation threshold {v3}to 1 with a probability corresponding to its own average activity over the last {v4}time steps.", "激活阈值{v3}", "过去{v4}"),
            ("The fifth and sixth rows are for the {v3}day wind (real) prediction in 1995 by similar training but supervised by the winds of the {v4}days.", "1995年的{v3}", "来自{v4}"),
        )
        for source, first, second in sources:
            ti = ILTranslator.TranslateInput(source, formulas, PdfStyle())
            hint = guard._source_order_grammar_hint(ti)
            self.assertIn(first, hint)
            self.assertIn(second, hint)
            self.assertLess(hint.index(first), hint.index(second))

    def test_unmatched_boundary_recovery_gets_exact_parenthesis_budget(self):
        ti = ILTranslator.TranslateInput("94) large training period. The matrix used 10000 epochs in 1975–94.", [], PdfStyle())
        good = "94{PBKEEP_0001}较长训练期。矩阵在1975–94年间训练10000轮。"
        engine = translator([good])
        _, output, _ = guard._whole_paragraph_translation(engine, ti, ParagraphTranslateTracker())
        self.assertIn("exactly 0 opening and 1 closing parentheses", engine.calls[0][0])
        self.assertIn("Use commas", engine.calls[0][0])
        self.assertEqual(output, good.replace("{PBKEEP_0001}", ")"))
        bad = translator([good.replace("10000轮", "10000轮（训练x）")])
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "parenthesis-boundary-changed"):
            guard._whole_paragraph_translation(bad, ti, ParagraphTranslateTracker())

    def test_whole_boundary_schema_keeps_one_natural_translation_and_original_parenthesis(self):
        formula = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode='x')]), '{v1}', r'\{v1\}')
        ti = ILTranslator.TranslateInput('94) The training uses 10000 epochs and value {v1}.', [formula], PdfStyle())
        engine = translator()
        calls = []
        def request(prompt, **kwargs):
            calls.append((prompt, kwargs))
            return json.dumps({'translation': '94{PBKEEP_0001}训练采用10000轮，数值为{v1}。'}, ensure_ascii=False)
        adapted = SimpleNamespace(llm_translate=request)
        with patch.object(guard, '_structured_translation_engine', return_value=adapted) as adapt:
            plain, output, _ = guard._whole_paragraph_translation(engine, ti, ParagraphTranslateTracker())
        self.assertEqual(output, '94)训练采用10000轮，数值为{v1}。')
        self.assertIn('single key "translation"', calls[0][0])
        self.assertNotIn('text_0001', calls[0][0])
        self.assertEqual(adapt.call_args.args[1]['json_schema']['schema']['required'], ['translation'])
        self.assertEqual(adapt.call_args.args[1]['json_schema']['schema']['properties']['translation']['pattern'], r'^[^()（）]*$')
        self.assertIs(plain.placeholders[0].formula, formula.formula)
        for bad in ('{"translation":"正文","extra":"内容"}', '{"translation":5}',
                    '{"translation":"正文","translation":"重复"}'):
            with patch.object(guard, '_structured_translation_engine', return_value=SimpleNamespace(llm_translate=lambda *a, **kw: bad)):
                with self.assertRaises(guard.PlaceholderIntegrityError):
                    guard._whole_paragraph_translation(engine, ti, ParagraphTranslateTracker())

    def test_only_new_plain_chinese_aside_parentheses_become_commas(self):
        ti = ILTranslator.TranslateInput('94) The matrix with 1975–94, not in Table 7, gives an error of 0.22.', [], PdfStyle())
        response = '94{PBKEEP_0001}1975–94年的矩阵（不在表7中）给出0.22的误差。'
        _, out, _ = guard._whole_paragraph_translation(translator([response]), ti, ParagraphTranslateTracker())
        self.assertEqual(out, '94)1975–94年的矩阵，不在表7中，给出0.22的误差。')
        self.assertEqual(guard.snapshot()['counters']['normalizedNewProseParenthesisPairs'], 1)
        for unsafe in ('（不在（表7）中）', '（不在表7中', '不在表7中）',
                       '（不在Table7中）', '（误差{v1}较大）', '（原始{PBKEEP_0001}边界）',
                       '（误差x较大）', '（误差λ较大）', '（误差≥0.22）', '（0.22）', '(不在表7中）'):
            with self.subTest(unsafe=unsafe), self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'parenthesis-boundary-changed'):
                guard._new_prose_parentheses_as_commas(unsafe)
        # An ordinary balanced source is outside this normalization contract.
        balanced = ILTranslator.TranslateInput('The matrix (not in Table 7) gives an error of 0.22.', [], PdfStyle())
        valid = '矩阵（不在表7中）给出0.22的误差。'
        _, out, _ = guard._whole_paragraph_translation(translator([valid]), balanced, ParagraphTranslateTracker())
        self.assertEqual(out, valid)
        no_boundary = ILTranslator.TranslateInput('The matrix gives an error of 0.22.', [], PdfStyle())
        ordinary = '矩阵（误差较小）给出0.22的误差。'
        _, out, _ = guard._whole_paragraph_translation(translator([ordinary]), no_boundary, ParagraphTranslateTracker())
        self.assertEqual(out, ordinary)


    def test_populated_wind_classes_mean_sample_counts(self):
        text = "Table 6 shows the number of days per class and the adjustment, which is better for the most populated classes."
        ti = ILTranslator.TranslateInput(text, [], PdfStyle())
        engine = translator()
        self.assertIn("样本数最多的类别", guard._contextual_term_hint(engine, ti))
        guard._check_body_translation(engine, paragraph(ti), ti, "表6显示每类天数及调整，对于样本数最多的类别效果更好。")
        for bad in ("表6显示每类天数及调整，对于最富有的类效果更好。", "表6显示每类天数及调整，对于最流行的类效果更好。"):
            with self.subTest(output=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "populated-class-mistranslated"):
                guard._check_body_translation(engine, paragraph(ti), ti, bad)
        other = ILTranslator.TranslateInput("The most populated classes of people have a larger average wealth.", [], PdfStyle())
        self.assertNotIn("sample counts", guard._contextual_term_hint(engine, other))

    def test_source_scientific_identifiers_cannot_be_changed_or_recast_as_counts(self):
        ti = ILTranslator.TranslateInput("The 5BLC predictor uses BLC6 and p53 with V0 for the 3rd trial.", [], PdfStyle())
        good = "5BLC预测器使用BLC6和p53，并在第三次试验中使用V0。"
        guard._check_body_translation(translator(), paragraph(ti), ti, good)
        for bad in (good.replace('5BLC', '五个BLC'), good.replace('BLC6', 'BLC7'),
                    good.replace('p53', ''), good + '使用V0。'):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'scientific-identifier-changed'):
                guard._check_body_translation(translator(), paragraph(ti), ti, bad)

    def test_formula_boundaries_do_not_invent_scientific_identifiers(self):
        formulas = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode="i")]),
                                       f"{{v{i}}}", rf"\{{v{i}\}}") for i in (1, 2)]
        ti = ILTranslator.TranslateInput("The 5BLC{v1}of the network and 5BLC{v2}shows the model result.", formulas, PdfStyle())
        good = "网络的5BLC{v1}和5BLC{v2}展示模型结果。"
        self.assertEqual(guard._prose_scientific_identifiers(ti, ti.unicode), {"5BLC": 2})
        guard._check_body_translation(translator(), paragraph(ti), ti, good)
        for bad in (good.replace("5BLC", "BLC", 1), good.replace("5BLC", "6BLC", 1), good + "5BLC"):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "scientific-identifier-changed"):
                guard._check_body_translation(translator(), paragraph(ti), ti, bad)
        # A contiguous source identifier is still literal; only an actual
        # opaque formula boundary creates a separator in the analysis view.
        named = ILTranslator.TranslateInput("The model 5BLCshows uses model5BLC of the network.", [], PdfStyle())
        self.assertEqual(guard._prose_scientific_identifiers(named, named.unicode), {"5BLCshows": 1, "model5BLC": 1})
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "scientific-identifier-changed"):
            guard._check_body_translation(translator(), paragraph(named), named, "模型5BLC使用网络的model5BLC。")
        styled = fixture()
        styled.unicode = "The model 5<style id='2'>BLC</style> uses {v1}."
        self.assertEqual(guard._prose_scientific_identifiers(styled, styled.unicode), {"5BLC": 1})

    def test_joined_numbered_figure_lists_have_explicit_reference_grammar(self):
        for source, output in (("Figures 3and 4", "图3和4"), ("Figures 13and14", "图13和14"),
                               ("Tables 13 and14", "表13和14"), ("Equations 3.1and 3.2", "式3.1和3.2")):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            with self.subTest(source=source):
                self.assertEqual(guard._prose_scientific_identifiers(ti, ti.unicode), {})
                self.assertEqual(guard._prose_numeric_literals(ti, ti.unicode), guard._prose_numeric_literals(ti, output))
                guard._check_body_translation(translator(), paragraph(ti), ti, output)
        for source in ("The identifier 3and is measured.", "Figure Eight uses 3and4.", "Figures 3and 4BLC are names."):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            self.assertTrue(guard._prose_scientific_identifiers(ti, ti.unicode), source)
        ti = ILTranslator.TranslateInput("Figures 13and14", [], PdfStyle())
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "prose-numeric-literal-changed"):
            guard._check_body_translation(translator(), paragraph(ti), ti, "图13和15")

    def test_joined_caption_numbers_require_initial_label_period_and_title(self):
        captions = (
            ("Table1. Distribution of the 27 LC wind classes inLondon, along 50 years.",
             "表1. 伦敦27类LC风在50年间的分布。"),
            ("Figure16. Distribution of the wind classes.", "图16. 风类别的分布。"),
            ("Fig.2. Distribution of the wind classes.", "图2. 风类别的分布。"),
            ("  Table27. Distribution of the wind classes.", "表27. 风类别的分布。"),
            ("Fig.12. Distribution of the wind classes.", "图12. 风类别的分布。"),
        )
        for source, output in captions:
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            with self.subTest(source=source):
                self.assertEqual(guard._prose_scientific_identifiers(ti, source), {})
                self.assertEqual(guard._prose_numeric_literals(ti, source),
                                 guard._prose_numeric_literals(ti, output))
                guard._check_body_translation(translator(), paragraph(ti), ti, output)
        for source in ("Table1 gene expression is measured.", "Figure16 protein is measured.",
                       "NotTable1. Distribution of classes.", "Table1B. Distribution of classes.",
                       "The Table1. Distribution is measured.", "Table1.Distribution of classes.",
                       "Table1.", "Table1. 27 classes are measured.",
                       "Table1.5 Distribution of classes.", "Table1: Distribution of classes.",
                       "Measurements\nTable1. Distribution of classes."):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            with self.subTest(identifier=source):
                self.assertEqual(guard._joined_prose_number_spans(source), [])
                self.assertTrue(guard._prose_scientific_identifiers(ti, source))
                numbers = " ".join(guard._prose_numeric_literals(ti, source).elements())
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "scientific-identifier-changed"):
                    guard._check_body_translation(translator(), paragraph(ti), ti, "类别分布已测量。" + numbers)
        for source, output in (captions[1], captions[3], captions[4]):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            for bad in (re.sub(r"\d+", "99", output, count=1), re.sub(r"\d+", "", output, count=1)):
                with self.subTest(source=source, bad=bad), self.assertRaisesRegex(
                        guard.PlaceholderIntegrityError, "prose-numeric-literal-changed"):
                    guard._check_body_translation(translator(), paragraph(ti), ti, bad)

    def test_joined_source_caption_labels_cannot_remain_english_in_output(self):
        for source, good, bad_labels in (
                ("Table1. Distribution of the wind classes.", "表1. 风类别分布。", ("Table1", "Table 1", "table1")),
                ("Figure16. Distribution of the wind classes.", "图16. 风类别分布。", ("Figure16", "Figure 16", "Fig.16", "Fig. 16")),
                ("Fig.2. Distribution of the wind classes.", "图2. 风类别分布。", ("Fig.2", "Fig. 2", "Figure2", "Figure 2"))):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            para = paragraph(ti)
            para.layout_label = "table_caption" if source.startswith("Table") else "figure_caption"
            with self.subTest(source=source):
                self.assertTrue(guard._untranslated_numbered_labels(ti, source))
                self.assertFalse(guard._untranslated_numbered_labels(ti, good))
                guard._check_body_translation(translator(), para, ti, good)
            for label in bad_labels:
                bad = label + ". 风类别分布。"
                with self.subTest(residual=bad):
                    self.assertTrue(guard._untranslated_numbered_labels(ti, bad))
                    with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "untranslated-prose-fragment"):
                        guard._check_body_translation(translator(), para, ti, bad)
        named = ILTranslator.TranslateInput("Table1 gene expression is measured.", [], PdfStyle())
        good = "Table1基因表达已测量。"
        self.assertFalse(guard._untranslated_numbered_labels(named, good))
        guard._check_body_translation(translator(), paragraph(named), named, good)
        caption = ILTranslator.TranslateInput("Table1. Distribution of the wind classes.", [], PdfStyle())
        for unrelated in ("Table16", "Table1Gene", "modelTable1", "Table1.5", "Figure1"):
            self.assertFalse(guard._untranslated_numbered_labels(caption, unrelated + "的类别分布。"), unrelated)
        for ambiguous in ("Table1.Distribution of classes.", "The Table1. Distribution is measured.",
                          "Measurements\nTable1. Distribution of classes."):
            ti = ILTranslator.TranslateInput(ambiguous, [], PdfStyle())
            self.assertFalse(guard._untranslated_numbered_labels(ti, "Table1类别分布。"), ambiguous)

    def test_month_first_publication_dates_remain_complete_source_metadata(self):
        source = "Received October 10, 2006; Accepted January 17, 2007; Published February 14, 2007"
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        para = paragraph(ti)
        original = para.pdf_paragraph_composition
        self.assertTrue(guard._preserve_publication_metadata(ti))
        with patch.dict(guard._originals, pre=lambda *a, **k: (source, ti)):
            self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
        self.assertEqual(para.unicode, source)
        self.assertIs(para.pdf_paragraph_composition, original)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)
        for extra in (" and we measured 10 days.", " discusses the network."):
            self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput(source + extra, [], PdfStyle())))
        self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput("Published October data were measured for 10 days in 2006.", [], PdfStyle())))

    def test_class_day_count_inversion_requires_original_small_i_and_explicit_roles(self):
        base = PdfStyle(font_id="F3", font_size=8.9663)
        formulas = []
        for index, face in ((1, "F6"), (2, "F3")):
            chars = [PdfCharacter(char_unicode="i", pdf_character_id=105,
                                  pdf_style=PdfStyle(font_id=face, font_size=5.9768))]
            formulas.append(FormulaPlaceholder(index, PdfFormula(pdf_character=chars), f"{{v{index}}}", rf"\{{v{index}\}}"))
        formulas.extend(FormulaPlaceholder(index, PdfFormula(pdf_character=[PdfCharacter(char_unicode="=")]),
                                          f"{{v{index}}}", rf"\{{v{index}\}}") for index in (3, 4))
        source = "{v3} In a time series of N days with n{v1}days per LC{v2}class, the probability is {v4}."
        ti = ILTranslator.TranslateInput(source, formulas, base)
        output = "{v3}在N天的时间序列中，若每类LC{v2}的天数为n{v1}，则概率为{v4}。"
        self.assertIn("每类LC{v2}的天数为n{v1}", guard._contextual_term_hint(translator(), ti))
        self.assertTrue(guard._safe_scientific_role_permutation(ti, source, output))
        self.assertEqual(guard.normalize_output(ti, output), output)
        parsed = translator().parse_translate_output(ti, output)
        self.assertEqual([c.pdf_formula for c in parsed if c.pdf_formula],
                         [formulas[2].formula, formulas[1].formula, formulas[0].formula, formulas[3].formula])
        for bad in (output.replace("LC{v2}", "LC"), output.replace("LC{v2}", "n{v2}"),
                    output.replace("n{v1}", "LC{v1}"), output.replace("天数", "频率"),
                    output.replace("{v1}", "{swap}").replace("{v2}", "{v1}").replace("{swap}", "{v2}"),
                    output.replace("{v3}", "").replace("，则", "{v3}，则"), output + "{v2}"):
            with self.subTest(bad=bad):
                self.assertFalse(guard._safe_scientific_role_permutation(ti, source, bad))
                with self.assertRaises(guard.PlaceholderIntegrityError):
                    guard.normalize_output(ti, bad)
        for changed_source in (source.replace("n{v1}", "N{v1}"), source.replace("LC{v2}", "lc{v2}"),
                               source.replace("days per", "days divided by")):
            self.assertFalse(guard._safe_scientific_role_permutation(ti, changed_source, output))
        for char, size in (("j", 5.9768), ("i", 8.9663), ("i+1", 5.9768)):
            formulas[0].formula.pdf_character[0].char_unicode = char
            formulas[0].formula.pdf_character[0].pdf_style.font_size = size
            self.assertFalse(guard._safe_scientific_role_permutation(ti, source, output))

    def test_pdf_joined_prose_numbers_are_counts_not_scientific_identifiers(self):
        ti = ILTranslator.TranslateInput('The 1975–94training period has 74 days in1975–94 with 5BLC and p53.', [], PdfStyle())
        good = '1975–94训练期包含1975–94年的74天，使用5BLC和p53。'
        guard._check_body_translation(translator(), paragraph(ti), ti, good)
        self.assertEqual(guard._prose_scientific_identifiers(ti, ti.unicode), {'5BLC': 1, 'p53': 1})
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'numeric-literal-changed'):
            guard._check_body_translation(translator(), paragraph(ti), ti, good.replace('74天', '若干天'))

    def test_natural_scientific_modifier_order_has_source_and_target_role_proof(self):
        examples = (
            ('Average size{v1}of avalanches of duration {v2}', '持续时间为{v2}的雪崩的平均大小{v1}'),
            ('In each time step, every node will increase its individual activation threshold {v1}to 1 '
             'with a probability corresponding to its own average activity over the last {v2}time steps',
             '在每个时间步，每个节点将以与其自身在过去{v2}个时间步内的平均活动相对应的概率将其自身的激活阈值{v1}提高到 1'),
            ('In each time step, every node will increase its individual activation threshold {v1}to 1 '
             'with a probability corresponding to its own average activity over the last {v2}time steps',
             '在每个时间步，每个节点将以与其自身过去{v2}个时间步的平均活动相对应的概率，将其自身的激活阈值{v1}提高到 1'),
            ('The fifth and sixth rows are for the {v1}day wind (real) prediction in 1995 '
             'by similar training but supervised by the winds of the {v2}days',
             '第五行和第六行用于通过类似但由{v2}天的风监督的训练对1995年的{v1}天风（真实）预测'),
        )
        for source, output in examples:
            with self.subTest(source=source):
                formulas = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode='x')]),
                                               f'{{v{i}}}', rf'\{{v{i}\}}') for i in range(1, 5)]
                ti = ILTranslator.TranslateInput('{v3} ' + source + '. {v4}', formulas, PdfStyle())
                translated = '{v3} ' + output + '。{v4}'
                self.assertTrue(guard._safe_scientific_role_permutation(ti, ti.unicode, translated))
                self.assertEqual(guard.normalize_output(ti, translated), translated)
                actual = [c.pdf_formula for c in translator().parse_translate_output(ti, translated) if c.pdf_formula]
                self.assertEqual(actual, [formulas[2].formula, formulas[1].formula, formulas[0].formula, formulas[3].formula])
                for bad in (translated + '{v1}', translated.replace('{v1}', '{v9}'),
                            translated.replace('{v3}', '{v4}', 1).rsplit('{v4}', 1)[0] + '{v3}',
                            translated.replace('{v2}', '{v2}。'),
                            translated.replace('{v2}', '{swap}').replace('{v1}', '{v2}').replace('{swap}', '{v1}')):
                    self.assertFalse(guard._safe_scientific_role_permutation(ti, ti.unicode, bad))
        # Similar keyword inventory in another relation/sentence is not proof.
        ti = ILTranslator.TranslateInput('Average size{v1}divided by duration {v2}', formulas[:2], PdfStyle())
        self.assertFalse(guard._safe_scientific_role_permutation(ti, ti.unicode, '持续时间为{v2}的雪崩的平均大小{v1}'))

    def test_multiple_scientific_modifier_groups_keep_every_outside_token_in_place(self):
        formulas = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode='x')]),
                                       f'{{v{i}}}', rf'\{{v{i}\}}') for i in range(1, 5)]
        source = ('Average size{v1}of avalanches of duration {v2}. '
                  'Average size{v3}of avalanches that have a certain duration {v4}.')
        ti = ILTranslator.TranslateInput(source, formulas, PdfStyle())
        first, second = '持续时间为{v2}的雪崩的平均大小{v1}', '特定持续时间{v4}的雪崩的平均大小{v3}'
        self.assertTrue(guard._safe_scientific_role_permutation(ti, source, first + '。' + second))
        self.assertFalse(guard._safe_scientific_role_permutation(ti, source, second + '。' + first))

    def test_loc_and_pred_source_atoms_cannot_lose_descriptors_or_accept_intervening_prose(self):
        formulas = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode='x')]),
                                       f'{{v{i}}}', rf'\{{v{i}\}}') for i in range(1, 5)]
        ti = ILTranslator.TranslateInput('The errors are {v1}loc{v2} and {v3}pred {v4}.', formulas, PdfStyle())
        good = '误差为{v1}loc{v2}和{v3}pred {v4}。'
        self.assertEqual(guard.normalize_output(ti, good), good)
        for bad in (good.replace('pred ', '时的相同误差'), good.replace('loc', ''), good.replace('pred', 'loc')):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'formula-descriptor-pairing'):
                guard.normalize_output(ti, bad)


    def test_complete_translation_schema_reaches_sdk_without_mutating_shared_provider(self):
        # Only the dependency's import-time cache goes to this temporary path;
        # no model/network/cache request occurs in this actual SDK-path test.
        with patch.object(Path, 'home', return_value=Path(_test_cache.name)):
            from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
        from babeldoc.utils.atomic_integer import AtomicInteger
        provider = object.__new__(OpenAITranslator)
        provider.options = {'temperature': 0, 'extra_body': {'presence_penalty': 0}}
        provider.enable_json_mode = True
        provider.ignore_cache = False
        provider.translate_call_count = provider.translate_cache_call_count = 0
        provider.rate_limiter = SimpleNamespace(wait=Mock())
        provider.cache = SimpleNamespace(get=Mock(), set=Mock())
        provider.model = 'existing-local-model'
        for name in ('token_count', 'prompt_token_count', 'completion_token_count', 'cache_hit_prompt_token_count'):
            setattr(provider, name, AtomicInteger())
        response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"translation":"完整结果"}'))],
                                   usage=SimpleNamespace(total_tokens=9, prompt_tokens=6, completion_tokens=3))
        create = Mock(return_value=response)
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        response_format = guard._complete_translation_schema()
        other_format = guard._complete_translation_schema()
        other_format['json_schema']['name'] = 'independent_request'
        first = guard._structured_translation_engine(provider, response_format)
        second = guard._structured_translation_engine(provider, other_format)
        self.assertIsNot(first, provider)
        self.assertIs(first.client, provider.client)
        self.assertIs(first.rate_limiter, provider.rate_limiter)
        self.assertIs(first.token_count, provider.token_count)
        first.llm_translate('Translate this JSON', ignore_cache=True,
                            rate_limit_params={'paragraph_token_count': 6, 'request_json_mode': True})
        sent = create.call_args.kwargs
        self.assertEqual(sent['response_format'], response_format)
        self.assertEqual(sent['extra_body']['presence_penalty'], 0)
        self.assertEqual(second.options['response_format']['json_schema']['name'], 'independent_request')
        self.assertEqual(first.options['response_format']['json_schema']['name'], 'pagebridge_complete_translation')
        self.assertEqual(provider.options, {'temperature': 0, 'extra_body': {'presence_penalty': 0}})
        self.assertTrue(provider.enable_json_mode)
        provider.cache.get.assert_not_called()
        provider.cache.set.assert_not_called()
        provider.rate_limiter.wait.assert_called_once()
        # Authentication/timeouts/schema errors propagate; never silently send
        # a second unconstrained request or change the destination.
        create.side_effect = RuntimeError('unsupported schema')
        with self.assertRaisesRegex(RuntimeError, 'unsupported schema'):
            first.llm_translate('Translate this JSON', ignore_cache=True)
        self.assertEqual(create.call_count, 2)
        fake = SimpleNamespace(llm_translate=Mock())
        self.assertIs(guard._structured_translation_engine(fake, response_format), fake)

    def test_failed_natural_recovery_never_uses_anchored_slot_output(self):
        ti = fixture()
        engine = translator(["坏{v8}", "坏{v9}", "坏{v7}", '{"text_0001":"流量 ","text_0002":" 速率。"}'])
        para = paragraph(ti)
        original = para.pdf_paragraph_composition
        self.assertFalse(hasattr(guard, '_anchored_paragraph_translation'))
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'unresolved-paragraph'):
            engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "坏{v6}")
        self.assertEqual(para.unicode, ti.unicode)
        self.assertEqual(len(engine.calls), 3)
        self.assertEqual(guard.snapshot()['unresolvedCount'], 1)
        self.assertIs(para.pdf_paragraph_composition, original)

    def test_failed_natural_recovery_does_not_commit_or_hide_untranslated_body(self):
        ti = ILTranslator.TranslateInput("The result is exclusively used in experiments with this method.", [], PdfStyle())
        engine = translator(["{v8}", "{v9}", "{v7}", '{"text_0001":"结果exclusively用于这种方法的实验。"}'])
        para = paragraph(ti)
        original = para.pdf_paragraph_composition
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "unresolved-paragraph"):
            engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "{v6}")
        self.assertEqual(para.unicode, ti.unicode)
        self.assertIs(para.pdf_paragraph_composition, original)
        self.assertEqual(guard.snapshot()['unresolvedCount'], 1)

    def test_source_attested_inflected_prose_is_checked_without_catching_names(self):
        ti = ILTranslator.TranslateInput("The most populated classes yield the best results.", [], PdfStyle())
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
            guard._check_body_translation(translator(), paragraph(ti), ti, "样本最populated的类别给出最佳结果。")
        name = ILTranslator.TranslateInput("Bradley and Regulated Networks designed this new instrument.", [], PdfStyle())
        guard._check_body_translation(translator(), paragraph(name), name, "Bradley和Regulated Networks设计了这台新仪器。")


    def test_complete_spaced_labels_use_fixed_translation_with_audited_style_fallback(self):
        placeholders, parts = [], []
        for index, char in enumerate("ARTICLEINFO", 1):
            marker = RichTextPlaceholder(index, PdfSameStyleCharacters(pdf_character=[PdfCharacter(char_unicode=char)], pdf_style=PdfStyle()),
                                        f"<style id='{index}'>", "</style>", rf"<style id='{index}'>", r"</style>")
            placeholders.append(marker)
            parts.append(marker.left_placeholder + char + marker.right_placeholder)
        ti = ILTranslator.TranslateInput(" ".join(parts), placeholders, PdfStyle())
        para = paragraph(ti)
        instance = translator()
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "科学研究论文：从工具的革命到革命的工具推动")
        self.assertEqual(para.unicode, "文章信息")
        self.assertEqual(instance.calls, [])
        self.assertEqual(guard.snapshot()["counters"]["fixedLabelRichTextFallbackParagraphs"], 1)
        self.assertEqual(guard.snapshot()["unresolvedCount"], 0)
        for source, expected in (("A B S T R A C T", "摘要"), ("Ｋ Ｅ Ｙ Ｗ Ｏ Ｒ Ｄ Ｓ：", "关键词："), ("KEY WORDS", "关键词")):
            with self.subTest(source=source):
                self.assertEqual(guard._fixed_label_translation(instance, ILTranslator.TranslateInput(source, [], PdfStyle())), expected)
        for source in ("Keywords: AI for science", "The abstract explains our method", "ARTICLE INFO is useful.", "A B S T R A C T !"):
            with self.subTest(source=source):
                self.assertIsNone(guard._fixed_label_translation(instance, ILTranslator.TranslateInput(source, [], PdfStyle())))
        formula_ti = fixture()
        formula_ti.unicode = "ARTICLE INFO {v1}"
        self.assertIsNone(guard._fixed_label_translation(instance, formula_ti))

    def test_contextual_idiom_retries_whole_paragraph_and_preserves_formula(self):
        ti = fixture()
        ti.unicode = "Under the umbrella of science, <style id='2'>methods</style> use {v1}."
        output = "在科学的 umbrella 下，<style id='2'>方法</style>使用 {v1}。"
        correct = "在科学框架下，<style id='2'>方法</style>使用 {v1}。"
        instance = translator([correct])
        para = paragraph(ti)
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, output)
        self.assertEqual(para.unicode, correct)
        self.assertEqual(len(instance.calls), 1)
        self.assertIn("conceptual idiom", instance.calls[0][0])
        self.assertTrue(instance.calls[0][0].endswith(ti.unicode))
        self.assertIs(next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula), ti.placeholders[0].formula)
        other = ILTranslator.TranslateInput("Umbrella Corporation reports this scientific method.", [], PdfStyle())
        self.assertEqual(guard._contextual_term_hint(instance, other), "")
        guard._check_body_translation(instance, paragraph(other), other, "Umbrella Corporation 报告了这一科学方法。")
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "translation-prompt-echo"):
            guard.normalize_output(other, "将其含义自然翻译为在框架下。")

    def test_complete_credit_metadata_preserves_names_and_roles_without_guessing(self):
        source = ("Alice Smith: Writing – review & editing, Writing – original draft, Supervision, "
                  "Resources, Investigation, Funding acquisition, Conceptual- ization. "
                  "Bob Brown: Writing – review & editing, Validation, Project administration, Formal analysis. "
                  "Carol Jones: Resources, Conceptualization.")
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        para = paragraph(ti)
        compositions = para.pdf_paragraph_composition
        instance = translator()
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            self.assertEqual(instance.pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
        self.assertIs(para.pdf_paragraph_composition, compositions)
        self.assertEqual(instance.calls, [])
        for text in ("We describe our results. " + source, source + " These findings support our hypothesis.",
                     source.replace("Resources, Conceptualization.", "Resources, unknown scientific role."),
                     "Alice Smith: Our method uses the following data. Bob Brown: We evaluate this approach."):
            with self.subTest(source=text):
                self.assertFalse(guard._is_credit_metadata(text))

    def test_correspondence_metadata_preserves_address_name_email_and_link_footnotes(self):
        marker = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode="∗")]), "{v1}", r"\{v1\}")
        source = ("{v1} Corresponding author at: Artificial Intelligence Laboratory, London, UK. "
                  "E-mail address: alice.smith@example.org (A. Smith). "
                  "1 https://example.org/the/from/learning 2 https://example.net/research/")
        ti = ILTranslator.TranslateInput(source, [marker], PdfStyle())
        para = paragraph(ti)
        compositions = para.pdf_paragraph_composition
        instance = translator()
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            self.assertEqual(instance.pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
        self.assertIs(para.pdf_paragraph_composition, compositions)
        self.assertEqual(para.unicode, source)
        self.assertEqual(instance.calls, [])
        visible, _ = guard._visible_text_map(ti, source)
        for invalid in (
            "We describe the method. " + visible,
            visible + " This method improves results.",
            visible.replace("Laboratory, London, UK", "Laboratory, London, UK, where we study learning"),
            visible.replace("E-mail address:", "Contact details:"),
            visible.replace("alice.smith@example.org", "https://example.org/contact"),
            visible.replace("(A. Smith)", "(Our work provides evidence)"),
        ):
            with self.subTest(source=invalid):
                self.assertFalse(guard._is_correspondence_metadata(invalid))

    def test_all_observed_paper_parenthetical_citations_are_protected(self):
        citations = (
            "(Wang et al., 2023)",
            "(Senior et al., 2020; Jumper et al., 2021; Abramson et al., 2024)",
            "(Watson et al., 2023)", "(Achiam et al., 2023; Guo et al., 2025)",
            "(Zhou et al., 2024)", "(Liu et al., 2021)", "(Ji et al., 2024)",
            "(Lander et al., 2001)", "(Aad et al., 2012)", "(Reuter et al., 2015)",
            "(Belis et al., 2024; Assael et al., 2022; Ma et al., 2024; Perez-Lopez et al., 2024; Calderaro et al., 2024; Wang and Deng, 2024; Wu et al., 2021; Li et al., 2022; Ma et al., 2024)",
            "(Gao et al., 2023; Coyner et al., 2022)", "(Burger et al., 2020; Lu et al., 2024)",
            "(Varadi et al., 2022)",
            "(Skamarock et al., 2008; Molteni et al., 1996; Stensrud, 2007; Stephens, 1984; Phillips et al., 2004; Allen et al., 2002; Palmer et al., 2005; Bi et al., 2023; Chen et al., 2023; Han et al., 2024; Kochkov et al., 2024)",
            "(Hermann et al., 2020)", "(Chen et al., 2023; Kochkov et al., 2024; Verma et al., 2024)",
            "(Zhang and A. Lapkin, 2023; Lan et al., 2024)", "(Wang et al., 2024; Dussauge et al., 2023)",
            "(Ukoba et al., 2024)", "(Yuan et al., 2025)", "(Rapp et al., 2024; Szymanski et al., 2023)",
            "(Watson et al., 2023; Dauparas et al., 2022)", "(Jumper et al., 2021; Dauparas et al., 2022; Watson et al., 2023)",
            "(Lam et al., 2023)", "(Li et al., 2020)", "(Raissi et al., 2019; Pathak et al., 2022)",
        )
        for citation in citations:
            with self.subTest(citation=citation):
                ti = ILTranslator.TranslateInput("Scientific evidence " + citation + ".", [], PdfStyle())
                self.assertEqual(len(guard._citation_spans(ti, ti.unicode)), 1)
                guard.normalize_output(ti, "科学证据" + citation + "。")
                for bad in ("科学证据。", "科学证据" + citation + citation, "科学证据" + citation.replace(", 20", ", 19", 1)):
                    with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "author-year-citation-changed"):
                        guard.normalize_output(ti, bad)
        for value in ("(2025)", "(May, 2020)", "(Figure, 2020)", "(x = 2020)", "(x, y)", "(see section 4)",
                      "(obtained in 2020)", "(the observed effect, 2020)", "(20 November 2025)"):
            ti = ILTranslator.TranslateInput(value, [], PdfStyle())
            self.assertEqual(guard._citation_spans(ti, value), [], value)

    def test_missing_citation_retries_and_citation_order_cannot_change(self):
        source = "Physics advances from (Lam et al., 2023) and (Li et al., 2020)."
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        corrected = "物理学的发展基于 (Lam et al., 2023) 和 (Li et al., 2020)。"
        instance = translator([corrected])
        para = paragraph(ti)
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, "物理学的发展基于 (Lam et al., 2023)。")
        self.assertEqual(para.unicode, corrected)
        self.assertEqual(len(instance.calls), 1)
        self.assertIn("parenthetical author-year citation", instance.calls[0][0])
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "author-year-citation-changed"):
            guard.normalize_output(ti, "物理学的发展基于 (Li et al., 2020) 和 (Lam et al., 2023)。")

    def test_citation_and_classification_recovery_keep_original_formula_objects(self):
        ti = fixture()
        ti.placeholders[0].formula.pdf_character = [PdfCharacter(char_unicode="2020")]
        other = FormulaPlaceholder(4, PdfFormula(pdf_character=[PdfCharacter(char_unicode="Q")]), "{v4}", r"\{v4\}")
        ti.placeholders.append(other)
        ti.unicode = "See (<style id='2'>Li et al.,</style> {v1}) and Zone 1/Division 2, using {v4}."
        instance = translator(["参见 {PBKEEP_0001}，用于{PBKEEP_0002}，使用 {v4}。"])
        plain, output, removed = guard._whole_paragraph_translation(instance, ti, ParagraphTranslateTracker())
        self.assertEqual(removed, 1)
        self.assertIn("(Li et al., {v1})", output)
        self.assertIn("用于Zone 1/Division 2", output)
        self.assertTrue(instance.calls[0][0].endswith("See {PBKEEP_0001} and {PBKEEP_0002}, using {v4}."))
        compositions = instance.parse_translate_output(plain, output)
        formulas = [c.pdf_formula for c in compositions if c.pdf_formula]
        self.assertIs(formulas[0], ti.placeholders[0].formula)
        self.assertIs(formulas[1], other.formula)

    def test_classification_boundaries_allow_chinese_not_ascii_identifier_prefixes(self):
        source = "Suitable for Zone 2 installations, using Division 2 equipment, or Division 1 or 2."
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        guard.normalize_output(ti, "适用于Zone 2的ic保护，用于Division 2的设备，以及Division 1 or 2。")
        other = ILTranslator.TranslateInput("Class I, Div. 1 protection", [], PdfStyle())
        guard.normalize_output(other, "采用Class I, Div. 1保护。")
        for prefix in ("Time", "a", "_", "2"):
            text = prefix + "Zone 2"
            self.assertEqual(guard._classification_spans(ti, text), [], text)

    def test_intact_atomic_subscripts_can_follow_natural_chinese_prose_order(self):
        ti = atomic_prose_fixture()
        output = "该曲线描述了在给定I{v2}且不影响本质安全时，允许连接的最大电感L{v1}。"
        para = paragraph(ti)
        instance = translator()
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, output)
        self.assertEqual(para.unicode, output)
        actual = [item.pdf_formula for item in para.pdf_paragraph_composition if item.pdf_formula]
        self.assertIs(actual[0], ti.placeholders[1].formula)
        self.assertIs(actual[1], ti.placeholders[0].formula)
        self.assertEqual(instance.calls, [])
        self.assertEqual(guard.snapshot()["counters"]["atomicVariableOrderAdjustments"], 1)

    def test_atomic_reorder_requires_maximum_inductance_given_current_relation(self):
        for relation in ("ratio of", "difference between"):
            ti = atomic_prose_fixture()
            ti.unicode = ("For the following analysis of the electrical system, we compute the " + relation
                          + " L{v1} to the given current I{v2} with all other assumptions unchanged.")
            output = "在以下电气系统分析中，我们计算I{v2}与所给定电感L{v1}的比值，其余假设保持不变。"
            with self.subTest(relation=relation):
                self.assertFalse(guard._safe_atomic_prose_permutation(ti, ti.unicode, output))
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
                    guard.normalize_output(ti, output)
        ti = atomic_prose_fixture()
        ti.unicode = ("The inductive minimum ignition curve describes the maximum (insulated) inductance L{v1}"
                      "which may be connected to an associated apparatus with a given I{v2}without adversely "
                      "affecting intrinsic safety. This value has been detected in laboratory measurements "
                      "through experiments using spark test apparatus. These experiments did not take into "
                      "account any capacitances in the circuit.")
        actual_response = ("感应最小点火曲线描述了与给定I{v2}相关联的设备可连接的最大（绝缘）电感L{v1}，而不会损害本质安全。"
                           "该值是通过使用火花试验装置进行的实验室测量实验检测到的。这些实验未考虑电路中的任何电容。")
        self.assertEqual(guard.normalize_output(ti, actual_response), actual_response)
        for bad in (actual_response.replace("而不会损害", "而会损害"),
                    actual_response.replace("可连接的最大", "不可连接的最大"),
                    actual_response.replace("最大（绝缘）电感", "最大（绝缘）电感的倒数"),
                    actual_response.replace("相关联的设备可连接的", "与设备可连接的").replace("，而不会", "的比值，而不会"),
                    actual_response.replace("给定I", "给定电感I").replace("电感L", "电流L")):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
                guard.normalize_output(ti, bad)
        for changed in (ti.unicode.replace("which may be connected", "which may not be connected"),
                        ti.unicode.replace("maximum", "minimum"),
                        ti.unicode.replace("without adversely affecting", "while adversely affecting")):
            other = ILTranslator.TranslateInput(changed, ti.placeholders, ti.base_style)
            self.assertFalse(guard._safe_atomic_prose_permutation(other, changed, actual_response))

    def test_atomic_subscript_exception_cannot_change_pairs_or_mathematics(self):
        valid = "在给定I{v2}且不影响本质安全时，允许连接的最大电感L{v1}。"
        bad_outputs = (
            valid.replace("I{v2}", "L{v2}"),
            valid.replace("L{v1}", "I{v1}"),
            "给定I{v1}且不影响本质安全时，允许连接的最大电感L{v2}。",
            valid + " L{v1}", valid.replace("L{v1}", "L"),
            "这两个量的计算公式为 I{v2}−L{v1}。",
            "这两个量的计算公式为 I{v2}+L{v1}。",
            "这两个量的计算公式为 I{v2}=L{v1}。",
            "这两个量的计算公式为 I{v2}×L{v1}。",
            "这两个量的计算公式为 I{v2}/L{v1}。",
            "这两个量的计算公式为 f(I{v2}, L{v1})。",
        )
        for output in bad_outputs:
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(atomic_prose_fixture(), output)
        for expression in ("L{v1}−I{v2}", "L{v1}+I{v2}", "L{v1}=I{v2}", "L{v1}×I{v2}", "L{v1}/I{v2}", "f(L{v1}, I{v2})"):
            ti = atomic_prose_fixture()
            ti.unicode = "This scientific expression is given with enough explanatory prose for an ordinary paragraph: " + expression
            with self.subTest(expression=expression), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, valid)
        for modification in ("full_size", "multiple_chars", "different_font", "non_subscript"):
            ti = atomic_prose_fixture()
            char = ti.placeholders[0].formula.pdf_character[0]
            if modification == "full_size": char.pdf_style.font_size = 11
            elif modification == "multiple_chars": char.char_unicode = "xy"
            elif modification == "different_font": char.pdf_style.font_id = "MATH"
            else: char.char_unicode = "+"
            with self.subTest(modification=modification), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti, valid)

    def test_multiple_protected_ranges_and_plain_parse_keep_actual_formula_objects(self):
        ti = fixture()
        ti.placeholders[0].formula.pdf_character = [PdfCharacter(char_unicode="1")]
        other = FormulaPlaceholder(4, PdfFormula(pdf_character=[PdfCharacter(char_unicode="Q")]), "{v4}", r"\{v4\}")
        ti.placeholders.append(other)
        ti.unicode = "Compare Zone {v1}/Division 2 with Class I, Div. 1; <style id='2'>response</style> {v4}."
        response = "比较 {PBKEEP_0001} 与 {PBKEEP_0002} 的响应 {v4}。"
        instance = translator([response])
        plain, output, removed = guard._whole_paragraph_translation(instance, ti, ParagraphTranslateTracker())
        self.assertEqual(removed, 1)
        self.assertEqual(len(plain.placeholders), 2)
        compositions = instance.parse_translate_output(plain, output)
        actual = [c.pdf_formula for c in compositions if c.pdf_formula]
        self.assertIs(actual[0], ti.placeholders[0].formula)
        self.assertIs(actual[1], other.formula)
        self.assertIn("Zone {v1}/Division 2", output)
        self.assertIn("Class I, Div. 1", output)
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard._whole_paragraph_translation(translator(["比较 {PBKEEP_0002} 与 {PBKEEP_0001} {v4}。"]), ti, ParagraphTranslateTracker())

    def test_single_small_script_must_keep_its_base_even_without_reordering(self):
        ti = atomic_prose_fixture()
        ti.placeholders = ti.placeholders[:1]
        ti.unicode = "This results in 88.9 mH for L{v1}."
        correct = "得到L{v1}为88.9 mH。"
        self.assertEqual(guard.normalize_output(ti, correct), correct)
        for bad in ("得到L为88.9 mH{v1}。", "得到I{v1}为88.9 mH。", "得到L为88.9 mH，{v1}。"):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "atomic-variable-pairing"):
                guard.normalize_output(ti, bad)
        # A bullet or a genuine full equation elsewhere does not disable pairing.
        other = FormulaPlaceholder(3, PdfFormula(pdf_character=[PdfCharacter(char_unicode="•")]), "{v3}", r"\{v3\}")
        ti.placeholders.insert(0, other); ti.unicode = "{v3}" + ti.unicode
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "atomic-variable-pairing"):
            guard.normalize_output(ti, "{v3}得到L为88.9 mH{v1}。")

    def test_decimal_crossing_formula_boundary_is_protected_and_restored(self):
        formula = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode="i = 0")]), "{v1}", r"\{v1\}")
        ti = ILTranslator.TranslateInput("The internal capacitance is C{v1}.2 μF in this example.", [formula], PdfStyle())
        guard.normalize_output(ti, "本例内部电容为C{v1}.2微法。")
        for bad in ("内部电容为C{v1}2微法。", "内部电容为C{v1}。2微法。", "内部电容为C{v1}.3微法。",
                    "内部电容为C{v1}0.2微法。", "内部电容为C{v1}.2毫亨。"):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "measurement-value-or-unit-changed"):
                guard.normalize_output(ti, bad)
        instance = translator(["本例内部电容为{PBKEEP_0001}。"])
        prepared, output, _ = guard._whole_paragraph_translation(instance, ti, ParagraphTranslateTracker())
        self.assertEqual(output, "本例内部电容为C{v1}.2 μF。")
        self.assertTrue(instance.calls[0][0].endswith("The internal capacitance is {PBKEEP_0001} in this example."))
        actual = [c.pdf_formula for c in instance.parse_translate_output(prepared, output) if c.pdf_formula]
        self.assertIs(actual[0], formula.formula)

    def test_measurements_allow_unit_words_but_not_precision_or_sign_changes(self):
        ti = ILTranslator.TranslateInput("Use 0.04 μF, 1.5 V and 20 mm2 at -5 °C.", [], PdfStyle())
        guard.normalize_output(ti, "在-5 °C时使用0.04微法、1.5伏和20毫米²。")
        for bad in ("使用00.04 μF、1.5 V和20 mm2，温度-5 °C。", "使用0.04 μF、1.5 V和20 mm2，温度5 °C。"):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "measurement-value-or-unit-changed"):
                guard.normalize_output(ti, bad)

    def test_complete_measurement_formula_before_glued_conjunction(self):
        original = PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in "1 Ω"])
        placeholder = FormulaPlaceholder(1, original, "{v1}", r"\{v1\}")
        ti = ILTranslator.TranslateInput("The impedance must be less than {v1}and isolation is required.", [placeholder], PdfStyle())
        source = ti.unicode
        self.assertEqual([s[2] for s in guard._measurement_spans(ti, source)], ["1 Ω"])
        left, right, _ = guard._measurement_spans(ti, source)[0]
        self.assertEqual(source[left:right], "{v1}")
        output = "阻抗必须小于{v1}，且需要隔离。"
        self.assertEqual(guard.normalize_output(ti, output), output)
        parsed = translator().parse_translate_output(ti, output)
        self.assertIs(next(c.pdf_formula for c in parsed if c.pdf_formula), original)
        self.assertEqual(ti.unicode, source)
        self.assertEqual("".join(c.char_unicode for c in original.pdf_character), "1 Ω")
        for bad in (output.replace("小于", "小于2"), output.replace("小于", "≤"), output.replace("小于", ">")):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, "measurement-value-or-unit-changed"):
                guard.normalize_output(ti, bad)

    def test_measurement_boundary_projection_does_not_split_units_decimals_or_urls(self):
        cases = (
            ("1 m", "{v1}s", ["1 ms"]),
            ("1 m", "{v1}/s", ["1 m/s"]),
            ("1", "{v1}.2 V", ["1.2 V"]),
            ("+ 0", "0.04 μF {v1}.2 μF", ["0.04 μF", "0.2 μF"]),
            ("1 Ω", "{v1}/km", ["1 Ω/km"]),
            ("1 Ω", "{v1}andromeda", []),
            ("1 m", "https://example.org/{v1}and", []),
            ("1 Ω/km", "{v1}and a cable", ["1 Ω/km"]),
        )
        for value, source, expected in cases:
            p = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in value]), "{v1}", r"\{v1\}")
            ti = ILTranslator.TranslateInput(source, [p], PdfStyle())
            with self.subTest(value=value, source=source):
                self.assertEqual([s[2] for s in guard._measurement_spans(ti, source)], expected)

    def test_connectible_pair_retry_hint_does_not_permit_order_changes(self):
        base = PdfStyle(font_id="TT0", font_size=11)
        placeholders = [FormulaPlaceholder(i, PdfFormula(pdf_character=[
            PdfCharacter(char_unicode="o", pdf_style=PdfStyle(font_id="TT0", font_size=6.38)),
            PdfCharacter(char_unicode=" ", pdf_style=base)]), f"{{v{i}}}", rf"\{{v{i}\}}") for i in range(1, 5)]
        ti = ILTranslator.TranslateInput("The maximum connectible C{v1}values and L{v2}values for the respective values U{v3}and I{v4}are determined by curves and tables.", placeholders, base)
        instance = translator()
        hint = guard._contextual_term_hint(instance, ti)
        self.assertIn("最大可连接的C{v1}值和L{v2}值", hint)
        self.assertIn("对应于U{v3}和I{v4}的相应值", hint)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
            guard.normalize_output(ti, "对于U{v3}和I{v4}，最大可连接C{v1}和L{v2}由图表确定。")
        for changed in (ti.unicode.replace("values and L", "values divided by L"),
                        ti.unicode.replace("for the respective values", "minus the respective values")):
            other = ILTranslator.TranslateInput(changed, placeholders, base)
            self.assertNotIn("maximum-connectible pair", guard._contextual_term_hint(instance, other))
        # Numeric prose/citations are not an indiscriminate all-number gate.
        ordinary = ILTranslator.TranslateInput("There are 3 groups.", [], PdfStyle())
        guard.normalize_output(ordinary, "共有三组。")

    def test_pure_measurement_expressions_keep_original_composition(self):
        for source in ("• Cc = 200 nF/km × 0.2 km = 40 nF = 0.04 μF", "0.04 μF + 0.3 μF",
                       "0,2 mH + 0 mH", "≥ 20 mm2 ... ≤ 1000 mm2", "W = 0.5 × L × I²"):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            para = paragraph(ti); original = para.pdf_paragraph_composition
            with self.subTest(source=source), patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
                self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
                self.assertIs(para.pdf_paragraph_composition, original)
                self.assertEqual(para.unicode, source)
        for source in ("The capacitance is 0.2 μF.", "If C = 0.2 μF, the calculation proceeds as follows.", "is 1 m", "If 1 V", "In 20 m"):
            self.assertFalse(guard._pure_technical_expression(ILTranslator.TranslateInput(source, [], PdfStyle())), source)

    def test_fallback_diagram_fragments_never_request_word_translations(self):
        for source in ("Ex ia", "Ex ib", "non-", "incendive", "apparatus (excluding lead", "wires)"):
            ti = ILTranslator.TranslateInput(source, [], PdfStyle())
            para = paragraph(ti); para.layout_label = "fallback_line"; original = para.pdf_paragraph_composition
            with self.subTest(source=source), patch.dict(guard._originals, pre=lambda *a, **k: self.fail("should skip original pre")):
                self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None, None))
                self.assertIs(para.pdf_paragraph_composition, original)
        self.assertEqual(guard.snapshot()["counters"]["preservedFragmentLabels"], 6)

    def test_source_standards_cannot_split_digits_or_change_parts(self):
        ti = ILTranslator.TranslateInput("IEC 60079-11 and ANSI/UL 60079-11 define protection.", [], PdfStyle())
        guard.normalize_output(ti, "IEC 60079-11和ANSI/UL 60079-11规定了保护。")
        guard.normalize_output(ti, "IEC60079-11和ANSI/UL60079-11规定了保护。")
        for bad in ("IEC 600 79-11和ANSI/UL 60079-11规定了保护。", "IEC 60079-14和ANSI/UL 60079-11规定了保护。"):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "standard-identifier-changed"):
                guard.normalize_output(ti, bad)

    def test_parenthesis_boundaries_do_not_duplicate_separate_caption_glyphs(self):
        ti = ILTranslator.TranslateInput("Figure 16. Current modulation (Manchester II coding", [], PdfStyle())
        guard.normalize_output(ti, "图16. 电流调制（曼彻斯特II编码")
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "parenthesis-boundary-changed"):
            guard.normalize_output(ti, "图16. 电流调制（曼彻斯特II编码）")
        balanced = ILTranslator.TranslateInput("Read the measurement (shown in the figure).", [], PdfStyle())
        guard.normalize_output(balanced, "读取图中显示的测量值。")
        for bad in ("读取图中显示的测量值。)", "读取)图中显示的(测量值。"):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "parenthesis-boundary-changed"):
                guard.normalize_output(balanced, bad)
        preexisting = ILTranslator.TranslateInput("A source fragment) with an original unmatched boundary.", [], PdfStyle())
        guard.normalize_output(preexisting, "源片段）带有原始不配对边界。")

    def test_non_intrinsically_safe_category_and_material_lead_context(self):
        ti = ILTranslator.TranslateInput("Keep intrinsically safe circuits separate from non-intrinsically safe circuits.", [], PdfStyle())
        guard._check_body_translation(translator(), paragraph(ti), ti, "将本安电路与非本安电路隔离。")
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "non-intrinsically-safe-category-changed"):
            guard._check_body_translation(translator(), paragraph(ti), ti, "将本安电路与危险场所电路隔离。")
        self.assertIn("非本安电路", guard._contextual_term_hint(translator(), ti))
        wire = ILTranslator.TranslateInput("Pure lead wires exhibit superconductivity at low temperature.", [], PdfStyle())
        guard._check_body_translation(translator(), paragraph(wire), wire, "纯铅线在低温下表现出超导性。")
        self.assertNotIn("lead", guard._contextual_term_hint(translator(), wire))

    def test_german_affiliation_accents_are_metadata_not_formula_reordering(self):
        accents = [FormulaPlaceholder(i, PdfFormula(pdf_character=[PdfCharacter(char_unicode="¨")]), f"{{v{i}}}", rf"\{{v{i}\}}") for i in (1,2)]
        ti = ILTranslator.TranslateInput("Institut fu{v1}r Theoretische Physik, Universita{v2}t Bremen, Bremen, Germany", accents, PdfStyle())
        self.assertTrue(guard._preserve_publication_metadata(ti))
        para = paragraph(ti); original = para.pdf_paragraph_composition
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            self.assertEqual(translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {}), (None,None))
        self.assertIs(para.pdf_paragraph_composition, original)
        self.assertFalse(guard._preserve_publication_metadata(ti, "title"))
        self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput("University Students and Science, Theory, and Practice", [], PdfStyle())))
        for source in (ti.unicode + " studied this problem.", "The Institut developed this model in Bremen, Germany, with colleagues."):
            self.assertFalse(guard._preserve_publication_metadata(ILTranslator.TranslateInput(source, accents, PdfStyle())))
        accents[0].formula.pdf_character[0].char_unicode = "x=2"
        self.assertFalse(guard._preserve_publication_metadata(ti))

    def test_rich_small_script_promotes_original_glyph_with_actual_baseline(self):
        base = PdfStyle(font_id="TT0", font_size=11)
        base_char = PdfCharacter(char_unicode="C", pdf_style=base, box=Box(220.7114,556.693,228.6644,567.693))
        script = PdfCharacter(char_unicode="o", pdf_style=PdfStyle(font_id="TT0",font_size=6.38),
                              pdf_character_id=111, box=Box(228.8017,553.063,232.36812,559.443),
                              visual_bbox=VisualBbox(box=Box(229.03846,552.96643,232.13189,556.49599)))
        rich = RichTextPlaceholder(2, PdfSameStyleCharacters(pdf_style=script.pdf_style,pdf_character=[script]),
                                   "<style id='2'>", "</style>", r"<style id='2'>", "</style>")
        ti = ILTranslator.TranslateInput("Determine C<style id='2'>o</style> from the graph.", [rich], base)
        para = paragraph(ti)
        para.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=[base_char,script]))]
        with patch.dict(guard._originals, pre=lambda *a, **k: (ti.unicode, ti)):
            text, prepared = translator().pre_translate_paragraph(para, ParagraphTranslateTracker(), {}, {})
        self.assertEqual(text, "Determine C{v2} from the graph.")
        formula = prepared.placeholders[0].formula
        self.assertIs(formula.pdf_character[0], script)
        self.assertAlmostEqual(formula.y_offset, -3.72657, places=4)
        self.assertAlmostEqual(formula.x_offset, 0.37406, places=4)
        compositions = translator().parse_translate_output(prepared, "根据图确定C{v2}。")
        self.assertIs(next(c.pdf_formula for c in compositions if c.pdf_formula), formula)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "atomic-variable-pairing"):
            guard.normalize_output(prepared, "根据图确定L{v2}。")
        # Real P53 embeds the adjacent L in a different subset of the same face.
        base_char.pdf_style = PdfStyle(font_id="C2_0",font_size=11)
        fonts = {"C2_0": SimpleNamespace(name="AAAAAA+HelveticaWorld-Regular"),
                 "TT0": SimpleNamespace(name="BBBBBB+HelveticaWorld-Regular")}
        promoted, count = guard._promote_rich_scripts(ti, para, fonts)
        self.assertEqual(count, 1)
        self.assertIs(promoted.placeholders[0].formula.pdf_character[0], script)
        self.assertAlmostEqual(promoted.placeholders[0].formula.y_offset, -3.72657, places=4)
        fonts["C2_0"].name = "CCCCCC+TimesNewRoman"
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "source-small-script-geometry"):
            guard._promote_rich_scripts(ti, para, fonts)

    def test_p14_subset_script_promotes_and_retains_binding_after_style_reduction(self):
        ti,para,fonts,script,_ = captured_rich_script_fixture()
        promoted,count = guard._promote_rich_scripts(ti,para,fonts)
        self.assertEqual(count,1)
        self.assertEqual(promoted.unicode,"{v1}Internal inductance L{v2} {v4}H")
        formula = promoted.placeholders[1].formula
        self.assertIs(formula.pdf_character[0],script)
        self.assertEqual((script.pdf_style.font_id,script.pdf_character_id),("TT0",105))
        self.assertAlmostEqual(formula.y_offset,-3.6301,places=4)
        plain,_ = guard._without_rich_wrappers(promoted)
        self.assertEqual(guard._source_script_bindings(plain),{"2":"L"})
        guard.normalize_output(plain,"{v1}内部电感L{v2} {v4}H")
        for bad in ("{v1}内部电感I{v2} {v4}H","{v1}内部电感L {v2} {v4}H"):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError,"atomic-variable-pairing"):
                guard.normalize_output(plain,bad)
        parsed = translator().parse_translate_output(plain,"{v1}内部电感L{v2} {v4}H")
        self.assertIs([c.pdf_formula for c in parsed if c.pdf_formula][1],formula)

    def test_p14_source_proof_survives_whole_paragraph_recovery(self):
        ti,para,fonts,script,_ = captured_rich_script_fixture()
        promoted,_ = guard._promote_rich_scripts(ti,para,fonts)
        engine = translator(["{v1}内部电感{PBKEEP_0001} {PBKEEP_0002}"])
        plain,output,_ = guard._whole_paragraph_translation(engine,promoted,ParagraphTranslateTracker())
        self.assertEqual(output,"{v1}内部电感L{v2} {v4}H")
        self.assertEqual(guard._source_script_bindings(plain),{"2":"L"})
        self.assertIs(plain.placeholders[1].formula.pdf_character[0],script)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError,"atomic-variable-pairing"):
            guard.normalize_output(plain,output.replace("L{v2}","I{v2}"))

    def test_same_id_different_xobj_and_duplicate_geometry_do_not_certify_scripts(self):
        for defect in ("xobj","duplicate","far","flat"):
            ti,para,fonts,script,bases = captured_rich_script_fixture()
            if defect=="xobj": bases[0].xobj_id=9
            elif defect=="duplicate": para.pdf_paragraph_composition[0].pdf_line.pdf_character.append(bases[0])
            elif defect=="far": script.box.x+=20
            else: script.box.y=bases[0].box.y
            with self.subTest(defect=defect),self.assertRaisesRegex(guard.PlaceholderIntegrityError,"source-small-script-geometry"):
                guard._promote_rich_scripts(ti,para,fonts)

    def test_subset_alias_requires_known_identical_face_and_source_proof_is_immutable(self):
        for name in (None,"AAAAAA+TimesNewRoman"):
            ti,para,fonts,script,_ = captured_rich_script_fixture()
            fonts["TT0"].name=name
            prepared,count=guard._promote_rich_scripts(ti,para,fonts)
            self.assertEqual(count,0)
            self.assertFalse(guard._source_script_bindings(prepared))
        for change in ("cid","font","box","object","offset"):
            ti,para,fonts,script,_=captured_rich_script_fixture()
            prepared,_=guard._promote_rich_scripts(ti,para,fonts)
            if change=="cid": script.pdf_character_id=106
            elif change=="font": script.pdf_style.font_id="other"
            elif change=="box": script.box.y+=1
            elif change=="offset": prepared.placeholders[1].formula.y_offset=0
            else: prepared.placeholders[1].formula=PdfFormula(pdf_character=[script])
            with self.subTest(change=change),self.assertRaisesRegex(guard.PlaceholderIntegrityError,"source-script-proof-changed"):
                guard.normalize_output(prepared,prepared.unicode)

    def test_p44_unit_power_promotes_original_glyph_and_keeps_original_formula(self):
        ti,para,fonts,script,_=captured_rich_script_fixture(unit=True)
        original=ti.placeholders[1].formula
        promoted,count=guard._promote_rich_scripts(ti,para,fonts)
        self.assertEqual(count,1)
        self.assertIn("1.5 mm{v1}",promoted.unicode)
        self.assertIs(promoted.placeholders[0].formula.pdf_character[0],script)
        self.assertIs(promoted.placeholders[1].formula,original)
        self.assertAlmostEqual(promoted.placeholders[0].formula.y_offset,3.634,places=4)
        self.assertFalse(guard._source_script_bindings(promoted))
        self.assertEqual([v for _,_,v in guard._measurement_spans(promoted,promoted.unicode)],["1.5 mm2","4 mm2"])
        output="接地连接截面（铜）为：2 x min. 1.5 mm{v1}或1 x min. 4 mm{v3}"
        parsed=translator().parse_translate_output(promoted,output)
        self.assertIs([c.pdf_formula for c in parsed if c.pdf_formula][0],promoted.placeholders[0].formula)
        for bad in (output.replace("1.5 mm{v1}","1.5 m{v1}"),output.replace("1.5 mm{v1}","4 mm{v1}"),
                    output.replace("mm{v1}","mm {v1}").replace("mm{v3}","m{v3}")):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError,"unit-exponent-pairing"):
                guard.normalize_output(promoted,bad)

    def test_p44_unit_binding_survives_whole_recovery_and_rejects_cross_measurement_move(self):
        ti,para,fonts,_,_=captured_rich_script_fixture(unit=True)
        promoted,_=guard._promote_rich_scripts(ti,para,fonts)
        engine=translator(["接地连接截面（铜）为：2 x min. {PBKEEP_0001}或1 x min. {PBKEEP_0002}"])
        plain,output,_=guard._whole_paragraph_translation(engine,promoted,ParagraphTranslateTracker())
        self.assertIs(plain.placeholders[0],promoted.placeholders[0])
        self.assertIn("1.5 mm{v1}",output)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError,"unit-exponent-pairing"):
            guard.normalize_output(plain,output.replace("1.5 mm{v1}","4 mm{v1}").replace("4 mm{v3}","1.5 mm{v3}"))

    def test_unit_power_requires_whole_unit_geometry_and_a_true_superscript(self):
        for defect in ("unit-gap","subscript","unit-family","unit-xobj"):
            ti,para,fonts,script,bases=captured_rich_script_fixture(unit=True)
            if defect=="unit-gap": bases[0].box.x2-=4
            elif defect=="subscript": script.box.y=bases[-1].box.y-3.63
            elif defect=="unit-family": bases[0].pdf_style=PdfStyle(font_id="unknown",font_size=11)
            else: bases[0].xobj_id=8
            with self.subTest(defect=defect),self.assertRaisesRegex(guard.PlaceholderIntegrityError,"source-unit-exponent-geometry"):
                guard._promote_rich_scripts(ti,para,fonts)

    def test_explicit_protocol_names_reject_actual_applicability_omission(self):
        # Actual P58 TI has no placeholders. Both protection techniques survived
        # translation, but the first protocol's applicability object disappeared.
        source = ('The mixed approach of the types of protection “increased safety (e)”/Division 2 wiring/ '
                  'installation techniques and “intrinsic safety (i)” are implemented for the PROFIBUS MBP '
                  'with the help of Segment Couplers and FieldBarriers for Zone 1/Division 2 applications '
                  'and Segment Couplers and Segment Protectors for Zone 2/Division 2 applications. '
                  'A Power Hub is used for the FOUNDATION Fieldbus H1 instead of a Segment Coupler. ')
        actual = ('对于Zone 1/Division 2应用，通过分段耦合器和场安全栅实现"增安型 (e)"/Division 2布线/安装技术与'
                  '"本质安全 (i)"保护类型的混合方法；对于Zone 2/Division 2应用，则采用分段耦合器和分段保护器。'
                  '对于FOUNDATION Fieldbus H1，使用电源集线器代替分段耦合器。')
        ti = ILTranslator.TranslateInput(source, [], PdfStyle())
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'protected-technical-name-changed'):
            guard.normalize_output(ti, actual)
        good = '对于PROFIBUS MBP，' + actual
        self.assertEqual(guard.normalize_output(ti, good), good)
        hint = guard._contextual_term_hint(translator(), ti)
        self.assertIn('clause describing what it applies to', hint)
        for bad in (good.replace('MBP', 'MBP-IS'), good.replace('H1', 'H2'),
                    good + ' PROFIBUS MBP', good.replace('PROFIBUS MBP', 'PROFIBUS')):
            with self.subTest(bad=bad), self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'protected-technical-name-changed'):
                guard.normalize_output(ti, bad)

    def test_protocol_names_have_complete_boundaries_and_preserve_formula_objects(self):
        ti = fixture()
        ti.placeholders[0].formula.pdf_character = [PdfCharacter(char_unicode='1')]
        ti.unicode = "Use <style id='2'>PROFIBUS</style> MBP-IS with FOUNDATION Fieldbus H{v1}."
        engine = translator(['使用{PBKEEP_0001}和{PBKEEP_0002}。'])
        plain, output, removed = guard._whole_paragraph_translation(engine, ti, ParagraphTranslateTracker())
        self.assertEqual(output, '使用PROFIBUS MBP-IS和FOUNDATION Fieldbus H{v1}。')
        self.assertEqual(removed, 1)
        self.assertIs(next(c.pdf_formula for c in engine.parse_translate_output(plain,output) if c.pdf_formula),
                      ti.placeholders[0].formula)
        self.assertTrue(engine.calls[0][0].endswith('Use {PBKEEP_0001} with {PBKEEP_0002}.'))
        for source in ('THE PROTECTION APPROACH', 'a PROFIBUS MBP-X adapter', 'xPROFIBUS MBP',
                       'FOUNDATION Fieldbus H10', 'a class of fieldbus systems', 'The red cable'):
            raw = ILTranslator.TranslateInput(source, [], PdfStyle())
            self.assertEqual(guard._technical_name_spans(raw,source), [], source)
        raw = ILTranslator.TranslateInput('PROFIBUS MBP-IS and PROFIBUS MBP', [], PdfStyle())
        self.assertEqual([v for _,_,v in guard._technical_name_spans(raw,raw.unicode)],
                         ['PROFIBUS MBP-IS','PROFIBUS MBP'])
        for dash in '-‐‑‒–—―−－':
            for source,output in (('PROFIBUS MBP','PROFIBUS MBP'+dash+'IS'),
                                  ('PROFIBUS MBP'+dash+'IS','PROFIBUS MBP')):
                raw=ILTranslator.TranslateInput(source,[],PdfStyle())
                with self.subTest(source=source,output=output),self.assertRaisesRegex(guard.PlaceholderIntegrityError,'protected-technical-name-changed'):
                    guard.normalize_output(raw,output)

    def test_reduced_variable_annotation_is_bound_and_survives_whole_recovery(self):
        ti = atomic_prose_fixture()
        # Same source form as P26: native small o, literal (red.), then a
        # formula whose first visible character is the equation's equals sign.
        ti.placeholders[1].formula.pdf_character = [PdfCharacter(char_unicode=c) for c in '= 0.5']
        ti.unicode = 'The output value is reduced: L{v1}(red.) {v2}.'
        good = '输出值降低为：L{v1}（red.） {v2}。'
        self.assertEqual(guard.normalize_output(ti,good),good)
        for bad in (good.replace('red.', '红'), good.replace('（red.）',''),
                    good.replace('L{v1}（red.）','（red.）L{v1}')):
            with self.subTest(bad=bad),self.assertRaisesRegex(guard.PlaceholderIntegrityError,'protected-reduction-annotation-changed'):
                guard.normalize_output(ti,bad)
        engine = translator(['输出值降低为：{PBKEEP_0001} {v2}。'])
        plain,output,_ = guard._whole_paragraph_translation(engine,ti,ParagraphTranslateTracker())
        self.assertEqual(output,'输出值降低为：L{v1}(red.) {v2}。')
        parsed = [c.pdf_formula for c in engine.parse_translate_output(plain,output) if c.pdf_formula]
        self.assertEqual(len(parsed),2)
        self.assertTrue(all(actual is ph.formula for actual,ph in zip(parsed,ti.placeholders)))
        self.assertTrue(engine.calls[0][0].endswith('The output value is reduced: {PBKEEP_0001} {v2}.'))

    def test_reduced_annotation_does_not_protect_color_names_or_ordinary_parentheses(self):
        for source in ('The red color is reduced.', 'Fred (red.) discussed a reduction.',
                       'The reduced label is L(red.).', 'The values are marked (red.) for reference.'):
            ti = ILTranslator.TranslateInput(source,[],PdfStyle())
            self.assertEqual(guard._reduction_annotation_spans(ti,source),[])
        for source in ('The color L{v1}(red.) = 1.', 'The reduced label L{v1}(red.) describes a cable.'):
            ti = atomic_prose_fixture()
            ti.unicode = source + ' I{v2}'
            self.assertEqual(guard._reduction_annotation_spans(ti,ti.unicode),[])
        ti = atomic_prose_fixture()
        ti.unicode = 'The value is reduced: L{v1}(red.) = 0.5. I{v2}(red.) is a color label.'
        self.assertEqual([v for _,_,v in guard._reduction_annotation_spans(ti,ti.unicode)], ['L{v1}(red.)'])
        output = '数值降低：L{v1}(red.) = 0.5。I{v2}（红）是颜色标签。'
        self.assertEqual(guard.normalize_output(ti,output), output)

    def test_standalone_reduced_calculation_preserves_actual_complete_source_equation(self):
        # Exact separate P26 Co TI: no preceding "reduced" word and no dot in red.
        base = PdfStyle(font_id='TT1',font_size=11)
        placeholders=[]
        for number,value in ((1,'o'),(2,'= 0'),(3,'5 ×'),(4,'o = 0'),(5,'5 × 2'),(6,'= 1')):
            chars=[PdfCharacter(char_unicode=c,pdf_style=PdfStyle(font_id='TT1',font_size=6.38 if number==1 else 11)) for c in value]
            placeholders.append(FormulaPlaceholder(number,PdfFormula(pdf_character=chars),f'{{v{number}}}',rf'\{{v{number}\}}'))
        ti=ILTranslator.TranslateInput('C{v1}(red) {v2}.{v3}C{v4}.{v5}.41 μF {v6}.205 μF',placeholders,base)
        self.assertTrue(guard._pure_technical_expression(ti))
        para=paragraph(ti)
        original=para.pdf_paragraph_composition
        with patch.dict(guard._originals,pre=lambda *a,**k:(ti.unicode,ti)):
            self.assertEqual(translator().pre_translate_paragraph(para,ParagraphTranslateTracker()),(None,None))
        self.assertIs(para.pdf_paragraph_composition,original)
        for prefix,suffix in (('If ',''),('', ' represents the red color.'),('The red color is ','')):
            other=ILTranslator.TranslateInput(prefix+ti.unicode+suffix,placeholders,base)
            self.assertFalse(guard._pure_technical_expression(other))
        for source in ('C{v1}(red) = 1 μF', 'C{v1}(red) + 0.5 × C{v4} = 1 μF',
                       'Circuit(red) = 0.5 × Circuit = 1 μF', 'C{v1}(red) = blue × C{v4} = 1 μF'):
            other=ILTranslator.TranslateInput(source,placeholders,base)
            self.assertFalse(guard._pure_technical_expression(other),source)


if __name__ == "__main__":
    unittest.main()
