"""Source-proven prose spacing without PDF fixtures or network requests."""
from copy import deepcopy
from pathlib import Path
import sys, unittest
from unittest.mock import patch
from types import SimpleNamespace as NS
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import translation_guard as g
from test_guard_review_boundaries import ti, formula, paragraph, translator, ParagraphTranslateTracker
from test_layout_guard import line_paragraph


def source(text, formulas=(), label='plain_text'):
    s=ti(text,formulas);s._pagebridge_layout_label=label;return s


class ProseWordBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):g.install()
    def test_full_grammar_recovers_count_but_retains_identifier_contract(self):
        for text,good in [('Values vary between 1 and 4for Example networks.','数值在1到4之间变化，用于Example网络。'),
                          ('Table 2. Mean scores over 10runs obtained using ABC.','表2。使用ABC得到的10次运行平均分数。'),
                          ('Figure 17summarizes the recorded results.','图17总结所记录的结果。')]:
            s=source(text);before=s.unicode;view=g._source_review_prompt_view(s,s.unicode)
            self.assertNotEqual(view,text);self.assertEqual(s.unicode,before)
            self.assertEqual(g._source_review_prompt_view(s,view),view)
            self.assertEqual(g._prose_scientific_identifiers(s,text),g._prose_scientific_identifiers(s,good))
            self.assertEqual(g._prose_numeric_literals(s,text),g._prose_numeric_literals(s,good))
        for text in ['The model 10runs is recorded.','The token 4for has a value.','Figure17summarizes is a variable.',
                     'Values vary between 1 and 4for Example networks. The identifier 4for is also used.']:
            s=source(text);self.assertEqual(g._source_review_prompt_view(s,text),text)
    def test_quotes_urls_code_formulas_and_references_cannot_establish_prose(self):
        body='Table 2. Mean scores over 10runs obtained using ABC.'
        for text,label in [(f'`{body}`','plain_text'),(f'"{body}"','plain_text'),(f"'{body}'",'plain_text'),
                           ('https://example.org/10runs','plain_text'),(body,'reference'),(body,'code')]:
            s=source(text,label=label);self.assertEqual(g._source_prose_boundary_repairs(s),[])
        atom=formula(1,body);s=source('The formula is {v1}.',[atom]);self.assertEqual(g._source_prose_boundary_repairs(s),[])
    def test_native_range_tokens_are_only_separated_not_decoded(self):
        atoms=[formula(1,'[1'),formula(2,'4]')];s=source('Vary the exponent in the range {v1},{v2}for Example networks.',atoms)
        states=deepcopy([p.formula for p in atoms]);view=g._source_review_prompt_view(s,s.unicode)
        self.assertIn('{v1},{v2} for',view);self.assertEqual([p.formula for p in atoms],states)
        self.assertEqual(g._source_prose_boundary_repairs(source(s.unicode)),[])
        self.assertEqual(g._source_prose_boundary_repairs(source(s.unicode+' {v2}',atoms)),[])
    def test_following_whole_native_name_is_opaque_unique_and_attested(self):
        text='Values vary between 1 and 4for {v1} networks.'
        s=source(text,[formula(1,'An opaque name')]);view=g._source_review_prompt_view(s,text)
        self.assertIn('4 for {v1}',view);self.assertEqual(s.unicode,text)
        for bad in [source(text),source(text+' {v1}',[formula(1,'An opaque name')])]:
            self.assertEqual(g._source_review_prompt_view(bad,bad.unicode),bad.unicode)

    def test_residuals_retry_and_numbers_cannot_disappear_duplicate_or_change(self):
        s=source('Table 2. Mean scores over 10runs obtained using ABC.',label='table_caption');p=paragraph(s);p.layout_label='table_caption'
        good='表2。使用ABC得到的10次运行平均分数。';engine=translator([good])
        g._guard_post(engine,p,ParagraphTranslateTracker(),s,'表2。使用ABC得到的10runs平均分数。')
        self.assertEqual(p.unicode,good);self.assertEqual(len(engine.calls),1)
        self.assertIn('Source-attested spaces',engine.calls[0][0])
        for bad in ['表2。使用ABC得到的运行平均分数。','表2。使用ABC得到的11次运行平均分数。','表2。使用ABC得到的10次10次运行平均分数。']:
            self.assertNotEqual(g._prose_numeric_literals(s,s.unicode),g._prose_numeric_literals(s,bad))
    def test_straightforward_modifier_is_source_bound_not_a_product_name(self):
        s=source('This is not a straightfor- ward task because data contain noise.')
        self.assertIn('a straightforward task',g._source_review_prompt_view(s,s.unicode))
        self.assertIn('straightforward',g._ordinary_english_residuals(s,'这不是一项straightforward任务。'))
        self.assertEqual(g._ordinary_english_residuals(s,'这不是一项简单的任务。'),[])
        for target in ['这不是一项"straightforward"任务。','这不是一项`straightforward`任务。']:
            self.assertIn('straightforward',g._ordinary_english_residuals(s,target))
        for text in ['The tool Straightforward is used.','Keep `a straightfor- ward task` as the code.']:
            x=source(text);self.assertEqual(g._source_prose_boundary_repairs(x),[])
        x=source('This is a straightforward task. The literal "straightforward" is the label.')
        self.assertEqual(g._prose_boundary_residuals(x,'这是一项简单任务，标签为"straightforward"。'),[])
    def test_complete_native_data_heading_is_prepared_and_fixed_without_model(self):
        p=line_paragraph(['Data'],label='title');s=source('Data',label='title')
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


if __name__=='__main__':unittest.main()
