import copy
import re
import unittest
from types import SimpleNamespace as NS
from test_translation_guard import guard, ILTranslator, FormulaPlaceholder, PdfFormula, PdfCharacter, ParagraphTranslateTracker, translator
from test_native_word_groups import fixture, styler, run, text
import layout_guard as layout
from babeldoc.format.pdf.document_il import Document, PdfParagraphComposition


def bound_input():
    page,para,_,_=fixture()
    assert layout.bind_source_accented_names(styler(),page)==1
    name=next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula)
    maths=[PdfFormula(pdf_character=[PdfCharacter(char_unicode=value)])for value in ('150%','0.49±0.08','0.92±0.03')]
    para.pdf_paragraph_composition += [PdfParagraphComposition(pdf_formula=f)for f in maths]
    source='Considering data ratio {v1}, our method scores {v2} and {v3} on Alpha and {v4} networks respectively.'
    placeholders=[FormulaPlaceholder(i,f,f'{{v{i}}}',rf'\{{v{i}\}}')for i,f in enumerate(maths+[name],1)]
    ti=ILTranslator.TranslateInput(source,placeholders,para.pdf_style)
    doc=Document(page=[page]);guard._capture_source_native_names(doc);guard._prepare_source_native_names(para,ti)
    return ti,para,page,doc,name

GOOD='考虑数据比例{v1}，我们的方法在Alpha和{v4}网络上的得分分别为{v2}和{v3}。'

