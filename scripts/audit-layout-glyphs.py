#!/usr/bin/env python3
"""Read-only source/translation glyph audit for legacy bullets and dot leaders."""
import argparse
from collections import Counter
import json
from pathlib import Path
import fitz


def characters(page):
    return [dict(font=span['font'], unicode=char[0], glyph=char[1],
                 color=span['color'], origin=list(char[2]))
            for span in page.get_texttrace() for char in span['chars']]


def match_positions(source, translated, tolerance=0.5):
    """Match visible characters despite font-subset glyph ID renumbering."""
    unused = set(range(len(translated)))
    deltas = []
    for char in source:
        candidates = []
        for index in unused:
            other = translated[index]
            if (char['font'], char['unicode']) != (other['font'], other['unicode']):
                continue
            dx, dy = [abs(a-b) for a,b in zip(char['origin'],other['origin'])]
            if max(dx, dy) <= tolerance:
                candidates.append((dx*dx+dy*dy,index,dx,dy))
        if candidates:
            _, index, dx, dy = min(candidates)
            unused.remove(index)
            deltas.append((dx,dy))
    return dict(matched=len(deltas), sourceUnmatched=len(source)-len(deltas),
                translatedUnmatched=len(unused), tolerancePt=tolerance,
                maxMatchedXDeltaPt=max((d[0] for d in deltas),default=None),
                maxMatchedYDeltaPt=max((d[1] for d in deltas),default=None))


def bullet_key(char):
    color = char['color']
    return (char['font'], char['glyph'], tuple(round(v, 5) for v in color)
            if isinstance(color, (list, tuple)) else round(color, 5))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--original', required=True, type=Path)
    parser.add_argument('--translated', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--visible-glyph-pages', default='',
                        help='Comma-separated 1-based pages expected to preserve all visible source text.')
    args = parser.parse_args()
    source, target = fitz.open(args.original), fitz.open(args.translated)
    assert len(source) == len(target)
    whole_pages = {int(value) for value in args.visible_glyph_pages.split(',') if value.strip()}
    bullets, leaders, preserved_pages = [], [], []
    for number, (s, t) in enumerate(zip(source, target), 1):
        sc, tc = characters(s), characters(t)
        sb = [c for c in sc if 'Webdings' in c['font'] and c['glyph'] == 31]
        tb = [c for c in tc if 'Webdings' in c['font'] and c['glyph'] == 31]
        if sb or tb:
            bullets.append(dict(page=number,sourceCount=len(sb),translatedCount=len(tb),
                countAndColorMatch=Counter(map(bullet_key,sb)) == Counter(map(bullet_key,tb)),
                sourceGlyphs=sb,translatedGlyphs=tb))
        sd = [c for c in sc if c['unicode'] == 46]
        td = [c for c in tc if c['unicode'] == 46]
        # A high repeated-dot count identifies the actual document's TOC/index
        # pages for review, without selecting fixed page numbers.
        if len(sd) >= 100:
            leaders.append(dict(page=number,sourceDots=len(sd),translatedDots=len(td),
                dotPositionComparison=match_positions(sd,td)))
        if number in whole_pages:
            visible = lambda chars: [c for c in chars if c['unicode'] >= 32 and not chr(c['unicode']).isspace()]
            source_visible, translated_visible = visible(sc), visible(tc)
            preserved_pages.append(dict(page=number, sourceVisibleGlyphs=len(source_visible),
                translatedVisibleGlyphs=len(translated_visible),
                visibleGlyphPositionComparison=match_positions(source_visible,translated_visible)))
    report=dict(source=str(args.original.resolve()),translated=str(args.translated.resolve()),
        sourceBullets=sum(x['sourceCount'] for x in bullets),
        translatedBullets=sum(x['translatedCount'] for x in bullets),
        allBulletCountsAndColorsMatch=all(x['countAndColorMatch'] for x in bullets),
        bulletPages=bullets,leaderPages=leaders,preservedVisibleGlyphPages=preserved_pages,
        limitations=['Dots in multi-page-number index entries are outside the conservative single-number leader rule.',
                     'Bullet count/color comparison does not establish list paragraph separation; inspect rendered pages.',
                     'Visible Unicode/font origins are matched within 0.5 PDF point; font-subset glyph IDs can be renumbered.',
                     'Text origins can shift slightly when the same source glyph objects are re-emitted by the PDF writer.'])
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k not in ('bulletPages','limitations')},ensure_ascii=False))


if __name__ == '__main__':
    main()
