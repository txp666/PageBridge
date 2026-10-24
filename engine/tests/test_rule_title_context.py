"""Native numbered-title context; portable fixtures, no PDFs or network."""
from copy import deepcopy
import json
from pathlib import Path
import pickle
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import translation_guard as guard
from test_page_definition_native import body
from test_document_weather_context import native_formula
from test_translation_guard import translator, ILTranslator, ParagraphTranslateTracker
from babeldoc.format.pdf.document_il import Document, Page, PdfFont, Box, Mediabox

TITLE='Rule 7: Keep the Data Behind Every Figure'
BODY=('A figure can be regenerated from the exact observations that were used in the analysis. '
      'The original data provides the basis of the figure. ')


def fixture(title=TITLE, prose=BODY):
    a=body(title,50,700,'title');a.layout_label='title'
    b=body(prose,50,686,'body')
    font=PdfFont(font_id='F1',name='ABCDEF+Body',xref_id=33,encoding_length=1,ascent=.8,descent=-.2)
    doc=Document(page=[Page(page_number=0,mediabox=Mediabox(Box(0,0,612,800)),pdf_font=[font],pdf_paragraph=[a,b])])
    return doc,a,b


def ti_for(p):
    return ILTranslator.TranslateInput(p.unicode,[],p.pdf_style)


def chars(p):
    import layout_guard
    return [c for comp in p.pdf_paragraph_composition for c in layout_guard._composition_characters(comp)]


