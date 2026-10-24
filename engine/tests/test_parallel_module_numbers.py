"""Portable lost-space tests for a complete pair of numbered module labels."""
import unittest
import json
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker


class ParallelModuleNumberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()

    def test_parallel_prose_proves_boundary_without_changing_real_source(self):
        text='The two scores are {v1} and {v2} on module 23 and module34 respectively.'
        ti=source(text);before=(ti.unicode,[id(p) for p in ti.placeholders])
        self.assertEqual(Counter({'23':1,'34':1}),g._prose_numeric_literals(ti,text))
        self.assertEqual(Counter(),g._prose_scientific_identifiers(ti,text))
        view=g._source_review_prompt_view(ti,text)
        self.assertEqual(text.replace('module34','module 34'),view)
        self.assertEqual(g._CANONICAL.findall(text),g._CANONICAL.findall(view))
        self.assertEqual(before,(ti.unicode,[id(p) for p in ti.placeholders]))
        self.assertEqual(view,g._source_review_prompt_view(ti,view))

    def test_actual_shape_accepts_two_source_twelve_occurrences(self):
        ti=source('Model scores {v1} on module 6 and scores {v2} on module 12. '
                  'Its two scores are {v3} and {v4} on module 6 and module12 respectively.')
        good='模型在模块6得分为{v1}，在模块12得分为{v2}。两个得分为{v3}和{v4}，分别对应模块6和模块12。'
        self.assertEqual(Counter({'12':2}),g._prose_numeric_literals(ti,ti.unicode))
        engine=actual_prompt_translator([]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,good)
        self.assertEqual(good,para.unicode);self.assertEqual([],engine.calls)
        self.assertEqual([id(p.formula) for p in ti.placeholders],
                         [id(c.pdf_formula) for c in para.pdf_paragraph_composition if c.pdf_formula])

    def test_wrong_missing_or_extra_number_and_formula_still_fail(self):
        ti=source('The two scores are {v1} and {v2} on module 23 and module34 respectively.')
        good='两个得分为{v1}和{v2}，分别对应模块23和模块34。'
        for bad in [good.replace('34','35'),good.replace('和模块34',''),good+'模块34。']:
            with self.subTest(output=bad),self.assertRaises(g.PlaceholderIntegrityError):
                g._check_body_translation(actual_prompt_translator(),paragraph(ti),ti,bad)
        for bad in [good.replace('{v1}',''),good.replace('{v1}','{v2}'),good.replace('{v1}','{x}').replace('{v2}','{v1}').replace('{x}','{v2}')]:
            with self.subTest(output=bad),self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,bad)

    def test_code_quotes_and_incomplete_or_identifier_grammar_stay_opaque(self):
        value='on module 23 and module34 respectively'
        for text in [value.replace(' respectively',''),value.replace('on module 23','on module23'),
                     value.replace('and module','or module'),value.replace('module34','module34A'),
                     '`'+value+'`','"'+value+'"',"'"+value+"'",'“'+value+'”','‘'+value+'’',
                     '<code>'+value+'</code>',value.replace('module34','Module34')]:
            with self.subTest(source=text):
                ti=source(text)
                self.assertEqual([],g._joined_parallel_module_numbers(text))
                self.assertEqual(text,g._source_review_prompt_view(ti,text))
                self.assertTrue(any(k.lower().startswith('module') for k in g._prose_scientific_identifiers(ti,text)))
        ti=source(value);ti._pagebridge_layout_label='code'
        self.assertEqual(value,g._source_review_prompt_view(ti,value))

    def test_same_named_real_identifier_elsewhere_is_still_required(self):
        text='The scores are on module 23 and module34 respectively; module34 is the software identifier.'
        ti=source(text)
        self.assertEqual(Counter({'module34':1}),g._prose_scientific_identifiers(ti,text))
        self.assertEqual(Counter({'23':1,'34':1}),g._prose_numeric_literals(ti,text))
        view=g._source_review_prompt_view(ti,text)
        self.assertEqual(text.replace('and module34','and module 34'),view)
        self.assertTrue(view.endswith('module34 is the software identifier.'))
        bad='分数分别对应模块23和模块34；该软件标识符已删除。'
        with self.assertRaises(g.PlaceholderIntegrityError):
            g._check_body_translation(actual_prompt_translator(),paragraph(ti),ti,bad)

    def test_batch_and_normal_retry_use_view_but_validate_real_source(self):
        text='The scores are {v1} and {v2} on module 23 and module34 respectively.'
        ti=source(text);token=g._review_batch.set([ti])
        try:
            with patch.dict(g._originals,{'batch_prompt_context':lambda self,value,*a,**kw:value}):
                result=g._guard_batch_prompt_context(SimpleNamespace(translation_config=SimpleNamespace(lang_out='zh')),
                    json.dumps([{'id':'same-item','input':text}]))
            self.assertEqual([{'id':'same-item','input':text.replace('module34','module 34')}],json.loads(result))
        finally:g._review_batch.reset(token)
        good='得分为{v1}和{v2}，分别对应模块23和模块34。'
        engine=actual_prompt_translator([good]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,good.replace('34','35'))
        self.assertEqual(len(engine.calls),1)
        self.assertIn('on module 23 and module 34 respectively',engine.calls[0][0])
        self.assertEqual(text,ti.unicode)
        self.assertEqual(good,para.unicode)


if __name__=='__main__':unittest.main()
