"""Render original artwork after removing only positioned, visible PDF text.

The immutable original remains the source for figures and formula crops. A
temporary in-memory copy removes text without painting rectangles over photos,
watermarks, fills or lines. Image-only scans keep their existing rendering path.
"""
from __future__ import annotations

import base64
from collections import Counter
import math
from typing import NamedTuple

import pymupdf

from reading_outlines import clear_outlines, outline_colors, verified_outlines
from reading_layout import cell_layouts, line_right_edges, source_rect as _rect
from reading_tables import table_cells

REDACTION_BATCH = 25


def _visible_mask_box(mask):
    """Ignore transparent asset padding when comparing a shadow to its text."""
    if mask.n != 1 or mask.is_unicolor:
        return None
    samples = mask.samples
    left, top, right, bottom = mask.width, mask.height, 0, 0
    for y in range(mask.height):
        row = samples[y * mask.stride:y * mask.stride + mask.width]
        if not row.strip(b"\0"):
            continue
        top = min(top, y)
        bottom = y + 1
        left = min(left, len(row) - len(row.lstrip(b"\0")))
        right = max(right, len(row.rstrip(b"\0")))
    if right <= left or bottom <= top:
        return None
    return pymupdf.Rect(left / mask.width, top / mask.height,
                        right / mask.width, bottom / mask.height)


def _paired_with_line(shadow, line):
    # Glyph ascenders/descenders and a small blur pad differ from line metrics.
    # The observed 44pt NASA title needs 12.1pt; do not use whole asset padding
    # (its transparent margins would otherwise need more than 30pt).
    margin = max(2.0, min(16.0, line.height / 3))
    return ((line + (-margin, -margin, margin, margin)).contains(shadow)
            and (shadow + (-margin, -margin, margin, margin)).contains(line))


def remove_text_shadows(page, selected_native_boxes, keep=()):
    """Delete uniform-color soft-mask images paired with selected native lines.

    Boxes are line/span rectangles in unrotated native PDF points, not whole
    paragraph or MinerU boxes. Photos, vector decorations and logo shadows
    without a selected line stay. Each occurrence of a reused image must match
    a selected line before its resource is removed; xrefs in keep (shown on
    other pages of the same document) are never removed. Returns the removed xrefs.
    """
    lines = [rect for rect in map(pymupdf.Rect, selected_native_boxes) if not rect.is_empty and not rect.is_infinite]
    if not lines:
        return []
    images = {xref: smask for xref, smask, *_ in page.get_images(full=True) if xref > 0 and smask > 0}
    removed = []
    for xref, smask in images.items():
        if xref in keep:
            continue
        placements = page.get_image_rects(xref, transform=True)
        if not placements:
            continue
        try:
            if not pymupdf.Pixmap(page.parent, xref).is_unicolor:
                continue
            mask_box = _visible_mask_box(pymupdf.Pixmap(page.parent, smask))
        except (RuntimeError, ValueError):
            continue
        if mask_box is None:
            continue
        if all(any(_paired_with_line(mask_box * transform, line) for line in lines) for _, transform in placements):
            page.delete_image(xref)
            removed.append(xref)
    return removed


def coalesce_text_redactions(source, rectangles):
    """Merge touching glyph boxes without adding any redacted area."""
    numbers = {tuple(bounds) for span in source.get_texttrace() for value, _, _, bounds in span["chars"]
               if 0 <= value <= 0x10FFFF and chr(value).isnumeric()}
    result = []
    previous_numeric = False
    for rect in sorted(rectangles, key=lambda r: (r.y0, r.y1, r.x0, r.x1)):
        numeric = tuple(rect) in numbers
        # Equal vertical bounds and touching horizontal intervals make this
        # exactly the original union. Never bridge spaces, unselected text or
        # another line; leave numerical glyphs as independent rectangles.
        if (result and not numeric and not previous_numeric
                and result[-1].y0 == rect.y0 and result[-1].y1 == rect.y1
                and rect.x0 <= result[-1].x1 and rect.x1 >= result[-1].x0):
            result[-1] |= rect
        else:
            result.append(pymupdf.Rect(rect))
        previous_numeric = numeric
    return result


