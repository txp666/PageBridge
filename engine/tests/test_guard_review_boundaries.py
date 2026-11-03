"""Independent production-path repros from the v6 guard review.

The fake engine returns a complete corrected paragraph when a guard requests
one. These tests require the actual retry/publication path, not merely matching
a private regex. No network, model or local user cache is used.
"""
import unittest
from copy import deepcopy
from babeldoc.format.pdf.document_il import Box
import re
from unittest.mock import patch
from test_layout_guard import line_paragraph
from test_translation_guard import RichTextPlaceholder, PdfSameStyleCharacters

from test_translation_guard import (
    guard as g, translator, paragraph, ILTranslator, FormulaPlaceholder,
    ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter,
)


def ti(text, formulas=()):
    return ILTranslator.TranslateInput(text, list(formulas), PdfStyle())


def formula(number, text):
    return FormulaPlaceholder(number, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in text]),
                              f'{{v{number}}}', rf'\{{v{number}\}}')


class GuardReviewBoundaries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def setUp(self):
        g._states.clear()
        g._counters.clear()

    def publish(self, source, output, responses=(), label='plain text'):
        p = paragraph(source)
        p.layout_label = label
        engine = translator(responses)
        engine.post_translate_paragraph(p, ParagraphTranslateTracker(), source, output)
        return p, engine

    def assert_retries(self, source, bad, good, label='plain text'):
        p, engine = self.publish(source, bad, [good], label)
        self.assertEqual(p.unicode, good)
        self.assertEqual(len(engine.calls), 1)
        self.assertTrue(engine.calls[0][1]['ignore_cache'])
        return p, engine

    def assert_accepted(self, source, good, label='plain text'):
        p, engine = self.publish(source, good, label=label)
        self.assertEqual(p.unicode, good)
        self.assertEqual(engine.calls, [])
        return p

    def test_new_unclosed_prose_emphasis_requires_retry(self):
        source = ti('The result agrees with the prediction.')
        for bad in ('结果与**预测一致。', '结果与预测**一致。'):
            with self.subTest(output=bad):
                self.assert_retries(source, bad, '结果与预测一致。')

    def test_new_mixed_cjk_identifier_emphasis_requires_retry_without_deleting_content(self):
        source = ti('The result matches model A.')
        self.assert_retries(source, '结果与**模型A**一致。', '结果与模型A一致。')

    def test_source_literal_markup_inline_code_and_formula_power_remain_allowed(self):
        self.assert_accepted(ti('The source uses **model A** as its label.'),
                             '原文使用**model A**作为标签。')
        self.assert_accepted(ti('A ** marker identifies the footnote.'),
                             '**标记用于识别脚注。')
        self.assert_accepted(ti('Preserve `**模型A**` as the literal code.'),
                             '将`**模型A**`保留为字面代码。')
        power = formula(1, 'x**2')
        p = self.assert_accepted(ti('The expression is {v1}.', [power]), '表达式为{v1}。')
        self.assertTrue(any(c.pdf_formula is power.formula for c in p.pdf_paragraph_composition))

    def test_wind_title_residual_is_case_insensitive(self):
        source = ti('Wind Data Mining by Kohonen Neural Networks')
        good = '基于Kohonen神经网络的风数据挖掘'
        for residual in ('Wind', 'wind', 'WIND'):
            with self.subTest(residual=residual):
                self.assert_retries(source, f'基于Kohonen神经网络的{residual}数据挖掘', good, 'title')

    def test_wind_company_name_citation_and_identifier_do_not_become_title_prose(self):
        self.assert_accepted(ti('Wind River Systems'), 'Wind River Systems', 'title')
        self.assert_accepted(ti('The WindToolkit API is available.'), 'WindToolkit API可用。')
        citation = ti('Fayos (2007) Wind Data Mining by Kohonen Neural Networks.')
        self.assert_accepted(citation, citation.unicode, 'reference')

    def test_single_digit_caption_number_change_requires_retry(self):
        for label, noun, translated in (('table_caption', 'Table', '表'), ('figure_caption', 'Figure', '图')):
            source = ti(f'{noun} 7. Wind prediction from previous days.')
            with self.subTest(label=label):
                self.assert_retries(source, f'{translated}8。根据前几天预测风。',
                                    f'{translated}7。根据前几天预测风。', label)

    def test_correct_caption_year_and_formula_numbers_keep_separate_roles(self):
        f = formula(1, 'R7 = 1994')
        source = ti('Table 7. Samples collected in 1994 use {v1}.', [f])
        p = self.assert_accepted(source, '表7。1994年采集的样本使用{v1}。', 'table_caption')
        self.assertTrue(any(c.pdf_formula is f.formula for c in p.pdf_paragraph_composition))
        self.assert_accepted(ti('The code Table7 is used in 1994.'), '1994年使用代码Table7。')
        opaque = formula(1, 'Table 7')
        self.assert_accepted(ti('In 1994 the formula {v1} is evaluated.', [opaque]),
                             '1994年计算公式{v1}。')

    def test_positive_non_equilibrium_cannot_be_satisfied_by_its_explicit_negation(self):
        source = ti('The analysis matches non-equilibrium critical phenomena.')
        good = '该分析与非平衡临界现象吻合。'
        for bad in ('该分析与平衡临界现象吻合，而不是非平衡临界现象。',
                    '该分析与平衡临界现象吻合，并非非平衡临界现象。'):
            with self.subTest(output=bad):
                self.assert_retries(source, bad, good)

    def test_source_negating_non_equilibrium_does_not_require_affirming_it(self):
        self.assert_accepted(ti('The analysis does not match non-equilibrium critical phenomena.'),
                             '该分析并不符合非平衡临界现象。')
        self.assert_accepted(ti('These are not non-equilibrium critical phenomena.'),
                             '这些并非非平衡临界现象。')
        self.assert_accepted(ti('We compare equilibrium and non-equilibrium critical phenomena.'),
                             '我们比较平衡与非平衡临界现象。')

    def test_month_fen_is_valid_chinese_date_grammar(self):
        source = ti('Then, if January 6th is the target day, the pair is used.')
        for good in ('那么，如果一月份的6日是目标日，就使用该向量对。',
                     '那么，如果1月份的6日是目标日，就使用该向量对。',
                     '那么，如果元月份的6日是目标日，就使用该向量对。'):
            with self.subTest(output=good):
                self.assert_accepted(source, good)

    def test_month_fen_preserves_actual_original_day_formula(self):
        day = formula(1, '6th ')
        source = ti('Then, if January {v1}is the target day, the pair is used.', [day])
        good = '那么，如果一月份的{v1}是目标日，就使用该向量对。'
        p = self.assert_accepted(source, good)
        self.assertTrue(any(c.pdf_formula is day.formula for c in p.pdf_paragraph_composition))
        self.assert_retries(source, '那么，如果二月份的{v1}是目标日，就使用该向量对。', good)


