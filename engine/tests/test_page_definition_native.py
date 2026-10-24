"""Portable source segmentation regression; no PDFs, pickles or network."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import pickle
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout_guard as guard
from babeldoc.format.pdf.document_il import (
    Box, Document, GraphicState, Mediabox, Page, PdfCharacter, PdfFont,
    PdfFormula, PdfParagraph, PdfParagraphComposition, PdfSameStyleCharacters,
    PdfStyle, VisualBbox,
)


def body(text,x,y,identifier):
    remaining=text;rows=[]
    while len(remaining)>58:
        cut=remaining.rfind(' ',0,59)+1
        if cut<=0:cut=58
        rows.append(remaining[:cut]);remaining=remaining[cut:]
    rows.append(remaining)
    style=PdfStyle(font_id='F1',font_size=8,graphic_state=GraphicState())
    chars=[]
    for row_index,row in enumerate(rows):
        step=240/len(row);baseline=y-12*row_index
        for i,c in enumerate(row):
            b=Box(x+i*step,baseline,x+(i+1)*step,baseline+8)
            chars.append(PdfCharacter(char_unicode=c,pdf_character_id=ord(c),
                pdf_style=deepcopy(style),box=b,visual_bbox=VisualBbox(deepcopy(b)),
                vertical=False,xobj_id=0,advance=step))
    box=Box(x,min(c.box.y for c in chars),x+240,y+8)
    return PdfParagraph(unicode=text,box=box,pdf_style=style,xobj_id=0,vertical=False,
        layout_label='plain text',debug_id=identifier,first_line_indent=False,
        pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_characters=
            PdfSameStyleCharacters(box=deepcopy(box),pdf_style=deepcopy(style),pdf_character=chars))])


def fixture():
    left=body('This score depends on the network density (the ratio of the number of links to the square of the number of',315,90,'left')
    right=body('nodes), e.g.: the algorithm improves results for the selected network modules under these conditions.',50,400,'right')
    a=body('This ordinary paragraph provides the other source column and complete contextual text.',50,300,'a')
    b=body('This ordinary paragraph provides the other source column and complete contextual text.',315,400,'b')
    font=PdfFont(font_id='F1',name='ABCDEF+Body',xref_id=33,encoding_length=1,ascent=.8,descent=-.2)
    pages=[Page(page_number=n,mediabox=Mediabox(Box(0,0,612,800)),pdf_font=[deepcopy(font)],pdf_paragraph=ps)
           for n,ps in ((0,[a,left]),(1,[right,b]))]
    return Document(page=pages),left,right


def chars(p):
    return [c for comp in p.pdf_paragraph_composition for c in guard._composition_characters(comp)]


def engine(**values):
    return SimpleNamespace(translation_config=SimpleNamespace(**(dict(skip_translation=False,min_text_length=5)|values)))


class PageDefinitionNativeTests(unittest.TestCase):
    def test_native_head_moves_without_glyph_or_formula_or_box_changes(self):
        doc,left,right=fixture();old_left=chars(left);old_right=chars(right)
        all_chars=[c for page in doc.page for p in page.pdf_paragraph for c in chars(p)]
        states={id(c):deepcopy(c) for c in all_chars}
        boxes=deepcopy([left.box,right.box,left.pdf_style,right.pdf_style])
        self.assertEqual(guard.rejoin_source_page_definition_heads(engine(),doc),1)
        self.assertEqual(list(map(id,chars(left))),list(map(id,old_left+old_right[:7])))
        self.assertEqual(list(map(id,chars(right))),list(map(id,old_right[7:])))
        current=[c for page in doc.page for p in page.pdf_paragraph for c in chars(p)]
        self.assertEqual(Counter(map(id,current)),Counter(map(id,all_chars)))
        self.assertTrue(all(c==states[id(c)] for c in current))
        self.assertEqual([left.box,right.box,left.pdf_style,right.pdf_style],boxes)
        self.assertIs(chars(right)[0],old_right[7]) # Original native space stays.
        self.assertTrue(left.unicode.endswith('number of nodes),'))
        self.assertTrue(right.unicode.startswith('e.g.:'))
        self.assertTrue(guard.source_sentence_was_rejoined(left))
        self.assertTrue(guard.source_sentence_was_rejoined(right))
        self.assertEqual(guard.rejoin_source_page_definition_heads(engine(),doc),0)

    def test_real_batch_entry_rejoins_before_original_scheduler(self):
        import babeldoc.const
        import translation_guard
        doc,left,right=fixture()
        old_left,old_right=chars(left),chars(right)
        self.assertTrue(left.unicode.endswith('number of'))
        self.assertTrue(right.unicode.startswith('nodes),'))
        for page in doc.page:
            for paragraph in page.pdf_paragraph:
                self.assertIsNone(translation_guard._short_prose_connector(paragraph))
        batch=engine(lang_out='zh')
        no_model=Mock(side_effect=AssertionError('This source-only test cannot call a model'))
        batch.il_translator=SimpleNamespace(
            pre_translate_paragraph=Mock(side_effect=AssertionError('No short connector expected')),
            post_translate_paragraph=Mock(side_effect=AssertionError('No short connector expected')),
            translate_engine=SimpleNamespace(llm_translate=no_model))
        result=object()

        def original_scheduler(actual_batch,actual_docs):
            self.assertIs(actual_batch,batch)
            self.assertIs(actual_docs,doc)
            self.assertTrue(left.unicode.endswith('number of nodes),'))
            self.assertTrue(right.unicode.startswith('e.g.:'))
            self.assertEqual(list(map(id,chars(left))),list(map(id,old_left+old_right[:7])))
            self.assertEqual(list(map(id,chars(right))),list(map(id,old_right[7:])))
            self.assertTrue(guard.source_sentence_was_rejoined(left))
            self.assertTrue(guard.source_sentence_was_rejoined(right))
            return result

        upstream=Mock(side_effect=original_scheduler)
        # The real batch entry lazily imports the paragraph tracker. Keep any
        # import-time BabelDOC cache isolated from the user's shared cache.
        with tempfile.TemporaryDirectory(prefix='pagebridge-definition-batch-test-') as cache, \
             patch.object(babeldoc.const,'CACHE_FOLDER',Path(cache)), \
             patch.dict(translation_guard._originals,batch_translate=upstream):
            self.assertIs(translation_guard._guard_batch_translate(batch,doc),result)
        upstream.assert_called_once_with(batch,doc)
        no_model.assert_not_called()
        batch.il_translator.pre_translate_paragraph.assert_not_called()
        batch.il_translator.post_translate_paragraph.assert_not_called()

    def test_existing_formula_object_is_untouched(self):
        doc,left,right=fixture()
        c=deepcopy(chars(left)[0]);c.char_unicode='X';c.pdf_character_id=88
        formula=PdfFormula(box=deepcopy(c.box),pdf_character=[c])
        left.pdf_paragraph_composition.insert(0,PdfParagraphComposition(pdf_formula=formula))
        left.unicode='X '+left.unicode
        original=deepcopy(formula)
        self.assertEqual(guard.rejoin_source_page_definition_heads(engine(),doc),1)
        self.assertIs(left.pdf_paragraph_composition[0].pdf_formula,formula)
        self.assertEqual(formula,original)

    def test_same_font_id_different_resource_or_metrics_is_rejected(self):
        for field,value in (('xref_id',34),('name','ABCDEF+Other'),('encoding_length',2),('ascent',.7)):
            with self.subTest(field=field):
                doc,left,right=fixture();setattr(doc.page[1].pdf_font[0],field,value)
                before=pickle.dumps(doc)
                self.assertEqual(guard.rejoin_source_page_definition_heads(engine(),doc),0)
                self.assertEqual(pickle.dumps(doc),before)

    def test_geometry_grammar_formula_and_glyph_ambiguities_are_rejected(self):
        for case in ('page_gap','indent','wrong_column','not_bottom','different_source_text',
                     'unclosed_outer','missing_example','formula_head','native_glyph_missing',
                     'formula_index','duplicate_font','xobject'):
            with self.subTest(case=case):
                doc,left,right=fixture()
                if case=='page_gap':doc.page[1].page_number=2
                if case=='indent':right.first_line_indent=True
                if case=='wrong_column':right.box.x+=300;right.box.x2+=300
                if case=='not_bottom':left.box.y+=150;left.box.y2+=150
                if case=='different_source_text':right.unicode=right.unicode.replace('nodes','edges',1)
                if case=='unclosed_outer':left.unicode='('+left.unicode
                if case=='missing_example':right.unicode=right.unicode.replace('e.g.:','therefore:')
                if case=='formula_head':
                    run=right.pdf_paragraph_composition[0].pdf_same_style_characters
                    right.pdf_paragraph_composition[0]=PdfParagraphComposition(pdf_formula=PdfFormula(box=run.box,pdf_character=run.pdf_character))
                if case=='native_glyph_missing':chars(right)[0].pdf_character_id=None
                if case=='formula_index':chars(left)[-1].formula_layout_id='formula'
                if case=='duplicate_font':doc.page[1].pdf_font.append(deepcopy(doc.page[1].pdf_font[0]))
                if case=='xobject':right.xobj_id=4
                before=pickle.dumps(doc)
                self.assertEqual(guard.rejoin_source_page_definition_heads(engine(),doc),0)
                self.assertEqual(pickle.dumps(doc),before)

    def test_config_or_absent_next_page_cannot_rehome(self):
        for skip,minimum,remove in ((True,5,False),(False,1000,False),(False,5,True)):
            doc,left,right=fixture()
            if remove:doc.page.pop()
            before=pickle.dumps(doc)
            self.assertEqual(guard.rejoin_source_page_definition_heads(engine(skip_translation=skip,min_text_length=minimum),doc),0)
            self.assertEqual(pickle.dumps(doc),before)


if __name__=='__main__':unittest.main()
