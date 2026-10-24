"""Preserve verified legacy list markers and unambiguous TOC leader glyphs.

Some PDFs map Webdings CID 31 (a filled square) to U+001F. BabelDOC then
strips it as whitespace before looking for list boundaries. Normalize only
that exact font/CID/control-character combination; retain its source glyph,
font, color, geometry and render order. The semantic bullet is recognized as
both a list boundary and a protected formula object by BabelDOC 0.6.2.
"""
from __future__ import annotations

from collections import Counter
import copy
import math
import re
import threading

GUARD_VERSION = 15
_lock = threading.RLock()
_installed = False
_original_process_page = None
_original_is_formulas_font = None
_original_process_translatable_formulas = None
_original_is_translatable_formula = None
_original_process_comma_formulas = None
_original_should_split_formula = None
_original_styles_process_page = None
_original_layout_typesetting_units = None
# Formula objects cannot be weak-referenced. Keep the exact source identity for
# this single-document worker lifetime; never infer a binding from target text.
_script_bindings = {}
_hanging_indents = {}
_rejoined_source_paragraphs = {}
_formula_context = threading.local()
_counters = Counter()
_TOC_TAIL = re.compile(r"\.{12,}[ \t]*([0-9]{1,4})[ \t]*$")
# Legacy mathematical encodings have misleading ToUnicode mappings (a minus
# can decode as '{'). Preserve their original glyphs, not guessed Unicode.
# Exact, audited font names only: similar AdvP body/italic fonts are excluded.
_LEGACY_MATH_FONTS = frozenset({
    'AdvP40798', 'AdvP497E2', 'AdvP497E3', 'AdvP497E4', 'AdvP497E5',
    'AdvP43B38B', 'AdvP4C4E51', 'AdvP4C4E46', 'AdvP3EAA99', 'AdvP4068C',
    'AdvPi1', 'AdvPi2', 'AdvP7DA6', 'AdvP4980C',
    'AdvP7DB7', 'AdvP697C', 'AdvP43A417', 'AdvP4383EF', 'AdvP44E6F4', 'AdvP7CA8',
})
# These two fonts encode real decimal digits. Upstream may translate standalone
# ordinary numbers; encoded mathematical symbols that happen to look numeric
# in ToUnicode must not take that path (AdvP7DA6 '2' is a minus glyph).
_LEGACY_SYMBOL_FONTS = _LEGACY_MATH_FONTS - {'AdvP497E2', 'AdvP497E4'}


def _composition_characters(composition):
    if composition.pdf_character is not None:
        return [composition.pdf_character]
    for name in ('pdf_line', 'pdf_same_style_characters', 'pdf_formula'):
        value = getattr(composition, name, None)
        if value is not None:
            return value.pdf_character
    return []


def _finite_character(character):
    return bool(character.pdf_style and character.box and character.visual_bbox
                and character.visual_bbox.box and not character.vertical
                and isinstance(character.pdf_style.font_size, (int, float))
                and math.isfinite(character.pdf_style.font_size)
                and character.pdf_style.font_size > 0
                and all(isinstance(v, (int, float)) and math.isfinite(v)
                        for box in (character.box, character.visual_bbox.box)
                        for v in (box.x, box.y, box.x2, box.y2)))


def repair_subscript_offsets(page):
    """Keep a source-proven isolated variable's small subscript below its base.

    Upstream's 0.6 vertical-IoU test can miss a genuinely lowered corner glyph,
    especially when a body-sized space expands its visual formula box. Relocate
    uses that box as its origin, so zero offset raises the glyph above new text.
    Derive the offset from the original base baseline; never shift source glyphs
    or use a fixed font descent. Compound/ambiguous formulas and superscripts
    remain under upstream control.
    """
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    repaired = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        def family(character):
            font = lookup.get(character.pdf_style.font_id) if character.pdf_style else None
            name = getattr(font, 'name', None)
            return re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) and name else None
        entries = [(c, comp.pdf_formula is not None)
                   for comp in paragraph.pdf_paragraph_composition
                   for c in _composition_characters(comp)]
        for composition in paragraph.pdf_paragraph_composition:
            formula = composition.pdf_formula
            if (formula is None or not formula.box or formula.pdf_curve or formula.pdf_form
                    or formula.is_corner_mark is False
                    or any(not _finite_character(c) or not isinstance(c.char_unicode, str)
                           or not c.char_unicode for c in formula.pdf_character)
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (formula.box.x, formula.box.y, formula.box.x2, formula.box.y2))):
                continue
            visible = [c for c in formula.pdf_character if c.char_unicode and c.char_unicode.strip()]
            if len(visible) != 1 or not re.fullmatch(r'[A-Za-z0-9]', visible[0].char_unicode):
                continue
            script = visible[0]
            formula_character_ids = {id(c) for c in formula.pdf_character}
            index = next((i for i, (c, _) in enumerate(entries) if c is script), None)
            if index is None:
                continue
            preceding = [(c, is_formula) for c, is_formula in entries[:index]
                         if id(c) not in formula_character_ids]
            if not preceding:
                continue
            base, base_is_formula = preceding[-1]
            if (base_is_formula or not _finite_character(base)
                    or not re.fullmatch(r'[A-Za-z]', base.char_unicode or '')
                    or len(preceding) > 1 and re.search(r'[A-Za-z0-9_]$', preceding[-2][0].char_unicode or '')):
                continue
            size = base.pdf_style.font_size
            source_family = family(base)
            if (not source_family or family(script) != source_family
                    or any(c.xobj_id != base.xobj_id for c in formula.pdf_character)
                    or base.xobj_id != paragraph.xobj_id
                    or not 0.35 * size <= script.pdf_style.font_size < 0.85 * size
                    or not -0.65 * size <= script.box.y - base.box.y <= -0.12 * size
                    or not -0.15 * size <= script.box.x - base.box.x2 <= 0.6 * size):
                continue
            candidates = [c for position, (c, is_formula) in enumerate(entries)
                          if not is_formula and _finite_character(c)
                          and re.fullmatch(r'[A-Za-z]', c.char_unicode or '')
                          and (position == 0 or not re.search(
                              r'[A-Za-z0-9_]$', entries[position - 1][0].char_unicode or ''))
                          and c.xobj_id == base.xobj_id and family(c) == source_family
                          and abs(c.pdf_style.font_size - size) <= 0.02
                          and -0.15 * size <= script.box.x - c.box.x2 <= 0.6 * size
                          and -0.65 * size <= script.box.y - c.box.y <= -0.12 * size]
            if len(candidates) != 1 or candidates[0] is not base:
                continue
            offset = formula.box.y - base.box.y
            if formula.y_offset is not None and abs(formula.y_offset - offset) <= 0.05:
                continue
            formula.y_offset = offset
            repaired += 1
    if repaired:
        with _lock:
            _counters['repairedSubscriptOffsets'] += repaired
    return repaired


def _guard_styles_process_page(self, page):
    split_source_physical_lines(self, page)
    result = _original_styles_process_page(self, page)
    repair_subscript_offsets(page)
    repair_script_following_formula_offsets(page)
    register_script_bindings(page)
    rejoin_source_column_sentence_tails(self, page)
    return result


def source_sentence_was_rejoined(paragraph):
    """Identity marker for the translation guard's no-silent-skip check."""
    return _rejoined_source_paragraphs.get(id(paragraph)) is paragraph


def _source_body_box(paragraph):
    box, style = paragraph.box, paragraph.pdf_style
    if (paragraph.vertical or paragraph.layout_label not in {'text', 'plain text', 'plain_text'}
            or not box or not style or not isinstance(paragraph.unicode, str)
            or len(re.findall(r'[A-Za-z]{2,}', paragraph.unicode)) < 5
            or re.match(r'\s*(?:[•▪■●–—]|\d+[.)]\s|\(?[A-Za-z][.)]\s)', paragraph.unicode)):
        return None
    values = box.x, box.y, box.x2, box.y2, style.font_size
    if (any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in values)
            or style.font_size <= 0 or box.x2 - box.x < 8 * style.font_size or box.y2 <= box.y):
        return None
    return values


def _source_column_sentence_pairs(page):
    """Require adjacent reading order and the bottom/top of two full columns."""
    def overlap(a, b):
        return max(0, min(a[2], b[2]) - max(a[0], b[0])) / min(a[2]-a[0], b[2]-b[0])

    bodies = [(p, _source_body_box(p)) for p in page.pdf_paragraph]
    eligible = [(p, g) for p, g in bodies if g]
    for (left, a), (right, b) in zip(bodies, bodies[1:]):
        if (not a or not b or left.xobj_id != right.xobj_id
                or right.first_line_indent is not False
                or left.pdf_style.font_id != right.pdf_style.font_id
                or abs(a[4] - b[4]) > .02
                or not re.match(r'[a-z][a-z]+\b', right.unicode.lstrip())):
            continue
        size = max(a[4], b[4])
        if (not .5 * size <= b[0]-a[2] <= 8 * size
                or not .85 <= (a[2]-a[0])/(b[2]-b[0]) <= 1.18):
            continue
        lc = [(p, g) for p, g in eligible if overlap(a, g) >= .8]
        rc = [(p, g) for p, g in eligible if overlap(b, g) >= .8]
        if (any(overlap(a, g) >= .2 and overlap(b, g) >= .2 for _, g in eligible)
                or any(g[1] < a[1] - .3*size for p, g in lc if p is not left)
                or any(g[3] > b[3] + .3*size for p, g in rc if p is not right)):
            continue
        lt, lb = max(g[3] for _, g in lc), min(g[1] for _, g in lc)
        rt, rb = max(g[3] for _, g in rc), min(g[1] for _, g in rc)
        if abs(lt-rt) <= 2*size and abs(lb-rb) <= 3*size and min(lt-lb, rt-rb) >= 8*size:
            yield left, right


