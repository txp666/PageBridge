import sys
from copy import deepcopy
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pdf_links as links
import pymupdf
from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfStyle


MATRIX = pymupdf.Matrix(1, 0, 0, 1, 0, 0)


def chars(text, x=10, y=10, size=10, font='F1'):
    return [PdfCharacter(char_unicode=c, box=Box(x+i*5, y, x+(i+1)*5, y+size),
                         pdf_style=PdfStyle(font_id=font, font_size=size),
                         pdf_character_id=ord(c)) for i,c in enumerate(text)]


class LinkContextTests(unittest.TestCase):
    def test_callout_does_not_require_unlinked_outer_parenthesis(self):
        source = chars('(see Fig. 1)'); target = chars('（见图1）', x=100)
        selected = {i for i in range(5, 11) if source[i].char_unicode.strip()}
        self.assertEqual(links.match_localized_callout(source, target, selected, MATRIX),
                         [[110., 10., 120., 20.]])
        self.assertEqual(links.match_localized_callout(source, target, {10}, MATRIX), [])
        self.assertEqual(links.match_localized_callout(source, target, set(range(5,9)), MATRIX), [])

    def test_callout_still_rejects_ambiguous_repeated_actions(self):
        source = chars('(Fig. 1) and (Fig. 1)'); target = chars('（图1）和（图1）')
        self.assertEqual(links.match_localized_callout(source, target, set(range(1,7)), MATRIX), [])

    def test_native_script_is_distinct_from_year_digit(self):
        for digit in ('1', '2', '4'):
            source = chars('source 2024 body ')+chars(digit, size=7.5, font='Native')
            target = chars('译文2024正文', x=100)+chars(digit, x=180, y=13, size=6, font='Native')
            if digit in '24':
                self.assertEqual(links.match_literal(source,target,{len(source)-1},MATRIX), [])
            self.assertEqual(links.match_linked_native_script(source,target,{len(source)-1},MATRIX),
                             [[180.,13.,185.,19.]])

    def test_script_needs_same_native_font_glyph_and_small_role(self):
        source = chars('source 2024 body ')+chars('2', size=7.5, font='Native')
        target = chars('译文2024正文', x=100)+chars('2', x=180, y=13, size=6, font='Native')
        for change in ('font', 'gid', 'size', 'duplicate', 'missing'):
            changed = deepcopy(target)
            if change=='font': changed[-1].pdf_style.font_id='Different'
            if change=='gid': changed[-1].pdf_character_id=999
            if change=='size': changed[-1].pdf_style.font_size=10
            if change=='duplicate': changed.append(deepcopy(changed[-1]))
            if change=='missing': changed.pop()
            with self.subTest(change=change):
                self.assertEqual(links.match_linked_native_script(source,changed,{len(source)-1},MATRIX), [])

    def test_two_source_scripts_and_body_digit_remain_ambiguous(self):
        source = chars('source 2024 body ')+chars('2', size=7.5, font='Native')
        target = chars('译文正文', x=100)+chars('2', x=180, y=13, size=6, font='Native')
        source.append(deepcopy(source[-1]))
        self.assertEqual(links.match_linked_native_script(source,target,{len(source)-1},MATRIX), [])

        source=chars('source 2024 body 2',font='Native')
        self.assertEqual(links.match_linked_native_script(source,target,{len(source)-1},MATRIX), [])

    def test_repeated_year_maps_by_complete_citation_not_position(self):
        source=chars('(Smith, 2024; Jones, 2024)');target=chars('（Jones，2024；Smith，2024）',x=100)
        selected=set(range(8,12))
        mapped=links.match_citation_fragment(source,target,selected,selected,MATRIX)
        self.assertEqual(mapped,[[190.,10.,210.,20.]])
        self.assertEqual(links.match_literal(source,target,selected,MATRIX),[])

    def test_identical_complete_citations_require_same_action_for_all(self):
        s='(Smith, 2024; Smith, 2024)';source=chars(s);target=chars('（Smith，2024；Smith，2024）')
        selected=set(range(8,12))
        # Derive the second year positions from the source itself.
        begin=s.rindex('2024');other=set(range(begin,begin+4))
        self.assertEqual(links.match_citation_fragment(source,target,selected,selected,MATRIX),[])
        self.assertEqual(len(links.match_citation_fragment(source,target,selected,selected|other,MATRIX)),2)

    def test_citation_author_piece_stays_inside_its_own_entry(self):
        s='(Smith et al., 2024; Jones et al., 2024)'
        t='（Jones et al.，2024；Smith et al.，2024）'
        source=chars(s);target=chars(t,x=100)
        selected=set(range(1,13))
        rects=links.match_citation_fragment(source,target,selected,selected,MATRIX)
        self.assertTrue(rects)
        for x0,y0,x1,y1 in rects:
            self.assertGreaterEqual(x0,100+t.index('Smith')*5)
            self.assertLessEqual(x1,100+(t.index('Smith')+len('Smith et al.'))*5)
        self.assertEqual(links.match_citation_fragment(source,target,{8,11},{8,11},MATRIX),[])

    def test_citation_changes_or_missing_group_do_not_get_context_link(self):
        source=chars('(Smith, 2024; Jones, 2024)');selected=set(range(8,12))
        for t in ('(Smith, 2023; Jones, 2024)','Smith 2024, Jones 2024',
                  '(Smith, 2024; Smith, 2024; Jones, 2024)'):
            self.assertEqual(links.match_citation_fragment(source,chars(t),selected,selected,MATRIX),[])
        source=chars('2024 and 2024')
        self.assertEqual(links.match_citation_fragment(source,chars('2024和2024'),set(range(4)),set(range(4)),MATRIX),[])


if __name__ == '__main__':
    unittest.main()
