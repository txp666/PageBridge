import copy
import pickle
import unittest
from types import SimpleNamespace as NS
from test_layout_guard import (guard, line_paragraph, page, ParagraphFinder, PdfFont,
    PdfStyle, GraphicState, PdfSameStyleCharacters, PdfParagraphComposition, PdfFormula)


def characters(p):
    return [c for q in p.pdf_paragraph for co in q.pdf_paragraph_composition for c in guard._composition_characters(co)]


def punctuation_fixture(mark='.'):
    body = line_paragraph(['These original words describe an example'], size=11)
    tail = line_paragraph([mark], label='fallback_line', size=11)
    c = tail.pdf_paragraph_composition[0].pdf_line.pdf_character[0]
    last = body.pdf_paragraph_composition[0].pdf_line.pdf_character[-1]
    dx = last.box.x2+.01-c.box.x
    for b in (c.box,c.visual_bbox.box):b.x+=dx;b.x2+=dx
    p=page([]);p.pdf_paragraph=[body,tail]
    return p,body,tail


def grammar_fixture(title=False):
    left = line_paragraph(['Technical Brochures by' if title else
                          'The installed circuit follows the additional protection requirements of'],
                         label='title' if title else 'plain text',size=11)
    right = line_paragraph(['Example+Company' if title else
                           'IEC 60079-14. This also applies to marking the circuits and their separation.'],
                          label=left.layout_label,y=486,size=11)
    gs=GraphicState(passthrough_per_char_instruction='0 0 0 scn')
    for index,q in enumerate((left,right)):
        cs=q.pdf_paragraph_composition[0].pdf_line.pdf_character
        style=PdfStyle(font_id='A' if index==0 else 'B',font_size=11,graphic_state=gs)
        q.pdf_style=style
        for c in cs:c.pdf_style=style
        q.pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_characters=
            PdfSameStyleCharacters(pdf_character=cs,pdf_style=style,box=copy.copy(q.box)))]
        if not title:q.box.x2=450
    if title:
        cs=right.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character
        i=next(i for i,c in enumerate(cs)if c.char_unicode=='+')
        formula=PdfFormula(pdf_character=[cs[i]],box=copy.copy(cs[i].box),x_offset=0,y_offset=0)
        right.pdf_paragraph_composition=[
            PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(pdf_character=cs[:i],pdf_style=right.pdf_style)),
            PdfParagraphComposition(pdf_formula=formula),
            PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(pdf_character=cs[i+1:],pdf_style=right.pdf_style))]
    p=page([]);p.pdf_paragraph=[left,right]
    p.pdf_font=[PdfFont(font_id='A',name='ABCDEF+Example-Regular',xref_id=10),
                PdfFont(font_id='B',name='GHIJKL+Example-Regular',xref_id=20)]
    return p,left,right


class NativePunctuationTests(unittest.TestCase):
    def test_terminal_single_mark_preserves_native_glyph_order_and_properties(self):
        for mark in ('.',','):
            p,a,b=punctuation_fixture(mark)
            before=characters(p);state=pickle.dumps(before);ids=list(map(id,before))
            self.assertEqual(guard.merge_source_terminal_punctuation(object.__new__(ParagraphFinder),p),1)
            self.assertEqual(list(map(id,characters(p))),ids)
            self.assertEqual(pickle.dumps(characters(p)),state)
            self.assertTrue(a.unicode.endswith('example'+mark))
            self.assertEqual(guard.merge_source_terminal_punctuation(object.__new__(ParagraphFinder),p),0)

    def test_terminal_mark_rejects_nonprose_or_ambiguous_geometry(self):
        for mode in ('far','line','font','size','object','formula','duplicate','number','intervening','punctuation-sequence'):
            with self.subTest(mode=mode):
                p,a,b=punctuation_fixture()
                c=characters(p)[-1]
                if mode=='far':c.box.x+=3;c.box.x2+=3
                elif mode=='line':c.box.y-=14;c.box.y2-=14
                elif mode=='font':c.pdf_style.font_id='Other'
                elif mode=='size':c.pdf_style.font_size=8
                elif mode=='object':b.xobj_id=5
                elif mode=='formula':c.formula_layout_id=1
                elif mode=='duplicate':p.pdf_paragraph.append(copy.deepcopy(b))
                elif mode=='number':a.pdf_paragraph_composition[0].pdf_line.pdf_character[-1].char_unicode='7'
                elif mode=='intervening':p.pdf_paragraph.insert(1,line_paragraph(['Different source paragraph'],y=300))
                elif mode=='punctuation-sequence':c.char_unicode='..'
                self.assertEqual(guard.merge_source_terminal_punctuation(object.__new__(ParagraphFinder),p),0)