def rejoin_source_column_sentence_tails(styler, page):
    """Move an unambiguous short unfinished prose sentence into its continuation.

    Only the last same-baseline, ordinary-style source suffix is rehomed. The
    target paragraph boxes/styles and every glyph remain untouched; translated
    Unicode is laid out inside the original right-column box. Keeping the
    original glyph coordinates also preserves an entirely untranslated page.
    No formula/citation/script, arbitrary sentence, or cross-page join qualifies.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition
    from babeldoc.format.pdf.document_il.utils.layout_helper import is_same_style

    config = getattr(styler, 'translation_config', None)
    if config is None or getattr(config, 'skip_translation', False):
        return 0
    minimum = getattr(config, 'min_text_length', 0)
    repaired = 0
    for left, right in _source_column_sentence_pairs(page):
        match = re.search(r'[.!?]\s+([A-Z][a-z]+(?:[ ,]+[a-z]+){1,7})\s*$', left.unicode)
        if not match or not left.debug_id or not right.debug_id:
            continue
        tail = match[1]
        retained = left.unicode[:match.start(1)].rstrip()
        if (len(tail) > 64 or tail.split()[-1] not in {'of', 'for', 'to', 'with', 'in', 'on', 'at', 'from', 'by', 'and', 'or'}
                or len(retained) < minimum or len(right.unicode) < minimum
                or len(re.findall(r'[A-Za-z]{2,}', retained)) < 5
                or re.search(r'\b[A-Za-z]{1,3}\.$', retained)
                or any(retained.count(a) != retained.count(b) for a,b in ('()', '[]', '{}'))
                or not left.pdf_paragraph_composition or not right.pdf_paragraph_composition):
            continue
        # The last plain-style composition must contain the source sentence
        # boundary. Never split a formula or reconstruct an ambiguous glyph run.
        comp = left.pdf_paragraph_composition[-1]
        run = comp.pdf_same_style_characters
        first = right.pdf_paragraph_composition[0].pdf_same_style_characters
        if not run or not first or not first.pdf_character:
            continue
        raw = ''.join(c.char_unicode or '' for c in run.pdf_character)
        suffix = re.search(r'([.!?])\s*(' + re.escape(tail).replace(r'\ ', r'\s*') + r')\s*$', raw)
        all_chars = [c for part in left.pdf_paragraph_composition for c in _composition_characters(part)]
        if (not suffix or ''.join(left.unicode.split()) != ''.join(''.join(c.char_unicode or '' for c in all_chars).split())
                or not is_same_style(left.pdf_style, right.pdf_style)
                or not is_same_style(run.pdf_style, right.pdf_style)
                or not is_same_style(first.pdf_style, right.pdf_style)):
            continue
        # The cut must coincide with an actual glyph boundary (no ligature cut).
        cut, offset = None, 0
        for index, char in enumerate(run.pdf_character):
            if offset == suffix.start(2):
                cut = index
                break
            offset += len(char.char_unicode or '')
        if cut is None or cut == 0:
            continue
        moved = run.pdf_character[cut:]
        prior = run.pdf_character[cut-1]
        next_char = first.pdf_character[0]
        size = right.pdf_style.font_size
        baseline = moved[0].box.y if moved[0].box else None
        if (baseline is None or not _finite_character(prior) or not _finite_character(next_char)
                or next_char.xobj_id != right.xobj_id or next_char.formula_layout_id is not None
                or not is_same_style(next_char.pdf_style, right.pdf_style)
                or any(not _finite_character(c) or c.formula_layout_id is not None
                or not re.fullmatch(r'[A-Za-z, ]+', c.char_unicode or '')
                or not is_same_style(c.pdf_style, right.pdf_style)
                or (c.xobj_id != left.xobj_id and not (c.xobj_id is None and c.pdf_character_id is None and c.char_unicode == ' '))
                or abs(c.box.y-baseline) > .1*size for c in moved)
                or abs(prior.box.y-baseline) > .1*size
                or any(not -.1*size <= b.box.x-a.box.x2 <= 1.2*size for a,b in zip(moved, moved[1:]))
                or abs(moved[-1].box.x2-left.box.x2) > size
                or abs(next_char.box.x-right.box.x) > .5*size
                or abs(next_char.box.y2-right.box.y2) > .5*size):
            continue
        kept_run, moved_run = copy.copy(run), copy.copy(run)
        kept_run.pdf_character, moved_run.pdf_character = run.pdf_character[:cut], moved
        # Container boxes describe their own source glyphs; paragraph boxes are
        # deliberately not recomputed, otherwise this would span both columns.
        for value in (kept_run, moved_run):
            value.box = copy.copy(run.box)
            if value.box:
                value.box.x = min(c.box.x for c in value.pdf_character)
                value.box.y = min(c.box.y for c in value.pdf_character)
                value.box.x2 = max(c.box.x2 for c in value.pdf_character)
                value.box.y2 = max(c.box.y2 for c in value.pdf_character)
        left.pdf_paragraph_composition[-1] = PdfParagraphComposition(pdf_same_style_characters=kept_run)
        right.pdf_paragraph_composition.insert(0, PdfParagraphComposition(pdf_same_style_characters=moved_run))
        left.unicode, right.unicode = retained, tail + ' ' + right.unicode.lstrip()
        _rejoined_source_paragraphs[id(left)] = left
        _rejoined_source_paragraphs[id(right)] = right
        repaired += 1
    if repaired:
        with _lock:
            _counters['rejoinedSourceColumnSentenceTails'] += repaired
    return repaired


def split_source_physical_lines(styler, page):
    """Restore source line wraps before assigning formula line IDs.

    A parser PdfLine can contain several complete physical rows. Its one line
    ID then lets a wrapped formula's bounding box absorb unrelated numbers in
    preceding prose. Split only an ordered, same-size body-text wrap with an
    unmistakable leftward reset and a full baseline descent. Original glyphs,
    scripts and their properties are retained; detected multiline equations,
    overprints, fractions and ambiguous writing directions are not touched.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition

    repaired = 0
    for paragraph in page.pdf_paragraph:
        if (paragraph.vertical or paragraph.layout_label not in {'plain text', 'plain_text', 'text'}
                or len(re.findall(r'[A-Za-z]{3,}', paragraph.unicode or '')) < 3):
            continue
        compositions = []
        for comp in paragraph.pdf_paragraph_composition:
            line = comp.pdf_line
            chars = line.pdf_character if line is not None else []
            if (len(chars) < 6 or any(not _finite_character(c) or c.formula_layout_id is not None
                    or (c.xobj_id != paragraph.xobj_id and not (c.xobj_id is None
                        and c.pdf_character_id is None and c.char_unicode == ' '))
                    or not isinstance(c.char_unicode, str)
                    for c in chars)):
                compositions.append(comp)
                continue
            sizes = sorted(c.pdf_style.font_size for c in chars if c.char_unicode.strip())
            if not sizes:
                compositions.append(comp)
                continue
            size = sizes[len(sizes) // 2]
            regular = [c for c in chars if abs(c.pdf_style.font_size - size) <= .02
                       and c.char_unicode.strip()]
            if not regular or len(regular) < len(chars) * .6:
                compositions.append(comp)
                continue
            baseline = regular[0].box.y
            groups, start, valid = [], 0, True
            for index, char in enumerate(chars):
                delta = baseline - char.box.y
                if delta > size * .85:
                    previous = chars[index - 1] if index else None
                    if (previous is None or abs(char.pdf_style.font_size - size) > .02
                            or not size * .85 < delta < size * 1.8
                            or previous.box.x - char.box.x < size * 2
                            or len(chars[start:index]) < 3):
                        valid = False
                        break
                    groups.append(chars[start:index])
                    start, baseline = index, char.box.y
                elif abs(delta) > size * .65:
                    valid = False
                    break
                # Small superscripts/subscripts stay with their physical row.
                if (index > start and char.box.x < chars[index - 1].box.x - .25
                        or char.pdf_style.font_size > size * 1.05):
                    valid = False
                    break
            groups.append(chars[start:])
            if (not valid or len(groups) == 1 or any(len(row) < 3 for row in groups)
                    or any(sum(abs(c.pdf_style.font_size - size) <= .02 for c in row) < len(row) * .6
                           for row in groups)):
                compositions.append(comp)
                continue
            for row in groups:
                physical = copy.copy(line)
                physical.pdf_character = row
                styler.update_line_data(physical)
                compositions.append(PdfParagraphComposition(pdf_line=physical))
            repaired += len(groups) - 1
        paragraph.pdf_paragraph_composition = compositions
    if repaired:
        with _lock:
            _counters['restoredPhysicalLineBreaks'] += repaired
    return repaired


def repair_script_following_formula_offsets(page):
    """Anchor a source-baseline '= number' fragment to the variable, not its o.

    Upstream sometimes measures an adjacent native number formula from a rich
    small script. Once that script is preserved separately, the number is
    visibly raised. Repair only the exact proven wrong anchor; all original
    glyphs and x offsets stay intact, including a separately translated decimal
    tail. This does not adjust compound expressions or genuine superscripts.
    """
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    repaired = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        def family(c):
            f = lookup.get(c.pdf_style.font_id) if c.pdf_style else None
            name = getattr(f, 'name', None)
            return re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) and name else None
        entries = [(c, comp.pdf_formula is not None)
                   for comp in paragraph.pdf_paragraph_composition
                   for c in _composition_characters(comp)]
        for comp in paragraph.pdf_paragraph_composition:
            f = comp.pdf_formula
            if (f is None or not f.box or f.pdf_curve or f.pdf_form or not f.pdf_character
                    or f.pdf_character[0].char_unicode != '='
                    or not re.fullmatch(r'=\s*[+−-]?\d+(?:[.,]\d+)?\s*',
                                        ''.join(c.char_unicode or '' for c in f.pdf_character))
                    or any(not _finite_character(c) for c in f.pdf_character)
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (f.box.x, f.box.y, f.box.x2, f.box.y2, f.x_offset, f.y_offset))):
                continue
            index = next((i for i, (c, _) in enumerate(entries) if c is f.pdf_character[0]), 0)
            if index < 2:
                continue
            base, base_formula = entries[index - 2]
            script, script_formula = entries[index - 1]
            if (base_formula or script_formula or not _finite_character(base) or not _finite_character(script)
                    or not re.fullmatch(r'[A-Za-z]', base.char_unicode or '')
                    or not re.fullmatch(r'[A-Za-z0-9]', script.char_unicode or '')
                    or index > 2 and re.search(r'[A-Za-z0-9_]$', entries[index - 3][0].char_unicode or '')):
                continue
            size = base.pdf_style.font_size
            name = family(base)
            if (not name or family(script) != name or base.xobj_id != paragraph.xobj_id
                    or script.xobj_id != base.xobj_id
                    or not .35 * size <= script.pdf_style.font_size < .85 * size
                    or not -.65 * size <= script.box.y - base.box.y <= -.12 * size
                    or not -.15 * size <= script.box.x - base.box.x2 <= .6 * size
                    or not -.15 * size <= f.pdf_character[0].box.x - script.box.x2 <= .6 * size
                    or any(c.xobj_id != base.xobj_id or family(c) != name
                           or abs(c.pdf_style.font_size - size) > .02
                           or abs(c.box.y - base.box.y) > .05 for c in f.pdf_character)
                    or abs(f.y_offset - (f.box.y - script.box.y)) > .05):
                continue
            candidates = [c for pos, (c, is_formula) in enumerate(entries)
                          if not is_formula and _finite_character(c)
                          and re.fullmatch(r'[A-Za-z]', c.char_unicode or '')
                          and (pos == 0 or not re.search(r'[A-Za-z0-9_]$', entries[pos - 1][0].char_unicode or ''))
                          and c.xobj_id == base.xobj_id and family(c) == name
                          and abs(c.pdf_style.font_size - size) <= .02
                          and -.15 * size <= script.box.x - c.box.x2 <= .6 * size
                          and -.65 * size <= script.box.y - c.box.y <= -.12 * size]
            if len(candidates) != 1 or candidates[0] is not base:
                continue
            f.y_offset = f.box.y - base.box.y
            repaired += 1
    if repaired:
        with _lock:
            _counters['repairedScriptFollowingFormulaOffsets'] += repaired
    return repaired


