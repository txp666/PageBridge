"""Portable native source-flow fixtures; no PDF, pickle capture or model."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock,patch
import pickle,sys,tempfile,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import layout_guard as g
from babeldoc.format.pdf.document_il import Box,Document,GraphicState,Mediabox,Page,PdfCharacter,PdfFont,PdfFormula,PdfParagraph,PdfParagraphComposition,PdfSameStyleCharacters,PdfStyle,VisualBbox


def body(text,x,y,identifier,font='F1',width=240,size=8):
    remaining=text;rows=[]
    while len(remaining)>58:
        cut=remaining.rfind(' ',0,59)+1
        if cut<=0:cut=58
        rows.append(remaining[:cut]);remaining=remaining[cut:]
    rows.append(remaining)
    style=PdfStyle(font_id=font,font_size=size,graphic_state=GraphicState())
    chars=[]
    for n,row in enumerate(rows):
        step=width/len(row)
        for i,t in enumerate(row):
            b=Box(x+i*step,y-12*n,x+(i+1)*step,y-12*n+size)
            chars.append(PdfCharacter(char_unicode=t,pdf_character_id=ord(t),pdf_style=deepcopy(style),box=b,visual_bbox=VisualBbox(deepcopy(b)),vertical=False,xobj_id=0,advance=step))
    box=Box(x,min(c.box.y for c in chars),x+width,y+size)
    return PdfParagraph(unicode=text,box=box,pdf_style=style,xobj_id=0,vertical=False,layout_label='plain text',debug_id=identifier,first_line_indent=False,pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(box=deepcopy(box),pdf_style=deepcopy(style),pdf_character=chars))])


def engine(**kw):return NS(translation_config=NS(**(dict(skip_translation=False,min_text_length=5)|kw)))
def chars(p):return[c for comp in p.pdf_paragraph_composition for c in g._composition_characters(comp)]
def allchars(doc):return[c for page in doc.page for p in page.pdf_paragraph for c in chars(p)]
PREFIX='The previous sentences describe the observed data and the method in full detail. All measurements are available for the analysis. The following sentence completes the explanation of the method and its result '
AFTER=' Further analysis provides the independent explanation of the observations recorded in the experiment.'
CASES=[('time-','course or steady states.'),('to','form the overall network.'),('a given','LC, considering the 1975–94 period.'),('the learned','matrix.')]

def fixture(kind=0,pages=False):
    ending,head=CASES[kind]
    left=body(PREFIX+ending,315 if pages else 50,120,'left')
    right=body(head+AFTER,50 if pages else 315,720,'right')
    top=body('The introductory source paragraph explains the context for the complete method and its observations.',50,720,'top')
    bottom=body('This closing source paragraph provides further details and the results of the method.',315,120,'bottom')
    font=PdfFont(font_id='F1',name='ABCDEF+Body',xref_id=33,encoding_length=1,bold=0,italic=0,monospace=0,serif=1,ascent=700,descent=-200)
    if pages:
        a=Page(page_number=0,mediabox=Mediabox(Box(0,0,612,800)),pdf_font=[deepcopy(font)],pdf_paragraph=[top,left])
        b=Page(page_number=1,mediabox=Mediabox(Box(0,0,612,800)),pdf_font=[deepcopy(font)],pdf_paragraph=[right,body(top.unicode,315,720,'other')])
        return Document(page=[a,b]),left,right,head
    return Document(page=[Page(page_number=0,pdf_font=[font],mediabox=Mediabox(Box(0,0,612,800)),pdf_paragraph=[top,left,right,bottom])]),left,right,head


def run(doc,pages=False,**kw):return g.rejoin_source_page_short_heads(engine(**kw),doc)if pages else g.rejoin_source_column_short_heads(engine(**kw),doc.page[0])

def native_proof(doc):
    cs=allchars(doc);return list(map(id,cs)),{id(c):pickle.dumps(c)for c in cs}


class SourceShortHeadTests(unittest.TestCase):
    def setUp(self):g._rejoined_source_paragraphs.clear()
    def test_four_source_grammars_keep_native_identity_properties_and_target_boxes(self):
        for kind in range(4):
            with self.subTest(kind=kind):
                doc,left,right,head=fixture(kind,pages=kind==1);old=native_proof(doc);boxes=deepcopy((left.box,right.box,left.pdf_style,right.pdf_style));lc=chars(left);rc=chars(right);source=g._definition_compact(left.unicode+right.unicode)
                self.assertEqual(run(doc,kind==1),1)
                self.assertTrue(left.unicode.endswith(head));self.assertTrue(right.unicode.startswith('Further analysis'))
                self.assertEqual(g._definition_compact(left.unicode+right.unicode),source)
                self.assertEqual((left.box,right.box,left.pdf_style,right.pdf_style),boxes)
                self.assertEqual(list(map(id,chars(left))),list(map(id,lc+rc[:len(head)])))
                self.assertEqual(list(map(id,chars(right))),list(map(id,rc[len(head):])))
                cs=allchars(doc);self.assertEqual(Counter(map(id,cs)),Counter(old[0]));self.assertTrue(all(pickle.dumps(c)==old[1][id(c)]for c in cs))
                self.assertEqual(list(map(id,cs)),old[0]);self.assertTrue(g.source_sentence_was_rejoined(left));self.assertTrue(g.source_sentence_was_rejoined(right));self.assertEqual(run(doc,kind==1),0)
    def test_existing_formula_elsewhere_and_source_diacritic_normalization_are_unchanged(self):
        doc,left,right,head=fixture(0);c=deepcopy(chars(left)[0]);c.char_unicode='o¨';c.pdf_character_id=77;formula=PdfFormula(box=deepcopy(c.box),pdf_character=[c]);left.pdf_paragraph_composition.insert(0,PdfParagraphComposition(pdf_formula=formula));left.unicode='o\u0308 '+left.unicode;state=pickle.dumps(formula)
        self.assertEqual(run(doc),1);self.assertIs(left.pdf_paragraph_composition[0].pdf_formula,formula);self.assertEqual(pickle.dumps(formula),state)
    def test_different_style_metadata_above_columns_does_not_block(self):
        doc,left,right,head=fixture();doc.page[0].pdf_paragraph.insert(0,body('Authors and affiliations occupy the full width above the separate body columns.',50,760,'authors',font='F9',width=505,size=6))
        self.assertEqual(run(doc),1)
    def test_full_width_body_or_metadata_inside_columns_blocks(self):
        for font,y in [('F1',760),('F9',650)]:
            doc,left,right,head=fixture();doc.page[0].pdf_paragraph.insert(0,body('An unrelated full width body paragraph obstructs the source column reading order.',50,y,'obstacle',font=font,width=505));before=pickle.dumps(doc);self.assertEqual(run(doc),0);self.assertEqual(pickle.dumps(doc),before)
    def test_bad_geometry_grammar_formula_or_source_identity_rejects(self):
        for case in ['intervening','wrongfont','size','indent','smallbox','glyph','formula','formulaattr','headline','headgap','wrongsource','bracket','url','laterleft','earlierright','nosource','tailformula','innertailformula','xobj','nocontinuation','longhead']:
            with self.subTest(case=case):
                doc,left,right,head=fixture();p=doc.page[0]
                if case=='intervening':p.pdf_paragraph.insert(2,body('An intervening caption cannot be skipped when joining the source body.',315,730,'caption'))
                if case=='wrongfont':right.pdf_style.font_id='F2'
                if case=='size':right.pdf_style.font_size=9
                if case=='indent':right.first_line_indent=True
                if case=='smallbox':left.box.y=left.box.y2-16
                if case=='glyph':chars(right)[0].pdf_character_id=None
                if case=='formula':
                    r=right.pdf_paragraph_composition[0].pdf_same_style_characters;right.pdf_paragraph_composition[0]=PdfParagraphComposition(pdf_formula=PdfFormula(box=r.box,pdf_character=r.pdf_character))
                if case=='formulaattr':chars(right)[0].formula_layout_id='math'
                if case=='headfound':right.unicode=right.unicode.replace('course','range',1)
                if case=='headline':chars(right)[5].box.y-=12;chars(right)[5].box.y2-=12
                if case=='headgap':chars(right)[5].box.x+=30;chars(right)[5].box.x2+=30
                if case=='wrongsource':right.unicode=right.unicode.replace('course','range',1)
                if case=='bracket':left.unicode='('+left.unicode
                if case=='url':right.unicode='https://example.org.'+AFTER
                if case=='laterleft':p.pdf_paragraph.append(body('A later left column paragraph prevents proving the source flow.',50,30,'later'))
                if case=='earlierright':p.pdf_paragraph.append(body('An earlier right column paragraph prevents proving the source flow.',315,750,'earlier'))
                if case=='nosource':chars(right)[0].char_unicode='C'
                if case=='tailformula':chars(left)[-1].formula_layout_id='math'
                if case=='innertailformula':chars(left)[-3].formula_layout_id='math'
                if case=='xobj':right.xobj_id=5
                if case=='nocontinuation':right.unicode=head
                if case=='longhead':right.unicode='course or steady states and additional future observations.'+AFTER
                before=pickle.dumps(doc);self.assertEqual(run(doc),0);self.assertEqual(pickle.dumps(doc),before)
    def test_page_gap_resource_or_state_differences_reject(self):
        for case in ['gap','xref','name','encoding','font_duplicate','color','gs','notbottom','notfirst','style']:
            with self.subTest(case=case):
                doc,left,right,head=fixture(1,True)
                if case=='gap':doc.page[1].page_number=2
                if case=='xref':doc.page[1].pdf_font[0].xref_id=34
                if case=='name':doc.page[1].pdf_font[0].name='Other'
                if case=='encoding':doc.page[1].pdf_font[0].encoding_length=2
                if case=='font_duplicate':doc.page[1].pdf_font.append(deepcopy(doc.page[1].pdf_font[0]))
                if case in ['color','gs','style']:right.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction={'color':'/GS1 gs 1 0 0 rg','gs':'/GS2 gs 0 0 0 rg','style':'/GS1 gs 2 w 0 0 0 rg'}[case])
                if case=='notbottom':left.box.y+=200;left.box.y2+=200
                if case=='notfirst':doc.page[1].pdf_paragraph.insert(0,body('A separate first source paragraph must not be jumped over.',50,750,'before'))
                before=pickle.dumps(doc);self.assertEqual(run(doc,True),0);self.assertEqual(pickle.dumps(doc),before)
    def test_same_font_resource_with_page_sample_metrics_and_equivalent_final_fill(self):
        doc,left,right,head=fixture(1,True)
        for p,state in [(left,'/GS1 gs /Cs8 cs 0.500000 scn 0 0 0 0 k 0 0 0 rg'),(right,'/GS1 gs 0 0 0 rg')]:
            p.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction=state)
            for comp in p.pdf_paragraph_composition:
                comp.pdf_same_style_characters.pdf_style=deepcopy(p.pdf_style)
                for c in g._composition_characters(comp):c.pdf_style=deepcopy(p.pdf_style)
        doc.page[1].pdf_font[0].descent=0
        self.assertEqual(run(doc,True),1)
    def test_real_batch_scheduler_observes_completed_source_before_any_model(self):
        import babeldoc.const,translation_guard
        doc,left,right,head=fixture(1,True);batch=engine(lang_out='zh');batch.il_translator=NS(pre_translate_paragraph=Mock(side_effect=AssertionError('No connector/model')),post_translate_paragraph=Mock(side_effect=AssertionError('No connector/model')))
        def scheduler(actual,docs):
            self.assertIs(docs,doc);self.assertTrue(left.unicode.endswith(head));self.assertTrue(right.unicode.startswith('Further'));return 'complete'
        with tempfile.TemporaryDirectory()as cache,patch.object(babeldoc.const,'CACHE_FOLDER',Path(cache)),patch.dict(translation_guard._originals,batch_translate=scheduler):self.assertEqual(translation_guard._guard_batch_translate(batch,doc),'complete')
        batch.il_translator.pre_translate_paragraph.assert_not_called()
    def test_styles_hook_calls_column_repair_after_original(self):
        calls=[];cfg=engine();page=Page()
        names=['split_source_physical_lines','bind_source_subscript_indices','repair_subscript_offsets','repair_script_following_formula_offsets','register_script_bindings','rejoin_source_column_sentence_tails','rejoin_source_column_orphan_heads','merge_source_bullet_prepositions','merge_source_numbered_rule_titles','rejoin_source_parenthetical_comparisons']
        from contextlib import ExitStack
        with ExitStack()as stack:
            for name in names:stack.enter_context(patch.object(g,name,return_value=0))
            stack.enter_context(patch.object(g,'_original_styles_process_page',side_effect=lambda*a:calls.append('original')));stack.enter_context(patch.object(g,'rejoin_source_column_short_heads',side_effect=lambda*a:calls.append('short-head')));g._guard_styles_process_page(cfg,page)
        self.assertEqual(calls,['original','short-head'])
    def test_skip_or_selected_page_gap_keeps_source_intact(self):
        for pages in [False,True]:
            for kw in [dict(skip_translation=True),dict(min_text_length=2000)]:
                doc,left,right,head=fixture(1 if pages else 0,pages);before=pickle.dumps(doc);self.assertEqual(run(doc,pages,**kw),0);self.assertEqual(pickle.dumps(doc),before)

if __name__=='__main__':unittest.main()
