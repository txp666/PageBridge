"""Independent production-path repros from the v6 guard review.

The fake engine returns a complete corrected paragraph when a guard requests
one. These tests require the actual retry/publication path, not merely matching
a private regex. No network, model or local user cache is used.
"""
import unittest

try:
    from .test_translation_guard import (
        guard as g, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter,
    )
except ImportError:
    from test_translation_guard import (
        guard as g, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter,
    )


def ti(text, formulas=()):
    return ILTranslator.TranslateInput(text, list(formulas), PdfStyle())


def formula(number, text):
    return FormulaPlaceholder(number, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in text]),
                              f'{{v{number}}}', rf'\{{v{number}\}}')


class GuardReviewBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def setUp(self):
        g._states.clear()
        g._counters.clear()

    def publish(self, source, output, responses=(), label='plain text'):
        p = paragraph(source)
        p.layout_label = label
        engine = translator(responses)
        engine.post_translate_paragraph(p, ParagraphTranslateTracker(), source, output)
        return p, engine

    def assert_retries(self, source, bad, good, label='plain text'):
        p, engine = self.publish(source, bad, [good], label)
        self.assertEqual(p.unicode, good)
        self.assertEqual(len(engine.calls), 1)
        self.assertTrue(engine.calls[0][1]['ignore_cache'])
        return p, engine

    def assert_accepted(self, source, good, label='plain text'):
        p, engine = self.publish(source, good, label=label)
        self.assertEqual(p.unicode, good)
        self.assertEqual(engine.calls, [])
        return p

    def test_new_unclosed_prose_emphasis_requires_retry(self):
        source = ti('The result agrees with the prediction.')
        for bad in ('结果与**预测一致。', '结果与预测**一致。'):
            with self.subTest(output=bad):
                self.assert_retries(source, bad, '结果与预测一致。')

    def test_new_mixed_cjk_identifier_emphasis_requires_retry_without_deleting_content(self):
        source = ti('The result matches model A.')
        self.assert_retries(source, '结果与**模型A**一致。', '结果与模型A一致。')

    def test_source_literal_markup_inline_code_and_formula_power_remain_allowed(self):
        self.assert_accepted(ti('The source uses **model A** as its label.'),
                             '原文使用**model A**作为标签。')
        self.assert_accepted(ti('A ** marker identifies the footnote.'),
                             '**标记用于识别脚注。')
        self.assert_accepted(ti('Preserve `**模型A**` as the literal code.'),
                             '将`**模型A**`保留为字面代码。')
        power = formula(1, 'x**2')
        p = self.assert_accepted(ti('The expression is {v1}.', [power]), '表达式为{v1}。')
        self.assertTrue(any(c.pdf_formula is power.formula for c in p.pdf_paragraph_composition))

    def test_wind_title_residual_is_case_insensitive(self):
        source = ti('Wind Data Mining by Kohonen Neural Networks')
        good = '基于Kohonen神经网络的风数据挖掘'
        for residual in ('Wind', 'wind', 'WIND'):
            with self.subTest(residual=residual):
                self.assert_retries(source, f'基于Kohonen神经网络的{residual}数据挖掘', good, 'title')

    def test_wind_company_name_citation_and_identifier_do_not_become_title_prose(self):
        self.assert_accepted(ti('Wind River Systems'), 'Wind River Systems', 'title')
        self.assert_accepted(ti('The WindToolkit API is available.'), 'WindToolkit API可用。')
        citation = ti('Fayos (2007) Wind Data Mining by Kohonen Neural Networks.')
        self.assert_accepted(citation, citation.unicode, 'reference')

    def test_single_digit_caption_number_change_requires_retry(self):
        for label, noun, translated in (('table_caption', 'Table', '表'), ('figure_caption', 'Figure', '图')):
            source = ti(f'{noun} 7. Wind prediction from previous days.')
            with self.subTest(label=label):
                self.assert_retries(source, f'{translated}8。根据前几天预测风。',
                                    f'{translated}7。根据前几天预测风。', label)

    def test_correct_caption_year_and_formula_numbers_keep_separate_roles(self):
        f = formula(1, 'R7 = 1994')
        source = ti('Table 7. Samples collected in 1994 use {v1}.', [f])
        p = self.assert_accepted(source, '表7。1994年采集的样本使用{v1}。', 'table_caption')
        self.assertTrue(any(c.pdf_formula is f.formula for c in p.pdf_paragraph_composition))
        self.assert_accepted(ti('The code Table7 is used in 1994.'), '1994年使用代码Table7。')
        opaque = formula(1, 'Table 7')
        self.assert_accepted(ti('In 1994 the formula {v1} is evaluated.', [opaque]),
                             '1994年计算公式{v1}。')

    def test_positive_non_equilibrium_cannot_be_satisfied_by_its_explicit_negation(self):
        source = ti('The analysis matches non-equilibrium critical phenomena.')
        good = '该分析与非平衡临界现象吻合。'
        for bad in ('该分析与平衡临界现象吻合，而不是非平衡临界现象。',
                    '该分析与平衡临界现象吻合，并非非平衡临界现象。'):
            with self.subTest(output=bad):
                self.assert_retries(source, bad, good)

    def test_source_negating_non_equilibrium_does_not_require_affirming_it(self):
        self.assert_accepted(ti('The analysis does not match non-equilibrium critical phenomena.'),
                             '该分析并不符合非平衡临界现象。')
        self.assert_accepted(ti('These are not non-equilibrium critical phenomena.'),
                             '这些并非非平衡临界现象。')
        self.assert_accepted(ti('We compare equilibrium and non-equilibrium critical phenomena.'),
                             '我们比较平衡与非平衡临界现象。')

    def test_month_fen_is_valid_chinese_date_grammar(self):
        source = ti('Then, if January 6th is the target day, the pair is used.')
        for good in ('那么，如果一月份的6日是目标日，就使用该向量对。',
                     '那么，如果1月份的6日是目标日，就使用该向量对。',
                     '那么，如果元月份的6日是目标日，就使用该向量对。'):
            with self.subTest(output=good):
                self.assert_accepted(source, good)

    def test_month_fen_preserves_actual_original_day_formula(self):
        day = formula(1, '6th ')
        source = ti('Then, if January {v1}is the target day, the pair is used.', [day])
        good = '那么，如果一月份的{v1}是目标日，就使用该向量对。'
        p = self.assert_accepted(source, good)
        self.assertTrue(any(c.pdf_formula is day.formula for c in p.pdf_paragraph_composition))
        self.assert_retries(source, '那么，如果二月份的{v1}是目标日，就使用该向量对。', good)


if __name__ == '__main__':
    unittest.main()