class RuleTitleContextTests(unittest.TestCase):
    def bound(self,prose=BODY):
        doc,p,b=fixture(prose=prose);before=pickle.dumps(doc)
        self.assertEqual(guard._capture_rule_title_context(doc),1)
        self.assertEqual(pickle.dumps(doc),before)
        ti=ti_for(p);guard._prepare_source_continuity(p,ti)
        self.assertIsNotNone(guard._rule_title_context(ti))
        return doc,p,b,ti

    def test_only_source_edges_are_read_only_and_bounded(self):
        text=BODY*6+'The final sentence explains that all figures use the original observations.'
        doc,p,b,ti=self.bound(text);before=pickle.dumps(doc)
        context=guard._rule_title_context(ti)
        self.assertEqual(context.head,guard._source_prose_context_fragment(b,tail=False))
        self.assertEqual(context.tail,guard._source_prose_context_fragment(b,tail=True))
        self.assertLessEqual(len(context.head.split()),48);self.assertLessEqual(len(context.tail.split()),48)
        self.assertLessEqual(len(context.head),360);self.assertLessEqual(len(context.tail),360)
        self.assertNotIn(text,guard._rule_title_context_hint(ti))
        self.assertEqual(ti.unicode,TITLE);self.assertEqual(ti.placeholders,[])
        self.assertEqual(pickle.dumps(doc),before)

    def test_only_explicit_numbered_prose_title_and_immediate_body(self):
        for bad in ('Algorithm 7: Keep the Data Behind Every Figure','Keep the Data Behind Every Figure',
                    'Rule7 Keep the Data Behind Every Figure','"Rule7: Keep the Data Behind Every Figure"',
                    'Rule 0: Keep the Data Behind Every Figure'):
            doc,p,b=fixture(bad);self.assertEqual(guard._capture_rule_title_context(doc),0,bad)
        for label in ('plain text','caption','reference','code'):
            doc,p,b=fixture();p.layout_label=label;self.assertEqual(guard._capture_rule_title_context(doc),0,label)
        for label in ('caption','reference','title','code'):
            doc,p,b=fixture();b.layout_label=label;self.assertEqual(guard._capture_rule_title_context(doc),0,label)
        doc,p,b=fixture();doc.page[0].pdf_paragraph.insert(1,body('An intervening caption.',50,694,'caption'))
        self.assertEqual(guard._capture_rule_title_context(doc),0)

    def test_geometry_fonts_and_visible_glyphs_fail_closed(self):
        for case in ('different_page','column','large_gap','body_above','object','glyph','font','duplicate_font','source_text','duplicate_glyph'):
            doc,p,b=fixture()
            if case=='different_page':
                doc.page[0].pdf_paragraph.pop();doc.page.append(deepcopy(doc.page[0]));doc.page[1].page_number=1;doc.page[1].pdf_paragraph=[b]
            elif case=='column':b.box.x+=260;b.box.x2+=260
            elif case=='large_gap':b.box.y2-=100
            elif case=='body_above':b.box.y2=710
            elif case=='object':b.xobj_id=9
            elif case=='glyph':chars(b)[0].pdf_character_id=None
            elif case=='font':doc.page[0].pdf_font[0].xref_id=None
            elif case=='duplicate_font':doc.page[0].pdf_font.append(deepcopy(doc.page[0].pdf_font[0]))
            elif case=='source_text':b.unicode=b.unicode.replace('figure','table')
            elif case=='duplicate_glyph':b.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character.append(chars(b)[0])
            self.assertEqual(guard._capture_rule_title_context(doc),0,case)

    def test_parser_spaces_without_glyph_ids_are_allowed_but_visible_glyphs_are_not(self):
        doc,p,b=fixture()
        for q in (p,b):
            for c in chars(q):
                if c.char_unicode.isspace():c.pdf_character_id=None;c.xobj_id=None
        self.assertEqual(guard._capture_rule_title_context(doc),1)
        chars(p)[0].pdf_character_id=None
        self.assertEqual(guard._capture_rule_title_context(doc),0)

    def test_formula_source_edges_do_not_flatten_or_import_formula(self):
        doc,p,b=fixture(prose=BODY+'The quantity X belongs to an independent formula. '+BODY)
        native_formula(b,'X');formula=b.pdf_paragraph_composition[1].pdf_formula
        before=pickle.dumps(doc)
        self.assertEqual(guard._capture_rule_title_context(doc),1)
        ti=ti_for(p);guard._prepare_rule_title_context(p,ti);ctx=guard._rule_title_context(ti)
        self.assertNotIn('X',ctx.head+ctx.tail)
        self.assertIs(b.pdf_paragraph_composition[1].pdf_formula,formula)
        self.assertEqual(pickle.dumps(doc),before)
        native_formula(p,'D')
        self.assertEqual(guard._capture_rule_title_context(doc),0)

    def test_object_document_and_title_identity_cannot_be_reused(self):
        doc,p,b,ti=self.bound()
        wrong=ti_for(p);guard._prepare_rule_title_context(deepcopy(p),wrong)
        self.assertIsNone(guard._rule_title_context(wrong))
        copied=ti_for(p);guard._copy_source_continuity(ti,copied)
        self.assertIsNotNone(guard._rule_title_context(copied))
        copied.unicode=TITLE+' extra';self.assertIsNone(guard._rule_title_context(copied))
        guard._capture_rule_title_context(Document(page=[]))
        self.assertIsNone(guard._rule_title_context(ti))

    def test_engine_syntax_in_source_edges_is_not_exposed(self):
        for literal in ('{v99}',"<style id='2'>data</style>",'{PBKEEP_0001}'):
            doc,p,b=fixture(prose=BODY+literal+' is a literal example. '+BODY)
            self.assertEqual(guard._capture_rule_title_context(doc),0)

    def test_single_and_batch_use_same_source_bound_context_without_source_change(self):
        doc,p,b,ti=self.bound();hint=guard._rule_title_context_hint(ti)
        eng=NS(translation_config=NS(lang_out='zh'))
        with patch.dict(guard._originals,single_prompt_context=lambda self,text,**kw:'PROMPT:'+text):
            single=guard._guard_single_prompt_context(eng,ti.unicode,translate_input=ti)
            self.assertIn(hint,single)
            eng.translation_config.lang_out='de'
            self.assertNotIn(hint,guard._guard_single_prompt_context(eng,ti.unicode,translate_input=ti))
        eng.translation_config.lang_out='zh'
        token=guard._review_batch.set([ti])
        try:
            with patch.dict(guard._originals,batch_prompt_context=lambda self,text:text):
                prompt=guard._guard_batch_prompt_context(eng,json.dumps([{'id':'a','input':ti.unicode}]))
                self.assertIn(hint,prompt)
                changed=guard._guard_batch_prompt_context(eng,json.dumps([{'id':'a','input':'Different title'}]))
                self.assertNotIn(hint,changed)
        finally:guard._review_batch.reset(token)
        self.assertEqual(ti.unicode,TITLE)

    def test_retry_and_whole_share_hint_original_numeric_gate_remains_strict(self):
        doc,p,b,ti=self.bound(prose=BODY+'The data includes 999 original observations.')
        hint=guard._rule_title_context_hint(ti)
        for whole in (False,True):
            il=translator(['规则7：保留每幅图所依据的数据'])
            if whole:out=guard._whole_paragraph_translation(il,ti,ParagraphTranslateTracker())
            else:out=guard._retry_translation(il,ti,ParagraphTranslateTracker(),0)
            self.assertIn(hint,il.calls[0][0])
            self.assertEqual(ti.unicode,TITLE)
        il=translator()
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard._check_body_translation(il,p,ti,'规则7：保留每幅图所依据的999个数据')
        with self.assertRaises(guard.PlaceholderIntegrityError):
            guard.normalize_output(ti,'规则7：保留每幅图所依据的数据 {v99}')

    def test_original_batch_entry_captures_before_upstream_without_mutation(self):
        doc,p,b=fixture();before=pickle.dumps(doc)
        config=NS(lang_out='zh',skip_translation=False,min_text_length=5)
        eng=NS(translation_config=config,il_translator=NS(translation_config=config))
        def scheduler(actual,docs):
            self.assertIs(actual,eng);self.assertIs(docs,doc)
            ti=ti_for(p);guard._prepare_source_continuity(p,ti)
            self.assertIsNotNone(guard._rule_title_context(ti));return 'done'
        with patch.dict(guard._originals,batch_translate=scheduler):
            self.assertEqual(guard._guard_batch_translate(eng,doc),'done')
        self.assertEqual(pickle.dumps(doc),before)
