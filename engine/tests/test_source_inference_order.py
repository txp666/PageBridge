"""Portable source-grammar regressions; no papers, model, network or user cache."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

try:
    from .test_translation_guard import (
        guard as g, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter, Box,
    )
except ImportError:
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


if __name__ == '__main__':
    unittest.main()