def register_script_bindings(page):
    """Record only unique source-proven Latin base / leading subscript pairs."""
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    found = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        def family(c):
            f = lookup.get(c.pdf_style.font_id) if c.pdf_style else None
            name = getattr(f, 'name', None)
            return re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) and name else None
        entries = [(c, comp.pdf_formula is not None)
                   for comp in paragraph.pdf_paragraph_composition
                   for c in _composition_characters(comp)]
        for comp in paragraph.pdf_paragraph_composition:
            f = comp.pdf_formula
            if (f is None or not f.box or f.pdf_curve or f.pdf_form
                    or f.is_corner_mark is False or not f.pdf_character
                    or any(not _finite_character(c) for c in f.pdf_character)
                    or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                           for v in (f.box.x, f.box.y, f.box.x2, f.box.y2,
                                     f.x_offset, f.y_offset))):
                continue
            script = f.pdf_character[0]
            if not re.fullmatch(r'[A-Za-z0-9]', script.char_unicode or ''):
                continue
            index = next((i for i, (c, _) in enumerate(entries) if c is script), 0)
            if not index:
                continue
            base, is_formula = entries[index - 1]
            if (is_formula or not _finite_character(base)
                    or not re.fullmatch(r'[A-Za-z]', base.char_unicode or '')
                    or index > 1 and re.search(r'[A-Za-z0-9_]$', entries[index - 2][0].char_unicode or '')):
                continue
            size = base.pdf_style.font_size
            name = family(base)
            if (not name or family(script) != name or base.xobj_id != paragraph.xobj_id
                    or any(c.xobj_id != base.xobj_id for c in f.pdf_character)
                    or not .35 * size <= script.pdf_style.font_size < .85 * size
                    or not -.65 * size <= script.box.y - base.box.y <= -.12 * size
                    or not -.15 * size <= script.box.x - base.box.x2 <= .6 * size
                    or abs(f.y_offset - (f.box.y - base.box.y)) > .05
                    or any(c.box.x < script.box.x - .1 or abs(c.box.y - base.box.y) > .7 * size
                           for c in f.pdf_character[1:])):
                continue
            candidates = [c for pos, (c, is_formula) in enumerate(entries)
                          if not is_formula and _finite_character(c)
                          and re.fullmatch(r'[A-Za-z]', c.char_unicode or '')
                          and (pos == 0 or not re.search(r'[A-Za-z0-9_]$', entries[pos - 1][0].char_unicode or ''))
                          and c.xobj_id == base.xobj_id and family(c) == name
                          and abs(c.pdf_style.font_size - size) <= .02
                          and -.15 * size <= script.box.x - c.box.x2 <= .6 * size
                          and -.65 * size <= script.box.y - c.box.y <= -.12 * size]
            if len(candidates) != 1 or candidates[0] is not base:
                continue
            with _lock:
                if id(f) not in _script_bindings:
                    _script_bindings[id(f)] = (f, base.char_unicode, name, size, base.xobj_id)
                    found += 1
    if found:
        with _lock:
            _counters['registeredScriptBindings'] += found
    return found


class _BoundScriptUnit:
    """Temporary line-layout atom; both children still use upstream relocation."""
    def __init__(self, base, script):
        self.base, self.script = base, script

    def __getattr__(self, name):
        return getattr(self.base, name)

    @property
    def width(self):
        return self.base.width + self.script.width + self.script.formular.x_offset

    @property
    def height(self):
        return max(self.base.height, self.script.height)

    @property
    def box(self):
        from babeldoc.format.pdf.document_il import Box
        b = self.base.box
        return Box(b.x, b.y, b.x + self.width, b.y + self.height)

    @property
    def can_break_line(self):
        return True

    def relocate(self, x, y, scale):
        base = self.base.relocate(x, y, scale)
        script = self.script.relocate(base.box.x2, y, scale)
        return _BoundScriptUnit(base, script)


def _bound_typesetting_units(units):
    result = []
    for unit in units:
        f = unit.formular
        with _lock:
            binding = _script_bindings.get(id(f)) if f is not None else None
        if binding and binding[0] is f and result and not isinstance(result[-1], _BoundScriptUnit):
            base = result[-1]
            previous = result[-2].try_get_unicode() if len(result) > 1 else None
            style = base.char.pdf_style if base.char else getattr(base, 'style', None)
            original_font = getattr(base, 'original_font', None)
            name = getattr(original_font, 'name', None)
            family = re.sub(r'^[A-Z]{6}\+', '', name) if isinstance(name, str) else None
            # Only generated base text is joined. Original passthrough glyphs
            # retain their source positions and do not need line-layout repair.
            if (base.unicode == binding[1] and base.formular is None and style
                    and family == binding[2] and base.xobj_id == binding[4]
                    and abs(base.font_size - binding[3]) <= .02
                    and not re.search(r'[A-Za-z0-9_]$', previous or '')
                    and math.isfinite(base.width) and base.width > 0
                    and math.isfinite(unit.width + f.x_offset) and unit.width + f.x_offset > 0):
                result[-1] = _BoundScriptUnit(base, unit)
                continue
        result.append(unit)
    return result


def _guard_layout_typesetting_units(self, typesetting_units, box, scale, line_skip,
                                   paragraph, use_english_line_break=True):
    grouped = _bound_typesetting_units(typesetting_units)
    indent = _hanging_indents.get(id(paragraph), (None, 0))
    indent = indent[1] * scale if indent[0] is paragraph else 0
    if any(unit.formular and (unit.formular.pdf_curve or unit.formular.pdf_form) for unit in typesetting_units):
        indent = 0  # Do not shift embedded vector/form objects without a transform.
    if indent:
        box = copy.copy(box)
        box.x += indent
    if any(isinstance(unit, _BoundScriptUnit) and unit.width * scale > box.x2 - box.x
           for unit in grouped):
        return [], False
    baselines = {}
    tracked = [_BaselineTrackingUnit(unit, baselines) for unit in grouped] if indent else grouped
    laid_out, fits = _original_layout_typesetting_units(
        self, tracked, box, scale, line_skip, paragraph, use_english_line_break)
    if indent and laid_out:
        # Layout inside the continuation column, then move only the first line
        # left by the source-proven hanging indent. The first line is slightly
        # conservative in width, so it cannot overflow the original box.
        first_y = baselines.get(id(laid_out[0]))
        for index, unit in enumerate(laid_out):
            if first_y is None or baselines.get(id(unit)) != first_y:
                break
            _shift_rendered_unit_x(unit, -indent)
    # Upstream consumers only see standard units; no special render or PDF path.
    return [child for unit in laid_out
            for child in ((unit.base, unit.script) if isinstance(unit, _BoundScriptUnit) else (unit,))], fits


class _BaselineTrackingUnit:
    """Observe upstream's layout baseline before a script offset is applied."""
    def __init__(self, unit, baselines):
        self.unit, self.baselines = unit, baselines

    def __getattr__(self, name):
        return getattr(self.unit, name)

    def relocate(self, x, y, scale):
        result = self.unit.relocate(x, y, scale)
        self.baselines[id(result)] = y
        return result


def _shift_rendered_unit_x(unit, delta):
    # Units have already been relocated. Relocating a formula again would apply
    # its script offset twice. Shift output boxes horizontally only instead.
    if isinstance(unit, _BoundScriptUnit):
        _shift_rendered_unit_x(unit.base, delta)
        _shift_rendered_unit_x(unit.script, delta)
        return
    boxes = []
    if unit.char:
        boxes.append(unit.char.box)
        if unit.char.visual_bbox:
            boxes.append(unit.char.visual_bbox.box)
    elif unit.formular:
        boxes.append(unit.formular.box)
        for char in unit.formular.pdf_character:
            boxes.append(char.box)
            if char.visual_bbox:
                boxes.append(char.visual_bbox.box)
    elif unit.unicode:
        unit.x += delta
    seen = set()
    for box in boxes:
        if box is not None and id(box) not in seen:
            box.x += delta
            box.x2 += delta
            seen.add(id(box))
    unit.box_cache = None


def _guard_is_formulas_font(font_name, formular_font_pattern):
    if _original_is_formulas_font(font_name, formular_font_pattern):
        return True
    name = re.sub(r'^[A-Z]{6}\+', '', font_name) if isinstance(font_name, str) else ''
    if name in _LEGACY_MATH_FONTS:
        with _lock:
            _counters['legacyMathFontMatches'] += 1
        return True
    return False


def _guard_is_translatable_formula(self, formula):
    if id(formula) in getattr(_formula_context, 'protected', ()):
        return False
    return _original_is_translatable_formula(self, formula)


def _guard_process_translatable_formulas(self, page):
    bind_source_formula_fragments(self, page)
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    protected = set()
    for paragraph in page.pdf_paragraph:
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        for comp in paragraph.pdf_paragraph_composition:
            formula = comp.pdf_formula
            if formula is None or not _original_is_translatable_formula(self, formula):
                continue
            if any(c.pdf_style and c.pdf_style.font_id in lookup and
                   re.sub(r'^[A-Z]{6}\+', '', lookup[c.pdf_style.font_id].name)
                   in _LEGACY_SYMBOL_FONTS for c in formula.pdf_character):
                protected.add(id(formula))
    previous = getattr(_formula_context, 'protected', None)
    _formula_context.protected = protected
    try:
        result = _original_process_translatable_formulas(self, page)
        # An ordinary decimal suffix may initially be PdfLine('.') followed
        # by a numeric PdfFormula. Only after numeric demotion is its complete
        # source text available to the narrow native-decimal binding below.
        bind_source_formula_fragments(self, page)
        return result
    finally:
        if previous is None:
            del _formula_context.protected
        else:
            _formula_context.protected = previous
        if protected:
            with _lock:
                _counters['preservedLegacySymbolFormulas'] += len(protected)


