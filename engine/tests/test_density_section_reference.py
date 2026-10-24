"""Portable source-only density-map roles and exact literal boundary regressions."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker

FIRST = ('The most significant clusters of each LC6 on the {v1}Kohonen map, are found on '
         'their particular density map up to 5 neighbours (defined above in DataClassification '
         'by KNN), where we choose the three largest maxima.')
TAIL = ' The component may be 1, 0 or {v2}.'
GOOD = ('在{v1}Kohonen图上，通过各自的密度图寻找每个LC6最显著的聚类，'
        '其邻域扩展至5个邻居（见上文KNN数据分类节）；从该图中选出三个最大的极大值。'
        '分量可为1、0或{v2}。')

class DensitySectionReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()

    def test_complete_first_sentence_view_keeps_original_atoms_and_roles(self):
        ti=source(FIRST+TAIL);before=(ti.unicode,tuple(id(p.formula) for p in ti.placeholders))
        match=g._source_density_cluster_sentence(ti);self.assertIsNotNone(match)
        view=g._source_review_prompt_view(ti,ti.unicode)
        self.assertIn('are found using their particular density map, with the neighbourhood extending up to 5 neighbours',view)
        self.assertIn('From this density map we choose the three largest maxima.',view)
        self.assertEqual(['1','2'],g._FORMULA.findall(view));self.assertTrue(view.endswith(TAIL))
        self.assertEqual(before,(ti.unicode,tuple(id(p.formula) for p in ti.placeholders)))
        self.assertEqual(view,g._source_review_prompt_view(ti,view))
        self.assertEqual(g._prose_numeric_literals(ti,ti.unicode),g._prose_numeric_literals(ti,view))

    def test_quantities_and_class_are_extracted_not_fixed(self):
        ti=source(FIRST.replace('LC6','LT9').replace('5 neighbours','7 neighbours').replace('three largest','four largest'))
        view=g._source_review_prompt_view(ti,ti.unicode)
        self.assertIn('each LT9',view);self.assertIn('up to 7 neighbours',view);self.assertIn('four largest maxima',view)
        self.assertNotIn('LC6',view)

    def test_nonmatching_unknown_duplicate_and_opaque_source_do_not_prove_roles(self):
        bads=[FIRST.replace('are found','are not found'),FIRST.replace('up to','at least'),
              FIRST.replace('largest maxima','smallest maxima'),FIRST.replace('by KNN','by ABC'),
              FIRST.replace('their particular density map','their particular temperature map'),
              'Perhaps '+FIRST,'`'+FIRST+'`','"'+FIRST+'"',"'"+FIRST+"'",'<code>'+FIRST+'</code>']
        for value in bads:
            with self.subTest(value=value):self.assertIsNone(g._source_density_cluster_sentence(source(value)))
        self.assertIsNone(g._source_density_cluster_sentence(source(FIRST,known=[])))
        self.assertIsNone(g._source_density_cluster_sentence(source(FIRST+' See {v1}.')))
        for label in ['code','formula','references','bibliography']:
            ti=source(FIRST);ti._pagebridge_layout_label=label
            self.assertIsNone(g._source_density_cluster_sentence(ti))

    def test_formula_order_and_multidigit_gate_still_fail_then_normal_retry(self):
        ti=source(FIRST.replace('5 neighbours','15 neighbours')+TAIL)
        good=GOOD.replace('5个邻居','15个邻居')
        with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,good.replace('{v1}','{v3}'))
        with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,good.replace('{v1}','{v3}').replace('{v2}','{v1}').replace('{v3}','{v2}'))
        bad=good.replace('15个邻居','16个邻居')
        engine=actual_prompt_translator([good]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,bad)
        self.assertEqual(good,para.unicode);self.assertEqual(1,len(engine.calls))
        self.assertIn('neighbourhood extending up to 15 neighbours',engine.calls[0][0])
        actual=[c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula is not None]
        self.assertEqual(len(actual),len(ti.placeholders))
        self.assertTrue(all(a is b.formula for a,b in zip(actual,ti.placeholders)))

    def test_source_literal_survives_but_added_duplicate_changed_and_new_literal_do_not(self):
        ti=source('The key `DataClassification` is documented (defined above in DataClassification by KNN).')
        good='键 `DataClassification` 已有说明（定义见上文KNN数据分类节）。'
        self.assertEqual([],g._ordinary_english_residuals(ti,good))
        engine=actual_prompt_translator([]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,good)
        self.assertEqual(good,para.unicode);self.assertEqual([],engine.calls)
        for bad in [good+' `DataClassification`',good.replace('`DataClassification`','`Data Classification`')]:
            self.assertIn('Data Classification',g._ordinary_english_residuals(ti,bad))
        fresh=source('Use (defined above in DataClassification by KNN).')
        self.assertIn('Data Classification',g._ordinary_english_residuals(fresh,'参见 `DataClassification`。'))

    def test_exact_literal_mask_is_analysis_only_and_multiplicity_bounded(self):
        values=['`resultant`','<code>resultant</code>','"resultant"',"'resultant'",'“resultant”','‘resultant’']
        for literal in values:
            ti=source('Label '+literal+' is used.');text='标签'+literal+'；新副本'+literal
            view=g._mask_exact_source_literals(ti,text)
            self.assertEqual(len(text),len(view));self.assertEqual(1,view.count(literal));self.assertIn(literal,ti.unicode)
        self.assertEqual("isn't x; wasn't y",g._mask_exact_source_literals(source("isn't x; wasn't y"),"isn't x; wasn't y"))

    def test_batch_and_whole_recovery_use_same_source_roles(self):
        ti=source(FIRST+TAIL);other=source('Kohonen is a proper name.');engine=actual_prompt_translator([])
        token=g._review_batch.set([ti,other])
        try:
            with patch.dict(g._originals,{'batch_prompt_context':lambda self,text,*a,**kw:text}):
                prompt=g._guard_batch_prompt_context(SimpleNamespace(translation_config=SimpleNamespace(lang_out='zh')),
                    json.dumps([{'id':'density','input':ti.unicode},{'id':'other','input':other.unicode}]))
            self.assertIn('neighbourhood extending up to 5 neighbours',prompt)
            self.assertIn('only for input item "density"',prompt)
            self.assertNotIn('only for input item "other"',prompt)
        finally:g._review_batch.reset(token)
        class Captured(Exception):pass
        prompts=[]
        def capture(prompt,**kw):prompts.append(prompt);raise Captured()
        engine.translate_engine=SimpleNamespace(llm_translate=capture)
        with self.assertRaises(Captured):g._whole_paragraph_translation(engine,ti,ParagraphTranslateTracker())
        self.assertIn('neighbourhood extending up to 5 neighbours',prompts[0])
        self.assertIn('three largest maxima',prompts[0])
        self.assertEqual(FIRST+TAIL,ti.unicode)

if __name__=='__main__':unittest.main()