# Portable source-attested terminology and three-way relation tests.
class ResearchSemanticViewsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()
    def check(self,s,t):
        p=paragraph(s);p.layout_label='plain text';g._check_body_translation(translator([]),p,s,t)
    def test_research_laboratory_context_view_and_actual_wrong_word(self):
        for source in ('Self-driving labs use closed-loop planning for materials and catalysis discovery.',
                       'Self-driving laboratories to autonomously navigate the protein fitness landscape.'):
            s=ti(source);view=g._source_review_prompt_view(s,source)
            self.assertIn('autonomous research laboratories',view);self.assertEqual(s.unicode,source)
            self.assertEqual(g._source_review_prompt_view(s,view),view)
            self.check(s,'自主科研实验室有助于科学研究。')
            for bad in ('自动驾驶实验室开展科研。','自主驾驶实验室开展科研。'):
                with self.assertRaisesRegex(g.PlaceholderIntegrityError,'autonomous-research-laboratory'):
                    self.check(s,bad)
    def test_vehicle_code_quote_formula_and_missing_context_are_not_reinterpreted(self):
        for source in ('Self-driving labs test autonomous cars and materials and catalysis discovery.',
                       'Self-driving laboratories test vehicles near the protein fitness landscape.',
                       'Self-driving labs perform tests.',
                       'The code `Self-driving labs` concerns materials and catalysis discovery.',
                       'The label "Self-driving labs" concerns materials and catalysis discovery.',
                       "The label 'Self-driving labs' concerns materials and catalysis discovery.",
                       'The label ‘Self-driving labs’ concerns materials and catalysis discovery.'):
            s=ti(source);self.assertEqual(g._review_autonomous_laboratories(s),[])
            self.assertEqual(g._source_review_prompt_view(s,source),source)
        s=ti('Use {v1} for materials and catalysis discovery.',[formula(1,'Self-driving labs')])
        self.assertEqual(g._review_autonomous_laboratories(s),[])
    def test_three_explicit_classes_remain_in_one_relation(self):
        source='The relationships among all researchers, research tools, and research objects guide our work.'
        s=ti(source);view=g._source_review_prompt_view(s,source)
        self.assertIn('to one another',view);self.assertEqual(s.unicode,source)
        self.check(s,'研究人员、研究工具和研究对象三者之间的关系指导我们的工作。')
        with self.assertRaisesRegex(g.PlaceholderIntegrityError,'research-triad-relation-narrowed'):
            self.check(s,'所有研究人员之间的关系、研究工具以及研究对象指导我们的工作。')
        for other in ('The relationships among researchers guide research tools and research objects.',
                      'We do not use relationships among all researchers, research tools, and research objects.',
                      'The code "relationships among all researchers, research tools, and research objects" is literal.'):
            self.assertIsNone(g._review_research_triad(ti(other)))
    def test_standard_post_recovers_without_changing_formula_or_numeric_gates(self):
        s=ti('Self-driving labs support materials and catalysis discovery {v1}.',[formula(1,'X')])
        p=paragraph(s);p.layout_label='plain text';good='自主科研实验室支持材料和催化发现{v1}。'
        e=translator([good]);g._guard_post(e,p,ParagraphTranslateTracker(),s,'自动驾驶实验室支持材料和催化发现{v1}。')
        self.assertEqual(p.unicode,good);self.assertEqual(len(e.calls),1)
        self.assertIs(p.pdf_paragraph_composition[-2].pdf_formula if len(p.pdf_paragraph_composition)>1 else None,s.placeholders[0].formula)
        with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(s,'自主科研实验室支持发现。')


