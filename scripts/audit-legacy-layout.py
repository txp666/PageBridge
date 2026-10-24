#!/usr/bin/env python3
"""Inspect actual legacy-font formula coverage and metadata moves; no LLM/network."""
import argparse
from collections import Counter
from dataclasses import asdict, fields
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))
import layout_guard


def character_state(c):
    s = c.pdf_style
    return dict(unicode=c.char_unicode, glyph=c.pdf_character_id,
                box=asdict(c.box) if c.box else None,
                visualBox=asdict(c.visual_bbox.box) if c.visual_bbox else None,
                fontId=s.font_id if s else None, fontSize=s.font_size if s else None,
                graphicStateIdentity=id(s.graphic_state) if s else None,
                xobject=c.xobj_id, renderOrder=c.render_order, subRenderOrder=c.sub_render_order,
                scale=c.scale, vertical=c.vertical, advance=c.advance)


def composition_chars(paragraph):
    result = []
    for comp in paragraph.pdf_paragraph_composition:
        for field in fields(comp):
            value = getattr(comp, field.name)
            if value is not None:
                result.extend([value] if field.name == 'pdf_character'
                              else getattr(value, 'pdf_character', []))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pages', required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / 'validation'):
        parser.error('Output must be inside validation.')
    capture = output.with_suffix('.capture.json')
    source_hash = hashlib.sha256(args.input.read_bytes()).hexdigest()
    layout_hash = hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest()
    totals, font_counts, moves, failures = Counter(), Counter(), [], []
    original_preserve = layout_guard.preserve_copyright_blocks

    def preserve(page):
        before = {id(c): (c, character_state(c)) for p in page.pdf_paragraph for c in composition_chars(p)}
        lane = {id(c) for c in page.pdf_character}
        count = original_preserve(page)
        if count:
            moved = [c for c in page.pdf_character if id(c) not in lane]
            identical = all(id(c) in before and c is before[id(c)][0]
                            and character_state(c) == before[id(c)][1] for c in moved)
            remaining = {id(c) for p in page.pdf_paragraph for c in composition_chars(p)}
            unique = len({id(c) for c in moved}) == len(moved) and not (remaining & {id(c) for c in moved})
            moves.append(dict(page=page.page_number+1, blocks=count, glyphs=len(moved),
                              exactOriginalGlyphIdentityAndProperties=identical, uniqueOwnership=unique,
                              text=''.join(c.char_unicode for c in moved)))
            if not identical or not unique:
                failures.append(dict(page=page.page_number+1, reason='copyright-glyph-change'))
        return count
    layout_guard.preserve_copyright_blocks = preserve
    original_install = layout_guard.install

    def install():
        original_install()
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        original_styles_page = StylesAndFormulas.process_page
        def styles_page(self, page):
            fonts = {f.font_id:f for f in page.pdf_font}
            xfonts = {o.xobj_id:{**fonts,**{f.font_id:f for f in o.pdf_font}} for o in page.pdf_xobject}
            before = {}
            for p in page.pdf_paragraph:
                lookup = xfonts.get(p.xobj_id,fonts)
                for c in composition_chars(p):
                    if (c.pdf_style and c.char_unicode and c.char_unicode.strip()
                            and re.sub(r'^[A-Z]{6}\+', '', lookup[c.pdf_style.font_id].name)
                            in layout_guard._LEGACY_MATH_FONTS):
                        before[id(c)] = character_state(c)
            result = original_styles_page(self,page)
            after = {id(c):character_state(c) for p in page.pdf_paragraph for c in composition_chars(p)}
            totals['auditedPreStyleMathGlyphs'] += len(before)
            bad = [state for key,state in before.items() if after.get(key) != state]
            if bad:
                failures.append(dict(page=page.page_number+1,reason='source-math-glyph-changed-in-style-stage',bad=bad))
            return result
        StylesAndFormulas.process_page = styles_page
        original_pre = ILTranslator.pre_translate_paragraph

        def pre(self, paragraph, tracker, fonts, xobj_fonts):
            chars = composition_chars(paragraph)
            font_map = xobj_fonts.get(paragraph.xobj_id, fonts)
            legacy = [c for c in chars if c.pdf_style and c.char_unicode.strip()
                      and c.pdf_character_id is not None
                      and re.sub(r'^[A-Z]{6}\+', '', font_map[c.pdf_style.font_id].name)
                      in layout_guard._LEGACY_SYMBOL_FONTS]
            snapshot = {id(c): character_state(c) for c in legacy}
            text, ti = original_pre(self, paragraph, tracker, fonts, xobj_fonts)
            totals['paragraphs'] += 1
            if ti is None:
                totals['upstreamSkipped'] += 1
                return text, ti
            totals['preparedTI'] += 1
            formula = [c for ph in ti.placeholders if hasattr(ph, 'formula') for c in ph.formula.pdf_character]
            formula_ids = {id(c) for c in formula}
            bad = [c for c in legacy if id(c) not in formula_ids or character_state(c) != snapshot[id(c)]]
            totals['auditedLegacyGlyphs'] += len(legacy)
            for c in legacy:
                font_counts[re.sub(r'^[A-Z]{6}\+', '', font_map[c.pdf_style.font_id].name)] += 1
            if bad:
                failures.append(dict(reason='legacy-glyph-not-original-formula',
                    sourceParagraphSHA256=hashlib.sha256((paragraph.unicode or '').encode()).hexdigest(),
                    text=paragraph.unicode, bad=[character_state(c) for c in bad]))
            if re.search(r'[A-Za-z]{3,}', re.sub(r'\{[^{}]*\}', '', text or '')):
                totals['TIWithTranslatableWords'] += 1
            return text, ti
        ILTranslator.pre_translate_paragraph = pre
    layout_guard.install = install
    spec = importlib.util.spec_from_file_location('legacy_source_capture', ROOT/'scripts/inspect-pdf-translation-input.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.argv = [str(spec.origin), '--input', str(args.input), '--output', str(capture),
                '--pages', args.pages, '--match', '.']
    module.main()
    report = dict(source=str(args.input.resolve()), sourceSHA256=source_hash,
        sourceUnchanged=source_hash==hashlib.sha256(args.input.read_bytes()).hexdigest(),
        layoutSHA256=layout_hash, layoutUnchanged=layout_hash==hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest(),
        networkDisabled=True, translationRequests=0, totals=dict(totals),
        fontGlyphCounts=dict(font_counts), copyrightMoves=moves, failures=failures,
        passed=not failures, layoutSnapshot=layout_guard.snapshot(), capture=str(capture))
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('passed','totals','layoutUnchanged','copyrightMoves')},ensure_ascii=False))


if __name__ == '__main__':
    main()
