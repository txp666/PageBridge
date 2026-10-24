"""Portable reference ownership and ordinary electrical modifier regressions."""
import unittest
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker


class StandardReferenceOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def test_reference_owner_and_number_pair_preserved(self):
        ti = source('See IEC/EN 60079-14, Article 504 of the NEC and Section 18 of the CEC.')
        view = g._source_review_prompt_view(ti, ti.unicode)
        self.assertEqual('See IEC/EN 60079-14, NEC Article 504 and CEC Section 18.', view)
        for good in ['参见IEC/EN 60079-14、NEC第504条和CEC第18节。',
                     '参见IEC/EN 60079-14、504条（NEC）和18节（CEC）。']:
            g._check_review_roles(ti, good)
        for bad in ['参见IEC/EN 60079-14第504条和CEC第18节。',
                    '参见IEC/EN 60079-14、NEC第18条和CEC第504节。',
                    '参见IEC/EN 60079-14、NEC和CEC第504条，以及第18节。',
                    '参见IEC/EN 60079-14、NEC第5040条和CEC第18节。']:
            with self.subTest(bad=bad), self.assertRaises(g.PlaceholderIntegrityError):
                g._check_review_roles(ti, bad)

    def test_normal_retry_rejects_dropped_owner(self):
        ti = source('The methods in IEC/EN 60079-14, Article 504 of the NEC and Section 18 of the CEC apply.')
        good = '适用IEC/EN 60079-14、NEC第504条及CEC第18节的方法。'
        engine = actual_prompt_translator([good]); para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti,
                                       '适用IEC/EN 60079-14第504条及CEC第18节的方法。')
        self.assertEqual(1, len(engine.calls))
        self.assertEqual(good, para.unicode)
        self.assertIn('NEC第504条', engine.calls[0][0])

    def test_code_quotation_and_ambiguous_authority_not_proof(self):
        value = 'Article 504 of the NEC'
        for text in ['`'+value+'`', '"'+value+'"', "'"+value+"'", '<code>'+value+'</code>',
                     value.replace('NEC','NEC2'), value.replace('of the','for the'), 'Article 504 applies to NEC data.']:
            with self.subTest(text=text):
                ti = source(text)
                self.assertEqual([], g._standard_reference_owners(ti))
                self.assertEqual(text, g._source_review_prompt_view(ti, text))
        for label in ['code','references','formula']:
            ti = source(value); ti._pagebridge_layout_label = label
            self.assertEqual([], g._standard_reference_owners(ti))

    def test_resultant_is_ordinary_only_in_proven_electrical_source_context(self):
        ti = source('In a series connection, the resulting voltage is the sum and the resulting current is the larger value.')
        good = '在串联连接中，所得电压为电压之和，所得电流取较大值。'
        self.assertEqual([], g._ordinary_english_residuals(ti, good))
        self.assertIn('resultant', g._ordinary_english_residuals(ti, good.replace('所得','resultant')))
        engine = actual_prompt_translator([good]); para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, good.replace('所得','resultant'))
        self.assertEqual(good, para.unicode)
        self.assertEqual(1, len(engine.calls))
        for text in ['The resultant method uses series connection.', 'The resulting vector is shown.',
                     '`In a series connection the resulting voltage changes.`']:
            self.assertFalse(g._resulting_electrical_quantity(source(text)))

    def test_comma_numbered_reference_is_valid_without_cross_owner_match(self):
        ti = source('Article 504 of the NEC and Section 18 of the CEC apply.')
        for good in ['适用NEC，第504条；CEC，第18节。', '适用NEC, 第504条及CEC, 第18节。']:
            g._check_review_roles(ti, good)
        for bad in ['适用NEC，CEC第504条，第18节。', '适用MyNEC，第504条；CEC，第18节。',
                    '适用NEC，第18条；CEC，第504节。', '适用NEC，第5040条；CEC，第18节。']:
            with self.subTest(bad=bad), self.assertRaises(g.PlaceholderIntegrityError):
                g._check_review_roles(ti, bad)

    def test_source_literal_allowed_once_but_new_quotes_cannot_hide_residue(self):
        clause = 'In a series connection, the resulting voltage is the sum.'
        for literal in ['`resultant`', '"resultant"', "'resultant'", '“resultant”', '<code>resultant</code>']:
            with self.subTest(literal=literal):
                ti = source(clause+' The source code label is '+literal+'.')
                good = '在串联连接中，所得电压是电压之和。源代码标签是'+literal+'。'
                self.assertEqual([], g._ordinary_english_residuals(ti, good))
                self.assertIn('resultant', g._ordinary_english_residuals(ti, good+literal))
                self.assertIn('resultant', g._ordinary_english_residuals(source(clause), '在串联连接中，'+literal+'电压是电压之和。'))


if __name__ == '__main__':
    unittest.main()
