"""Source-positive semantic regressions; no model or translation-output edits."""
import json
import unittest
from types import SimpleNamespace as NS
from unittest.mock import patch

try:
    from .test_translation_guard import (
        guard as g, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter,
    )
except ImportError:
    from test_translation_guard import (
        guard as g, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter,
    )


def formula(number, text):
    return FormulaPlaceholder(number, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in text]),
                              f"{{v{number}}}", rf"\{{v{number}\}}")


def ti(text, formulas=(), previous='', following=''):
    value = ILTranslator.TranslateInput(text, list(formulas), PdfStyle())
    if previous or following:
        setattr(value, g._CONTINUITY_ATTR, g._SourceContinuity(previous, following))
    return value


RATIO = ('The probability is the number of time steps where {v5}divided by '
         'the total number of time steps {v6}.')
RATIO_BAD = '该概率为{v5}除以总时间步数{v6}的时间步数。'
RATIO_GOOD = '该概率为满足{v5}的时间步数除以总时间步数{v6}。'


class SemanticRoleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def test_non_equilibrium_keeps_negative_meaning_and_reasonable_synonyms(self):
        source = ti('These measurements agree with the scaling theory of non-equilibrium critical phenomena.')
        for correct in ('非平衡', '非均衡', '未达到平衡', '远离平衡'):
            g._check_semantic_roles(source, '这些测量结果与' + correct + '临界现象的标度理论一致。')
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'non-equilibrium-negation-lost'):
            g._check_semantic_roles(source, '这些测量结果与平衡临界现象的标度理论一致。')
        self.assertIn('Retain this negative meaning', g._semantic_role_hint(source))

    def test_negated_term_requires_ordinary_source_not_formula_or_url(self):
        for source in (ti('The theory describes equilibrium critical phenomena.'),
                       ti('Use {v1} here.', [formula(1, 'non-equilibrium')]),
                       ti('See https://example.test/non-equilibrium for details.')):
            g._check_semantic_roles(source, '这里使用平衡临界现象的理论。')
            self.assertNotIn('Technical terminology:', g._semantic_role_hint(source))

    def test_source_term_can_cross_rich_style_boundary(self):
        source = ti("The source describes non-<style id='1'>equilibrium</style> critical phenomena.")
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'non-equilibrium-negation-lost'):
            g._check_semantic_roles(source, '原文描述平衡临界现象。')



    def test_continuation_roles_require_proven_safe_context_and_precise_boundary(self):
        for source in (ti('Recently, correlations of'), ti('The correlations of events are useful.', following='other events'),
                       ti('Recently, correlations of', following='subsequent events {v1}'),
                       ti('remains: the measurements are reported.', previous='the human remains'),
                       ti('remains: the measurements are reported.'),
                       ti('remains: the measurements are reported.', previous='the general tendency.')):
            self.assertIsNone(g._continuity_semantic_role(source.unicode, getattr(source, g._CONTINUITY_ATTR, None)))
            g._check_semantic_roles(source, '剩余的是：这里给出测量结果。')

    def test_remains_is_a_predicate_for_verified_abstract_subject(self):
        source = ti('remains: the response keeps the system near criticality.', previous='but the general tendency')
        for bad in ('剩余的是：响应使系统保持在临界状态附近。', '剩余部分：响应使系统保持在临界状态附近。',
                    '残余的内容是：响应使系统保持在临界状态附近。'):
            with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'continuation-predicate-mistranslated'):
                g._check_semantic_roles(source, bad)
        for good in ('依然存在：响应使系统保持在临界状态附近。', '仍然成立：响应使系统保持在临界状态附近。'):
            g._check_semantic_roles(source, good)
        self.assertIn('without repeating the preceding subject', g._semantic_role_hint(source))

    def test_ratio_rejects_observed_wrong_count_attachment_but_allows_other_grammar(self):
        source = ti(RATIO, [formula(5, 'σi=1'), formula(6, 'Ω')])
        self.assertEqual(g._source_count_ratio_groups(source), [('{v5}', '{v6}')])
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'count-ratio-numerator-misgrouped'):
            g._check_semantic_roles(source, RATIO_BAD)
        for good in (RATIO_GOOD, '该概率为条件{v5}成立的次数与总时间步数{v6}之比。',
                     '该概率为处于{v5}的时间步在全部{v6}个时间步中所占比例。',
                     '先统计满足条件{v5}的时间步，再除以窗口{v6}内的总时间步数。'):
            g._check_semantic_roles(source, good)
        self.assertIn('numerator is the NUMBER OF TIME STEPS', g._semantic_role_hint(source))

    def test_ratio_requires_complete_source_grammar_and_unique_real_formula_tokens(self):
        for source in (ti(RATIO), ti(RATIO, [formula(5, 'σi=1')]),
                       ti(RATIO + ' {v5}', [formula(5, 'σi=1'), formula(6, 'Ω')]),
                       ti(RATIO.replace('divided by', 'multiplied by'), [formula(5, 'σi=1'), formula(6, 'Ω')]),
                       ti(RATIO.replace('{v6}', '{v5}'), [formula(5, 'σi=1')])):
            self.assertEqual(g._source_count_ratio_groups(source), [])
            g._check_semantic_roles(source, RATIO_BAD)

    def test_actual_body_gate_rejects_semantics_only_for_chinese_prose(self):
        source = ti('The measurements follow the scaling theory of non-equilibrium critical phenomena.')
        instance = translator([])
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'non-equilibrium-negation-lost'):
            g._check_body_translation(instance, paragraph(source), source, '测量遵循平衡临界现象的标度理论。')
        instance.translation_config.lang_out = 'fr'
        g._check_body_translation(instance, paragraph(source), source, 'Les phénomènes critiques hors équilibre.')
        instance.translation_config.lang_out = 'zh'
        p = paragraph(source); p.layout_label = 'code'
        g._check_body_translation(instance, p, source, '平衡临界现象')

    def test_semantic_failure_retries_full_paragraph_and_preserves_formula_objects(self):
        formulas = [formula(5, 'σi=1'), formula(6, 'Ω')]
        source = ti(RATIO, formulas)
        instance = translator([RATIO_GOOD])
        p = paragraph(source)
        tracker = ParagraphTranslateTracker()
        g._guard_post(instance, p, tracker, source, RATIO_BAD)
        self.assertEqual(p.unicode, RATIO_GOOD)
        self.assertEqual(len(instance.calls), 1)
        self.assertTrue(instance.calls[0][0].endswith(RATIO))
        self.assertIn('numerator is the NUMBER OF TIME STEPS', instance.calls[0][0])
        actual = [c.pdf_formula for c in p.pdf_paragraph_composition if c.pdf_formula]
        self.assertEqual(len(actual), 2)
        self.assertTrue(all(c is old.formula for c, old in zip(actual, formulas)))
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'formula-count-or-order'):
            g.normalize_output(source, '总时间步数{v6}除以满足{v5}的时间步数。')

    def test_batch_hint_is_bound_to_exact_item_and_never_changes_input(self):
        source = 'remains: the system responds near criticality.'
        context = g._SourceContinuity(previous='but the general tendency')
        data = json.dumps([{'id':9, 'input':source}])
        token = g._continuity_batch.set([(source, context)])
        try:
            prompt = g._source_batch_context_prompt(data)
            self.assertIn('"input_item_id": 9', prompt)
            self.assertIn('opening \'remains:\' is a verb', prompt)
            self.assertIn('ONLY for input_item_id=9', prompt)
            self.assertNotIn('Boundary grammar', g._source_batch_context_prompt(data, chinese_target=False))
            self.assertEqual(json.loads(data)[0]['input'], source)
            self.assertEqual(g._source_batch_context_prompt(data.replace('responds','reacts')), '')
        finally:
            g._continuity_batch.reset(token)

    def test_non_equilibrium_retry_cannot_duplicate_opposite_complete_noun_phrase(self):
        source = ti('Recent measurements match non-equilibrium critical phenomena.')
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'non-equilibrium-contradictory-duplicate'):
            g._check_semantic_roles(source, '近期测量与平衡态临界现象的非平衡临界现象吻合。')
        hint = g._semantic_role_hint(source)
        self.assertIn('complete phrase non-equilibrium critical phenomena', hint)
        self.assertNotIn('equilibrium alone', hint)
        # A source discussing both kinds must not be treated as a single-kind
        # paragraph solely because the negative compound also occurs.
        both = ti('We compare equilibrium and non-equilibrium critical phenomena.')
        g._check_semantic_roles(both, '我们比较平衡临界现象与非平衡临界现象。')

    def test_term_hint_cannot_introduce_markdown_emphasis(self):
        source = ti('Recent measurements match non-equilibrium critical phenomena.')
        for target in ('近期测量与**非平衡临界现象**吻合。', '近期测量与**预期结果**吻合。'):
            with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'model-added-prose-markup'):
                g._check_added_prose_markup(source, target)
        g._check_added_prose_markup(source, '近期测量与非平衡临界现象吻合。')
        literal = ti('The label **non-equilibrium** appears in this sample.')
        g._check_added_prose_markup(literal, '这个示例含有标签**非平衡**。')
        opaque = ti('Use {v1}.', [formula(1, '**原有公式**')])
        g._check_added_prose_markup(opaque, '使用{v1}。')
        g._check_added_prose_markup(source, '见 https://example.test/**原有路径** 的说明。')
        code = ti('Preserve `**原有代码**` and explain the finding.')
        g._check_added_prose_markup(code, '保留 `**原有代码**` 并解释结果。')
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'model-added-prose-markup'):
            g._check_added_prose_markup(code, '保留 `**原有代码**` 并解释**结果**。')


    def test_only_balanced_new_pure_cjk_emphasis_is_normalized(self):
        source = ti('A non-equilibrium result uses {v1}.', [formula(1, 'x**2')])
        instance = translator([]); p = paragraph(source)
        self.assertIsNone(g._normalize_added_prose_emphasis(instance, p, source, None))
        self.assertEqual(g._normalize_added_prose_emphasis(instance, p, source, ''), '')
        before = g._counters['normalizedNewProseEmphasisPairs']
        candidate = '**非平衡结果**使用{v1}，并保留**变量**。'
        self.assertEqual(g._normalize_added_prose_emphasis(instance, p, source, candidate),
                         '非平衡结果使用{v1}，并保留变量。')
        self.assertEqual(g._counters['normalizedNewProseEmphasisPairs'], before + 2)
        for excluded in ('x**2', 'x**指数**', '**CJK词组**', '**变量2**', '***变量***',
                         '**未闭合', '未开启**', '**成对**及**未闭合',
                         '见 https://example.test/**原有路径**', '保留 `**代码**`'):
            self.assertEqual(g._normalize_added_prose_emphasis(instance, p, source, excluded), excluded)
        literal = ti('The literal source uses **bold** notation.')
        self.assertEqual(g._normalize_added_prose_emphasis(instance, paragraph(literal), literal, '**正文**'), '**正文**')
        p.layout_label = 'code'
        self.assertEqual(g._normalize_added_prose_emphasis(instance, p, source, '**代码**'), '**代码**')
        p.layout_label = 'text'; instance.translation_config.lang_out = 'en'
        self.assertEqual(g._normalize_added_prose_emphasis(instance, p, source, '**词组**'), '**词组**')

    def test_emphasis_normalization_preserves_semantics_and_original_formula_object(self):
        f = formula(1, 'F = ma'); source = ti('Non-equilibrium phenomena include {v1}.', [f])
        instance = translator([]); p = paragraph(source)
        g._guard_post(instance, p, ParagraphTranslateTracker(), source, '**非平衡现象**包括{v1}。')
        self.assertEqual(p.unicode, '非平衡现象包括{v1}。')
        self.assertEqual(instance.calls, [])
        self.assertTrue(any(c.pdf_formula is f.formula for c in p.pdf_paragraph_composition))
        bad = g._normalize_added_prose_emphasis(instance, p, source, '**平衡现象**包括{v1}。')
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'non-equilibrium-negation-lost'):
            g._check_semantic_roles(source, bad)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'unexpected-formula'):
            g.normalize_output(source, g._normalize_added_prose_emphasis(instance, p, source, '**非平衡现象**包括{v2}。'))

    def test_rejoined_source_paragraphs_cannot_skip_translation(self):
        import layout_guard
        source = ti('The complete scientific sentence must be translated after source rejoining.')
        left, right, unrelated = (paragraph(source) for _ in range(3))
        marked = {id(left): left, id(right): right}
        with patch.dict(layout_guard._rejoined_source_paragraphs, marked, clear=True), \
             patch.dict(g._originals, pre=lambda *a, **k: (None, None)):
            for p in (left, right):
                with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'rejoined-source-paragraph-skipped'):
                    translator().pre_translate_paragraph(p, ParagraphTranslateTracker(), {}, {})
            self.assertEqual(translator().pre_translate_paragraph(unrelated, ParagraphTranslateTracker(), {}, {}), (None, None))

    def test_rejoined_source_paragraph_can_prepare_and_validate_normally(self):
        import layout_guard
        source = ti('The complete scientific sentence must be translated after source rejoining.')
        p = paragraph(source); instance = translator([])
        with patch.dict(layout_guard._rejoined_source_paragraphs, {id(p): p}, clear=True), \
             patch.dict(g._originals, pre=lambda *a, **k: (source.unicode, source)):
            prepared, actual = instance.pre_translate_paragraph(p, ParagraphTranslateTracker(), {}, {})
            self.assertEqual(prepared, source.unicode)
            self.assertIs(actual, source)
            instance.post_translate_paragraph(p, ParagraphTranslateTracker(), source, '源文本重新分段后，必须翻译完整的科学句子。')
            self.assertEqual(instance.calls, [])

    def test_complete_source_sentence_has_no_retired_correlation_boundary_rules(self):
        complete = 'Recently, correlations of subsequent firing events again came into focus as a synaptic facilitation criterion.'
        source = ti(complete)
        self.assertIsNone(g._continuity_semantic_role(complete, None))
        self.assertNotIn('Boundary grammar', g._semantic_role_hint(source))
        self.assertNotIn('apposition', g._semantic_role_hint(source))
        # The old fragment-specific rules are removed even when handed stale
        # context: source segmentation now owns complete-sentence preparation.
        fragment = ti('Recently, correlations of', following='subsequent firing events')
        self.assertIsNone(g._continuity_semantic_role(fragment.unicode, getattr(fragment, g._CONTINUITY_ATTR)))
        self.assertEqual(g._continuity_semantic_hint(fragment.unicode, getattr(fragment, g._CONTINUITY_ATTR)), '')

    def test_attention_as_role_requires_source_idiom_and_accepts_equivalent_role_grammar(self):
        source = ti('The measurement came into focus as a reliable criterion.')
        self.assertIn('Use 作为 before that role', g._semantic_role_hint(source))
        for good in ('该测量作为可靠判据受到关注。', '该测量作为一种可靠标准引起了关注。',
                     '该测量作为可靠判据受到关注，而不是成为可靠标准的焦点。',
                     '“成为可靠判据的焦点”这一译法是错误的；该测量作为可靠判据受到关注。'):
            g._check_semantic_roles(source, good)
        with self.assertRaisesRegex(g.PlaceholderIntegrityError, 'attention-role-mistranslated'):
            g._check_semantic_roles(source, '该测量成为可靠标准的焦点。')

    def test_attention_role_never_reclassifies_literal_focus_formula_or_url(self):
        for source in (ti('The measurement became the focus of the standard.'),
                       ti('The expression is {v1}.', [formula(1, 'came into focus as a criterion')]),
                       ti('See https://example.test/came%20into%20focus%20as%20a%20criterion for details.')):
            self.assertFalse(g._source_attention_role(source))
            self.assertNotIn('Source idiom:', g._semantic_role_hint(source))
            g._check_semantic_roles(source, '该测量成为可靠标准的焦点。')

    def test_attention_role_full_post_retries_without_touching_formula_identity(self):
        f = formula(1, '[34]')
        source = ti('The measurement came into focus as a reliable criterion {v1}.', [f])
        correct = '该测量作为可靠判据受到关注{v1}。'
        instance = translator([correct]); p = paragraph(source)
        g._guard_post(instance, p, ParagraphTranslateTracker(), source, '该测量成为可靠标准的焦点{v1}。')
        self.assertEqual(p.unicode, correct)
        self.assertEqual(len(instance.calls), 1)
        self.assertTrue(any(c.pdf_formula is f.formula for c in p.pdf_paragraph_composition))


if __name__ == '__main__':
    unittest.main()
