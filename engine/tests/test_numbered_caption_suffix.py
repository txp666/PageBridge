"""Native-caption boundary tests without private PDF fixtures."""
import copy
import pickle
import unittest
from test_layout_guard import guard, line_paragraph, page, ParagraphFinder


def fixture(prefix='Figure 8. Example temperature classe', suffix='s'):
    body = line_paragraph([prefix], label='table_footnote', size=8)
    tail = line_paragraph([suffix], label='fallback_line', size=8)
    chars = tail.pdf_paragraph_composition[0].pdf_line.pdf_character
    end = body.pdf_paragraph_composition[-1].pdf_line.pdf_character[-1].box.x2
    offset = end + .2 - chars[0].box.x
    for char in chars:
        for box in (char.box, char.visual_bbox.box):
            box.x += offset
            box.x2 += offset
    p = page([])
    p.pdf_paragraph = [body, tail]
    return p, body, tail


class NumberedCaptionSuffixTests(unittest.TestCase):
    def test_proven_numbered_caption_suffix_preserves_original_glyphs(self):
        for prefix in ('Figure 8.', 'Fig. 6.', 'Table 4:'):
            with self.subTest(prefix=prefix):
                p, body, tail = fixture(prefix + ' Example temperature classe')
                chars = [c for para in p.pdf_paragraph for co in para.pdf_paragraph_composition for c in co.pdf_line.pdf_character]
                before = pickle.dumps(chars)
                identities = list(map(id, chars))
                finder = object.__new__(ParagraphFinder)
                self.assertEqual(guard.merge_caption_fragments(finder, p), 1)
                self.assertEqual(p.pdf_paragraph, [body])
                self.assertEqual(body.unicode, prefix + ' Example temperature classes')
                after = [c for co in body.pdf_paragraph_composition for c in co.pdf_line.pdf_character]
                self.assertEqual(list(map(id, after)), identities)
                self.assertEqual(pickle.dumps(after), before)
                self.assertEqual(guard.merge_caption_fragments(finder, p), 0)

    def test_footnote_requires_explicit_caption_and_native_adjacency(self):
        for mode in ('unlabelled', 'wrong-prefix', 'space', 'different-baseline', 'different-face',
                     'different-size', 'different-xobject', 'formula', 'duplicate', 'word'):
            with self.subTest(mode=mode):
                p, body, tail = fixture()
                c = tail.pdf_paragraph_composition[0].pdf_line.pdf_character[0]
                if mode == 'unlabelled': body.pdf_paragraph_composition[0].pdf_line.pdf_character[0].char_unicode = 'X'
                elif mode == 'wrong-prefix': body.layout_label = 'text'
                elif mode == 'space': c.box.x += 1.5; c.box.x2 += 1.5
                elif mode == 'different-baseline': c.box.y += 2; c.box.y2 += 2
                elif mode == 'different-face': c.pdf_style.font_id = 'Other'
                elif mode == 'different-size': c.pdf_style.font_size = 6
                elif mode == 'different-xobject': tail.xobj_id = 3
                elif mode == 'formula': c.formula_layout_id = 7
                elif mode == 'duplicate': p.pdf_paragraph.append(copy.deepcopy(tail))
                elif mode == 'word': c.char_unicode = 'other'
                self.assertEqual(guard.merge_caption_fragments(object.__new__(ParagraphFinder), p), 0)

    def test_numbered_footnote_does_not_enable_prefix_merge(self):
        p, body, tail = fixture()
        tail.unicode = 'Figure 7.'
        # No candidate can qualify as a suffix once it contains a label/number.
        tail.pdf_paragraph_composition[0].pdf_line.pdf_character[0].char_unicode = 'Figure 7.'
        self.assertEqual(guard.merge_caption_fragments(object.__new__(ParagraphFinder), p), 0)


if __name__ == '__main__':
    unittest.main()
