import json
import unittest
from unittest.mock import patch
from test_translation_guard import guard,translator,paragraph,ILTranslator,ParagraphTranslateTracker,PdfStyle,PdfCharacter,PdfFormula,FormulaPlaceholder

PAIRS=(('Voltage','U','电压'),('Current','I','电流'),('Power','P','功率'),('Inductance','L','电感'),('Capacitance','C','电容'))
def source(name='Power',symbol='P',bullet='formula'):
    formulas=[]
    prefix='• 'if bullet=='plain'else''
    if bullet=='formula':
        formula=PdfFormula(pdf_character=[PdfCharacter(char_unicode='•'),PdfCharacter(char_unicode=' ')])
        formulas=[FormulaPlaceholder(1,formula,'{v1}',r'\{v1\}')];prefix='{v1}'
    ti=ILTranslator.TranslateInput(prefix+name+' '+symbol,formulas,PdfStyle())
    ti._pagebridge_layout_label='plain_text'
    return ti,prefix

class QuantitySymbolLabelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):guard.install()
    def setUp(self):guard._states.clear();guard._counters.clear()
    def test_all_five_quantity_symbol_pairs_accept_literal_native_or_no_bullet(self):
        for name,symbol,chinese in PAIRS:
            for bullet in('formula','plain','none'):
                with self.subTest(name=name,bullet=bullet):
                    ti,prefix=source(name,symbol,bullet);p=paragraph(ti);il=translator();before=ti.unicode
                    il.post_translate_paragraph(p,ParagraphTranslateTracker(),ti,prefix+chinese+' '+symbol)
                    self.assertEqual(p.unicode,prefix+chinese+' '+symbol);self.assertEqual(ti.unicode,before);self.assertEqual(il.calls,[])
                    if ti.placeholders:self.assertIs(next(c.pdf_formula for c in p.pdf_paragraph_composition if c.pdf_formula),ti.placeholders[0].formula)
    def test_missing_changed_duplicate_or_wrong_term_are_rejected(self):
        ti,_=source()
        for bad in('{v1}电源','{v1}功率','{v1}功率 p','{v1}功率 R','{v1}功率 P P','{v1}电源 P','{v1}电压 P','功率 P{v1}'):
            with self.subTest(bad=bad),self.assertRaisesRegex(guard.PlaceholderIntegrityError,'physical-quantity-symbol-label-changed'):
                guard._check_body_translation(translator(),paragraph(ti),ti,bad)
    def test_positive_target_punctuation_keeps_explicit_quantity_symbol_pair(self):
        ti,_=source()
        for good in('{v1}功率P','{v1}功率：P','{v1}功率（P）','{v1}功率 (P)。'):
            guard._check_quantity_symbol_label(ti,good)
    def test_no_rule_for_source_prose_quotes_punctuation_code_or_wrong_symbol(self):
        for value in('Power','Power supply','Power P.','Power: P','"Power P"',"'Power P'",'The power P is measured.','P','Power p','Power R','Power P = 10 W','Power P and Current I'):
            ti=ILTranslator.TranslateInput(value,[],PdfStyle());ti._pagebridge_layout_label='plain_text'
            self.assertIsNone(guard._source_quantity_symbol_label(ti),value)
        for label in('code','formula','equation','reference','references','bibliography'):
            ti,_=source();ti._pagebridge_layout_label=label
            self.assertIsNone(guard._source_quantity_symbol_label(ti))
    def test_unknown_or_nonbullet_formula_does_not_authorize_label(self):
        ti,_=source();ti.placeholders[0].formula.pdf_character[0].char_unicode='P'
        self.assertIsNone(guard._source_quantity_symbol_label(ti))
        ti.placeholders=[];self.assertIsNone(guard._source_quantity_symbol_label(ti))
    def test_initial_and_batch_hints_bind_exact_source(self):
        ti,_=source();il=translator()
        with patch.dict(guard._originals,single_prompt_context=lambda self,text,**kw:text):
            result=guard._guard_single_prompt_context(il,ti.unicode,translate_input=ti)
        self.assertIn('paired as 功率 P',result);self.assertTrue(result.endswith(ti.unicode))
        token=guard._review_batch.set([ti])
        try:
            with patch.dict(guard._originals,batch_prompt_context=lambda self,text:text):
                result=guard._guard_batch_prompt_context(il,json.dumps([{'id':'a','input':ti.unicode}]))
                self.assertIn('paired as 功率 P',result)
                other=guard._guard_batch_prompt_context(il,json.dumps([{'id':'a','input':'Power supply'}]))
                self.assertNotIn('paired as 功率 P',other)
        finally:guard._review_batch.reset(token)
    def test_bad_complete_label_uses_normal_retry_and_keeps_bullet_formula(self):
        ti,_=source();p=paragraph(ti);il=translator(['{v1}功率 P']);f=ti.placeholders[0].formula
        il.post_translate_paragraph(p,ParagraphTranslateTracker(),ti,'{v1}电源')
        self.assertEqual(p.unicode,'{v1}功率 P');self.assertEqual(len(il.calls),1)
        self.assertIn(ti.unicode,il.calls[0][0]);self.assertIn('paired as 功率 P',il.calls[0][0])
        self.assertIs(next(c.pdf_formula for c in p.pdf_paragraph_composition if c.pdf_formula),f)
        self.assertEqual(guard.snapshot()['unresolvedCount'],0)
    def test_unchanged_formula_and_numeric_gates_still_apply(self):
        ti,_=source()
        for bad in('功率 P','{v2}功率 P','{v1}{v1}功率 P','{v1}功率 P 100'):
            with self.subTest(bad=bad),self.assertRaises(guard.PlaceholderIntegrityError):
                normalized=guard.normalize_output(ti,bad)
                guard._check_body_translation(translator(),paragraph(ti),ti,normalized)
    def test_non_chinese_target_keeps_existing_full_post_behavior(self):
        ti,_=source();p=paragraph(ti);il=translator();il.translation_config.lang_out='fr'
        with patch.dict(guard._originals,single_prompt_context=lambda self,text,**kw:text):
            prompt=guard._guard_single_prompt_context(il,ti.unicode,translate_input=ti)
        self.assertNotIn('paired as 功率 P',prompt)
        il.post_translate_paragraph(p,ParagraphTranslateTracker(),ti,'{v1}Puissance P')
        self.assertEqual(p.unicode,'{v1}Puissance P');self.assertEqual(il.calls,[])
        self.assertIs(next(c.pdf_formula for c in p.pdf_paragraph_composition if c.pdf_formula),ti.placeholders[0].formula)

if __name__=='__main__':unittest.main()
