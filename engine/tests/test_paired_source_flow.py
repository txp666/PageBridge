"""Portable two-box source/target flow, independent of any paper/model."""
from pathlib import Path
from copy import deepcopy
from collections import Counter
from types import SimpleNamespace as NS
from unittest.mock import patch
import pickle,sys,unittest,tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import layout_guard as g
from babeldoc.format.pdf.document_il import Box,Document,Page,PdfFormula,PdfParagraphComposition,GraphicState,PdfCharacter,PdfParagraph,PdfSameStyleCharacters,PdfStyle,VisualBbox
import babeldoc.const
with tempfile.TemporaryDirectory()as temporary,patch.object(babeldoc.const,"CACHE_FOLDER",Path(temporary)):
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting,TypesettingUnit
import pymupdf

# Capture actual upstream backends at collection time. Older installation tests
# deliberately toggle the global installed flag; this fixture restores its
# own hooks without retaining a second wrapper as the upstream function.
_NATIVE_LAYOUT=g._original_layout_typesetting_units or Typesetting._layout_typesetting_units
_NATIVE_FIND=g._original_find_optimal_scale_and_layout or Typesetting._find_optimal_scale_and_layout

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

A='The accuracy of the experimental results and the agreement of the observations obtained with this new'
B='methods on both simulated and recorded data are encouraging and support further practical investigations.'
TARGET='实验结果的准确性以及这种新方法在模拟数据和记录数据上得到的观测结果的一致性令人鼓舞，并支持进一步开展实际研究。该方法在这些数据上的一致性支持进一步研究其实际应用范围。'

def pair():
    left=body(A,50,80,'left');right=body(B,315,720,'right')
    top=body('This introductory source paragraph establishes the context of the method for the following research.',50,720,'top')
    bottom=body('This following source paragraph belongs to the next section and must keep its independent content.',315,100,'bottom')
    heading=body('Materials and Methods',315,680,'heading',font='F2',size=10);heading.layout_label='title'
    page=Page(page_number=0,pdf_paragraph=[top,left,right,heading,bottom]);return Document(page=[page]),page,left,right,heading

def renderer():
    obj=object.__new__(Typesetting);obj.is_cjk=True;obj.font_mapper=NS(base_font=pymupdf.Font('china-s'));return obj

def units(text,p):
    font=pymupdf.Font('china-s');return[TypesettingUnit(unicode=c,font=font,font_size=p.pdf_style.font_size,style=p.pdf_style,xobj_id=0)for c in text]

