"""Portable native source boundary fixtures; no paper, files, or model needed."""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import math,pickle,sys,unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import layout_guard as g
from babeldoc.format.pdf.document_il import Box,GraphicState,Page,PdfCharacter,PdfParagraph,PdfParagraphComposition,PdfSameStyleCharacters,PdfStyle,VisualBbox,PdfFormula

def para(text,x=40,y=400,width=240,size=8,font='F1',label='plain text',identifier='body'):
    rows=[];remaining=text
    while len(remaining)>58:
        cut=remaining.rfind(' ',0,59)+1
        if cut<=0:cut=58
        rows.append(remaining[:cut]);remaining=remaining[cut:]
    rows.append(remaining)
    style=PdfStyle(font_id=font,font_size=size,graphic_state=GraphicState())
    cs=[]
    for n,row in enumerate(rows):
        step=width/len(row)
        for i,ch in enumerate(row):
            box=Box(x+i*step,y-12*n,x+(i+1)*step,y-12*n+size)
            cs.append(PdfCharacter(char_unicode=ch,pdf_character_id=ord(ch),pdf_style=deepcopy(style),box=box,visual_bbox=VisualBbox(deepcopy(box)),xobj_id=0,vertical=False,advance=step))
    box=Box(x,min(c.box.y for c in cs),x+width,y+size)
    return PdfParagraph(unicode=text,pdf_style=style,box=box,debug_id=identifier,layout_label=label,xobj_id=0,vertical=False,first_line_indent=False,pdf_paragraph_composition=[PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(box=deepcopy(box),pdf_style=deepcopy(style),pdf_character=cs))])

def styler(**kw):return NS(translation_config=NS(**({'skip_translation':False,'min_text_length':5}|kw)))
def chars(p):return [c for comp in p.pdf_paragraph_composition for c in g._composition_characters(comp)]
def all_chars(page):return [c for p in page.pdf_paragraph for c in chars(p)]
def titles():
    return Page(pdf_paragraph=[para('Rule 12: Retain the Exact',size=10,font='F2',label='title',identifier='title1'),para('Versions of External',y=388,size=10,font='F2',label='title',identifier='title2'),para('Programs Used',y=376,size=10,font='F2',label='title',identifier='title3'),para('This body contains the complete explanation in ordinary text.',y=350,identifier='body')])
def comparison():
    text='These earlier notes explain the method and the work needed to recover them from memory (or at least much more difficult'
    left=para(text,y=110,identifier='left')
    right=para('than to do it while underway). We believe that the effort preserves all relevant details for later analysis.',x=300,y=400,identifier='right')
    top=para('This earlier context explains the methods used for this research and the document records the entire analysis.',y=400,identifier='top')
    bottom=para(text.replace('(or at least much more difficult','under all the ordinary circumstances.'),x=300,y=110,identifier='bottom')
    header=para('Authors and affiliations cover the full page width in another style.',x=40,y=440,width=500,size=6,font='F3',identifier='header')
    return Page(pdf_paragraph=[header,top,left,right,bottom]),left,right

def proof_before(page):
    cs=all_chars(page);return [id(c)for c in cs],{id(c):pickle.dumps(c)for c in cs}
def prove(test,page,old):
    cs=all_chars(page);test.assertEqual([id(c)for c in cs],old[0]);test.assertEqual(Counter(map(id,cs)),Counter(old[0]));test.assertTrue(all(pickle.dumps(c)==old[1][id(c)]for c in cs))

