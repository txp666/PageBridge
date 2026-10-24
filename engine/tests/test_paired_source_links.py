"""Portable source-link veto at the Styles-stage two-box join boundary."""
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import patch
import pickle,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from test_paired_source_flow import g,engine,pair,chars
import pdf_links
import pymupdf

class PairedSourceLinkTests(unittest.TestCase):
    def setUp(self):
        for target,name in [(pdf_links,'_pages'),(pdf_links,'_mapped'),(g,'_paired_source_paragraphs'),(g,'_rejoined_source_paragraphs')]:
            context=patch.dict(getattr(target,name),{},clear=True);context.start();self.addCleanup(context.stop)
    def capture(self,page,paragraph=None,whole=True):
        matrix=pymupdf.Matrix(1,0,0,-1,0,800);links=[]
        if paragraph is not None:
            b=paragraph.box
            if whole:rect=pymupdf.Rect(b.x,b.y,b.x2,b.y2)*matrix
            else:
                selected=chars(paragraph)[:3]
                rect=pymupdf.Rect(min(c.box.x for c in selected),min(c.box.y for c in selected),max(c.box.x2 for c in selected),max(c.box.y2 for c in selected))*matrix
            links=[dict(kind=2,uri='https://example.invalid/source-only',**{'from':rect})]
        pdf_links._pages[page.page_number]=dict(matrix=matrix,links=links,source={})
        pdf_links.capture_source(page)
        return matrix
    def test_whole_and_partial_links_on_either_source_preserve_independent_paragraphs(self):
        for side in ('left','right'):
            for whole in (True,False):
                with self.subTest(side=side,whole=whole):
                    doc,page,left,right,heading=pair();linked=left if side=='left' else right;other=right if side=='left' else left
                    matrix=self.capture(page,linked,whole);associations=pdf_links._pages[0]['source'][id(linked)][1]
                    self.assertEqual(associations[0][2],whole);self.assertTrue(pdf_links.has_source_links(page,linked));self.assertFalse(pdf_links.has_source_links(page,other))
                    before=pickle.dumps(doc);self.assertEqual(g.join_source_column_sentence_pairs(engine(),page),0);self.assertEqual(pickle.dumps(doc),before)
                    pdf_links.capture_target(page);mapped=pdf_links._mapped[0][0]
                    b=other.box;other_rect=pymupdf.Rect(b.x,b.y,b.x2,b.y2)*matrix
                    self.assertTrue(mapped);self.assertFalse(any(pymupdf.Rect(r).intersects(other_rect)for r in mapped))
    def test_captured_no_link_sources_still_pair(self):
        doc,page,left,right,heading=pair();self.capture(page)
        self.assertFalse(pdf_links.has_source_links(page,left));self.assertFalse(pdf_links.has_source_links(page,right));self.assertEqual(g.join_source_column_sentence_pairs(engine(),page),1)
    def test_existing_repair_cannot_hide_linked_original_glyph_ownership(self):
        doc,page,left,right,heading=pair();self.capture(page,right,False)
        self.assertFalse(pdf_links.has_source_links(page,left))
        left.pdf_paragraph_composition.extend(right.pdf_paragraph_composition);right.pdf_paragraph_composition=[]
        self.assertTrue(pdf_links.has_source_links(page,left))
    def test_production_paragraph_hook_captures_before_styles_join(self):
        doc,page,left,right,heading=pair();self.capture(page,left,True)
        info=pdf_links._pages[0];info['source']={};events=[];original_capture=pdf_links.capture_source
        def capture(argument):events.append('capture');return original_capture(argument)
        def original_styles(*args):
            self.assertTrue(pdf_links.has_source_links(page,left));events.append('styles')
        paragraph_helpers=['normalize_page_markers','preserve_alphabetical_index','preserve_copyright_blocks','merge_caption_closers','merge_caption_fragments','merge_source_terminal_punctuation','merge_source_year_range_fragments','preserve_trailing_equations','preserve_toc_leaders','split_structured_entries']
        style_helpers=['split_source_physical_lines','bind_source_subscript_indices','repair_subscript_offsets','repair_script_following_formula_offsets','register_script_bindings','rejoin_source_column_sentence_tails','rejoin_source_column_orphan_heads','merge_source_bullet_prepositions','merge_source_open_grammar_blocks','merge_source_numbered_rule_titles','rejoin_source_parenthetical_comparisons','rejoin_source_column_short_heads']
        before=pickle.dumps(doc)
        with ExitStack()as stack:
            for name in paragraph_helpers+style_helpers:stack.enter_context(patch.object(g,name,return_value=0))
            stack.enter_context(patch.object(g,'_original_process_page',return_value=None));stack.enter_context(patch.object(g,'_original_styles_process_page',side_effect=original_styles));stack.enter_context(patch.object(pdf_links,'capture_source',side_effect=capture))
            g._guard_process_page(engine(),page);g._guard_styles_process_page(engine(),page)
        self.assertEqual(events,['capture','styles']);self.assertEqual(pickle.dumps(doc),before)
if __name__=='__main__':unittest.main()
