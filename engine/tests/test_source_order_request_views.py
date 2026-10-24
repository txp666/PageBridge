"""Portable tests for source-only, token-preserving equivalent request views."""
import unittest
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker


class SourceOrderRequestViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def test_exact_roles_rewrite_request_only_and_keep_all_tokens(self):
        cases = [
            ('The model shows MCC scores ranging between {v13} and {v9} for data ratio {v41} and data ratio {v2} respectively.',
             'MCC scores of {v13} and {v9}; these scores correspond to data ratios {v41} and {v2} respectively'),
            ('Finally, the value {v7} for the output unit {v8} can be calculated as follows:',
             'the value {v7} corresponds to the output unit {v8} and can be calculated as follows'),
        ]
        for text,wanted in cases:
            ti=source(text); original=(ti.unicode,[id(p) for p in ti.placeholders])
            view=g._source_review_prompt_view(ti,text)
            self.assertIn(wanted,view)
            self.assertEqual(g._CANONICAL.findall(text),g._CANONICAL.findall(view))
            self.assertEqual(original,(ti.unicode,[id(p) for p in ti.placeholders]))
            self.assertEqual(view,g._source_review_prompt_view(ti,view))
            self.assertIn(wanted,actual_prompt_translator().generate_prompt_for_llm(text,translate_input=ti))

    def test_ambiguous_unproven_quoted_and_nonprose_stay_exact(self):
        plain='the value {v1} for the output unit {v2} can be calculated as follows'
        values=[plain.replace('output','input'),plain+' and {v2}',plain.replace('{v2}','{v1}'),
                'It is not '+plain,'If '+plain,'`'+plain+'`','“'+plain+'”',
                plain.replace('for the','<style id=\'3\'>for the</style>')]
        scores='MCC scores ranging between {v1} and {v2} for data ratio {v3} and data ratio {v4} respectively'
        values += [scores.replace(' respectively',''),scores.replace(' and data ratio',' or data ratio'),
                   scores.replace('MCC scores','other scores'),scores+' and {v3}']
        for text in values:
            with self.subTest(text=text):
                ti=source(text);self.assertEqual(text,g._source_review_prompt_view(ti,text))
        ti=source(plain,['1']);self.assertEqual(plain,g._source_review_prompt_view(ti,plain))
        for label in ['code','formula','references']:
            ti=source(plain);ti._pagebridge_layout_label=label
            self.assertEqual(plain,g._source_review_prompt_view(ti,plain))

    def test_no_permutation_or_bad_pairing_is_newly_authorized(self):
        ti=source('The model shows MCC scores ranging between {v1} and {v2} for data ratio {v3} and data ratio {v4} respectively.')
        for bad in ['数据比率{v3}和{v4}对应MCC得分{v1}和{v2}。',
                    '数据比率{v4}和{v3}对应MCC得分{v1}和{v2}。',
                    'MCC得分{v2}和{v1}对应数据比率{v3}和{v4}。']:
            with self.subTest(output=bad),self.assertRaises(g.PlaceholderIntegrityError):
                g.normalize_output(ti,bad)
        ti=source('Finally, the value {v7} for the output unit {v8} can be calculated as follows:')
        with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,'输出单元{v8}的值{v7}如下。')
        good='最后，值{v7}对应输出单元{v8}，可按下式计算：'
        engine=actual_prompt_translator([good]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,'输出单元{v8}的值{v7}如下。')
        self.assertEqual(para.unicode,good)
        self.assertEqual(len(engine.calls),1)
        self.assertEqual(ti.unicode,'Finally, the value {v7} for the output unit {v8} can be calculated as follows:')
        self.assertIn('the value {v7} corresponds to the output unit {v8}',engine.calls[0][0])


if __name__=='__main__':unittest.main()
