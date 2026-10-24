"""Rebuild PDF links from source/translated paragraph lineage.

Only annotations are changed. Text, graphics and fonts are never rewritten.
Partial links require an unchanged, unambiguous literal in the same paragraph;
whole-paragraph links follow the actual rendered lines. A source rectangle is
reused only when its visible raster region is exactly unchanged.
"""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
import math
from pathlib import Path
import re
from statistics import median
import threading
import unicodedata

_lock = threading.RLock()
_source = None
_pages = {}
_mapped = {}
_original_render_page = None


def _chars(paragraph):
    result = []
    for comp in paragraph.pdf_paragraph_composition:
        if comp.pdf_character is not None:
            result.append(comp.pdf_character)
        else:
            for key in ('pdf_line', 'pdf_same_style_characters', 'pdf_formula'):
                value = getattr(comp, key, None)
                if value is not None:
                    result.extend(value.pdf_character)
                    break
    return result


def _normal(text):
    return ''.join(c for c in unicodedata.normalize('NFKC', text)
                   if not c.isspace() and unicodedata.category(c) != 'Cc')


def _text_map(chars):
    text, indexes = '', []
    for index, char in enumerate(chars):
        value = _normal(char.char_unicode or '')
        text += value
        indexes.extend([index] * len(value))
    return text, indexes


def _rect(char, matrix):
    import pymupdf
    box = char.box
    return pymupdf.Rect(box.x, box.y, box.x2, box.y2) * matrix


def _line_rects(chars, matrix):
    """Separate clickable rectangles per actual line, never bridge columns."""
    rows = []
    for char in chars:
        if not _normal(char.char_unicode or '') or char.box is None:
            continue
        rect = _rect(char, matrix)
        if rect.is_empty or rect.is_infinite:
            continue
        row = next((row for row in rows
                    if abs(row[0].y1 - rect.y1) <= max(1, min(row[0].height, rect.height) * .3)
                    and rect.x0 >= row[0].x0 - 1
                    and rect.x0 <= row[0].x1 + max(12, rect.height * 2)), None)
        if row is None:
            rows.append([rect])
        else:
            row[0] |= rect
    return [list(row[0]) for row in rows]


def _occurrences(text, needle):
    return [match.start() for match in re.finditer(re.escape(needle), text)]


def match_literal(source_chars, target_chars, selected, matrix):
    """Map an identical literal only with unique same-paragraph provenance."""
    source, source_indexes = _text_map(source_chars)
    target, target_indexes = _text_map(target_chars)
    offsets = [i for i, index in enumerate(source_indexes) if index in selected]
    if not offsets:
        return []
    start, end = min(offsets), max(offsets) + 1
    if offsets != list(range(start, end)):
        return []
    needle = source[start:end]
    old = _occurrences(source, needle)
    new = _occurrences(target, needle)
    if not needle or len(old) != len(new) or start not in old:
        return []
    # Repeated text is ambiguous after translation: order, separators and
    # nearby connective words can change. Do not infer citation identity from
    # occurrence position or arbitrary adjacent context. Whole-entry links and
    # explicit figure/table/equation callouts use separate proven mappings.
    if len(old) != 1:
        return []
    position = new[0]
    indexes = sorted(set(target_indexes[position:position + len(needle)]))
    return _line_rects([target_chars[i] for i in indexes], matrix)


