"""Synthetic outline lettering with a separate, empty Type 3 search font.

No private PDF or embedded font is needed. The dot, counter and descender also
exercise contours that a generic line-height box cannot describe correctly.
"""
import pymupdf


def make_outline_pdf(path, *, photo=False, artwork=False, extra_font=False):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=300, height=220)
        paper, ink = (1,) * 3, (0,) * 3
        page.draw_rect(page.rect, fill=paper, color=None)
        if photo:
            pixels = bytes(channel for y in range(220) for x in range(300) for channel in (x % 256, y, 110))
            page.insert_image(page.rect, stream=pymupdf.Pixmap(pymupdf.csRGB, 300, 220, pixels, False).tobytes('png'))
        shape = page.new_shape()
        if artwork:
            # This arbitrary rectangle is inside the search passage, but is
            # not the lettering described by the Type 3 glyph metrics.
            shape.draw_rect(pymupdf.Rect(26, 36, 87, 49))
        else:
            for x in (24, 64):
                shape.draw_polyline([(x + 1, 50), (x + 6, 30), (x + 9, 30), (x + 14, 50),
                                     (x + 11, 50), (x + 7.5, 35), (x + 4, 50), (x + 1, 50)])
                shape.draw_oval(pymupdf.Rect(x + 17, 36, x + 27, 48))
                shape.draw_oval(pymupdf.Rect(x + 20, 39, x + 24, 45))
                shape.draw_rect(pymupdf.Rect(x + 24, 42, x + 27, 55))
                shape.draw_rect(pymupdf.Rect(x + 30, 37, x + 33, 50))
                shape.draw_rect(pymupdf.Rect(x + 30, 30, x + 33, 33))
        shape.finish(fill=ink, color=None, even_odd=True, closePath=False)
        shape.commit()
        page.draw_rect(pymupdf.Rect(240, 20, 280, 60), fill=(.2, .7, .3), color=None)
        page.insert_text((24, 190), 'Keep this source footer', fontsize=10, color=ink)

        def stream(data):
            xref = pdf.get_new_xref()
            pdf.update_object(xref, '<<>>')
            pdf.update_stream(xref, data)
            return xref

        # Fixed independent metrics for A, g, i, space at 24 pt. These font
        # programs deliberately draw no ink and always report black text.
        metrics = [b'.6666667 0 .0416667 0 .5833333 .8333333 d1\n',
                   b'.5416667 0 .0416667 -.2083333 .4583333 .5833333 d1\n',
                   b'.25 0 .0416667 0 .1666667 .8333333 d1\n',
                   b'.2083333 0 0 0 0 0 d1\n']
        programs = [stream(value) for value in metrics]
        unicode = stream(b'/CIDInit /ProcSet findresource begin 12 dict begin begincmap\n'
                         b'/CIDSystemInfo << /Registry (Test) /Ordering (Outline) /Supplement 0 >> def\n'
                         b'/CMapName /Outline def /CMapType 2 def\n'
                         b'1 begincodespacerange <00> <FF> endcodespacerange\n'
                         b'4 beginbfchar <00> <0041> <01> <0067> <02> <0069> <03> <0020> endbfchar\n'
                         b'endcmap CMapName currentdict /CMap defineresource pop end end\n')
        font = pdf.get_new_xref()
        pdf.update_object(font, f'''<< /Type /Font /Subtype /Type3 /FontBBox [0 0 0 0]
            /FontMatrix [1 0 0 1 0 0] /FirstChar 0 /LastChar 3
            /Widths [.6666667 .5416667 .25 .2083333]
            /Encoding << /Type /Encoding /Differences [0 /A /g /i /space] >>
            /CharProcs << /A {programs[0]} 0 R /g {programs[1]} 0 R /i {programs[2]} 0 R /space {programs[3]} 0 R >>
            /Resources <<>> /ToUnicode {unicode} 0 R >>''')
        resources = int(pdf.xref_get_key(page.xref, 'Resources')[1].split()[0])
        pdf.xref_set_key(resources, 'Font/FOutline', f'{font} 0 R')
        if extra_font:
            # Reuse the same character codes in another unnamed font. Its
            # native advances differ, as in a PDF that splits fonts at 256 codes.
            other_programs = [stream(b'.75 ' + value.split(b' ', 1)[1]) for value in metrics]
            other = pdf.get_new_xref()
            pdf.update_object(other, pdf.xref_object(font))
            pdf.xref_set_key(other, 'Widths', '[.75 .75 .75 .75]')
            for name, program in zip(('A', 'g', 'i', 'space'), other_programs):
                pdf.xref_set_key(other, 'CharProcs/' + name, f'{program} 0 R')
            pdf.xref_set_key(resources, 'Font/FExtra', f'{other} 0 R')
            page.draw_polyline([(151, 100), (156, 80), (159, 80), (164, 100), (161, 100),
                                (157.5, 85), (154, 100), (151, 100)], fill=ink, color=None)
            extra_contents = stream(b'q 0 g BT /FExtra 24 Tf 1 0 0 1 150 120 Tm <00> Tj ET Q\n')
            pdf.xref_set_key(page.xref, 'Contents', '[' + ' '.join(f'{xref} 0 R' for xref in page.get_contents() + [extra_contents]) + ']')
        contents = stream(b'q 0 g BT /FOutline 24 Tf 1 0 0 1 24 170 Tm <00010203000102> Tj ET Q\n')
        references = ' '.join(f'{xref} 0 R' for xref in page.get_contents() + [contents])
        pdf.xref_set_key(page.xref, 'Contents', '[' + references + ']')
        pdf.save(path)
    return path


def outline_metadata(path):
    with pymupdf.open(path) as pdf:
        page = pdf[0]
        box = pymupdf.Rect(22, 28, 100, 53) * page.rotation_matrix
        return dict(pageNumber=1, width=page.rect.width, height=page.rect.height, blocks=[dict(
            id='p00001-b00001', kind='heading', sourceText='Agi Agi', translatedText='译文',
            status='translated', bbox=list(box), backgroundColor=[255, 255, 255])])