# Native measurement/bullet prefixes cannot be relocated as sentence objects.
def source(number='3'):
    glyphs=[]
    for i,char in enumerate('• '+number+' '):
        glyphs.append(PdfCharacter(char_unicode=char,pdf_character_id=ord(char),pdf_style=PdfStyle(font_id='F1',font_size=11),
            box=Box(60+6*i,100,66+6*i,111),xobj_id=0,vertical=False,formula_layout_id=None))
    f=PdfFormula(pdf_character=glyphs,box=Box(60,100,60+6*len(glyphs),111))
    s=ti('{v1}mm between conductive parts and separate circuits',[FormulaPlaceholder(1,f,'{v1}',r'\{v1\}')])
    s._pagebridge_layout_label='plain_text';return s


class NativeListPrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()
    def test_native_list_quantity_must_precede_the_between_relation(self):
        for n in ('3','6','0.5'):
            s=source(n);before=deepcopy(s.placeholders[0].formula)
            self.assertEqual(g._source_measured_list_prefix(s),('{v1}','mm'))
            self.assertEqual(g.normalize_output(s,'{v1}mm：导电部件与独立电路之间。'),'{v1}mm：导电部件与独立电路之间。')
            for bad in ('导电部件与独立电路之间为{v1}mm。','{v1}导电部件之间为mm。'):
                with self.assertRaisesRegex(g.PlaceholderIntegrityError,'source-list-prefix-displaced'):g.normalize_output(s,bad)
            self.assertEqual(s.placeholders[0].formula,before)
            self.assertIn('at the beginning',g._source_review_hint(s))
    def test_no_native_list_proof_means_no_new_rule(self):
        for case in ('middle','unit','between','label','quote','operator','missing_glyph_id','reverse','detached','script','vertical','xobject','nan','no_bullet'):
            with self.subTest(case=case):
                s=source();cs=s.placeholders[0].formula.pdf_character
                if case=='middle':s.unicode='The value is '+s.unicode
                if case=='unit':s.unicode=s.unicode.replace('mm','kg')
                if case=='between':s.unicode=s.unicode.replace('between','within')
                if case=='label':s._pagebridge_layout_label='equation'
                if case=='quote':s.unicode='"'+s.unicode+'"'
                if case=='operator':cs[0].char_unicode='×'
                if case=='missing_glyph_id':cs[2].pdf_character_id=None
                if case=='reverse':cs[2].box.x=40;cs[2].box.x2=46
                if case=='detached':cs[2].box.y+=20;cs[2].box.y2+=20
                if case=='script':cs[2].formula_layout_id=3
                if case=='vertical':cs[2].vertical=True
                if case=='xobject':cs[2].xobj_id=2
                if case=='nan':cs[2].box.x=float('nan')
                if case=='no_bullet':cs[0].char_unicode=' '
                self.assertIsNone(g._source_measured_list_prefix(s));self.assertNotIn('list bullet',g._source_review_hint(s))
    def test_formula_count_unknown_tokens_and_normal_post_stay_strict(self):
        s=source();p=paragraph(s);p.layout_label='plain text';f=s.placeholders[0].formula
        e=translator(['{v1}mm：导电部件与独立电路之间。'])
        g._guard_post(e,p,ParagraphTranslateTracker(),s,'导电部件与独立电路之间为{v1}mm。')
        self.assertEqual(len(e.calls),1);self.assertIs(p.pdf_paragraph_composition[0].pdf_formula,f)
        self.assertEqual(p.unicode,'{v1}mm：导电部件与独立电路之间。')
        for bad in ('3mm：导电部件之间。','{v1}mm{v1}：导电部件之间。','{v1}mm{v99}：导电部件之间。'):
            with self.assertRaises(g.PlaceholderIntegrityError):g.normalize_output(s,bad)


