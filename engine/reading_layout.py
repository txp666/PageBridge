"""Find usable line width on the cleared page without changing source geometry."""
import math

import pymupdf

from reading_assets import read_background


def source_rect(value):
    if (isinstance(value, list) and len(value) == 4
            and all(type(v) in (int, float) and math.isfinite(v) for v in value)):
        rect = pymupdf.Rect(value)
        return None if rect.is_empty else rect
    return None


def estimated_font_size(block):
    """Font size from the region's area and character count, for text without a text layer
    (scans); within ±25% of the real size on the demo pages."""
    text = (block.get("sourceText") or "").strip()
    bbox = block.get("bbox")
    if block.get("kind") not in {"text", "heading", "list"} or len(text) < 3 or not bbox or len(bbox) != 4:
        return None
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width <= 0 or height <= 0:
        return None
    return min(height / 1.2, math.sqrt(width * height / (len(text) * 0.6)))


def _uniform(pixmap, left, top, right, bottom):
    if left >= right or top >= bottom:
        return False
    samples, stride, channels = pixmap.samples_mv, pixmap.stride, pixmap.n
    start = top * stride + left * channels
    row = bytes(samples[start:start + channels]) * (right - left)
    return all(samples[i:i + len(row)] == row for i in range(start, start + (bottom - top) * stride, stride))


def cell_layouts(page, background, cells):
    """Preserve the source alignment in a measured cell on a clear horizontal strip."""
    result = {}
    positioned = set(page.get("backgroundBlocks", ()))
    sx, sy = background.width / page["width"], background.height / page["height"]
    for block in page["blocks"]:
        cell = cells.get(block["id"])
        if cell is None or block["id"] not in positioned:
            continue
        box, area = source_rect(block.get("bbox")), cell.bounds
        if box is None:
            continue
        before, after = max(0, box.x0 - area.x0), max(0, area.x1 - box.x1)
        margin = box.height / 2
        if abs(before - after) <= box.height / 2:
            inset = min(margin, before, after)
            left, right, align = area.x0 + inset, area.x1 - inset, "center"
        elif before < after:
            left, right, align = box.x0, area.x1 - min(margin, after), "left"
        else:
            left, right, align = area.x0 + min(margin, before), box.x1, "right"
        left, right = max(0, left), min(page["width"], right)
        top, bottom = max(0, math.floor(box.y0 * sy)), min(background.height, math.ceil(box.y1 * sy))

        # The source box has already been cleared. Only newly occupied space must be
        # clear; a painted seam at the original leading edge does not limit the far margin.
        def clear(start, end):
            return _uniform(background, max(0, math.floor(start * sx)), top,
                            min(background.width, math.ceil(end * sx)), bottom)

        if ((left >= box.x0 or clear(left, box.x0 + 1 / sx))
                and (right <= box.x1 or clear(box.x1 - 1 / sx, right))):
            result[block["id"]] = dict(left=left, right=right, alignment=align)
    return result


def line_right_edges(page, background):
    """Use the width of aligned rows, stopping at other blocks and nonuniform pixels."""
    positioned = set(page.get("backgroundBlocks", ()))
    if not positioned:
        return {}
    boxes = [(b, box) for b in page["blocks"] if (box := source_rect(b.get("bbox"))) is not None]
    rows = [(block, box) for block, box in boxes
            if block.get("kind") in ("heading", "text", "list")
            and len(block["sourceText"].strip().splitlines()) == 1
            and box.height < 2 * (estimated_font_size(block) or box.height)]
    sx, sy = background.width / page["width"], background.height / page["height"]
    result = {}
    for block, box in rows:
        if block["id"] not in positioned:
            continue
        peers = [r.x1 for _, r in rows if abs(r.x0 - box.x0) <= 1 and abs(r.height - box.height) <= 1
                 and (r.y1 <= box.y0 or r.y0 >= box.y1)]
        limit = min(page["width"], max(peers, default=box.x1))
        if limit <= box.x1:
            continue
        for other, r in boxes:
            if other is not block and r.y0 < box.y1 and box.y0 < r.y1 and r.x1 > box.x1:
                limit = min(limit, r.x0 - 1 / sx)
        if limit <= box.x1:
            continue
        # Include the inner edge of the source box so a change of paper colour,
        # photo, rule or dot leader constrains the line even without a parser box.
        start = max(0, math.floor(box.x1 * sx) - 1)
        stop = min(background.width, math.floor(limit * sx))
        top, bottom = max(0, math.floor(box.y0 * sy)), min(background.height, math.ceil(box.y1 * sy))
        if start >= stop or top >= bottom:
            continue

        def clear(end):
            return _uniform(background, start, top, end, bottom)

        if not clear(stop):
            lo, hi = start, stop
            while hi - lo > 1:
                mid = (lo + hi) // 2
                if clear(mid):
                    lo = mid
                else:
                    hi = mid
            stop = lo
        if stop / sx > box.x1:
            result[block["id"]] = min(limit, stop / sx)
    return result


def saved_layouts(page, source, reading_path=None):
    """Read stored plans or measure missing ones for an immutable older draft."""
    from reading_tables import table_cells
    edges, cells = page.get("layoutRightEdges"), page.get("cellLayouts")
    has_cells = any(b.get("sourceType") == "table_cell" for b in page["blocks"])
    if not has_cells:
        cells = {}
    if (edges is not None and cells is not None) or not page.get("backgroundBlocks"):
        return edges or {}, cells or {}
    data = read_background(reading_path, page)
    if data is None:
        return edges or {}, cells or {}
    pixmap = pymupdf.Pixmap(data)
    if edges is None:
        edges = line_right_edges(page, pixmap)
    if cells is None:
        cells = cell_layouts(page, pixmap, table_cells(source, page["blocks"]))
    return edges, cells