def _source_glyph_run(chars, xobj_id):
    """A compact single-baseline run, allowing only parser-created spaces."""
    visible = [c for c in chars if c.char_unicode and c.char_unicode.strip()]
    if not visible or any(not _finite_character(c) for c in chars):
        return False
    anchor = visible[0]
    size = anchor.pdf_style.font_size
    return (all((c.xobj_id == xobj_id or c.xobj_id is None and c.pdf_character_id is None
                 and c.char_unicode == ' ')
                and abs(c.box.y - anchor.box.y) <= .1 * size
                and abs(c.pdf_style.font_size - size) <= .02 for c in chars)
            and all(-.25 <= b.box.x - a.box.x2 <= .35 * size for a, b in zip(chars, chars[1:])))


def bind_source_formula_fragments(styler, page):
    """Keep native '_descriptor = decimal' fragments in their source formula.

    No words are inferred from output: an explicit underscore, a short attached
    letter run and an adjacent same-line formula containing '=' prove the
    descriptor. A decimal continuation requires a native leading digit and
    touching original dot/digit glyphs. Curves, forms, scripts and real word
    spaces reject the repair. All original glyphs and their properties survive.
    """
    from babeldoc.format.pdf.document_il import PdfParagraphComposition
    descriptors = decimals = 0
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical:
            continue
        comps = paragraph.pdf_paragraph_composition
        index = 0
        while index < len(comps):
            left = comps[index].pdf_formula
            if left is None or left.pdf_curve or left.pdf_form:
                index += 1
                continue
            # A descriptor is a complete text island inside a source formula,
            # not a word following a formula or a neighboring prose connector.
            if index + 2 < len(comps):
                middle, right = comps[index + 1].pdf_line, comps[index + 2].pdf_formula
                letters = middle.pdf_character if middle is not None else []
                text = ''.join(c.char_unicode or '' for c in letters)
                if (right is not None and not right.pdf_curve and not right.pdf_form
                        and left.line_id == right.line_id and left.pdf_character
                        and left.pdf_character[-1].char_unicode == '_'
                        and re.fullmatch(r'[A-Za-z]{1,8}', text)
                        and '=' in ''.join(c.char_unicode or '' for c in right.pdf_character)
                        and all(c.pdf_style.font_id == left.pdf_character[-1].pdf_style.font_id
                                for c in letters)
                        and _source_glyph_run(left.pdf_character + letters + right.pdf_character,
                                              paragraph.xobj_id)):
                    merged = copy.copy(left)
                    merged.pdf_character = left.pdf_character + letters + right.pdf_character
                    styler.update_formula_data(merged)
                    comps[index:index + 3] = [PdfParagraphComposition(pdf_formula=merged)]
                    left = merged
                    descriptors += 1
            if index + 1 < len(comps) and comps[index + 1].pdf_line is not None:
                following_lines = []
                for following_comp in comps[index + 1:]:
                    if following_comp.pdf_line is None:
                        break
                    following_lines.append(following_comp.pdf_line)
                chars = [c for line in following_lines for c in line.pdf_character]
                text = ''.join(c.char_unicode or '' for c in chars)
                match = re.match(r'\.\d+(?![A-Za-z0-9_])', text)
                original = ''.join(c.char_unicode or '' for c in left.pdf_character)
                if (match and re.search(r'=\s*[+−-]?\d+$', original)
                        and all(len(c.char_unicode or '') == 1 for c in chars[:match.end()])
                        and len(chars) >= match.end()
                        and _source_glyph_run(left.pdf_character + chars[:match.end()], paragraph.xobj_id)
                        and all(c.pdf_style.font_id == left.pdf_character[-1].pdf_style.font_id
                                for c in chars[:match.end()])):
                    left.pdf_character = left.pdf_character + chars[:match.end()]
                    styler.update_formula_data(left)
                    remaining = match.end()
                    while remaining:
                        following = comps[index + 1].pdf_line
                        consumed = min(remaining, len(following.pdf_character))
                        rest = following.pdf_character[consumed:]
                        if rest:
                            remainder = copy.copy(following)
                            remainder.pdf_character = rest
                            styler.update_line_data(remainder)
                            comps[index + 1] = PdfParagraphComposition(pdf_line=remainder)
                        else:
                            del comps[index + 1]
                        remaining -= consumed
                    decimals += 1
            index += 1
    if descriptors or decimals:
        with _lock:
            _counters['boundSourceFormulaDescriptors'] += descriptors
            _counters['boundSourceFormulaDecimals'] += decimals
    return descriptors + decimals


def _guard_should_split_formula(self, formula):
    if id(formula) in getattr(_formula_context, 'encoded_commas', ()):
        return False
    return _original_should_split_formula(self, formula)


def _guard_process_comma_formulas(self, page):
    fonts = {f.font_id: f for f in page.pdf_font}
    xfonts = {o.xobj_id: {**fonts, **{f.font_id: f for f in o.pdf_font}}
              for o in page.pdf_xobject}
    protected = set()
    for paragraph in page.pdf_paragraph:
        lookup = xfonts.get(paragraph.xobj_id, fonts)
        for comp in paragraph.pdf_paragraph_composition:
            formula = comp.pdf_formula
            if formula is None or not _original_should_split_formula(self, formula):
                continue
            if any(c.char_unicode == ',' and c.pdf_style and c.pdf_style.font_id in lookup and
                   re.sub(r'^[A-Z]{6}\+', '', lookup[c.pdf_style.font_id].name)
                   in _LEGACY_SYMBOL_FONTS for c in formula.pdf_character):
                protected.add(id(formula))
    previous = getattr(_formula_context, 'encoded_commas', None)
    _formula_context.encoded_commas = protected
    try:
        return _original_process_comma_formulas(self, page)
    finally:
        if previous is None:
            del _formula_context.encoded_commas
        else:
            _formula_context.encoded_commas = previous
        if protected:
            with _lock:
                _counters['preservedLegacyCommaFormulas'] += len(protected)


def _is_webdings(font):
    name = getattr(font, "name", None)
    if not isinstance(name, str):
        return False
    # PDF subset prefixes are six ASCII uppercase letters followed by '+'.
    return re.sub(r"^[A-Z]{6}\+", "", name).casefold() == "webdings"


def normalize_page_markers(page):
    """Change interpretation only, never substitute or remove the PDF glyph."""
    page_fonts = {font.font_id: font for font in page.pdf_font}
    xobject_fonts = {
        xobject.xobj_id: {**page_fonts, **{font.font_id: font for font in xobject.pdf_font}}
        for xobject in page.pdf_xobject
    }
    normalized = 0
    for character in page.pdf_character:
        if character.pdf_character_id != 31 or character.char_unicode != "\x1f":
            continue
        style = character.pdf_style
        if style is None:
            continue
        fonts = xobject_fonts.get(character.xobj_id, page_fonts)
        if not _is_webdings(fonts.get(style.font_id)):
            continue
        # U+2022 triggers the upstream formula-preservation path. The visible
        # result remains the original square because pdf_character_id stays 31.
        character.char_unicode = "•"
        normalized += 1
    if normalized:
        with _lock:
            _counters["normalizedWebdingsBullets"] += normalized
            _counters["pagesWithNormalizedBullets"] += 1
    return normalized


def _guard_process_page(self, page):
    normalize_page_markers(page)
    result = _original_process_page(self, page)
    if not preserve_alphabetical_index(page):
        preserve_copyright_blocks(page)
        merge_caption_closers(self, page)
        merge_caption_fragments(self, page)
        merge_source_year_range_fragments(self, page)
        preserve_trailing_equations(self, page)
        preserve_toc_leaders(self, page)
        split_structured_entries(self, page)
    # Links are mapped only after source paragraph boundaries are final.
    import pdf_links
    pdf_links.capture_source(page)
    return result


_ENTRY_NUMBER = re.compile(r'^\s*(\d{1,3})(?:([.)\]])\s*|\s+)(?=[A-Za-z])')
_ENTRY_LETTER = re.compile(r'^\s*\(([a-z])\)\s*(?=[A-Za-z])')
_ENTRY_AUTHOR = re.compile(r'^[^\W\d_][\w’\x27.\-]+(?:[ -][^\W\d_][\w’\x27.\-]+){0,2},\s+[A-Z](?:[.-][A-Z])*\.')


def _entry_marker(row, letter=False):
    """Read a marker only at a real glyph boundary with visible source spacing."""
    text = ''.join(c.char_unicode for c in row)
    match = (_ENTRY_LETTER if letter else _ENTRY_NUMBER).match(text)
    if not match:
        return None
    offset, prefix = 0, []
    for body in row:
        if offset == match.end():
            break
        offset += len(body.char_unicode)
        prefix.append(body)
    else:
        return None
    if offset != match.end() or not prefix:
        return None
    visible = [c for c in prefix if c.char_unicode.strip()]
    if not visible:
        return None
    first, last = visible[0], visible[-1]
    size = first.pdf_style.font_size
    # PDFs often contain a drawn gap after `1.` without a space character.
    # A dot glued to the next word is not enough to infer a list boundary.
    if body.box.x - last.box.x2 < size * .1:
        return None
    return dict(value=ord(match[1]) if letter else int(match[1]),
                left=first.box.x, right=last.box.x2, body=body.box.x,
                explicit=letter or bool(match[2]), size=size)


def _aligned_entry_markers(markers):
    """Accept left-aligned labels or right-aligned bibliography numbers."""
    return bool(markers and (
        max(m['left'] for m in markers) - min(m['left'] for m in markers) < 1 or
        max(m['right'] for m in markers) - min(m['right'] for m in markers) < 1))