class NativeNameOrderTests(unittest.TestCase):
    def test_native_name_movement_preserves_math_and_identity(self):
        ti,para,page,doc,name=bound_input()
        self.assertEqual(guard._native_name_tokens(ti),('4',))
        self.assertTrue(guard._safe_native_name_permutation(ti,ti.unicode,GOOD))
        self.assertEqual(guard.normalize_output(ti,GOOD),GOOD)
        il=translator();old=dict(guard._originals)
        try:
            guard._originals['parse']=old.get('parse',ILTranslator.parse_translate_output)
            compositions=guard._guard_parse(il,ti,GOOD)
        finally:guard._originals.clear();guard._originals.update(old)
        self.assertEqual([id(c.pdf_formula)for c in compositions if c.pdf_formula],
                         [id(ti.placeholders[n-1].formula)for n in [1,4,2,3]])

    def test_unknown_deleted_duplicate_and_math_swap_rejected(self):
        ti,*_=bound_input()
        for bad in (GOOD.replace('{v4}',''),GOOD+'{v4}',GOOD.replace('{v4}','{v8}'),
                    GOOD.replace('{v2}','TEMP').replace('{v3}','{v2}').replace('TEMP','{v3}')):
            with self.subTest(bad=bad):
                self.assertFalse(guard._safe_native_name_permutation(ti,ti.unicode,bad))
                with self.assertRaises(guard.PlaceholderIntegrityError):guard.normalize_output(ti,bad)

    def test_source_score_network_correspondence_is_not_exchanged(self):
        ti,*_=bound_input()
        for bad in (GOOD.replace('Alpha和{v4}','{v4}和Alpha'),GOOD.replace('分别',''),
                    GOOD.replace('分别为','分别为{v1}').replace('数据比例{v1}','数据比例')):
            self.assertFalse(guard._safe_native_name_permutation(ti,ti.unicode,bad))
        self.assertTrue(guard._safe_native_name_permutation(ti,ti.unicode,GOOD.replace('和','与')))

    def test_formula_text_alone_cannot_certify_a_name(self):
        ti,para,page,doc,name=bound_input()
        clone=copy.deepcopy(ti)
        self.assertFalse(guard._safe_native_name_permutation(clone,clone.unicode,GOOD))
        delattr(ti,guard._NATIVE_NAME_ATTR)
        self.assertFalse(guard._safe_native_name_permutation(ti,ti.unicode,GOOD))

    def test_glyph_state_and_native_identity_mutation_invalidates_binding(self):
        for mode in ('char-replaced','glyph-id','text','geometry','formula-offset','formula-replaced','duplicate-owner','removed-owner'):
            with self.subTest(mode=mode):
                ti,para,page,doc,name=bound_input()
                if mode=='char-replaced':name.pdf_character[0]=copy.deepcopy(name.pdf_character[0])
                elif mode=='glyph-id':name.pdf_character[0].pdf_character_id+=1
                elif mode=='text':name.pdf_character[0].char_unicode='x'
                elif mode=='geometry':name.pdf_character[0].box.x+=1
                elif mode=='formula-offset':name.x_offset+=1
                elif mode=='formula-replaced':ti.placeholders[-1].formula=copy.deepcopy(name)
                elif mode=='duplicate-owner':page.pdf_paragraph.append(para)
                elif mode=='removed-owner':page.pdf_paragraph=[]
                self.assertFalse(guard._safe_native_name_permutation(ti,ti.unicode,GOOD))

    def test_document_binding_and_original_ti_are_required(self):
        for mode in ('new-document','page-removed','source-changed'):
            ti,para,page,doc,name=bound_input()
            if mode=='new-document':guard._capture_source_native_names(Document(page=[]))
            elif mode=='page-removed':doc.page=[]
            else:ti.unicode=ti.unicode.replace('Alpha','Beta')
            self.assertFalse(guard._safe_native_name_permutation(ti,ti.unicode,GOOD))

    def test_different_and_repeated_native_name_instances_keep_source_order(self):
        ti,para,page,doc,name=bound_input()
        other_page,other,_,_=fixture()
        # A separately attested name is not interchangeable with the first.
        for co in other.pdf_paragraph_composition:
            for c in layout._composition_characters(co):
                if c.char_unicode=='M':c.char_unicode='N'
        page.pdf_paragraph.append(other)
        layout.bind_source_accented_names(styler(),page)
        other_name=next(c.pdf_formula for c in other.pdf_paragraph_composition if c.pdf_formula)
        # Keep each name owned by its original paragraph: transplanting a
        # certified formula into another paragraph never inherits permission.
        para.pdf_paragraph_composition.append(PdfParagraphComposition(pdf_formula=other_name))
        ti.placeholders.append(FormulaPlaceholder(5,other_name,'{v5}',r'\{v5\}'))
        ti.unicode+=' Compare {v5}.'
        guard._capture_source_native_names(doc);guard._prepare_source_native_names(para,ti)
        self.assertEqual(guard._native_name_tokens(ti),('4',))
        self.assertFalse(guard._safe_native_name_permutation(ti,ti.unicode,GOOD.replace('{v4}','{v5}')+' 比较{v4}。'))

    def test_two_binder_created_names_cannot_exchange_or_repeat(self):
        page,para,_,_=fixture()
        _,second,_,_=fixture()
        for co in second.pdf_paragraph_composition:
            for c in layout._composition_characters(co):
                if c.char_unicode=='M':c.char_unicode='N'
                c.box.x+=500;c.box.x2+=500
                c.visual_bbox.box.x+=500;c.visual_bbox.box.x2+=500
        para.pdf_paragraph_composition+=second.pdf_paragraph_composition
        self.assertEqual(layout.bind_source_accented_names(styler(),page),2)
        names=[c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula]
        maths=[PdfFormula(pdf_character=[PdfCharacter(char_unicode=v)])for v in ('x=1','y=2')]
        source='The measured values {v1} and {v2} apply to {v3} and {v4} networks.'
        ti=ILTranslator.TranslateInput(source,[FormulaPlaceholder(i,f,f'{{v{i}}}',rf'\{{v{i}\}}')for i,f in enumerate(maths+names,1)],para.pdf_style)
        doc=Document(page=[page]);guard._capture_source_native_names(doc);guard._prepare_source_native_names(para,ti)
        self.assertEqual(set(guard._native_name_tokens(ti)),{'3','4'})
        good='{v3}和{v4}网络的测量值为{v1}和{v2}。'
        self.assertTrue(guard._safe_native_name_permutation(ti,source,good))
        for bad in (good.replace('{v3}和{v4}','{v4}和{v3}'),good.replace('{v4}','{v3}')):
            self.assertFalse(guard._safe_native_name_permutation(ti,source,bad))

    def test_pre_binding_uses_unique_original_placeholder(self):
        ti,para,page,doc,name=bound_input()
        ti.placeholders.append(FormulaPlaceholder(5,name,'{v5}',r'\{v5\}'))
        ti.unicode+=' Compare {v5}.'
        if hasattr(ti,guard._NATIVE_NAME_ATTR):delattr(ti,guard._NATIVE_NAME_ATTR)
        guard._prepare_source_native_names(para,ti)
        self.assertFalse(guard._native_name_tokens(ti))

    def test_whole_recovery_copy_preserves_binding_without_reinterpreting_formula(self):
        ti,*_=bound_input()
        reduced,_=guard._without_rich_wrappers(ti)
        self.assertEqual(guard._native_name_tokens(reduced),('4',))
        self.assertEqual(guard.normalize_output(reduced,GOOD),GOOD)
        il=translator([GOOD])
        recovered,output,_=guard._whole_paragraph_translation(il,ti,ParagraphTranslateTracker())
        self.assertEqual(output,GOOD)
        self.assertEqual(guard._native_name_tokens(recovered),('4',))
        self.assertEqual(len(il.calls),1)

if __name__=='__main__':unittest.main()
