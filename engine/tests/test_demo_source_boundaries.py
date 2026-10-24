"""Portable numbered-rule headings and complete publisher metadata boundaries."""
import json
import unittest
from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker

META = ('Citation: Sample AB, Author CD (2018) Rules for Reproducible Research. '
        'Journal Name 12(3): e1004567. doi:10.1234/journal.test.1004567 '
        'Editor: Editor E. Name, University of Example, Example Country Published November 26, 2018')

def title(text):
    ti=source(text);ti._pagebridge_layout_label='title';return ti

class DemoSourceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()

    def check(self,ti,out):
        para=paragraph(ti);para.layout_label=getattr(ti,'_pagebridge_layout_label','plain text')
        g._check_body_translation(actual_prompt_translator(),para,ti,out)

    def test_glued_heading_number_is_prose_and_valid_response_needs_no_retry(self):
        for n,cn in [(1,'一'),(10,'十'),(21,'二十一')]:
            ti=title(f'Rule{n}: Keep the Original Results for Research')
            self.assertEqual(g._prose_scientific_identifiers(ti,ti.unicode),Counter())
            for translated in [f'规则{n}：保留科研的原始结果',f'第{cn}条：保留科研的原始结果']:
                self.check(ti,translated)
                para=paragraph(ti);para.layout_label='title';engine=actual_prompt_translator([])
                engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,translated)
                self.assertEqual(para.unicode,translated);self.assertEqual(engine.calls,[])
            self.assertEqual(g._source_review_prompt_view(ti,ti.unicode),ti.unicode.replace('Rule','Rule ',1))
            self.assertTrue(ti.unicode.startswith(f'Rule{n}:'))

    def test_number_must_remain_in_the_heading_not_move_or_change(self):
        for n in [1,10]:
            ti=title(f'Rule{n}: Keep the Original Results for Research')
            for wrong in [f'规则{n+1}：保留结果',f'保留{n}项原始结果',f'规则{n}0：保留结果','规则：保留结果']:
                with self.subTest(n=n,wrong=wrong),self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,wrong)
        ti=title('Rule10: Keep the Original Results for Research')
        with self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,'规则10：保留结果，另外有10份数据。')

    def test_body_quotes_code_identifiers_and_incomplete_titles_are_not_rules(self):
        valid='Rule1: Keep the Original Results for Research'
        for value in ['Rule1Beta: Keep the Original Results for Research',valid.replace(':',''),
                      'Rule1: ModelName',valid.lower(),'"'+valid+'"',"'"+valid+"'",'`'+valid+'`','<code>'+valid+'</code>']:
            ti=title(value);self.assertIsNone(g._source_rule_heading(ti));self.assertEqual(g._source_review_prompt_view(ti,value),value)
        for label in ['plain_text','abandon','code','references','formula']:
            ti=title(valid);ti._pagebridge_layout_label=label;self.assertIsNone(g._source_rule_heading(ti))

    def test_only_initial_heading_occurrence_is_reclassified(self):
        ti=title('Rule10: Use Rule10 and H2O for the 2018 Results')
        self.assertEqual(g._prose_scientific_identifiers(ti,ti.unicode),Counter({'Rule10':1,'H2O':1}))
        good='规则10：对2018年的结果使用Rule10和H2O'
        self.check(ti,good)
        with self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,good.replace('Rule10','规则10'))
        with self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,good.replace('2018','2019'))

    def test_batch_view_keeps_formula_objects_and_other_inputs_unchanged(self):
        ti=title('Rule10: Preserve the Original Results for {v1}')
        other=source('The identifier Rule10 is used for this program.')
        original=ti.unicode;identity=id(ti.placeholders[0].formula)
        token=g._review_batch.set([ti,other])
        try:
            with patch.dict(g._originals,{'batch_prompt_context':lambda self,text,*a,**k:text}):
                value=g._guard_batch_prompt_context(SimpleNamespace(translation_config=SimpleNamespace(lang_out='zh')),
                    json.dumps([{'id':'heading','input':ti.unicode},{'id':'body','input':other.unicode}]))
            self.assertIn('Rule 10:',value);self.assertIn(other.unicode,value)
        finally:g._review_batch.reset(token)
        self.assertEqual(ti.unicode,original);self.assertEqual(id(ti.placeholders[0].formula),identity)
        with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,'规则10：保留原结果')

    def test_complete_citation_editor_date_block_is_metadata_only_in_abandon(self):
        ti=source(META)
        self.assertTrue(g._preserve_publication_metadata(ti,'abandon'))
        for label in ['plain text','title','reference','code']:
            self.assertFalse(g._preserve_publication_metadata(ti,label))
        for value in ['"'+META+'"',"'"+META+"'",'The example is '+META,META+' Additional results are discussed here.',
                      META.replace('doi:10.1234/journal.test.1004567 ','') ,META.replace('Editor: ','Contributor: '),
                      META.replace('November 26','November 31'),META.replace('12(3)','12'),
                      META.replace('e1004567','e1004568'),META.replace('Published','Received')]:
            with self.subTest(value=value):self.assertFalse(g._preserve_publication_metadata(source(value),'abandon'))
        ti=source(META+' {v1}');self.assertFalse(g._preserve_publication_metadata(ti,'abandon'))

    def test_native_pre_preserves_metadata_without_calling_model_or_post(self):
        ti=source(META);para=paragraph(ti);para.layout_label='abandon'
        original=para.pdf_paragraph_composition;engine=actual_prompt_translator([])
        with patch.dict(g._originals,{'pre':lambda *a,**k:(ti.unicode,ti)}):
            result=g._guard_pre(engine,para,ParagraphTranslateTracker(),{}, {})
        self.assertEqual(result,(None,None));self.assertEqual(para.unicode,META)
        self.assertIs(para.pdf_paragraph_composition,original);self.assertEqual(engine.calls,[])

if __name__=='__main__':unittest.main()