# Known conservative false positives, independent of the five paper fixtures.
class ExtendedSourceInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def setUp(self):
        g._states.clear()
        g._counters.clear()

    def test_explicit_source_exhibit_negation_is_not_forced_to_affirm(self):
        source = ti('The system does not exhibit non-equilibrium critical phenomena.')
        correct = '该系统表现出的并非非平衡临界现象。'
        instance = translator([]); p = paragraph(source)
        instance.post_translate_paragraph(p, ParagraphTranslateTracker(), source, correct)
        self.assertEqual(p.unicode, correct)
        self.assertEqual(instance.calls, [])

    def test_weather_title_preserves_source_attested_wind_river_name(self):
        source = ti('Wind Speed Prediction by Wind River Systems')
        correct = 'Wind River Systems 的风速预测'
        instance = translator([]); p = paragraph(source); p.layout_label = 'title'
        instance.post_translate_paragraph(p, ParagraphTranslateTracker(), source, correct)
        self.assertEqual(p.unicode, correct)
        self.assertEqual(instance.calls, [])
        # Masking the attested company must not exempt the meteorological noun.
        for residual in ('wind', 'Wind', 'WIND'):
            with self.subTest(residual=residual):
                engine = translator([correct]); target = paragraph(source); target.layout_label = 'title'
                engine.post_translate_paragraph(target, ParagraphTranslateTracker(), source,
                                                'Wind River Systems 的' + residual + '速度预测')
                self.assertEqual(target.unicode, correct)
                self.assertEqual(len(engine.calls), 1)

    def test_title_case_weather_research_phrases_are_not_proper_names(self):
        for phrase in ('Wind Speed Measurements', 'Wind Speed Data', 'Wind Data Mining',
                       'Wind Direction Analysis', 'Wind Turbine Measurements'):
            source = ti('Wind Speed Prediction from ' + phrase)
            bad = '根据 ' + phrase + ' 进行风速预测。'
            p = paragraph(source); p.layout_label = 'title'
            with self.subTest(phrase=phrase), self.assertRaisesRegex(
                    g.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
                g._check_body_translation(translator([]), p, source, bad)

    def test_attested_company_does_not_mask_another_common_weather_phrase(self):
        source = ti('Wind Speed Prediction from Wind Speed Measurements, by Wind River Systems')
        p = paragraph(source); p.layout_label = 'title'
        good = 'Wind River Systems 根据风速测量进行风速预测。'
        g._check_body_translation(translator([]), p, source, good)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'untranslated-prose-fragment'):
            g._check_body_translation(translator([]), p, source,
                                     'Wind River Systems 根据 Wind Speed Measurements 进行风速预测。')


# Portable source-role regressions; no validation files, PDFs, model or network.
SOURCE = {'R07': 'Figure 13. Accuracy (MCC) scores of RegnANN and KELLER on synthetic networks with 100 '
        'nodes and SLC data generation. (a) Results obtained on Barabasi networks varying the '
        'power-law coefficient and Erdo{v1}s-Re{v2}nyi topology while varying the mean degree of '
        'the network. (b) Results obtained for Erdo{v3}s-Re{v4}nyi (mean degree value equal to 2) '
        'while varyingthe data ratio. doi:10.1371/journal.pone.0028646.g013',
 'R12': 'Here we use the reference implementation of the algorithm provided in {v1}with default '
        'value for the data processing inequality tolerance parameter. In Section ‘‘ARACNE: '
        'varying learning parameters’’ we explore the influence of the tolerance parameter on a '
        'sample testbed.',
 'R14': 'Figure 23 shows the mean {v1}(the AUC value for the MCC-Recall curve) varying the eps '
        'value in{v2},{v3}the topology of the network and the number of nodes. Gene expression '
        'profiles are rescaled linearly in {v4}. We consider fixed data ratio equal to {v5}.',
 'R15': 'Figure 23. Mean {v1}varying the eps value, the topology of the network and the number of '
        'nodes. Gene expression profiles are rescaled linearly in {v2}. (a) Barabasi topology. (b) '
        'Erdo{v3}s-Re{v4}nyi networks. doi:10.1371/journal.pone.0028646.g023'}