class PairedSourceFlowTests(unittest.TestCase):
    def setUp(self):
        g._paired_source_paragraphs.clear();g._rejoined_source_paragraphs.clear()
        for target,name,value in [(g,'_original_layout_typesetting_units',_NATIVE_LAYOUT),
                                  (g,'_original_find_optimal_scale_and_layout',_NATIVE_FIND),
                                  (Typesetting,'_find_optimal_scale_and_layout',g._guard_find_optimal_scale_and_layout)]:
            context=patch.object(target,name,value);context.start();self.addCleanup(context.stop)
    def test_source_pair_preserves_every_original_glyph_property_order_box_and_neighbor(self):
        doc,page,left,right,heading=pair();original=allchars(doc);states={id(c):pickle.dumps(c)for c in original};boxes=deepcopy((left.box,right.box));neighbor=pickle.dumps(heading)
        self.assertEqual(g.join_source_column_sentence_pairs(engine(),page),1)
        self.assertEqual(left.unicode,A+' '+B);self.assertEqual(right.unicode,'');self.assertEqual(right.pdf_paragraph_composition,[])
        new=allchars(doc);self.assertEqual(list(map(id,new)),list(map(id,original)));self.assertEqual(Counter(map(id,new)),Counter(map(id,original)));self.assertTrue(all(pickle.dumps(c)==states[id(c)]for c in new));self.assertEqual((left.box,right.box),boxes);self.assertEqual(pickle.dumps(heading),neighbor)
        self.assertTrue(g.source_sentence_was_rejoined(left));self.assertFalse(g.source_sentence_was_rejoined(right));self.assertEqual(g.join_source_column_sentence_pairs(engine(),page),0)
    def test_complete_output_flows_in_order_in_two_original_boxes_without_heading_overlap(self):
        doc,page,left,right,heading=pair();self.assertEqual(g.join_source_column_sentence_pairs(engine(),page),1);boxes=deepcopy((left.box,right.box));neighbor=pickle.dumps(heading);r=renderer()
        scale,laid=r._find_optimal_scale_and_layout(left,page,units(TARGET,left),apply_layout=True)
        output=chars(left);self.assertEqual(''.join(c.char_unicode for c in output),TARGET);self.assertGreaterEqual(scale,7/8)
        groups=[[c for c in output if b.x<=c.box.x<=c.box.x2<=b.x2 and b.y<=c.box.y<=c.box.y2<=b.y2]for b in boxes]
        self.assertTrue(all(groups));self.assertEqual([id(c)for group in groups for c in group],[id(c)for c in output]);self.assertEqual(sum(map(len,groups)),len(output));self.assertEqual((left.box,right.box),boxes);self.assertEqual(pickle.dumps(heading),neighbor)
    def test_upstream_empty_or_partial_false_contract_retries_smaller_scale(self):
        for partial in [False,True]:
            with self.subTest(partial=partial):
                doc,page,left,right,heading=pair();g.join_source_column_sentence_pairs(engine(),page);r=renderer();calls=[];original=g._original_layout_typesetting_units
                def backend(obj,input_units,box,scale,line_skip,paragraph,use_english):
                    calls.append(scale)
                    if len(calls)==1:
                        if not partial:return [],False
                        # A fitted partial prefix with False supplies no first
                        # overflow unit. It cannot be treated as all output.
                        result,_=original(obj,input_units[:3],box,scale,line_skip,paragraph,use_english)
                        return result,False
                    return original(obj,input_units,box,scale,line_skip,paragraph,use_english)
                with patch.object(g,'_original_layout_typesetting_units',side_effect=backend):
                    scale,laid=r._find_optimal_scale_and_layout(left,page,units(TARGET,left),apply_layout=True)
                self.assertLess(scale,1);self.assertGreaterEqual(len(calls),3)
                self.assertEqual(''.join(c.char_unicode for c in chars(left)),TARGET)

    def test_no_fit_is_explicit_failure_without_box_expansion_or_silent_truncation(self):
        doc,page,left,right,heading=pair();g.join_source_column_sentence_pairs(engine(),page);before=deepcopy((left.box,right.box));r=renderer()
        with self.assertRaisesRegex(ValueError,'does not fit'):r._find_optimal_scale_and_layout(left,page,units(TARGET*8,left),apply_layout=True)
        self.assertEqual((left.box,right.box),before)
    def test_changed_partner_or_wrong_page_is_not_rendered(self):
        for what in ['box','contents','page']:
            doc,page,left,right,heading=pair();g.join_source_column_sentence_pairs(engine(),page)
            if what=='box':right.box.x+=1
            if what=='contents':right.unicode='unexpected'
            if what=='page':page=Page()
            with self.assertRaises(ValueError):renderer()._find_optimal_scale_and_layout(left,page,units(TARGET,left))
    def test_source_formula_rich_style_heading_interruption_or_grammar_ambiguity_rejects(self):
        for what in ['formula','style','heading','new_sentence','wrong_tail','wrong_predicate','three_rows','native_mismatch','short','skip','minimum']:
            with self.subTest(what=what):
                doc,page,left,right,heading=pair();kw={}
                if what=='formula':
                    run=right.pdf_paragraph_composition[0].pdf_same_style_characters;right.pdf_paragraph_composition[0]=PdfParagraphComposition(pdf_formula=PdfFormula(box=run.box,pdf_character=run.pdf_character))
                if what=='style':right.pdf_style.font_id='F3'
                if what=='heading':page.pdf_paragraph.remove(heading);page.pdf_paragraph.insert(2,heading)
                if what=='new_sentence':left.unicode=left.unicode.replace('and','and.',1)
                if what=='wrong_tail':left.unicode=left.unicode.replace('new','method')
                if what=='wrong_predicate':right.unicode=right.unicode.replace('are','with')
                if what=='three_rows':chars(right)[0].box.y-=24;chars(right)[0].box.y2-=24
                if what=='native_mismatch':chars(left)[0].char_unicode='A'
                if what=='short':left.box.y=left.box.y2-8
                if what=='skip':kw['skip_translation']=True
                if what=='minimum':kw['min_text_length']=1000
                before=pickle.dumps(doc);self.assertEqual(g.join_source_column_sentence_pairs(engine(**kw),page),0);self.assertEqual(pickle.dumps(doc),before)
    def test_rejoined_logical_source_may_not_silently_skip_preparation(self):
        import translation_guard as t
        doc,page,left,right,heading=pair();g.join_source_column_sentence_pairs(engine(),page)
        with patch.dict(t._originals,pre=lambda*a,**k:(None,None)):
            with self.assertRaises(t.PlaceholderIntegrityError):t._guard_pre(NS(),left,NS())
    def test_original_renderer_still_used_for_unpaired_paragraph(self):
        doc,page,left,right,heading=pair();sentinel=object()
        with patch.object(g,'_original_find_optimal_scale_and_layout',return_value=sentinel)as original:
            self.assertIs(renderer()._find_optimal_scale_and_layout(left,page,[]),sentinel);original.assert_called_once()
    def test_styles_hook_pairs_after_existing_repairs(self):
        calls=[];names=['split_source_physical_lines','bind_source_subscript_indices','repair_subscript_offsets','repair_script_following_formula_offsets','register_script_bindings','rejoin_source_column_sentence_tails','rejoin_source_column_orphan_heads','merge_source_bullet_prepositions','merge_source_numbered_rule_titles','rejoin_source_parenthetical_comparisons','rejoin_source_column_short_heads']
        from contextlib import ExitStack
        with ExitStack()as stack:
            for name in names:stack.enter_context(patch.object(g,name,return_value=0))
            stack.enter_context(patch.object(g,'_original_styles_process_page',side_effect=lambda*a:calls.append('original')));stack.enter_context(patch.object(g,'join_source_column_sentence_pairs',side_effect=lambda*a:calls.append('pair')));g._guard_styles_process_page(engine(),Page())
        self.assertEqual(calls,['original','pair'])
if __name__=='__main__':unittest.main()
