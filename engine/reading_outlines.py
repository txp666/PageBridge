"""Identify and remove vector lettering with independent PDF glyph geometry.

A PDF can store search text in non-painting glyph programs and draw the visible
letters as paths. A path is replaceable only if its contours match a unique
text run. The page is tested on a copy before any original artwork is cleared.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re

import pymupdf


@dataclass(frozen=True)
class OutlineRun:
    drawing: dict
    block_ids: frozenset[str]
    ink: tuple[pymupdf.Rect, ...]
    removal_boxes: tuple[pymupdf.Rect, ...]

    @property
    def bounds(self):
        return pymupdf.Rect(self.drawing["rect"])


@dataclass(frozen=True)
class Lettering:
    trace: dict
    drawing: dict
    ink: tuple[pymupdf.Rect, ...]
    removal_boxes: tuple[pymupdf.Rect, ...]


def _blank_glyph_fonts(page):
    """Read metric-only Type 3 programs from the font's actual encoding."""
    doc = page.parent
    fonts = {entry[0]: entry for entry in page.get_fonts(full=True) if entry[2] == "Type3"}
    result = defaultdict(list)
    for xref, _, _, base, *_ in fonts.values():
        name = base or "Unnamed-T3"
        matrix = doc.xref_get_key(xref, "FontMatrix")
        encoding = doc.xref_get_key(xref, "Encoding/Differences")
        if matrix[0] != "array" or encoding[0] != "array":
            continue
        try:
            values = tuple(map(float, matrix[1][1:-1].split()))
            if len(values) != 6 or not all(math.isfinite(v) for v in values):
                continue
            transform = pymupdf.Matrix(*values)
            metrics = {}
            code = 0
            for token in re.findall(r"/[^ /\[\]]+|\d+", encoding[1]):
                if not token.startswith("/"):
                    code = int(token)
                    continue
                ref = doc.xref_get_key(xref, "CharProcs/" + token[1:])
                program = doc.xref_stream(int(ref[1].split()[0])).split() if ref[0] == "xref" else ()
                if len(program) == 7 and program[-1] == b"d1":
                    bounds = tuple(map(float, program[2:6]))
                    if all(math.isfinite(v) for v in bounds):
                        metrics[code] = pymupdf.Rect(bounds).normalize() * transform
                code += 1
            if metrics:
                result[name].append(metrics)
        except (RuntimeError, ValueError):
            continue
    return result


def _curve_limits(values):
    """Bounds of the cubic itself, without the excess control point area."""
    a, b, c, d = values
    u, v, w = b - a, c - b, d - c
    quadratic, linear = u - 2 * v + w, 2 * (v - u)
    roots = []
    if abs(quadratic) < 1e-7:
        if abs(linear) > 1e-7:
            roots.append(-u / linear)
    else:
        discriminant = linear * linear - 4 * quadratic * u
        if discriminant >= 0:
            root = math.sqrt(discriminant)
            roots = [(-linear + root) / (2 * quadratic), (-linear - root) / (2 * quadratic)]
    extremes = [a, d]
    for t in roots:
        if 0 < t < 1:
            s = 1 - t
            extremes.append(s**3 * a + 3 * s**2 * t * b + 3 * s * t**2 * c + t**3 * d)
    return min(extremes), max(extremes)


def _contours(drawing):
    bounds = []
    controls = []
    last = None
    for operator, *points in drawing["items"]:
        if operator in {"l", "c"}:
            limits = _curve_limits if operator == "c" else lambda values: (min(values), max(values))
            xs, ys = zip(*points)
            x0, x1 = limits(xs)
            y0, y1 = limits(ys)
            rect = pymupdf.Rect(x0, y0, x1, y1)
            control = pymupdf.Rect(min(xs), min(ys), max(xs), max(ys))
            if bounds and last == points[0]:
                _include_edges(bounds[-1], rect)
                _include_edges(controls[-1], control)
            else:
                bounds.append(rect)
                controls.append(control)
            last = points[-1]
        else:
            last = None
            if operator == "re":
                rect = pymupdf.Rect(points[0])
            elif operator == "qu":
                rect = pymupdf.Quad(points[0]).rect
            else:
                return []
            bounds.append(rect)
            controls.append(rect)
    return [(rect, control) for rect, control in zip(bounds, controls) if not rect.is_empty]