def match_localized_callout(source_chars, target_chars, selected, matrix):
    """Map explicit Fig./Table/Eq. + unchanged number to 图/表/式.

    Every source occurrence of that exact callout must belong to the same link
    action. This permits two producer annotations ("Fig." and "3") to become
    one clickable 图3, without making the surrounding sentence clickable.
    """
    source, source_indexes = '', []
    for index, char in enumerate(source_chars):
        text = unicodedata.normalize('NFKC', char.char_unicode or '')
        source += text
        source_indexes.extend([index] * len(text))
    target, target_indexes = _text_map(target_chars)
    pattern = re.compile(r'(?<![A-Za-z])(?P<label>Fig(?:ure)?|Table|Eq(?:uation)?)\.?\s*\(?(?P<number>\d+[a-z]?)\)?(?![A-Za-z0-9])', re.I)
    candidates = []
    for match in pattern.finditer(source):
        # Producers may link only the label and number in "(see Fig. 1)".
        # The enclosing parenthesis is prose punctuation, not part of either
        # semantic component of the callout. Require both components to be
        # source-attested by the same action; never expand the target hotspot.
        covered = {source_indexes[i] for name in ('label', 'number')
                   for i in range(*match.span(name)) if source[i].strip()}
        if covered <= selected:
            candidates.append(match)
    result = []
    for match in candidates:
        label = match['label'].lower()
        localized = ('图' if label.startswith('fig') else '表' if label == 'table' else '式')
        number = match['number']
        # Parentheses around equation numbers can be retained or localized.
        query = re.compile(re.escape(localized) + r'(?:\(' + re.escape(number)
                           + r'\)|' + re.escape(number) + r')(?![0-9a-z])', re.I)
        targets = list(query.finditer(target))
        equivalent = [item for item in pattern.finditer(source)
                      if item['number'] == number and item['label'].lower() == label]
        linked = [item for item in candidates if item['number'] == number and item['label'].lower() == label]
        if not targets or len(targets) != len(equivalent) or len(linked) != len(equivalent):
            continue
        for found in targets:
            indexes = sorted(set(target_indexes[found.start():found.end()]))
            result.extend(_line_rects([target_chars[i] for i in indexes], matrix))
    return result


def match_linked_native_script(source_chars, target_chars, selected, matrix):
    """Map one source-linked small native digit, never its body-text matches.

    The original font/glyph signature and smaller-size role must occur exactly
    once in both versions of this same paragraph. No page-wide number search,
    ordinal assignment, old rectangle or guessed footnote destination is used.
    """
    if len(selected) != 1:
        return []
    original = source_chars[next(iter(selected))]
    style = original.pdf_style
    if (not re.fullmatch(r'[0-9]', original.char_unicode or '') or style is None
            or not style.font_id or original.pdf_character_id is None):
        return []
    def candidates(chars):
        sizes = [c.pdf_style.font_size for c in chars if c.pdf_style is not None
                 and c.pdf_style.font_size and _normal(c.char_unicode or '')]
        if not sizes:
            return []
        body = median(sizes)
        return [c for c in chars if c.pdf_style is not None
                and c.char_unicode == original.char_unicode
                and c.pdf_style.font_id == style.font_id
                and c.pdf_character_id == original.pdf_character_id
                and c.pdf_style.font_size is not None
                and 0 < c.pdf_style.font_size < body * .85]
    source_matches, target_matches = candidates(source_chars), candidates(target_chars)
    if (len(source_matches) != 1 or source_matches[0] is not original
            or len(target_matches) != 1):
        return []
    return _line_rects(target_matches, matrix)


def _citation_entries(text):
    result = []
    for group in re.finditer(r'\([^()]{1,1000}\)', text):
        start = group.start() + 1
        for part in group[0][1:-1].split(';'):
            if re.fullmatch(r"[A-Z][A-Za-zÀ-ž.'’&\-]{1,},(?:19|20)\d{2}[a-z]?", part):
                result.append((start, start+len(part), part))
            start += len(part) + 1
    return result


def match_citation_fragment(source_chars, target_chars, selected, action_selected, matrix):
    """Use a complete unchanged author/year item to locate its linked part.

    A year alone is never assigned by occurrence order. If the same complete
    item occurs twice, every equivalent source fragment must have this same
    source action; otherwise it remains unmapped. Only the original linked
    substring becomes clickable, not the surrounding citation or prose.
    """
    source, source_indexes = _text_map(source_chars)
    target, target_indexes = _text_map(target_chars)
    offsets = [i for i, index in enumerate(source_indexes) if index in selected]
    if not offsets or offsets != list(range(min(offsets), max(offsets)+1)):
        return []
    start, end = offsets[0], offsets[-1]+1
    entries = _citation_entries(source)
    containers = [entry for entry in entries if entry[0] <= start and end <= entry[1]]
    if len(containers) != 1:
        return []
    anchor = containers[0]
    left, right = start-anchor[0], end-anchor[0]
    equivalent = [entry for entry in entries if entry[2] == anchor[2]]
    for entry in equivalent:
        needed = set(source_indexes[entry[0]+left:entry[0]+right])
        if not needed <= action_selected:
            return []
    targets = [entry for entry in _citation_entries(target) if entry[2] == anchor[2]]
    if len(targets) != len(equivalent):
        return []
    result = []
    for entry in targets:
        indexes = sorted(set(target_indexes[entry[0]+left:entry[0]+right]))
        result.extend(_line_rects([target_chars[i] for i in indexes], matrix))
    return result


