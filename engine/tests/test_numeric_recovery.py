"""Offline contracts for whole-paragraph numeric recovery and source hints.

The fakes return complete translations, not isolated word/number repairs. These
tests exercise the restored structural and body gates; they do not certify that
a model preserved arbitrary natural-language semantic roles.
"""
import unittest

try:
    from .test_translation_guard import (
        guard, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter,
    )
except ImportError:  # unittest discover -s engine/tests
    from test_translation_guard import (
        guard, translator, paragraph, ILTranslator, FormulaPlaceholder,
        ParagraphTranslateTracker, PdfStyle, PdfFormula, PdfCharacter,
    )


def source_input(text, formulas=()):
    return ILTranslator.TranslateInput(text, list(formulas), PdfStyle())


def formula(number, visible="x+y"):
    return FormulaPlaceholder(
        number, PdfFormula(pdf_character=[PdfCharacter(char_unicode=c) for c in visible]),
        f"{{v{number}}}", rf"\{{v{number}\}}")


def recover(ti, response):
    instance = translator([response])
    plain, output, removed = guard._whole_paragraph_translation(
        instance, ti, ParagraphTranslateTracker())
    # Production performs this second independent gate before parsing/publishing.
    guard._check_body_translation(instance, paragraph(ti), plain, output)
    return output, instance, plain, removed


class NumericRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        guard.install()

    def setUp(self):
        guard._states.clear()
        guard._counters.clear()

    def test_natural_comparison_group_and_year_order_is_valid(self):
        ti = source_input(COMPARISON)
        output, engine, _, _ = recover(ti, comparison_response())
        self.assertEqual(output, COMPARISON_TRANSLATION)
        self.assertTrue(engine.calls[0][0].endswith(PROTECTED_COMPARISON))
        self.assertIn("Comparative source roles", engine.calls[0][0])
        self.assertTrue(engine.calls[0][1]["ignore_cache"])
        self.assertEqual(len(engine.calls), 1)

    def test_missing_duplicated_replaced_or_unknown_comparison_atom_is_rejected(self):
        ti = source_input(COMPARISON)
        valid = comparison_response()
        for bad in (
            valid.replace("{PBKEEP_0007}", ""),
            valid + "重复{PBKEEP_0001}。",
            valid.replace("{PBKEEP_0003}", "1995"),
            valid.replace("{PBKEEP_0003}", "{PBKEEP_0008}"),
            valid + "{PBKEEPX_0099}。",
        ):
            with self.subTest(response=bad), self.assertRaisesRegex(
                    guard.PlaceholderIntegrityError, "protected-literal-token-changed"):
                recover(ti, bad)

    def test_equal_comparison_counts_still_require_each_source_atom_once(self):
        ti = source_input(COMPARISON.replace("726 input", "7300 input"))
        output, _, _, _ = recover(ti, comparison_response())
        self.assertEqual(output.count("7300"), 2)
        self.assertNotIn("726", output)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "protected-literal-token-changed"):
            recover(ti, comparison_response().replace("{PBKEEP_0004}", "{PBKEEP_0001}"))

    def test_restored_comparison_body_gate_rejects_unanchored_extra_number(self):
        ti = source_input(COMPARISON)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "prose-numeric-literal-changed"):
            recover(ti, comparison_response() + "另有7300个数据。")

    def test_comparison_numeric_order_does_not_relax_nonnumeric_atom_order(self):
        ti = source_input("Use Zone 10, then Class I, Div. 2. " + COMPARISON)
        valid = "采用{PBKEEP_0001}，然后采用{PBKEEP_0002}。" + comparison_response(offset=2)
        output, _, _, _ = recover(ti, valid)
        self.assertEqual(output, "采用Zone 10，然后采用Class I, Div. 2。" + COMPARISON_TRANSLATION)
        invalid = "采用{PBKEEP_0002}，然后采用{PBKEEP_0001}。" + comparison_response(offset=2)
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "protected-literal-token-changed"):
            recover(ti, invalid)

    def test_comparison_numeric_order_does_not_relax_formula_order_or_objects(self):
        formulas = [formula(123), formula(456, "a+b")]
        ti = source_input("Evaluate {v123}. " + COMPARISON + "; then {v456}.", formulas)
        valid = "计算{v123}。" + comparison_response() + "然后计算{v456}。"
        output, instance, plain, _ = recover(ti, valid)
        actual = [c.pdf_formula for c in instance.parse_translate_output(plain, output) if c.pdf_formula]
        self.assertEqual(len(actual), 2)
        self.assertTrue(all(a is b.formula for a, b in zip(actual, formulas)))
        invalid = "计算{v456}。" + comparison_response() + "然后计算{v123}。"
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "formula-count-or-order"):
            recover(ti, invalid)

    def test_comparison_prefix_collision_preserves_existing_source_token(self):
        ti = source_input("The label {PBKEEP_0001}: " + COMPARISON)
        output, instance, _, _ = recover(
            ti, "标签{PBKEEP_0001}：" + comparison_response(prefix="PBKEEPX"))
        self.assertEqual(output, "标签{PBKEEP_0001}：" + COMPARISON_TRANSLATION)
        self.assertTrue(instance.calls[0][0].endswith(
            "The label {PBKEEP_0001}: " + PROTECTED_COMPARISON.replace("PBKEEP_", "PBKEEPX_")))

    def test_ordinary_training_counts_and_years_stay_visible_in_recovery(self):
        text = "The model uses 10000 training epochs in 1994."
        output, instance, _, _ = recover(source_input(text), "模型在1994年训练10000轮。")
        self.assertEqual(output, "模型在1994年训练10000轮。")
        self.assertTrue(instance.calls[0][0].endswith(text))
        self.assertNotIn("Numeric tokens", instance.calls[0][0])
        self.assertNotIn("{PBKEEP_0001}", instance.calls[0][0])
        for bad in ("模型训练10000轮。", "模型在1994年训练10000轮，另外10000轮。"):
            with self.subTest(response=bad), self.assertRaisesRegex(
                    guard.PlaceholderIntegrityError, "prose-numeric-literal-changed"):
                recover(source_input(text), bad)

    def test_truncated_training_year_stays_visible_beside_protected_parenthesis(self):
        text = "94) large training data from 1992 use 10000 epochs."
        response = "94{PBKEEP_0001}大规模训练数据来自1992年，使用10000轮。"
        output, instance, _, _ = recover(source_input(text), response)
        self.assertEqual(output, "94)大规模训练数据来自1992年，使用10000轮。")
        self.assertTrue(instance.calls[0][0].endswith(
            "94{PBKEEP_0001} large training data from 1992 use 10000 epochs."))
        self.assertNotIn("Numeric tokens", instance.calls[0][0])
        self.assertNotIn("{PBKEEP_0002}", instance.calls[0][0])
        with self.assertRaisesRegex(guard.PlaceholderIntegrityError, "prose-numeric-literal-changed"):
            recover(source_input(text), response.replace("94{", "1994{"))

    def test_only_bare_or_source_proven_joined_numbers_are_anchored(self):
        text = "The values 0.05 and 10.25 use 10000epochs in1994; Figures 13and14."
        ti = source_input(text)
        spans = guard._recovery_numeric_spans(ti, text)
        self.assertEqual([text[a:b] for a, b in spans], ["0.05", "10.25", "10000", "1994", "13", "14"])
        self.assertTrue(all(a < b <= c for (a, b), (c, _) in zip(spans, spans[1:])))

    def test_formula_ids_urls_emails_identifiers_and_protected_literals_are_not_numeric_atoms(self):
        examples = (
            "5BLC BLC6 H2O p53 V0 name_123 ABC123XYZ {v123}",
            "https://example.com/2024/12?q=123 first42@example.org",
            "Zone 10, Class I, Div. 2, IEC 60079-11, 45 V and (Smith, 1994)",
        )
        for prefix in examples:
            text = prefix + " for 10000 epochs."
            with self.subTest(text=text):
                ti = source_input(text)
                self.assertEqual([text[a:b] for a, b in guard._recovery_numeric_spans(ti, text)], ["10000"])

    def test_scientific_notation_and_compound_identifiers_are_not_freely_movable_numbers(self):
        # Moving the 10 in 1e-10 changes the exponent, not an ordinary count.
        for atom in ("1e-10", "10^-12", "IL-12", "RFC-1122"):
            text = f"The source names {atom} and uses 10000 epochs."
            with self.subTest(atom=atom):
                ti = source_input(text)
                self.assertEqual([text[a:b] for a, b in guard._recovery_numeric_spans(ti, text)], ["10000"])


COMPARISON = (
    "7300 5BLC input data represent 10 times better the possible series of 1995, "
    "than the 726 input data of 1992–93 the series of 1994")
PROTECTED_COMPARISON = (
    "{PBKEEP_0001} 5BLC input data represent {PBKEEP_0002} times better the possible series of {PBKEEP_0003}, "
    "than the {PBKEEP_0004} input data of {PBKEEP_0005}–{PBKEEP_0006} the series of {PBKEEP_0007}")


COMPARISON_TRANSLATION = (
    "与来自1992–93年、用于表征1994年序列的726个输入数据相比，"
    "7300个5BLC输入数据对1995年可能序列的表征效果好10倍。")


def comparison_response(*, offset=0, prefix="PBKEEP"):
    # Fixed source semantic roles, deliberately ordered group B before group A.
    a_count, factor, a_target, b_count, b_start, b_end, b_target = (
        "{" + prefix + f"_{number + offset:04d}" + "}" for number in range(1, 8))
    return (f"与来自{b_start}–{b_end}年、用于表征{b_target}年序列的{b_count}个输入数据相比，"
            f"{a_count}个5BLC输入数据对{a_target}年可能序列的表征效果好{factor}倍。")


