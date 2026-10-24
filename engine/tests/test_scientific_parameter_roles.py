"""Portable source-role regressions; no validation files, PDFs, model or network."""
import importlib.util,json,re,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import translation_guard as g
from test_guard_review_boundaries import ti,formula,translator,paragraph,ParagraphTranslateTracker
SOURCE = {'R07': 'Figure 13. Accuracy (MCC) scores of RegnANN and KELLER on synthetic networks with 100 '
        'nodes and SLC data generation. (a) Results obtained on Barabasi networks varying the '
        'power-law coefficient and Erdo{v1}s-Re{v2}nyi topology while varying the mean degree of '
        'the network. (b) Results obtained for Erdo{v3}s-Re{v4}nyi (mean degree value equal to 2) '
        'while varyingthe data ratio. doi:10.1371/journal.pone.0028646.g013',
 'R12': 'Here we use the reference implementation of the algorithm provided in {v1}with default '
        'value for the data processing inequality tolerance parameter. In Section ‘‘ARACNE: '
        'varying learning parameters’’ we explore the influence of the tolerance parameter on a '
        'sample testbed.',
 'R14': 'Figure 23 shows the mean {v1}(the AUC value for the MCC-Recall curve) varying the eps '
        'value in{v2},{v3}the topology of the network and the number of nodes. Gene expression '
        'profiles are rescaled linearly in {v4}. We consider fixed data ratio equal to {v5}.',
 'R15': 'Figure 23. Mean {v1}varying the eps value, the topology of the network and the number of '
        'nodes. Gene expression profiles are rescaled linearly in {v2}. (a) Barabasi topology. (b) '
        'Erdo{v3}s-Re{v4}nyi networks. doi:10.1371/journal.pone.0028646.g023'}
def source(text):return ti(text,[formula(i,'x')for i in sorted(set(map(int,re.findall(r'\{v(\d+)\}',text))))])
class CandidateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):g.install()
 def test_real_complete_sources_keep_formula_order_and_original(self):
  for key,text in SOURCE.items():
   s=source(text);view=g._source_review_prompt_view(s,text)
   self.assertEqual(s.unicode,text);self.assertEqual(re.findall(r'\{v\d+\}',view),re.findall(r'\{v\d+\}',text));self.assertTrue(g._scientific_relation_hint(s))
  self.assertIn('; and on Erdo',g._source_review_prompt_view(source(SOURCE['R07']),SOURCE['R07']))
  self.assertIn('eps value in{v2},{v3}, the topology',g._source_review_prompt_view(source(SOURCE['R14']),SOURCE['R14']))
 def test_literal_and_excluded_regions_do_not_trigger(self):
  for id in ['R07','R14','R15']:
   text=SOURCE[id]
   for wrapped in [f'`{text}`',f'"{text}"',f"'{text}'",f'<code>{text}</code>']:
    s=source(wrapped);self.assertFalse(g._scientific_relation_request_views(s));self.assertFalse(g._scientific_relation_hint(s))
   for label in ['code','formula','reference','bibliography']:
    s=source(text);s._pagebridge_layout_label=label;self.assertFalse(g._scientific_relation_request_views(s))
 def test_conditional_negative_different_grammars_do_not_rewrite(self):
  clause=SOURCE['R07'].split('(a) ')[1].split('(b)')[0]
  for s in [source('If '+clause),source('Never use '+clause),source(clause.replace(' and Erdo',' or Erdo')),source(clause.replace('topology while varying','topology without varying'))]:
   self.assertFalse(g._source_parallel_network_experiments(s))
  for text in [SOURCE['R14'].replace('{v2},{v3}','{v2},{v2}'),SOURCE['R14'].replace('eps value','other value'),SOURCE['R15']+' Repeat {v1}.']:
   self.assertFalse(g._source_mean_parameter_variation(source(text)))
 def test_whole_native_name_tokens_keep_two_experiment_roles(self):
  text='Results obtained on Barabasi networks varying the power-law coefficient and {v1} topology while varying the mean degree of the network.'
  s=ti(text,[formula(1,'Erdo\u0308s-Re\u0301nyi')])
  self.assertEqual(len(g._source_parallel_network_experiments(s)),1)
  view=g._source_review_prompt_view(s,text)
  self.assertIn('; and on {v1} topology while varying',view)
  self.assertEqual(re.findall(r'\{v\d+\}',view),['{v1}'])
  self.assertEqual(s.unicode,text)
  self.assertFalse(g._source_parallel_network_experiments(ti(text,[])))
  self.assertFalse(g._source_parallel_network_experiments(source(text+' Repeated {v1}.')))
  self.assertFalse(g._source_parallel_network_experiments(source(text.replace('Barabasi','{v1}'))))
  for wrapped in ['If '+text,'"'+text+'"','`'+text+'`']:
   self.assertFalse(g._source_parallel_network_experiments(source(wrapped)))
 def test_ordinary_technical_terms_are_not_expanded(self):
  s=source(SOURCE['R12'])
  for good in ['采用数据处理不等式容差参数的默认值。{v1}','采用数据处理不等式的容差参数。{v1}']:g._check_scientific_relations(s,good)
  for bad in ['数据处理的非负性不等式容差参数。{v1}','数据处理的非等式容差参数。{v1}']:
   with self.assertRaises(g.PlaceholderIntegrityError):g._check_scientific_relations(s,bad)
  s=source(SOURCE['R12']+' A non-negative constraint is discussed.');g._check_scientific_relations(s,'数据处理的非负性不等式{v1}')
 def test_mean_operand_and_parameter_omission(self):
  s=source(SOURCE['R15'])
  g._check_scientific_relations(s,'改变eps值、网络拓扑和节点数量所得的平均{v1}。{v2}{v3}{v4}')
  for bad in ['均值随{v1}变化，改变eps。{v2}{v3}{v4}','平均{v1}，改变网络拓扑和节点数量。{v2}{v3}{v4}']:
   with self.assertRaises(g.PlaceholderIntegrityError):g._check_scientific_relations(s,bad)
 def test_post_retry_preserves_original_formulas(self):
  text='Mean {v1} varying the eps value, the topology of the network and the number of nodes.'
  s=source(text);p=paragraph(s);engine=translator(['改变eps值、网络拓扑和节点数量所得的平均{v1}。'])
  g._guard_post(engine,p,ParagraphTranslateTracker(),s,'均值随{v1}变化，改变eps值、网络拓扑和节点数量。')
  self.assertEqual(len(engine.calls),1);self.assertEqual(s.unicode,text);self.assertIn('the measured quantity',engine.calls[0][0]);self.assertTrue(any(c.pdf_formula is s.placeholders[0].formula for c in p.pdf_paragraph_composition))
if __name__=='__main__':unittest.main()