def configure(source):
    global _source, _original_render_page
    import pymupdf
    from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
    _source = Path(source)
    _pages.clear()
    _mapped.clear()
    with pymupdf.open(_source) as pdf:
        for i, page in enumerate(pdf):
            rotation = page.rotation
            # In PyMuPDF 1.25 a rotated page's transformation_matrix omits
            # nonzero cropbox origins. Obtain the true PDF-to-crop transform
            # in a transient unrotated view; the source file is never saved.
            page.set_rotation(0)
            matrix = page.transformation_matrix
            page.set_rotation(rotation)
            _pages[i] = dict(matrix=matrix, links=_unrotated_links(page), source={})
    if _original_render_page is None:
        _original_render_page = Typesetting.render_page
        Typesetting.render_page = _render_page


def capture_source(page):
    info = _pages.get(page.page_number)
    if info is None:
        return
    records = {}
    for paragraph in page.pdf_paragraph:
        chars = _chars(paragraph)
        if not chars:
            continue
        associations = []
        for index, link in enumerate(info['links']):
            rect = link['from']
            selected = set()
            for i, char in enumerate(chars):
                if char.box is None or not _normal(char.char_unicode or ''):
                    continue
                box = _rect(char, info['matrix'])
                if (box.width > 0 and box.height > 0 and rect.x0 - .1 <= (box.x0 + box.x1) / 2 <= rect.x1 + .1
                        and (box & rect).get_area() >= box.get_area() * .2):
                    selected.add(i)
            if selected:
                visible = {i for i, char in enumerate(chars) if _normal(char.char_unicode or '')}
                associations.append((index, selected, len(selected) >= 4 and len(selected) >= len(visible) * .92))
        if associations:
            # PDF producers often make one annotation per physical line of a
            # linked reference. Together those lines cover one whole entry;
            # after reflow the entire same entry is still the correct target.
            groups = {}
            for index, selected, whole in associations:
                link = info['links'][index]
                key = (link['kind'], link.get('uri'), link.get('page'), link.get('nameddest'))
                groups.setdefault(key, set()).update(selected)
            associations = [(index, selected, whole or (len(groups[(info['links'][index]['kind'],
                                info['links'][index].get('uri'), info['links'][index].get('page'),
                                info['links'][index].get('nameddest'))]) >= max(4, len(visible) * .92)))
                            for index, selected, whole in associations]
            records[id(paragraph)] = (chars, associations)
    info['source'] = records
    # Copyright/index preservation moves original glyphs out of paragraphs.
    # Keep their identity and complete state instead of relying on byte-exact
    # raster equality (PDF serialization can round a coordinate by 0.0003 pt).
    standalone = []
    for index, link in enumerate(info['links']):
        rect = link['from']
        selected = []
        for char in getattr(page, 'pdf_character', []):
            if char.box is None or not _normal(char.char_unicode or ''):
                continue
            box = _rect(char, info['matrix'])
            if (box.width > 0 and box.height > 0
                    and rect.x0 - .1 <= (box.x0 + box.x1) / 2 <= rect.x1 + .1
                    and (box & rect).get_area() >= box.get_area() * .2):
                selected.append((char, deepcopy(char)))
        if selected:
            standalone.append((index, selected))
    info['standalone'] = standalone


