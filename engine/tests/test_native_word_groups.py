import copy
import pickle
import unittest
from test_layout_guard import (guard, character, page, PdfParagraph, PdfParagraphComposition,
    PdfSameStyleCharacters, PdfFormula, PdfStyle, GraphicState, Box, VisualBbox)
from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas

def styler():return object.__new__(StylesAndFormulas)
def chars(p):return [c for q in p.pdf_paragraph for co in q.pdf_paragraph_composition for c in guard._composition_characters(co)]
def run(cs):return styler()._create_same_style_composition(cs,cs[0].pdf_style)
def text(value,x=10,y=500):
    cs=[character(c,ord(c),font_id='F2',x=x+5*i,y=y)for i,c in enumerate(value)]
    for c in cs:
        c.box.x2=c.box.x+5;c.visual_bbox.box.x2=c.box.x2
    return cs
def fixture(author=False,blank=False):
    prefix='Smith, J., 'if author else'Networks of '
    left=prefix+('Dure'if author else'Muller-Re')
    right=('t, L., 2024. A documented title.'if author else'nard are used.')
    before=text(left);base=before[-1]
    mark=character('\u0307'if author else'´',10,font_id='F2',x=base.box.x+2,y=500.2)
    mark.box.x2=mark.box.x+1
    mark.visual_bbox=VisualBbox(Box(mark.box.x,506,mark.box.x2,507))
    following=text(right,x=base.box.x2)
    if blank:
        # Accent appears before its source vowel with a synthetic overlapping
        # blank. The glyph stream and invisible blank must both survive.
        vowel=before.pop();before.append(character(' ',None,font_id='F2',x=vowel.box.x,y=500))
        before[-1].xobj_id=None;before[-1].box.x2=vowel.box.x+1
        before[-1].visual_bbox.box.x2=vowel.box.x+1
        following.insert(0,vowel)
    formula=PdfFormula(pdf_character=[mark]);styler().update_formula_data(formula)
    q=PdfParagraph(layout_label='plain text',vertical=False,xobj_id=-1,
        pdf_style=before[0].pdf_style,box=Box(10,500,500,510),
        pdf_paragraph_composition=[run(before),PdfParagraphComposition(pdf_formula=formula),run(following)])
    q.unicode=''.join(c.char_unicode or ''for co in q.pdf_paragraph_composition for c in guard._composition_characters(co))
    p=page([]);p.pdf_paragraph=[q]
    return p,q,mark,base

def article_fixture():
    before=text('Smith, J., 2024. Neural models:');a=text('A',x=10,y=488)[0]
    blank=text(' ',x=15,y=488)[0];blank.pdf_style.font_id='F3';blank.pdf_style.font_size=10
    following=text('deep learning framework is available.',x=19,y=488)
    f=PdfFormula(pdf_character=[a,blank]);styler().update_formula_data(f)
    q=PdfParagraph(layout_label='plain text',vertical=False,xobj_id=-1,
      pdf_style=a.pdf_style,box=Box(10,488,500,510),
      pdf_paragraph_composition=[run(before),run(text(' ',x=before[-1].box.x2)),PdfParagraphComposition(pdf_formula=f),run(following)])
    q.unicode='Smith, J., 2024. Neural models: A deep learning framework is available.'
    p=page([]);p.pdf_paragraph=[q]
    return p,q,a,following[0]