def _restore_detached_list_markers(finder, page):
    """Attach a paired `(a)`, `(b)` source run to its unique body rows.

    Some layout boxes exclude labels and leave them in the original-glyph
    layer. Require two or more sequential, same-column labels, one paragraph,
    matching original font/size/baselines and a small clear horizontal gap.
    Move the original glyph objects; never synthesize or rewrite a label.
    """
    from babeldoc.format.pdf.document_il import PdfLine, PdfParagraphComposition
    rows_by_paragraph = [(p, _source_rows(p)) for p in page.pdf_paragraph
                         if p.layout_label in {'plain text', 'text', 'reference'}]
    candidates = []
    raw = page.pdf_character
    for index in range(len(raw) - 2):
        chars = raw[index:index + 3]
        if not (chars[0].char_unicode == '(' and chars[2].char_unicode == ')' and
                isinstance(chars[1].char_unicode, str) and re.fullmatch(r'[a-z]', chars[1].char_unicode)):
            continue
        if any(not _finite_character(c) for c in chars):
            continue
        first = chars[0]
        size = first.pdf_style.font_size
        if any(c.pdf_style.font_id != first.pdf_style.font_id or
               abs(c.pdf_style.font_size - size) > .02 or c.xobj_id != first.xobj_id or
               abs(c.box.y - first.box.y) > size * .1 for c in chars):
            continue
        if any(not -.1 <= b.box.x - a.box.x2 <= size * .25 for a, b in zip(chars, chars[1:])):
            continue
        matches = []
        for paragraph, rows in rows_by_paragraph:
            if not rows or paragraph.xobj_id != first.xobj_id:
                continue
            for row_index, row in enumerate(rows):
                body = next(c for c in row if c.char_unicode.strip())
                if (not re.match(r'^[A-Za-z]', body.char_unicode) or
                        body.pdf_style.font_id != first.pdf_style.font_id or
                        abs(body.pdf_style.font_size - size) > .02 or
                        abs(body.box.y - first.box.y) > size * .15 or
                        not size * .5 <= body.box.x - chars[-1].box.x2 <= size * 3):
                    continue
                matches.append((paragraph, rows, row_index, body.box.x))
        if len(matches) == 1:
            paragraph, rows, row_index, body_x = matches[0]
            candidates.append(dict(paragraph=paragraph, rows=rows, index=row_index,
                                   chars=chars, value=ord(chars[1].char_unicode),
                                   left=first.box.x, body=body_x, size=size))
    moved = set()
    for paragraph, rows in rows_by_paragraph:
        group = sorted([c for c in candidates if c['paragraph'] is paragraph], key=lambda c: c['index'])
        if (len(group) < 2 or group[0]['index'] != 0 or
                len({c['index'] for c in group}) != len(group) or
                any(b['value'] != a['value'] + 1 for a, b in zip(group, group[1:])) or
                max(c['left'] for c in group) - min(c['left'] for c in group) >= 1 or
                max(c['body'] for c in group) - min(c['body'] for c in group) >= 1 or
                max(c['size'] for c in group) - min(c['size'] for c in group) > .02):
            continue
        ids = [id(char) for entry in group for char in entry['chars']]
        if len(set(ids)) != len(ids) or moved.intersection(ids):
            continue
        for entry in group:
            rows[entry['index']] = entry['chars'] + rows[entry['index']]
        paragraph.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=row))
                                               for row in rows]
        for comp in paragraph.pdf_paragraph_composition:
            finder.update_line_data(comp.pdf_line)
        finder.update_paragraph_data(paragraph, update_unicode=True)
        moved.update(ids)
    if moved:
        page.pdf_character = [c for c in raw if id(c) not in moved]
        with _lock:
            _counters['restoredDetachedListMarkers'] += len(moved) // 3
    return len(moved) // 3


def _source_rows(paragraph):
    """Recover physical rows without touching source glyphs or script offsets."""
    if paragraph.vertical or any(c.pdf_line is None for c in paragraph.pdf_paragraph_composition):
        return None
    chars = [c for comp in paragraph.pdf_paragraph_composition for c in comp.pdf_line.pdf_character]
    if not chars or any(not _finite_character(c) or not isinstance(c.char_unicode, str) for c in chars):
        return None
    rows, row, anchor = [], [], None
    for char in chars:
        if not char.char_unicode.strip():
            row.append(char)
            continue
        if anchor is not None and (char.box.y < anchor.box.y - anchor.pdf_style.font_size * .65
                and char.box.x < row[-1].box.x - anchor.pdf_style.font_size):
            rows.append(row)
            row, anchor = [], None
        if anchor is None:
            anchor = char
        row.append(char)
    if row:
        rows.append(row)
    # Actual lines must run top to bottom. A stream crossing into another
    # column or with ambiguous vertical ordering is not resegmented.
    visible = [[c for c in row if c.char_unicode.strip()] for row in rows]
    if any(not row for row in visible) or any(
            b[0].box.y >= a[0].box.y for a, b in zip(visible, visible[1:])):
        return None
    return rows


def split_structured_entries(finder, page):
    """Restore explicit list items and hanging bibliography entry boundaries.

    Numbered lists require consecutive source labels aligned at their left or
    right edge. Bibliographies require repeated author/initial headers and
    indented continuation rows. No page numbers or paper titles are used.
    """
    from babeldoc.format.pdf.document_il import PdfLine, PdfParagraphComposition
    _restore_detached_list_markers(finder, page)
    result, splits, hanging = [], 0, 0
    for paragraph in page.pdf_paragraph:
        rows = _source_rows(paragraph)
        if (not rows or paragraph.layout_label not in {'plain text', 'text', 'reference'}):
            result.append(paragraph)
            continue
        texts = [''.join(c.char_unicode for c in row).strip() for row in rows]
        starts = [next(c for c in row if c.char_unicode.strip()) for row in rows]
        left = min(c.box.x for c in starts)
        size = max(c.pdf_style.font_size for c in starts)
        numeric = [(i, m) for i, row in enumerate(rows) if (m := _entry_marker(row))]
        letters = [(i, m) for i, row in enumerate(rows) if (m := _entry_marker(row, letter=True))]
        authors = [i for i, text in enumerate(texts)
                   if _ENTRY_AUTHOR.match(text) and abs(starts[i].box.x - left) < 1]
        continuation = [c.box.x - left for c in starts if size * .5 <= c.box.x - left <= size * 3]
        is_numbered = (len(numeric) >= 2 and _aligned_entry_markers([m for _, m in numeric]) and
                       all(b[1]['value'] == a[1]['value'] + 1 for a, b in zip(numeric, numeric[1:])))
        is_lettered = (len(letters) >= 2 and _aligned_entry_markers([m for _, m in letters]) and
                       all(b[1]['value'] == a[1]['value'] + 1 for a, b in zip(letters, letters[1:])))
        is_references = len(authors) >= 2 and continuation and bool(re.search(r'\b(?:19|20)\d{2}\b', ' '.join(texts)))
        boundaries = ([i for i, _ in numeric] if is_numbered else
                      [i for i, _ in letters] if is_lettered else authors if is_references else [])
        markers = dict(numeric if is_numbered else letters if is_lettered else [])
        # Already separated bullets still need their original hanging column.
        is_bullet = bool(re.match(r'^[•▪◦●]\s*', texts[0]))
        # A source item already separated by the layout detector still needs
        # its hanging indentation. Require an explicit label and aligned body
        # continuation rows, rather than treating an arbitrary leading number
        # as a one-item list.
        single_marker = _entry_marker(rows[0]) or _entry_marker(rows[0], letter=True)
        is_single = bool(single_marker and single_marker['explicit'] and len(rows) > 1 and
                         all(abs(c.box.x - single_marker['body']) < 1 for c in starts[1:]))
        if not boundaries and not is_bullet and not is_single:
            result.append(paragraph)
            continue
        boundaries = sorted(set([0] + boundaries + [len(rows)]))
        for start, end in zip(boundaries, boundaries[1:]):
            item = paragraph if len(boundaries) == 2 else copy.copy(paragraph)
            item.pdf_paragraph_composition = [PdfParagraphComposition(pdf_line=PdfLine(pdf_character=row))
                                              for row in rows[start:end]]
            for comp in item.pdf_paragraph_composition:
                finder.update_line_data(comp.pdf_line)
            finder.update_paragraph_data(item, update_unicode=True)
            item.first_line_indent = False
            if item.debug_id and item is not paragraph:
                item.debug_id += '-entry-' + str(start)
            candidate_indents = [c.box.x - starts[start].box.x for c in starts[start + 1:end]
                                 if size * .4 <= c.box.x - starts[start].box.x <= size * 3]
            indent = min(candidate_indents) if candidate_indents else 0
            if not indent:
                marker = markers.get(start) or (single_marker if start == 0 and is_single else None)
                if marker:
                    indent = marker['body'] - starts[start].box.x
            if indent > 0 and indent < (item.box.x2 - item.box.x) * .3:
                _hanging_indents[id(item)] = (item, indent)
                hanging += 1
            result.append(item)
        splits += max(0, len(boundaries) - 2)
    page.pdf_paragraph = result
    if splits or hanging:
        with _lock:
            _counters['restoredStructuredEntryBoundaries'] += splits
            _counters['registeredHangingIndents'] += hanging
    return splits


def _physical_line(line):
    chars = line.pdf_character if line else []
    visible = [c for c in chars if isinstance(c.char_unicode, str) and c.char_unicode.strip()]
    if not visible or any(c.vertical or not c.pdf_style or not c.box or not c.visual_bbox
                          or not c.visual_bbox.box or not isinstance(c.pdf_style.font_size, (int, float))
                          or c.pdf_style.font_size <= 0
                          or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                 for b in (c.box, c.visual_bbox.box) for v in (b.x,b.y,b.x2,b.y2))
                          or not isinstance(c.char_unicode, str) for c in chars):
        return None
    size = max(c.pdf_style.font_size for c in visible)
    if (max(c.box.y for c in visible)-min(c.box.y for c in visible) > size*0.25
            or any(b.box.x+0.25 < a.box.x for a,b in zip(visible,visible[1:]))):
        return None
    return chars, visible, size


_COPYRIGHT_START = re.compile(r'^\s*(?:Copyright\s*:|©\s*(?:19|20)\d{2})', re.I)
_COPYRIGHT_LICENSE = re.compile(
    r'Creative\s+Commons|all\s+rights\s+reserved|open[ -]access.*licen[cs]e', re.I)


