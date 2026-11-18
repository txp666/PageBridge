"""Find usable line width on the cleared page without changing source geometry."""
import base64
import math

import pymupdf


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

        # Compare whole scanlines as byte buffers; do not build a colour histogram.
        samples, stride, channels = background.samples_mv, background.stride, background.n
        offset = top * stride + start * channels
        pixel = bytes(samples[offset:offset + channels])

        def clear(end):
            row = pixel * (end - start)
            return all(samples[i:i + len(row)] == row for i in range(offset, offset + (bottom - top) * stride, stride))

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


def saved_line_right_edges(page):
    if "layoutRightEdges" in page:
        return page["layoutRightEdges"]
    if not page.get("backgroundImage") or not page.get("backgroundBlocks"):
        return {}
    data = base64.b64decode(page["backgroundImage"], validate=True)
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n") or data[12:16] != b"IHDR":
        return {}
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    if not 0 < width * height <= 16_000_000:
        return {}
    return line_right_edges(page, pymupdf.Pixmap(data))
