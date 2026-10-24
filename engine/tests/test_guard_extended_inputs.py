"""Known conservative false positives, independent of the five paper fixtures."""
import unittest

try:
    from .test_guard_review_boundaries import ti, g, translator, paragraph, ParagraphTranslateTracker
except ImportError:
    from test_guard_review_boundaries import ti, g, translator, paragraph, ParagraphTranslateTracker


class ExtendedSourceInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def setUp(self):
        g._states.clear()
        g._counters.clear()

    def test_explicit_source_exhibit_negation_is_not_forced_to_affirm(self):
        source = ti('The system does not exhibit non-equilibrium critical phenomena.')
        correct = '该系统表现出的并非非平衡临界现象。'
        instance = translator([]); p = paragraph(source)
        instance.post_translate_paragraph(p, ParagraphTranslateTracker(), source, correct)
        self.assertEqual(p.unicode, correct)
        self.assertEqual(instance.calls, [])

    def test_weather_title_preserves_source_attested_wind_river_name(self):
        source = ti('Wind Speed Prediction by Wind River Systems')
        correct = 'Wind River Systems 的风速预测'
        instance = translator([]); p = paragraph(source); p.layout_label = 'title'
        instance.post_translate_paragraph(p, ParagraphTranslateTracker(), source, correct)
        self.assertEqual(p.unicode, correct)
        self.assertEqual(instance.calls, [])
        # Masking the attested company must not exempt the meteorological noun.
        for residual in ('wind', 'Wind', 'WIND'):
            with self.subTest(residual=residual):
                engine = translator([correct]); target = paragraph(source); target.layout_label = 'title'
                engine.post_translate_paragraph(target, ParagraphTranslateTracker(), source,
                                                'Wind River Systems 的' + residual + '速度预测')
                self.assertEqual(target.unicode, correct)
                self.assertEqual(len(engine.calls), 1)

    def test_title_case_weather_research_phrases_are_not_proper_names(self):
        for phrase in ('Wind Speed Measurements', 'Wind Speed Data', 'Wind Data Mining',
                       'Wind Direction Analysis', 'Wind Turbine Measurements'):
            source = ti('Wind Speed Prediction from ' + phrase)
            bad = '根据 ' + phrase + ' 进行风速预测。'
            p = paragraph(source); p.layout_label = 'title'
            with self.subTest(phrase=phrase), self.assertRaisesRegex(
                    g.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
                g._check_body_translation(translator([]), p, source, bad)

    def test_attested_company_does_not_mask_another_common_weather_phrase(self):
        source = ti('Wind Speed Prediction from Wind Speed Measurements, by Wind River Systems')
        p = paragraph(source); p.layout_label = 'title'
        good = 'Wind River Systems 根据风速测量进行风速预测。'
        g._check_body_translation(translator([]), p, source, good)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
            g._check_body_translation(translator([]), p, source,
                                     'Wind River Systems 根据 Wind Speed Measurements 进行风速预测。')


if __name__ == '__main__':
    unittest.main()
