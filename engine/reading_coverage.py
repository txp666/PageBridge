"""Reconcile parser regions with independently positioned text in the source PDF."""
from collections import defaultdict
from itertools import groupby

import pymupdf

from reading_outlines import lettering_lines
from reading_tables import native_words


def _bounds(boxes):
    result = pymupdf.Rect()
    for box in boxes:
        result |= box
    return result


def _passages(page):
    """Use the PDF's paragraph/line grouping. Some outlined fonts only expose traces."""
    native = []
    for _, words in groupby(native_words(page), key=lambda word: word["line"][0]):
        lines = []
        for _, row in groupby(words, key=lambda word: word["line"]):
            row = list(row)
            if all(w["horizontal"] for w in row):
                box = _bounds(w["bbox"] for w in row)
                lines.append((box, " ".join(w["text"] for w in row)))
                native.append(box)
        if lines:
            yield lines
    for passage in lettering_lines(page):
        lines = []
        for chars in passage:
            box = _bounds(c["bbox"] for c in chars if not c["c"].isspace())
            if not any(region.contains((box.tl + box.br) / 2) for region in native):
                lines.append((box, "".join(c["c"] for c in chars).strip()))
        if lines:
            yield lines


def reconcile_native_text(page, result):
    """Recover uncovered lines and use native spacing when both sources agree on letters."""
    blocks = result["blocks"]
    regions = [(pymupdf.Rect(b["bbox"]), b) for b in blocks if b.get("bbox")]
    unpositioned = ["".join(b["sourceText"].split()) for b in blocks if not b.get("bbox")]
    owned = defaultdict(list)
    additions = []
    page_box = pymupdf.Rect(0, 0, result["width"], result["height"])

    def add(lines):
        if not lines:
            return
        text = " ".join(s for _, s in lines).strip()
        if (not any(c.isalpha() for c in text) or "\ufffd" in text
                or any("".join(text.split()) in s for s in unpositioned)):
            return
        box = _bounds(r for r, _ in lines) & page_box
        if box.is_empty:
            return
        block = dict(id=f"p{result['pageNumber']:05d}-b{len(blocks) + len(additions) + 1:05d}",
                     kind="text", sourceType="native_text", status="preserved", sourceText=text, bbox=list(box))
        additions.append((box, block))
        regions.append((box, block))

    try:
        passages = list(_passages(page))
    except (RuntimeError, ValueError, OverflowError):
        return  # Keep the parser's result if a native font cannot be interpreted.
    for passage in passages:
        missing = []
        for box, text in passage:
            middle = (box.y0 + box.y1) / 2
            # A figure can contain its own independently painted labels. Tables,
            # formulas and code already own their content, even when left original.
            hits = [b for region, b in regions if b["kind"] != "figure" and
                    region.y0 <= middle <= region.y1 and box.x0 < region.x1 and region.x0 < box.x1]
            if hits:
                add(missing)
                missing = []
                if len(hits) == 1:
                    owned[hits[0]["id"]].append((box, text))
            else:
                # Do not join disjoint columns or span a region already owned
                # by another block just because the PDF uses the same text run.
                if missing:
                    previous = missing[-1][0]
                    span = _bounds(r for r, _ in missing) | box
                    if (box.y0 <= previous.y0 or min(previous.x1, box.x1) <= max(previous.x0, box.x0)
                            or any(b["kind"] != "figure" and span.intersects(r) for r, b in regions)):
                        add(missing)
                        missing = []
                missing.append((box, text))
        add(missing)

    for b in blocks:
        lines = owned.get(b["id"], [])
        if b["kind"] not in ("text", "heading", "list") or len(lines) != 1 or b.get("inlineMath"):
            continue
        native = " ".join(lines[0][1].split())
        if "".join(native.split()) == "".join(b["sourceText"].split()):
            b["sourceText"] = native
    # Keep the parser's reading order and stable IDs when inserting missing
    # passages. Anchor each new paragraph to the next block in its column.
    insertions = defaultdict(list)
    for box, block in sorted(additions, key=lambda item: (item[0].y0, item[0].x0)):
        figure = next((i for i, b in enumerate(blocks) if b["kind"] == "figure" and b.get("bbox")
                       and pymupdf.Rect(b["bbox"]).contains((box.tl + box.br) / 2)), None)
        following = [(i, r) for i, b in enumerate(blocks) if (r := b.get("bbox")) and r[1] >= box.y1]
        anchor = figure + 1 if figure is not None else next(
            (i for i, r in following if r[0] < box.x1 and box.x0 < r[2]),
            following[0][0] if following else len(blocks))
        insertions[anchor].append(block)
    for index in sorted(insertions, reverse=True):
        blocks[index:index] = insertions[index]
    if additions and result.get("status") == "source_fallback":
        result.update(status="completed")
        result.pop("error", None)
