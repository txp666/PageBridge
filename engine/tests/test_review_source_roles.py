"""Source-role request views and guards, with no external files or model calls."""
import json,re,unittest
from types import SimpleNamespace
from unittest.mock import patch
try:
 from .test_guard_review_boundaries import g,ti,formula,translator,paragraph,ParagraphTranslateTracker
except ImportError:
 from test_guard_review_boundaries import g,ti,formula,translator,paragraph,ParagraphTranslateTracker

class ReviewSourceRolesTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):g.install()
 def setUp(self):g._states.clear();g._counters.clear()
 def body(self,s,text,label='plain text'):
  p=paragraph(s);p.layout_label=label;g._check_body_translation(translator([]),p,s,text)
 def reject(self,s,bad,reason,label='plain text'):
  with self.assertRaisesRegex(g.PlaceholderIntegrityError,reason):self.body(s,bad,label)
 def post(self,s,bad,good,label='plain text'):
  p=paragraph(s);p.layout_label=label;e=translator([good]);original=s.unicode
  g._guard_post(e,p,ParagraphTranslateTracker(),s,bad)
  self.assertEqual(p.unicode,good);self.assertEqual(len(e.calls),1);self.assertEqual(s.unicode,original)
  for f in s.placeholders:self.assertTrue(any(c.pdf_formula is f.formula for c in p.pdf_paragraph_composition))
  return e
 def test_passive_good_and_bad_local_relations(self):
  s=ti('One group is shown; one LC6 can be preceded by different 5BLC clusters.')
  for good in ('一个LC6之前可能有不同的5BLC簇。','不同的5BLC簇可能在一个LC6之前。','一个LC6可能出现在不同的5BLC簇之后。','不同的5BLC簇之后是一个LC6。'):
   g._check_review_roles(s,good)
  for bad in ('一个LC6可能在不同的5BLC簇之前。','LC6先于5BLC簇。','5BLC簇之前是LC6。'):
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'passive-temporal'):g._check_review_roles(s,bad)
 def test_passive_ambiguous_and_protected_sources_unchanged(self):
  for text in ('one LC6 cannot be preceded by different 5BLC clusters.', 'one LC6 can be preceded by different LC6 clusters.',
    'one LC6 can be followed by different 5BLC clusters.', 'one LC6 can be preceded by different 5BLC and 3BLC clusters.',
    'one LC6 can be preceded by different {v1} clusters.', 'If one LC6 can be preceded by different 5BLC clusters.',
    'The author named Preceded By discusses LC6.', 'See https://example.test/LC6 for details.'):
   s=ti(text);self.assertEqual(g._source_review_prompt_view(s,text),text)
  s=ti('Use {v1}.',[formula(1,'one LC6 can be preceded by different 5BLC clusters.')]);self.assertEqual(g._source_review_replacements(s),[])
 def test_passive_other_pair_mentions_do_not_impose_unique_temporal_role(self):
  for text in ('one LC6 can be preceded by different 5BLC clusters. LC6 can precede 5BLC vectors.',
               'one LC6 can be preceded by different 5BLC clusters. The label LC6 is printed before 5BLC.'):
   source=ti(text);self.assertEqual(g._review_passive_groups(source),[])
   g._check_review_roles(source,'LC6在5BLC之前。')
 def test_inline_code_class_count_and_whole_title_label_stay_safe(self):
  s=ti('The code `wind sequences previous to each one of the 27 LC’s` is literal.')
  self.assertEqual(g._review_class_groups(s),[]);self.assertEqual(g._source_review_replacements(s),[])
  s=ti('Wind Data Mining');setattr(s,'_pagebridge_layout_label','abandon')
  e=translator(['风数据挖掘']);plain,out,_=g._whole_paragraph_translation(e,s,ParagraphTranslateTracker())
  self.assertEqual(plain._pagebridge_layout_label,'abandon');self.assertIn('contains no energy',e.calls[0][0])
 def test_count_attachment_and_negative_source_grammar(self):
  s=ti('Wind sequences previous to each one of the 27 LC’s are shown.')
  # Source matcher intentionally requires lowercase prose as in original TI.
  s=ti('The wind sequences previous to each one of the 27 LC’s are shown.')
  for good in ('给出27个LC类别中的每一类之前的风序列。','给出27类LC中每一类之前的风序列。','给出27个LC中的每一个之前的风序列。'):
   self.body(s,good)
  for bad in ('给出每类27个LC之前的风序列。','每个27个LC之前都有风序列。','给出每个LC之前的27个风序列。'):
   self.reject(s,bad,'weather-class-count-scope')
  for text in ('Each class has 27 wind sequences.','The 27 LC names belong to the classes.'):
   a=ti(text);self.assertEqual(g._source_review_replacements(a),[])
  self.reject(s,'给出LC类别中每一类之前的风序列。','prose-numeric-literal')
  self.reject(s,'给出27个LC类别中每一类之前的27个风序列。','weather-class-count-scope|prose-numeric-literal')
 def test_codified_ordinary_suffix_only(self):
  s=ti('Their label is noted; their BLCcodified vector dispersion is estimated.')
  for bad in ('估计其BLCcodified向量的分散性。','估计其BLC codified向量的分散性。'):
   self.reject(s,bad,'untranslated-codified')
  self.body(s,'估计其BLC编码向量的分散性。')
  for text in ('BLCcodified is a product.','their BLC2codified vectors are named.','their BLCCODIFIED vectors are named.',
               'their BLCencoded vectors are named.','The code `their BLCcodified vectors` is literal.',
               'See https://example.test/their-BLCcodified-vectors.', 'Use {v1}.'):
   s=ti(text,[formula(1,'their BLCcodified vectors')] if '{v1}' in text else [])
   self.assertEqual(g._review_codified_groups(s),[])
 def test_short_title_label_energy_and_names_boundaries(self):
  for label in ('title','abandon'):
   s=ti('Wind Data Mining');self.body(s,'风数据挖掘',label)
   self.reject(s,'风能数据挖掘','meteorological-wind-energy-added',label)
   self.reject(s,'Wind数据挖掘','untranslated-prose-fragment',label)
  for text,label in [('Wind Energy Data Mining','title'),('Wind River Systems','title'),('Wind Data Mining','reference'),
                     ('Wind Data Mining','code'),('Citation: Wind Data Mining.','plain text')]:
   g._check_review_roles(ti(text),'风能数据挖掘',label)
 def test_subjective_polarity_protected_literals_and_competing_concept(self):
  s=ti('Reproducing a subjective classification scheme for weather.')
  self.body(s,'复现天气的主观分类方案。')
  self.reject(s,'复现天气的客观分类方案。','subjective-classification-reversed')
  for text in ('This is not a subjective classification scheme.', 'A subjective and objective classification scheme.',
               'Compare a subjective classification scheme and an objective classification scheme.',
               'The code "a subjective classification scheme" is literal.', 'Use {v1}.'):
   s=ti(text,[formula(1,'a subjective classification scheme')] if '{v1}' in text else [])
   self.assertIsNone(g._review_subjective_scheme(s))
 def test_average_winds_requires_complete_categorical_context(self):
  for text in ('The observed average winds have speeds of 10 m/s.',
               'LC classes are given; observed average winds are recorded.',
               'These winds have been calculated from the weather data.'):
   s=ti(text);self.assertFalse(g._review_wind_classification(s));g._check_review_roles(s,'平均风速。')
 def test_attention_first_sentence_and_boundary_negatives(self):
  s=ti('The measurement again came into focus as a reliable criterion. The next subject attracted attention.')
  for bad in ('该测量再次成为可靠标准。下一主题受到关注。','该测量作为可靠判据没有受到关注。'):
   self.reject(s,bad,'attention-event-omitted')
  for good in ('该测量作为可靠标准再次受到关注。下一主题受到关注。','该测量作为可靠判据重新引起注意。下一主题受到关注。'):
   self.body(s,good)
  for text in ('The measurement became a reliable criterion.','The measurement did not come into focus as a reliable criterion.',
               'The measurement never came into focus as a reliable criterion.','Use {v1}.',
               'The code `The measurement came into focus as a reliable criterion.` is literal.'):
   s=ti(text,[formula(1,'came into focus as a criterion')] if '{v1}' in text else [])
   self.assertIsNone(g._review_attention_clause(s))
 def test_whole_recovery_preserves_existing_opaque_literal_mapping(self):
  source=ti('one LC6 can be preceded by different 5BLC clusters. See IEC 60079-11.')
  e=translator(['一个LC6之前可能有不同的5BLC簇。见{PBKEEP_0001}。'])
  plain,out,_=g._whole_paragraph_translation(e,source,ParagraphTranslateTracker())
  self.assertEqual(plain.unicode,source.unicode)
  self.assertEqual(out,'一个LC6之前可能有不同的5BLC簇。见IEC 60079-11。')
  self.assertIn('one LC6 can occur after different 5BLC clusters',e.calls[0][0])
  self.assertIn('{PBKEEP_0001}',e.calls[0][0]);self.assertNotIn('IEC 60079-11',e.calls[0][0])
 def test_whole_recovery_never_relaxes_missing_duplicate_opaque_tokens(self):
  source=ti('one LC6 can be preceded by different 5BLC clusters. See IEC 60079-11.')
  for bad in ('一个LC6之前可能有不同的5BLC簇。', '一个LC6之前可能有不同的5BLC簇。{PBKEEP_0001}{PBKEEP_0001}'):
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'protected-literal-token-changed'):
    g._whole_paragraph_translation(translator([bad]),source,ParagraphTranslateTracker())

 def test_complete_mock_retry_retains_source_and_formula_identity(self):
  cases=[('The pattern shows one LC6 can be preceded by different 5BLC clusters. {v1}',
          '该模式显示一个LC6在不同的5BLC簇之前。{v1}', '该模式显示不同的5BLC簇在一个LC6之前。{v1}'),
         ('The wind sequences previous to each one of the 27 LC’s are shown. {v1}',
          '展示每类27个LC之前的风序列。{v1}', '展示27个LC类别中的每一类之前的风序列。{v1}'),
         ('We examine their BLCcodified vector dispersion. {v1}',
          '我们检查其BLCcodified向量离散度。{v1}', '我们检查其BLC编码向量离散度。{v1}'),
         ('We reproduce a subjective classification scheme. {v1}',
          '我们复现客观分类方案。{v1}', '我们复现主观分类方案。{v1}'),
         ('The measurement again came into focus as a reliable criterion. {v1}',
          '该测量再次成为可靠标准。{v1}', '该测量再次作为可靠标准受到关注。{v1}')]
  for text,bad,good in cases:
   s=ti(text,[formula(1,'x=7')]);self.post(s,bad,good)
 def test_source_views_preserve_numbers_identifiers_and_formula_order(self):
  s=ti('The pattern shows one LC6 can be preceded by different 5BLC clusters. We use 27 samples and {v1}.',[formula(1,'y=2')])
  original=s.unicode;view=g._source_review_prompt_view(s,original)
  self.assertNotEqual(view,original);self.assertEqual(s.unicode,original)
  self.assertEqual(g._source_review_prompt_view(s,view),view)
  self.assertEqual(g._prose_numeric_literals(s,view),g._prose_numeric_literals(s,original))
  self.assertEqual(g._prose_scientific_identifiers(s,view),g._prose_scientific_identifiers(s,original))
  self.assertEqual(g._FORMULA.findall(view),g._FORMULA.findall(original))
 def test_initial_single_and_batch_share_idempotent_original_source_view(self):
  s=ti('The pattern shows one LC6 can be preceded by different 5BLC clusters.')
  expected=g._source_review_prompt_view(s,s.unicode);original=s.unicode
  inst=SimpleNamespace(translation_config=SimpleNamespace(lang_out='zh'))
  with patch.dict(g._originals,single_prompt_context=lambda self,text,*a,**kw:text,batch_prompt_context=lambda self,text,*a,**kw:text):
   self.assertEqual(g._guard_single_prompt_context(inst,original,translate_input=s),expected)
   token=g._review_batch.set([s])
   try:
    out=g._guard_batch_prompt_context(inst,json.dumps([{'id':1,'input':original}]))
    self.assertEqual(json.loads(out)[0]['input'],expected)
   finally:g._review_batch.reset(token)
  self.assertEqual(s.unicode,original)
 def test_complete_wind_classification_context_rejects_speed_only_when_source_proves_classes(self):
  s=ti('We list LC classes, then, the previous five wind sequences and observed average winds. These winds have been calculated from 5BLC/BLC6 vector pairs.')
  self.reject(s,'我们列出LC类别及前五个风序列和平均风速。这些风速由5BLC/BLC6向量对计算得出。','wind-classification-became-speed')
  self.body(s,'我们列出LC类别及前五个风序列和平均风型。这些风型由5BLC/BLC6向量对计算得出。')

if __name__=='__main__':unittest.main()
