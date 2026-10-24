"""Source-defined weather terminology; portable native fixtures, no model calls."""
from copy import deepcopy
from pathlib import Path
import pickle
import re
import sys
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import translation_guard as guard
from test_page_definition_native import body
from babeldoc.format.pdf.document_il import Document,Page,PdfFont,Box,Mediabox,PdfParagraphComposition,PdfFormula,PdfSameStyleCharacters
DEFINITION='The Circulation Weather Type (CWT) defines weather classes. The first rows show anticyclone (A), and other rows show cyclone (C).'


def fixture(text,definition=DEFINITION,label='plain text'):
    a=body(definition,50,650,'definition');b=body(text,50,500,'target');b.layout_label=label
    font=PdfFont(font_id='F1',name='ABCDEF+Body',xref_id=33,encoding_length=1,ascent=.8,descent=-.2)
    pages=[Page(page_number=i,mediabox=Mediabox(Box(0,0,612,800)),pdf_font=[deepcopy(font)],pdf_paragraph=[p])for i,p in enumerate((a,b))]
    return Document(page=pages),a,b


def ti_for(p):
    text='';holders=[]
    for comp in p.pdf_paragraph_composition:
        if comp.pdf_formula is not None:
            token='{v'+str(len(holders)+1)+'}';holders.append(NS(formula=comp.pdf_formula,placeholder=token));text+=token
        else:text+=''.join(c.char_unicode for c in comp.pdf_same_style_characters.pdf_character)
    return NS(unicode=text,placeholders=holders,base_style=p.pdf_style,_pagebridge_layout_label=p.layout_label.replace(' ','_'))


def native_formula(p,letter):
    run=p.pdf_paragraph_composition[0].pdf_same_style_characters;index=next(i for i,c in enumerate(run.pdf_character)if c.char_unicode==letter)
    char=run.pdf_character[index];parts=[]
    for chars in (run.pdf_character[:index],[char],run.pdf_character[index+1:]):
        if chars==[char]:parts.append(PdfParagraphComposition(pdf_formula=PdfFormula(box=deepcopy(char.box),pdf_character=chars)))
        elif chars:parts.append(PdfParagraphComposition(pdf_same_style_characters=PdfSameStyleCharacters(box=deepcopy(run.box),pdf_style=run.pdf_style,pdf_character=chars)))
    p.pdf_paragraph_composition=parts


