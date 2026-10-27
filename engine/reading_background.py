"""Render original artwork after removing only positioned, visible PDF text.

The immutable original remains the source for figures and formula crops. A
temporary in-memory copy removes text without painting rectangles over photos,
watermarks, fills or lines. Image-only scans keep their existing rendering path.
"""
from __future__ import annotations

import base64
from collections import Counter

import pymupdf


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


def add_page_background(input_path, page_result):
    page_result.pop("backgroundImage", None)
    page_result.pop("backgroundBlocks", None)
    translated = [block for block in page_result["blocks"]
                  if block.get("kind") in {"text", "heading", "list"}
                  and block.get("status") == "translated" and block.get("bbox")
                  and block.get("translatedText", "").strip() != block.get("sourceText", "").strip()]
    if not translated:
        return
    with pymupdf.open(input_path) as pdf:
        source = pdf[page_result["pageNumber"] - 1]
        rotation, origin = source.rotation_matrix, source.rect.tl
        visible, native_spans = [], []
        for span in source.get_texttrace():
            if span["type"] == 3 or span.get("opacity", 1) <= 0:
                continue  # A hidden OCR layer cannot remove pixels in a scan.
            direction = pymupdf.Point(span["dir"]) * pymupdf.Matrix(rotation.a, rotation.b, rotation.c, rotation.d, 0, 0)
            if abs(direction.y) > .01 or direction.x < .99:
                continue  # Rotated captions stay on the original page.
            native_spans.append(pymupdf.Rect(span["bbox"]))
            color = span.get("color", (0, 0, 0))
            if len(color) == 1:
                color = color * 3
            if len(color) != 3:
                color = (0, 0, 0)
            rgb = tuple(round(max(0, min(1, channel)) * 255) for channel in color)
            for unicode_value, _, _, bounds in span["chars"]:
                if not 0 <= unicode_value <= 0x10FFFF or chr(unicode_value).isspace():
                    continue
                native = pymupdf.Rect(bounds)
                local = native * rotation
                local += (-origin.x, -origin.y, -origin.x, -origin.y)
                if not local.is_empty:
                    visible.append((native, local, rgb))
        if not visible:
            return

        # Assign a glyph to one paragraph, preferring a smaller actual region
        # when the parser has overlapping text boxes. This is geometry, not a
        # content-quality or translation acceptance rule.
        boxes = {b["id"]: pymupdf.Rect(b["bbox"]) for b in translated}
        owners = {b["id"]: [] for b in translated}
        for glyph in visible:
            center = (glyph[1].tl + glyph[1].br) / 2
            matches = [b for b in translated if boxes[b["id"]].contains(center)]
            if matches:
                owner = min(matches, key=lambda b: boxes[b["id"]].get_area())
                owners[owner["id"]].append(glyph)

        redactions, background_ids = [], []
        for block in translated:
            chars = owners[block["id"]]
            if not chars:
                continue
            if block.get("kind") == "heading" and len({rgb for _, _, rgb in chars}) > 1:
                # A combined heading can contain a white badge on a black bar
                # followed by black text on white. One translated text color
                # cannot reproduce both runs. Keep this heading's source pixels;
                # its complete translation remains available through the detail.
                continue
            actual = pymupdf.Rect(chars[0][1])
            for _, box, _ in chars[1:]:
                actual |= box
            # Use the same native glyph geometry to place and clear text, so a
            # decorative shape inside an oversized heading box is never moved.
            actual = (actual + (-.3, -.6, .3, .6)) & pymupdf.Rect(0, 0, page_result["width"], page_result["height"])
            if actual.is_empty:
                continue
            block["bbox"] = list(actual)
            block["foregroundColor"] = list(Counter(rgb for _, _, rgb in chars).most_common(1)[0][0])
            background_ids.append(block["id"])
            redactions.extend(native for native, _, _ in chars)

        selected_native = [box for box in native_spans if any(
            boxes[block["id"]].contains(((box * rotation).tl + (box * rotation).br) / 2 - origin)
            for block in translated if block["id"] in background_ids)]
        from reading_text_effects import remove_text_shadows
        remove_text_shadows(source, selected_native)
        # Formula attachments move with the translated sentence. Remove the
        # original fraction bars as well, but only paths fully inside their
        # explicit source boxes; a page-wide background shape is not covered.
        formulas = [formula for block in translated if block["id"] in background_ids
                    for formula in block.get("inlineMath", [])]
        for formula in formulas:
            local = pymupdf.Rect(formula["bbox"]) + (-.75, -.75, .75, .75)
            displayed = local + (origin.x, origin.y, origin.x, origin.y)
            source.add_redact_annot(displayed * source.derotation_matrix, fill=False, cross_out=False)
        if formulas:
            source.apply_redactions(images=0, graphics=1, text=0)
        for rect in coalesce_text_redactions(source, redactions):
            # No fill: the image/vector artwork under each glyph remains real
            # source artwork, not a sampled flat approximation of its color.
            source.add_redact_annot(rect, fill=False, cross_out=False)
        source.apply_redactions(images=0, graphics=0, text=0)
        scale = min(1800 / source.rect.width, 2400 / source.rect.height)
        matrix = pymupdf.Matrix(scale, scale).pretranslate(-origin.x, -origin.y)
        pixmap = source.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
        page_result["backgroundImage"] = base64.b64encode(pixmap.tobytes("png")).decode("ascii")
        page_result["backgroundBlocks"] = background_ids
        # Their original pixels already exist in the page background. Keeping
        # thousands of duplicate vector commands inflates artifacts and would
        # invite clients to paint the old content over the translated text.
        page_result.pop("decorations", None)
