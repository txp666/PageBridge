"""Portable source-structure tests; no private documents or model requests."""
import re
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_translation_guard import (
    guard, translator, paragraph, ILTranslator, ParagraphTranslateTracker,
    PdfStyle, PdfCharacter, PdfFormula, FormulaPlaceholder,
)


SOURCE_OR = ('Intrinsically safe and associated apparatus must at least meet the level of '
             'protection ic for Zone 2. Equipment intended for Division 2 must be rated '
             'for use in Division 1 or 2.')
TARGET_OR = ('本质安全型和关联设备必须至少满足Zone 2的ic防护等级。用于Division 2的设备'
             '必须额定用于Division 1或2。')
SOURCE_AND = ('The FieldBarrier spur outputs are intrinsically safe “ia” for connecting '
              'field devices in Zone 0 and 1/Division 1 and 2. The trunk connection is type '
              'of protection “increased safety (e)” or Division 2 wiring/installation '
              'techniques are implemented.')
TARGET_AND = ('FieldBarrier分支输出本质安全“ia”，用于连接Zone 0和1/Division 1及2中的现场设备。'
              '主干连接采用“增安型(e)”保护类型，或实施Division 2布线/安装技术。')


def ti(source):
    return ILTranslator.TranslateInput(source, [], PdfStyle())


class ClassificationConnectiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.install()

    def setUp(self):
        guard._states.clear()
        guard._counters.clear()

    def test_complete_paragraph_initial_post_preserves_source(self):
        for source, output in ((SOURCE_OR, TARGET_OR), (SOURCE_AND, TARGET_AND)):
            with self.subTest(source=source):
                prepared = ti(source)
                para, instance = paragraph(prepared), translator()
                before = prepared.unicode, prepared.placeholders, prepared.base_style
                instance.post_translate_paragraph(para, ParagraphTranslateTracker(), prepared, output)
                self.assertEqual(para.unicode, output)
                self.assertEqual((prepared.unicode, prepared.placeholders, prepared.base_style), before)
                self.assertEqual(instance.calls, [])
                self.assertEqual(guard.snapshot()['unresolvedCount'], 0)

    def test_equivalent_connectives_preserve_complete_classification_keys(self):
        for source, targets in (
            ('Zone 0 and 1/Division 1 and 2', ('Zone 0和1/Division 1及2', 'Zone 0与1/Division 1和2')),
            ('Division 1 or 2', ('Division 1或2', 'Division 1或者2')),
        ):
            for output in targets:
                with self.subTest(source=source, output=output):
                    self.assertEqual(guard.normalize_output(ti(source), output), output)

    def test_wrong_logic_values_ranges_labels_and_multiplicity_still_rejected(self):
        source = 'Zone 0 and 1/Division 1 or 2'
        for output in (
            'Zone 0或1/Division 1或2', 'Zone 0和1/Division 1和2',
            'Zone 0和1/Division 2或1', 'Zone 1和2/Division 0或1',
            'Zone 0/Division 1或2', 'Zone 0和1/Division 1或2或3',
            'Zone 0-1/Division 1或2', 'Zone 0和1, Division 1或2',
            'Division 0和1/Zone 1或2', '0区和1区/Division 1或2',
            'Zone 0和1/Division 1或2 Zone 0和1/Division 1或2',
        ):
            with self.subTest(output=output), self.assertRaisesRegex(
                    guard.PlaceholderIntegrityError, 'protected-classification-changed'):
                guard.normalize_output(ti(source), output)
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard.normalize_output(ti('Zone 0 to 2'), 'Zone 0和1和2')

    def test_fixed_label_contract_stays_exact(self):
        for source, output in (('Zone 2', '2区'), ('Class I, Div. 1', 'I类1类'),
                               ('Zone 1/Division 2', 'Zone 1和Division 2')):
            with self.subTest(source=source), self.assertRaises(guard.PlaceholderIntegrityError):
                guard.normalize_output(ti(source), output)
        self.assertEqual(guard.normalize_output(ti('Class I, Div. 1'), '用于Class I, Div. 1。'),
                         '用于Class I, Div. 1。')

    def test_full_paragraph_english_residue_retries_and_preserves_actual_source(self):
        for source, good, bad in (
            (SOURCE_OR, TARGET_OR, TARGET_OR.replace('1或2', '1 or 2')),
            (SOURCE_AND, TARGET_AND, TARGET_AND.replace('0和1', '0 and 1').replace('1及2', '1 and 2')),
        ):
            with self.subTest(source=source):
                prepared, instance = ti(source), translator([good])
                para = paragraph(prepared)
                instance.post_translate_paragraph(para, ParagraphTranslateTracker(), prepared, bad)
                self.assertEqual(para.unicode, good)
                self.assertEqual(prepared.unicode, source)
                self.assertEqual(len(instance.calls), 1)
                self.assertIn(source, instance.calls[0][0])
                self.assertIn('same logical relationship', instance.calls[0][0])
                self.assertEqual(guard.snapshot()['unresolvedCount'], 0)

    def test_initial_and_batch_hints_require_exact_source_input(self):
        prepared = ti(SOURCE_AND)
        instance = translator()
        with patch.dict(guard._originals, single_prompt_context=lambda self, text, **kw: text):
            prompt = guard._guard_single_prompt_context(instance, prepared.unicode, translate_input=prepared)
        self.assertIn('ordinary connective and as 和/及 and or as 或', prompt)
        self.assertTrue(prompt.endswith(SOURCE_AND))
        token = guard._review_batch.set([prepared])
        try:
            with patch.dict(guard._originals, batch_prompt_context=lambda self, text: text):
                prompt = guard._guard_batch_prompt_context(instance, json.dumps([{'id': 'a', 'input': SOURCE_AND}]))
                self.assertIn('ordinary connective and as 和/及 and or as 或', prompt)
                mismatch = guard._guard_batch_prompt_context(instance, json.dumps([{'id': 'a', 'input': 'Other source.'}]))
                self.assertNotIn('ordinary connective', mismatch)
        finally:
            guard._review_batch.reset(token)

    def test_bad_pairing_normal_post_uses_same_strict_gate(self):
        prepared, instance = ti(SOURCE_AND), translator([TARGET_AND])
        para = paragraph(prepared)
        instance.post_translate_paragraph(para, ParagraphTranslateTracker(), prepared,
                                          TARGET_AND.replace('0和1/Division 1及2', '1和2/Division 0及1'))
        self.assertEqual(para.unicode, TARGET_AND)
        self.assertEqual(len(instance.calls), 1)

    def test_whole_recovery_leaves_only_connective_visible(self):
        for source, good, opaque in (
            (SOURCE_OR, TARGET_OR,
             '本质安全型和关联设备必须至少满足{PBKEEP_0001}的ic防护等级。用于{PBKEEP_0002}的设备'
             '必须额定用于{PBKEEP_0003}或{PBKEEP_0004}。'),
            (SOURCE_AND, TARGET_AND,
             'FieldBarrier分支输出本质安全“ia”，用于连接{PBKEEP_0001}和{PBKEEP_0002}及{PBKEEP_0003}中的现场设备。'
             '主干连接采用“增安型(e)”保护类型，或实施{PBKEEP_0004}布线/安装技术。'),
        ):
            with self.subTest(source=source):
                prepared = ti(source)
                bad = good.replace('1或2', '1 or 2').replace('0和1', '0 and 1').replace('1及2', '1 and 2')
                instance = translator([bad, bad, opaque])
                para = paragraph(prepared)
                instance.post_translate_paragraph(para, ParagraphTranslateTracker(), prepared, bad)
                self.assertEqual(para.unicode, good)
                self.assertEqual(prepared.unicode, source)
                self.assertEqual(len(instance.calls), 3)
                protected_input = instance.calls[-1][0].split('Translate: ')[-1]
                self.assertRegex(protected_input, r'\{PBKEEP_\d+\} (?:and|or) \{PBKEEP_\d+\}')
                self.assertNotIn('Division', protected_input)
                self.assertNotIn('Zone', protected_input)

    def test_recovery_tokens_cannot_be_dropped_duplicated_or_reassociated(self):
        prepared = ti('Use Zone 0 and 1/Division 1 or 2 safely.')
        for output in ('使用{PBKEEP_0001}和{PBKEEP_0002}或{PBKEEP_0002}。',
                       '使用{PBKEEP_0001}和{PBKEEP_0002}。',
                       '使用{PBKEEP_0002}和{PBKEEP_0001}或{PBKEEP_0003}。',
                       '使用{PBKEEP_0001}或{PBKEEP_0002}和{PBKEEP_0003}。'):
            with self.subTest(output=output), self.assertRaises(guard.PlaceholderIntegrityError):
                guard._whole_paragraph_translation(translator([output]), prepared, ParagraphTranslateTracker())

    def test_native_formula_connective_remains_opaque(self):
        chars = [PdfCharacter(char_unicode=c) for c in 'Division 1 or 2']
        formula = FormulaPlaceholder(1, PdfFormula(pdf_character=chars), '{v1}', r'\{v1\}')
        prepared = ILTranslator.TranslateInput('Use {v1} safely.', [formula], PdfStyle())
        self.assertEqual(guard._classification_prose_connectors(prepared, prepared.unicode), [])
        self.assertEqual(guard._classification_prose_hint(prepared), '')
        instance = translator(['安全使用{PBKEEP_0001}。'])
        plain, output, _ = guard._whole_paragraph_translation(instance, prepared, ParagraphTranslateTracker())
        actual = instance.parse_translate_output(plain, output)
        self.assertIs(next(c.pdf_formula for c in actual if c.pdf_formula), formula.formula)
        self.assertEqual(formula.formula.pdf_character, chars)
        self.assertEqual(output, '安全使用{v1}。')

    def test_formula_number_can_neighbor_translatable_plain_connective(self):
        formula = FormulaPlaceholder(1, PdfFormula(pdf_character=[PdfCharacter(char_unicode='1')]),
                                     '{v1}', r'\{v1\}')
        prepared = ILTranslator.TranslateInput('Use Division {v1} or 2 safely.', [formula], PdfStyle())
        instance = translator(['安全使用{PBKEEP_0001}或{PBKEEP_0002}。'])
        plain, output, _ = guard._whole_paragraph_translation(instance, prepared, ParagraphTranslateTracker())
        self.assertEqual(output, '安全使用Division {v1}或2。')
        actual = instance.parse_translate_output(plain, output)
        self.assertIs(next(c.pdf_formula for c in actual if c.pdf_formula), formula.formula)

    def test_exact_code_and_quoted_classifications_are_not_opened(self):
        for literal in ('`Zone 0 and 1`', '"Division 1 or 2"', '“Zone 0 and 1”',
                        '<code>Division 1 or 2</code>'):
            prepared = ti('The literal is ' + literal + '.')
            self.assertEqual(guard._classification_prose_connectors(prepared, prepared.unicode), [])
            self.assertEqual(guard._classification_prose_hint(prepared), '')
            spans = guard._classification_literal_spans(prepared, prepared.unicode)
            self.assertEqual(len(spans), 1)
            self.assertRegex(prepared.unicode[spans[0][0]:spans[0][1]], r'and|or')

    def test_unrelated_words_and_non_chinese_output_do_not_trigger(self):
        prepared = ti('A class of methods uses division by 2 and multiplication by 3.')
        self.assertEqual(guard._classification_prose_connectors(prepared, prepared.unicode), [])
        self.assertEqual(guard._classification_prose_hint(prepared), '')
        instance = translator()
        instance.translation_config.lang_out = 'de'
        prepared = ti(SOURCE_OR)
        guard._check_body_translation(instance, paragraph(prepared), prepared, SOURCE_OR)
        for prefix in ('Time', '_', '2'):
            self.assertEqual(guard._classification_spans(prepared, prefix + 'Zone 0 and 1'), [])


if __name__ == '__main__':
    unittest.main()
