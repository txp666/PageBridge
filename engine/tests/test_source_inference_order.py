"""Portable source-grammar regressions; no papers, model, network or user cache."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch
from collections import Counter

from test_translation_guard import (
    guard as g, translator, paragraph, ILTranslator, FormulaPlaceholder,
    ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter, Box,
)


def source(text, known=None):
    ids = known if known is not None else list(dict.fromkeys(g._FORMULA.findall(text)))
    placeholders = [FormulaPlaceholder(int(n), PdfFormula(), '{v'+str(n)+'}', r'\{v'+str(n)+r'\}') for n in ids]
    return ILTranslator.TranslateInput(text, placeholders, PdfStyle(font_id='F1', font_size=10))


def actual_prompt_translator(responses=()):
    engine = translator(responses)
    del engine.generate_prompt_for_llm
    engine._cached_glossaries = []
    engine.translation_config.custom_system_prompt = None
    engine.translation_config.add_formula_placehold_hint = False
    return engine


class InferenceSourceOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def setUp(self):
        g._states.clear()
        g._counters.clear()

    def test_source_roles_have_equivalent_ordered_hints_without_mutation(self):
        cases = [
            ('Values are uniformly distributed in {v3},{v4} with probability {v5}.',
             '在{v3},{v4}范围内均匀分布，其概率为{v5}'),
            ('No finding is changed: no correlation (equal or less than {v7}) is recorded among {v8},{v9} and the other genes.',
             '未记录到相关性（小于或等于{v7}）'),
            ('We find that no correlation (less than {v10}) is recorded among {v11} and the other genes.',
             '未记录到相关性（小于{v10}）'),
            ('The model shows MCC scores ranging between {v4} and {v5} for data ratio {v6} and data ratio {v7} respectively.',
             'MCC得分分别为{v4}和{v5}，对应的数据比率分别为{v6}和{v7}'),
            ('We obtain {v31} different values for each gene by repeating the process {v6} times and recording the expression value at steady state.',
             '获得{v31}个不同的值，方法是重复该过程{v6}次'),
            ('Here, the value {v9} for the output unit {v10} can be calculated as follows:',
             '值{v9}对应输出单元{v10}'),
        ]
        for text, wanted in cases:
            with self.subTest(source=text):
                ti = source(text)
                before = (ti.unicode, tuple(id(p) for p in ti.placeholders))
                self.assertIn(wanted, g._source_inference_order_hint(ti, 'plain text'))
                self.assertIn(wanted, actual_prompt_translator().generate_prompt_for_llm(text, translate_input=ti))
                self.assertEqual(before, (ti.unicode, tuple(id(p) for p in ti.placeholders)))

    def test_source_count_hint_does_not_hardcode_or_duplicate_counts(self):
        ti = source('The entire procedure is repeated 12 times, the final prediction is estimated as the mean '
                    'and the associated error as twice the standard deviation of the 13 independent runs.')
        hint = g._source_inference_order_hint(ti)
        self.assertIn('整个流程重复12次，最终预测取均值，相关误差取13次独立运行的标准差的两倍', hint)
        self.assertNotIn('10', hint)
        bad = '整个流程重复12次，最终预测取13次运行的均值，相关误差取13次独立运行的标准差的两倍。'
        good = '整个流程重复12次，最终预测取均值，相关误差取13次独立运行的标准差的两倍。'
        engine = actual_prompt_translator([good])
        para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, bad)
        self.assertEqual(para.unicode, good)
        self.assertEqual(len(engine.calls), 1)
        self.assertIn('do not repeat that count', engine.calls[0][0])

    def test_unknown_duplicate_tokens_negation_code_and_nonmatching_roles_do_not_trigger(self):
        text = 'uniformly distributed in {v1},{v2} with probability {v3}'
        for ti in [source(text, ['1','2']), source(text+' and {v3}'), source(text.replace('{v2}','{v1}'))]:
            self.assertEqual('', g._source_inference_order_hint(ti))
        for value in ['`'+text+'`', '<code>'+text+'</code>', '"'+text+'"', '“'+text+'”', 'It is not '+text,
                      text.replace('uniformly','nonuniformly'), text.replace('with probability','without probability'),
                      'no correlation (greater than {v1}) is recorded among {v2} and the other genes',
                      'the value {v1} for the input unit {v2} can be calculated as follows']:
            with self.subTest(source=value):
                self.assertEqual('', g._source_inference_order_hint(source(value)))
        for label in ['code','isolate_formula','references']:
            self.assertEqual('', g._source_inference_order_hint(source(text), label))

    def test_reordered_or_missing_formula_is_still_rejected_then_retried(self):
        ti = source('We obtain {v1} different values for each gene by repeating the process {v2} times and recording the expression value at steady state.')
        good = '每个基因获得{v1}个不同的值，方法是重复该过程{v2}次并记录稳态表达值。'
        for bad in ['通过重复该过程{v2}次，获得每个基因的{v1}个不同值。', '每个基因获得{v1}个不同的值。']:
            with self.subTest(output=bad):
                with self.assertRaises(g.PlaceholderIntegrityError):
                    g.normalize_output(ti, bad)
                engine = actual_prompt_translator([good])
                para = paragraph(ti)
                engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, bad)
                self.assertEqual(len(engine.calls), 1)
                self.assertIn('获得{v1}个不同的值，方法是重复该过程{v2}次', engine.calls[0][0])
                formulas = [c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula]
                self.assertTrue(all(a is b.formula for a,b in zip(formulas,ti.placeholders)))

    def test_batch_item_hint_is_scoped_and_does_not_change_source(self):
        ti = source('Here, the value {v9} for the output unit {v10} can be calculated as follows:')
        token = g._review_batch.set([ti])
        items = json.dumps([{'id':'sample','input':ti.unicode}])
        try:
            with patch.dict(g._originals, {'batch_prompt_context': lambda self, text, *a, **k: text}):
                prompt = g._guard_batch_prompt_context(NS(translation_config=NS(lang_out='zh')), items)
            self.assertIn('only for input item "sample"', prompt)
            self.assertIn('值{v9}对应输出单元{v10}', prompt)
            self.assertIn(json.dumps(g._source_review_prompt_view(ti, ti.unicode)), prompt)
            self.assertEqual(ti.unicode, "Here, the value {v9} for the output unit {v10} can be calculated as follows:")
        finally:
            g._review_batch.reset(token)

    def test_whole_name_protection_restores_original_accent_glyph_objects(self):
        base = PdfStyle(font_id='F1', font_size=10)
        chars = [PdfCharacter(char_unicode=mark, pdf_character_id=code, pdf_style=base,
                             box=Box(x,50,x+2,60)) for mark,code,x in [('¨',168,20),('´',180,60)]]
        formulas = [FormulaPlaceholder(i,PdfFormula(pdf_character=[c]),f'{{v{i}}}',rf'\{{v{i}\}}') for i,c in enumerate(chars,1)]
        ti = ILTranslator.TranslateInput('The Erdo{v1}s- Re{v2}nyi model is used.',formulas,base)
        before = [(id(c),c.char_unicode,c.pdf_character_id,c.pdf_style.font_size,c.box.x,c.box.y) for c in chars]
        self.assertEqual(['Erdo{v1}s- Re{v2}nyi'],[w for _,_,w in g._accent_word_spans(ti,ti.unicode)])
        engine = actual_prompt_translator(['使用{PBKEEP_0001}模型。'])
        recovered, output, _ = g._whole_paragraph_translation(engine, ti, ParagraphTranslateTracker())
        self.assertEqual('使用Erdo{v1}s- Re{v2}nyi模型。',output)
        self.assertIn('Now translate the following text:\n\nThe {PBKEEP_0001} model is used.',engine.calls[0][0])
        compositions = engine.parse_translate_output(recovered,output,ParagraphTranslateTracker())
        actual = [c.pdf_formula for c in compositions if c.pdf_formula]
        self.assertEqual(len(actual),len(formulas))
        self.assertTrue(all(a is b.formula for a,b in zip(actual,formulas)))
        self.assertEqual(before,[(id(c),c.char_unicode,c.pdf_character_id,c.pdf_style.font_size,c.box.x,c.box.y) for c in chars])
        for bad in ['使用Erdős- Re{v2}nyi模型。','使用Erdo{v1}s- Rényi模型。']:
            with self.assertRaises(g.PlaceholderIntegrityError):
                g.normalize_output(ti,bad)

    def test_accent_proof_rejects_small_scripts_and_lowercase_following_word(self):
        base = PdfStyle(font_id='F1',font_size=10)
        accent = PdfCharacter(char_unicode='¨',pdf_style=PdfStyle(font_id='F1',font_size=5))
        p = FormulaPlaceholder(1,PdfFormula(pdf_character=[accent]),'{v1}',r'\{v1\}')
        ti = ILTranslator.TranslateInput('The Erdo{v1}s- Name model is used.',[p],base)
        self.assertEqual([],g._accent_word_spans(ti,ti.unicode))
        accent.pdf_style.font_size = 10
        ti.unicode = 'The Erdo{v1}s- ordinary text is used.'
        self.assertNotIn('Erdo{v1}s- ordinary',[w for _,_,w in g._accent_word_spans(ti,ti.unicode)])


# Portable tests for source-only, token-preserving equivalent request views.
class SourceOrderRequestViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def test_exact_roles_rewrite_request_only_and_keep_all_tokens(self):
        cases = [
            ('The model shows MCC scores ranging between {v13} and {v9} for data ratio {v41} and data ratio {v2} respectively.',
             'MCC scores of {v13} and {v9}; these scores correspond to data ratios {v41} and {v2} respectively'),
            ('Finally, the value {v7} for the output unit {v8} can be calculated as follows:',
             'the value {v7} corresponds to the output unit {v8} and can be calculated as follows'),
        ]
        for text,wanted in cases:
            ti=source(text); original=(ti.unicode,[id(p) for p in ti.placeholders])
            view=g._source_review_prompt_view(ti,text)
            self.assertIn(wanted,view)
            self.assertEqual(g._CANONICAL.findall(text),g._CANONICAL.findall(view))
            self.assertEqual(original,(ti.unicode,[id(p) for p in ti.placeholders]))
            self.assertEqual(view,g._source_review_prompt_view(ti,view))
            self.assertIn(wanted,actual_prompt_translator().generate_prompt_for_llm(text,translate_input=ti))

    def test_ambiguous_unproven_quoted_and_nonprose_stay_exact(self):
        plain='the value {v1} for the output unit {v2} can be calculated as follows'
        values=[plain.replace('output','input'),plain+' and {v2}',plain.replace('{v2}','{v1}'),
                'It is not '+plain,'If '+plain,'`'+plain+'`','“'+plain+'”',
                plain.replace('for the','<style id=\'3\'>for the</style>')]
        scores='MCC scores ranging between {v1} and {v2} for data ratio {v3} and data ratio {v4} respectively'
        values += [scores.replace(' respectively',''),scores.replace(' and data ratio',' or data ratio'),
                   scores.replace('MCC scores','other scores'),scores+' and {v3}']
        for text in values:
            with self.subTest(text=text):
                ti=source(text);self.assertEqual(text,g._source_review_prompt_view(ti,text))
        ti=source(plain,['1']);self.assertEqual(plain,g._source_review_prompt_view(ti,plain))
        for label in ['code','formula','references']:
            ti=source(plain);ti._pagebridge_layout_label=label
            self.assertEqual(plain,g._source_review_prompt_view(ti,plain))

    def test_no_permutation_or_bad_pairing_is_newly_authorized(self):
        ti=source('The model shows MCC scores ranging between {v1} and {v2} for data ratio {v3} and data ratio {v4} respectively.')
        for bad in ['数据比率{v3}和{v4}对应MCC得分{v1}和{v2}。',
                    '数据比率{v4}和{v3}对应MCC得分{v1}和{v2}。',
                    'MCC得分{v2}和{v1}对应数据比率{v3}和{v4}。']:
            with self.subTest(output=bad),self.assertRaises(g.PlaceholderIntegrityError):
                g.normalize_output(ti,bad)
        ti=source('Finally, the value {v7} for the output unit {v8} can be calculated as follows:')
        with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,'输出单元{v8}的值{v7}如下。')
        good='最后，值{v7}对应输出单元{v8}，可按下式计算：'
        engine=actual_prompt_translator([good]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,'输出单元{v8}的值{v7}如下。')
        self.assertEqual(para.unicode,good)
        self.assertEqual(len(engine.calls),1)
        self.assertEqual(ti.unicode,'Finally, the value {v7} for the output unit {v8} can be calculated as follows:')
        self.assertIn('the value {v7} corresponds to the output unit {v8}',engine.calls[0][0])


# Portable complete-grammar tests for a compound prose section reference.
class CompoundSectionReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def test_complete_reference_restores_request_boundary_only(self):
        text = 'The clusters on {v1} have 5 neighbours (defined above in DataClassification by KNN).'
        ti = source(text)
        before = (ti.unicode, [id(p) for p in ti.placeholders])
        self.assertEqual(['Data Classification'], g._source_section_references(ti))
        view = g._source_review_prompt_view(ti, text)
        self.assertEqual(text.replace('DataClassification', 'Data Classification'), view)
        self.assertEqual(before, (ti.unicode, [id(p) for p in ti.placeholders]))
        self.assertIn('数据分类', actual_prompt_translator().generate_prompt_for_llm(text, translate_input=ti))
        self.assertEqual(view, g._source_review_prompt_view(ti, view))

    def test_glued_spaced_and_wrapped_heading_residue_rejected(self):
        ti = source('Use {v1} (defined above in DataClassification by KNN).')
        for term in ['DataClassification', 'Data Classification', 'data\nclassification']:
            with self.subTest(term=term):
                self.assertIn('Data Classification', g._ordinary_english_residuals(ti, '采用{v1}（参见'+term+'中的KNN定义）。'))
        self.assertEqual([], g._ordinary_english_residuals(ti, '采用{v1}（参见数据分类中的KNN定义）。'))

    def test_unproven_identifiers_code_quotes_and_labels_stay_unchanged(self):
        clause = '(defined above in DataClassification by KNN)'
        values = ['Use DataClassification software.', clause.replace('defined above', 'not defined above'),
                  clause.replace(' by KNN', ''), clause.replace('DataClassification', 'DataClassification2'),
                  clause.replace('DataClassification', 'DataClassification_extra'),
                  '`'+clause+'`', '"'+clause+'"', "'"+clause+"'", '“'+clause+'”', '‘'+clause+'’',
                  '<code>'+clause+'</code>']
        for text in values:
            with self.subTest(text=text):
                ti = source(text)
                self.assertEqual([], g._data_classification_references(ti))
                self.assertEqual(text, g._source_review_prompt_view(ti, text))
        for label in ['code', 'formula', 'reference', 'bibliography']:
            ti = source(clause)
            ti._pagebridge_layout_label = label
            self.assertEqual([], g._data_classification_references(ti))

    def test_complete_retry_retains_original_formula_objects(self):
        ti = source('Use {v1} (defined above in DataClassification by KNN).')
        good = '采用{v1}（参见数据分类中的KNN定义）。'
        engine = actual_prompt_translator([good])
        para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti,
                                       '采用{v1}（参见DataClassification中的KNN定义）。')
        self.assertEqual(good, para.unicode)
        self.assertEqual(1, len(engine.calls))
        self.assertIn('Data Classification', engine.calls[0][0])
        self.assertEqual(ti.placeholders[0].formula, next(c.pdf_formula for c in para.pdf_paragraph_composition if c.pdf_formula))

    def test_batch_hint_only_scoped_to_matching_input(self):
        text = 'Use {v1} (defined above in DataClassification by KNN).'
        ti, other = source(text), source('DataClassification is the software name.')
        token = g._review_batch.set([ti, other])
        try:
            with patch.dict(g._originals, {'batch_prompt_context': lambda self, text, *a, **kw: text}):
                prompt = g._guard_batch_prompt_context(NS(translation_config=NS(lang_out='zh')),
                    json.dumps([{'id':'section','input':text}, {'id':'code-name','input':other.unicode}]))
            self.assertIn('only for input item "section"', prompt)
            self.assertNotIn('only for input item "code-name"', prompt)
            self.assertIn('Data Classification by KNN', prompt)
            self.assertIn(other.unicode, prompt)
        finally:
            g._review_batch.reset(token)


# Portable reference ownership and ordinary electrical modifier regressions.
class StandardReferenceOwnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def test_reference_owner_and_number_pair_preserved(self):
        ti = source('See IEC/EN 60079-14, Article 504 of the NEC and Section 18 of the CEC.')
        view = g._source_review_prompt_view(ti, ti.unicode)
        self.assertEqual('See IEC/EN 60079-14, NEC Article 504 and CEC Section 18.', view)
        for good in ['参见IEC/EN 60079-14、NEC第504条和CEC第18节。',
                     '参见IEC/EN 60079-14、504条（NEC）和18节（CEC）。']:
            g._check_review_roles(ti, good)
        for bad in ['参见IEC/EN 60079-14第504条和CEC第18节。',
                    '参见IEC/EN 60079-14、NEC第18条和CEC第504节。',
                    '参见IEC/EN 60079-14、NEC和CEC第504条，以及第18节。',
                    '参见IEC/EN 60079-14、NEC第5040条和CEC第18节。']:
            with self.subTest(bad=bad), self.assertRaises(g.PlaceholderIntegrityError):
                g._check_review_roles(ti, bad)

    def test_normal_retry_rejects_dropped_owner(self):
        ti = source('The methods in IEC/EN 60079-14, Article 504 of the NEC and Section 18 of the CEC apply.')
        good = '适用IEC/EN 60079-14、NEC第504条及CEC第18节的方法。'
        engine = actual_prompt_translator([good]); para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti,
                                       '适用IEC/EN 60079-14第504条及CEC第18节的方法。')
        self.assertEqual(1, len(engine.calls))
        self.assertEqual(good, para.unicode)
        self.assertIn('NEC第504条', engine.calls[0][0])

    def test_code_quotation_and_ambiguous_authority_not_proof(self):
        value = 'Article 504 of the NEC'
        for text in ['`'+value+'`', '"'+value+'"', "'"+value+"'", '<code>'+value+'</code>',
                     value.replace('NEC','NEC2'), value.replace('of the','for the'), 'Article 504 applies to NEC data.']:
            with self.subTest(text=text):
                ti = source(text)
                self.assertEqual([], g._standard_reference_owners(ti))
                self.assertEqual(text, g._source_review_prompt_view(ti, text))
        for label in ['code','references','formula']:
            ti = source(value); ti._pagebridge_layout_label = label
            self.assertEqual([], g._standard_reference_owners(ti))

    def test_resultant_is_ordinary_only_in_proven_electrical_source_context(self):
        ti = source('In a series connection, the resulting voltage is the sum and the resulting current is the larger value.')
        good = '在串联连接中，所得电压为电压之和，所得电流取较大值。'
        self.assertEqual([], g._ordinary_english_residuals(ti, good))
        self.assertIn('resultant', g._ordinary_english_residuals(ti, good.replace('所得','resultant')))
        engine = actual_prompt_translator([good]); para = paragraph(ti)
        engine.post_translate_paragraph(para, ParagraphTranslateTracker(), ti, good.replace('所得','resultant'))
        self.assertEqual(good, para.unicode)
        self.assertEqual(1, len(engine.calls))
        for text in ['The resultant method uses series connection.', 'The resulting vector is shown.',
                     '`In a series connection the resulting voltage changes.`']:
            self.assertFalse(g._resulting_electrical_quantity(source(text)))

    def test_comma_numbered_reference_is_valid_without_cross_owner_match(self):
        ti = source('Article 504 of the NEC and Section 18 of the CEC apply.')
        for good in ['适用NEC，第504条；CEC，第18节。', '适用NEC, 第504条及CEC, 第18节。']:
            g._check_review_roles(ti, good)
        for bad in ['适用NEC，CEC第504条，第18节。', '适用MyNEC，第504条；CEC，第18节。',
                    '适用NEC，第18条；CEC，第504节。', '适用NEC，第5040条；CEC，第18节。']:
            with self.subTest(bad=bad), self.assertRaises(g.PlaceholderIntegrityError):
                g._check_review_roles(ti, bad)

    def test_source_literal_allowed_once_but_new_quotes_cannot_hide_residue(self):
        clause = 'In a series connection, the resulting voltage is the sum.'
        for literal in ['`resultant`', '"resultant"', "'resultant'", '“resultant”', '<code>resultant</code>']:
            with self.subTest(literal=literal):
                ti = source(clause+' The source code label is '+literal+'.')
                good = '在串联连接中，所得电压是电压之和。源代码标签是'+literal+'。'
                self.assertEqual([], g._ordinary_english_residuals(ti, good))
                self.assertIn('resultant', g._ordinary_english_residuals(ti, good+literal))
                self.assertIn('resultant', g._ordinary_english_residuals(source(clause), '在串联连接中，'+literal+'电压是电压之和。'))


# Portable lost-space tests for a complete pair of numbered module labels.
class ParallelModuleNumberTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()

    def test_parallel_prose_proves_boundary_without_changing_real_source(self):
        text='The two scores are {v1} and {v2} on module 23 and module34 respectively.'
        ti=source(text);before=(ti.unicode,[id(p) for p in ti.placeholders])
        self.assertEqual(Counter({'23':1,'34':1}),g._prose_numeric_literals(ti,text))
        self.assertEqual(Counter(),g._prose_scientific_identifiers(ti,text))
        view=g._source_review_prompt_view(ti,text)
        self.assertEqual(text.replace('module34','module 34'),view)
        self.assertEqual(g._CANONICAL.findall(text),g._CANONICAL.findall(view))
        self.assertEqual(before,(ti.unicode,[id(p) for p in ti.placeholders]))
        self.assertEqual(view,g._source_review_prompt_view(ti,view))

    def test_actual_shape_accepts_two_source_twelve_occurrences(self):
        ti=source('Model scores {v1} on module 6 and scores {v2} on module 12. '
                  'Its two scores are {v3} and {v4} on module 6 and module12 respectively.')
        good='模型在模块6得分为{v1}，在模块12得分为{v2}。两个得分为{v3}和{v4}，分别对应模块6和模块12。'
        self.assertEqual(Counter({'12':2}),g._prose_numeric_literals(ti,ti.unicode))
        engine=actual_prompt_translator([]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,good)
        self.assertEqual(good,para.unicode);self.assertEqual([],engine.calls)
        self.assertEqual([id(p.formula) for p in ti.placeholders],
                         [id(c.pdf_formula) for c in para.pdf_paragraph_composition if c.pdf_formula])

    def test_wrong_missing_or_extra_number_and_formula_still_fail(self):
        ti=source('The two scores are {v1} and {v2} on module 23 and module34 respectively.')
        good='两个得分为{v1}和{v2}，分别对应模块23和模块34。'
        for bad in [good.replace('34','35'),good.replace('和模块34',''),good+'模块34。']:
            with self.subTest(output=bad),self.assertRaises(g.PlaceholderIntegrityError):
                g._check_body_translation(actual_prompt_translator(),paragraph(ti),ti,bad)
        for bad in [good.replace('{v1}',''),good.replace('{v1}','{v2}'),good.replace('{v1}','{x}').replace('{v2}','{v1}').replace('{x}','{v2}')]:
            with self.subTest(output=bad),self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,bad)

    def test_code_quotes_and_incomplete_or_identifier_grammar_stay_opaque(self):
        value='on module 23 and module34 respectively'
        for text in [value.replace(' respectively',''),value.replace('on module 23','on module23'),
                     value.replace('and module','or module'),value.replace('module34','module34A'),
                     '`'+value+'`','"'+value+'"',"'"+value+"'",'“'+value+'”','‘'+value+'’',
                     '<code>'+value+'</code>',value.replace('module34','Module34')]:
            with self.subTest(source=text):
                ti=source(text)
                self.assertEqual([],g._joined_parallel_module_numbers(text))
                self.assertEqual(text,g._source_review_prompt_view(ti,text))
                self.assertTrue(any(k.lower().startswith('module') for k in g._prose_scientific_identifiers(ti,text)))
        ti=source(value);ti._pagebridge_layout_label='code'
        self.assertEqual(value,g._source_review_prompt_view(ti,value))

    def test_same_named_real_identifier_elsewhere_is_still_required(self):
        text='The scores are on module 23 and module34 respectively; module34 is the software identifier.'
        ti=source(text)
        self.assertEqual(Counter({'module34':1}),g._prose_scientific_identifiers(ti,text))
        self.assertEqual(Counter({'23':1,'34':1}),g._prose_numeric_literals(ti,text))
        view=g._source_review_prompt_view(ti,text)
        self.assertEqual(text.replace('and module34','and module 34'),view)
        self.assertTrue(view.endswith('module34 is the software identifier.'))
        bad='分数分别对应模块23和模块34；该软件标识符已删除。'
        with self.assertRaises(g.PlaceholderIntegrityError):
            g._check_body_translation(actual_prompt_translator(),paragraph(ti),ti,bad)

    def test_batch_and_normal_retry_use_view_but_validate_real_source(self):
        text='The scores are {v1} and {v2} on module 23 and module34 respectively.'
        ti=source(text);token=g._review_batch.set([ti])
        try:
            with patch.dict(g._originals,{'batch_prompt_context':lambda self,value,*a,**kw:value}):
                result=g._guard_batch_prompt_context(NS(translation_config=NS(lang_out='zh')),
                    json.dumps([{'id':'same-item','input':text}]))
            self.assertEqual([{'id':'same-item','input':text.replace('module34','module 34')}],json.loads(result))
        finally:g._review_batch.reset(token)
        good='得分为{v1}和{v2}，分别对应模块23和模块34。'
        engine=actual_prompt_translator([good]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,good.replace('34','35'))
        self.assertEqual(len(engine.calls),1)
        self.assertIn('on module 23 and module 34 respectively',engine.calls[0][0])
        self.assertEqual(text,ti.unicode)
        self.assertEqual(good,para.unicode)


# Portable numbered-rule headings and complete publisher metadata boundaries.
META = ('Citation: Sample AB, Author CD (2018) Rules for Reproducible Research. '
        'Journal Name 12(3): e1004567. doi:10.1234/journal.test.1004567 '
        'Editor: Editor E. Name, University of Example, Example Country Published November 26, 2018')

def title(text):
    ti=source(text);ti._pagebridge_layout_label='title';return ti

class DemoSourceBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()

    def check(self,ti,out):
        para=paragraph(ti);para.layout_label=getattr(ti,'_pagebridge_layout_label','plain text')
        g._check_body_translation(actual_prompt_translator(),para,ti,out)

    def test_glued_heading_number_is_prose_and_valid_response_needs_no_retry(self):
        for n,cn in [(1,'一'),(10,'十'),(21,'二十一')]:
            ti=title(f'Rule{n}: Keep the Original Results for Research')
            self.assertEqual(g._prose_scientific_identifiers(ti,ti.unicode),Counter())
            for translated in [f'规则{n}：保留科研的原始结果',f'第{cn}条：保留科研的原始结果']:
                self.check(ti,translated)
                para=paragraph(ti);para.layout_label='title';engine=actual_prompt_translator([])
                engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,translated)
                self.assertEqual(para.unicode,translated);self.assertEqual(engine.calls,[])
            self.assertEqual(g._source_review_prompt_view(ti,ti.unicode),ti.unicode.replace('Rule','Rule ',1))
            self.assertTrue(ti.unicode.startswith(f'Rule{n}:'))

    def test_number_must_remain_in_the_heading_not_move_or_change(self):
        for n in [1,10]:
            ti=title(f'Rule{n}: Keep the Original Results for Research')
            for wrong in [f'规则{n+1}：保留结果',f'保留{n}项原始结果',f'规则{n}0：保留结果','规则：保留结果']:
                with self.subTest(n=n,wrong=wrong),self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,wrong)
        ti=title('Rule10: Keep the Original Results for Research')
        with self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,'规则10：保留结果，另外有10份数据。')

    def test_body_quotes_code_identifiers_and_incomplete_titles_are_not_rules(self):
        valid='Rule1: Keep the Original Results for Research'
        for value in ['Rule1Beta: Keep the Original Results for Research',valid.replace(':',''),
                      'Rule1: ModelName',valid.lower(),'"'+valid+'"',"'"+valid+"'",'`'+valid+'`','<code>'+valid+'</code>']:
            ti=title(value);self.assertIsNone(g._source_rule_heading(ti));self.assertEqual(g._source_review_prompt_view(ti,value),value)
        for label in ['plain_text','abandon','code','references','formula']:
            ti=title(valid);ti._pagebridge_layout_label=label;self.assertIsNone(g._source_rule_heading(ti))

    def test_only_initial_heading_occurrence_is_reclassified(self):
        ti=title('Rule10: Use Rule10 and H2O for the 2018 Results')
        self.assertEqual(g._prose_scientific_identifiers(ti,ti.unicode),Counter({'Rule10':1,'H2O':1}))
        good='规则10：对2018年的结果使用Rule10和H2O'
        self.check(ti,good)
        with self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,good.replace('Rule10','规则10'))
        with self.assertRaises(g.PlaceholderIntegrityError):self.check(ti,good.replace('2018','2019'))

    def test_batch_view_keeps_formula_objects_and_other_inputs_unchanged(self):
        ti=title('Rule10: Preserve the Original Results for {v1}')
        other=source('The identifier Rule10 is used for this program.')
        original=ti.unicode;identity=id(ti.placeholders[0].formula)
        token=g._review_batch.set([ti,other])
        try:
            with patch.dict(g._originals,{'batch_prompt_context':lambda self,text,*a,**k:text}):
                value=g._guard_batch_prompt_context(NS(translation_config=NS(lang_out='zh')),
                    json.dumps([{'id':'heading','input':ti.unicode},{'id':'body','input':other.unicode}]))
            self.assertIn('Rule 10:',value);self.assertIn(other.unicode,value)
        finally:g._review_batch.reset(token)
        self.assertEqual(ti.unicode,original);self.assertEqual(id(ti.placeholders[0].formula),identity)
        with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(ti,'规则10：保留原结果')

    def test_complete_citation_editor_date_block_is_metadata_only_in_abandon(self):
        ti=source(META)
        self.assertTrue(g._preserve_publication_metadata(ti,'abandon'))
        for label in ['plain text','title','reference','code']:
            self.assertFalse(g._preserve_publication_metadata(ti,label))
        for value in ['"'+META+'"',"'"+META+"'",'The example is '+META,META+' Additional results are discussed here.',
                      META.replace('doi:10.1234/journal.test.1004567 ','') ,META.replace('Editor: ','Contributor: '),
                      META.replace('November 26','November 31'),META.replace('12(3)','12'),
                      META.replace('e1004567','e1004568'),META.replace('Published','Received')]:
            with self.subTest(value=value):self.assertFalse(g._preserve_publication_metadata(source(value),'abandon'))
        ti=source(META+' {v1}');self.assertFalse(g._preserve_publication_metadata(ti,'abandon'))

    def test_native_pre_preserves_metadata_without_calling_model_or_post(self):
        ti=source(META);para=paragraph(ti);para.layout_label='abandon'
        original=para.pdf_paragraph_composition;engine=actual_prompt_translator([])
        with patch.dict(g._originals,{'pre':lambda *a,**k:(ti.unicode,ti)}):
            result=g._guard_pre(engine,para,ParagraphTranslateTracker(),{}, {})
        self.assertEqual(result,(None,None));self.assertEqual(para.unicode,META)
        self.assertIs(para.pdf_paragraph_composition,original);self.assertEqual(engine.calls,[])


if __name__ == '__main__':
    unittest.main()