def preserve_copyright_blocks(page):
    """Keep one uniquely continuous, explicitly anchored license block intact.

    Layout detectors may divide a physical word across a fallback fragment and
    a translated paragraph. Gather only adjacent, equal-sized source lines of
    a Copyright/© block; require license wording and a closed final sentence.
    No glyph is synthesized, rewritten, or discarded.
    """
    records = []
    for paragraph in page.pdf_paragraph:
        if paragraph.vertical or paragraph.layout_label not in {'plain text', 'text', 'fallback_line'}:
            continue
        infos = [_physical_line(c.pdf_line) for c in paragraph.pdf_paragraph_composition]
        if not infos or any(info is None for info in infos):
            continue
        for info in infos:
            chars, visible, size = info
            if (any(abs(c.pdf_style.font_size-size) > 0.02 for c in visible)
                    or any(c.xobj_id != paragraph.xobj_id for c in visible)):
                continue
            records.append(dict(paragraph=paragraph, chars=chars, size=size,
                                x=visible[0].box.x, right=visible[-1].box.x2,
                                y=visible[0].box.y,
                                text=''.join(c.char_unicode for c in chars)))
    removed, blocks, count = set(), 0, 0
    for anchor in records:
        if id(anchor['paragraph']) in removed or not _COPYRIGHT_START.match(anchor['text']):
            continue
        size, left = anchor['size'], anchor['x']
        allowed_fonts = {c.pdf_style.font_id for c in anchor['chars']}
        selected, rows, current = [], [], anchor
        complete = False
        for _ in range(4):
            row = [current]
            while True:
                end = row[-1]
                adjacent = [r for r in records if r is not end and r not in row
                    and r not in selected and id(r['paragraph']) not in removed
                    and r['paragraph'].xobj_id == anchor['paragraph'].xobj_id
                    and abs(r['size']-size) <= 0.02 and abs(r['y']-end['y']) <= size*0.1
                    and -0.25 <= r['x']-end['right'] <= size*0.75
                    and {c.pdf_style.font_id for c in r['chars']} <= allowed_fonts]
                if len(adjacent) != 1:
                    if adjacent:  # An overlapping/ambiguous continuation is unsafe.
                        row = []
                    break
                row.append(adjacent[0])
            if not row:
                break
            selected.extend(row)
            rows.append(''.join(r['text'] for r in row).strip())
            text = ' '.join(rows)
            if _COPYRIGHT_LICENSE.search(text) and re.search(r'[.!]$', rows[-1]):
                complete = True
                break
            next_rows = [r for r in records if r not in selected
                and id(r['paragraph']) not in removed
                and r['paragraph'].xobj_id == anchor['paragraph'].xobj_id
                and abs(r['size']-size) <= 0.02 and abs(r['x']-left) <= max(1, size*0.2)
                and size*0.9 <= current['y']-r['y'] <= size*1.7
                and {c.pdf_style.font_id for c in r['chars']} <= allowed_fonts]
            if len(next_rows) != 1:
                break
            current = next_rows[0]
        if not complete:
            continue
        paragraphs = {id(r['paragraph']): r['paragraph'] for r in selected}
        glyphs = [c for p in paragraphs.values() for comp in p.pdf_paragraph_composition
                  for c in comp.pdf_line.pdf_character]
        selected_ids = {id(c) for r in selected for c in r['chars']}
        glyph_ids = [id(c) for c in glyphs]
        if (set(glyph_ids) != selected_ids or len(set(glyph_ids)) != len(glyph_ids)
                or set(glyph_ids) & {id(c) for c in page.pdf_character}):
            continue  # Never consume a partial paragraph or duplicate a glyph.
        page.pdf_character.extend(glyphs)
        removed.update(paragraphs)
        blocks += 1
        count += len(glyphs)
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['preservedCopyrightBlocks'] += blocks
            _counters['preservedCopyrightParagraphs'] += len(removed)
            _counters['preservedCopyrightGlyphs'] += count
    return blocks


def merge_caption_closers(finder, page):
    """Join a detached ')' only to its unique adjacent, unclosed caption line."""
    removed = set()
    for closing in list(page.pdf_paragraph):
        if (closing.layout_label not in {'figure_caption', 'table_caption'} or closing.vertical
                or len(closing.pdf_paragraph_composition) != 1):
            continue
        end_line = closing.pdf_paragraph_composition[0].pdf_line
        end = _physical_line(end_line)
        if not end or len(end[1]) != 1 or end[1][0].char_unicode not in {')', '）'}:
            continue
        symbol = end[1][0]
        opener = '(' if symbol.char_unicode == ')' else '（'
        candidates = []
        for body in page.pdf_paragraph:
            if (body is closing or id(body) in removed or body.vertical
                    or body.layout_label != closing.layout_label or body.xobj_id != closing.xobj_id
                    or not body.pdf_paragraph_composition
                    or any(comp.pdf_line is None for comp in body.pdf_paragraph_composition)):
                continue
            last_line = body.pdf_paragraph_composition[-1].pdf_line
            info = _physical_line(last_line)
            if not info:
                continue
            text = ''.join(c.char_unicode for comp in body.pdf_paragraph_composition
                           for c in comp.pdf_line.pdf_character)
            # Exactly one unmatched opener of this kind; no unmatched closers.
            depth, malformed = 0, False
            for ch in text:
                if ch == opener:
                    depth += 1
                elif ch == symbol.char_unicode:
                    depth -= 1
                    malformed |= depth < 0
            if depth != 1 or malformed:
                continue
            anchor = info[1][-1]
            size = symbol.pdf_style.font_size
            gap = symbol.box.x-anchor.box.x2
            if (anchor.pdf_style.font_id != symbol.pdf_style.font_id
                    or abs(anchor.pdf_style.font_size-size) > 0.01
                    or abs(anchor.box.y-symbol.box.y) > size*0.1
                    or not -0.25 <= gap <= size*0.4
                    or anchor.xobj_id != symbol.xobj_id):
                continue
            if any(id(c) in {id(g) for g in info[0]} for c in end[0]):
                continue
            candidates.append((body,last_line))
        if len(candidates) != 1:
            continue
        body,line = candidates[0]
        line.pdf_character.extend(end[0])
        finder.update_line_data(line)
        finder.update_paragraph_data(body, update_unicode=True)
        removed.add(id(closing))
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedCaptionClosers'] += len(removed)
    return len(removed)


def merge_caption_fragments(finder, page):
    """Join uniquely adjacent detector fragments of one physical caption.

    Recognize only an explicit Figure/Table number prefix and a tiny trailing
    alphabetic word fragment; same font, size, baseline and sub-space gap are
    required. A nearby label, another line or an ambiguous neighbor is kept.
    """
    removed = set()
    for body in page.pdf_paragraph:
        if (body.vertical or body.layout_label not in {'figure_caption', 'table_caption'}
                or not body.pdf_paragraph_composition
                or any(c.pdf_line is None for c in body.pdf_paragraph_composition)):
            continue
        for side in ('prefix', 'suffix'):
            comp = body.pdf_paragraph_composition[0 if side == 'prefix' else -1]
            info = _physical_line(comp.pdf_line)
            if not info:
                continue
            chars, visible, _ = info
            anchor = visible[0 if side == 'prefix' else -1]
            body_text = ''.join(c.char_unicode for c in chars)
            if (side == 'prefix' and not re.match(r'^\s*(?:[.:]|[A-Za-z])', body_text)
                    or side == 'suffix' and not re.search(r'[A-Za-z]$', body_text)):
                continue
            candidates = []
            for fragment in page.pdf_paragraph:
                if (fragment is body or id(fragment) in removed or fragment.vertical
                        or fragment.layout_label != 'fallback_line' or fragment.xobj_id != body.xobj_id
                        or len(fragment.pdf_paragraph_composition) != 1):
                    continue
                line = fragment.pdf_paragraph_composition[0].pdf_line
                candidate = _physical_line(line)
                if not candidate:
                    continue
                text = ''.join(c.char_unicode for c in candidate[0]).strip()
                complete_prefix = bool(re.fullmatch(r'(?:Fig(?:ure)?\.?|Table)\s+\d+[a-z]?[.:]', text, re.I))
                if (side == 'prefix' and not (complete_prefix or re.fullmatch(
                        r'(?:Fig(?:ure)?\.?|Table)\s+\d+[a-z]?', text, re.I)
                        and re.match(r'^\s*[.:]', body_text))
                        or side == 'suffix' and not re.fullmatch(r'[a-z]{1,3}\.?', text)):
                    continue
                last = candidate[1][-1] if side == 'prefix' else anchor
                first = anchor if side == 'prefix' else candidate[1][0]
                size = anchor.pdf_style.font_size
                if (any((c.pdf_style.font_id != anchor.pdf_style.font_id and not (side == 'prefix' and complete_prefix))
                        or abs(c.pdf_style.font_size - size) > .02
                        or abs(c.box.y - anchor.box.y) > size * .1
                        or c.xobj_id != anchor.xobj_id for c in candidate[1])
                        or not -.25 <= first.box.x - last.box.x2 <= size * (.6 if side == 'prefix' and complete_prefix else .3)):
                    continue
                candidates.append((fragment, candidate[0]))
            if len(candidates) != 1:
                continue
            fragment, incoming = candidates[0]
            if side == 'prefix':
                comp.pdf_line.pdf_character = incoming + chars
            else:
                comp.pdf_line.pdf_character.extend(incoming)
            finder.update_line_data(comp.pdf_line)
            finder.update_paragraph_data(body, update_unicode=True)
            removed.add(id(fragment))
    if removed:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedCaptionFragments'] += len(removed)
    return len(removed)


