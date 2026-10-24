"""Portable regressions for source-proven electrical prose; no paper/model."""
import unittest
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker
from test_translation_guard import captured_rich_script_fixture


def area_input():
    ti, para, fonts, _, _ = captured_rich_script_fixture(unit=True)
    promoted, _ = g._promote_rich_scripts(ti, para, fonts)
    return type(promoted)('The surface of the simple apparatus without supply lines is at least 1.5 mm{v1}',
                          [promoted.placeholders[0]], promoted.base_style)


class ElectricalSourceTermsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        g.install()

    def test_solid_curves_hint_line_style_and_reject_known_wrong_meaning(self):
        for kind, wrong in [('inductive','实感感性'), ('capacitive','实性电容')]:
            ti = source('The characteristic curve must not cross one of the solid '+kind+' limit curves.')
            before = ti.unicode
            self.assertIn('solid-line '+kind, g._source_review_prompt_view(ti,ti.unicode))
            self.assertIn('实线', g._source_review_hint(ti))
            g._check_review_roles(ti,'特性曲线不得穿过任一条实线表示的极限曲线。')
            with self.assertRaisesRegex(g.PlaceholderIntegrityError,'electrical-source-solid-line'):
                g._check_review_roles(ti,'特性曲线不得穿过'+wrong+'极限曲线。')
            self.assertEqual(before,ti.unicode)
        ti=source('The solid capacitive material changes the characteristic curve.')
        self.assertFalse(g._electrical_source_terms(ti))

    def test_area_needs_genuine_native_squared_unit_and_keeps_original_glyph(self):
        ti=area_input(); ph=ti.placeholders[0];before=(ti.unicode,id(ph.formula),g._script_glyph_state(ph.formula.pdf_character[0]))
        self.assertIn('surface area',g._source_review_prompt_view(ti,ti.unicode))
        g._check_review_roles(ti,'无供电线路的简单设备的表面积至少为1.5 mm{v1}。')
        with self.assertRaisesRegex(g.PlaceholderIntegrityError,'electrical-source-surface-area'):
            g._check_review_roles(ti,'无供电线路的简单设备的表面距离至少为1.5 mm{v1}。')
        self.assertEqual(before,(ti.unicode,id(ph.formula),g._script_glyph_state(ph.formula.pdf_character[0])))
        fake=source(ti.unicode)
        self.assertFalse(g._electrical_source_terms(fake))
        for text in [ti.unicode.replace('1.5','2'),ti.unicode.replace('mm','m'),ti.unicode.replace('surface','length')]:
            self.assertFalse(g._electrical_source_terms(type(ti)(text,ti.placeholders,ti.base_style)))

    def test_area_excludes_generic_supply_lines_without_inventing_a_medium(self):
        ti=area_input();ph=ti.placeholders[0]
        before=(ti.unicode,id(ph.formula),g._script_glyph_state(ph.formula.pdf_character[0]))
        view=g._source_review_prompt_view(ti,ti.unicode)
        self.assertEqual('The surface area of the simple apparatus (excluding supply lines) is at least 1.5 mm{v1}',view)
        hint=g._electrical_source_hint(ti)
        self.assertIn('generic supply lines (供给线路)',hint)
        self.assertIn('excludes those lines from the stated apparatus area',hint)
        self.assertNotIn('electrical supply',view)
        good='简单设备的表面积（不计供给线路）至少为1.5 mm{v1}。'
        g._check_review_roles(ti,good)
        self.assertEqual(before,(ti.unicode,id(ph.formula),g._script_glyph_state(ph.formula.pdf_character[0])))

    def test_area_rewrite_preserves_a_separate_explicit_gas_statement(self):
        ti=area_input();ti.unicode+=' A separate gas supply line is shown.'
        view=g._source_review_prompt_view(ti,ti.unicode)
        self.assertTrue(view.endswith(' A separate gas supply line is shown.'))
        g._check_review_roles(ti,'简单设备的表面积（不计供给线路）至少为1.5 mm{v1}。图中还显示了一条独立的供气管。')
        self.assertIn('separately explicit source statement',g._electrical_source_hint(ti))
        unbound=source('The surface of the simple apparatus without supply lines is at least 1.5 mm2. A separate gas supply line is shown.')
        self.assertFalse(g._electrical_source_terms(unbound))
        self.assertFalse(g._electrical_source_hint(unbound))

    def test_transmission_has_input_direction_without_asserting_shutdown(self):
        ti=source('During transmission of a participant, no power is fed into the circuit. The limits apply.')
        self.assertIn('no power is injected into the circuit',g._source_review_prompt_view(ti,ti.unicode))
        good='在参与者传输期间，没有功率馈入电路。相应限值适用。'
        g._check_review_roles(ti,good)
        engine=actual_prompt_translator([good]);para=paragraph(ti)
        engine.post_translate_paragraph(para,ParagraphTranslateTracker(),ti,'在参与者传输期间，电路不供电。相应限值适用。')
        self.assertEqual(good,para.unicode);self.assertEqual(1,len(engine.calls))
        self.assertFalse(g._electrical_source_terms(source('During shutdown of a participant, no power is fed into the circuit.')))

    def test_nonincendive_is_not_nonsparking_and_preserves_acronym(self):
        for name in ['Fieldbus Non-Incendive Concept','Fieldbus non-incendive concept']:
            ti=source(name+' (FNICO) defines requirements.')
            self.assertIn('非引燃',g._source_review_hint(ti))
            g._check_review_roles(ti,'现场总线非引燃概念（FNICO）定义了要求。')
            with self.assertRaisesRegex(g.PlaceholderIntegrityError,'electrical-source-nonincendive'):
                g._check_review_roles(ti,'现场总线非火花概念（FNICO）定义了要求。')
        self.assertFalse(g._electrical_source_terms(source('The non-sparking concept is different.')))

    def test_quotes_code_and_nonprose_are_not_term_evidence(self):
        text='Fieldbus Non-Incendive Concept (FNICO)'
        for wrapper in ['`{}`','"{}"',"'{}'",'“{}”','‘{}’','<code>{}</code>']:
            self.assertFalse(g._electrical_source_terms(source(wrapper.format(text))))
        for label in ['code','formula','references']:
            ti=source(text);ti._pagebridge_layout_label=label
            self.assertFalse(g._electrical_source_terms(ti))

    def test_other_explicit_source_concepts_do_not_trigger_whole_paragraph_false_positives(self):
        ti=source('During transmission of a participant, no power is fed into the circuit. After transmission, the circuit is switched off.')
        g._check_review_roles(ti,'在参与者传输期间，没有功率馈入电路。传输结束后，电路断电。')
        ti=source('The Fieldbus Non-Incendive Concept (FNICO) differs from the non-sparking concept.')
        g._check_review_roles(ti,'现场总线非引燃概念（FNICO）不同于无火花概念。')
        ti=area_input();ti.unicode+=' The surface distance is measured separately.'
        g._check_review_roles(ti,'表面积至少为1.5 mm{v1}。表面距离单独测量。')

    def test_explaining_a_mistranslation_is_allowed_and_new_quotes_do_not_hide_it(self):
        ti=source('Fieldbus Non-Incendive Concept (FNICO) defines requirements.')
        g._check_review_roles(ti,'现场总线非引燃概念（FNICO），不是“非火花概念”，定义了要求。')
        with self.assertRaises(g.PlaceholderIntegrityError):
            g._check_review_roles(ti,'现场总线“非火花概念”（FNICO）定义了要求。')


if __name__ == '__main__':unittest.main()
