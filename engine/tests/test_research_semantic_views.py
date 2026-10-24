"""Portable source-attested terminology and three-way relation tests."""
import unittest
from test_guard_review_boundaries import g,ti,formula,translator,paragraph,ParagraphTranslateTracker


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


if __name__=='__main__':unittest.main()
