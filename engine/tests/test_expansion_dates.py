"""Access-date equivalence and acronym recovery, without model or file fixtures."""
import unittest
try:
 from .test_guard_review_boundaries import g,ti,formula,translator,paragraph,ParagraphTranslateTracker
 from .test_translation_guard import RichTextPlaceholder,PdfSameStyleCharacters,PdfStyle,PdfCharacter
except ImportError:
 from test_guard_review_boundaries import g,ti,formula,translator,paragraph,ParagraphTranslateTracker
 from test_translation_guard import RichTextPlaceholder,PdfSameStyleCharacters,PdfStyle,PdfCharacter

class ExpansionDateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):g.install()
 def setUp(self):g._states.clear();g._counters.clear()
 def body(self,s,out):g._check_body_translation(translator([]),paragraph(s),s,out)
 def test_valid_month_spellings_are_equivalent_but_other_numbers_unchanged(self):
  for name,number,chinese in [('Nov',11,'十一'),('November',11,'十一'),('Oct.',10,'十'),('December',12,'十二'),('Jan',1,'一')]:
   s=ti(f'The record was used. Accessed 2011 {name} 26.')
   for m in (str(number),chinese):
    self.body(s,f'使用了该记录。访问日期2011年{m}月26日。')
   self.body(s,f'使用了该记录。Accessed 2011 {name} 26.')
 def test_date_omission_change_duplicate_or_unrelated_month_digit_rejected(self):
  s=ti('The record was used. Accessed 2011 Nov 26.')
  for bad in ['使用了该记录。访问日期2011年10月26日。','使用了该记录。访问日期2011年11月27日。',
              '使用了该记录。访问日期2012年11月26日。','使用了该记录。2011年26日。',
              '使用了该记录。2011年11月26日。2011年11月26日。']:
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'bibliographic-access-date-changed'):self.body(s,bad)
  with self.assertRaisesRegex(g.PlaceholderIntegrityError,'prose-numeric-literal-changed'):
   self.body(s,'使用了11条记录。访问日期2011年11月26日。')
 def test_date_source_names_code_url_formula_and_invalid_calendar_do_not_trigger(self):
  for source in ['The model November is used in 2011 with 26 records.', 'Accessed 2011 Nov26.',
                 'Accessed 2011 Foo 26.', 'Accessed 2011 Feb 31.', 'Accessed 2011 Nov 32.',
                 'The code `Accessed 2011 Nov 26` is literal.', 'See https://example.test/Accessed/2011/Nov/26.',
                 'The expression is {v1}.']:
   s=ti(source,[formula(1,'Accessed 2011 Nov 26')] if '{v1}' in source else [])
   self.assertEqual(g._source_access_dates(s),{})
 def test_exact_date_preserves_multiple_occurrences_and_numeric_roles(self):
  s=ti('Accessed 2011 Nov 26. Accessed 2012 Oct 15. The count was 11.')
  self.body(s,'访问日期2012年10月15日。访问日期2011年11月26日。计数为11。')
  with self.assertRaises(g.PlaceholderIntegrityError):self.body(s,'访问日期2012年10月15日。访问日期2011年11月26日。')
 def test_fisco_definition_requires_complete_source_and_ignores_opaque_content(self):
  for source in ['FISCO is supported.', 'FISCO stands for a different protocol.',
                 'FISCO is a name. Fieldbus Intrinsically Safe COncept appears elsewhere.',
                 'The code `FISCO stands for Fieldbus Intrinsically Safe COncept` is literal.',
                 'The formula {v1} is shown.']:
   s=ti(source,[formula(1,'FISCO stands for Fieldbus Intrinsically Safe COncept')] if '{v1}' in source else [])
   self.assertIsNone(g._source_fisco_expansion(s))

 def test_normal_post_accepts_correct_numeric_calendar_date_without_retry(self):
  s=ti('The record contains 27 measurements. Accessed 2011 Nov 26.')
  good='该记录包含27次测量。访问日期2011年11月26日。'
  p=paragraph(s);e=translator([]);g._guard_post(e,p,ParagraphTranslateTracker(),s,good)
  self.assertEqual(p.unicode,good);self.assertEqual(e.calls,[])
 def test_rich_acronym_definition_uses_whole_recovery_and_keeps_english_gate(self):
  placeholders=[]
  for index,value in [(1,'F'),(3,'I'),(5,'S'),(7,'CO')]:
   style=PdfStyle(font_id='F1',font_size=10)
   comp=PdfSameStyleCharacters(pdf_style=style,pdf_character=[PdfCharacter(char_unicode=c,pdf_style=style) for c in value])
   placeholders.append(RichTextPlaceholder(index,comp,f"<style id='{index}'>",'</style>',rf"<style id='{index}'>",r'</style>'))
  expansion="<style id='1'>F</style>ieldbus <style id='3'>I</style>ntrinsically <style id='5'>S</style>afe <style id='7'>CO</style>ncept"
  s=ti('FISCO stands for '+expansion+' and describes a procedure for safe fieldbus installations.',placeholders)
  bad='FISCO代表'+expansion+'，描述安全现场总线安装的程序。'
  good='FISCO代表现场总线本质安全概念，描述安全现场总线安装的程序。'
  english='FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），描述安全现场总线安装的程序。'
  for rejected in (bad,):
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'untranslated-prose-fragment'):self.body(s,rejected)
  e=translator([bad,bad,good]);p=paragraph(s);original=s.unicode
  g._guard_post(e,p,ParagraphTranslateTracker(),s,bad)
  self.assertEqual(p.unicode,good);self.assertEqual(s.unicode,original);self.assertEqual(len(e.calls),3)
  self.body(s,english)
  self.assertNotIn("<style id='",e.calls[-1][0])

 def test_exact_bilingual_expansion_preserves_complete_original_output(self):
  s=ti('FISCO stands for Fieldbus Intrinsically Safe COncept and describes a safe installation.')
  for chinese in ('现场总线本质安全概念','现场总线本安概念'):
   for opening,closing in (('（','）'),('(',')')):
    good='FISCO代表'+chinese+opening+'Fieldbus Intrinsically Safe COncept'+closing+'，描述安全安装。'
    p=paragraph(s);e=translator([]);g._guard_post(e,p,ParagraphTranslateTracker(),s,good)
    self.assertEqual(p.unicode,good);self.assertEqual(e.calls,[])
 def test_bilingual_exception_is_not_an_english_allowlist(self):
  s=ti('FISCO stands for Fieldbus Intrinsically Safe COncept and describes an intrinsically safe installation.')
  bads=['FISCO代表Fieldbus Intrinsically Safe COncept，描述本安安装。',
        'FISCO代表现场总线概念（Fieldbus Intrinsically Safe COncept），描述本安安装。',
        'FISCO代表现场总线非本质安全概念（Fieldbus Intrinsically Safe COncept），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Safe COncept），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），描述intrinsically安全安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），另有Fieldbus。',
        'FISCO不代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept)，描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept）。']
  for bad in bads:
   with self.subTest(output=bad),self.assertRaisesRegex(g.PlaceholderIntegrityError,'untranslated-prose-fragment'):
    self.body(s,bad)
  for bad in ('FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe Concept）。',
              'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept extra）。'):
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'untranslated-prose-fragment'):self.body(s,bad)

if __name__=='__main__':unittest.main()