def sourced(text):return ti(text,[formula(i,'x')for i in sorted(set(map(int,re.findall(r'\{v(\d+)\}',text))))])
class CandidateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):g.install()
 def test_real_complete_sources_keep_formula_order_and_original(self):
  for key,text in SOURCE.items():
   s=sourced(text);view=g._source_review_prompt_view(s,text)
   self.assertEqual(s.unicode,text);self.assertEqual(re.findall(r'\{v\d+\}',view),re.findall(r'\{v\d+\}',text));self.assertTrue(g._scientific_relation_hint(s))
  self.assertIn('; and on Erdo',g._source_review_prompt_view(sourced(SOURCE['R07']),SOURCE['R07']))
  self.assertIn('eps value in{v2},{v3}, the topology',g._source_review_prompt_view(sourced(SOURCE['R14']),SOURCE['R14']))
 def test_literal_and_excluded_regions_do_not_trigger(self):
  for id in ['R07','R14','R15']:
   text=SOURCE[id]
   for wrapped in [f'`{text}`',f'"{text}"',f"'{text}'",f'<code>{text}</code>']:
    s=sourced(wrapped);self.assertFalse(g._scientific_relation_request_views(s));self.assertFalse(g._scientific_relation_hint(s))
   for label in ['code','formula','reference','bibliography']:
    s=sourced(text);s._pagebridge_layout_label=label;self.assertFalse(g._scientific_relation_request_views(s))
 def test_conditional_negative_different_grammars_do_not_rewrite(self):
  clause=SOURCE['R07'].split('(a) ')[1].split('(b)')[0]
  for s in [sourced('If '+clause),sourced('Never use '+clause),sourced(clause.replace(' and Erdo',' or Erdo')),sourced(clause.replace('topology while varying','topology without varying'))]:
   self.assertFalse(g._source_parallel_network_experiments(s))
  for text in [SOURCE['R14'].replace('{v2},{v3}','{v2},{v2}'),SOURCE['R14'].replace('eps value','other value'),SOURCE['R15']+' Repeat {v1}.']:
   self.assertFalse(g._source_mean_parameter_variation(sourced(text)))
 def test_whole_native_name_tokens_keep_two_experiment_roles(self):
  text='Results obtained on Barabasi networks varying the power-law coefficient and {v1} topology while varying the mean degree of the network.'
  s=ti(text,[formula(1,'Erdo\u0308s-Re\u0301nyi')])
  self.assertEqual(len(g._source_parallel_network_experiments(s)),1)
  view=g._source_review_prompt_view(s,text)
  self.assertIn('; and on {v1} topology while varying',view)
  self.assertEqual(re.findall(r'\{v\d+\}',view),['{v1}'])
  self.assertEqual(s.unicode,text)
  self.assertFalse(g._source_parallel_network_experiments(ti(text,[])))
  self.assertFalse(g._source_parallel_network_experiments(sourced(text+' Repeated {v1}.')))
  self.assertFalse(g._source_parallel_network_experiments(sourced(text.replace('Barabasi','{v1}'))))
  for wrapped in ['If '+text,'"'+text+'"','`'+text+'`']:
   self.assertFalse(g._source_parallel_network_experiments(sourced(wrapped)))
 def test_ordinary_technical_terms_are_not_expanded(self):
  s=sourced(SOURCE['R12'])
  for good in ['采用数据处理不等式容差参数的默认值。{v1}','采用数据处理不等式的容差参数。{v1}']:g._check_scientific_relations(s,good)
  for bad in ['数据处理的非负性不等式容差参数。{v1}','数据处理的非等式容差参数。{v1}']:
   with self.assertRaises(g.PlaceholderIntegrityError):g._check_scientific_relations(s,bad)
  s=sourced(SOURCE['R12']+' A non-negative constraint is discussed.');g._check_scientific_relations(s,'数据处理的非负性不等式{v1}')
 def test_mean_operand_and_parameter_omission(self):
  s=sourced(SOURCE['R15'])
  g._check_scientific_relations(s,'改变eps值、网络拓扑和节点数量所得的平均{v1}。{v2}{v3}{v4}')
  for bad in ['均值随{v1}变化，改变eps。{v2}{v3}{v4}','平均{v1}，改变网络拓扑和节点数量。{v2}{v3}{v4}']:
   with self.assertRaises(g.PlaceholderIntegrityError):g._check_scientific_relations(s,bad)
 def test_post_retry_preserves_original_formulas(self):
  text='Mean {v1} varying the eps value, the topology of the network and the number of nodes.'
  s=sourced(text);p=paragraph(s);engine=translator(['改变eps值、网络拓扑和节点数量所得的平均{v1}。'])
  g._guard_post(engine,p,ParagraphTranslateTracker(),s,'均值随{v1}变化，改变eps值、网络拓扑和节点数量。')
  self.assertEqual(len(engine.calls),1);self.assertEqual(s.unicode,text);self.assertIn('the measured quantity',engine.calls[0][0]);self.assertTrue(any(c.pdf_formula is s.placeholders[0].formula for c in p.pdf_paragraph_composition))