def _include_edges(rect, other):
    # A straight segment has a zero-area box but its endpoints still matter.
    rect.x0 = min(rect.x0, other.x0)
    rect.y0 = min(rect.y0, other.y0)
    rect.x1 = max(rect.x1, other.x1)
    rect.y1 = max(rect.y1, other.y1)


def _envelope(rects):
    bounds = pymupdf.Rect()
    for rect in rects:
        bounds |= rect
    return bounds


def _edge_distance(a, b):
    return max(abs(x - y) for x, y in zip(a, b))


def _matched_lines(drawing, ink, lines, baselines, size):
    # Tolerances are relative to glyph size. PDF text metrics and outline
    # extrema differ slightly. A large object occupying the same text box is
    # still rejected because its contours do not cover the individual glyphs.
    margin = size * .12
    padding = (-margin, -margin, margin, margin)
    contours = _contours(drawing)
    if len(contours) < len(ink) * .85:
        return ()
    cell = max(1, size)
    locations = defaultdict(list)
    for index, glyph in enumerate(ink):
        padded = glyph + padding
        for x in range(math.floor(padded.x0 / cell), math.floor(padded.x1 / cell) + 1):
            for y in range(math.floor(padded.y0 / cell), math.floor(padded.y1 / cell) + 1):
                locations[x, y].append((index, padded))
    matched = defaultdict(list)
    controls = defaultdict(list)
    unmatched = []
    for contour, control in contours:
        center = (contour.tl + contour.br) / 2
        near = [i for i, padded in locations[math.floor(center.x / cell), math.floor(center.y / cell)]
                if padded.contains(contour)]
        if near:
            best = max(near, key=lambda i: (ink[i] & contour).get_area())
            matched[best].append(contour)
            controls[baselines[best]].append(control)
        else:
            unmatched.append((contour, control))
    # A declared glyph box can be larger than the visible ink. Require
    # substantial coverage of each box rather than equality of its edges.
    aligned = sum((_envelope(parts) & ink[i]).get_area() >= ink[i].get_area() * .7
                  for i, parts in matched.items())
    if aligned < len(ink) * .85 or sum(r.get_area() for r, _ in unmatched) > sum(r.get_area() for r in ink) * .02:
        return ()
    # Small accents or rules can be painted as part of a line of text. An
    # unmatched contour spilling outside its line invalidates the whole draw.
    # The advance box can end before a punctuation mark or underline.
    # Allow its horizontal overhang within the text line's vertical band.
    line_padding = (-size * .6, -margin * 2, size * .6, margin * 2)
    for rect, control in unmatched:
        line = next((index for index, box in lines.items() if (box + line_padding).contains(rect)), None)
        if line is None:
            return ()
        controls[line].append(control)
    # A single fill can paint multiple interleaved paragraphs. Clear only
    # its separate lines. MuPDF's coverage test also includes control points.
    return tuple(_envelope(parts) + (-.12, -.12, .12, .12) for parts in controls.values())


def _unsupported_direction(page, run):
    direction = pymupdf.Point(run["dir"])
    matrix = page.rotation_matrix
    displayed = direction * pymupdf.Matrix(matrix.a, matrix.b, matrix.c, matrix.d, 0, 0)
    return abs(displayed.y) > .01 or displayed.x < .99 or run["wmode"] != 0


def _ink_boxes(run, font):
    size = run["size"]
    dx, dy = run["dir"]
    ink, lines, baselines = [], defaultdict(list), []
    for code, gid, (x, y), bounds in run["chars"]:
        if gid < 0 or not 0 <= code <= 0x10ffff or chr(code).isspace():
            continue
        if gid not in font:
            return [], {}, []
        box = font[gid] * pymupdf.Matrix(size * dx, size * dy, size * dy, -size * dx, x, y)
        if not box.is_empty:
            baseline = round((y * dx - x * dy) * 10)
            ink.append(box)
            baselines.append(baseline)
            lines[baseline].append(pymupdf.Rect(bounds) | box)
    return ink, {baseline: _envelope(line) for baseline, line in lines.items()}, baselines