class DocumentWeatherTests(unittest.TestCase):
    def bound(self,text,definition=DEFINITION,label='plain text'):
        doc,definition_p,p=fixture(text,definition,label);old=pickle.dumps(doc)
        guard._capture_source_weather_terms(doc);self.assertEqual(pickle.dumps(doc),old)
        ti=ti_for(p);guard._prepare_source_weather_terms(p,ti)
        return doc,p,ti

    def test_missing_native_font_metadata_cannot_create_context(self):
        page=NS(pdf_paragraph=[NS(unicode=DEFINITION,layout_label='plain text')])
        self.assertEqual(guard._capture_source_weather_terms(NS(page=[page])),0)

    def test_native_definition_binds_later_cwt_body_and_caption(self):
        for label in ('plain text','figure_caption'):
            _,_,ti=self.bound('The CWT of earlier days is used to estimate the weather class.',label=label)
            self.assertTrue(guard._source_weather_context(ti).cwt)
            self.assertIn('Circulation Weather Type',guard._source_weather_document_hint(ti))
            guard._check_source_weather_terms(ti,'前几天的CWT用于估计天气类别。')
            guard._check_source_weather_terms(ti,'前几天的环流天气类型（CWT）用于估计天气类别。')
            for bad in ('前几天的连续小波变换（CWT）用于估计天气类别。','经小波变换（CWT）监督训练。','遥远日期的连续小波\n变换（CWT）。'):
                with self.assertRaisesRegex(guard.PlaceholderIntegrityError,'acronym-sense'):guard._check_source_weather_terms(ti,bad)

    def test_no_definition_wrong_definition_or_unselected_definition_never_guesses(self):
        for definition in ('The experiment uses CWT for analysis.','Continuous Wavelet Transform (CWT) is useful for signals.',
                           'It is not Circulation Weather Type (CWT).','The phrase "Circulation Weather Type (CWT)" is a quotation.'):
            _,_,ti=self.bound('The CWT of earlier days is analyzed.',definition)
            self.assertIsNone(guard._source_weather_context(ti))
            guard._check_source_weather_terms(ti,'前几天的连续小波变换。')
        doc,_,p=fixture('The CWT of earlier days is analyzed.');doc.page.pop(0);guard._capture_source_weather_terms(doc)
        ti=ti_for(p);guard._prepare_source_weather_terms(p,ti);self.assertIsNone(guard._source_weather_context(ti))

    def test_conflicting_expansion_disables_cwt_binding(self):
        for conflict in ('Continuous Wavelet Transform (CWT) is also defined here.','CWT (Continuous Wavelet Transform) is another definition.',
                         'The continuous wavelet transform (CWT) is also defined here.',
                         'CWT (continuous wavelet transform) is another definition.'):
            _,_,ti=self.bound('The CWT of earlier days is analyzed.',DEFINITION+' '+conflict)
            self.assertIsNone(guard._source_weather_context(ti))
        _,_,ti=self.bound('The CWT of earlier days is analyzed.',DEFINITION+' We classify the input (CWT) by weather type.')
        self.assertIsNotNone(guard._source_weather_context(ti))

    def test_definition_must_be_native_plain_prose_not_formula_or_code(self):
        for case in ('formula','glyph','unicode','code','reference'):
            doc,a,p=fixture('The CWT of earlier days is analyzed.')
            if case=='formula':
                run=a.pdf_paragraph_composition[0].pdf_same_style_characters;a.pdf_paragraph_composition=[PdfParagraphComposition(pdf_formula=PdfFormula(box=run.box,pdf_character=run.pdf_character))]
            if case=='glyph':a.pdf_paragraph_composition[0].pdf_same_style_characters.pdf_character[5].pdf_character_id=None
            if case=='unicode':a.unicode=a.unicode.replace('Circulation','Continuous')
            if case in ('code','reference'):a.layout_label=case
            guard._capture_source_weather_terms(doc);ti=ti_for(p);guard._prepare_source_weather_terms(p,ti)
            self.assertIsNone(guard._source_weather_context(ti),case)

    def test_current_source_wavelet_description_remains_legal(self):
        _,_,ti=self.bound('Compare CWT data with an independent continuous wavelet transform.')
        guard._check_source_weather_terms(ti,'将CWT数据与独立的连续小波变换进行比较。')

    def test_wind_class_context_rejects_invented_speed_only(self):
        for text in ('The classes A and C can be biased by daily average winds.',
                     'The 5LC wind series conditions the wind of the last LC6 day.',
                     'The 5LC/LC6 correlation conditions wind prediction.'):
            _,_,ti=self.bound(text);self.assertTrue(guard._source_weather_context(ti).neutral_wind)
            for good in ('日平均风','风况预测','风类型','LC6的风'):
                guard._check_source_weather_terms(ti,good)
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError,'wind-became-speed'):guard._check_source_weather_terms(ti,'日平均风速以及风速预测。')

    def test_explicit_source_speed_and_ordinary_wind_are_not_rejected(self):
        for text in ('The 5LC wind series predicts wind speed.',
                     'The LC6 wind prediction also includes velocity.',
                     'The LC6 wind prediction includes measurements in m/s.',
                     'The daily average winds were analyzed.'):
            _,_,ti=self.bound(text)
            context=guard._source_weather_context(ti)
            self.assertTrue(context is None or not context.neutral_wind)
            guard._check_source_weather_terms(ti,'预测日平均风速。')

    def test_anticyclone_centre_roles_preserve_point_and_compass_direction(self):
        _,_,ti=self.bound('an A centred to the SE of Q and a C centred to the NW of Q.')
        self.assertEqual(guard._source_weather_context(ti).centres,(('A','SE','Q'),('C','NW','Q')))
        guard._check_source_weather_terms(ti,'一个以Q东南方为中心的反气旋和一个以Q西北方为中心的气旋。')
        guard._check_source_weather_terms(ti,'A位于Q的东南方，C位于Q的西北方。')
        for bad in ('一个以Q东南方为中心的气旋和一个以Q西北方为中心的气旋。',
                    '一个以Q东南方为中心的反气旋和一个以Q西北方为中心的反气旋。'):
            with self.assertRaisesRegex(guard.PlaceholderIntegrityError,'circulation-role'):guard._check_source_weather_terms(ti,bad)

    def test_point_centred_a_is_not_a_cyclone(self):
        _,_,ti=self.bound('This would be an A centred on Q, and other centres follow.')
        guard._check_source_weather_terms(ti,'这将是以Q为中心的反气旋。')
        guard._check_source_weather_terms(ti,'中心位于Q处的一个反气旋。')
        with self.assertRaises(guard.PlaceholderIntegrityError):guard._check_source_weather_terms(ti,'中心位于Q处的一个气旋。')
        with self.assertRaises(guard.PlaceholderIntegrityError):guard._check_source_weather_terms(ti,'这将是以\x03Q\x03为中心的气旋。')
        guard._check_source_weather_terms(ti,'这将是以Q为中心的A。')

    def test_indefinite_article_a_and_conflicting_ac_are_not_codes(self):
        _,_,ti=self.bound('A third analysis considers circulation and weather classes.')
        self.assertIsNone(guard._source_weather_context(ti))
        for definition in (DEFINITION.replace('anticyclone (A)','cyclone (A)'),DEFINITION.replace('cyclone (C)','anticyclone (C)')):
            _,_,ti=self.bound('an A centred on Q.',definition);self.assertIsNone(guard._source_weather_context(ti))

    def test_single_native_formula_letter_is_read_only_and_compound_is_opaque(self):
        doc,_,p=fixture('an A centred on Q and a C centred to the NW of Q.')
        native_formula(p,'A');original=pickle.dumps(doc);formula=p.pdf_paragraph_composition[1].pdf_formula
        guard._capture_source_weather_terms(doc);ti=ti_for(p);guard._prepare_source_weather_terms(p,ti)
        self.assertTrue(guard._source_weather_context(ti).centres)
        self.assertIs(ti.placeholders[0].formula,formula);self.assertEqual(pickle.dumps(doc),original)
        guard._check_source_weather_terms(ti,'以Q为中心的{v1}与位于Q西北的C。')
        changed=deepcopy(formula);ti.placeholders[0].formula=changed;self.assertIsNone(guard._source_weather_context(ti))

    def test_document_epoch_wrong_paragraph_source_and_formula_cannot_reuse_binding(self):
        doc,p,ti=self.bound('The CWT of earlier days is analyzed.')
        wrong=ti_for(p);guard._prepare_source_weather_terms(deepcopy(p),wrong);self.assertIsNone(guard._source_weather_context(wrong))
        copy_ti=NS(unicode=ti.unicode,placeholders=ti.placeholders,base_style=ti.base_style)
        guard._copy_source_weather_terms(ti,copy_ti);self.assertIsNotNone(guard._source_weather_context(copy_ti))
        copy_ti.unicode='Other CWT paragraph.';self.assertIsNone(guard._source_weather_context(copy_ti))
        guard._capture_source_weather_terms(Document(page=[]));self.assertIsNone(guard._source_weather_context(ti))

    def test_complete_weather_sequence_range_disambiguates_target_day_class(self):
        text='Now, as an example, let us take from Table 7 the most probable wind sequence, from the fifth to the first day before the target day with CN: C NW AN A SE.'
        _,_,ti=self.bound(text);original=ti.unicode
        view=guard._source_review_prompt_view(ti,ti.unicode)
        self.assertIn('from the fifth day before the target day to the first day before the target day',view)
        self.assertIn('CN denotes the weather class of the target day: C NW AN A SE.',view)
        self.assertEqual(ti.unicode,original)
        self.assertIn('not an extra member',guard._source_weather_document_hint(ti))
        token=guard._review_batch.set([ti])
        try:
            with patch.dict(guard._originals,batch_prompt_context=lambda self,text:text):
                prompt=guard._guard_batch_prompt_context(NS(translation_config=NS(lang_out='zh')),json.dumps([dict(id='test-item',input=ti.unicode)]))
                self.assertIn('from the fifth day before the target day to the first day before the target day',prompt)
                self.assertIn('CN denotes the weather class of the target day: C NW AN A SE.',prompt)
                mismatch=guard._guard_batch_prompt_context(NS(translation_config=NS(lang_out='zh')),json.dumps([dict(id='test-item',input='A different source.')]))
                self.assertNotIn('denotes the weather class',mismatch)
        finally:guard._review_batch.reset(token)
        for other in ('The winds range from the fifth to the first day before the target day with CN.',text.replace('before the target day','after the target day'),text.replace('C NW AN A SE.','C NW AN A.'),text.replace('most probable wind sequence','recorded code list')):
            _,_,different=self.bound(other)
            self.assertEqual(guard._source_weather_range_replacements(different),[])

    def test_rich_wrapper_removal_preserves_context_for_whole_recovery(self):
        doc,_,p=fixture('The CWT of earlier days is analyzed.');guard._capture_source_weather_terms(doc);ti=ti_for(p)
        ti.unicode=ti.unicode.replace('earlier',"<style id='3'>earlier</style>")
        guard._prepare_source_weather_terms(p,ti)
        plain=NS(unicode=ti.unicode.replace("<style id='3'>",'').replace('</style>',''),placeholders=ti.placeholders,base_style=ti.base_style)
        guard._copy_source_continuity(ti,plain)
        self.assertIsNotNone(guard._source_weather_context(plain))
        self.assertIn('Circulation Weather',guard._source_review_hint(plain,'plain_text'))

if __name__=='__main__':unittest.main()
