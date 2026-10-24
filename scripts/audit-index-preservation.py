#!/usr/bin/env python3
"""Audit actual parser index preservation, without translation/network calls."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))


def character_state(char):
    style = char.pdf_style
    return dict(unicode=char.char_unicode, glyph=char.pdf_character_id,
                box=asdict(char.box) if char.box else None,
                visualBox=asdict(char.visual_bbox.box) if char.visual_bbox else None,
                fontId=style.font_id if style else None,
                fontSize=style.font_size if style else None,
                graphicStateIdentity=id(style.graphic_state) if style else None,
                xobject=char.xobj_id, renderOrder=char.render_order,
                subRenderOrder=char.sub_render_order, scale=char.scale,
                vertical=char.vertical, advance=char.advance)


def digest(states):
    return hashlib.sha256(json.dumps(states, sort_keys=True).encode()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pages', required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'validation'):
        parser.error('Output must be under this repository validation directory.')
    import layout_guard
    original = layout_guard.preserve_alphabetical_index
    records = []

    def audit(page):
        paragraphs = list(page.pdf_paragraph)
        glyphs = [c for p in paragraphs for comp in p.pdf_paragraph_composition
                  if comp.pdf_line is not None for c in comp.pdf_line.pdf_character]
        before_existing = list(page.pdf_character)
        before_states = [character_state(c) for c in glyphs]
        existing_states = [character_state(c) for c in before_existing]
        headings = []
        for paragraph in paragraphs:
            if paragraph.layout_label == 'title' and re.fullmatch(r'[A-Z]', paragraph.unicode.strip()):
                chars = [c for comp in paragraph.pdf_paragraph_composition
                         if comp.pdf_line for c in comp.pdf_line.pdf_character]
                headings.append(dict(text=paragraph.unicode, glyphs=[character_state(c) for c in chars]))
        matched = original(page)
        after_states = [character_state(c) for c in glyphs]
        same_existing = (len(page.pdf_character) >= len(before_existing)
                         and all(a is b for a,b in zip(before_existing,page.pdf_character))
                         and existing_states == [character_state(c) for c in page.pdf_character[:len(before_existing)]])
        moved = page.pdf_character[len(before_existing):]
        preserved = bool(matched and len(moved) == len(glyphs)
                         and all(a is b for a,b in zip(glyphs,moved))
                         and before_states == after_states and not page.pdf_paragraph)
        unchanged = bool(not matched and len(page.pdf_character) == len(before_existing)
                         and len(page.pdf_paragraph) == len(paragraphs)
                         and all(a is b for a,b in zip(paragraphs,page.pdf_paragraph))
                         and before_states == after_states)
        records.append(dict(page=page.page_number+1,matchedIndex=matched,
            originalParagraphCount=len(paragraphs),originalParagraphGlyphCount=len(glyphs),
            originalPageCharacterCount=len(before_existing),afterPageCharacterCount=len(page.pdf_character),
            afterParagraphCount=len(page.pdf_paragraph),existingCharactersUnchanged=same_existing,
            allGlyphObjectsAndPropertiesPreserved=preserved if matched else None,
            nonmatchingPageUnchanged=unchanged if not matched else None,
            sourceGlyphStateSHA256=digest(before_states),afterGlyphStateSHA256=digest(after_states),
            alphabeticTitleGlyphs=headings))
        return matched

    spec = importlib.util.spec_from_file_location('inspect_pdf_translation_input',
             ROOT / 'scripts/inspect-pdf-translation-input.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    capture_path = output.with_suffix('.capture.json')
    argv = [str(spec.origin), '--input', str(args.input), '--output',str(capture_path),
            '--pages',args.pages,'--match','.','--index-only']
    with patch.object(layout_guard,'preserve_alphabetical_index',audit), patch.object(sys,'argv',argv):
        module.main()
    capture = json.loads(capture_path.read_text())
    report = dict(source=capture['source'],sourceSHA256=capture['sourceSHA256'],pages=args.pages,
        networkDisabled=capture['networkDisabled'],translationRequests=capture['translationRequests'],
        layoutGuardSHA256=hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest(),
        indexPages=[r['page'] for r in records if r['matchedIndex']],
        auditPassed=all(r['existingCharactersUnchanged'] and
            (r['allGlyphObjectsAndPropertiesPreserved'] if r['matchedIndex'] else r['nonmatchingPageUnchanged'])
            for r in records),pageAudits=records,layoutGuardSnapshot=layout_guard.snapshot(),
        limitations=['In-memory glyph identity and properties are verified before PDF writing; a rendered candidate must verify final output.'])
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('indexPages','auditPassed','translationRequests')},ensure_ascii=False))


if __name__ == '__main__':
    main()
