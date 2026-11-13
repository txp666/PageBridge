"""Reconcile inline formula spans with visible words in the original PDF."""
import re

import pymupdf

from reading_tables import native_words


def _trim_word_edge(latex, fragment, *, left):
    """Remove only the letters the PDF and the parser both assign to the split word."""
    letters = r"\s*".join(re.escape(c) for c in fragment)
    literal = rf"(?:\\(?:mathrm|mathbf|mathit|text)\s*\{{\s*{letters}\s*\}}|{letters})"
    pattern = rf"^\s*{literal}(?=\s|\\)" if left else rf"{literal}\s*$"
    return re.sub(pattern, "", latex, count=1).strip()


def _refine_block(block, words):
    template = block["sourceTemplate"]
    surviving = []
    changed = False
    for formula in block["inlineMath"]:
        token, latex = formula["token"], formula["latex"]
        before, match, after = template.partition(token)
        if not match:
            surviving.append(formula)
            continue
        box = pymupdf.Rect(formula["bbox"])
        near = [w for w in words if w["horizontal"] and pymupdf.Rect(w["bbox"]).intersects(box)]
        inside = [c for w in near for c in w["chars"]
                  if box.contains((pymupdf.Rect(c["bbox"]).tl + pymupdf.Rect(c["bbox"]).br) / 2)]

        # A whole source word in the same running text as its neighbours can
        # be mistaken for math, e.g. a parenthesized label or abbreviation.
        prose = next((w for w in near if w["in_prose"] and w["chars"] == inside), None)
        if prose and re.fullmatch(r"\(?(?:[^\W\d_]\.){2,}\)?|\([^\W\d_]\)[:;]", prose["text"]):
            template = before + prose["text"] + after
            changed = True
            continue

        for w in near:
            text, bounds = w["text"], pymupdf.Rect(w["bbox"])
            if (not w["in_prose"] or not text.isalpha() or len(text) < 2
                    or not box.y0 <= (bounds.y0 + bounds.y1) / 2 <= box.y1):
                continue
            others = [c["bbox"] for c in inside if c not in w["chars"]]
            if not others:
                continue
            if bounds.x0 < box.x0 < bounds.x1:
                start = re.search(r"([^\W\d_]+)\s*$", before)
                if not start or not text.startswith(start[1]) or text == start[1]:
                    continue
                suffix = text[len(start[1]):]
                trimmed = _trim_word_edge(latex, suffix, left=True)
                next_x = min(c[0] for c in others)
                if trimmed and trimmed != latex and next_x > bounds.x1:
                    before = before[:start.start(1)] + text + " "
                    # All renderers already add a .75 pt crop margin. Keep its
                    # outer edge in the gap and inside the original formula.
                    box.x0 = min(next_x, (bounds.x1 + next_x) / 2 + .75)
                    latex = trimmed
            elif bounds.x0 < box.x1 < bounds.x1:
                end = re.match(r"\s*([^\W\d_]+)", after)
                if not end or not text.endswith(end[1]) or text == end[1]:
                    continue
                prefix = text[:-len(end[1])]
                trimmed = _trim_word_edge(latex, prefix, left=False)
                prev_x = max(c[2] for c in others)
                if trimmed and trimmed != latex and prev_x < bounds.x0:
                    after = " " + text + after[end.end(1):]
                    box.x1 = max(prev_x, (prev_x + bounds.x0) / 2 - .75)
                    latex = trimmed
        if latex != formula["latex"]:
            formula.update(bbox=list(box), latex=latex)
            template = before + token + after
            changed = True
        surviving.append(formula)
    if not changed:
        return
    if surviving:
        block.update(inlineMath=surviving, sourceTemplate=template)
    else:
        block.pop("inlineMath")
        block.pop("sourceTemplate")
    source = template
    for formula in surviving:
        source = source.replace(formula["token"], "$" + formula["latex"] + "$")
    block["sourceText"] = source


def refine_inline_formulas(input_path, page_number, blocks):
    passages = [b for b in blocks if b.get("inlineMath") and b.get("sourceTemplate")]
    if not passages:
        return
    try:
        with pymupdf.open(input_path) as pdf:
            words = native_words(pdf[page_number - 1], include_chars=True)
    except (OSError, ValueError, RuntimeError, pymupdf.mupdf.FzErrorBase):
        return
    if not words:
        return
    for i, word in enumerate(words):
        # Confirm the surrounding font on the same visible line with a real word.
        style = word["chars"][0]["style"]
        neighbours = words[max(0, i - 1):i] + words[i + 1:i + 2]
        word["in_prose"] = all(c["style"] == style for c in word["chars"]) and any(
            w["line"] == word["line"] and sum(c.isalpha() for c in w["text"]) >= 2
            and w["chars"][0]["style"] == style for w in neighbours)
    for block in passages:
        _refine_block(block, words)