def translated_blocks(page_result):
    """Positioned text whose translation differs from its source."""
    return [block for block in page_result["blocks"]
            if block.get("kind") in {"text", "heading", "list"}
            and block.get("status") == "translated" and _rect(block.get("bbox"))
            and isinstance(block.get("translatedText"), str)
            and block["translatedText"].strip() != str(block.get("sourceText", "")).strip()]


class SourceGlyph(NamedTuple):
    native: pymupdf.Rect
    local: pymupdf.Rect
    rgb: tuple
    size: float
    bold: bool | None


def _cff_weight(document, xref):
    """Some PDF producers strip all weight information from embedded Type 1 subsets.
    Measure a vertical stem halfway up the actual glyph in em units."""
    data = document.extract_font(xref)[3]
    if not data:
        return None
    font = pymupdf.Font(fontbuffer=data)
    m = pymupdf.mupdf
    for char in "lIi":
        gid = font.has_glyph(ord(char))
        if gid:
            mask = pymupdf.Pixmap(m.fz_render_glyph_pixmap(font.this, gid, m.FzMatrix(100, 0, 0, -100, 0, 0),
                                                            m.FzIrect(-200, -200, 200, 200), 8))
            row = (mask.height // 2) * mask.stride
            if mask.width and mask.height:
                return sum(mask.samples[row:row + mask.width]) >= 12 * 255
    return None


def source_bold(glyphs):
    weights = Counter(g.bold for g in glyphs if g.bold is not None)
    return weights.most_common(1)[0][0] if weights else None


def visible_glyphs(page):
    """Visible horizontal glyphs and their native span boxes."""
    rotation, origin = page.rotation_matrix, page.rect.tl
    glyphs, spans = [], []
    cff = {name.split("+")[-1]: xref for xref, ext, _, name, *_ in page.get_fonts() if ext == "cff"}
    weights = {}
    for span in page.get_texttrace():
        if span["type"] == 3 or span.get("opacity", 1) <= 0:
            continue  # A hidden OCR layer cannot remove pixels in a scan.
        direction = pymupdf.Point(span["dir"]) * pymupdf.Matrix(rotation.a, rotation.b, rotation.c, rotation.d, 0, 0)
        if abs(direction.y) > .01 or direction.x < .99:
            continue  # Rotated captions stay on the original page.
        spans.append(pymupdf.Rect(span["bbox"]))
        color = span.get("color", (0, 0, 0))
        if len(color) == 1:
            color = color * 3
        if len(color) != 3:
            color = (0, 0, 0)
        rgb = tuple(round(max(0, min(1, channel)) * 255) for channel in color)
        name = span["font"]
        bold = bool(span["flags"] & pymupdf.TEXT_FONT_BOLD)
        if not bold and name in cff:
            if name not in weights:
                try:
                    weights[name] = _cff_weight(page.parent, cff[name])
                except (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError):
                    weights[name] = None
            bold = weights[name]
        for unicode_value, _, _, bounds in span["chars"]:
            if not 0 <= unicode_value <= 0x10FFFF or chr(unicode_value).isspace():
                continue
            native = pymupdf.Rect(bounds)
            local = native * rotation
            local += (-origin.x, -origin.y, -origin.x, -origin.y)
            if not local.is_empty:
                glyphs.append(SourceGlyph(native, local, rgb, span["size"], bold))
    return glyphs, spans


def glyph_owners(glyphs, blocks):
    """Assign a glyph to one paragraph, preferring a smaller actual region
    when the parser has overlapping text boxes. This is geometry, not a
    content-quality or translation acceptance rule."""
    boxes = {b["id"]: pymupdf.Rect(b["bbox"]) for b in blocks}
    owners = {b["id"]: [] for b in blocks}
    for glyph in glyphs:
        center = (glyph.local.tl + glyph.local.br) / 2
        matches = [b for b in blocks if boxes[b["id"]].contains(center)]
        if matches:
            owner = min(matches, key=lambda b: boxes[b["id"]].get_area())
            owners[owner["id"]].append(glyph)
    return owners


def spans_in(page, spans, rects):
    """Native span boxes whose page-local center lies in one of these regions."""
    rotation, origin = page.rotation_matrix, page.rect.tl
    return [box for box in spans if any(
        rect.contains(((box * rotation).tl + (box * rotation).br) / 2 - origin) for rect in rects)]


def remove_text(page, glyph_boxes, span_boxes, formula_boxes, keep=()):
    """Clear native glyphs, their paired shadows and line art inside moved inline formulas."""
    remove_text_shadows(page, span_boxes, keep)
    # Formula attachments move with the translated sentence. Remove the
    # original fraction bars as well, but only paths fully inside their
    # explicit source boxes; a page-wide background shape is not covered.
    origin = page.rect.tl
    for box in formula_boxes:
        local = pymupdf.Rect(box) + (-.75, -.75, .75, .75)
        displayed = local + (origin.x, origin.y, origin.x, origin.y)
        page.add_redact_annot(displayed * page.derotation_matrix, fill=False, cross_out=False)
    if formula_boxes:
        page.apply_redactions(images=0, graphics=1, text=0)
    rects = coalesce_text_redactions(page, glyph_boxes)
    # PyMuPDF scans every existing annotation when adding or applying one, so a
    # dense page's word boxes cost quadratic time in one pass; batches do not.
    for start in range(0, len(rects), REDACTION_BATCH):
        for rect in rects[start:start + REDACTION_BATCH]:
            # No fill: the image/vector artwork under each glyph remains real
            # source artwork, not a sampled flat approximation of its color.
            page.add_redact_annot(rect, fill=False, cross_out=False)
        page.apply_redactions(images=0, graphics=0, text=0)


def residual_text_fills(before, after, blocks, owners, protected, scale, load_native_mask, uncleared=None):
    """Find duplicated raster/outline text that native redaction cannot clear.

    A searchable scan may paint text both as glyphs and as pixels. Only use the
    scan-style fill when most source ink survives, the surrounding paper is
    uniform, and the remaining ink follows the actual native glyph shapes.
    Photos and protected source regions keep their actual background.
    """
    original, cleared = before.samples_mv, after.samples_mv

    def pixel(data, x, y):
        offset = y * before.stride + x * 3
        return data[offset:offset + 3]

    def close(a, b, tolerance=24):
        return max(abs(x - y) for x, y in zip(a, b)) <= tolerance

    def bounds(rect):
        return (max(0, int(rect.x0 * scale)), max(0, int(rect.y0 * scale)),
                min(before.width, math.ceil(rect.x1 * scale)), min(before.height, math.ceil(rect.y1 * scale)))

    fills = []
    native_mask = None
    page_area = pymupdf.Rect(0, 0, before.width / scale, before.height / scale)
    for block in blocks:
        color = block.get("backgroundColor")
        if (not isinstance(color, list) or len(color) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 255 for v in color)):
            continue
        background = tuple(round(v) for v in color)
        foreground = block["foregroundColor"]
        delta = [b - f for b, f in zip(background, foreground)]
        pivot = max(range(3), key=lambda i: abs(delta[i]))
        if abs(delta[pivot]) < 32:
            continue
        box = (pymupdf.Rect(block["bbox"]) + (-.75, -.75, .75, .75)) & page_area
        if any(box.intersects(rect) for rect in protected):
            continue
        ink = remaining = 0
        for glyph in owners[block["id"]]:
            glyph_ink = glyph_remaining = 0
            left, top, right, bottom = bounds(glyph.local)
            for y in range(top, bottom, max(1, (bottom - top) // 10)):
                for x in range(left, right, max(1, (right - left) // 10)):
                    if close(pixel(original, x, y), foreground):
                        ink += 1
                        glyph_ink += 1
                        # A lower-resolution duplicate is antialiased: its
                        # visible grey edges need not equal the native ink RGB.
                        survives = (background[pivot] - pixel(cleared, x, y)[pivot]) / delta[pivot] > .25
                        remaining += survives
                        glyph_remaining += close(pixel(cleared, x, y), foreground)
            if uncleared is not None and glyph_ink >= 4 and glyph_remaining >= glyph_ink * .8:
                uncleared.add(block["id"])
        if ink < 16 or remaining < ink * .8:
            continue
        left, top, right, bottom = bounds((box + (-1.5, -1.5, 1.5, 1.5)) & page_area)
        perimeter = ([(x, y) for x in range(left, right, max(1, (right - left) // 32)) for y in (top, bottom - 1)]
                     + [(x, y) for y in range(top, bottom, max(1, (bottom - top) // 32)) for x in (left, right - 1)])
        if not perimeter or sum(close(pixel(cleared, x, y), background, 16) for x, y in perimeter) < .95 * len(perimeter):
            continue
        left, top, right, bottom = bounds(box)
        step = max(1, math.ceil(math.sqrt((right - left) * (bottom - top) / 4096)))
        samples = paper = other = 0
        for y in range(top, bottom, step):
            for x in range(left, right, step):
                value = pixel(cleared, x, y)
                fraction = max(0, min(1, (value[pivot] - foreground[pivot]) / delta[pivot]))
                other += not close(value, [f + fraction * d for f, d in zip(foreground, delta)], 16)
                paper += close(value, background, 16)
                samples += 1
        if not samples or paper < .1 * samples or other > .005 * samples:
            continue
        # A monochrome photo or diagram can also use only ink/paper colours.
        # Verify that its surviving pixels follow letters, including their
        # counters and spaces, rather than erasing arbitrary two-colour artwork.
        if native_mask is None:
            native_mask = load_native_mask()
        if native_mask is None or (native_mask.width, native_mask.height) != (before.width, before.height):
            continue
        alpha = native_mask.samples_mv
        radius = max(1, math.ceil(scale * .75))
        unpaired = residual = 0
        for y in range(top, bottom, step):
            for x in range(left, right, step):
                if (background[pivot] - pixel(cleared, x, y)[pivot]) / delta[pivot] <= .25:
                    continue
                residual += 1
                nearby = any(alpha[yy * native_mask.stride + xx * 4 + 3] > 16
                             for yy in range(max(0, y - radius), min(before.height, y + radius + 1))
                             for xx in range(max(0, x - radius), min(before.width, x + radius + 1)))
                unpaired += not nearby
        if not residual or unpaired > .02 * residual:
            continue
        block["bbox"] = list(box)
        fills.append(block)
    return fills


def region_changed(before, after, rect, scale):
    """Require visible evidence of removal, independently of PDF font or producer."""
    box = (pymupdf.Rect(rect) * pymupdf.Matrix(scale, scale)).irect & before.irect
    if box.is_empty:
        return False
    original, cleared = before.samples_mv, after.samples_mv
    for y in range(box.y0, box.y1):
        start, end = y * before.stride + box.x0 * 3, y * before.stride + box.x1 * 3
        if original[start:end] != cleared[start:end]:
            return True
    return False


def _place_on_flat_paper(pixmap, matrix, page_result, translated):
    """Keep the OCR overlay for detected text on a solid scanned background."""
    placed, filled = [], []
    for block in translated:
        box = pymupdf.Rect(block["bbox"])
        pixels = (box * matrix).irect & pixmap.irect
        if pixels.is_empty:
            continue
        coverage, paper = pixmap.color_topusage(clip=pixels)
        if coverage < .8:
            continue
        if coverage < 1:
            if any(other is not block and (area := _rect(other.get("bbox"))) is not None and box.intersects(area)
                   for other in page_result["blocks"]):
                continue
            border = (pixels + (-1, -1, 1, 1)) & pixmap.irect
            sides = (pymupdf.IRect(border.x0, border.y0, border.x1, border.y0 + 1),
                     pymupdf.IRect(border.x0, border.y1 - 1, border.x1, border.y1),
                     pymupdf.IRect(border.x0, border.y0, border.x0 + 1, border.y1),
                     pymupdf.IRect(border.x1 - 1, border.y0, border.x1, border.y1))
            if any(pixmap.color_topusage(clip=side) != (1, paper) for side in sides):
                continue
            pixmap.set_rect(pixels, paper)
            filled.append(block["id"])
        block["backgroundColor"] = list(paper)
        dark = sum(value * weight for value, weight in zip(paper, (.299, .587, .114))) < 128
        block["foregroundColor"] = [255 if dark else 0] * 3
        placed.append(block["id"])
    return placed, filled


def add_page_background(input_source, page_result, *, include_image=True):
    """Prepare an atomic backdrop and return the verified outline removal plan.

    Export can use the same preparation from its immutable source bytes,
    without serializing a PNG that it will never display.
    """
    def open_source():
        if isinstance(input_source, bytes):
            return pymupdf.open(stream=input_source, filetype="pdf")
        return pymupdf.open(input_source)

    page_result.pop("backgroundImage", None)
    page_result.pop("backgroundBlocks", None)
    page_result.pop("backgroundFillBlocks", None)
    page_result["layoutRightEdges"] = {}
    page_result["cellLayouts"] = {}
    translated = translated_blocks(page_result)
    if not translated:
        return []
    with open_source() as pdf:
        source = pdf[page_result["pageNumber"] - 1]
        cells = table_cells(source, page_result["blocks"])
        origin = source.rect.tl
        scale = min(1800 / source.rect.width, 2400 / source.rect.height)
        matrix = pymupdf.Matrix(scale, scale).pretranslate(-origin.x, -origin.y)
        visible, native_spans = visible_glyphs(source)
        if not visible:
            pixmap = source.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
            ids, fills = _place_on_flat_paper(pixmap, pymupdf.Matrix(scale, scale), page_result, translated)
            if ids and include_image:
                page_result["backgroundImage"] = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
            if fills:
                page_result["backgroundFillBlocks"] = fills
            page_result["backgroundBlocks"] = ids
            page_result["layoutRightEdges"] = line_right_edges(page_result, pixmap)
            page_result["cellLayouts"] = cell_layouts(page_result, pixmap, cells)
            page_result.pop("decorations", None)
            return []
        boxes = {b["id"]: pymupdf.Rect(b["bbox"]) for b in translated}
        owners = glyph_owners(visible, translated)
        outlines = verified_outlines(source, [block for block in translated if owners[block["id"]]])
        outlined = outline_colors(outlines)

        redactions, background_ids = [], []
        for block in translated:
            chars = owners[block["id"]]
            if not chars:
                continue
            if block["id"] in outlined:
                # The search layer does not paint. Its nominal color and
                # weight cannot describe the PDF paths that were visible.
                # Start at the body glyphs so preserved list markers and other
                # graphics in the parser's left margin stay clear of the text.
                block["bbox"][0] = max(boxes[block["id"]].x0, min(glyph.local.x0 for glyph in chars))
                block["foregroundColor"] = outlined[block["id"]]
                block["sourceBold"] = block["kind"] == "heading"
                background_ids.append(block["id"])
                redactions.extend(g.native for g in chars)
                continue
            if block.get("kind") == "heading" and len({g.rgb for g in chars}) > 1:
                # A combined heading can contain a white badge on a black bar
                # followed by black text on white. One translated text color
                # cannot reproduce both runs. Keep this heading's source pixels;
                # its complete translation remains available through the detail.
                continue
            actual = pymupdf.Rect(chars[0].local)
            for glyph in chars[1:]:
                actual |= glyph.local
            # Use the same native glyph geometry to place and clear text, so a
            # decorative shape inside an oversized heading box is never moved.
            actual = (actual + (-.3, -.6, .3, .6)) & pymupdf.Rect(0, 0, page_result["width"], page_result["height"])
            if actual.is_empty:
                continue
            block["bbox"] = list(actual)
            block["foregroundColor"] = list(Counter(g.rgb for g in chars).most_common(1)[0][0])
            if (bold := source_bold(chars)) is not None:
                block["sourceBold"] = bold
            background_ids.append(block["id"])
            redactions.extend(g.native for g in chars)

        selected = [block for block in translated if block["id"] in background_ids]
        def clear(page, blocks, glyph_boxes):
            clear_outlines(page, outlines)
            native_blocks = [b for b in blocks if b["id"] not in outlined]
            remove_text(page, glyph_boxes, spans_in(page, native_spans, [boxes[b["id"]] for b in native_blocks]),
                        [f["bbox"] for b in native_blocks for f in b.get("inlineMath", [])])
            return page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)

        pixmap = clear(source, selected, redactions)
        native_selected = [b for b in selected if b["id"] not in outlined]
        if native_selected:
            # Render the immutable reference separately, after the cleared page,
            # so comparison does not warm that page's image caches before redaction.
            with open_source() as reference_pdf:
                before = reference_pdf[page_result["pageNumber"] - 1].get_pixmap(
                    matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
            protected = [rect for block in page_result["blocks"] if block["id"] not in background_ids
                         if (rect := _rect(block.get("bbox"))) is not None]
            def native_mask():
                try:
                    with open_source() as mask_pdf:
                        page = mask_pdf[page_result["pageNumber"] - 1]
                        page.add_redact_annot(page.rect * page.derotation_matrix, fill=False, cross_out=False)
                        page.apply_redactions(images=1, graphics=2, text=1)
                        return page.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=True, annots=False)
                except (pymupdf.mupdf.FzErrorBase, RuntimeError, ValueError):
                    return None
            uncleared = set()
            changed = region_changed(before, pixmap, [0, 0, page_result["width"], page_result["height"]], scale)
            mask = native_mask() if not changed else None
            if not changed and (mask is None or (mask.is_unicolor and mask.pixel(0, 0)[3] == 0)):
                # The text operations painted no removable ink. No font or
                # producer identification is needed to reject these overlays.
                fills, retained = [], {block["id"] for block in native_selected}
            else:
                fills = residual_text_fills(before, pixmap, native_selected, owners, protected, scale, native_mask, uncleared)
                filled_ids = {block["id"] for block in fills}
                retained = {block["id"] for block in native_selected if block["id"] not in filled_ids
                            and (block["id"] in uncleared or any(not region_changed(before, pixmap, glyph.local, scale)
                                                                for glyph in owners[block["id"]]))}
            if retained:
                # Rebuild from the source so retained passages keep all of
                # their original text and formulas.
                background_ids = [identifier for identifier in background_ids if identifier not in retained]
                selected = [block for block in selected if block["id"] not in retained]
                with open_source() as clean_pdf:
                    pixmap = clear(clean_pdf[page_result["pageNumber"] - 1], selected,
                                   [g.native for block in selected for g in owners[block["id"]]])
            for block in fills:
                pixels = (pymupdf.Rect(block["bbox"]) * pymupdf.Matrix(scale, scale)).irect & pixmap.irect
                pixmap.set_rect(pixels, tuple(round(v) for v in block["backgroundColor"]))
            if fills:
                page_result["backgroundFillBlocks"] = [block["id"] for block in fills]
        if background_ids and include_image:
            page_result["backgroundImage"] = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        page_result["backgroundBlocks"] = background_ids
        page_result["layoutRightEdges"] = line_right_edges(page_result, pixmap)
        page_result["cellLayouts"] = cell_layouts(page_result, pixmap, cells)
        # Their original pixels already exist in the page background. Keeping
        # thousands of duplicate vector commands inflates artifacts and would
        # invite clients to paint the old content over the translated text.
        page_result.pop("decorations", None)
        return outlines