def match_lettering(page, traces=None):
    """Find search runs whose glyphs have independently identifiable vector ink."""
    fonts = _blank_glyph_fonts(page)
    if not fonts:
        return {}
    traces = page.get_texttrace() if traces is None else traces
    drawings = [d for d in page.get_cdrawings() if d["type"] == "f"]
    matched = {}
    for index, run in enumerate(traces):
        size = run["size"]
        if not math.isfinite(size) or size <= 0 or _unsupported_direction(page, run):
            continue
        matches = {}
        for font in fonts.get(run["font"], ()):
            ink, bands, baselines = _ink_boxes(run, font)
            if not ink:
                continue
            envelope = _envelope(ink)
            for drawing in drawings:
                if _edge_distance(drawing["rect"], envelope) <= size * .35:
                    boxes = _matched_lines(drawing, ink, bands, baselines, size)
                    if boxes:
                        matches[drawing["seqno"]] = Lettering(run, drawing, tuple(ink), boxes)
        # Two resources sometimes use the same nominal font name. Match the
        # drawing by glyph positions instead of requiring a unique font name.
        if len(matches) == 1:
            matched[index] = next(iter(matches.values()))
    claims = Counter(item.drawing["seqno"] for item in matched.values())
    return {index: item for index, item in matched.items() if claims[item.drawing["seqno"]] == 1}


def lettering_lines(page):
    """Positioned searchable lines for fonts omitted by standard extraction.

    Only expose runs with independently matched paint. Invisible OCR on a
    photograph supplies search hits but cannot establish replaceable regions.
    """
    rotation, origin = page.rotation_matrix, page.rect.tl
    for item in match_lettering(page).values():
        groups = defaultdict(list)
        for code, _, point, bounds in item.trace["chars"]:
            if not 0 <= code <= 0x10ffff:
                continue
            baseline = pymupdf.Point(point) * rotation - origin
            rect = pymupdf.Rect(bounds) * rotation
            rect -= (origin.x, origin.y, origin.x, origin.y)
            groups[round(baseline.y, 2)].append({"c": chr(code), "bbox": list(rect)})
        yield [line for line in groups.values() if "".join(c["c"] for c in line).strip()]


def _find_runs(page, blocks):
    boxes = {b["id"]: pymupdf.Rect(b["bbox"]) for b in blocks}
    block_lookup = {block["id"]: block for block in blocks}
    traces = page.get_texttrace()
    matched = match_lettering(page, traces)
    if not matched:
        return []
    rotation, origin = page.rotation_matrix, page.rect.tl
    approved, rejected = [], set()
    for index, run in enumerate(traces):
        assigned, unassigned = set(), False
        for code, gid, _, bounds in run["chars"]:
            if gid < 0 or not 0 <= code <= 0x10ffff or chr(code).isspace():
                continue
            rect = pymupdf.Rect(bounds) * rotation
            center = (rect.tl + rect.br) / 2 - origin
            candidates = [identifier for identifier, box in boxes.items() if box.contains(center)]
            if len(candidates) == 1:
                assigned.add(candidates[0])
            else:
                # Outline fills are atomic. An unassigned glyph can belong to
                # text that has to stay in the original page.
                unassigned = True
                assigned.update(candidates)
        if not assigned:
            continue
        if index in matched and not unassigned and not any(block_lookup[k].get("inlineMath") for k in assigned):
            item = matched[index]
            approved.append(OutlineRun(item.drawing, frozenset(assigned), item.ink, item.removal_boxes))
        else:
            rejected.update(assigned)
    colors = defaultdict(set)
    for run in approved:
        for identifier in run.block_ids:
            colors[identifier].add(tuple(run.drawing["fill"]))
    rejected.update(k for k, values in colors.items() if len(values) > 1)
    # If one part of a passage is uncertain, keep all source ink for it.
    while True:
        linked = rejected.union(*(run.block_ids for run in approved if run.block_ids & rejected))
        if linked == rejected:
            break
        rejected = linked
    return [run for run in approved if not run.block_ids & rejected]


def clear_outlines(page, runs):
    """Apply a plan already verified on a copy of this page."""
    if not runs:
        return
    for run in runs:
        for box in run.removal_boxes:
            page.add_redact_annot(box, fill=False, cross_out=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
                          text=pymupdf.PDF_REDACT_TEXT_NONE)


def _other_paint(page, skipped=()):
    paths = [{key: value for key, value in entry.items() if key != "seqno"}
             for entry in page.get_cdrawings() if entry["seqno"] not in skipped]
    # The PDF writer can retain a fill operator with an empty path.
    commands = [entry for index, entry in enumerate(page.get_bboxlog()) if index not in skipped
                and (entry[0] != "fill-path" or not pymupdf.Rect(entry[1]).is_empty)]
    return paths, commands