def has_source_links(page, paragraph):
    """Read captured source associations before a paragraph ownership merge.

    Exact paragraph identity is usual. Existing source repairs may have moved
    original glyphs into another paragraph, so retain their selected-glyph
    identity as well; never infer a link from target text or nearby geometry.
    """
    info = _pages.get(page.page_number)
    if info is None:
        return False
    records = info.get('source', {})
    record = records.get(id(paragraph))
    if record is not None and record[1]:
        return True
    native_ids = {id(char) for char in _chars(paragraph)}
    return any(id(chars[index]) in native_ids
               for chars, associations in records.values()
               for _, selected, _ in associations for index in selected)


def _render_page(self, page):
    result = _original_render_page(self, page)
    capture_target(page)
    return result


def capture_target(page):
    info = _pages.get(page.page_number)
    if info is None:
        return
    mapped = {}
    standalone = {id(char): char for char in getattr(page, 'pdf_character', [])}
    for index, selected in info.get('standalone', []):
        # No text search or positional inference: every original selected glyph
        # must still exist, with its text, font, glyph id and geometry unchanged.
        if all(standalone.get(id(char)) is char and char == snapshot
               for char, snapshot in selected):
            rects = _line_rects([char for char, _ in selected], info['matrix'])
            if rects:
                mapped.setdefault(index, []).extend(rects)
    for paragraph in page.pdf_paragraph:
        record = info['source'].get(id(paragraph))
        if record is None:
            continue
        source_chars, associations = record
        target_chars = _chars(paragraph)
        action_selections = {}
        for index, selected, whole in associations:
            link = info['links'][index]
            key = (link['kind'], link.get('uri'), link.get('page'), link.get('nameddest'))
            action_selections.setdefault(key, set()).update(selected)
        for index, selected, whole in associations:
            rects = (_line_rects(target_chars, info['matrix']) if whole else
                     match_literal(source_chars, target_chars, selected, info['matrix']))
            if not rects:
                link = info['links'][index]
                key = (link['kind'], link.get('uri'), link.get('page'), link.get('nameddest'))
                rects = match_localized_callout(source_chars, target_chars, action_selections[key], info['matrix'])
            if not rects:
                rects = match_linked_native_script(source_chars, target_chars, selected, info['matrix'])
            if not rects:
                link = info['links'][index]
                key = (link['kind'], link.get('uri'), link.get('page'), link.get('nameddest'))
                if link['kind'] in (1, 4):  # PyMuPDF GoTo / named page destinations.
                    rects = match_citation_fragment(source_chars, target_chars, selected,
                                                   action_selections[key], info['matrix'])
            if rects:
                mapped.setdefault(index, []).extend(rects)
    with _lock:
        _mapped[page.page_number] = mapped


def write_mapping(path):
    # No document text, URLs or API settings are written to this temporary map.
    Path(path).write_text(json.dumps({'version': 1, 'pages': _mapped}) + '\n')


def _unrotated_links(page):
    # PyMuPDF get_links returns rotated rectangles, while insert_link takes
    # unrotated coordinates. Keep all internal source/IL/mapped rectangles in
    # the latter system and convert only at raster/API boundaries.
    return [dict(link, **{'from': link['from'] * page.derotation_matrix})
            for link in page.get_links()]


def _unrotated_bounds(page):
    return page.rect * page.derotation_matrix


def _same_visible_region(source, target, rect):
    import pymupdf
    if source.rect != target.rect or source.rotation != target.rotation or rect.is_empty or rect.is_infinite:
        return False
    area = (rect & _unrotated_bounds(source)) * source.rotation_matrix
    if area.is_empty:
        return False
    a = source.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=area, alpha=False)
    b = target.get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=area, alpha=False)
    return (a.width == b.width and a.height == b.height and a.samples == b.samples
            and any(value < 220 for value in a.samples))


