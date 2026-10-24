"""Portable role-continuity tests; synthetic native glyphs, no PDF/model calls."""
from copy import deepcopy
from pathlib import Path
import pickle
import re
import sys
from types import SimpleNamespace as NS
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import layout_guard as layout
import translation_guard as guard
roles=NS(capture=guard._capture_regulator_continuity,prepare=guard._prepare_regulator_continuity,
    _context=guard._regulator_context,copy_context=guard._copy_regulator_continuity,
    replacements=guard._regulator_request_replacements,hint=guard._regulator_continuity_hint,
    check=guard._check_regulator_continuity)
from babeldoc.format.pdf.document_il import (Box,Document,GraphicState,Mediabox,Page,PdfCharacter,PdfFont,
    PdfFormula,PdfParagraph,PdfParagraphComposition,PdfSameStyleCharacters,PdfStyle,VisualBbox)


def body(text,x,y,identifier,formulas=False,after=0):
    rows=[];rest=text
    while len(rest)>58:
        cut=rest.rfind(' ',0,59)+1 or 58;rows.append(rest[:cut]);rest=rest[cut:]
    rows.append(rest);style=PdfStyle(font_id='F1',font_size=8,graphic_state=GraphicState());chars=[]
    for ri,row in enumerate(rows):
        step=240/len(row)
        for i,c in enumerate(row):
            box=Box(x+i*step,y-12*ri,x+(i+1)*step,y-12*ri+8)
            chars.append(PdfCharacter(char_unicode=c,pdf_character_id=ord(c),pdf_style=deepcopy(style),box=box,
                visual_bbox=VisualBbox(deepcopy(box)),vertical=False,xobj_id=0,advance=step))
    pbox=Box(x,min(c.box.y for c in chars),x+240,y+8);comps=[];run=[]
    def flush():
        if run:
            comps.append(PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(
                box=deepcopy(pbox),pdf_style=deepcopy(style),pdf_character=list(run))))
            run.clear()
    for i,c in enumerate(chars):
        is_atom=formulas and i>=after and re.fullmatch('[XY]',c.char_unicode) and (i==0 or text[i-1]==' ') and (i+1==len(text) or not text[i+1].isalnum())
        if is_atom:
            flush();c.pdf_style.font_id='F2'
            comps.append(PdfParagraphComposition(pdf_formula=PdfFormula(box=deepcopy(c.box),pdf_character=[c])))
        else:run.append(c)
    flush()
    return PdfParagraph(unicode=text,box=pbox,pdf_style=style,xobj_id=0,vertical=False,layout_label='plain text',
        debug_id=identifier,first_line_indent=False,pdf_paragraph_composition=comps)


def fixture():
    left=body('Consider a simple network: gene X regulates Y. We assume that X is a regulator that can be in an active or inactive state. If it is in an active state, it has',315,90,'left',True)
    opening='the effect of a linear regulation on Y. If it is in an inactive state, Y is driven by noise.'
    right=body(opening+' We generate 100 profiles: value of X is 0. We also discuss Y in later conditions.',50,600,'right',True,len(opening))
    a=body('This ordinary paragraph provides the other source column and complete contextual text.',50,300,'a')
    b=body('This ordinary paragraph provides the other source column and complete contextual text.',315,600,'b')
    fonts=[PdfFont(font_id=f'F{i}',name=f'ABCDEF+Body{i}',xref_id=32+i,encoding_length=1,ascent=.8,descent=-.2)for i in (1,2)]
    pages=[Page(page_number=n,mediabox=Mediabox(Box(0,0,612,800)),pdf_font=deepcopy(fonts),pdf_paragraph=ps)
           for n,ps in ((4,[a,left]),(5,[right,b]))]
    return Document(page=pages),left,right


def ti_for(p):
    placeholders=[];text=''
    for comp in p.pdf_paragraph_composition:
        if comp.pdf_formula:
            token='{v'+str(len(placeholders)+1)+'}';placeholders.append(NS(formula=comp.pdf_formula,placeholder=token))
            text+=token
        else:text+=''.join(c.char_unicode for c in layout._composition_characters(comp))
    return NS(unicode=text,placeholders=placeholders,base_style=p.pdf_style,_pagebridge_layout_label='plain_text')


