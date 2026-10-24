"""Source-positive retries for actual Wind v5 prose defects (no model/network)."""
import unittest

try:
    from .test_translation_guard import (
        guard, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter, fixture,
    )
except ImportError:
    from test_translation_guard import (
        guard, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter, fixture,
    )


def source(text, formulas=()):
    return ILTranslator.TranslateInput(text, list(formulas), PdfStyle())


def formula(number, text):
    return FormulaPlaceholder(number, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in text]),
                              f'{{v{number}}}', rf'\{{v{number}\}}')


class SourcePositiveEnglishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.install()

    def setUp(self):
        guard._states.clear()
        guard._counters.clear()

    def check(self, ti, output, label='plain text'):
        para = paragraph(ti)
        para.layout_label = label
        guard._check_body_translation(translator(), para, ti, output)

    def test_preceding_and_active_passive_verb_residuals_are_source_attested(self):
        for original, bad, good in (
            ('The sequences preceding each one of the CWT classes are shown.',
             '给出了各CWT类别的preceding序列。', '给出了各CWT类别之前的序列。'),
            ('Vectors in a cluster can precede different LC6 or one LC6 can be preceded by different 5BLC clusters.',
             '簇中的向量可precede不同的LC6，或一个LC6可由不同的5BLC簇先行。',
             '簇中的向量可先于不同的LC6，或一个LC6之前可出现不同的5BLC簇。'),
            ('The sequence was preceded by another wind.',
             '该序列被另一个风preceded。', '另一个风出现在该序列之前。'),
            ('The correlation allows giving wind sequences previous to each LC.',
             '该相关性可给出每个LC的preceding风序列。', '该相关性可给出每个LC之前的风序列。'),
        ):
            ti = source(original)
            with self.subTest(source=original):
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
                    self.check(ti, bad)
                self.check(ti, good)

    def test_preceding_does_not_reclassify_names_identifiers_urls_or_formulas(self):
        for original, good in (
            ('The PRECEDE model is applied.', '使用了PRECEDE模型。'),
            ('The Preceding Institute provides data.', '数据由Preceding Institute提供。'),
            ('The model preceding_state is used.', '使用preceding_state模型。'),
            ('See https://example.org/preceding for data.', '数据见https://example.org/preceding。'),
            ('The predictor was evaluated.', 'preceding模型用于评估。'),
        ):
            ti = source(original)
            with self.subTest(source=original):
                self.assertEqual(guard._ordinary_english_residuals(ti, good), [])
        ti = source('The label {v1} is retained.', [formula(1, 'preceding')])
        self.check(ti, '标签{v1}保持不变。')

    def test_section_references_require_source_grammar_and_retry_english(self):
        for original in (
            'The distribution is smoothed (see in METHODS).',
            'The distribution is smoothed (see METHODS).',
            'We show in METHODS how the matrix is calculated.',
            'In the section of METHODS we show the process in detail.',
        ):
            ti = source(original)
            with self.subTest(source=original):
                self.assertEqual(guard._source_section_references(ti), ['METHODS'])
                for section in ('METHODS', 'Methods', 'methods'):
                    with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
                        self.check(ti, '计算方法见' + section + '。')
                self.check(ti, '计算过程详见方法部分。')
                self.assertIn('names the paper section 方法', guard._contextual_term_hint(translator(), ti))

    def test_section_acronyms_partial_names_and_urls_remain_opaque(self):
        for original in (
            'The METHODS predictor was used.',
            'The matrix (see in METHODS2) was used.',
            'The matrix (see in METHODS_extra) was used.',
            'The matrix (see in METHODS OF ANALYSIS) was used.',
            'The matrix see in METHODS was used.',
            'See https://example.org/(see%20in%20METHODS).',
        ):
            ti = source(original)
            with self.subTest(source=original):
                self.assertEqual(guard._source_section_references(ti), [])
                self.assertNotIn('METHODS', guard._ordinary_english_residuals(ti, '采用METHODS。'))

    def test_wind_requires_meteorological_title_grammar_and_title_layout(self):
        for original in (
            'Wind Data Mining by Kohonen Neural Networks',
            'Wind Data Analysis using Regression',
            'Wind Speed Prediction with Neural Networks',
            'Wind Measurements for Coastal Forecasting',
            'Wind Measurements',
        ):
            ti = source(original)
            with self.subTest(source=original):
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
                    self.check(ti, 'Wind数据的研究', 'title')
                self.check(ti, '风数据的研究', 'title')
                self.check(ti, 'Wind数据的研究', 'reference')
                self.assertEqual(guard._ordinary_english_residuals(ti, 'Wind数据的研究', 'plain_text'), [])
        for original in ('Wind River Systems', 'Wind Data Toolkit', 'Wind',
                         'Citation: Fayos (2007) Wind Data Mining by Kohonen Neural Networks.',
                         'The journal "Wind Data Mining" is indexed.'):
            with self.subTest(proper_name=original):
                self.assertFalse(guard._wind_data_title(source(original)))

    def test_spaced_source_caption_rejects_joined_lowercase_or_abbreviated_output(self):
        for original, outputs, good in (
            ('Table 7. Wind prediction from the previous five days.',
             ('Table 7. 风预测。', 'Table7. 风预测。', 'table7. 风预测。'), '表7。风预测。'),
            ('Figure 7. Wind prediction.', ('Fig.7. 风预测。', 'figure7. 风预测。'), '图7。风预测。'),
            ('Fig. 7. Wind prediction.', ('Figure7. 风预测。', 'Fig. 7. 风预测。'), '图7。风预测。'),
        ):
            ti = source(original)
            self.check(ti, good, 'table_caption')
            for bad in outputs:
                with self.subTest(source=original, output=bad):
                    with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
                        self.check(ti, bad, 'table_caption')

    def test_caption_detection_masks_formula_urls_and_preserves_identifier_boundaries(self):
        for original, output, placeholders in (
            ('The label {v1} is retained.', '标签{v1}保留。', [formula(1, 'Table 7')]),
            ('The data are at https://example.org/Table%207.', '数据见https://example.org/Table%207。', []),
            ('Table7 gene expression is measured.', 'Table7基因表达已测量。', []),
            ('The table model Table7A is evaluated.', '表格模型Table7A已评估。', []),
        ):
            ti = source(original, placeholders)
            with self.subTest(source=original):
                self.assertEqual(guard._untranslated_numbered_labels(ti, output), [])
                self.check(ti, output)
        ti = source('Table 7. Wind prediction.')
        for output in ('Table70', 'Table7A', 'modelTable7', 'Table7.5', 'Figure7'):
            self.assertEqual(guard._untranslated_numbered_labels(ti, output + '的预测。'), [])

    def test_styled_caption_and_word_keep_full_source_proof(self):
        ti = fixture()
        ti.unicode = "<style id='2'>Table 7.</style> Wind prediction uses {v1}."
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
            self.check(ti, "<style id='2'>Table7.</style> 风预测使用{v1}。", 'table_caption')
        self.check(ti, "<style id='2'>表7。</style> 风预测使用{v1}。", 'table_caption')

    def test_plain_label_with_opaque_numeric_formula_still_requires_translation(self):
        ti = source('Table {v1}. Wind prediction.', [formula(1, '7')])
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
            self.check(ti, 'Table{v1}。风预测。', 'table_caption')
        self.check(ti, '表{v1}。风预测。', 'table_caption')

    def test_actual_ordinal_formula_date_keeps_month_and_day_link(self):
        # Wind P3 actual TI prefix and four original chars: 6,t,h,space.
        ti = source('Then, if January {v1}is the target day, the predictor pair is used.', [formula(1, '6th ')])
        self.assertEqual(guard._source_month_dates(ti), [('January', '{v1}', 6)])
        for month in ('1月', '一月', '元月'):
            self.check(ti, f'那么，如果{month}{{v1}}是目标日，就使用预测向量对。')
        for bad in ('那么，如果{v1}是目标日，就使用预测向量对。',
                    '那么，如果2月{v1}是目标日，就使用预测向量对。',
                    '那么，如果11月{v1}是目标日，就使用预测向量对。',
                    '那么，如果十一月{v1}是目标日，就使用预测向量对。',
                    '那么，如果January {v1}是目标日，就使用预测向量对。',
                    '一月的研究表明，如果{v1}是目标日，就使用预测向量对。'):
            with self.subTest(output=bad), self.assertRaisesRegex(
                    guard.PlaceholderIntegrityError, 'calendar-month-or-day-changed'):
                self.check(ti, bad)

    def test_literal_date_allows_natural_chinese_day_without_formula_rewrite(self):
        ti = source('Then, if January 6th is the target day, the pair is used.')
        for output in ('如果1月6日是目标日，就使用该向量对。', '如果一月六日是目标日，就使用该向量对。'):
            self.check(ti, output)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'calendar-month-or-day-changed'):
            self.check(ti, '如果1月7日是目标日，就使用该向量对。')

    def test_month_gate_does_not_open_general_formulas_or_assume_model_names_are_dates(self):
        for original, placeholders in (
            ('The January 6 model is used.', []),
            ('Then, if January {v1}is the target day.', []),
            ('Then, if January {v1}is the target day.', [formula(1, '6x')]),
            ('Then, if January {v1}is the target day.', [formula(1, '6th+7th')]),
            ('Then, if January 6st is the target day.', []),
            ('Then, if January 32nd is the target day.', []),
            ('The address is https://example.org/January/6th.', []),
            ('The date {v1} is used.', [formula(1, 'January 6th')]),
        ):
            with self.subTest(source=original):
                self.assertEqual(guard._source_month_dates(source(original, placeholders)), [])

    def test_month_retry_keeps_original_formula_object_and_does_not_patch_text(self):
        ti = source('Then, if January {v1}is the target day, the pair is used.', [formula(1, '6th ')])
        good = '那么，如果一月{v1}是目标日，就使用该向量对。'
        engine = translator([good])
        para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, '那么，如果{v1}是目标日，就使用该向量对。')
        self.assertEqual(para.unicode, good)
        self.assertEqual(len(engine.calls), 1)
        self.assertIn('January {v1} includes the calendar month 1月', engine.calls[0][0])
        self.assertIs(next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula), ti.placeholders[0].formula)

    def test_production_retries_each_source_positive_residual(self):
        for original, bad, good, label in (
            ('The wind sequences previous to each LC are shown.', '每个LC的preceding风序列已给出。',
             '每个LC之前的风序列已给出。', 'plain text'),
            ('We show in METHODS how the matrix is calculated.', '我们在METHODS中展示矩阵计算。',
             '我们在方法部分展示矩阵计算。', 'plain text'),
            ('Wind Data Mining by Kohonen Neural Networks', '利用Kohonen神经网络进行Wind数据挖掘',
             '利用Kohonen神经网络进行风数据挖掘', 'title'),
            ('Table 7. Wind prediction from the previous five days.', 'Table7. 根据前五天数据预测风。',
             '表7。根据前五天数据预测风。', 'table_caption'),
        ):
            ti = source(original)
            para = paragraph(ti)
            para.layout_label = label
            engine = translator([good])
            with self.subTest(source=original):
                engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, bad)
                self.assertEqual(para.unicode, good)
                self.assertEqual(len(engine.calls), 1)
                self.assertTrue(engine.calls[0][1]['ignore_cache'])

    def test_failed_month_retries_never_publish_a_partially_repaired_paragraph(self):
        ti = source('Then, if January {v1}is the target day, the pair is used.', [formula(1, '6th ')])
        bad = '那么，如果{v1}是目标日，就使用该向量对。'
        para = paragraph(ti)
        original_compositions = para.pdf_paragraph_composition
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, 'unresolved-paragraph'):
            translator([bad, bad, bad]).post_translate_paragraph(para, ParagraphTranslateTracker(), ti, bad)
        self.assertEqual(para.unicode, ti.unicode)
        self.assertIs(para.pdf_paragraph_composition, original_compositions)
        self.assertEqual(guard.snapshot()['unresolvedCount'], 1)

    def test_reference_metadata_non_chinese_and_diagram_names_remain_allowed(self):
        ti = source('Received October 10, 2006; Accepted January 17, 2007; Published February 14, 2007')
        self.check(ti, ti.unicode)
        ti = source('The sequences preceding each one are shown (see in METHODS).')
        self.check(ti, '图中给出preceding序列（METHODS）。', 'reference')
        engine = translator()
        engine.translation_config.lang_out = 'de'
        guard._check_body_translation(engine, paragraph(ti), ti, ti.unicode)
        for label in ('formula', 'equation', 'isolate_formula', 'code'):
            self.check(ti, '图中给出preceding序列（METHODS）。', label)


if __name__ == '__main__':
    unittest.main()