class SourceHeadingComparisonTests(unittest.TestCase):
    def setUp(self):g._rejoined_source_paragraphs.clear()
    def test_complete_title_group_preserves_all_native_glyphs_and_uses_only_union(self):
        page=titles();original=proof_before(page);style=deepcopy(page.pdf_paragraph[0].pdf_style);boxes=[deepcopy(p.box)for p in page.pdf_paragraph];remaining=page.pdf_paragraph[3]
        self.assertEqual(g.merge_source_numbered_rule_titles(styler(),page),1)
        p=page.pdf_paragraph[0];self.assertEqual(p.unicode,'Rule 12: Retain the Exact Versions of External Programs Used');self.assertEqual(len(page.pdf_paragraph),2);self.assertIs(page.pdf_paragraph[1],remaining)
        self.assertEqual(p.box,Box(min(b.x for b in boxes[:3]),min(b.y for b in boxes[:3]),max(b.x2 for b in boxes[:3]),max(b.y2 for b in boxes[:3])))
        self.assertEqual(p.pdf_style,style);self.assertTrue(g.source_sentence_was_rejoined(p));prove(self,page,original)
        self.assertEqual(g.merge_source_numbered_rule_titles(styler(),page),0)
    def test_title_stops_at_body_or_new_number(self):
        for label,text in [('plain text','Versions of External'),('title','Rule 13: Another Heading')]:
            page=titles();page.pdf_paragraph[1].layout_label=label
            if text.startswith('Rule'):page.pdf_paragraph[1]=para(text,y=388,size=10,font='F2',label=label,identifier='new')
            first=page.pdf_paragraph[0];before=pickle.dumps(first);g.merge_source_numbered_rule_titles(styler(),page);self.assertEqual(before,pickle.dumps(first))
    def test_ambiguous_title_geometry_style_or_native_formula_does_not_merge(self):
        cases=['column','wide','gap','font','size','graphics','formula','glyph','overlap','nan']
        for kind in cases:
            with self.subTest(kind=kind):
                page=titles();p=page.pdf_paragraph[1]
                if kind=='column':p.box.x+=260;p.box.x2+=260
                if kind=='wide':p.box.x2+=200
                if kind=='nan':p.box.y=float('nan')
                if kind=='gap':
                    for c in chars(p):c.box.y-=30;c.box.y2-=30
                if kind=='font':p.pdf_style.font_id='F9'
                if kind=='size':p.pdf_style.font_size+=1
                if kind=='graphics':p.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction='1 0 0 rg')
                if kind=='formula':p.pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=PdfFormula(pdf_character=chars(p)))]
                if kind=='glyph':chars(p)[0].pdf_character_id=None
                if kind=='overlap':page.pdf_paragraph.append(para('This interfering source paragraph blocks the proposed heading union.',y=390))
                before=pickle.dumps(page);self.assertEqual(g.merge_source_numbered_rule_titles(styler(),page),0);self.assertEqual(before,pickle.dumps(page))
    def test_comparison_moves_short_head_and_keeps_both_target_boxes(self):
        page,left,right=comparison();old=proof_before(page);boxes=deepcopy((left.box,right.box,left.pdf_style,right.pdf_style));head='than to do it while underway).'
        self.assertEqual(g.rejoin_source_parenthetical_comparisons(styler(),page),1)
        self.assertTrue(left.unicode.endswith('more difficult '+head));self.assertTrue(right.unicode.startswith('We believe'));self.assertEqual((left.box,right.box,left.pdf_style,right.pdf_style),boxes);prove(self,page,old)
        self.assertTrue(g.source_sentence_was_rejoined(left));self.assertTrue(g.source_sentence_was_rejoined(right));self.assertEqual(g.rejoin_source_parenthetical_comparisons(styler(),page),0)
    def test_full_width_same_body_style_remains_an_obstacle(self):
        page,left,right=comparison();page.pdf_paragraph[0]=para('This full width body paragraph prevents proving two independent source columns.',x=40,y=440,width=500,identifier='body-header');before=pickle.dumps(page)
        self.assertEqual(g.rejoin_source_parenthetical_comparisons(styler(),page),0);self.assertEqual(before,pickle.dumps(page))
    def test_comparison_rejects_style_gaps_missing_proof_and_wrong_grammar(self):
        for kind in ['intervening','indent','font','size','graphics','formula','unclosed','other-head','two-lines','changed-native','lower-body','higher-body','xobject']:
            with self.subTest(kind=kind):
                page,left,right=comparison()
                if kind=='intervening':page.pdf_paragraph.insert(3,para('A caption or unrelated paragraph is not part of this source continuation.',x=300,y=430,identifier='caption'))
                if kind=='indent':right.first_line_indent=True
                if kind=='font':right.pdf_style.font_id='F9'
                if kind=='size':right.pdf_style.font_size+=1
                if kind=='graphics':right.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction='1 0 0 rg')
                if kind=='formula':right.pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=PdfFormula(pdf_character=chars(right)))]
                if kind=='unclosed':left.unicode='('+left.unicode
                if kind=='other-head':right.unicode=right.unicode.replace('than to','rather than',1)
                if kind=='two-lines':chars(right)[10].box.y-=12;chars(right)[10].box.y2-=12
                if kind=='changed-native':chars(right)[0].char_unicode='T'
                if kind=='lower-body':page.pdf_paragraph.append(para('An additional lower left body paragraph prevents claiming the left paragraph is last.',y=40,identifier='later'))
                if kind=='higher-body':page.pdf_paragraph.append(para('An additional upper right body paragraph prevents claiming the right paragraph is first.',x=300,y=430,identifier='earlier'))
                if kind=='xobject':right.xobj_id=4
                before=pickle.dumps(page);self.assertEqual(g.rejoin_source_parenthetical_comparisons(styler(),page),0);self.assertEqual(before,pickle.dumps(page))
    def test_translation_skipped_never_rehomes_native_source(self):
        page,left,right=comparison();before=pickle.dumps(page);self.assertEqual(g.rejoin_source_parenthetical_comparisons(styler(skip_translation=True),page),0);self.assertEqual(before,pickle.dumps(page))
        page=titles();before=pickle.dumps(page);self.assertEqual(g.merge_source_numbered_rule_titles(styler(skip_translation=True),page),0);self.assertEqual(before,pickle.dumps(page))
    def test_styles_hook_invokes_both_repairs_after_original_styling(self):
        page=Page();order=[]
        names=['split_source_physical_lines','bind_source_subscript_indices','repair_subscript_offsets','repair_script_following_formula_offsets','register_script_bindings','rejoin_source_column_sentence_tails','rejoin_source_column_orphan_heads','merge_source_bullet_prepositions']
        from contextlib import ExitStack
        with ExitStack()as stack:
            for name in names:stack.enter_context(patch.object(g,name,return_value=0))
            stack.enter_context(patch.object(g,'_original_styles_process_page',side_effect=lambda *a:order.append('original')))
            stack.enter_context(patch.object(g,'merge_source_numbered_rule_titles',side_effect=lambda *a:order.append('title')))
            stack.enter_context(patch.object(g,'rejoin_source_parenthetical_comparisons',side_effect=lambda *a:order.append('comparison')))
            g._guard_styles_process_page(styler(),page)
        self.assertEqual(order,['original','title','comparison'])
if __name__=='__main__':unittest.main()
