"""Remove raster shadow layers paired with text that will be replaced.

Only memory-copy pages are passed here. Photos, vector decorations and logo
shadows without a selected native text line remain in the source background.
"""
from __future__ import annotations

import math

import pymupdf


def _visible_mask_box(mask):
    """Ignore transparent asset padding when comparing a shadow to its text."""
    if mask.n != 1 or mask.is_unicolor:
        return None
    samples = mask.samples
    left, top, right, bottom = mask.width, mask.height, 0, 0
    for y in range(mask.height):
        row = samples[y * mask.stride:y * mask.stride + mask.width]
        trimmed = row.strip(b"\0")
        if not trimmed:
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


def remove_text_shadows(page, selected_native_boxes):
    """Delete uniform-color soft-mask images paired with selected native lines.

    ``selected_native_boxes`` contains [x0, y0, x1, y1] line/span rectangles in
    unrotated native PDF points, not whole paragraph or MinerU page boxes. Each
    occurrence of a reused image must match a selected line before its resource
    can be removed. The return value contains the removed image xrefs.
    """
    lines = []
    for box in selected_native_boxes:
        if len(box) != 4 or any(not math.isfinite(value) for value in box):
            continue
        rect = pymupdf.Rect(box)
        if not rect.is_empty and not rect.is_infinite:
            lines.append(rect)
    if not lines:
        return []

    images = {item[0]: item[1] for item in page.get_images(full=True) if item[0] > 0 and item[1] > 0}
    removed = []
    for xref, smask in images.items():
        placements = page.get_image_rects(xref, transform=True)
        if not placements:
            continue
        try:
            image = pymupdf.Pixmap(page.parent, xref)
            if not image.is_unicolor:
                continue
            mask_box = _visible_mask_box(pymupdf.Pixmap(page.parent, smask))
        except (RuntimeError, ValueError):
            continue
        if mask_box is None:
            continue
        visible = [mask_box * transform for _, transform in placements]
        if not all(any(_paired_with_line(shadow, line) for line in lines) for shadow in visible):
            continue
        page.delete_image(xref)
        removed.append(xref)
    return removed
