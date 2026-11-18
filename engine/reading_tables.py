"""Translate native table labels without reconstructing their number columns.

MinerU still locates the table. The PDF's own character geometry supplies the
labels and leaves every value, grid line and merged-cell position in the source.
No inferred cell grid or model-produced numeric table is used for native PDFs.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import re
from threading import Lock
import unicodedata

import pymupdf


def _bounds(items):
    result = pymupdf.Rect(items[0]["bbox"])
    for item in items[1:]:
        result |= pymupdf.Rect(item["bbox"])
    return result


def native_words(page, *, include_chars=False):
    rotation, origin = page.rotation_matrix, page.rect.tl
    zero = pymupdf.Point(0, 0) * rotation
    # Scanned PDFs often carry invisible OCR text. Its words cannot be removed
    # from the source image, so they must not create native translation overlays.
    visible = {(char[0], round(char[2][0], 3), round(char[2][1], 3))
               for span in page.get_texttrace() if span.get("type") != 3 and span.get("opacity", 1) > 0
               for char in span["chars"]}
    words = []
    flags = pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    for block_index, block in enumerate(page.get_text("rawdict", flags=flags)["blocks"]):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block["lines"]):
            direction = pymupdf.Point(line["dir"]) * rotation - zero
            horizontal = direction.x > 0.99 and abs(direction.y) < 0.01
            current = []

            def finish():
                if current:
                    rect = _bounds(current)
                    words.append(dict(text="".join(c["text"] for c in current), bbox=list(rect),
                                      line=(block_index, line_index), horizontal=horizontal))
                    if include_chars:
                        words[-1]["chars"] = current.copy()
                    current.clear()

            for span in line["spans"]:
                for char in span["chars"]:
                    key = (ord(char["c"]), round(char["origin"][0], 3), round(char["origin"][1], 3))
                    if char["c"].isspace() or key not in visible:
                        finish()
                    else:
                        box = pymupdf.Rect(char["bbox"]) * rotation
                        box += (-origin.x, -origin.y, -origin.x, -origin.y)
                        glyph = dict(text=char["c"], bbox=list(box))
                        if include_chars:
                            glyph["style"] = (span["font"], span["size"], span["flags"])
                        current.append(glyph)
            finish()
    return words


def _native_rows(words):
    """Group the existing native words by baseline without inferring a grid."""
    rows = []
    for word in sorted(words, key=lambda w: (w["bbox"][1], w["bbox"][0])):
        box = pymupdf.Rect(word["bbox"])
        middle = (box.y0 + box.y1) / 2
        if rows and abs(rows[-1]["middle"] - middle) <= min(rows[-1]["height"], box.height) * 0.35:
            rows[-1]["words"].append(word)
        else:
            rows.append(dict(middle=middle, height=box.height, words=[word]))
    for row in rows:
        row["text"] = "  ".join(w["text"] for w in sorted(row["words"], key=lambda w: w["bbox"][0]))
    return rows


def _translation_context(label, rows, section):
    """Give short labels the local topic and their actual row and headings."""
    middle = (label["bbox"][1] + label["bbox"][3]) / 2
    index = min(range(len(rows)), key=lambda i: abs(rows[i]["middle"] - middle))
    row = rows[index]["text"]
    headings = "\n".join(row["text"] for row in rows[:5])
    return (f"Text introducing the table:\n{section[:300]}\n"
            f"Table column headings and first rows:\n{headings[:350]}\n"
            f"Row containing this cell:\n{row[:450]}")


def _introduction(area, text_regions):
    preceding = ((box.y1, text) for box, text in text_regions
                 if box.y1 <= area.y0 and box.x0 < area.x1 and box.x1 > area.x0)
    return max(preceding, default=(0, ""))[1][:300]


def _table_topics(blocks):
    areas = [pymupdf.Rect(b["bbox"]) for b in blocks if b.get("kind") == "table" and b.get("bbox")]
    captions = defaultdict(list)
    text_regions = []
    for block in blocks:
        if not block.get("bbox") or block.get("kind") not in ("text", "heading", "list") or block.get("sourceType") == "table_cell":
            continue
        box, text = pymupdf.Rect(block["bbox"]), block["sourceText"]
        text_regions.append((box, text))
        if block.get("sourceType") in ("table_caption", "table_footnote"):
            # A caption or key can be above or below its table. Bind it to the nearest table in its column.
            candidates = [i for i, area in enumerate(areas) if box.x0 < area.x1 and area.x0 < box.x1]
            if candidates:
                owner = min(candidates, key=lambda i: max(0, areas[i].y0 - box.y1, box.y0 - areas[i].y1))
                captions[owner].append((box.y0, text))
    return [(area, "\n".join(text for _, text in sorted(captions[i]))[:1200] or _introduction(area, text_regions))
            for i, area in enumerate(areas)]


def _label_runs(words):
    current, runs, previous = [], [], None

    def finish():
        if current and any(unicodedata.category(c).startswith("L") for w in current for c in w["text"]):
            runs.append(dict(sourceText=" ".join(w["text"] for w in current), bbox=list(_bounds(current))))
        current.clear()

    for word in words:
        box = pymupdf.Rect(word["bbox"])
        # Values and years stay exactly where the PDF put them. Splitting at a
        # numeric word also preserves dates and level numbers beside a label.
        if not word["horizontal"] or any(c.isnumeric() for c in word["text"]):
            finish(); previous = None
            continue
        if previous is not None:
            before = pymupdf.Rect(previous["bbox"])
            if word["line"] != previous["line"] or box.x0 - before.x1 > max(3, min(box.height, before.height)):
                finish()
        current.append(word)
        previous = word
    finish()
    return runs


def _outline_labels(page, tables, existing_text):
    from reading_outlines import lettering_lines

    labels = {table["id"]: [] for table in tables}
    for lines in lettering_lines(page):
        chars = [c for line in lines for c in line if not c["c"].isspace()]
        if not chars:
            continue
        bounds = _bounds(chars)
        if any(other.intersects(bounds) for other in existing_text):
            continue
        areas = [table for table in tables if pymupdf.Rect(table["bbox"]).contains(bounds)]
        if len(areas) != 1:
            continue
        text = " ".join("".join(c["c"] for c in line).strip() for line in lines)
        row = labels[areas[0]["id"]]
        row.append({"sourceText": text, "bbox": list(bounds)})
    return labels


def refine_native_tables(input_path, page_number, page_result):
    """Add independent label blocks only where the PDF has actual text geometry."""
    tables = [block for block in page_result["blocks"] if block["kind"] == "table" and block.get("bbox")]
    if not tables:
        return
    text_regions = [(pymupdf.Rect(block["bbox"]), block["sourceText"]) for block in page_result["blocks"]
                    if block["kind"] in ("text", "heading", "list") and block.get("bbox")]
    existing_text = [box for box, _ in text_regions]
    try:
        with pymupdf.open(input_path) as pdf:
            page = pdf[page_number - 1]
            words = native_words(page)
            outlined = _outline_labels(page, tables, existing_text + [pymupdf.Rect(w["bbox"]) for w in words])
    except (OSError, ValueError, RuntimeError):
        return
    labels, assigned = [], set()
    for table in tables:
        area = pymupdf.Rect(table["bbox"])
        table_words = []
        label_words = []
        for index, word in enumerate(words):
            box = pymupdf.Rect(word["bbox"])
            center = (box.tl + box.br) / 2
            # A parser table box may stop inside the final row's glyphs. Keep
            # those actual intersecting words; do not expand or move their boxes.
            if not area.intersects(box):
                continue
            table_words.append(word)
            if index not in assigned and not any(other.contains(center) for other in existing_text):
                label_words.append(word)
                assigned.add(index)
        cells = outlined[table["id"]]
        if not table_words and not cells:
            continue
        section = _introduction(area, text_regions)
        rows = _native_rows(table_words + [dict(text=cell["sourceText"], bbox=cell["bbox"]) for cell in cells])
        # A paint run for a prose cell can include digits. Keep that passage
        # together and leave separate numeric cells in place.
        runs = _label_runs(label_words) + [cell for cell in cells if any(c.isalpha() for c in cell["sourceText"])]
        table.update(sourceText="\n".join(row["text"] for row in rows), sourceType="native_table", status="preserved")
        for label in runs:
            label.update(id=f"p{page_number:05d}-b{len(page_result['blocks']) + len(labels) + 1:05d}",
                         kind="text", sourceType="table_cell", status="preserved",
                         translationContext=_translation_context(label, rows, section))
            labels.append(label)
    page_result["blocks"].extend(labels)


@dataclass
class TableCell:
    bounds: pymupdf.Rect
    field: tuple
    introduction: str = ""


# MuPDF's table finder uses process-wide character and edge buffers.
_finder_lock = Lock()


def table_cells(source, blocks):
    """Map whole labels to actual grid cells. Number-only cells and split runs stay in place."""
    labels = [(b, pymupdf.Rect(b["bbox"])) for b in blocks if b.get("sourceType") == "table_cell" and b.get("bbox")]
    if not labels:
        return {}
    origin = source.rect.tl
    # Take a plain copy while the finder still owns its buffers.
    with _finder_lock:
        try:
            grids = [[row.cells for row in t.rows] for t in source.find_tables().tables]
        except (ValueError, RuntimeError):
            return {}
    if not grids:
        return {}
    topics = _table_topics(blocks)
    words = [pymupdf.Rect(w["bbox"]) for w in native_words(source)]
    centers = [(word.tl + word.br) / 2 for word in words]
    label_words = {b["id"]: {i for i, p in enumerate(centers) if box.contains(p)} for b, box in labels}
    mapped = {}
    for number, rows in enumerate(grids):
        if not rows:
            continue
        owners = {}
        for row in rows:
            for cell in filter(None, row):
                rect = pymupdf.Rect(cell) - (origin.x, origin.y, origin.x, origin.y)
                inside = [(b, box) for b, box in labels if rect.contains((box.tl + box.br) / 2)]
                if len(inside) == 1:
                    block, box = inside[0]
                    # Font ascenders, descenders and bearings can cross a painted cell edge.
                    # Require the same actual words on both sides of the assignment. Outlined
                    # text has no native words, so it still needs a fully contained box.
                    cell_words = {i for i, p in enumerate(centers) if rect.contains(p)}
                    if cell_words == label_words[block["id"]] and (cell_words or rect.contains(box)):
                        owners[cell] = (block, rect)
        if not owners:
            continue
        def label(cell):
            return owners[cell][0]["sourceText"].strip() if cell in owners else ""
        # Stacked tables share a field only when both their header pattern and
        # ordered row keys agree. Identical words in unrelated fields stay independent.
        header = tuple(re.sub(r"\d+", "#", label(cell)) for cell in rows[0])
        keys = tuple(label(row[0]) for row in rows[1:])
        family = (header, keys) if any(header) and len(set(keys) - {""}) > 1 else (number,)
        starts = sorted({cell[0] for row in rows for cell in row if cell})
        for row in rows:
            for index, cell in enumerate(row):
                if cell in owners:
                    block, rect = owners[cell]
                    span = sum(cell[0] <= x < cell[2] for x in starts)
                    center = (rect.tl + rect.br) / 2
                    introduction = next((topic for area, topic in topics if area.contains(center)), "")
                    mapped[block["id"]] = TableCell(rect, (family, index, span), introduction)
    return mapped


def passage_groups(blocks, cells):
    """Keep prose and individual table fields in separate translation and review contexts."""
    groups = defaultdict(list)
    equivalents = {}
    for block in blocks:
        cell = cells.get(block["id"])
        if cell and not block.get("inlineMath"):
            key = (cell.field, block["sourceText"])
            if key not in equivalents:
                equivalents[key] = []
                groups[cell.field].append(block)
            equivalents[key].append(block)
        else:
            groups["cells" if block.get("sourceType") == "table_cell" else "prose"].append(block)
    return list(groups.values()), [group for group in equivalents.values() if len(group) > 1]