def merge_source_year_range_fragments(finder, page):
    """Restore a source line split around a separately detected year-range dash.

    Only three consecutive source paragraphs may participate: a one-line prose
    prefix ending in '(YYYY', one original dash glyph, and a body paragraph
    beginning with 'YY)' or 'YYYY)'. The entire boundary line must share its
    baseline, face, size and XObject; every range glyph must touch its next
    neighbor within a fraction of a space. No text/glyph is created, sorted or
    guessed, and the remainder of the body keeps its existing compositions.
    """
    paragraphs = list(page.pdf_paragraph)
    page_chars = list(page.pdf_character)
    page_chars.extend(c for p in paragraphs for comp in p.pdf_paragraph_composition
                      for c in _composition_characters(comp))
    occurrences = Counter(id(c) for c in page_chars)
    removed, merged = set(), 0
    for left, separator, right in zip(paragraphs, paragraphs[1:], paragraphs[2:]):
        if (any(id(p) in removed or p.vertical for p in (left, separator, right))
                or left.layout_label not in {'plain text', 'text'}
                or right.layout_label != left.layout_label
                or separator.layout_label not in {'plain text', 'text', 'title', 'fallback_line'}
                or len(left.pdf_paragraph_composition) != 1
                or len(separator.pdf_paragraph_composition) != 1
                or not right.pdf_paragraph_composition
                or len({p.xobj_id for p in (left, separator, right)}) != 1):
            continue
        line_parts = [_physical_line(p.pdf_paragraph_composition[0].pdf_line)
                      for p in (left, separator, right)]
        if any(info is None for info in line_parts):
            continue
        first, middle, last = [info[0] for info in line_parts]
        left_text = ''.join(c.char_unicode for c in first)
        right_text = ''.join(c.char_unicode for c in last)
        opening = re.search(r'\(([12]\d{3})$', left_text)
        closing = re.match(r'(\d{2}|[12]\d{3})\)\s*[A-Za-z]', right_text)
        if (not opening or not closing or len(re.findall(r'[A-Za-z]{2,}', left_text[:opening.start()])) < 3
                or len(middle) != 1 or middle[0].char_unicode not in {'–', '-'}):
            continue
        start_year, ending = int(opening[1]), closing[1]
        end_year = int(ending) if len(ending) == 4 else start_year // 100 * 100 + int(ending)
        if not start_year <= end_year <= start_year + 100:
            continue
        range_chars = first[-5:] + middle + last[:len(ending) + 1]
        if ''.join(c.char_unicode for c in range_chars) != opening[0] + middle[0].char_unicode + ending + ')':
            continue  # The range must map one-to-one to original glyphs.
        anchor = middle[0]
        size = anchor.pdf_style.font_size
        boundary_chars = first + middle + last
        visible = [c for c in boundary_chars if c.char_unicode.strip()]
        if (any(c.pdf_style.font_id != anchor.pdf_style.font_id
                or abs(c.pdf_style.font_size - size) > .02
                or abs(c.box.y - anchor.box.y) > size * .05
                or c.xobj_id != anchor.xobj_id or c.xobj_id != left.xobj_id
                or c.formula_layout_id is not None for c in visible)
                or any(c.pdf_character_id is None or len(c.char_unicode) != 1
                       or c.box.x2 <= c.box.x or occurrences[id(c)] != 1 for c in range_chars)
                or any(not -size * .03 <= b.box.x - a.box.x2 <= size * .12
                       for a, b in zip(range_chars, range_chars[1:]))):
            continue
        member_ids = {id(c) for c in boundary_chars}
        if (len(member_ids) != len(boundary_chars)
                or any(_finite_character(c) and isinstance(c.char_unicode, str) and c.char_unicode.strip()
                       and id(c) not in member_ids and c.xobj_id == anchor.xobj_id
                       and abs(c.box.y - anchor.box.y) <= size * .25
                       and c.box.x < range_chars[-1].box.x2 and c.box.x2 > range_chars[0].box.x
                       for c in page_chars)):
            continue  # An intervening/overprinted source glyph makes adjacency ambiguous.
        line = right.pdf_paragraph_composition[0].pdf_line
        line.pdf_character = boundary_chars
        finder.update_line_data(line)
        finder.update_paragraph_data(right, update_unicode=True)
        removed.update((id(left), id(separator)))
        merged += 1
    if merged:
        page.pdf_paragraph = [p for p in page.pdf_paragraph if id(p) not in removed]
        with _lock:
            _counters['mergedSourceYearRanges'] += merged
    return merged


_LINE_SI_UNIT = re.compile(r'(?:[pnumkMGTμµ]?(?:Ω|F|H|V|A|W|Hz|s|m|J|K))\Z')


def _technical_physical_rows(line):
    """Recover clear physical rows inside an upstream merged PdfLine.

    A smaller single-character subscript is not a new baseline. Require its
    original same-font base immediately to the left; all other visible glyphs
    must follow one main baseline, or reset left on a distinct lower row.
    """
    chars = line.pdf_character if line else []
    visible = [c for c in chars if isinstance(c.char_unicode, str) and c.char_unicode.strip()]
    if not visible or any(c.vertical or not c.pdf_style or not c.box or not c.visual_bbox
                          or not c.visual_bbox.box or not isinstance(c.pdf_style.font_size, (int, float))
                          or c.pdf_style.font_size <= 0
                          or any(not isinstance(v, (int, float)) or not math.isfinite(v)
                                 for b in (c.box, c.visual_bbox.box) for v in (b.x,b.y,b.x2,b.y2))
                          or not isinstance(c.char_unicode, str) for c in chars):
        return None
    size = max(c.pdf_style.font_size for c in visible)
    rows, current, row_visible = [], [], []
    anchor = None
    for char in chars:
        if not char.char_unicode.strip():
            current.append(char)
            continue
        small = char.pdf_style.font_size < size*0.85
        if small:
            previous = row_visible[-1] if row_visible else None
            if (not previous or previous.pdf_style.font_size < size*0.85
                    or not re.fullmatch(r'[A-Za-z0-9]',char.char_unicode)
                    or not re.fullmatch(r'[A-Za-z]',previous.char_unicode)
                    or previous.pdf_style.font_id != char.pdf_style.font_id
                    or previous.xobj_id != char.xobj_id
                    or not -0.25 <= char.box.x-previous.box.x2 <= size*0.25
                    or not -size*0.6 <= char.box.y-previous.box.y <= -size*0.1):
                return None
        elif anchor is None:
            anchor = char
        elif abs(char.box.y-anchor.box.y) > size*0.25:
            if (not 0.9*size <= anchor.box.y-char.box.y <= 2.5*size
                    or abs(char.box.x-anchor.box.x) > max(1,size*0.15)):
                return None
            rows.append((current,row_visible,size))
            current,row_visible,anchor = [],[],char
        if row_visible and char.box.x+0.25 < row_visible[-1].box.x:
            return None
        current.append(char)
        row_visible.append(char)
    if not row_visible:
        return None
    rows.append((current,row_visible,size))
    return rows


def _standalone_technical_row(info):
    # Validated small subscripts are retained as original glyphs, but excluded
    # from lexical checks so U + small o is not mistaken for an English word.
    text = ''.join(c.char_unicode for c in info[0]
                   if c.pdf_style.font_size >= info[2]*0.85).strip()
    if ('=' not in text and len(re.findall(r'[<>≤≥]',text)) < 2) or not re.search(r'\d',text):
        return None
    tokens = re.findall(r'[A-Za-zα-ωΑ-Ωµ]+', text)
    if (not any(_LINE_SI_UNIT.fullmatch(token) for token in tokens)
            or any(not (_LINE_SI_UNIT.fullmatch(token) or re.fullmatch(r'[A-Za-zα-ωΑ-Ω]',token))
                   for token in tokens)):
        return None
    if not re.fullmatch(r'[\s\d.,:;()\[\]=+−\-×÷*/<>≤≥≈±∓·∗^²³°]*',
                        re.sub(r'[A-Za-zα-ωΑ-Ωµ]+','',text)):
        return None
    return info


def preserve_trailing_equations(finder, page):
    """Keep two or more standalone calculation/inequality rows below prose."""
    paragraphs, lines, glyphs = 0, 0, 0
    for paragraph in page.pdf_paragraph:
        comps = paragraph.pdf_paragraph_composition
        if (paragraph.vertical or paragraph.layout_label not in {'plain text','text'}
                or len(comps) < 2 or any(c.pdf_line is None for c in comps)):
            continue
        split = len(comps)
        tail_info = []
        while split:
            rows = _technical_physical_rows(comps[split-1].pdf_line)
            if not rows or any(not _standalone_technical_row(row) for row in rows):
                break
            tail_info = rows+tail_info
            split -= 1
        if split == 0 or len(tail_info) < 2:
            continue
        head = [c for comp in comps[:split] for c in comp.pdf_line.pdf_character]
        if not re.search(r'[A-Za-z]{3,}', ''.join(c.char_unicode for c in head)):
            continue
        head_last = _physical_line(comps[split-1].pdf_line)
        if not head_last:
            continue
        sequence = [head_last]+tail_info
        if any(not 0.9*max(a[2],b[2]) <= a[1][0].box.y-b[1][0].box.y <= 2.5*max(a[2],b[2])
               for a,b in zip(sequence,sequence[1:])):
            continue
        if any(abs(a[1][0].box.x-b[1][0].box.x)>max(1,a[2]*0.15)
               for a,b in zip(tail_info,tail_info[1:])):
            continue
        tail = [c for info in tail_info for c in info[0]]
        ids = [id(c) for c in head+tail]
        if len(set(ids)) != len(ids) or set(ids)&{id(c) for c in page.pdf_character}:
            continue
        barrier = max(c.box.y2 for c in tail)+0.01
        visible_head = [c for c in head if c.char_unicode.strip()]
        if min(c.visual_bbox.box.y for c in visible_head) <= barrier:
            continue
        paragraph.pdf_paragraph_composition = comps[:split]
        finder.update_paragraph_data(paragraph, update_unicode=True)
        # Empty space boxes can extend below actual ink. Keep the prose box
        # above the preserved rows, which also block typesetter expansion.
        paragraph.box.y = max(paragraph.box.y,barrier)
        page.pdf_character.extend(tail)
        paragraphs += 1
        lines += len(tail_info)
        glyphs += len(tail)
    if paragraphs:
        with _lock:
            _counters['preservedTrailingEquationParagraphs'] += paragraphs
            _counters['preservedTrailingEquationLines'] += lines
            _counters['preservedTrailingEquationGlyphs'] += glyphs
    return lines