# Source-proven prose spacing without PDF fixtures or network requests.
def word_source(text, formulas=(), label='plain_text'):
    s=ti(text,formulas);s._pagebridge_layout_label=label;return s


class ProseWordBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()
    def test_full_grammar_recovers_count_but_retains_identifier_contract(self):
        for text,good in [('Values vary between 1 and 4for Example networks.','数值在1到4之间变化，用于Example网络。'),
                          ('Table 2. Mean scores over 10runs obtained using ABC.','表2。使用ABC得到的10次运行平均分数。'),
                          ('Figure 17summarizes the recorded results.','图17总结所记录的结果。')]:
            s=word_source(text);before=s.unicode;view=g._source_review_prompt_view(s,s.unicode)
            self.assertNotEqual(view,text);self.assertEqual(s.unicode,before)
            self.assertEqual(g._source_review_prompt_view(s,view),view)
            self.assertEqual(g._prose_scientific_identifiers(s,text),g._prose_scientific_identifiers(s,good))
            self.assertEqual(g._prose_numeric_literals(s,text),g._prose_numeric_literals(s,good))
        for text in ['The model 10runs is recorded.','The token 4for has a value.','Figure17summarizes is a variable.',
                     'Values vary between 1 and 4for Example networks. The identifier 4for is also used.']:
            s=word_source(text);self.assertEqual(g._source_review_prompt_view(s,text),text)
    def test_quotes_urls_code_formulas_and_references_cannot_establish_prose(self):
        body='Table 2. Mean scores over 10runs obtained using ABC.'
        for text,label in [(f'`{body}`','plain_text'),(f'"{body}"','plain_text'),(f"'{body}'",'plain_text'),
                           ('https://example.org/10runs','plain_text'),(body,'reference'),(body,'code')]:
            s=word_source(text,label=label);self.assertEqual(g._source_prose_boundary_repairs(s),[])
        atom=formula(1,body);s=word_source('The formula is {v1}.',[atom]);self.assertEqual(g._source_prose_boundary_repairs(s),[])
    def test_native_range_tokens_are_only_separated_not_decoded(self):
        atoms=[formula(1,'[1'),formula(2,'4]')];s=word_source('Vary the exponent in the range {v1},{v2}for Example networks.',atoms)
        states=deepcopy([p.formula for p in atoms]);view=g._source_review_prompt_view(s,s.unicode)
        self.assertIn('{v1},{v2} for',view);self.assertEqual([p.formula for p in atoms],states)
        self.assertEqual(g._source_prose_boundary_repairs(word_source(s.unicode)),[])
        self.assertEqual(g._source_prose_boundary_repairs(word_source(s.unicode+' {v2}',atoms)),[])
    def test_following_whole_native_name_is_opaque_unique_and_attested(self):
        text='Values vary between 1 and 4for {v1} networks.'
        s=word_source(text,[formula(1,'An opaque name')]);view=g._source_review_prompt_view(s,text)
        self.assertIn('4 for {v1}',view);self.assertEqual(s.unicode,text)
        for bad in [word_source(text),word_source(text+' {v1}',[formula(1,'An opaque name')])]:
            self.assertEqual(g._source_review_prompt_view(bad,bad.unicode),bad.unicode)

    def test_residuals_retry_and_numbers_cannot_disappear_duplicate_or_change(self):
        s=word_source('Table 2. Mean scores over 10runs obtained using ABC.',label='table_caption');p=paragraph(s);p.layout_label='table_caption'
        good='表2。使用ABC得到的10次运行平均分数。';engine=translator([good])
        g._guard_post(engine,p,ParagraphTranslateTracker(),s,'表2。使用ABC得到的10runs平均分数。')
        self.assertEqual(p.unicode,good);self.assertEqual(len(engine.calls),1)
        self.assertIn('Source-attested spaces',engine.calls[0][0])
        for bad in ['表2。使用ABC得到的运行平均分数。','表2。使用ABC得到的11次运行平均分数。','表2。使用ABC得到的10次10次运行平均分数。']:
            self.assertNotEqual(g._prose_numeric_literals(s,s.unicode),g._prose_numeric_literals(s,bad))
    def test_straightforward_modifier_is_source_bound_not_a_product_name(self):
        s=word_source('This is not a straightfor- ward task because data contain noise.')
        self.assertIn('a straightforward task',g._source_review_prompt_view(s,s.unicode))
        self.assertIn('straightforward',g._ordinary_english_residuals(s,'这不是一项straightforward任务。'))
        self.assertEqual(g._ordinary_english_residuals(s,'这不是一项简单的任务。'),[])
        for target in ['这不是一项"straightforward"任务。','这不是一项`straightforward`任务。']:
            self.assertIn('straightforward',g._ordinary_english_residuals(s,target))
        for text in ['The tool Straightforward is used.','Keep `a straightfor- ward task` as the code.']:
            x=word_source(text);self.assertEqual(g._source_prose_boundary_repairs(x),[])
        x=word_source('This is a straightforward task. The literal "straightforward" is the label.')
        self.assertEqual(g._prose_boundary_residuals(x,'这是一项简单任务，标签为"straightforward"。'),[])
    def test_complete_native_data_heading_is_prepared_and_fixed_without_model(self):
        p=line_paragraph(['Data'],label='title');s=word_source('Data',label='title')
        p.pdf_style=p.pdf_paragraph_composition[0].pdf_line.pdf_character[0].pdf_style
        for c in p.pdf_paragraph_composition[0].pdf_line.pdf_character:c.pdf_character_id=ord(c.char_unicode);c.xobj_id=p.xobj_id
        engine=translator([]);engine.get_translate_input=lambda*a,**kw:s
        with patch.dict(g._originals,pre=lambda*a,**kw:(None,None)):
            text,actual=g._guard_pre(engine,p,ParagraphTranslateTracker(),{}, {})
        self.assertEqual(text,'Data');self.assertIs(actual,s)
        self.assertEqual(g._fixed_label_translation(engine,actual,p),'数据')
        for label in ['plain text','formula','code','fallback_line']:
            q=deepcopy(p);q.layout_label=label;self.assertIsNone(g._short_prose_connector(q))
        q=deepcopy(p);q.pdf_paragraph_composition[0].pdf_line.pdf_character[0].pdf_character_id=None
        self.assertIsNone(g._short_prose_connector(q))