class RegulatorContextTests(unittest.TestCase):
    def bound(self):
        doc,left,right=fixture();self.assertEqual(roles.capture(doc),1)
        ti=ti_for(right);roles.prepare(right,ti);self.assertIsNotNone(roles._context(ti));return doc,left,right,ti

    def test_native_source_and_formula_objects_never_change(self):
        doc,left,right=fixture();original=pickle.dumps(doc)
        self.assertEqual(roles.capture(doc),1);ti=ti_for(right);original_ti=ti.unicode
        formulas=[p.formula for p in ti.placeholders];roles.prepare(right,ti)
        self.assertEqual(roles.replacements(ti),[(43,45,'X','crossPageRegulatorRequestViews')])
        self.assertEqual(ti.unicode,original_ti);self.assertEqual(pickle.dumps(doc),original)
        self.assertTrue(all(a is p.formula for a,p in zip(formulas,ti.placeholders)))
        self.assertNotIn('100',roles.hint(ti));self.assertNotIn('{v',roles.hint(ti))

    def test_renamed_roles_and_nonfixed_page_numbers(self):
        _,_,_,ti=self.bound();self.assertIn('regulator X',roles.hint(ti));self.assertIn('Y is the regulated',roles.hint(ti))

    def test_same_resource_metrics_need_not_match_but_xref_must(self):
        doc,left,right=fixture();doc.page[1].pdf_font[1].descent=0
        self.assertEqual(roles.capture(doc),1)
        for field,value in [('xref_id',91),('name','ABCDEF+Other'),('encoding_length',2)]:
            with self.subTest(field=field):
                doc,left,right=fixture();setattr(doc.page[1].pdf_font[1],field,value)
                self.assertEqual(roles.capture(doc),0)

    def test_contiguous_geometry_style_and_body_boundaries_required(self):
        for case in ('gap','removed_page','indent','wrong_column','not_bottom','caption','footnote','footer_body','xobject','style','duplicate_font'):
            with self.subTest(case=case):
                doc,left,right=fixture()
                if case=='gap':doc.page[1].page_number+=1
                if case=='removed_page':doc.page.pop()
                if case=='indent':right.first_line_indent=True
                if case=='wrong_column':right.box.x+=300;right.box.x2+=300
                if case=='not_bottom':left.box.y+=200;left.box.y2+=200
                if case in ('caption','footnote'):left.layout_label=case
                if case=='footer_body':
                    p=body('This small footnote intervenes and ends the page.',315,25,'footnote');p.layout_label='footnote';doc.page[0].pdf_paragraph.append(p)
                if case=='xobject':right.xobj_id=1
                if case=='style':right.pdf_style.graphic_state=GraphicState(passthrough_per_char_instruction='red')
                if case=='duplicate_font':doc.page[1].pdf_font.append(deepcopy(doc.page[1].pdf_font[0]))
                before=pickle.dumps(doc);self.assertEqual(roles.capture(doc),0);self.assertEqual(before,pickle.dumps(doc))

    def test_source_mutation_or_ambiguous_formula_cannot_bind(self):
        for case in ('unicode','glyph','compound','cid','actor_formula_missing','formula_in_prose'):
            with self.subTest(case=case):
                doc,left,right=fixture()
                if case=='unicode':left.unicode=left.unicode.replace('regulates','does not regulate')
                if case=='glyph':layout._composition_characters(left.pdf_paragraph_composition[0])[0].pdf_character_id=None
                if case=='compound':
                    f=next(c.pdf_formula for c in left.pdf_paragraph_composition if c.pdf_formula);f.pdf_character.append(deepcopy(f.pdf_character[0]))
                if case=='cid':next(c.pdf_formula for c in left.pdf_paragraph_composition if c.pdf_formula).pdf_character[0].pdf_character_id=999
                if case=='actor_formula_missing':right.pdf_paragraph_composition=[c for c in right.pdf_paragraph_composition if not c.pdf_formula]
                if case=='formula_in_prose':
                    run=right.pdf_paragraph_composition[0].pdf_same_style_characters;right.pdf_paragraph_composition[0]=PdfParagraphComposition(pdf_formula=PdfFormula(box=run.box,pdf_character=run.pdf_character))
                if case=='glyph':
                    # The irrelevant paragraph introduction is outside the proven
                    # source role spans; corrupt a native role-prose glyph instead.
                    next(c for comp in left.pdf_paragraph_composition for c in layout._composition_characters(comp) if c.char_unicode=='r' and c.pdf_style.font_id=='F1' and c.box.x>315+130).pdf_character_id=None
                self.assertEqual(roles.capture(doc),0)

    def test_positive_assertion_only(self):
        for change in ('We do not assume that','We question whether','He says "','It is false that'):
            doc,left,right=fixture();replacement=left.unicode.replace('We assume that',change)
            doc.page[0].pdf_paragraph[-1]=body(replacement,315,90,'left',True)
            self.assertEqual(roles.capture(doc),0)

    def test_paragraph_and_native_formula_identity_are_required(self):
        doc,left,right,ti=self.bound();wrong=deepcopy(right);other=ti_for(wrong)
        roles.prepare(wrong,other);self.assertIsNone(roles._context(other))
        ti.placeholders[0].formula=deepcopy(ti.placeholders[0].formula);self.assertIsNone(roles._context(ti))
        roles.capture(Document(page=[]));self.assertIsNone(roles._context(ti));fresh=ti_for(right);roles.prepare(right,fresh);self.assertIsNone(roles._context(fresh))

    def test_context_survives_view_copy_but_not_source_or_formula_replacement(self):
        _,_,_,ti=self.bound();target=NS(unicode=ti.unicode,placeholders=ti.placeholders)
        roles.copy_context(ti,target);self.assertIs(roles._context(ti),roles._context(target))
        target.unicode='Other paragraph '+target.unicode;self.assertEqual(roles.replacements(target),[])

    def test_wrong_subject_rejected_only_in_opening_condition(self):
        _,_,_,ti=self.bound()
        for sentence in ('如果 Y 处于非激活状态，则 Y 由噪声驱动。','若基因Y失活，Y受噪声驱动。','当Y不活跃时，Y由噪声驱动。','Y处于非激活状态时，Y受噪声驱动。','基因Y失活的时候，Y由噪声驱动。'):
            with self.subTest(sentence=sentence),self.assertRaises(guard.PlaceholderIntegrityError):roles.check(ti,'线性调控对Y的影响。'+sentence+'后文{v1}。')
        for good in ('如果 X 处于非激活状态，则 Y 由噪声驱动。','如果它处于非激活状态，则Y由噪声驱动。','非激活时，Y由噪声驱动。','X处于非激活状态时，Y受噪声驱动。'):
            roles.check(ti,'线性调控对Y的影响。'+good+'后文讨论：如果Y失活。{v1}')
        roles.check(ti,'线性调控对Y的影响。若X失活，Y由噪声驱动。若Y失活，这是后文。{v1}')
        roles.check(ti,'线性调控对Y的影响。若X失活，Y由噪声驱动。Y处于非激活状态时，这是后文。{v1}')
        roles.check(ti,'线性调控对Y的影响。若X失活，Y由噪声驱动。{v1}如果Y失活。')

    def test_new_document_invalidates_already_bound_ti(self):
        _,_,_,ti=self.bound();self.assertIsNotNone(roles._context(ti))
        roles.capture(Document(page=[]));self.assertIsNone(roles._context(ti))
        self.assertEqual(roles.replacements(ti),[])

    def test_no_source_binding_does_not_guess_roles(self):
        _,_,right=fixture();ti=ti_for(right)
        roles.check(ti,'线性调控对Y的影响。如果Y失活，Y由噪声驱动。')
        self.assertEqual(roles.replacements(ti),[])

if __name__=='__main__':unittest.main()
