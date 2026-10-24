from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import pymupdf
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import pdf_links as links
from babeldoc.format.pdf.document_il import Box, PdfCharacter, PdfParagraph, PdfParagraphComposition


def chars(text,x=10,y=10):
    return [PdfCharacter(char_unicode=c,box=Box(x+i*5,y,x+(i+1)*5,y+10)) for i,c in enumerate(text)]


class LinkTests(unittest.TestCase):
    def test_partial_url_follows_translated_paragraph(self):
        source=chars('Read https://a.test now')
        target=chars('阅读 https://a.test 现在',x=100,y=80)
        result=links.match_literal(source,target,set(range(5,19)),pymupdf.Matrix(1,0,0,1,0,0))
        self.assertEqual(result,[[115.,80.,185.,90.]])

    def test_repeated_literals_are_not_bound_by_order_or_adjacent_punctuation(self):
        matrix = pymupdf.Matrix(1,0,0,1,0,0)
        for source, target in [
            ('Smith 2024; Jones 2024', 'Jones 2024; Smith 2024'),
            ('Smith 2024, Jones 2024', 'Jones 2024, Smith 2024'),
            ('Smith 2024 and Jones 2024', 'Jones 2024 and Smith 2024'),
            ('2024; 2024', '2024; 2024'),
            ('Smith2024 a Jones2024', 'Jones2024和Smith2024'),
        ]:
            with self.subTest(source=source):
                offset = source.index('2024')
                self.assertEqual(links.match_literal(chars(source), chars(target),
                    set(range(offset,offset+4)),matrix), [])

    def test_changed_partial_label_is_not_guessed(self):
        self.assertEqual(links.match_literal(chars('Figure 1 text'),chars('图1正文'),set(range(6)),pymupdf.Matrix(1,0,0,1,0,0)),[])

    def test_multiline_literal_has_distinct_click_regions(self):
        source=chars('https://a.test/path')
        target=chars('https://a.')+chars('test/path',x=10,y=30)
        result=links.match_literal(source,target,set(range(len(source))),pymupdf.Matrix(1,0,0,1,0,0))
        self.assertEqual(len(result),2)
        self.assertEqual(result[0][1],10)
        self.assertEqual(result[1][1],30)

    def test_empty_and_noncontiguous_selection_rejected(self):
        source=chars('abcdef')
        self.assertEqual(links.match_literal(source,source,set(),pymupdf.Matrix(1,0,0,1,0,0)),[])
        self.assertEqual(links.match_literal(source,source,{0,2},pymupdf.Matrix(1,0,0,1,0,0)),[])

    def test_action_unsupported_and_destination_range(self):
        self.assertIsNone(links._action({'kind':pymupdf.LINK_URI,'uri':'javascript:alert(1)'},2,lambda p:p))
        self.assertIsNone(links._action({'kind':pymupdf.LINK_GOTO,'page':3},2,lambda p:p))
        named=links._action({'kind':pymupdf.LINK_NAMED,'page':1},2,lambda p:p*2+1)
        self.assertEqual(named['page'],3)
        self.assertEqual(named['to'],pymupdf.Point(0,0))

    def test_annotation_rebuild_changes_no_visible_pixels_and_maps_dual_pages(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);src=root/'source.pdf';mono=root/'mono.pdf';dual=root/'dual.pdf'
            doc=pymupdf.open()
            for i in range(2):
                page=doc.new_page(width=300,height=300)
                page.insert_text((20,30),'link alpha',fontsize=12)
            page=doc[0]
            rect=page.search_for('link alpha')[0]
            page.insert_link({'kind':pymupdf.LINK_URI,'from':rect,'uri':'https://a.test'})
            page.insert_link({'kind':pymupdf.LINK_GOTO,'from':pymupdf.Rect(20,50,80,65),'page':1,'to':pymupdf.Point(0,0)})
            doc.save(src);doc.close()
            doc=pymupdf.open()
            for i in range(2):
                page=doc.new_page(width=300,height=300)
                page.insert_text((120,130),'new target',fontsize=12)
            doc.save(mono);doc.close()
            source=pymupdf.open(src);translated=pymupdf.open(mono);both=pymupdf.open()
            for i in range(2):
                both.insert_pdf(source,from_page=i,to_page=i,links=False)
                both.insert_pdf(translated,from_page=i,to_page=i,links=False)
            both.save(dual);both.close()
            source.close();translated.close()
            before=pymupdf.open(mono);pixels=[p.get_pixmap().samples for p in before];before.close()
            mapping=root/'mapping.json'
            mapping.write_text(json.dumps({'pages':{'0':{'0':[[120,115,180,132]],'1':[[120,140,180,155]]}}}))
            report=links.restore_links(src,mono,dual,mapping)
            self.assertEqual(report['translated']['restoredSourceAnnotations'],2)
            self.assertEqual(report['translated']['paragraphMapped'],2)
            after=pymupdf.open(mono)
            self.assertEqual(pixels,[p.get_pixmap().samples for p in after])
            target_links=after[0].get_links()
            self.assertEqual([l['page'] for l in target_links if l['kind']==pymupdf.LINK_GOTO],[1])
            self.assertEqual(target_links[0]['from'],pymupdf.Rect(120,115,180,132))
            after.close()
            both=pymupdf.open(dual)
            self.assertEqual([l['page'] for l in both[1].get_links() if l['kind']==pymupdf.LINK_GOTO],[3])
            self.assertEqual([l['uri'] for l in both[0].get_links() if l['kind']==pymupdf.LINK_URI],['https://a.test'])
            both.close()

    def test_unchanged_raster_requires_visible_ink_not_white_space(self):
        a=pymupdf.open();b=pymupdf.open()
        pa=a.new_page(width=300,height=300);pb=b.new_page(width=300,height=300)
        self.assertFalse(links._same_visible_region(pa,pb,pymupdf.Rect(0,0,50,50)))
        for p in [pa,pb]:p.insert_text((20,30),'test')
        self.assertTrue(links._same_visible_region(pa,pb,pymupdf.Rect(10,10,100,50)))
        pb.insert_text((20,30),'changed')
        self.assertFalse(links._same_visible_region(pa,pb,pymupdf.Rect(10,10,100,50)))
        a.close();b.close()

    def test_full_paragraph_capture_uses_target_geometry(self):
        source=chars('Read this text');p=PdfParagraph(pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in source])
        page=SimpleNamespace(page_number=0,pdf_paragraph=[p])
        old=links._pages.copy()
        try:
            links._pages[0]={'matrix':pymupdf.Matrix(1,0,0,1,0,0),'links':[{'kind':pymupdf.LINK_URI,'uri':'https://a.test','from':pymupdf.Rect(9,9,81,21)}],'source':{}}
            links.capture_source(page)
            p.pdf_paragraph_composition=[PdfParagraphComposition(pdf_character=c) for c in chars('已翻译',x=90,y=100)]
            links.capture_target(page)
            self.assertEqual(links._mapped[0][0],[[90.,100.,105.,110.]])
        finally:links._pages.clear();links._pages.update(old)

    def test_preserved_standalone_glyphs_need_original_identity_and_state(self):
        from copy import deepcopy
        for mutation in ('none', 'replace', 'text', 'move', 'remove'):
            with self.subTest(mutation=mutation):
                original = chars('Creative Commons')
                page = SimpleNamespace(page_number=0, pdf_paragraph=[], pdf_character=original[:])
                old = links._pages.copy()
                try:
                    links._pages[0] = {'matrix': pymupdf.Matrix(1,0,0,1,0,0),
                        'links': [{'kind': pymupdf.LINK_URI, 'uri': 'https://a.test',
                                   'from': pymupdf.Rect(9,9,91,21)}], 'source': {}}
                    links.capture_source(page)
                    if mutation == 'replace': page.pdf_character = deepcopy(original)
                    if mutation == 'text': original[0].char_unicode = 'X'
                    if mutation == 'move': original[0].box.x += 1
                    if mutation == 'remove': page.pdf_character.pop()
                    links.capture_target(page)
                    self.assertEqual(bool(links._mapped[0]), mutation == 'none')
                    if mutation == 'none': self.assertEqual(links._mapped[0][0], [[10.,10.,90.,20.]])
                finally:
                    links._pages.clear(); links._pages.update(old)

    def test_localized_figure_and_equation_hotspot_excludes_surrounding_prose(self):
        source=chars('See Fig. 3 for details')
        target=chars('详见图3了解详情',x=100)
        result=links.match_localized_callout(source,target,set(range(4,10)),pymupdf.Matrix(1,0,0,1,0,0))
        self.assertEqual(result,[[110.,10.,120.,20.]])
        self.assertEqual(links.match_localized_callout(source,chars('详见图4'),set(range(4,10)),pymupdf.Matrix(1,0,0,1,0,0)),[])
        source=chars('See Eq. (12)')
        self.assertEqual(links.match_localized_callout(source,chars('见式（12）'),set(range(4,12)),pymupdf.Matrix(1,0,0,1,0,0)),[[15.,10.,40.,20.]])

    def test_repeated_localized_labels_need_every_source_occurrence_same_action(self):
        source=chars('Fig. 3 and Fig. 3');target=chars('图3和图3')
        self.assertEqual(links.match_localized_callout(source,target,set(range(6)),pymupdf.Matrix(1,0,0,1,0,0)),[])
        result=links.match_localized_callout(source,target,set(range(6))|set(range(11,17)),pymupdf.Matrix(1,0,0,1,0,0))
        self.assertTrue(result)

    def test_rotation_and_cropbox_roundtrip_preserves_click_rectangle(self):
        for rotation in [0,90,180,270]:
            for cropped in [False,True]:
                with self.subTest(rotation=rotation,cropped=cropped),tempfile.TemporaryDirectory() as temp:
                    root=Path(temp);src=root/'s.pdf';mono=root/'m.pdf';dual=root/'d.pdf'
                    d=pymupdf.open();p=d.new_page(width=360,height=500)
                    if cropped:p.set_cropbox(pymupdf.Rect(20,40,340,480))
                    p.insert_text((50,70),'click target')
                    p.insert_link({'kind':pymupdf.LINK_URI,'from':p.search_for('click target')[0],'uri':'https://a.test'})
                    p.set_rotation(rotation);d.save(src);d.close()
                    d=pymupdf.open(src);expected=d[0].get_links()[0]['from'];d.save(mono)
                    both=pymupdf.open();both.insert_pdf(d,links=False);both.insert_pdf(d,links=False);both.save(dual);both.close();d.close()
                    report=links.restore_links(src,mono,dual,root/'absent.json')
                    self.assertEqual(report['translated']['restoredSourceAnnotations'],1)
                    for path,index in [(mono,0),(dual,0),(dual,1)]:
                        out=pymupdf.open(path);actual=out[index].get_links()[0]['from']
                        for a,b in zip(expected,actual):self.assertAlmostEqual(a,b,places=3)
                        out.close()

if __name__=='__main__':unittest.main()
