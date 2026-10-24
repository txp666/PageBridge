"""Regression coverage for complete Wind paragraphs rejected in v6."""
import unittest

try:
    from .test_guard_review_boundaries import g, ti, formula, translator, paragraph, ParagraphTranslateTracker
except ImportError:
    from test_guard_review_boundaries import g, ti, formula, translator, paragraph, ParagraphTranslateTracker


def prediction_source(name='CWT'):
    day = formula(1, '6th ')
    text = (f'The observed correlation between both five-day {name} sequences and the{name} of the{{v1}}day, '
            f'in the long 20-year period, can be generalized to predict the last from the previous {name} '
            'sequence in a different test period, like 1995.')
    return ti(text, [day])


def recovered_prediction(*, offset=0):
    start, target = (f'{{PBKEEP_{n + offset:04d}}}' for n in (1, 2))
    return (f'在{start}年期间，五天CWT序列与{{v1}}日的CWT相关，'
            f'可根据前序CWT序列预测{target}年该日的CWT。')


def recover(source, response):
    engine = translator([response])
    plain, output, _ = g._whole_paragraph_translation(engine, source, ParagraphTranslateTracker())
    g._check_body_translation(engine, paragraph(source), plain, output)
    return plain, output, engine


class WeatherProseRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def setUp(self):
        g._states.clear()
        g._counters.clear()

    def test_class_count_is_proven_in_both_source_grammars(self):
        for text, label in (
            ('The most probable wind sequences preceding each one of the 27 CWT Lamb classes are shown.', 'CWT Lamb'),
            ('The most probable wind sequences previous to each one of the 27 LC’s are shown.', 'LC'),
        ):
            source = ti(text)
            self.assertEqual([m.groupdict() for m in g._source_weather_sequence_groups(source)],
                             [dict(count='27', classes=label)])
            note = g._source_weather_retry_note(translator([]), source)
            self.assertIn('27个' + label + '类别', note)
            self.assertNotIn('28', note)
        self.assertEqual(g._source_weather_sequence_groups(ti('The 27LC model predicts wind sequences.')), [])

    def test_count_omission_or_duplication_stays_rejected(self):
        source = ti('The wind sequences previous to each one of the 27 LC’s are shown.')
        for bad in ('给出每个LC类别之前的风序列。', '给出27个LC类别中每一类之前的27个风序列。'):
            with self.subTest(output=bad), self.assertRaisesRegex(g.PlaceholderIntegrityError, 'prose-numeric-literal-changed'):
                g._check_body_translation(translator([]), paragraph(source), source, bad)
        g._check_body_translation(translator([]), paragraph(source), source, '给出27个LC类别中每一类之前的风序列。')

    def test_meteorological_expansion_is_translatable_but_cwt_remains(self):
        source = ti('Time series of Circulation Weather Type (CWT) are classified.')
        self.assertEqual(g._ordinary_english_residuals(source, 'Circulation Weather Type（CWT）的时间序列被分类。'),
                         ['Circulation Weather Type'])
        g._check_body_translation(translator([]), paragraph(source), source, '环流天气类型（CWT）的时间序列被分类。')
        self.assertFalse(g._source_circulation_weather_term(ti('Circulation Weather Type Toolkit')))

    def test_prompt_view_uses_extracted_antecedent_and_never_mutates_ti(self):
        for name in ('CWT', 'RWT'):
            source = prediction_source(name)
            original = source.unicode
            original_formula = source.placeholders[0].formula
            view = g._source_prediction_prompt_view(source, original)
            expected = original.replace('predict the last from', f'predict the {name} of that day from')
            self.assertEqual(view, expected)
            self.assertEqual(source.unicode, original)
            self.assertIs(source.placeholders[0].formula, original_formula)
            self.assertEqual(g._prose_numeric_literals(source, original), g._prose_numeric_literals(source, view))
            self.assertEqual(g._FORMULA.findall(original), g._FORMULA.findall(view))
            self.assertEqual(g._source_prediction_prompt_view(source, view), view)

    def test_unproven_or_ambiguous_anaphora_stays_byte_for_byte(self):
        valid = prediction_source()
        cases = [ti('We predict the last from the previous CWT sequence.'),
                 ti(valid.unicode, [formula(1, 'x + 6')]),
                 ti(valid.unicode.replace('between both', 'after'), valid.placeholders),
                 ti(valid.unicode + ' We predict the last from the previous CWT sequence.', valid.placeholders)]
        for source in cases:
            with self.subTest(source=source.unicode):
                self.assertEqual(g._source_prediction_prompt_view(source, source.unicode), source.unicode)

    def test_intervening_third_quantity_does_not_prove_the_prediction_object(self):
        original = prediction_source()
        for adjunct in ('as well as the next CWT sequence in a 20-year period,',
                        'in the long 20-year period and the next CWT sequence,',
                        'after considering a second CWT sequence for 20 years,'):
            source = ti(original.unicode.replace('in the long 20-year period,', adjunct), original.placeholders)
            with self.subTest(adjunct=adjunct):
                self.assertIsNone(g._source_sequence_prediction_roles(source))
                self.assertEqual(g._source_prediction_prompt_view(source, source.unicode), source.unicode)
                # This ambiguous source must not impose the earlier pair's
                # second member as the only permissible prediction object.
                g._check_weather_prediction_roles(source, '可根据前序CWT序列预测最后一个序列。')
        third = ti(original.unicode.replace('five-day CWT sequences',
                                            'five-day CWT sequences and six-day CWT sequences'), original.placeholders)
        self.assertIsNone(g._source_sequence_prediction_roles(third))
        self.assertEqual(g._source_prediction_prompt_view(third, third.unicode), third.unicode)

    def test_only_duration_or_no_adjunct_can_intervene_in_prediction_relation(self):
        original = prediction_source()
        for middle in (', in the long 40-year period, ', ', in the 30-year period, ', ', ', ' '):
            source = ti(original.unicode.replace(', in the long 20-year period, ', middle), original.placeholders)
            with self.subTest(middle=middle):
                self.assertIsNotNone(g._source_sequence_prediction_roles(source))
                view = g._source_prediction_prompt_view(source, source.unicode)
                self.assertIn('predict the CWT of that day from the previous CWT sequence', view)
                self.assertEqual(g._prose_numeric_literals(source, source.unicode), g._prose_numeric_literals(source, view))

    def test_original_source_still_rejects_observed_wrong_prediction_objects(self):
        source = prediction_source()
        for output, category in (
            ('在20年中，五天CWT序列与{v1}日的CWT相关，可根据前序CWT序列预测1995年最后一个序列。', 'prediction-output-role-changed'),
            ('在20年中，两个五天CWT序列与{v1}日的CWT相关，可根据前序CWT序列预测1995年该日的CWT。', 'coordinated-quantities-miscounted'),
        ):
            with self.subTest(output=output), self.assertRaisesRegex(g.PlaceholderIntegrityError, category):
                g._check_body_translation(translator([]), paragraph(source), source, output)

    def test_good_prediction_preserves_original_formula_and_all_numeric_roles(self):
        source = prediction_source()
        good = '在20年期间，五天CWT序列与{v1}日的CWT的相关性，可用于根据前序CWT序列预测1995年该日的CWT。'
        p, engine = paragraph(source), translator([])
        engine.post_translate_paragraph(p, ParagraphTranslateTracker(), source, good)
        self.assertEqual(p.unicode, good)
        self.assertEqual(engine.calls, [])
        self.assertTrue(any(c.pdf_formula is source.placeholders[0].formula for c in p.pdf_paragraph_composition))

    def test_prediction_numeric_recovery_retains_source_year_and_formula(self):
        source = prediction_source()
        plain, output, engine = recover(source, recovered_prediction())
        self.assertIn('20年期间', output)
        self.assertIn('预测1995年该日的CWT', output)
        self.assertEqual(g._counters['predictionRecoveryNumericProtectedParagraphs'], 1)
        comps = engine.parse_translate_output(plain, output)
        self.assertTrue(any(c.pdf_formula is source.placeholders[0].formula for c in comps))
        self.assertIn('predict the CWT of that day from the previous CWT sequence', engine.calls[0][0])
        self.assertEqual(source.unicode, prediction_source().unicode)

    def test_prediction_recovery_rejects_missing_replaced_and_duplicated_numeric_atoms(self):
        source = prediction_source()
        good = recovered_prediction()
        for bad in (good.replace('{PBKEEP_0002}', ''), good.replace('{PBKEEP_0002}', '1995'),
                    good + '{PBKEEP_0002}', good.replace('{PBKEEP_0002}', '{PBKEEP_0003}')):
            with self.subTest(output=bad), self.assertRaisesRegex(g.PlaceholderIntegrityError, 'protected-literal-token-changed'):
                recover(source, bad)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'prose-numeric-literal-changed'):
            recover(source, good + '1995年。')

    def test_prediction_anchor_does_not_relax_formula_or_nonnumeric_atom_order(self):
        source = prediction_source()
        extra = formula(2, 'x+y')
        source = ti(source.unicode + ' Then use {v2}.', source.placeholders + [extra])
        good = recovered_prediction() + '然后使用{v2}。'
        plain, output, engine = recover(source, good)
        objects = [c.pdf_formula for c in engine.parse_translate_output(plain, output) if c.pdf_formula]
        self.assertTrue(all(a is b.formula for a, b in zip(objects, source.placeholders)))
        bad = good.replace('{v1}', '{temp}').replace('{v2}', '{v1}').replace('{temp}', '{v2}')
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'formula-count-or-order'):
            recover(source, bad)
        source = ti('Use Zone 10 then Class I, Div. 2. ' + prediction_source().unicode, prediction_source().placeholders)
        good = '使用{PBKEEP_0001}，然后使用{PBKEEP_0002}。' + recovered_prediction(offset=2)
        recover(source, good)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'protected-literal-token-changed'):
            recover(source, good.replace('{PBKEEP_0001}', '{temp}').replace('{PBKEEP_0002}', '{PBKEEP_0001}').replace('{temp}', '{PBKEEP_0002}'))

    def test_nonmatching_prediction_source_keeps_visible_numbers_without_anchor(self):
        source = prediction_source()
        source = ti(source.unicode.replace('between both', 'after'), source.placeholders)
        good = '20年期间采用五天CWT序列与{v1}日的CWT，根据前序CWT序列预测1995年该日的CWT。'
        _, output, engine = recover(source, good)
        self.assertEqual(output, good)
        self.assertNotIn('{PBKEEP_0001}', engine.calls[0][0])
        self.assertEqual(g._counters['predictionRecoveryNumericProtectedParagraphs'], 0)


    def test_actual_complete_wind_abstract_keeps_request_view_and_original_validation(self):
        # Frozen actual source/accepted output fixture; no model or validation-file dependency.
        text = 'Time series of Circulation Weather Type (CWT), including daily averaged wind direction and vorticity, are self-classified by similarity using Kohonen Neural Networks (KNN). It is shown that KNN is able to map by similarity all 7300 five-day CWT sequences during the period of 1975–94, in London, United Kingdom. It gives, as a first result, the most probable wind sequences preceding each one of the 27 CWT Lamb classes in that period. Inversely, as a second result, the observed diffuse correlation between both five-day CWT sequences and theCWT of the{v1}day, in the long 20-year period, can be generalized to predict the last from the previous CWT sequence in a different test period, like 1995, as both time series are similar. Although the average prediction error is comparable to that obtained by forecasting standard methods, the KNN approach gives complementary results, as they depend only on an objective classification of observed CWT data, without any model assumption. The 27 CWT of the Lamb Catalogue were coded with binary three-dimensional vectors, pointing to faces, edges and vertex of a ‘‘wind-cube,’’ so that similar CWT vectors were close.'
        output = '环流天气类型（CWT）的时间序列，包括日均风向和涡度，使用 Kohonen 神经网络（KNN）通过相似性进行自分类。结果表明，KNN 能够映射 1975–94 年期间伦敦联合王国的所有 7300 个五天 CWT 序列。作为第一个结果，它给出了该时期 27 个 CWT Lamb 类别中每一类之前最可能的风序列。反之，作为第二个结果，在长达 20 年的时期内，观测到的五天 CWT 序列与{v1}天的 CWT 之间的弥散相关性可以推广，用于从之前的 CWT 序列预测该天的 CWT，在另一个测试时期（如 1995 年），因为两个时间序列是相似的。尽管平均预测误差与使用标准方法获得的误差相当，但 KNN 方法提供了补充结果，因为它们仅依赖于观测 CWT 数据的客观分类，没有任何模型假设。Lamb 目录中的 27 个 CWT 使用二进制三维矢量进行编码，指向“风立方体”的面、边和顶点，使得相似的 CWT 矢量彼此接近。'
        source = ti(text, [formula(1, '6th ')])
        original_formula = source.placeholders[0].formula
        view = g._source_prediction_prompt_view(source, text)
        self.assertEqual(view, text.replace('predict the last from', 'predict the CWT of that day from'))
        self.assertEqual(source.unicode, text)
        p, engine = paragraph(source), translator([])
        engine.post_translate_paragraph(p, ParagraphTranslateTracker(), source, output)
        self.assertEqual(p.unicode, output)
        self.assertEqual(engine.calls, [])
        self.assertTrue(any(c.pdf_formula is original_formula for c in p.pdf_paragraph_composition))
        self.assertEqual(g._prose_numeric_literals(source, text), g._prose_numeric_literals(source, output))


if __name__ == '__main__':
    unittest.main()
