"""Portable regressions for source-proven electrical prose; no paper/model."""
import unittest
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker
from test_translation_guard import captured_rich_script_fixture
import re
from test_guard_review_boundaries import ti, formula, translator


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


# Portable source-role regressions; no validation files, PDFs, model or network.
SOURCE = {'V11-P47-T4-classification': 'As T4, a component is most easily assessed in accordance with '
                              'IEC/EN 60079-14, section 16.4',
 'V11-P38-source-attribution': 'Figure 12. Diagram for equipment group IIC, 2 mH. For the source '
                               'compare IEC/EN 60079-25, Annex C, drawing C.7d',
 'V11-P39-source-attribution': 'Figure 13. Diagram for equipment group IIC, 2 mH. The U{v1}and '
                               'I{v2}values are higher in comparison with the given diagram. For '
                               'the source compare IEC/EN 60079-25, Annex C, drawing C.7d',
 'V11-P45-named': 'In addition to the named requirements for Zone 1, the following requirements '
                  'must be complied with for Zone 0/Division 1.',
 'V11-P53-modulation-review': 'Each node receives a basic current of at least 10 mA for modulation '
                              'purposes. This enables the device power supply. The communication '
                              'signals are generated by the sending device through modulation of '
                              '{v1}/- 9 mA on the average current. As each bit always consists of '
                              'a ratio of positive and negative signal, the average of this signal '
                              'is always constant. This average is now used to power the devices. '
                              'Hence the name MBP (Manchester Coded — Bus Powered).',
 'V11-P56-restriction-review': 'A FISCO system can be considered as safe when it is made up of a '
                               'power supply, a number of up to 32 field devices and up to 2 line '
                               'terminators which comply with all requirements of IEC/EN 60079-25 '
                               'and which use a cable in accordance with the above specification.'}
IO='Values that describe the associated apparatus are marked “o” for “out” (U{v1}or I{v2}, for example). The values of the intrinsically safe apparatus are marked “i” for “in” (L{v3}or C{v4}, for example).'
def relation_source(text):
 ids=sorted(set(map(int,re.findall(r'\{v(\d+)\}',text))));return ti(text,[formula(i,'+')for i in ids])
class CandidateTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):g.install()
 def test_io_definition_is_source_bound(self):
  s=relation_source(IO);self.assertTrue(g._source_electrical_io_definitions(s));self.assertIn('输出',g._technical_prose_relation_hint(s))
  g._check_technical_prose_relations(s,'o代表输出，i代表输入。{v1}{v2}{v3}{v4}')
  with self.assertRaises(g.PlaceholderIntegrityError):g._check_technical_prose_relations(s,'o代表外部，i代表内部。{v1}{v2}{v3}{v4}')
  for text in ['The word out means outside.','The values are marked o.','Use '+IO.replace('marked','not marked')]:
   self.assertFalse(g._source_electrical_io_definitions(relation_source(text)))
  for text in [f'`{IO}`',f'"{IO}"',f'<code>{IO}</code>',f'If {IO}']:
   self.assertFalse(g._source_electrical_io_definitions(relation_source(text)))
 def test_source_roles_complete_ti_do_not_change_tokens(self):
  for id in ['V11-P47-T4-classification','V11-P38-source-attribution','V11-P39-source-attribution','V11-P45-named','V11-P53-modulation-review','V11-P56-restriction-review']:
   s=relation_source(SOURCE[id]);s._pagebridge_layout_label='figure_caption'if'source-attribution'in id else'plain text'
   view=g._source_review_prompt_view(s,s.unicode)
   self.assertNotEqual(view,s.unicode,id);self.assertEqual(re.findall(r'\{v\d+\}',view),re.findall(r'\{v\d+\}',s.unicode))
   self.assertEqual(s.unicode,SOURCE[id])
 def test_io_letter_direction_binding_cannot_be_swapped_or_hidden_by_quotes(self):
  s=relation_source(IO)
  for quote in ['', '“', '"', "'", '`']:
   close='”' if quote=='“' else quote
   bad=f'{quote}o{close}代表输入，{quote}i{close}代表输出。{{v1}}{{v2}}{{v3}}{{v4}}'
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'electrical-input-output-definition-lost'):
    g._check_technical_prose_relations(s,bad)
  for good in ['“o”代表输出，“i”代表输入。', 'o并非代表输入，而是输出；i代表输入。',
               '描述相关设备的值标记为"o"，代表“输出”；本安设备的值标记为"i"，代表“输入”。']:
   g._check_technical_prose_relations(s,good+'{v1}{v2}{v3}{v4}')
 def test_quote_code_and_reference_regions(self):
  text=SOURCE['V11-P47-T4-classification']
  for wrap in [f'`{text}`',f'"{text}"',f"'{text}'",f'<code>{text}</code>']:
   self.assertEqual(g._source_technical_prose_relations(relation_source(wrap)),[])
  for label in ['code','formula','reference','bibliography']:
   s=relation_source(text);s._pagebridge_layout_label=label;self.assertEqual(g._source_technical_prose_relations(s),[])
 def test_unrelated_source_words_not_classified(self):
  for text in ['A T4 device resembles another component.','The source powers the diagram apparatus.',
               'Use the named requirements in the API.','The average current is +/- 9 mA.',
               SOURCE['V11-P56-restriction-review'].replace('can be considered as safe','cannot be considered as safe')]:
   self.assertEqual(g._source_technical_prose_relations(relation_source(text)),[])
 def test_source_amplitude_needs_native_plus(self):
  text=SOURCE['V11-P53-modulation-review'];s=ti(text,[formula(1,'-')]);self.assertEqual(g._source_technical_prose_relations(s),[])
  s=relation_source(text);self.assertTrue(g._source_technical_prose_relations(s))
  with self.assertRaisesRegex(g.PlaceholderIntegrityError,'modulation-amplitude'):
   g._check_technical_prose_relations(s,'通过调制{v1}/-9mA的平均电流生成。')
  g._check_technical_prose_relations(s,'在平均电流上施加{v1}/-9mA幅度的调制。')
 def test_native_unit_exponent_cannot_be_repeated(self):
  s=area_input();f=s.placeholders[0].formula
  g._check_unit_script_bindings(s,'表面积至少为1.5mm{v1}。')
  for bad in ['至少为1.5mm{v1}平方','至少为1.5mm{v1}平方。','至少为1.5mm{v1}立方。']:
   with self.assertRaisesRegex(g.PlaceholderIntegrityError,'unit-exponent-repeated'):g._check_unit_script_bindings(s,bad)
  g._check_unit_script_bindings(s,'测量1.5mm{v1}的平方根。')
  s.unicode+=' Find the square root.';g._check_unit_script_bindings(s,'计算1.5mm{v1}平方。')
  self.assertIs(s.placeholders[0].formula,f)
 def test_retry_complete_temperature_caption(self):
  s=relation_source(SOURCE['V11-P47-T4-classification']);s._pagebridge_layout_label='table_caption';p=paragraph(s)
  good='组件归为T4时，最易依据IEC/EN 60079-14第16.4节进行评估。';engine=translator([good])
  g._guard_post(engine,p,ParagraphTranslateTracker(),s,'与T4类似，组件最易依据IEC/EN 60079-14第16.4节进行评估。')
  self.assertEqual(p.unicode,good);self.assertEqual(len(engine.calls),1);self.assertIn('classification into that class',engine.calls[0][0])


if __name__ == '__main__':unittest.main()
