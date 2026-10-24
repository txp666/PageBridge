"""Portable native segmentation tests; no PDF fixtures or model calls."""
from collections import Counter
from copy import deepcopy
import pickle
from types import SimpleNamespace as NS
import unittest
from test_page_definition_native import body, chars, engine
import layout_guard as g
from babeldoc.format.pdf.document_il import (Box, Document, GraphicState, Mediabox, Page,
    PdfCharacter, PdfFont, PdfFormula, PdfParagraph, PdfParagraphComposition, PdfSameStyleCharacters, PdfStyle, VisualBbox)


def research_fixture():
    left=body('Scientific models provide useful observation datasets,',315,90,'left')
    tail=body('compound libraries for drug research and',315,78,'tail')
    left.unicode+=' '+tail.unicode;left.pdf_paragraph_composition+=tail.pdf_paragraph_composition;left.box.y=78
    right=body('development, design of catalysts is included in the research programme.',50,400,'right')
    otherleft=body('This ordinary source paragraph establishes the other column on this source page.',50,300,'otherleft')
    otherright=body('This ordinary source paragraph establishes the other column on this source page.',315,600,'otherright')
    caption=body('Figure title for a separate floating figure.',50,450,'caption');caption.layout_label='figure_caption'
    font=PdfFont(font_id='F1',name='ABCDEF+Body',xref_id=33,encoding_length=1,ascent=.8,descent=-.2)
    pages=[Page(page_number=i,mediabox=Mediabox(Box(0,0,612,800)),pdf_font=[deepcopy(font)],pdf_paragraph=ps)
           for i,ps in enumerate(([otherleft,left],[caption,right,otherright]))]
    return Document(page=pages),left,right


def bullet_fixture():
    style=PdfStyle(font_id='F1',font_size=11,graphic_state=GraphicState())
    def run(text,x):
        cs=[]
        for i,c in enumerate(text):
            box=Box(x+5*i,100,x+5*(i+1),111)
            cs.append(PdfCharacter(char_unicode=c,pdf_character_id=ord(c),pdf_style=deepcopy(style),box=box,
                visual_bbox=VisualBbox(deepcopy(box)),vertical=False,xobj_id=0,advance=5))
        return PdfSameStyleCharacters(pdf_character=cs,pdf_style=deepcopy(style),box=Box(x,100,x+5*len(text),111))
    marker=run('• ',64);prep=run('In',74);rest=run('all other cases, the original value applies.',88)
    formula=PdfFormula(pdf_character=marker.pdf_character,box=deepcopy(marker.box))
    left=PdfParagraph(unicode='• In',box=Box(64,100,84,111),pdf_style=deepcopy(style),vertical=False,
        xobj_id=0,layout_label='plain text',debug_id='short',first_line_indent=False,
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=formula),PdfParagraphComposition(pdf_same_style_characters=prep)])
    right=PdfParagraph(unicode='all other cases, the original value applies.',box=deepcopy(rest.box),pdf_style=deepcopy(style),vertical=False,
        xobj_id=0,layout_label='plain text',debug_id='body',first_line_indent=False,
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_characters=rest)])
    return Page(page_number=0,pdf_paragraph=[left,right]),left,right,formula


