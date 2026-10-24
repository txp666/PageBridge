"""Native measurement/bullet prefixes cannot be relocated as sentence objects."""
from copy import deepcopy
import unittest
try:
    from .test_guard_review_boundaries import g,ti,translator,paragraph,ParagraphTranslateTracker
except ImportError:
    from test_guard_review_boundaries import g,ti,translator,paragraph,ParagraphTranslateTracker
from babeldoc.format.pdf.document_il import Box,PdfCharacter,PdfStyle,PdfFormula
from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder


def source(number='3'):
    glyphs=[]
    for i,char in enumerate('• '+number+' '):
        glyphs.append(PdfCharacter(char_unicode=char,pdf_character_id=ord(char),pdf_style=PdfStyle(font_id='F1',font_size=11),
            box=Box(60+6*i,100,66+6*i,111),xobj_id=0,vertical=False,formula_layout_id=None))
    f=PdfFormula(pdf_character=glyphs,box=Box(60,100,60+6*len(glyphs),111))
    s=ti('{v1}mm between conductive parts and separate circuits',[FormulaPlaceholder(1,f,'{v1}',r'\{v1\}')])
    s._pagebridge_layout_label='plain_text';return s


class NativeListPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()
    def test_native_list_quantity_must_precede_the_between_relation(self):
        for n in ('3','6','0.5'):
            s=source(n);before=deepcopy(s.placeholders[0].formula)
            self.assertEqual(g._source_measured_list_prefix(s),('{v1}','mm'))
            self.assertEqual(g.normalize_output(s,'{v1}mm：导电部件与独立电路之间。'),'{v1}mm：导电部件与独立电路之间。')
            for bad in ('导电部件与独立电路之间为{v1}mm。','{v1}导电部件之间为mm。'):
                with self.assertRaisesRegex(g.PlaceholderIntegrityError,'source-list-prefix-displaced'):g.normalize_output(s,bad)
            self.assertEqual(s.placeholders[0].formula,before)
            self.assertIn('at the beginning',g._source_review_hint(s))
    def test_no_native_list_proof_means_no_new_rule(self):
        for case in ('middle','unit','between','label','quote','operator','missing_glyph_id','reverse','detached','script','vertical','xobject','nan','no_bullet'):
            with self.subTest(case=case):
                s=source();cs=s.placeholders[0].formula.pdf_character
                if case=='middle':s.unicode='The value is '+s.unicode
                if case=='unit':s.unicode=s.unicode.replace('mm','kg')
                if case=='between':s.unicode=s.unicode.replace('between','within')
                if case=='label':s._pagebridge_layout_label='equation'
                if case=='quote':s.unicode='"'+s.unicode+'"'
                if case=='operator':cs[0].char_unicode='×'
                if case=='missing_glyph_id':cs[2].pdf_character_id=None
                if case=='reverse':cs[2].box.x=40;cs[2].box.x2=46
                if case=='detached':cs[2].box.y+=20;cs[2].box.y2+=20
                if case=='script':cs[2].formula_layout_id=3
                if case=='vertical':cs[2].vertical=True
                if case=='xobject':cs[2].xobj_id=2
                if case=='nan':cs[2].box.x=float('nan')
                if case=='no_bullet':cs[0].char_unicode=' '
                self.assertIsNone(g._source_measured_list_prefix(s));self.assertNotIn('list bullet',g._source_review_hint(s))
    def test_formula_count_unknown_tokens_and_normal_post_stay_strict(self):
        s=source();p=paragraph(s);p.layout_label='plain text';f=s.placeholders[0].formula
        e=translator(['{v1}mm：导电部件与独立电路之间。'])
        g._guard_post(e,p,ParagraphTranslateTracker(),s,'导电部件与独立电路之间为{v1}mm。')
        self.assertEqual(len(e.calls),1);self.assertIs(p.pdf_paragraph_composition[0].pdf_formula,f)
        self.assertEqual(p.unicode,'{v1}mm：导电部件与独立电路之间。')
        for bad in ('3mm：导电部件之间。','{v1}mm{v1}：导电部件之间。','{v1}mm{v99}：导电部件之间。'):
            with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(s,bad)

if __name__=='__main__':unittest.main()