class RecoveryHintTests(unittest.TestCase):
    def test_exact_comparison_grammar_preserves_distinct_input_and_target_roles(self):
        hint = guard._recovery_comparison_hint(COMPARISON, PROTECTED_COMPARISON)
        self.assertIn("Group A has {PBKEEP_0001} input data and represents the series of {PBKEEP_0003}", hint)
        self.assertIn("Group B has {PBKEEP_0004} input data from {PBKEEP_0005}–{PBKEEP_0006}", hint)
        self.assertIn("separate series of {PBKEEP_0007}", hint)
        self.assertIn("comparison factor is {PBKEEP_0002}", hint)
        self.assertNotIn("5BLC", hint)  # An identifier is never inferred to be a count.
        collision = guard._recovery_comparison_hint(COMPARISON, PROTECTED_COMPARISON.replace("PBKEEP_", "PBKEEPXX_"))
        self.assertIn("Group A has {PBKEEPXX_0001}", collision)

    def test_incomplete_ambiguous_or_different_comparison_does_not_get_hint(self):
        for original in (
            "The model trains on 7300 samples from 1992–93 and predicts 1994 and 1995.",
            COMPARISON.replace("input data represent", "input data do not represent"),
            COMPARISON.replace("than the", "rather than the"),
            COMPARISON.replace("1992–93 the series", "1992–93 predicts the series"),
            COMPARISON.replace(" the series of 1994", ""),
            COMPARISON + ". " + COMPARISON,
        ):
            with self.subTest(source=original):
                self.assertEqual(guard._recovery_comparison_hint(original, PROTECTED_COMPARISON), "")
        self.assertEqual(guard._recovery_comparison_hint(COMPARISON, "A different protected source."), "")
        self.assertEqual(guard._recovery_comparison_hint(COMPARISON, PROTECTED_COMPARISON * 2), "")

    def test_comparison_count_and_target_are_complete_numeric_atoms(self):
        # The same words surrounding an identifier do not prove a numeric role.
        for bad in ("x" + COMPARISON, COMPARISON + "x", COMPARISON + "_label"):
            with self.subTest(source=bad):
                self.assertEqual(guard._recovery_comparison_hint(bad, bad), "")

    def test_map_size_hint_uses_genuine_formula_ids_in_original_order(self):
        text = "We don’t show the Kohonen map due to its {v1}big size necessary to conserve {v2}.5 neurons per input data."
        ti = source_input(text, [formula(1, "162×162"), formula(2, "<3")])
        hint = guard._source_order_grammar_hint(ti)
        self.assertIn("size {v1}", hint)
        self.assertIn("{v2}.5 neurons", hint)
        self.assertLess(hint.index("{v1}"), hint.index("{v2}"))
        for text_bad, placeholders in (
            (text, []), (text, [formula(1)]),
            (text.replace("{v2}", "{v1}"), [formula(1)]),
            (text + " {v1}", [formula(1), formula(2)]),
            (text.replace("necessary to conserve", "too small to conserve"), [formula(1), formula(2)]),
            (text.replace(".5 neurons", " neurons"), [formula(1), formula(2)]),
        ):
            with self.subTest(text=text_bad, known=len(placeholders)):
                self.assertNotIn("neuron-count formula", guard._source_order_grammar_hint(source_input(text_bad, placeholders)))

    def test_map_size_hint_must_not_invent_a_source_negation(self):
        for text in (
            "We show the Kohonen map due to its {v1}big size necessary to conserve {v2}.5 neurons per input data.",
            "We use the map due to its {v1}big size necessary to conserve {v2}.5 neurons per input data.",
        ):
            with self.subTest(text=text):
                hint = guard._source_order_grammar_hint(source_input(text, [formula(1), formula(2)]))
                self.assertNotIn("not shown", hint)

    def test_standard_section_references_are_narrowly_source_attested(self):
        for section, zh in (("METHODS", "方法"), ("RESULTS", "结果"), ("DISCUSSION", "讨论"),
                            ("INTRODUCTION", "引言"), ("CONCLUSIONS", "结论")):
            for preposition in ("", "in "):
                ti = source_input(f"The matrices have been smoothed (see {preposition}{section}) three times.")
                with self.subTest(section=section, preposition=preposition):
                    hint = guard._contextual_term_hint(translator(), ti)
                    self.assertIn("names the paper section " + zh, hint)

    def test_ordinary_acronym_url_and_partial_section_name_do_not_get_section_hint(self):
        for source in (
            "The METHODS predictor was applied to the samples.",
            "The matrix (see in METHODS2) was used.",
            "The matrix (see in METHODS_extra) was used.",
            "The matrix (see in METHODS OF ANALYSIS) was used.",
            "The matrix see in METHODS was used.",
            "See https://example.com/(see in METHODS) for data.",
        ):
            with self.subTest(source=source):
                hint = guard._contextual_term_hint(translator(), source_input(source))
                self.assertNotIn("names the paper section", hint)


if __name__ == "__main__":
    unittest.main()