def overprint_fixture(pronoun=False):
    before=text('We compare NetABC. 'if not pronoun else'we use the method in ')
    lead=text('w'if pronoun else'N',x=150)[0];lead.pdf_style.font_id='F8'
    duplicate=copy.deepcopy(lead)
    suffix=text('e show how the system works.'if pronoun else'etABC: parameters are varied.',x=155)
    prefix=[]
    if pronoun:
        quote=character('€',160,font_id='F21',x=151,y=500)
        quote.box.x2=155;quote.visual_bbox=VisualBbox(Box(151,505,153,505.7))
        opening=copy.deepcopy(quote);opening.box.x-=40;opening.box.x2-=40
        opening.visual_bbox.box.x-=40;opening.visual_bbox.box.x2-=40
        first=PdfFormula(pdf_character=[opening]);styler().update_formula_data(first)
        prefix=[PdfParagraphComposition(pdf_formula=first),run(text('section title ',x=90))]
        native=[quote,lead,duplicate]
    else:native=[lead,duplicate]
    f=PdfFormula(pdf_character=native);styler().update_formula_data(f)
    q=PdfParagraph(layout_label='plain text',vertical=False,xobj_id=-1,box=Box(10,490,500,510),
        pdf_style=before[0].pdf_style,pdf_paragraph_composition=[run(before)]+prefix+[PdfParagraphComposition(pdf_formula=f),run(suffix)])
    q.unicode=''.join(c.char_unicode or ''for co in q.pdf_paragraph_composition for c in guard._composition_characters(co))
    p=page([]);p.pdf_paragraph=[q]
    return p,q,f,lead,duplicate