class OpenGrammarJoinTests(unittest.TestCase):
    def test_body_mixed_subset_runs_are_kept_and_union_is_native(self):
        for title in (False,True):
            with self.subTest(title=title):
                p,a,b=grammar_fixture(title)
                before=characters(p);state=pickle.dumps(before);ids=list(map(id,before))
                formulas=[co.pdf_formula for co in b.pdf_paragraph_composition if co.pdf_formula]
                formula_ids=list(map(id,formulas));formula_state=pickle.dumps(formulas)
                comps=list(a.pdf_paragraph_composition)+list(b.pdf_paragraph_composition)
                expected_box=(min(a.box.x,b.box.x),min(a.box.y,b.box.y),max(a.box.x2,b.box.x2),max(a.box.y2,b.box.y2))
                source=a.unicode+' '+b.unicode
                self.assertEqual(guard.merge_source_open_grammar_blocks(NS(translation_config=NS(skip_translation=False)),p),1)
                self.assertEqual(p.pdf_paragraph,[a]);self.assertEqual(a.unicode,source)
                self.assertEqual(a.pdf_paragraph_composition,comps)
                self.assertEqual(list(map(id,characters(p))),ids)
                self.assertEqual(pickle.dumps(characters(p)),state)
                self.assertEqual(tuple(getattr(a.box,k)for k in ('x','y','x2','y2')),expected_box)
                self.assertTrue(guard.source_sentence_was_rejoined(a))
                self.assertEqual([co.pdf_formula for co in a.pdf_paragraph_composition if co.pdf_formula],formulas)
                after_formulas=[co.pdf_formula for co in a.pdf_paragraph_composition if co.pdf_formula]
                self.assertEqual(list(map(id,after_formulas)),formula_ids)
                self.assertEqual(pickle.dumps(after_formulas),formula_state)
                self.assertEqual(guard.merge_source_open_grammar_blocks(NS(translation_config=NS(skip_translation=False)),p),0)

    def test_grammar_join_requires_source_geometry_style_and_noninterference(self):
        for mode in ('complete','label','font-family','weight','size','fill','gap','column','overlap','intervening','formula','duplicate'):
            with self.subTest(mode=mode):
                p,a,b=grammar_fixture()
                if mode=='complete':a.unicode+='.'
                elif mode=='label':b.layout_label='table'
                elif mode=='font-family':p.pdf_font[-1].name='GHIJKL+Other-Regular'
                elif mode=='weight':p.pdf_font[-1].bold=True
                elif mode=='size':b.pdf_style.font_size=10
                elif mode=='fill':b.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction='1 0 0 scn')
                elif mode=='gap':
                    for c in guard._definition_chars(b):c.box.y-=20;c.box.y2-=20
                elif mode=='column':b.box.x+=100;b.box.x2+=100
                elif mode=='overlap':
                    other=line_paragraph(['interference'],y=485)
                    p.pdf_paragraph.append(other)
                elif mode=='intervening':p.pdf_paragraph.insert(1,line_paragraph(['other'],y=300))
                elif mode=='formula':
                    co=b.pdf_paragraph_composition[0];co.pdf_formula=PdfFormula(pdf_character=co.pdf_same_style_characters.pdf_character);co.pdf_same_style_characters=None
                elif mode=='duplicate':p.pdf_character.append(guard._definition_chars(b)[0])
                self.assertEqual(guard.merge_source_open_grammar_blocks(NS(translation_config=NS(skip_translation=False)),p),0)

    def test_title_plus_remains_exact_atomic_source_and_is_not_general_math(self):
        p,a,b=grammar_fixture(True)
        plus=b.pdf_paragraph_composition[1].pdf_formula
        plus.pdf_character[0].char_unicode='='
        b.unicode=b.unicode.replace('+','=')
        self.assertEqual(guard.merge_source_open_grammar_blocks(NS(translation_config=NS(skip_translation=False)),p),0)
        p,a,b=grammar_fixture(True)
        b.unicode='A+B'
        self.assertEqual(guard.merge_source_open_grammar_blocks(NS(translation_config=NS(skip_translation=False)),p),0)


if __name__=='__main__':unittest.main()