class ResearchPhraseLayoutTests(unittest.TestCase):
    def test_native_research_phrase_preserves_glyphs_boxes_and_following_item(self):
        doc,left,right=research_fixture();before=[c for page in doc.page for p in page.pdf_paragraph for c in chars(p)]
        states={id(c):deepcopy(c) for c in before};boxes=deepcopy([left.box,right.box,left.pdf_style,right.pdf_style])
        self.assertEqual(g.rejoin_source_page_research_phrases(engine(),doc),1)
        self.assertEqual(left.unicode,'Scientific models provide useful observation datasets,')
        self.assertTrue(right.unicode.startswith('compound libraries for drug research and development, design of catalysts'))
        after=[c for page in doc.page for p in page.pdf_paragraph for c in chars(p)]
        self.assertEqual(Counter(map(id,before)),Counter(map(id,after)))
        self.assertTrue(all(c==states[id(c)] for c in after));self.assertEqual([left.box,right.box,left.pdf_style,right.pdf_style],boxes)
        self.assertTrue(g.source_sentence_was_rejoined(left));self.assertTrue(g.source_sentence_was_rejoined(right))
        self.assertEqual(g.rejoin_source_page_research_phrases(engine(),doc),0)

    def test_source_grammar_geometry_and_resource_ambiguities_fail_closed(self):
        for case in ('gap','indent','title','different_font_resource','different_fill','open_bracket','wrong_word',
                     'native_text_mismatch','tail_formula','script','different_xobject','not_last_line','not_last_body','skip'):
            with self.subTest(case=case):
                doc,left,right=research_fixture();e=engine()
                if case=='gap':doc.page[1].page_number=3
                if case=='indent':right.first_line_indent=True
                if case=='title':doc.page[1].pdf_paragraph[0].layout_label='title'
                if case=='different_font_resource':doc.page[1].pdf_font[0].xref_id=34
                if case=='different_fill':right.pdf_style.graphic_state.passthrough_per_char_instruction='1 0 0 rg'
                if case=='open_bracket':left.unicode='('+left.unicode
                if case=='wrong_word':right.unicode=right.unicode.replace('development,','production,')
                if case=='native_text_mismatch':chars(left)[-1].char_unicode='x'
                if case=='tail_formula':
                    run=left.pdf_paragraph_composition[-1].pdf_same_style_characters
                    left.pdf_paragraph_composition[-1]=PdfParagraphComposition(pdf_formula=PdfFormula(pdf_character=run.pdf_character,box=run.box))
                if case=='script':chars(left)[-1].formula_layout_id=1
                if case=='different_xobject':chars(left)[-1].xobj_id=2
                if case=='not_last_line':chars(left)[-1].box.y+=3
                if case=='not_last_body':doc.page[0].pdf_paragraph.append(body('Some more body text follows the proposed list fragment.',315,50,'later'))
                if case=='skip':e=engine(skip_translation=True)
                before=pickle.dumps(doc)
                self.assertEqual(g.rejoin_source_page_research_phrases(e,doc),0)
                self.assertEqual(pickle.dumps(doc),before)

    def test_bullet_preposition_keeps_original_formula_and_geometry(self):
        page,left,right,formula=bullet_fixture();before=chars(left)+chars(right);states={id(c):deepcopy(c) for c in before}
        self.assertEqual(g.merge_source_bullet_prepositions(engine(),page),1)
        self.assertEqual(len(page.pdf_paragraph),1);self.assertIs(left.pdf_paragraph_composition[0].pdf_formula,formula)
        self.assertEqual(left.unicode,'• In all other cases, the original value applies.')
        self.assertEqual(list(map(id,chars(left))),list(map(id,before)))
        self.assertTrue(all(c==states[id(c)] for c in chars(left)))
        self.assertEqual(left.box,Box(64,100,right.box.x2,111));self.assertEqual(g._hanging_indents[id(left)][1],10)
        self.assertEqual(g.merge_source_bullet_prepositions(engine(),page),0)

    def test_bullet_preposition_rejects_detached_formula_title_and_math_in(self):
        for case in ('gap','baseline','xobject','fill','title','uppercase','formula_in','other_marker','unicode_mismatch'):
            with self.subTest(case=case):
                page,left,right,formula=bullet_fixture()
                if case=='gap':chars(right)[0].box.x+=30
                if case=='baseline':chars(right)[0].box.y-=12
                if case=='xobject':right.xobj_id=2
                if case=='fill':right.pdf_style.graphic_state.passthrough_per_char_instruction='1 0 0 rg'
                if case=='title':right.layout_label='title'
                if case=='uppercase':right.unicode='All'+right.unicode[3:]
                if case=='formula_in':
                    run=left.pdf_paragraph_composition[-1].pdf_same_style_characters
                    left.pdf_paragraph_composition[-1]=PdfParagraphComposition(pdf_formula=PdfFormula(pdf_character=run.pdf_character,box=run.box))
                if case=='other_marker':formula.pdf_character[0].char_unicode='X'
                if case=='unicode_mismatch':chars(right)[-1].char_unicode='x'
                before=pickle.dumps(page)
                self.assertEqual(g.merge_source_bullet_prepositions(engine(),page),0);self.assertEqual(pickle.dumps(page),before)


if __name__=='__main__':unittest.main()