def _same_paint(expected, actual):
    if isinstance(expected, float) and isinstance(actual, float):
        return math.isclose(expected, actual, rel_tol=0, abs_tol=.001)
    if isinstance(expected, (list, tuple)) and isinstance(actual, type(expected)):
        return len(expected) == len(actual) and all(_same_paint(a, b) for a, b in zip(expected, actual))
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(_same_paint(v, actual[k]) for k, v in expected.items())
    return expected == actual


def _changes_within(before, after, bounds):
    if (before.width, before.height, before.n) != (after.width, after.height, after.n):
        return False
    orig, cleared = before.samples_mv, after.samples_mv
    rows = [[] for _ in range(before.height)]
    for number, rect in enumerate(bounds):
        for y in range(rect.y0, rect.y1):
            rows[y].append((rect.x0, rect.x1, number))
    changed = set()
    for y, regions in enumerate(rows):
        offset = y * before.stride
        def fixed(x0, x1):
            start, end = offset + x0 * before.n, offset + x1 * before.n
            return orig[start:end] == cleared[start:end]
        right = 0
        for x0, x1, number in sorted(regions):
            if x0 > right and not fixed(right, x0):
                return False
            if number not in changed and not fixed(x0, x1):
                changed.add(number)
            right = max(right, x1)
        if not fixed(right, before.width):
            return False
    return len(changed) == len(bounds)


def _verified(page, runs):
    # Verify preservation of all other paint operations and the rendering
    # outside the removed paths. Neither the source file nor the caller's
    # working page has been edited at this point.
    with pymupdf.open() as copy:
        copy.insert_pdf(page.parent, from_page=page.number, to_page=page.number, links=False, annots=False)
        candidate = copy[0]
        expected = _other_paint(candidate, {run.drawing["seqno"] for run in runs})
        scale = min(1, 1800 / max(candidate.rect.width, candidate.rect.height))
        origin = candidate.rect.tl
        matrix = pymupdf.Matrix(scale, scale).pretranslate(-origin.x, -origin.y)
        before = candidate.get_pixmap(matrix=matrix, annots=False)
        clear_outlines(candidate, runs)
        if not _same_paint(expected, _other_paint(candidate)):
            return False
        after = candidate.get_pixmap(matrix=matrix, annots=False)
        transform = candidate.rotation_matrix * matrix
        regions = [((run.bounds * transform) + (-2, -2, 2, 2)).irect & before.irect for run in runs]
        if not _changes_within(before, after, regions):
            return False
        orig, cleared = before.samples_mv, after.samples_mv
        for run in runs:
            for glyph in run.ink:
                box = ((glyph * transform) + (-1, -1, 1, 1)).irect & before.irect
                if box.is_empty:
                    return False
                width = box.width * before.n
                offsets = (y * before.stride + box.x0 * before.n for y in range(box.y0, box.y1))
                if not any(orig[i:i + width] != cleared[i:i + width] for i in offsets):
                    return False
        return True


def _components(runs):
    """Keep all drawing operations that supply the same paragraph together."""
    pending = list(runs)
    while pending:
        group = [pending.pop(0)]
        block_ids = set(group[0].block_ids)
        while True:
            related = [run for run in pending if run.block_ids & block_ids]
            if not related:
                break
            pending = [run for run in pending if not run.block_ids & block_ids]
            group.extend(related)
            block_ids.update(*(run.block_ids for run in related))
        yield group


def verified_outlines(page, blocks):
    if not blocks:
        return []
    try:
        if not any(font[2] == "Type3" for font in page.get_fonts()) or any(page.annots(types=[pymupdf.PDF_ANNOT_REDACT])):
            return []
        runs = _find_runs(page, blocks)
        if not runs or _verified(page, runs):
            return runs
        groups = list(_components(runs))
        if len(groups) == 1:
            return []
        accepted = [run for group in groups if _verified(page, group) for run in group]
        return accepted if accepted and _verified(page, accepted) else []
    except (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError):
        return []


def outline_colors(runs):
    return {k: [round(channel * 255) for channel in run.drawing["fill"]]
            for run in runs for k in run.block_ids}
