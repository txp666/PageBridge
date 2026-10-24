import copy
from dataclasses import asdict
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout_guard as guard
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_layout_guard import character, line_paragraph, page, ParagraphFinder, Box, PdfStyle, GraphicState, PdfParagraph, PdfParagraphComposition, PdfSameStyleCharacters
from babeldoc.format.pdf.document_il import PdfFormula
from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas


def body(text, x, y, top):
    chars = [character(c, ord(c), 'F2', x+i*4, top-8) for i,c in enumerate(text)]
    for c in chars:
        c.box.x2 = c.box.x+4; c.visual_bbox.box.x2 = c.box.x2
    style=chars[0].pdf_style
    return PdfParagraph(unicode=text, box=Box(x,y,x+200,top), pdf_style=style,
        layout_label='plain text', xobj_id=-1, vertical=False, first_line_indent=False,
        debug_id=text[:10], pdf_paragraph_composition=[PdfParagraphComposition(
        pdf_same_style_characters=PdfSameStyleCharacters(pdf_character=chars,
        pdf_style=style, box=Box(x,top-8,x+4*len(chars),top)))])


def chars(f):
    return [c for p in f.pdf_paragraph for co in p.pdf_paragraph_composition
            for c in guard._composition_characters(co)]


class OrphanHeadTests(unittest.TestCase):
    def fixture(self, footer=True):
        left=body('These findings now define a shared goal across both',10,100,140)
        right=body('groups.',230,100,520)
        spacer=body(' ',258,100,520)
        continuation=body('In later experiments the full result is independently verified.',262,100,520)
        right.pdf_paragraph_composition.extend(spacer.pdf_paragraph_composition+continuation.pdf_paragraph_composition)
        right.unicode += ' '+continuation.unicode
        f=page([])
        f.pdf_paragraph=[body('Earlier findings explain the full background of this experiment.',10,400,520),left]
        if footer:
            foot=body('Corresponding author information.',10,50,80);foot.layout_label='abandon'
            f.pdf_paragraph.append(foot)
        f.pdf_paragraph.append(right)
        return f,left,right,NS(translation_config=NS(min_text_length=5,skip_translation=False))

    def test_original_glyphs_boxes_and_complete_body_reading_order_are_preserved(self):
        for footer in (False,True):
            f,l,r,s=self.fixture(footer)
            original=chars(f);states={id(c):asdict(c) for c in original}
            bodytext=''.join((l.unicode+r.unicode).split())
            boxes=[(p.box,asdict(p.box),p.pdf_style) for p in f.pdf_paragraph]
            self.assertEqual(guard.rejoin_source_column_orphan_heads(s,f),1)
            self.assertEqual(l.unicode,'These findings now define a shared goal across both groups.')
            self.assertTrue(r.unicode.startswith('In later experiments'))
            self.assertEqual(''.join((l.unicode+r.unicode).split()),bodytext)
            self.assertEqual({id(c):asdict(c) for c in chars(f)},states)
            self.assertEqual(len(chars(f)),len(original))
            for p,(box,state,style) in zip(f.pdf_paragraph,boxes):
                self.assertIs(p.box,box);self.assertEqual(asdict(p.box),state);self.assertIs(p.pdf_style,style)
            self.assertTrue(guard.source_sentence_was_rejoined(l));self.assertTrue(guard.source_sentence_was_rejoined(r))
            self.assertEqual(guard.rejoin_source_column_orphan_heads(s,f),0)

    def test_mismatched_or_ambiguous_source_remains_untouched(self):
        for mode in ('not-both','not-plural','missing-space','heading','raised-footer','wrong-font','color',
                     'superscript','formula','xobject','indented','overlap','no-id','skip','short'):
            f,l,r,s=self.fixture()
            if mode=='not-both':l.unicode=l.unicode[:-4]+'many'
            elif mode=='not-plural':r.unicode=r.unicode.replace('groups.','group.')
            elif mode=='missing-space':r.pdf_paragraph_composition.pop(1)
            elif mode=='heading':f.pdf_paragraph[-2].layout_label='title'
            elif mode=='raised-footer':f.pdf_paragraph[-2].box.y2=110
            elif mode=='wrong-font':r.pdf_style.font_id='Another'
            elif mode=='color':r.pdf_style.graphic_state.passthrough_per_char_instruction='1 0 0 rg'
            elif mode=='superscript':r.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[2].box.y+=4
            elif mode=='formula':r.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[2].formula_layout_id=9
            elif mode=='xobject':r.xobj_id=9
            elif mode=='indented':r.first_line_indent=True
            elif mode=='overlap':f.pdf_paragraph[0].box.x2=400
            elif mode=='no-id':r.debug_id=None
            elif mode=='skip':s.translation_config.skip_translation=True
            elif mode=='short':s.translation_config.min_text_length=1000
            before=[id(c) for c in chars(f)];texts=[p.unicode for p in f.pdf_paragraph]
            with self.subTest(mode=mode):
                self.assertEqual(guard.rejoin_source_column_orphan_heads(s,f),0)
                self.assertEqual([id(c) for c in chars(f)],before)
                self.assertEqual([p.unicode for p in f.pdf_paragraph],texts)

    def test_only_unused_black_text_stroke_width_may_differ(self):
        f,l,r,s=self.fixture()
        for p in f.pdf_paragraph:
            for c in chars(NS(pdf_paragraph=[p])):
                c.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction='0 0 0 rg 0.398000 w')
            p.pdf_style=copy.deepcopy(p.pdf_style)
            p.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction='0 0 0 rg 0.398000 w')
        r.pdf_style.graphic_state.passthrough_per_char_instruction='0 0 0 rg 0.498000 w'
        self.assertEqual(guard.rejoin_source_column_orphan_heads(s,f),1)

    def test_stroke_only_commands_do_not_prove_matching_fill(self):
        for first,second in [('1 w','2 w'),('0 0 0 RG 1 w','0 0 0 RG 2 w')]:
            f,l,r,s=self.fixture()
            l.pdf_style.graphic_state.passthrough_per_char_instruction=first
            r.pdf_style.graphic_state.passthrough_per_char_instruction=second
            self.assertEqual(guard.rejoin_source_column_orphan_heads(s,f),0)