def preserve_alphabetical_index(page):
    """Preserve a confidently recognized alphabetical index as source glyphs.

    Merged multi-line entries can contain several page references; translating
    them as prose loses references, dot leaders and even isolated letter titles.
    Require repeated alphabetic headings AND a dense set of dotted references.
    A normal contents page, bibliography or prose page must not match. Moving
    original character objects to the existing page character lane also avoids
    re-typesetting short headings that upstream skips during translation.
    """
    paragraphs = page.pdf_paragraph
    if not paragraphs:
        return False
    headings, reference_lines, meaningful_lines = set(), 0, 0
    glyphs = []
    for paragraph in paragraphs:
        if paragraph.vertical:
            return False
        lines = []
        for composition in paragraph.pdf_paragraph_composition:
            if composition.pdf_line is None:
                return False  # Never discard an unfamiliar composition.
            chars = composition.pdf_line.pdf_character
            if any(not isinstance(c.char_unicode, str) or c.vertical for c in chars):
                return False
            lines.append("".join(c.char_unicode for c in chars))
            glyphs.extend(chars)
        text = " ".join(lines).strip()
        if str(paragraph.layout_label).casefold() == "title" and re.fullmatch(r"[A-Z]", text):
            headings.add(text)
        for line in lines:
            if re.search(r"[A-Za-z]", line):
                meaningful_lines += 1
            if re.search(r"[A-Za-z].*\.{8,}\s*[1-9][0-9]{0,3}(?:\s*,\s*[1-9][0-9]{0,3})*\s*$", line):
                reference_lines += 1
        # An introductory paragraph of real prose is not an index entry. Avoid
        # preserving an entire mixed page just because it contains an index box.
        if "." * 8 not in text and len(re.findall(r"[A-Za-z]{2,}", text)) > 28:
            return False
    if len(headings) < 3 or reference_lines < 8 or reference_lines < meaningful_lines * 0.25:
        return False
    existing_ids = {id(c) for c in page.pdf_character}
    if len({id(c) for c in glyphs}) != len(glyphs) or any(id(c) in existing_ids for c in glyphs):
        return False
    page.pdf_character.extend(glyphs)
    page.pdf_paragraph = []
    with _lock:
        _counters["preservedIndexPages"] += 1
        _counters["preservedIndexGlyphs"] += len(glyphs)
        _counters["preservedIndexReferenceLines"] += reference_lines
    return True


def _toc_candidate(paragraph):
    """Return a split only for a single physical line with an explicit leader."""
    compositions = paragraph.pdf_paragraph_composition
    if paragraph.vertical or len(compositions) != 1 or not compositions[0].pdf_line:
        return None
    line = compositions[0].pdf_line
    chars = line.pdf_character
    if not chars or any(not isinstance(c.char_unicode, str) or not c.char_unicode
                        for c in chars):
        return None
    text = "".join(c.char_unicode for c in chars)
    match = _TOC_TAIL.search(text)
    if match is None or int(match[1]) < 1:
        return None
    # A source glyph can represent a complete ligature (for example "fi").
    # Match offsets refer to Unicode, not glyph indexes. Only split at actual
    # glyph boundaries; never divide a ligature or synthesize its characters.
    boundaries, offset = {0: 0}, 0
    for index, char in enumerate(chars):
        offset += len(char.char_unicode)
        boundaries[offset] = index + 1
    if any(offset not in boundaries for offset in
           (match.start(), match.start(1), match.end(1))):
        return None
    leader_index = boundaries[match.start()]
    if any(len(c.char_unicode) != 1 for c in chars[leader_index:]):
        return None
    label = text[:match.start()]
    if len(re.findall(r"[^\W\d_]", label, flags=re.UNICODE)) < 2:
        return None
    if any(c.box is None or c.visual_bbox is None or c.visual_bbox.box is None
           or c.pdf_style is None or not isinstance(c.pdf_style.font_size, (int, float))
           or c.pdf_style.font_size <= 0
           or any(not isinstance(value, (int, float)) or not math.isfinite(value)
                  for box in (c.box, c.visual_bbox.box)
                  for value in (box.x, box.y, box.x2, box.y2)) for c in chars):
        return None
    # Reject merged lines, vertical writing and ambiguous content-stream order.
    size = max(c.pdf_style.font_size for c in chars)
    if (max(c.box.y for c in chars) - min(c.box.y for c in chars) > size * 0.4
            or any(c.vertical for c in chars)
            or any(b.box.x + 0.25 < a.box.x for a, b in zip(chars, chars[1:]))):
        return None
    # Some source spaces have boxes extending into the first dot. Keep those
    # original spacing glyphs with the leader instead of treating their empty
    # ink as overlapping body text. Visible text must still clear the leader.
    body_end = leader_index
    while body_end and re.fullmatch(r"[ \t]+", chars[body_end - 1].char_unicode):
        body_end -= 1
    body, tail = chars[:body_end], chars[body_end:]
    if not body:
        return None
    leader_x = chars[leader_index].box.x
    body_right = max(c.visual_bbox.box.x2 for c in body)
    if body_right > leader_x + 0.25 or leader_x - min(c.box.x for c in body) < size:
        return None
    digits = chars[boundaries[match.start(1)]:boundaries[match.end(1)]]
    return {"paragraph": paragraph, "line": line, "body": body, "tail": tail,
            "leaderX": leader_x, "right": max(c.box.x2 for c in digits),
            "baseline": chars[0].box.y}


def preserve_toc_leaders(finder, page):
    """Keep leader/page-number glyphs outside the translation/reflow pipeline.

    Require another matching row with an aligned page-number edge. This avoids
    mistaking a lone prose ellipsis followed by a number for a table of contents.
    The existing page-level character lane preserves source glyphs and positions;
    Typesetting.get_max_right_space also treats these characters as obstacles.
    """
    candidates = [candidate for p in page.pdf_paragraph
                  if (candidate := _toc_candidate(p)) is not None]
    confirmed = [candidate for candidate in candidates if any(
        other is not candidate and abs(other["right"] - candidate["right"]) <= 2
        and abs(other["baseline"] - candidate["baseline"]) > 2
        for other in candidates)]
    for candidate in confirmed:
        paragraph, line = candidate["paragraph"], candidate["line"]
        line.pdf_character = candidate["body"]
        finder.update_line_data(line)
        finder.update_paragraph_data(paragraph, update_unicode=True)
        paragraph.box.x2 = min(paragraph.box.x2, candidate["leaderX"])
        # Move existing objects, including source render order and empty spacing
        # glyphs. Do not paint replacement dots or ask the model to regenerate them.
        page.pdf_character.extend(candidate["tail"])
    if confirmed:
        with _lock:
            _counters["preservedTOCRows"] += len(confirmed)
            _counters["preservedTOCGlyphs"] += sum(
                c.pdf_character_id is not None for candidate in confirmed for c in candidate["tail"])
            _counters["pagesWithPreservedTOC"] += 1
    return len(confirmed)


def install():
    """Install in the actual translation subprocess before parsing pages."""
    global _installed, _original_process_page, _original_is_formulas_font
    global _original_process_translatable_formulas, _original_is_translatable_formula
    global _original_process_comma_formulas, _original_should_split_formula
    global _original_styles_process_page, _original_layout_typesetting_units
    with _lock:
        if _installed:
            return
        from importlib.metadata import version
        if version("babeldoc") != "0.6.2":
            raise RuntimeError("Layout guard requires dependency re-audit")
        from babeldoc.format.pdf.document_il.midend.paragraph_finder import ParagraphFinder
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
        from babeldoc.format.pdf.document_il.utils import formular_helper
        _original_is_formulas_font = formular_helper.is_formulas_font
        formular_helper.is_formulas_font = _guard_is_formulas_font
        _original_process_translatable_formulas = StylesAndFormulas.process_translatable_formulas
        _original_is_translatable_formula = StylesAndFormulas.is_translatable_formula
        StylesAndFormulas.process_translatable_formulas = _guard_process_translatable_formulas
        StylesAndFormulas.is_translatable_formula = _guard_is_translatable_formula
        _original_process_comma_formulas = StylesAndFormulas.process_comma_formulas
        _original_should_split_formula = StylesAndFormulas.should_split_formula
        StylesAndFormulas.process_comma_formulas = _guard_process_comma_formulas
        StylesAndFormulas.should_split_formula = _guard_should_split_formula
        _original_styles_process_page = StylesAndFormulas.process_page
        StylesAndFormulas.process_page = _guard_styles_process_page
        _original_layout_typesetting_units = Typesetting._layout_typesetting_units
        Typesetting._layout_typesetting_units = _guard_layout_typesetting_units
        _original_process_page = ParagraphFinder.process_page
        ParagraphFinder.process_page = _guard_process_page
        _installed = True


def snapshot():
    with _lock:
        return {"guardVersion": GUARD_VERSION, "guardInstalled": _installed,
                "repairedSubscriptOffsets": _counters['repairedSubscriptOffsets'],
                "repairedScriptFollowingFormulaOffsets": _counters['repairedScriptFollowingFormulaOffsets'],
                "registeredScriptBindings": _counters['registeredScriptBindings'],
                "restoredPhysicalLineBreaks": _counters['restoredPhysicalLineBreaks'],
                "boundSourceFormulaDescriptors": _counters['boundSourceFormulaDescriptors'],
                "boundSourceFormulaDecimals": _counters['boundSourceFormulaDecimals'],
                "rejoinedSourceColumnSentenceTails": _counters['rejoinedSourceColumnSentenceTails'],
                "legacyMathFontMatches": _counters['legacyMathFontMatches'],
                "preservedLegacySymbolFormulas": _counters['preservedLegacySymbolFormulas'],
                "preservedLegacyCommaFormulas": _counters['preservedLegacyCommaFormulas'],
                "preservedCopyrightBlocks": _counters['preservedCopyrightBlocks'],
                "preservedCopyrightParagraphs": _counters['preservedCopyrightParagraphs'],
                "preservedCopyrightGlyphs": _counters['preservedCopyrightGlyphs'],
                "normalizedWebdingsBullets": _counters["normalizedWebdingsBullets"],
                "pagesWithNormalizedBullets": _counters["pagesWithNormalizedBullets"],
                "preservedTOCRows": _counters["preservedTOCRows"],
                "preservedTOCGlyphs": _counters["preservedTOCGlyphs"],
                "pagesWithPreservedTOC": _counters["pagesWithPreservedTOC"],
                "preservedIndexPages": _counters["preservedIndexPages"],
                "preservedIndexGlyphs": _counters["preservedIndexGlyphs"],
                "preservedIndexReferenceLines": _counters["preservedIndexReferenceLines"],
                "mergedCaptionClosers": _counters["mergedCaptionClosers"],
                "mergedCaptionFragments": _counters["mergedCaptionFragments"],
                "mergedSourceYearRanges": _counters["mergedSourceYearRanges"],
                "preservedTrailingEquationParagraphs": _counters["preservedTrailingEquationParagraphs"],
                "preservedTrailingEquationLines": _counters["preservedTrailingEquationLines"],
                "preservedTrailingEquationGlyphs": _counters["preservedTrailingEquationGlyphs"],
                "restoredStructuredEntryBoundaries": _counters["restoredStructuredEntryBoundaries"],
                "restoredDetachedListMarkers": _counters["restoredDetachedListMarkers"],
                "registeredHangingIndents": _counters["registeredHangingIndents"],
                "rawDocumentTextStored": False}
