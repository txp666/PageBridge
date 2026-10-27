"""Translate native table labels without reconstructing their number columns.

MinerU still locates the table. The PDF's own character geometry supplies the
labels and leaves every value, grid line and merged-cell position in the source.
No inferred cell grid or model-produced numeric table is used for native PDFs.
"""
from __future__ import annotations

import unicodedata


def _bounds(items, pymupdf):
    result = pymupdf.Rect(items[0]["bbox"])
    for item in items[1:]:
        result |= pymupdf.Rect(item["bbox"])
    return result


def _native_words(page, pymupdf):
    rotation, origin = page.rotation_matrix, page.rect.tl
    zero = pymupdf.Point(0, 0) * rotation
    # Scanned PDFs often carry invisible OCR text. Its words cannot be removed
    # from the source image, so they must not create native translation overlays.
    visible = {(char[0], round(char[2][0], 3), round(char[2][1], 3))
               for span in page.get_texttrace() if span.get("type") != 3 and span.get("opacity", 1) > 0
               for char in span["chars"]}
    words = []
    for block_index, block in enumerate(page.get_text("rawdict")["blocks"]):
        if block.get("type") != 0:
            continue
        for line_index, line in enumerate(block["lines"]):
            direction = pymupdf.Point(line["dir"]) * rotation - zero
            horizontal = direction.x > 0.99 and abs(direction.y) < 0.01
            current = []

            def finish():
                if current:
                    rect = _bounds(current, pymupdf)
                    words.append(dict(text="".join(c["text"] for c in current), bbox=list(rect),
                                      line=(block_index, line_index), horizontal=horizontal))
                    current.clear()

            for span in line["spans"]:
                for char in span["chars"]:
                    key = (ord(char["c"]), round(char["origin"][0], 3), round(char["origin"][1], 3))
                    if char["c"].isspace() or key not in visible:
                        finish()
                    else:
                        box = pymupdf.Rect(char["bbox"]) * rotation
                        box += (-origin.x, -origin.y, -origin.x, -origin.y)
                        current.append(dict(text=char["c"], bbox=list(box)))
            finish()
    return words


def _native_rows(words, pymupdf):
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


def _row_text(words, pymupdf):
    """Plain native rows for details, not a reconstructed cell/column matrix."""
    return "\n".join(row["text"] for row in _native_rows(words, pymupdf))


def _translation_context(label, rows):
    """Give short labels their real row and column headings as source context."""
    middle = (label["bbox"][1] + label["bbox"][3]) / 2
    index = min(range(len(rows)), key=lambda i: abs(rows[i]["middle"] - middle))
    nearby = "\n".join(row["text"] for row in rows[max(0, index - 1):index + 2])
    headings = "\n".join(row["text"] for row in rows[:5])
    return ("Native table rows around this label:\n" + nearby[:600]
            + "\nNative table first rows and column headings:\n" + headings[:500])[:1200]


def _label_runs(words, pymupdf):
    current, runs, previous = [], [], None

    def finish():
        if current and any(unicodedata.category(c).startswith("L") for w in current for c in w["text"]):
            runs.append(dict(sourceText=" ".join(w["text"] for w in current), bbox=list(_bounds(current, pymupdf))))
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


def refine_native_tables(input_path, page_number, page_result):
    """Add independent label blocks only where the PDF has actual text geometry."""
    tables = [block for block in page_result["blocks"] if block["kind"] == "table" and block.get("bbox")]
    if not tables:
        return
    try:
        import pymupdf
        with pymupdf.open(input_path) as pdf:
            words = _native_words(pdf[page_number - 1], pymupdf)
    except (OSError, ValueError, RuntimeError):
        return
    if not words:
        return
    existing_text = [pymupdf.Rect(block["bbox"]) for block in page_result["blocks"]
                     if block["kind"] in ("text", "heading", "list") and block.get("bbox")]
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
        if not table_words:
            continue
        rows = _native_rows(table_words, pymupdf)
        table.update(sourceText="\n".join(row["text"] for row in rows), sourceType="native_table", status="preserved")
        table.pop("translatedText", None)
        table.pop("translatedTemplate", None)
        for label in _label_runs(label_words, pymupdf):
            label.update(id=f"p{page_number:05d}-b{len(page_result['blocks']) + len(labels) + 1:05d}",
                         kind="text", sourceType="table_cell", status="preserved",
                         translationContext=_translation_context(label, rows))
            labels.append(label)
    page_result["blocks"].extend(labels)