# Access-date equivalence and acronym recovery, without model or file fixtures.
class ExpansionDateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):g.install()
 def setUp(self):g._states.clear();g._counters.clear()
 def body(self,s,out):g._check_body_translation(translator([]),paragraph(s),s,out)
 def test_valid_month_spellings_are_equivalent_but_other_numbers_unchanged(self):
  for name,number,chinese in [('Nov',11,'十一'),('November',11,'十一'),('Oct.',10,'十'),('December',12,'十二'),('Jan',1,'一')]:
   s=ti(f'The record was used. Accessed 2011 {name} 26.')
   for m in (str(number),chinese):
    self.body(s,f'使用了该记录。访问日期2011年{m}月26日。')
   self.body(s,f'使用了该记录。Accessed 2011 {name} 26.')
 def test_date_omission_change_duplicate_or_unrelated_month_digit_rejected(self):
  s=ti('The record was used. Accessed 2011 Nov 26.')
  for bad in ['使用了该记录。访问日期2011年10月26日。','使用了该记录。访问日期2011年11月27日。',
              '使用了该记录。访问日期2012年11月26日。','使用了该记录。2011年26日。',
              '使用了该记录。2011年11月26日。2011年11月26日。']:
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'bibliographic-access-date-changed'):self.body(s,bad)
  with self.assertRaisesRegex(g.PlaceholderIntegrityError,'prose-numeric-literal-changed'):
   self.body(s,'使用了11条记录。访问日期2011年11月26日。')
 def test_date_source_names_code_url_formula_and_invalid_calendar_do_not_trigger(self):
  for source in ['The model November is used in 2011 with 26 records.', 'Accessed 2011 Nov26.',
                 'Accessed 2011 Foo 26.', 'Accessed 2011 Feb 31.', 'Accessed 2011 Nov 32.',
                 'The code `Accessed 2011 Nov 26` is literal.', 'See https://example.test/Accessed/2011/Nov/26.',
                 'The expression is {v1}.']:
   s=ti(source,[formula(1,'Accessed 2011 Nov 26')] if '{v1}' in source else [])
   self.assertEqual(g._source_access_dates(s),{})
 def test_exact_date_preserves_multiple_occurrences_and_numeric_roles(self):
  s=ti('Accessed 2011 Nov 26. Accessed 2012 Oct 15. The count was 11.')
  self.body(s,'访问日期2012年10月15日。访问日期2011年11月26日。计数为11。')
  with self.assertRaises(g.PlaceholderIntegrityError):self.body(s,'访问日期2012年10月15日。访问日期2011年11月26日。')
 def test_fisco_definition_requires_complete_source_and_ignores_opaque_content(self):
  for source in ['FISCO is supported.', 'FISCO stands for a different protocol.',
                 'FISCO is a name. Fieldbus Intrinsically Safe COncept appears elsewhere.',
                 'The code `FISCO stands for Fieldbus Intrinsically Safe COncept` is literal.',
                 'The formula {v1} is shown.']:
   s=ti(source,[formula(1,'FISCO stands for Fieldbus Intrinsically Safe COncept')] if '{v1}' in source else [])
   self.assertIsNone(g._source_fisco_expansion(s))

 def test_normal_post_accepts_correct_numeric_calendar_date_without_retry(self):
  s=ti('The record contains 27 measurements. Accessed 2011 Nov 26.')
  good='该记录包含27次测量。访问日期2011年11月26日。'
  p=paragraph(s);e=translator([]);g._guard_post(e,p,ParagraphTranslateTracker(),s,good)
  self.assertEqual(p.unicode,good);self.assertEqual(e.calls,[])
 def test_rich_acronym_definition_uses_whole_recovery_and_keeps_english_gate(self):
  placeholders=[]
  for index,value in [(1,'F'),(3,'I'),(5,'S'),(7,'CO')]:
   style=PdfStyle(font_id='F1',font_size=10)
   comp=PdfSameStyleCharacters(pdf_style=style,pdf_character=[PdfCharacter(char_unicode=c,pdf_style=style) for c in value])
   placeholders.append(RichTextPlaceholder(index,comp,f"<style id='{index}'>",'</style>',rf"<style id='{index}'>",r'</style>'))
  expansion="<style id='1'>F</style>ieldbus <style id='3'>I</style>ntrinsically <style id='5'>S</style>afe <style id='7'>CO</style>ncept"
  s=ti('FISCO stands for '+expansion+' and describes a procedure for safe fieldbus installations.',placeholders)
  bad='FISCO代表'+expansion+'，描述安全现场总线安装的程序。'
  good='FISCO代表现场总线本质安全概念，描述安全现场总线安装的程序。'
  english='FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），描述安全现场总线安装的程序。'
  for rejected in (bad,):
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'untranslated-prose-fragment'):self.body(s,rejected)
  e=translator([bad,bad,good]);p=paragraph(s);original=s.unicode
  g._guard_post(e,p,ParagraphTranslateTracker(),s,bad)
  self.assertEqual(p.unicode,good);self.assertEqual(s.unicode,original);self.assertEqual(len(e.calls),3)
  self.body(s,english)
  self.assertNotIn("<style id='",e.calls[-1][0])

 def test_exact_bilingual_expansion_preserves_complete_original_output(self):
  s=ti('FISCO stands for Fieldbus Intrinsically Safe COncept and describes a safe installation.')
  for chinese in ('现场总线本质安全概念','现场总线本安概念'):
   for opening,closing in (('（','）'),('(',')')):
    good='FISCO代表'+chinese+opening+'Fieldbus Intrinsically Safe COncept'+closing+'，描述安全安装。'
    p=paragraph(s);e=translator([]);g._guard_post(e,p,ParagraphTranslateTracker(),s,good)
    self.assertEqual(p.unicode,good);self.assertEqual(e.calls,[])
 def test_bilingual_exception_is_not_an_english_allowlist(self):
  s=ti('FISCO stands for Fieldbus Intrinsically Safe COncept and describes an intrinsically safe installation.')
  bads=['FISCO代表Fieldbus Intrinsically Safe COncept，描述本安安装。',
        'FISCO代表现场总线概念（Fieldbus Intrinsically Safe COncept），描述本安安装。',
        'FISCO代表现场总线非本质安全概念（Fieldbus Intrinsically Safe COncept），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Safe COncept），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），描述intrinsically安全安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），另有Fieldbus。',
        'FISCO不代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept)，描述本安安装。',
        'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept），FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept）。']
  for bad in bads:
   with self.subTest(output=bad),self.assertRaisesRegex(g.PlaceholderIntegrityError,'untranslated-prose-fragment'):
    self.body(s,bad)
  for bad in ('FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe Concept）。',
              'FISCO代表现场总线本质安全概念（Fieldbus Intrinsically Safe COncept extra）。'):
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'untranslated-prose-fragment'):self.body(s,bad)


if __name__ == '__main__':
    unittest.main()