def _action(link, page_count, target_page):
    import pymupdf
    if link['kind'] == pymupdf.LINK_URI:
        uri = link.get('uri', '')
        if re.match(r'^(https?://|mailto:)', uri, re.I):
            return dict(kind=pymupdf.LINK_URI, uri=uri)
    if link['kind'] in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
        page = link.get('page', -1)
        if isinstance(page, int) and 0 <= page < page_count:
            # Named destinations can expose PDF-space rather than MuPDF-space
            # coordinates. Page-level navigation is exact; an unproved y offset
            # is deliberately not reused after translation/reflow.
            return dict(kind=pymupdf.LINK_GOTO, page=target_page(page), to=pymupdf.Point(0, 0))
    return None


def restore_links(source_path, mono_path, dual_path, mapping_path):
    """Restore URI/GoTo links atomically before the worker publishes results."""
    import pymupdf
    mapping = json.loads(Path(mapping_path).read_text()) if Path(mapping_path).exists() else {'pages': {}}
    report = {'version': 1, 'destinationPrecision': 'page', 'translated': {}, 'bilingualOriginal': {}}
    with pymupdf.open(source_path) as source, pymupdf.open(mono_path) as mono, pymupdf.open(dual_path) as dual:
        if len(source) != len(mono) or len(dual) != len(source) * 2:
            raise ValueError('Unexpected bilingual page mapping')
        counts, original_counts = Counter(), Counter()
        for i, source_page in enumerate(source):
            target_page, original_side, target_side = mono[i], dual[2 * i], dual[2 * i + 1]
            # Upstream may preserve some links on one side. Rebuild once to
            # prevent duplicate annotations, using only verified source links.
            for page in (target_page, original_side, target_side):
                for existing in page.get_links():
                    page.delete_link(existing)
            seen = {id(target_page): set(), id(original_side): set(), id(target_side): set()}
            def insert(page, action, rects):
                number = 0
                for values in rects:
                    rect = pymupdf.Rect(values) & _unrotated_bounds(page)
                    if rect.is_empty or rect.is_infinite:
                        continue
                    key = (action['kind'], action.get('uri', ''), action.get('page', -1), *(round(v, 3) for v in rect))
                    if key in seen[id(page)]:
                        continue
                    # insert_link's inverse transform has the same cropbox
                    # issue when rotation is set. Create the annotation in
                    # unrotated crop coordinates, then restore page rotation.
                    rotation = page.rotation
                    try:
                        page.set_rotation(0)
                        page.insert_link(dict(action, **{'from': rect}))
                    finally:
                        page.set_rotation(rotation)
                    seen[id(page)].add(key)
                    number += 1
                return number
            for index, link in enumerate(_unrotated_links(source_page)):
                counts['sourceAnnotations'] += 1
                original_counts['sourceAnnotations'] += 1
                action = _action(link, len(source), lambda page: page)
                if action is None:
                    counts['unsupported'] += 1
                    original_counts['unsupported'] += 1
                    continue
                if link['from'].is_empty:
                    counts['emptySourceRectangles'] += 1
                    original_counts['emptySourceRectangles'] += 1
                    continue
                rects = mapping['pages'].get(str(i), {}).get(str(index), [])
                method = 'paragraphMapped'
                if not rects and _same_visible_region(source_page, target_page, link['from']):
                    rects, method = [list(link['from'])], 'unchangedVisibleRegion'
                if rects:
                    count = insert(target_page, action, rects)
                    counts['restoredSourceAnnotations'] += 1
                    counts[method] += 1
                    counts['outputRectangles'] += count
                    dual_action = _action(link, len(source), lambda page: page * 2 + 1)
                    insert(target_side, dual_action, rects)
                else:
                    counts['unmapped'] += 1
                if _same_visible_region(source_page, original_side, link['from']):
                    original_counts['outputRectangles'] += insert(
                        original_side, _action(link, len(source), lambda page: page * 2), [list(link['from'])])
                    original_counts['restoredSourceAnnotations'] += 1
                else:
                    original_counts['unmapped'] += 1
        report['translated'], report['bilingualOriginal'] = dict(counts), dict(original_counts)
        for document, path in ((mono, mono_path), (dual, dual_path)):
            temporary = Path(path).with_suffix('.links.pdf')
            document.save(temporary, garbage=3, deflate=True)
            temporary.replace(path)
    return report
