"""Source-only column URL restoration; no fabricated links or model."""
from pathlib import Path
from copy import deepcopy
from types import SimpleNamespace as NS
import pickle,re,sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import layout_guard as g
from test_source_short_heads import body,engine,chars
from babeldoc.format.pdf.document_il import Page


def fixture():
    left=body('The complete algorithm and its observations are explained in the preceding source. The reference implementation of the software is available at https://example.org/',50,150,'left')
    right=body('projects/tool/files/. The source code is distributed according to the open software license.',315,720,'right')
    top=body('This source body paragraph provides the context for the observations and the reported method.',50,720,'top')
    bottom=body('A separate source paragraph follows the original implementation and supplies additional explanations.',315,100,'bottom')
    return Page(pdf_paragraph=[top,left,right,bottom]),left,right


class SourceSplitURLTests(unittest.TestCase):
    def test_complete_uri_keeps_native_glyph_order_state_boxes_and_next_sentence(self):
        page,left,right=fixture();original=[c for p in page.pdf_paragraph for c in chars(p)];ids=list(map(id,original));state=pickle.dumps(original);boxes=deepcopy((left.box,right.box))
        self.assertEqual(g.rejoin_source_column_url_paths(engine(),page),1)
        self.assertTrue(left.unicode.endswith('https://example.org/projects/tool/files/.'))
        self.assertTrue(right.unicode.startswith('The source code'))
        # Actual upstream one-run input must not insert a column-jump space
        # inside this source-proven continuous URI.
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        il=object.__new__(ILTranslator)
        for name in ['_formula_placeholder_pattern','_style_left_placeholder_pattern','_style_right_placeholder_pattern']:
            setattr(il,name,re.compile(r'(?!)'))
        native=il.get_translate_input(left)
        self.assertEqual(native.unicode,left.unicode)
        self.assertIn('https://example.org/projects/tool/files/',native.unicode)
        now=[c for p in page.pdf_paragraph for c in chars(p)]
        self.assertEqual(list(map(id,now)),ids);self.assertEqual(pickle.dumps(now),state);self.assertEqual((left.box,right.box),boxes)
        self.assertTrue(g.source_sentence_was_rejoined(left));self.assertTrue(g.source_sentence_was_rejoined(right))
        self.assertEqual(g.rejoin_source_column_url_paths(engine(),page),0)
    def test_native_terminal_period_kerning_keeps_its_original_glyph(self):
        page,left,right=fixture();cs=chars(right);dot=next(c for c in cs if c.char_unicode=='.');i=cs.index(dot);previous=cs[i-1];size=right.pdf_style.font_size
        width=dot.box.x2-dot.box.x;dot.box.x=previous.box.x2-.11*size;dot.box.x2=dot.box.x+width
        before=pickle.dumps(dot);self.assertEqual(g.rejoin_source_column_url_paths(engine(),page),1);self.assertEqual(pickle.dumps(dot),before)
        page,left,right=fixture();cs=chars(right);dot=next(c for c in cs if c.char_unicode=='.');i=cs.index(dot);dot.box.x=cs[i-1].box.x2-.5*right.pdf_style.font_size
        before=pickle.dumps(page);self.assertEqual(g.rejoin_source_column_url_paths(engine(),page),0);self.assertEqual(pickle.dumps(page),before)

    def test_missing_boundary_native_identity_or_path_grammar_never_joins(self):
        for bad in ['no_uri','complete_uri','different_line','intervening','formula','missing_glyph','different_style','same_column','no_tail','duplicate','native_mismatch','skip']:
            page,left,right=fixture();kw={}
            if bad=='no_uri':left.unicode=left.unicode.replace('https://','')
            if bad=='complete_uri':left.unicode+='.'
            if bad=='different_line':chars(right)[0].box.y-=15
            if bad=='intervening':page.pdf_paragraph.insert(2,body('An unrelated paragraph explains an independent result in this source document.',315,680,'other'))
            if bad=='formula':chars(right)[0].formula_layout_id=1
            if bad=='missing_glyph':chars(right)[0].pdf_character_id=None
            if bad=='different_style':right.pdf_style.font_id='F99'
            if bad=='same_column':right.box.x=left.box.x
            if bad=='no_tail':right.unicode='projects/tool/files/.'
            if bad=='duplicate':page.pdf_character.append(chars(right)[0])
            if bad=='native_mismatch':chars(right)[0].char_unicode='X'
            if bad=='skip':kw['skip_translation']=True
            before=pickle.dumps(page);self.assertEqual(g.rejoin_source_column_url_paths(engine(**kw),page),0,bad);self.assertEqual(pickle.dumps(page),before,bad)


if __name__=='__main__':unittest.main()
