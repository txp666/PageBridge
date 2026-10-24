"""Portable complete-grammar tests for a compound prose section reference."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from test_source_inference_order import g, source, actual_prompt_translator, paragraph, ParagraphTranslateTracker


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
                prompt = g._guard_batch_prompt_context(SimpleNamespace(translation_config=SimpleNamespace(lang_out='zh')),
                    json.dumps([{'id':'section','input':text}, {'id':'code-name','input':other.unicode}]))
            self.assertIn('only for input item "section"', prompt)
            self.assertNotIn('only for input item "code-name"', prompt)
            self.assertIn('Data Classification by KNN', prompt)
            self.assertIn(other.unicode, prompt)
        finally:
            g._review_batch.reset(token)


if __name__ == '__main__':
    unittest.main()