class NativeWordGroupTests(unittest.TestCase):
    def assert_preserved(self,p,before,values):
        self.assertEqual(list(map(id,chars(p))),list(map(id,before)))
        self.assertEqual(pickle.dumps(chars(p)),values)

    def test_accented_hyphenated_name_keeps_all_native_glyphs(self):
        p,q,mark,base=fixture();before=chars(p);values=pickle.dumps(before)
        self.assertEqual(guard.bind_source_accented_names(styler(),p),1)
        self.assert_preserved(p,before,values)
        formulas=[c.pdf_formula for c in q.pdf_paragraph_composition if c.pdf_formula]
        self.assertEqual(len(formulas),1)
        self.assertEqual(''.join(c.char_unicode for c in formulas[0].pdf_character),'Muller-Re´nard')
        self.assertEqual(guard.bind_source_accented_names(styler(),p),0)

    def test_author_name_preserves_overlapping_synthetic_blank(self):
        p,q,mark,base=fixture(author=True,blank=True);before=chars(p);values=pickle.dumps(before)
        self.assertEqual(guard.bind_source_accented_names(styler(),p),1)
        self.assert_preserved(p,before,values)
        self.assertIn('Dur \u0307et',''.join(c.char_unicode for c in next(c.pdf_formula for c in q.pdf_paragraph_composition if c.pdf_formula).pdf_character))

    def test_accent_rejects_non_name_or_ambiguous_geometry(self):
        for mode in ('lowercase','single-prose-word','other-object','font','size','formula-layout','raised-script','remote-mark','real-space','cross-line'):
            with self.subTest(mode=mode):
                p,q,mark,base=fixture()
                if mode=='lowercase':
                    next(c for c in chars(p)if c.char_unicode=='M').char_unicode='m'
                elif mode=='single-prose-word':
                    next(c for c in chars(p)if c.char_unicode=='-').char_unicode=' '
                elif mode=='other-object':mark.xobj_id=8
                elif mode=='font':mark.pdf_style.font_id='Different'
                elif mode=='size':mark.pdf_style.font_size=5
                elif mode=='formula-layout':mark.formula_layout_id=4
                elif mode=='raised-script':mark.box.y+=4
                elif mode=='remote-mark':mark.visual_bbox.box.x+=30;mark.visual_bbox.box.x2+=30
                elif mode=='real-space':
                    before=q.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character
                    before.append(text(' ',x=base.box.x2)[0])
                elif mode=='cross-line':base.box.y-=10
                before=chars(p);values=pickle.dumps(before)
                self.assertEqual(guard.bind_source_accented_names(styler(),p),0)
                self.assert_preserved(p,before,values)

    def test_overprint_name_attested_elsewhere_keeps_two_original_letters(self):
        p,q,f,a,b=overprint_fixture();before=chars(p);values=pickle.dumps(before)
        self.assertEqual(guard.bind_source_overprinted_words(styler(),p),1)
        self.assert_preserved(p,before,values)
        group=next(co.pdf_formula for co in q.pdf_paragraph_composition if co.pdf_formula)
        self.assertEqual(''.join(c.char_unicode for c in group.pdf_character),'NNetABC')
        self.assertIs(group.pdf_character[0],a);self.assertIs(group.pdf_character[1],b)
        self.assertEqual(guard.bind_source_overprinted_words(styler(),p),0)

    def test_overprint_pronoun_demotes_only_letters_and_preserves_quote(self):
        p,q,f,a,b=overprint_fixture(True);before=chars(p);values=pickle.dumps(before)
        quote=f.pdf_character[0]
        self.assertEqual(guard.bind_source_overprinted_words(styler(),p),1)
        self.assert_preserved(p,before,values)
        self.assertEqual(guard.source_native_overprint_words(q),(('wwe','we'),))
        self.assertTrue(any(co.pdf_formula and co.pdf_formula.pdf_character==[quote]for co in q.pdf_paragraph_composition))
        self.assertFalse(any(a in co.pdf_formula.pdf_character for co in q.pdf_paragraph_composition if co.pdf_formula))
        self.assertEqual(guard.bind_source_overprinted_words(styler(),p),0)

    def test_overprint_does_not_demote_math_currency_or_distinct_letters(self):
        for mode in ('different-position','different-glyph','currency-shape','no-other-quote','variable','no-attestation','different-object','raised','space-before-tail'):
            with self.subTest(mode=mode):
                p,q,f,a,b=overprint_fixture(True)
                if mode=='different-position':b.box.x+=1
                elif mode=='different-glyph':b.pdf_character_id+=1
                elif mode=='currency-shape':f.pdf_character[0].visual_bbox.box.y=500
                elif mode=='no-other-quote':q.pdf_paragraph_composition[1].pdf_formula.pdf_character[0].pdf_character_id+=1
                elif mode=='variable':a.formula_layout_id=b.formula_layout_id=9
                elif mode=='no-attestation':q.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[0].char_unicode='x'
                elif mode=='different-object':a.xobj_id=b.xobj_id=9
                elif mode=='raised':a.box.y+=2;b.box.y+=2
                elif mode=='space-before-tail':q.pdf_paragraph_composition[-1].pdf_same_style_characters.pdf_character[0].box.x+=2
                before=chars(p);values=pickle.dumps(before)
                self.assertEqual(guard.bind_source_overprinted_words(styler(),p),0)
                self.assert_preserved(p,before,values)

    def test_article_keeps_original_a_and_space_style(self):
        p,q,a,following=article_fixture();before=chars(p);values=pickle.dumps(before)
        self.assertEqual(guard.restore_source_reference_articles(styler(),p),1)
        self.assert_preserved(p,before,values)
        self.assertFalse(any(c.pdf_formula for c in q.pdf_paragraph_composition))
        self.assertEqual(guard.restore_source_reference_articles(styler(),p),0)

    def test_article_rejects_math_and_unproven_reference_boundaries(self):
        for mode in ('no-authors','no-year','no-colon','formula-layout','superscript','font','remote','xobject','not-prose'):
            with self.subTest(mode=mode):
                p,q,a,following=article_fixture()
                if mode=='no-authors':q.unicode='A text description from 2024.'
                elif mode=='no-year':q.unicode=q.unicode.replace('2024.','recently')
                elif mode=='no-colon':q.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[-1].char_unicode='.'
                elif mode=='formula-layout':a.formula_layout_id=5
                elif mode=='superscript':a.box.y+=4
                elif mode=='font':a.pdf_style.font_id='Math'
                elif mode=='remote':following.box.x+=20
                elif mode=='xobject':a.xobj_id=10
                elif mode=='not-prose':following.char_unicode='='
                before=chars(p);values=pickle.dumps(before)
                self.assertEqual(guard.restore_source_reference_articles(styler(),p),0)
                self.assert_preserved(p,before,values)

if __name__=='__main__':unittest.main()