class BulletedBoundaryTests(unittest.TestCase):
    def test_three_source_items_are_separate_without_changing_any_glyph(self):
        p=line_paragraph(['• For the first group: a complete source item.',
                          'continuing the first item.',
                          '• For the second group: another item.',
                          '• For the third group: the final item.'])
        for c in p.pdf_paragraph_composition[1].pdf_line.pdf_character:
            c.box.x+=10;c.box.x2+=10;c.visual_bbox.box.x+=10;c.visual_bbox.box.x2+=10
        f=page([]);f.pdf_paragraph=[p]
        original=chars(f);before=[asdict(c) for c in original]
        finder=object.__new__(ParagraphFinder)
        self.assertEqual(guard.split_structured_entries(finder,f),2)
        self.assertEqual(len(f.pdf_paragraph),3)
        self.assertIn('continuing the first item.',f.pdf_paragraph[0].unicode)
        self.assertEqual([id(c) for c in chars(f)],[id(c) for c in original])
        self.assertEqual([asdict(c) for c in chars(f)],before)
        self.assertTrue(all(p.unicode.startswith('• For') for p in f.pdf_paragraph))
        self.assertEqual(guard.split_structured_entries(finder,f),0)

    def test_inline_bullets_and_nested_or_mixed_markers_do_not_prove_sibling_items(self):
        for lines in (['A • symbol in prose','Another • symbol in prose'],
                      ['• First outer item','● A different marker'],
                      ['• First outer item','• Nested item']):
            p=line_paragraph(lines);f=page([]);f.pdf_paragraph=[p]
            if lines[-1]=='• Nested item':
                for c in p.pdf_paragraph_composition[1].pdf_line.pdf_character:
                    c.box.x+=20;c.box.x2+=20
            self.assertEqual(guard.split_structured_entries(object.__new__(ParagraphFinder),f),0)


class SubscriptIndexTests(unittest.TestCase):
    def fixture(self):
        glyphs=[character('w',119,'F2',10,100),character('h',104,'F2',17,98.5),
                character(',',44,'F3',20,98.5),character('k',107,'F2',22,98.5)]
        for i,(c,width) in enumerate(zip(glyphs,[7,3,2,3])):
            c.pdf_style.font_size=10 if i==0 else 7
            c.box.x2=c.box.x+width;c.box.y2=c.box.y+c.pdf_style.font_size
            c.visual_bbox.box=copy.deepcopy(c.box);c.render_order=100+i
        a=PdfFormula(pdf_character=glyphs[:2],line_id=3)
        b=PdfSameStyleCharacters(pdf_character=[glyphs[2]],pdf_style=glyphs[2].pdf_style)
        c=PdfFormula(pdf_character=[glyphs[3]],line_id=3)
        f=page([]);f.pdf_paragraph=[PdfParagraph(vertical=False,xobj_id=-1,
            pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=a),
             PdfParagraphComposition(pdf_same_style_characters=b),PdfParagraphComposition(pdf_formula=c)])]
        return f,glyphs,a,b,c,object.__new__(StylesAndFormulas)

    def test_complete_native_subscript_preserves_every_original_glyph(self):
        f,gs,a,b,c,s=self.fixture();before=[asdict(g) for g in gs]
        self.assertEqual(guard.bind_source_subscript_indices(s,f),1)
        merged=f.pdf_paragraph[0].pdf_paragraph_composition[0].pdf_formula
        self.assertEqual([id(g) for g in merged.pdf_character],[id(g) for g in gs])
        self.assertEqual([asdict(g) for g in gs],before)
        self.assertEqual(''.join(g.char_unicode for g in merged.pdf_character),'wh,k')
        self.assertEqual(guard.bind_source_subscript_indices(s,f),0)

    def test_unproven_math_or_prose_commas_are_not_merged(self):
        for mode in ('full-size','baseline','font','color','gap','render','different-line',
                     'offset','other-xobject','missing-glyph','formula-layout','non-comma'):
            f,gs,a,b,c,s=self.fixture()
            if mode=='full-size':gs[1].pdf_style.font_size=10
            elif mode=='baseline':gs[3].box.y+=1
            elif mode=='font':gs[3].pdf_style.font_id='F4'
            elif mode=='color':gs[3].pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction='1 0 0 rg')
            elif mode=='gap':gs[3].box.x+=5
            elif mode=='render':gs[3].render_order+=4
            elif mode=='different-line':c.line_id=4
            elif mode=='offset':c.y_offset=1
            elif mode=='other-xobject':gs[3].xobj_id=7
            elif mode=='missing-glyph':gs[2].pdf_character_id=None
            elif mode=='formula-layout':gs[2].formula_layout_id=7
            elif mode=='non-comma':gs[2].char_unicode=';'
            with self.subTest(mode=mode):
                self.assertEqual(guard.bind_source_subscript_indices(s,f),0)
                self.assertEqual(len(f.pdf_paragraph[0].pdf_paragraph_composition),3)


if __name__=='__main__':unittest.main()
