"""Source-aware protection for BabelDOC's formula/style translation boundary.

The upstream package is not modified. Install this adapter in the translation
child process, then write its text-free report before accepting generated PDFs.
"""
from __future__ import annotations

from collections import Counter
import copy
import hashlib
import html
import json
from pathlib import Path
import re
import threading
import unicodedata

GUARD_VERSION = 3
_lock = threading.RLock()
_installed = False
_states: dict[int, dict] = {}
_counters = Counter()
_originals = {}

# These are engine syntax, not a general HTML sanitizer. Literal source examples
# are shielded before matching and restored in the resulting text compositions.
_FORMULA = re.compile(r"\{\s*v\s*(\d+)\s*\}", re.I)
_TAG = re.compile(r"<\s*(/?)\s*([A-Za-z][\w:-]*|风格|样式)([^<>]*?)>", re.I)
_OPEN = re.compile(r"<\s*(style|span|风格|样式)\s+id\s*=\s*(['\"])(\d+)\2\s*>", re.I)
_CLOSE = re.compile(r"<\s*/\s*(style|span|风格|样式)\s*>", re.I)
_CANONICAL = re.compile(r"\{v\d+\}|<style id='\d+'>|</style>")
_CLASS_NUMBER = r"\d+(?:\s*(?:and|or|,|[-–]|to)\s*\d+)*"
_CLASS_ATOM = rf"(?<![A-Za-z0-9_])(?:(?:Zone|Division|Div\.)\s*{_CLASS_NUMBER}|Class\s*(?:III|II|I)(?![A-Za-z]))"
_SAFETY_CLASSIFICATION = re.compile(rf"{_CLASS_ATOM}(?:\s*[/,]\s*{_CLASS_ATOM})*(?![A-Za-z0-9_])", re.I)
_URL_OR_EMAIL = re.compile(
    r"(?:https?://|www\.)[^\s<>]+"
    r"|(?<![\w.+-])[\w.!#$%&'*+/=?^`{|}~-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}\b",
    re.I,
)
_SI_UNIT = r"(?:[pnumckMGTμµ]?(?:Hz|Pa|V|A|F|H|Ω|W|J|m|s)|°\s*C)(?:[23²³])?"
_UNIT_ALIASES = {"千米": "km", "毫米": "mm", "厘米": "cm", "微法": "μF", "微法拉": "μF",
                 "纳法": "nF", "纳法拉": "nF", "毫亨": "mH", "毫亨利": "mH", "微亨": "μH",
                 "毫安": "mA", "毫安培": "mA", "伏": "V", "伏特": "V", "瓦": "W", "瓦特": "W",
                 "毫瓦": "mW", "兆欧": "MΩ", "兆欧姆": "MΩ", "欧": "Ω", "欧姆": "Ω"}
_MEASUREMENT_UNIT = "(?:(?:" + "|".join(sorted(map(re.escape, _UNIT_ALIASES), key=len, reverse=True)) + ")(?:[23²³])?|" + _SI_UNIT + ")"
_MEASUREMENT = re.compile(r"(?<![A-Za-z0-9_.])(?P<number>[<>≤≥≈±]?\s*[+−-]?\d+(?:[.,]\d+)?(?:[eE][+-]?\d+)?)"
                          r"\s*(?P<unit>" + _MEASUREMENT_UNIT + r"(?:\s*/\s*" + _SI_UNIT + r")?)(?![A-Za-z0-9_])")
_STANDARD_ID = re.compile(r"(?<![A-Za-z0-9_])(?:IEC(?:/EN)?|ANSI/UL|DIN\s+EN|NFPA|ISO(?:/IEC)?|EN|UL)"
                          r"\s*\d{2,6}(?:\s*[-‑–]\s*\d{1,3})?(?::\d{4})?(?![A-Za-z0-9_])")
# Explicit protocol names, not a heuristic for capitalized words or headings.
# The suffix belongs to the name: MBP-IS must never match as plain MBP.
_NAME_HYPHEN = r"[-‐‑‒–—―−－]"
_TECHNICAL_NAME = re.compile(r"(?<![A-Za-z0-9_\-‐‑‒–—―−－])(?:PROFIBUS\s+MBP(?:" + _NAME_HYPHEN
                             + r"IS)?|FOUNDATION\s+Fieldbus\s+H1)(?![A-Za-z0-9_\-‐‑‒–—―−－])")
_PRIME_VARIABLE = re.compile(r"(?<![A-Za-z0-9_'’′])([A-Za-z])(['’′]{1,3})(?![A-Za-z0-9_'’′])")
_CITATION_NAME = (r"(?:[A-Z]\.\s*)*(?:(?:de|del|van|von|der|den|da|dos)\s+){0,2}"
                  r"(?P<surname>[^\W\d_]+(?:['’-][^\W\d_]+)*)")
_CITATION_AUTHORS = (r"(?:[A-Z]\.\s*)*(?:(?:de|del|van|von|der|den|da|dos)\s+){0,2}"
                     r"[^\W\d_]+(?:['’-][^\W\d_]+)*")
_CITATION_ITEM = re.compile(
    rf"(?P<authors>{_CITATION_AUTHORS}(?:\s+et\s+al\.|\s+(?:and|&)\s+{_CITATION_AUTHORS})?)"
    r"\s*,\s*(?:18|19|20)\d{2}[a-z]?(?:\s*,\s*(?:18|19|20)\d{2}[a-z]?)*"
)
_PROMPT_ECHO_GROUPS = tuple(re.compile(pattern, re.I) for pattern in (
    r"this is one text run from a scientific pdf|这(?:是|为)[^。\n]{0,16}(?:科学|科研)\s*pdf[^。\n]{0,12}(?:文本|文字)(?:运行|片段|段)",
    r"translate only this run|仅翻译(?:此|该|本)(?:运行|文本片段|文字片段)|只翻译(?:此|该)(?:运行|文本片段)",
    r"preserve all numbers,? units,? citations and mathematical symbols|保留所有数字[、，,\s]*单位[、，,\s]*(?:引用|引文|引证)[^。\n]{0,16}数学符号",
    r"add no tags,? placeholders,? json|(?:不添加|不要添加|不得添加)任何标签[、，,\s]*占位符[、，,\s]*json",
    r"placeholder repair rules|占位符修复规则|text-run preservation rules|文本片段保留规则|whole-paragraph recovery(?: rules)?|整段恢复(?:规则)?",
    r"the previous response did not preserve the source placeholders|上一次(?:响应|回复|回答)未(?:能)?保留(?:源|原文)占位符",
    r"is a conceptual idiom, not a technical identifier|translate its meaning naturally as|do not leave umbrella in English|将其含义自然翻译为|不要将\s*umbrella\s*保留为英文",
    r"technical terminology:|keep each small-script placeholder|a formula token may contain the integer part|技术术语[：:]|保持每个小(?:字号|下标)占位符",
    r"keep any unmatched source|its other half may be in another paragraph|其另一半可能(?:位于|在)另一个段落",
    r"preserve each mathematical prime|do not append a prime after a subscript|保留每个数学撇号|不要在下标后添加撇号",
    r"cross-page boundary rules|跨页边界规则|do not complete the truncated sentence|不要补全截断的句子",
    r"source-order grammar:|numeric preservation:|copy these plain-text numeric literals|源顺序语法[：:]|复制这些纯文本数字",
    r"anchored paragraph recovery rules|return a json object whose keys|do not output the fixed anchors|锚定段落恢复规则",
))


class PlaceholderIntegrityError(ValueError):
    """Only fixed category names are exposed; never document or API content."""

    def __init__(self, category: str):
        self.category = category
        super().__init__(category)


def _formula(placeholders):
    return [p for p in placeholders if hasattr(p, "formula")]


def _rich(placeholders):
    return [p for p in placeholders if hasattr(p, "left_placeholder")]


def _literal_tokens(ti):
    """Find raw source syntax, excluding exactly the engine-injected tokens."""
    text = ti.unicode
    expected = Counter()
    for p in _formula(ti.placeholders):
        expected[p.placeholder] += 1
    for p in _rich(ti.placeholders):
        expected[p.left_placeholder] += 1
        expected[p.right_placeholder] += 1
    actual = Counter(m.group(0) for pattern in (_FORMULA, _TAG) for m in pattern.finditer(text))
    # Metadata is needed when raw source tokens collide with injected syntax.
    originals = getattr(ti, "original_placeholder_tokens", {}) or {}
    for token, count in originals.items():
        if token in expected and count:
            raise PlaceholderIntegrityError("source-token-collision")
    return actual - expected


def _shield(text, literal_tokens):
    replacements = {}
    for index, (token, count) in enumerate(literal_tokens.items()):
        if text.count(token) != count:
            raise PlaceholderIntegrityError("literal-source-token-changed")
        sentinel = f"\ue000PB_LITERAL_{index}\ue001"
        if sentinel in text:
            raise PlaceholderIntegrityError("literal-sentinel-collision")
        text = text.replace(token, sentinel)
        replacements[sentinel] = token
    return text, replacements


def _restore(text, replacements):
    for sentinel, token in replacements.items():
        text = text.replace(sentinel, token)
    return text


def _encoded_tokens(text):
    """Inspect disguised markup without rewriting actual document contents."""
    projected = unicodedata.normalize("NFKC", text)
    for _ in range(8):
        decoded = unicodedata.normalize("NFKC", html.unescape(projected))
        if decoded == projected:
            break
        projected = decoded
    raw = Counter(match.group(0) for pattern in (_TAG, _FORMULA) for match in pattern.finditer(text))
    visible = Counter(match.group(0) for pattern in (_TAG, _FORMULA) for match in pattern.finditer(projected))
    return visible - raw


def _visible_text_map(ti, text):
    """Project engine tokens for matching, retaining every original text span."""
    formulas = {p.placeholder: p for p in _formula(ti.placeholders)}
    visible, positions = [], []
    previous = 0
    for match in list(_CANONICAL.finditer(text)) + [None]:
        end = match.start() if match else len(text)
        visible.append(text[previous:end])
        positions.extend((index, index + 1) for index in range(previous, end))
        if match:
            if match.group(0) in formulas:
                value = "".join(c.char_unicode or "" for c in formulas[match.group(0)].formula.pdf_character)
                # Unmapped glyphs remain an opaque formula token, never guessed.
                value = value or match.group(0)
                visible.append(value)
                positions.extend((match.start(), match.end()) for _ in value)
            previous = match.end()
    return "".join(visible), positions


def _classification_spans(ti, text):
    visible, positions = _visible_text_map(ti, text)
    return [(positions[match.start()][0], positions[match.end() - 1][1],
             re.sub(r"\s+", "", match.group(0)).casefold())
            for match in _SAFETY_CLASSIFICATION.finditer(visible)]


def _check_classifications(ti, output):
    source = Counter(value for _, _, value in _classification_spans(ti, ti.unicode))
    translated = Counter(value for _, _, value in _classification_spans(ti, output))
    if source != translated:
        raise PlaceholderIntegrityError("protected-classification-changed")


def _technical_name_spans(ti, text):
    visible, positions = _visible_text_map(ti, text)
    return [(positions[m.start()][0], positions[m.end() - 1][1],
             re.sub(_NAME_HYPHEN, "-", re.sub(r"\s+", " ", m[0])))
            for m in _TECHNICAL_NAME.finditer(visible)]


def _reduction_annotation_spans(ti, text):
    """A reduced-value annotation belongs to its proven small-script variable."""
    visible, _ = _visible_text_map(ti, ti.unicode)
    if not re.search(r"\b(?:reduced|reduction)\b", visible, re.I):
        return []
    pattern = re.compile(r"(?<![A-Za-z0-9_])(?P<base>[A-Za-z])(?P<token>\{v(?P<id>\d+)\})"
                         r"\s*[（(]red\.[)）]")
    bindings = _source_script_bindings(ti)
    annotated_keys = set()
    for match in pattern.finditer(ti.unicode):
        tail, _ = _visible_text_map(ti, ti.unicode[match.end():])
        if bindings.get(match["id"]) == match["base"] and re.match(r"\s*=", tail):
            annotated_keys.add(match["base"] + match["token"] + "(red.)")
    if not annotated_keys:
        return []
    return [(m.start(), m.end(), m["base"] + m["token"] + "(red.)")
            for m in pattern.finditer(text)
            if m["base"] + m["token"] + "(red.)" in annotated_keys]


def _check_technical_literals(ti, output):
    for spans, category in ((_technical_name_spans, "protected-technical-name-changed"),
                            (_reduction_annotation_spans, "protected-reduction-annotation-changed")):
        if Counter(v for _, _, v in spans(ti, ti.unicode)) != Counter(v for _, _, v in spans(ti, output)):
            raise PlaceholderIntegrityError(category)


def _citation_spans(ti, text):
    """Recognize complete parenthetical author-year groups, never bare numbers."""
    visible, positions = _visible_text_map(ti, text)
    spans = []
    non_authors = {"january", "february", "march", "april", "may", "june", "july", "august", "september",
                   "october", "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
                   "sep", "sept", "oct", "nov", "dec", "spring", "summer", "autumn", "winter",
                   "year", "figure", "table", "equation", "section", "appendix", "version"}
    for match in re.finditer(r"[（(]([^()（）]*)[)）]", visible):
        canonical = unicodedata.normalize("NFKC", match[0])
        items = canonical[1:-1].split(";")
        valid = True
        for item in items:
            parsed = _CITATION_ITEM.fullmatch(item.strip())
            if not parsed:
                valid = False
                break
            authors = parsed["authors"]
            names = re.split(r"\s+(?:and|&)\s+", re.sub(r"\s+et\s+al\.$", "", authors))
            for name in names:
                author = re.fullmatch(_CITATION_NAME, name)
                if (not author or not author["surname"][0].isupper()
                        or (len(names) == 1 and "et al." not in authors and author["surname"].casefold() in non_authors)):
                    valid = False
                    break
        if valid:
            spans.append((positions[match.start()][0], positions[match.end() - 1][1],
                          re.sub(r"\s+", "", canonical).casefold()))
    return spans


def _check_citations(ti, output):
    source = [value for _, _, value in _citation_spans(ti, ti.unicode)]
    translated = [value for _, _, value in _citation_spans(ti, output)]
    if source != translated:
        raise PlaceholderIntegrityError("author-year-citation-changed")


def _measurement_spans(ti, text):
    """Keep decimals whole even when a formula glyph ends inside the number."""
    visible, positions = _visible_text_map(ti, text)
    # Upstream can omit the space between an intact measurement formula and
    # an ordinary conjunction: {v1}and, where v1 is the original '1 Ω' glyphs.
    # Add only a recognition separator at this proven token boundary. Never
    # separate a partial decimal, a unit suffix, or a compound-unit operator.
    formulas = {p.placeholder: p for p in _formula(ti.placeholders)}
    separators = []
    for token in _FORMULA.finditer(text):
        placeholder = formulas.get(token[0])
        if placeholder is None or not re.match(
                r"(?:and|or|but|which|that|while|when|where|with|without|must|shall|may|can|then)(?![A-Za-z0-9_])",
                text[token.end():]):
            continue
        value = "".join(c.char_unicode or "" for c in placeholder.formula.pdf_character)
        if not value or value[-1].isspace() or not _MEASUREMENT.fullmatch(value.strip()):
            continue
        indices = [index for index, span in enumerate(positions) if span == token.span()]
        if indices:
            separators.append((indices[-1] + 1, token.end()))
    for index, boundary in reversed(separators):
        visible = visible[:index] + " " + visible[index:]
        positions.insert(index, (boundary, boundary))
    opaque = [m.span() for m in _URL_OR_EMAIL.finditer(visible)]
    spans = []
    for match in _MEASUREMENT.finditer(visible):
        if any(a <= match.start() < b for a, b in opaque):
            continue
        number = re.sub(r"\s+", "", match["number"]).replace(",", ".").replace("−", "-")
        unit = match["unit"]
        for alias in sorted(_UNIT_ALIASES, key=len, reverse=True):
            if unit.startswith(alias):
                unit = _UNIT_ALIASES[alias] + unit[len(alias):]
                break
        unit = re.sub(r"\s+", "", unicodedata.normalize("NFKC", unit)).replace("µ", "μ")
        left, right = positions[match.start()][0], positions[match.end() - 1][1]
        # Ci=0 may be one original formula, preceded by the plain C. Preserve
        # that base as part of the opaque range, not just the fractional tail.
        if (text[left:left+2] == "{v" and left and text[left-1].isascii() and text[left-1].isalpha()
                and (left == 1 or not re.match(r"[A-Za-z0-9_]", text[left-2]))):
            left -= 1
        spans.append((left, right, number + " " + unit))
    return spans


def _standard_spans(ti, text):
    visible, positions = _visible_text_map(ti, text)
    return [(positions[m.start()][0], positions[m.end()-1][1],
             re.sub(r"\s+", "", m[0]).replace("‑", "-").replace("–", "-"))
            for m in _STANDARD_ID.finditer(visible)]


def _check_measurements_and_standards(ti, output):
    for finder, category in ((_measurement_spans, "measurement-value-or-unit-changed"),
                             (_standard_spans, "standard-identifier-changed")):
        before = Counter(value for _, _, value in finder(ti, ti.unicode))
        after = Counter(value for _, _, value in finder(ti, output))
        if before != after:
            raise PlaceholderIntegrityError(category)


def _check_parenthesis_boundaries(ti, output):
    def unmatched(text):
        visible, _ = _visible_text_map(ti, text)
        opens, closes = 0, 0
        for char in unicodedata.normalize("NFKC", visible):
            if char == "(":
                opens += 1
            elif char == ")":
                if opens:
                    opens -= 1
                else:
                    closes += 1
        return opens, closes
    # Another source paragraph can hold the other half of a caption. Do not
    # "repair" that boundary and duplicate its separately preserved bracket.
    # Complete parenthetical phrasing may change or be omitted naturally.
    source_boundary, output_boundary = unmatched(ti.unicode), unmatched(output)
    if source_boundary != output_boundary:
        raise PlaceholderIntegrityError("parenthesis-boundary-changed")
    if any(source_boundary):
        def counts(text):
            visible, _ = _visible_text_map(ti, text)
            normalized = unicodedata.normalize("NFKC", visible)
            return normalized.count("("), normalized.count(")")
        # A preserved cross-page opening plus a new pair around the same
        # definition looks like '((... )' and must not pass a net-balance check.
        if counts(ti.unicode) != counts(output):
            raise PlaceholderIntegrityError("parenthesis-boundary-changed")
        source_literals = _unmatched_parenthesis_spans(ti, ti.unicode)
        output_literals = _unmatched_parenthesis_spans(ti, output)
        if len(source_literals) == len(output_literals):
            for (source_left, source_right, _), (left, right, _) in zip(source_literals, output_literals):
                source_char, char = ti.unicode[source_left:source_right], output[left:right]
                # A removed pair elsewhere must not mask a doubled cross-page
                # opening. Check the unmatched boundary itself, not only totals.
                if source_char in "(（" and char in "(（":
                    old = ti.unicode[source_right:].lstrip()[:1]
                    new = output[right:].lstrip()[:1]
                    if new in set("(（") and new != old:
                        raise PlaceholderIntegrityError("parenthesis-boundary-changed")
                elif source_char in ")）" and char in ")）":
                    old = ti.unicode[:source_left].rstrip()[-1:]
                    new = output[:left].rstrip()[-1:]
                    if new in set(")）") and new != old:
                        raise PlaceholderIntegrityError("parenthesis-boundary-changed")


def _prime_rich_atoms(ti, text):
    """Locate only source-proven prime variables with small literal subscripts.

    C'<small>wire/shield</small> is a mathematical identifier, not a quoted
    English word. Keep that descriptor literal and styled during recovery.
    Ordinary possessives, contractions and quoted letters do not qualify.
    """
    atoms = []
    size = getattr(ti.base_style, "font_size", None)
    if not size:
        return atoms
    for p in _rich(ti.placeholders):
        chars = p.composition.pdf_character
        body = "".join(c.char_unicode or "" for c in chars)
        if (not re.fullmatch(r"[A-Za-z0-9]+(?:[/_-][A-Za-z0-9]+)*", body)
                or not chars or any(not c.pdf_style or not c.pdf_style.font_size
                                   or c.pdf_style.font_size >= size * 0.85 for c in chars)):
            continue
        wrapper = re.escape(p.left_placeholder) + r"(.*?)" + re.escape(p.right_placeholder)
        pattern = re.compile(r"(?<![A-Za-z0-9_'’′])([A-Za-z])(['’′]{1,3})" + wrapper + r"(['’′]*)", re.S)
        source = list(pattern.finditer(ti.unicode))
        if len(source) != 1 or source[0][3] != body:
            continue
        found = list(pattern.finditer(text))
        atoms.append({"id": p.id, "source": source[0], "matches": found})
    return atoms


def _prime_candidates(ti, text, atoms):
    # Mask entire rich variable groups, preserving raw indices for source spans.
    # Otherwise a C' beside a translated subscript would be counted twice.
    masked = text
    for atom in atoms:
        for match in reversed(atom["matches"]):
            masked = masked[:match.start()] + " " * len(match[0]) + masked[match.end():]
    visible, positions = _visible_text_map(ti, masked)
    candidates = []
    for m in _PRIME_VARIABLE.finditer(visible):
        mathematical = ("′" in m[2]
                        or bool(re.search(r"[=+−×÷*/<>≤≥]\s*$", visible[:m.start()]))
                        or bool(re.match(r"\s*[=+−×÷*/<>≤≥]", visible[m.end():])))
        candidates.append((positions[m.start()][0], positions[m.end()-1][1],
                           m[1], len(m[2]), mathematical))
    return candidates


def _math_prime_data(ti, text):
    source_atoms = _prime_rich_atoms(ti, ti.unicode)
    source_candidates = _prime_candidates(ti, ti.unicode, source_atoms)
    bases = {a["source"][1] for a in source_atoms}
    bases.update(base for _, _, base, _, mathematical in source_candidates if mathematical)
    atoms = _prime_rich_atoms(ti, text)
    candidates = [c for c in _prime_candidates(ti, text, atoms) if c[2] in bases or (bases and c[4])]
    return atoms, candidates


def _math_prime_spans(ti, text):
    atoms, candidates = _math_prime_data(ti, text)
    spans = [(m.start(), m.end(), "math-prime") for a in atoms for m in a["matches"]]
    return spans + [(left, right, "math-prime") for left, right, *_ in candidates]


def _check_math_primes(ti, output):
    before_atoms, before = _math_prime_data(ti, ti.unicode)
    after_atoms, after = _math_prime_data(ti, output)
    for source, translated in zip(before_atoms, after_atoms):
        expected = source["source"]
        matches = translated["matches"]
        if (len(matches) != 1 or matches[0][1] != expected[1]
                or len(matches[0][2]) != len(expected[2])
                or matches[0][3] != expected[3]
                or len(matches[0][4]) != len(expected[4])):
            raise PlaceholderIntegrityError("mathematical-prime-variable-changed")
    if Counter((base, count) for _, _, base, count, _ in before) != Counter(
            (base, count) for _, _, base, count, _ in after):
        raise PlaceholderIntegrityError("mathematical-prime-variable-changed")


def _accent_word_spans(ti, text):
    """Source-font accent glyphs inside a complete capitalized Latin word.

    These are lexical accents, not mathematical primes or small superscripts.
    The spelling and original formula objects remain unchanged.
    """
    size, font = getattr(ti.base_style, "font_size", None), getattr(ti.base_style, "font_id", None)
    if not size or not font:
        return []
    accents = set()
    for p in _formula(ti.placeholders):
        chars = [c for c in p.formula.pdf_character if (c.char_unicode or "").strip()]
        if (len(chars) == 1 and chars[0].char_unicode in {"´", "¨", "\u0301", "\u0308"}
                and chars[0].pdf_style and chars[0].pdf_style.font_id == font
                and abs((chars[0].pdf_style.font_size or 0) - size) <= size * 0.05):
            accents.add(str(p.id))
    word_pattern = r"(?<![A-Za-z0-9_-])(?:[A-Za-zı]+|\{v\d+\})+(?:[-‐‑–](?:[A-Za-zı]+|\{v\d+\})+)*(?![A-Za-z0-9_-])"
    result = []
    for match in re.finditer(word_pattern, text):
        tokens = list(_FORMULA.finditer(match[0]))
        letters = _FORMULA.sub("", match[0])
        if (not tokens or not match[0][0].isupper() or len(re.findall(r"[A-Za-zı]", letters)) < 3
                or not re.search(r"[a-zı]", letters)
                or any(t[1] not in accents or t.start() == 0 or t.end() == len(match[0])
                       or match[0][t.start()-1] not in "aeiouyAEIOUYı"
                       or not re.search(r"[A-Za-zı]{2,}$", match[0][:t.start()])
                       or not match[0][t.end()].isalpha() for t in tokens)):
            continue
        result.append((match.start(), match.end(), match[0]))
    return result


def _check_accent_words(ti, output):
    for _, _, word in _accent_word_spans(ti, ti.unicode):
        # A raw substring inside a longer or changed name is not preservation.
        pattern = r"(?<![A-Za-z0-9_-])" + re.escape(word) + r"(?![A-Za-z0-9_-])"
        if len(re.findall(pattern, output)) != 1:
            raise PlaceholderIntegrityError("source-accent-word-changed")


def _unmatched_parenthesis_spans(ti, text):
    visible, positions = _visible_text_map(ti, text)
    opens, unmatched = [], []
    for index, char in enumerate(visible):
        if char in "(（":
            opens.append(index)
        elif char in ")）":
            if opens:
                opens.pop()
            else:
                unmatched.append(index)
    result = []
    for index in unmatched + opens:
        left, right = positions[index]
        # Formula-contained parentheses participate in balance but are never
        # split out, normalized, or separately hidden behind a literal token.
        if right == left + 1 and text[left:right] in "()（）":
            result.append((left, right, "unmatched-parenthesis"))
    return result


def _protected_literal_spans(ti, text):
    # A protected citation may include an existing formula placeholder. Restore
    # its original span, so parsing still receives that exact formula object.
    merged = []
    for left, right, _ in sorted(_classification_spans(ti, text) + _citation_spans(ti, text)
                                + _measurement_spans(ti, text) + _standard_spans(ti, text)
                                + _technical_name_spans(ti, text) + _reduction_annotation_spans(ti, text)
                                + _math_prime_spans(ti, text) + _accent_word_spans(ti, text)
                                + _unmatched_parenthesis_spans(ti, text)
                                + _certified_script_spans(ti, text)):
        if merged and left < merged[-1][1]:
            merged[-1] = (merged[-1][0], max(right, merged[-1][1]))
        else:
            merged.append((left, right))
    return merged


def _prose_text(ti, text):
    # Identifiers can contain English-looking path components or label words.
    # Exclude them from language detection only; structural validation still
    # checks protected identifiers independently and never rewrites the source.
    for left, right in reversed(_protected_literal_spans(ti, text)):
        text = text[:left] + " " + text[right:]
    return _URL_OR_EMAIL.sub(" ", _CANONICAL.sub("", text))


def _contextual_terms(ti):
    source, _ = _visible_text_map(ti, ti.unicode)
    terms = []
    if (re.search(r"\bhigh[-‑– ]power\s+trunk\b", source, re.I)
            and re.search(r"\b(?:concept|fieldbus|FISCO|spurs?|cables?)\b", source, re.I)
            and not re.search(r"\b(?:vehicle|car|automotive|chassis|bodywork)\b", source, re.I)):
        terms.append(("车体", "high-power-trunk-mistranslated",
                      " Technical terminology: high-power trunk refers to the fieldbus 干线, not a vehicle body; translate the concept using 干线."))
    if re.search(r"\bvirtuous\s+cycle\b", source, re.I):
        terms.append(("美德循环", "virtuous-cycle-mistranslated",
                      " Technical terminology: virtuous cycle means a reinforcing beneficial feedback loop, 良性循环, in this context."))
    return terms


def _affirmed_wrong_term(output, term):
    """Allow explicit negation or discussion of a quoted mistranslation."""
    for match in re.finditer(re.escape(term), output):
        prefix = output[max(0, match.start()-28):match.start()]
        suffix = output[match.end():match.end()+28]
        quoted_prefix = r"[\s“‘\"'「『]*$"
        rejected_before = re.search(
            r"(?:不是|并非|而非|不等于|不意味着|不代表|不要|不能|不可|不应|不该|避免)"
            r"(?:被|将其)?(?:译为|译成|译作|翻译为|翻译成|称为|理解为)?" + quoted_prefix,
            prefix)
        discussed_before = re.search(r"(?:误译|错译|误称|错误(?:地)?翻译)(?:为|成|作)?" + quoted_prefix, prefix)
        rejected_after = re.match(
            r"[\s”’\"'」』]*(?:(?:这一|这种|的)?(?:译法|翻译|说法|用词))?"
            r"(?:(?:是|属于|为)?(?:错误|错译|误译|不准确|不正确)|(?:并)?不是(?:正确|准确|恰当))", suffix)
        if not (rejected_before or discussed_before or rejected_after):
            return True
    return False


def _ordinary_english_residuals(ti, output):
    """Flag source-attested prose, never guess that every Latin token is prose.

    Formula glyphs, measurements, citations, protocol names and URLs are masked
    first. The small lexical set contains ordinary modifiers/connectives rather
    than scientific identifiers. Longer copied clauses require multiple common
    function words; names such as University of Cambridge remain permissible.
    This is a retry signal, not an automatic find/replace translation.
    """
    source, translated = _prose_text(ti, ti.unicode), _prose_text(ti, output)
    if not re.search(r"[\u3400-\u9fff]", translated):
        return []
    ordinary = set("galvanically exclusively respectively approximately therefore however "
                   "whereas although because consequently additionally furthermore nevertheless "
                   "otherwise subsequently previously simultaneously alternatively necessarily "
                   "sufficiently significantly typically generally usually permissible applicable "
                   "corresponding according provided regardless unless whenever wherever "
                   "intrinsically experimentally theoretically resulting".split())
    source_words = set(re.findall(r"(?<![A-Za-z0-9_])[A-Za-z]+(?![A-Za-z0-9_])", source))
    residuals = set()
    for match in re.finditer(r"(?<![A-Za-z0-9_])[A-Za-z]+(?![A-Za-z0-9_])", translated):
        word = match[0]
        # Uppercase abbreviations and differently cased proper names are not
        # evidence of the source's lowercase English modifier surviving.
        prose_inflection = (word.islower() and len(word) >= 7 and word.endswith(("edly", "ingly", "ically", "tively", "quently", "ulated")))
        if word in source_words and (word.lower() in ordinary or prose_inflection) and not word.isupper():
            residuals.add(word)
    # A source-initial preposition is prose only with this syntactic context.
    # This must not reject the element In, the SI unit in, or a formula I_n.
    if (re.match(r"\s*In\s+(?:all|the|this|these|those|such|other|both|each|order)\b", source)
            and re.match(r"\s*In(?=$|[^A-Za-z0-9_])", translated)):
        residuals.add("In")
    function_words = {"the", "is", "are", "was", "were", "with", "from", "for", "of", "and", "to", "in", "by"}
    for match in re.finditer(r"(?<![A-Za-z0-9_])[A-Za-z]+(?:[ \t]+[A-Za-z]+){3,}(?![A-Za-z0-9_])", translated):
        phrase = match[0]
        words = phrase.split()
        # Multiple title-case words indicate a name/title, not a copied clause.
        if sum(w[0].isupper() for w in words) > 1:
            continue
        if sum(w.lower() in function_words for w in words) < 2:
            continue
        if re.search(r"(?<![A-Za-z0-9_])" + re.escape(phrase) + r"(?![A-Za-z0-9_])", source):
            residuals.add(phrase)
    return sorted(residuals)


def _untranslated_numbered_labels(ti, output):
    """A numbered prose reference is translatable; its number stays protected."""
    source, _ = _visible_text_map(ti, ti.unicode)
    translated, _ = _visible_text_map(ti, output)
    pattern = re.compile(r"(?<![A-Za-z0-9_])(?:Figures?|Tables?|Equations?|Sections?|Appendices|Appendix)"
                         r"\s+(?:\d+(?:\.\d+)*|[A-Z]\d+)(?![A-Za-z0-9_])")
    source_labels = {re.sub(r"\s+", " ", m[0]) for m in pattern.finditer(source)}
    return sorted(source_labels & {re.sub(r"\s+", " ", m[0]) for m in pattern.finditer(translated)})


def _prose_numeric_literals(ti, text):
    """Multi-digit counts and decimal values outside opaque scientific objects.

    Single digits can legitimately become ordinary Chinese enumeration words.
    For larger counts/decimal values require the existing prompt's Arabic-digit
    form so omissions like 10000 training epochs cannot silently pass. Digits
    inside variables (5BLC), formulas, citations and protected units are separate
    contracts and are excluded here, never duplicated into prose.
    """
    text = _separate_prose_numbers(_prose_text(ti, text))
    pattern = re.compile(r"(?<![A-Za-z0-9_.])\d+(?:\.\d+)?(?![A-Za-z0-9_]|\.\d)")
    return Counter(m[0] for m in pattern.finditer(text) if len(m[0]) > 1)


def _joined_prose_number_spans(text):
    """Recognize narrow PDF whitespace losses, without reclassifying names."""
    patterns = (
        r"(?<![A-Za-z0-9_])(?:in|from|during|since|between|for|of|to|by|at|on|and|or|the|as|with)"
        r"(?P<number>\d+(?:\.\d+)?)(?![A-Za-z0-9_])",
        r"(?<![A-Za-z0-9_])(?P<number>\d+(?:\.\d+)?)(?:training|epochs?|days?|steps?|trials?|years?|samples?|nodes?)(?![A-Za-z0-9_])",
    )
    return sorted({match.span('number') for pattern in patterns for match in re.finditer(pattern, text, re.I)})


def _separate_prose_numbers(text):
    # This is only an analysis representation; do not edit source PDF strings.
    for left, right in reversed(_joined_prose_number_spans(text)):
        text = text[:left] + ' ' + text[left:right] + ' ' + text[right:]
    return text


def _scientific_identifier_matches(text):
    # A digit embedded in an ASCII name is part of the name, not a count:
    # 5BLC, BLC6, p53, V0 and H2O must not become "five BLC" etc. Ordinary
    # English ordinal words are translatable and have a separate numeric gate.
    joined_numbers = _joined_prose_number_spans(text)
    return [m for m in re.finditer(r"(?<![A-Za-z0-9_])[A-Za-z0-9_]+(?![A-Za-z0-9_])", text)
            if re.search(r"[A-Za-z]", m[0]) and re.search(r"[0-9]", m[0])
            and not re.fullmatch(r"\d+(?:st|nd|rd|th)", m[0], re.I)
            and not any(m.start() <= left < right <= m.end() for left, right in joined_numbers)]


def _prose_scientific_identifiers(ti, text):
    return Counter(m[0] for m in _scientific_identifier_matches(_prose_text(ti, text)))


def _check_body_translation(translator, paragraph, ti, output):
    # References, proper names and short labels can legitimately stay unchanged.
    # A long body paragraph returned verbatim must not pass the quality gate.
    label = str(getattr(paragraph, "layout_label", "")).lower().replace(" ", "_")
    source_text = _prose_text(ti, ti.unicode)
    output_text = _prose_text(ti, output)
    words = re.findall(r"[A-Za-z]{2,}", source_text)
    prose_words = {"the", "of", "for", "and", "in", "on", "with", "from", "to", "by", "is", "are", "as",
                   "through", "toward", "towards", "how", "what", "why", "challenges", "methods", "results",
                   "discussion", "conclusion", "conclusions", "introduction", "abstract", "references", "acknowledgments",
                   "advancing", "science", "scientific", "artificial", "intelligence", "learning", "deep", "neural",
                   "network", "networks", "attention", "language", "model", "models", "mathematical", "analysis",
                   "experiments", "experimental", "background", "limitations", "evaluation", "applications",
                   "contents", "overview", "features", "requirements", "installation", "grounding", "safety",
                   "intrinsic", "protection", "verification", "assumptions", "parameters", "results", "conclusions"}
    ordinary_title = label == "title" and bool(set(w.lower() for w in words) & prose_words)
    # Layout models use several labels for prose (plain_text, list_item, caption,
    # and sometimes unknown labels). Exclude explicit non-prose instead of using
    # a narrow text-only allowlist. Proper-name-only author lists remain valid.
    non_prose = label in {"reference", "references", "bibliography", "code", "formula", "equation", "isolate_formula"}
    natural_language = bool(set(w.lower() for w in words) & prose_words) or sum(w.islower() for w in words) >= 3
    ordinary_body = not non_prose and len(words) >= 8 and natural_language
    chinese_target = str(getattr(translator.translation_config, "lang_out", "")).lower().startswith("zh")
    if chinese_target:
        for wrong, category, _ in _contextual_terms(ti):
            if _affirmed_wrong_term(output_text, wrong):
                raise PlaceholderIntegrityError(category)
        for source_pattern, translated_pattern, category in (
            (r"\bnon[- ]intrinsically\s+safe\s+circuits?\b",
             r"非(?:本安|本质安全)(?:型)?电路|\bnon[- ]intrinsically\s+safe\s+circuits?\b",
             "non-intrinsically-safe-category-changed"),
        ):
            required = len(re.findall(source_pattern, source_text, re.I))
            if required and len(re.findall(translated_pattern, output_text, re.I)) < required:
                raise PlaceholderIntegrityError(category)
    if (chinese_target and not non_prose and re.search(r"\bunder\s+the\s+umbrella\s+of\b", source_text, re.I)
            and re.search(r"[\u3400-\u9fff]", output_text) and re.search(r"\bumbrella\b", output_text, re.I)):
        raise PlaceholderIntegrityError("untranslated-contextual-idiom")
    if (chinese_target
            and (ordinary_title or ordinary_body)
            and not re.search(r"[\u3400-\u9fff]", output_text)):
        raise PlaceholderIntegrityError("untranslated-prose-paragraph")
    if chinese_target and not non_prose and not _preserve_publication_metadata(ti, label):
        if _ordinary_english_residuals(ti, output) or _untranslated_numbered_labels(ti, output):
            raise PlaceholderIntegrityError("untranslated-prose-fragment")
        if _prose_numeric_literals(ti, ti.unicode) != _prose_numeric_literals(ti, output):
            raise PlaceholderIntegrityError("prose-numeric-literal-changed")
        if _prose_scientific_identifiers(ti, ti.unicode) != _prose_scientific_identifiers(ti, output):
            raise PlaceholderIntegrityError("scientific-identifier-changed")


def _fixed_label_translation(translator, ti):
    if _formula(ti.placeholders) or not str(getattr(translator.translation_config, "lang_out", "")).lower().startswith("zh"):
        return None
    labels = {"articleinfo": "文章信息", "abstract": "摘要", "keywords": "关键词"}
    source = ti.unicode
    for placeholder in _rich(ti.placeholders):
        source = source.replace(placeholder.left_placeholder, "").replace(placeholder.right_placeholder, "")
    source = unicodedata.normalize("NFKC", source).strip()
    colon = source.endswith(":")
    key = re.sub(r"\s+", "", source[:-1] if colon else source).casefold()
    if key in labels and not _literal_tokens(ti):
        return labels[key] + ("：" if colon else "")
    return None


def _is_affiliation_metadata(ti):
    """Recognize source-marked institutional addresses, not prose mentioning them."""
    markers = []
    for placeholder in ti.placeholders:
        if hasattr(placeholder, "formula"):
            characters = placeholder.formula.pdf_character
            pattern = re.escape(placeholder.placeholder)
        else:
            characters = placeholder.composition.pdf_character
            pattern = (re.escape(placeholder.left_placeholder) + r"\s*([a-z])\s*"
                       + re.escape(placeholder.right_placeholder))
        value = "".join(c.char_unicode or "" for c in characters).strip()
        if not re.fullmatch(r"[a-z]", value):
            continue
        matches = list(re.finditer(pattern, ti.unicode))
        if len(matches) == 1 and (not matches[0].lastindex or matches[0][1] == value):
            markers.append((matches[0].start(), matches[0].end(), value))
    markers.sort()
    if (len(markers) < 2 or len({value for _, _, value in markers}) != len(markers)
            or ti.unicode[:markers[0][0]].strip()):
        return False
    institution = re.compile(r"\b(?:University|Laboratory|Institute|College|School|Department|Hospital|Academy|Centre|Center)\b")
    connectors = {"of", "for", "and", "the", "de", "del", "di", "van", "von", "in"}
    for index, (_, end, _) in enumerate(markers):
        following = markers[index + 1][0] if index + 1 < len(markers) else len(ti.unicode)
        source_segment = ti.unicode[end:following]
        if _FORMULA.search(source_segment):
            return False
        segment = _CANONICAL.sub("", source_segment).strip()
        words = re.findall(r"[^\W\d_]+(?:['’.-][^\W\d_]+)*", segment, re.UNICODE)
        if (segment.count(",") < 2 or not institution.search(segment)
                or re.search(r"[{}<>:;!?]", segment)
                or not words or any(not word[0].isupper() and word not in connectors for word in words)):
            return False
    return True


def _is_credit_metadata(visible):
    """Require complete Name: official CRediT-role lists, with no free prose."""
    name_word = r"(?:[A-Z][A-Za-z'’-]+|[A-Z]\.)"
    headers = list(re.finditer(rf"\b{name_word}(?:\s+{name_word}){{1,3}}\s*:", visible))
    if len(headers) < 2 or visible[:headers[0].start()].strip():
        return False
    roles = {"conceptualization", "data curation", "formal analysis", "funding acquisition", "investigation",
             "methodology", "project administration", "resources", "software", "supervision", "validation",
             "visualization", "writing - original draft", "writing - review & editing"}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(visible)
        body = visible[header.end():end].strip(" \t\r\n.;")
        # Repair PDF line-break hyphenation only for recognition, never in the
        # preserved source, names, role text or original character compositions.
        body = re.sub(r"(?<=\w)-\s+(?=\w)", "", body)
        body = re.sub(r"\s*[-–—]\s*", " - ", body)
        listed = [re.sub(r"\s+", " ", role.strip()).casefold() for role in body.split(",")]
        if not listed or any(role not in roles for role in listed):
            return False
    return True


def _is_unnumbered_affiliation(ti, visible):
    """Recognize a complete institutional postal line, including source accents."""
    for placeholder in _formula(ti.placeholders):
        chars = placeholder.formula.pdf_character
        if not chars or any((c.char_unicode or "").strip() not in {"", "¨", "\u0308"} for c in chars):
            return False
    # Recognition only: never rewrite the original accent glyph or composition.
    projected = re.sub(r"([A-Za-z])\s*[¨\u0308]", lambda m: m[1] + "\u0308", visible)
    projected = unicodedata.normalize("NFC", projected).strip().rstrip(".")
    countries = {"Germany", "China", "United States", "USA", "United Kingdom", "UK", "France", "Italy",
                 "Spain", "Japan", "Canada", "Australia", "Netherlands", "Switzerland", "Austria", "Belgium",
                 "Sweden", "Norway", "Denmark", "Finland", "India", "South Korea", "Republic of Korea",
                 "Brazil", "Argentina", "Mexico", "Portugal", "Poland", "Czech Republic", "Turkey", "Taiwan",
                 "Singapore", "South Africa", "New Zealand", "Ireland", "Greece", "Israel"}
    if (len(projected) > 350 or projected.count(",") < 2
            or projected.rsplit(",", 1)[-1].strip() not in countries
            or not re.match(r"(?:Institut|Institute|University|Department|Laboratory|College|School|Hospital|Centre|Center)\b", projected)
            or re.search(r"[\d{}<>:;!?=+*/]", projected)):
        return False
    connectors = {"of", "for", "and", "the", "de", "del", "di", "van", "von", "in", "für", "der", "und", "du", "des"}
    words = re.findall(r"[^\W\d_]+(?:['’.-][^\W\d_]+)*", projected, re.UNICODE)
    return bool(words and all(word[0].isupper() or word in connectors for word in words))


def _is_numbered_affiliation(ti, visible):
    """Whole numbered institutional addresses; source accents stay original."""
    size, font = getattr(ti.base_style, "font_size", None), getattr(ti.base_style, "font_id", None)
    if not size or not font:
        return False
    for p in _formula(ti.placeholders):
        chars = [c for c in p.formula.pdf_character if (c.char_unicode or "").strip()]
        if (len(chars) != 1 or chars[0].char_unicode not in {"´", "¨", "\u0301", "\u0308"}
                or not chars[0].pdf_style or chars[0].pdf_style.font_id != font
                or abs((chars[0].pdf_style.font_size or 0) - size) > size * 0.05):
            return False
    # This is a recognition projection only, not a rewrite of glyphs or names.
    projected = re.sub(r"([A-Za-zı])\s*([´¨\u0301\u0308])",
                       lambda m: m[1].replace("ı", "i") + ("\u0301" if m[2] in {"´", "\u0301"} else "\u0308"), visible)
    projected = unicodedata.normalize("NFC", projected).strip()
    markers = list(re.finditer(r"(?:^|,\s*)([1-9])\s+", projected))
    if (not 2 <= len(markers) <= 9 or markers[0].start() != 0
            or [int(m[1]) for m in markers] != list(range(1, len(markers) + 1))):
        return False
    countries = {"Spain", "Germany", "France", "Italy", "China", "United States", "United States of America",
                 "USA", "United Kingdom", "UK", "Canada", "Australia", "Japan", "Netherlands", "Switzerland"}
    connectors = {"of", "for", "and", "the", "de", "del", "di", "van", "von", "in", "la", "las", "los", "y", "du", "des"}
    for index, marker in enumerate(markers):
        end = markers[index + 1].start() if index + 1 < len(markers) else len(projected)
        segment = projected[marker.end():end].strip().rstrip(".")
        institution = re.match(r"(?:Department|Departamento|Institute|Instituto|University|Universidad|Laboratory|College|School|Hospital|Centre|Center)\b", segment)
        company = re.fullmatch(r"[A-Z][A-Za-z'’-]+(?:\s+[A-Z][A-Za-z'’-]+){0,3}\s+(?:Solutions|Laboratories|Research)", segment.split(",", 1)[0])
        words = re.findall(r"[^\W\d_]+(?:['’.-][^\W\d_]+)*", segment, re.UNICODE)
        if (segment.count(",") < 2 or segment.rsplit(",", 1)[-1].strip() not in countries
                or not (institution or company) or re.search(r"[\d{}<>:;!?=+*/]", segment)
                or not words or any(not w[0].isupper() and w not in connectors for w in words)):
            return False
    return True


def _is_correspondence_metadata(visible):
    """Keep a complete corresponding-author address/email block verbatim."""
    heading = re.match(r"[\s\d*∗†‡.()\[\]]*Corresponding\s+author\s+at\s*:\s*", visible, re.I)
    if not heading:
        return False
    email_label = re.search(r"\bE-?mail\s+address\s*:\s*", visible[heading.end():], re.I)
    if not email_label:
        return False
    address = visible[heading.end():heading.end() + email_label.start()].strip()
    words = re.findall(r"[^\W\d_]+(?:['’.-][^\W\d_]+)*", address, re.UNICODE)
    connectors = {"of", "for", "and", "the", "de", "del", "di", "van", "von", "in"}
    if (address.count(",") < 2 or not re.search(r"\b(?:University|Laboratory|Institute|College|School|Department|Hospital|Academy|Centre|Center)\b", address)
            or re.search(r"[{}<>:;!?]", address) or not words
            or any(not word[0].isupper() and word not in connectors for word in words)):
        return False
    tail = visible[heading.end() + email_label.end():].lstrip()
    email = _URL_OR_EMAIL.match(tail)
    if not email or "@" not in email[0] or re.match(r"https?://|www\.", email[0], re.I):
        return False
    tail = tail[email.end():].lstrip()
    name_word = r"(?:[A-Z][A-Za-z'’-]+|[A-Z]\.)"
    name = re.match(rf"\({name_word}(?:\s+{name_word}){{1,3}}\)", tail)
    if name:
        tail = tail[name.end():].lstrip()
    if not tail.strip(" \t\r\n."):
        return True
    links = list(_URL_OR_EMAIL.finditer(tail))
    if not links or any(not re.match(r"https?://|www\.", link[0], re.I) for link in links):
        return False
    return bool(re.fullmatch(r"[\s\d*∗†‡.,;:()\[\]{}-]*", _URL_OR_EMAIL.sub("", tail)))


def _preserve_publication_metadata(ti, layout_label=None):
    visible, _ = _visible_text_map(ti, ti.unicode)
    # A numbered URL/email footnote has no translatable prose. Keep its original
    # link text and superscript objects, without making the model invent wording.
    if _URL_OR_EMAIL.search(visible):
        remainder = _URL_OR_EMAIL.sub("", visible)
        if re.fullmatch(r"[\s\d*∗†‡.,;:()\[\]{}-]*", remainder):
            return True
    dates = r"\b\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4}\b"
    publication_label = r"\b(?:Received(?:\s+in\s+revised\s+form)?|Accepted|Available\s+online|Published\s+online)\b"
    if re.match(publication_label, visible.strip(), re.I) and re.search(dates, visible, re.I):
        remaining = re.sub(publication_label, "", re.sub(dates, "", visible, flags=re.I), flags=re.I)
        if not remaining.strip(" \t\r\n;:.,"):
            return True
    if (_is_affiliation_metadata(ti)
            or (str(layout_label).lower() != "title"
                and (_is_unnumbered_affiliation(ti, visible) or _is_numbered_affiliation(ti, visible)))):
        return True
    if _is_credit_metadata(visible):
        return True
    if _is_correspondence_metadata(visible):
        return True
    # Author lists carry affiliation/symbol superscripts. Require several names,
    # commas, such a superscript and no sentence words; never guess translated names.
    name = r"\b(?:[A-Z][A-Za-z'-]+|[A-Z]\.)(?:\s+(?:[A-Z][A-Za-z'-]+|[A-Z]\.)){1,3}\b"
    superscripts = ["".join(c.char_unicode or "" for c in p.formula.pdf_character).strip()
                    for p in _formula(ti.placeholders)]
    words = {word.casefold() for word in re.findall(r"[A-Za-z]+", visible)}
    sentence_words = {"the", "of", "for", "and", "with", "from", "are", "is", "this", "that", "we", "our", "using"}
    if (visible.count(",") >= 2 and len(re.findall(name, visible)) >= 2
            and any(re.fullmatch(r"[a-z0-9*∗†‡]+", text) for text in superscripts if text)
            and not (words & sentence_words)):
        remaining = re.sub(name, "", visible)
        if re.fullmatch(r"[\s,a-z0-9*∗†‡;]+", remaining):
            return True
    return False


def _pure_descriptor_relation(ti):
    """Keep complete source equations whose variable descriptors are small runs.

    Require every atom to be a single Latin base plus a source small subscript,
    at least three atoms, a relation and a sum. Only the mathematical modifier
    'total' is accepted; any surrounding natural language keeps the prose path.
    """
    size = getattr(ti.base_style, "font_size", None)
    if not size:
        return False
    text, atom_count, descriptor_count = ti.unicode, 0, 0
    for p in _rich(ti.placeholders):
        chars = p.composition.pdf_character
        body = "".join(c.char_unicode or "" for c in chars)
        if body and body.isspace():
            continue
        if (not re.fullmatch(r"[A-Za-z0-9]{1,32}(?:[/_-][A-Za-z0-9]{1,32})*", body)
                or not chars or any(not c.pdf_style or not c.pdf_style.font_size
                                   or c.pdf_style.font_size >= size * 0.85 for c in chars)):
            return False
        atom = (r"(?<![A-Za-z0-9_])([A-Za-z])" + re.escape(p.left_placeholder + body + p.right_placeholder))
        matches = list(re.finditer(atom, text))
        if len(matches) != 1:
            return False
        text = re.sub(atom, "@", text, count=1)
        atom_count += 1
        descriptor_count += len(body) > 1
    if atom_count < 3 or not descriptor_count:
        return False
    visible, _ = _visible_text_map(ti, text)
    visible = re.sub(r"^\s*•?\s*", "", visible).strip()
    operand = r"(?:total\s+)?@"
    return bool(re.fullmatch(r"@\s*(?:[<>]=?|[=≤≥])\s*" + operand
                            + r"(?:\s*[+−-]\s*" + operand + r")+\s*[.;]?", visible, re.I))


def _pure_technical_expression(ti):
    """Preserve source-only calculations; mixed explanatory prose must translate."""
    if _pure_descriptor_relation(ti):
        return True
    visible, _ = _visible_text_map(ti, ti.unicode)
    # A standalone reduced-value calculation can be a separate PDF paragraph,
    # without the preceding prose's "reduced" context. Recognize only a leading
    # proven variable+small-script annotation and a multi-step multiplication
    # equality. The complete remainder must still pass the pure-math grammar
    # below; keep original compositions, not a translated/rewritten equation.
    note = re.match(r"\s*(?P<base>[A-Za-z])\{v(?P<id>\d+)\}\s*(?P<note>\(red\.?\))", ti.unicode)
    if (note and _source_script_bindings(ti).get(note["id"]) == note["base"]
            and visible.count("=") >= 2 and re.search(r"[×*]", visible)):
        following, _ = _visible_text_map(ti, ti.unicode[note.end():])
        if re.match(r"\s*=", following):
            projected = ti.unicode[:note.start("note")] + ti.unicode[note.end("note"):]
            visible, _ = _visible_text_map(ti, projected)
    if not re.search(r"\d", visible) or re.search(r"[\u3400-\u9fff]", visible):
        return False
    if re.search(r"\b(?:if|in|on|as|is|it|we|he|be|by|of|to|or|no)\b", visible, re.I):
        return False
    # Strip units before short variables. A normal sentence leaves words and
    # therefore cannot take this path, even if it contains a complete equation.
    remaining = re.sub(r"(?<![A-Za-z])" + _SI_UNIT + r"(?![A-Za-z])", "", visible)
    remaining = re.sub(r"\bat\b", "", remaining)
    remaining = re.sub(r"(?<![A-Za-z])(?:[A-Z][a-z]?|[a-z]|[α-ωΑ-Ω])(?![A-Za-z])", "", remaining)
    if not re.fullmatch(r"[\s\d.,:;()\[\]{}=+−\-×÷*/<>≤≥≈±∓·∗^'’²³°•]+", remaining):
        return False
    return bool(_MEASUREMENT.search(visible) or re.search(r"[=+−×÷<>≤≥]", visible))


def _without_rich_wrappers(ti, whitespace_only=False):
    text, literals = _shield(ti.unicode, _literal_tokens(ti))
    prime_styles = {atom["id"] for atom in _prime_rich_atoms(ti, ti.unicode)}
    remaining = []
    removed = 0
    for placeholder in ti.placeholders:
        if not hasattr(placeholder, "left_placeholder"):
            remaining.append(placeholder)
            continue
        if placeholder.id in prime_styles:
            # These small runs are mathematical subscripts, not optional
            # emphasis. The complete variable is opaque to the recovery LLM.
            remaining.append(placeholder)
            continue
        chars = "".join(c.char_unicode or "" for c in placeholder.composition.pdf_character)
        if whitespace_only and (not chars or not chars.isspace()):
            remaining.append(placeholder)
            continue
        pattern = re.escape(placeholder.left_placeholder) + r"(.*?)" + re.escape(placeholder.right_placeholder)
        matches = list(re.finditer(pattern, text, re.S))
        if len(matches) != 1 or (whitespace_only and not matches[0][1].isspace()):
            raise PlaceholderIntegrityError("source-style-structure")
        text = re.sub(pattern, lambda m: m[1], text, count=1, flags=re.S)
        removed += 1
    reduced = type(ti)(_restore(text, literals), remaining, ti.base_style)
    reduced.original_placeholder_tokens = dict(getattr(ti, "original_placeholder_tokens", {}) or {})
    return reduced, removed


def _same_source_face(font_map, left, right):
    if left == right:
        return True
    names = [getattr(font_map.get(key), "name", None) for key in (left, right)]
    return bool(all(names) and re.sub(r"^[A-Z]{6}\+", "", names[0])
                == re.sub(r"^[A-Z]{6}\+", "", names[1]))


def _small_script_character(ti, characters, font_map=None):
    base_size = getattr(ti.base_style, "font_size", None)
    base_font = getattr(ti.base_style, "font_id", None)
    if not base_size or not base_font or any(not c.char_unicode for c in characters):
        return None
    visible = [c for c in characters if not c.char_unicode.isspace()]
    if len(visible) != 1:
        return None
    char = visible[0]
    style = char.pdf_style
    if (not re.fullmatch(r"[A-Za-z0-9]", char.char_unicode) or not style
            or not _same_source_face(font_map or {}, style.font_id, base_font)
            or not style.font_size or style.font_size >= base_size * 0.85):
        return None
    return char


def _script_glyph_state(char):
    return (char.char_unicode, char.pdf_character_id, char.pdf_style.font_id, char.pdf_style.font_size,
            getattr(char, "xobj_id", None),
            tuple(getattr(char.box, key) for key in ("x", "y", "x2", "y2")),
            tuple(getattr(char.visual_bbox.box, key) for key in ("x", "y", "x2", "y2"))
            if char.visual_bbox else None)


def _script_proof(placeholder):
    proof = getattr(placeholder, "pagebridge_script_proof", None)
    if proof is not None:
        chars = placeholder.formula.pdf_character
        if (placeholder.formula is not proof["formula"] or len(chars) != len(proof["characters"])
                or any(a is not b for a, b in zip(chars, proof["characters"]))
                or (placeholder.formula.x_offset, placeholder.formula.y_offset) != proof["offsets"]
                or any(_script_glyph_state(c) != state for c, state in proof["glyphStates"])):
            raise PlaceholderIntegrityError("source-script-proof-changed")
    return proof


def _unit_script_matches(text, placeholder):
    return list(re.finditer(r"(?<![A-Za-z0-9_.])(?P<value>\d+(?:[.,]\d+)?)\s*"
                            r"(?P<unit>mm|cm|m)\s*" + re.escape(placeholder.placeholder), text))


def _check_unit_script_bindings(ti, text):
    for placeholder in _formula(ti.placeholders):
        proof = _script_proof(placeholder)
        if not proof or proof["kind"] != "unit":
            continue
        matches = _unit_script_matches(text, placeholder)
        if (len(matches) != 1 or matches[0]["unit"] != proof["base"]
                or matches[0]["value"].replace(",", ".") != proof["value"]):
            raise PlaceholderIntegrityError("unit-exponent-pairing")


def _certified_script_spans(ti, text):
    spans = []
    for placeholder in _formula(ti.placeholders):
        proof = _script_proof(placeholder)
        if not proof:
            continue
        matches = (_unit_script_matches(text, placeholder) if proof["kind"] == "unit" else
                   list(re.finditer(r"(?<![A-Za-z0-9_])" + re.escape(proof["base"] + placeholder.placeholder), text)))
        spans.extend((m.start(), m.end(), "source-script") for m in matches)
    return spans


def _source_script_bindings(ti):
    """Known small-script pairings, independent of count or sentence length."""
    bindings = {}
    for placeholder in _formula(ti.placeholders):
        proof = _script_proof(placeholder)
        if proof:
            if proof["kind"] == "variable":
                bindings[str(placeholder.id)] = proof["base"]
            continue
        if not _small_script_character(ti, placeholder.formula.pdf_character):
            continue
        matches = list(re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z])" + re.escape(placeholder.placeholder), ti.unicode))
        if len(matches) == 1:
            bindings[str(placeholder.id)] = matches[0][1]
    return bindings


def _promote_rich_scripts(ti, paragraph, font_map=None):
    """Retain original small-script glyphs and their actual baseline offset."""
    from babeldoc.format.pdf.document_il import PdfFormula
    from babeldoc.format.pdf.document_il.midend.il_translator import FormulaPlaceholder
    from babeldoc.format.pdf.document_il.utils.formular_helper import update_formula_data
    font_map = font_map or {}
    characters = []
    for comp in paragraph.pdf_paragraph_composition:
        for name in ("pdf_line", "pdf_same_style_characters", "pdf_formula"):
            value = getattr(comp, name, None)
            if value:
                characters.extend(value.pdf_character)
        char = getattr(comp, "pdf_character", None)
        if char:
            characters.append(char)
    text, placeholders, count = ti.unicode, [], 0
    for placeholder in ti.placeholders:
        rich = hasattr(placeholder, "composition")
        original_chars = (placeholder.composition.pdf_character if rich else placeholder.formula.pdf_character)
        script = _small_script_character(ti, original_chars, font_map)
        if script is None:
            placeholders.append(placeholder)
            continue
        wrapper = (placeholder.left_placeholder + script.char_unicode + placeholder.right_placeholder
                   if rich else placeholder.placeholder)
        matches = list(re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z])" + re.escape(wrapper), text))
        unit_matches = (list(re.finditer(r"(?<![A-Za-z0-9_.])(?P<value>\d+(?:[.,]\d+)?)\s*"
                                        r"(?P<unit>mm|cm|m)(?P<gap>\s*)" + re.escape(wrapper), text))
                        if script.char_unicode in ("2", "3") else [])
        unit = len(unit_matches) == 1
        if not unit and (not rich or len(matches) != 1):
            placeholders.append(placeholder)
            continue
        base = unit_matches[0]["unit"] if unit else matches[0][1]
        candidates = [c for c in characters if c.char_unicode == base[-1] and c.box and script.box
                      and c.pdf_style and _same_source_face(font_map, c.pdf_style.font_id, script.pdf_style.font_id)
                      and _same_source_face(font_map, c.pdf_style.font_id, ti.base_style.font_id)
                      and getattr(c, "xobj_id", None) == getattr(script, "xobj_id", None)
                      and c.pdf_style.font_size >= ti.base_style.font_size * 0.85
                      and abs(script.box.x - c.box.x2) < ti.base_style.font_size * 0.6
                      and ti.base_style.font_size * 0.1 < abs(script.box.y - c.box.y) < ti.base_style.font_size * 0.65]
        if len(candidates) != 1 or not script.visual_bbox:
            raise PlaceholderIntegrityError("source-small-script-geometry")
        base_chars = [candidates[0]]
        if unit:
            # Unit powers must be superscripts beside one complete source unit,
            # not a small digit after an arbitrary last letter of a word.
            if script.box.y <= candidates[0].box.y:
                raise PlaceholderIntegrityError("source-unit-exponent-geometry")
            for letter in reversed(base[:-1]):
                right = base_chars[0]
                previous = [c for c in characters if c.char_unicode == letter and c.box and c.pdf_style
                            and _same_source_face(font_map, c.pdf_style.font_id, right.pdf_style.font_id)
                            and getattr(c, "xobj_id", None) == getattr(right, "xobj_id", None)
                            and abs(c.pdf_style.font_size - right.pdf_style.font_size) < right.pdf_style.font_size * .05
                            and abs(c.box.y - right.box.y) < right.pdf_style.font_size * .05
                            and -.1 * right.pdf_style.font_size < right.box.x - c.box.x2 < .15 * right.pdf_style.font_size]
                if len(previous) != 1:
                    raise PlaceholderIntegrityError("source-unit-exponent-geometry")
                base_chars.insert(0, previous[0])
        formula = (PdfFormula(pdf_character=list(original_chars)) if rich else placeholder.formula)
        if rich:
            update_formula_data(formula)
            formula.x_offset = formula.box.x - candidates[0].box.x2
            formula.y_offset = formula.box.y - candidates[0].box.y
        token = f"{{v{placeholder.id}}}"
        prepared = FormulaPlaceholder(placeholder.id, formula, token, re.escape(token)) if rich else placeholder
        prepared.pagebridge_script_proof = {"kind": "unit" if unit else "variable", "base": base,
            "value": unit_matches[0]["value"].replace(",", ".") if unit else None,
            "formula": formula, "characters": tuple(original_chars),
            "offsets": (formula.x_offset, formula.y_offset),
            "glyphStates": tuple((c, _script_glyph_state(c)) for c in base_chars + list(original_chars))}
        placeholders.append(prepared)
        if unit:
            match = unit_matches[0]
            # Remove only the parser's textual gap before this proven exponent;
            # the original glyph and its measured x/y offsets remain unchanged.
            text = text[:match.start("gap")] + token + text[match.end():]
        else:
            text = text.replace(wrapper, token, 1)
        count += int(rich)
    if not count and text == ti.unicode:
        return ti, 0
    promoted = type(ti)(text, placeholders, ti.base_style)
    promoted.original_placeholder_tokens = dict(getattr(ti, "original_placeholder_tokens", {}) or {})
    return promoted, count


def _atomic_subscript_anchors(ti, text):
    """Identify only intact prose variables with an original small subscript."""
    anchors = {}
    base_size = getattr(ti.base_style, "font_size", None)
    base_font = getattr(ti.base_style, "font_id", None)
    if not base_size or not base_font:
        return None
    operators = "+-=−×÷*/\\^<>≤≥±∓·∗"
    for placeholder in _formula(ti.placeholders):
        chars = placeholder.formula.pdf_character
        if any(not c.char_unicode for c in chars):
            return None
        visible_chars = [c for c in chars if not c.char_unicode.isspace()]
        if len(visible_chars) != 1:
            return None
        char = visible_chars[0]
        style = char.pdf_style
        if (not re.fullmatch(r"[A-Za-z0-9]", char.char_unicode)
                or not style or style.font_id != base_font or not style.font_size or style.font_size >= base_size * 0.85):
            return None
        matches = list(re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z])" + re.escape(placeholder.placeholder), text))
        if len(matches) != 1 or text.count(placeholder.placeholder) != 1:
            return None
        match = matches[0]
        if (text[:match.start()].rstrip()[-1:] in set(operators + "(（")
                or text[match.end():].lstrip()[:1] in set(operators + ")）")):
            return None
        anchors[str(placeholder.id)] = (match[1], match.start(), match.end())
    ordered = sorted(anchors.values(), key=lambda anchor: anchor[1])
    for previous, current in zip(ordered, ordered[1:]):
        gap = text[previous[2]:current[1]]
        # Ordinary hyphenated words are not subtraction. All mathematical
        # adjacency, function arguments, and short symbolic lists stay strict.
        without_word_hyphens = re.sub(r"(?<=[A-Za-z])-(?=[A-Za-z])", "", gap)
        if (any(char in operators for char in without_word_hyphens)
                or re.search(r"\b(?:plus|minus|equal|less than|greater than|multiplied|divided)\b|[小大]于|等于|乘以|除以", gap, re.I)):
            return None
        english = re.findall(r"[A-Za-z]{2,}", gap)
        chinese = re.findall(r"[\u3400-\u9fff]", gap)
        if len(english) < 3 and len(chinese) < 4:
            return None
    return anchors


def _atomic_prose_source(ti, source):
    if _rich(ti.placeholders) or len(_formula(ti.placeholders)) != 2:
        return None
    anchors = _atomic_subscript_anchors(ti, source)
    if not anchors:
        return None
    ordered = sorted(anchors.items(), key=lambda item: item[1][1])
    inductance, current = [re.escape(value[0] + "{v" + number + "}") for number, value in ordered]
    # A long prose gap does not establish independent quantities: e.g. the
    # ratio of L to I must never become the ratio of I to L. Only this complete
    # source relation establishes a maximum inductance for a given current.
    sentence = (r"(?:^|(?<=[.!?]))\s*The\s+(?:[A-Za-z]+\s+){0,5}curve\s+describes\s+the\s+"
                r"maximum\s+(?:\(insulated\)\s+)?inductance\s+" + inductance
                + r"\s*which\s+may\s+be\s+connected\s+to\s+an?\s+(?:associated\s+)?apparatus\s+"
                r"with\s+a\s+given\s+(?:current\s+)?" + current
                + r"\s*without\s+(?:adversely\s+)?affecting\s+intrinsic\s+safety\s*(?=[.!?]|$)")
    return anchors if len(re.findall(sentence, source, re.I)) == 1 else None


def _safe_atomic_prose_permutation(ti, source, output):
    source_ids, output_ids = _FORMULA.findall(source), _FORMULA.findall(output)
    if len(source_ids) != len(set(source_ids)) or Counter(source_ids) != Counter(output_ids):
        return False
    before, after = _atomic_prose_source(ti, source), _atomic_subscript_anchors(ti, output)
    if not before or not after or {key: value[0] for key, value in before.items()} != {key: value[0] for key, value in after.items()}:
        return False
    ordered = sorted(before.items(), key=lambda item: item[1][1])
    inductance, current = [re.escape(value[0] + "{v" + number + "}") for number, value in ordered]
    maximum = r"最大(?:（绝缘）|\(绝缘\))?电感" + inductance
    # Validate the translated roles and the non-interference condition too.
    # All intervening words belong to a positively recognized relation; no
    # arbitrary prose, ratio/difference operand list, or new negation fits.
    prefix = r"(?:(?:该|这条|(?:感应|电感)最小点火)曲线描述了)?"
    relation = (r"(?:与给定(?:电流)?" + current
                + r"相关联的(?:设备|装置)(?:可|允许)连接的" + maximum
                + r"，而不会(?:损害|影响)本质安全"
                + r"|在给定(?:电流)?" + current
                + r"且不影响本质安全时，(?:允许|可)连接的" + maximum + r")")
    sentence = r"(?:^|(?<=[。.!?！？]))" + prefix + relation + r"(?=[。.!?！？]|$)"
    return len(re.findall(sentence, re.sub(r"\s+", "", output))) == 1


def _safe_accent_word_permutation(ti, source, output):
    """Lexical accent movement may not reorder any genuine mathematical token."""
    ids = _FORMULA.findall(source)
    if len(ids) != len(set(ids)) or Counter(ids) != Counter(_FORMULA.findall(output)):
        return False
    words = _accent_word_spans(ti, source)
    if not words:
        return False
    try:
        _check_accent_words(ti, output)
    except PlaceholderIntegrityError:
        return False
    before, after = source, output
    for _, _, word in words:
        before, after = before.replace(word, "", 1), after.replace(word, "", 1)
    return _CANONICAL.findall(before) == _CANONICAL.findall(after)


def _assigned_script_atoms(ti):
    """An original small script followed by '= number', with its outer base/unit."""
    size, font = getattr(ti.base_style, "font_size", None), getattr(ti.base_style, "font_id", None)
    atoms = {}
    if not size or not font:
        return atoms
    for p in _formula(ti.placeholders):
        chars = [c for c in p.formula.pdf_character if (c.char_unicode or "").strip()]
        if (len(chars) < 3 or not _small_script_character(ti, chars[:1])
                or any(not c.pdf_style or c.pdf_style.font_id != font
                       or not c.pdf_style.font_size or abs(c.pdf_style.font_size - size) > size * 0.05
                       for c in chars[1:])):
            continue
        tail = "".join(c.char_unicode for c in chars[1:])
        if not re.fullmatch(r"=[+−-]?\d+(?:[.,]\d+)?", tail):
            continue
        matches = list(re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z])" + re.escape(p.placeholder)
                                  + r"\s*(" + _SI_UNIT + r")(?![A-Za-z0-9_])", ti.unicode))
        if len(matches) == 1:
            atoms[str(p.id)] = (matches[0][1], matches[0][2])
    return atoms


def _safe_variable_measurement_permutation(ti, source, output):
    """One source-proven 'determine X not for voltage A, but for B' relation."""
    if (_rich(ti.placeholders) or len(_formula(ti.placeholders)) != 2
            or len(re.findall(r"[A-Za-z]{2,}", _CANONICAL.sub("", source))) < 12):
        return False
    assigned, scripts = _assigned_script_atoms(ti), _source_script_bindings(ti)
    ids = _FORMULA.findall(source)
    if (len(assigned) != 1 or len(scripts) != 1 or set(assigned) & set(scripts)
            or len(ids) != len(set(ids)) or Counter(ids) != Counter(_FORMULA.findall(output))):
        return False
    assigned_id, (assigned_base, unit) = next(iter(assigned.items()))
    variable_id, variable_base = next(iter(scripts.items()))
    if unit != "V":
        return False
    variable = re.escape(variable_base + "{v" + variable_id + "}")
    voltage = re.escape(assigned_base + "{v" + assigned_id + "}") + r"\s*" + re.escape(unit)
    # Match the entire determining clause, not any English gap between atoms.
    source_sentence = (r"(?:^|(?<=[.!?]))\s*(?:This\s+means\s+that\s+)?(?:we\s+)?determine\s+"
                       + variable + r"\s*not\s+for\s+voltage\s+" + voltage
                       + r"\s*,\s*but\s+for\s+(?P<alternative>[+−-]?\d+(?:[.,]\d+)?)\s*"
                       + re.escape(unit) + r"\s*(?=[.!?]|$)")
    matches = list(re.finditer(source_sentence, source, re.I))
    if len(matches) != 1:
        return False
    alternative = re.escape(matches[0]["alternative"] + unit)
    target_sentence = (r"(?:^|(?<=[。.!?！？]))(?:这意味着)?(?:我们)?(?:不是|并非)针对电压"
                       + voltage + r"确定" + variable + r"，而是针对" + alternative
                       + r"(?:进行)?确定(?=[。.!?！？]|$)")
    return len(re.findall(target_sentence, re.sub(r"\s+", "", output))) == 1


def _safe_percentage_reference_permutation(ti, source, output):
    """Accept one source-proven percent-of pair in its Chinese noun order.

    English 'is {> 1}% of Lo and Co' can become 'Lo和Co的{> 1}%'. The
    original comparison object and two bound subscripts stay one local group;
    all other formula/style order remains exact. This is not a general math
    permutation rule, and never applies to standalone equations.
    """
    source_ids, output_ids = _FORMULA.findall(source), _FORMULA.findall(output)
    if (len(source_ids) != len(set(source_ids)) or Counter(source_ids) != Counter(output_ids)
            or len(re.findall(r"[A-Za-z]{2,}", _CANONICAL.sub("", source))) < 12
            or not re.search(r"\bif\b", source, re.I)):
        return False
    pattern = (r"(?P<limit>\{v(?P<limit_id>\d+)\})\s*[%％]\s+of\s+"
               r"(?P<left>[A-Za-z])(?P<left_token>\{v(?P<left_id>\d+)\})\s+and\s+"
               r"(?P<right>[A-Za-z])(?P<right_token>\{v(?P<right_id>\d+)\})")
    matches = list(re.finditer(pattern, source))
    if len(matches) != 1:
        return False
    group = matches[0]
    operators = set("+-=−×÷*/\\^<>≤≥±∓·∗(%％")
    if (not re.search(r"\bis\s*$", source[:group.start()], re.I)
            or source[group.end():].lstrip()[:1] in operators):
        return False
    formulas = {str(p.id): p for p in _formula(ti.placeholders)}
    limit = formulas.get(group["limit_id"])
    if limit is None:
        return False
    threshold = "".join(c.char_unicode or "" for c in limit.formula.pdf_character)
    if not re.fullmatch(r"\s*(?:[<>]=?|[≤≥])\s*\d+(?:[.,]\d+)?\s*", threshold):
        return False
    for name in ("left_id", "right_id"):
        placeholder = formulas.get(group[name])
        if placeholder is None or not _small_script_character(ti, placeholder.formula.pdf_character):
            return False
    translated_pattern = (r"(?<![A-Za-z0-9_])" + re.escape(group["left"] + group["left_token"])
                          + r"\s*(?:和|与|及|、)\s*" + re.escape(group["right"] + group["right_token"])
                          + r"\s*的\s*" + re.escape(group["limit"]) + r"\s*[%％]")
    translated = list(re.finditer(translated_pattern, output))
    if len(translated) != 1:
        return False
    match = translated[0]
    if (output[:match.start()].rstrip()[-1:] in operators
            or output[match.end():].lstrip()[:1] in operators):
        return False
    # Rebase only for comparison; never rewrite the translated sentence. This
    # also rejects an unrelated rich-style relocation or any cross-group move.
    rebased = output[:match.start()] + group[0] + output[match.end():]
    return _CANONICAL.findall(rebased) == _CANONICAL.findall(source)


def _safe_scientific_role_permutation(ti, source, output):
    """Prove three specific Chinese modifier inversions using both languages.

    Every participating original formula occurs exactly once. Each complete
    source phrase establishes two distinct named roles; a local Chinese phrase
    must attach the same token to the same role. Rebase only these proven spans
    for skeleton comparison. No source/output glyph, value or string is edited,
    and any other movement (including between sentences/groups) is rejected.
    """
    source_tokens = _FORMULA.findall(source)
    if (Counter(source_tokens) != Counter(_FORMULA.findall(output))
            or any(count != 1 for count in Counter(source_tokens).values())):
        return False
    known = {p.placeholder for p in _formula(ti.placeholders)}
    source_patterns = (
        ('size-duration', r"average size\s*(?P<a>\{v\d+\})\s*of avalanches "
         r"(?:that have a certain |of )?duration\s*(?P<b>\{v\d+\})"),
        ('threshold-window', r"In each time step, every node will increase its individual activation threshold\s*(?P<a>\{v\d+\})"
         r"\s*to 1 with a probability corresponding to its own average activity over the last\s*"
         r"(?P<b>\{v\d+\})\s*time steps"),
        ('forecast-supervision', r"The fifth and sixth rows are for the\s*(?P<a>\{v\d+\})\s*day wind \(real\) prediction in (?P<year>\d{4}) "
         r"by similar training but supervised by the winds of the\s*(?P<b>\{v\d+\})\s*days"),
    )
    replacements = []
    for kind, pattern in source_patterns:
        for group in re.finditer(pattern, source, re.I):
            if group['a'] not in known or group['b'] not in known or group['a'] == group['b']:
                return False
            a, b = re.escape(group['a']), re.escape(group['b'])
            if kind == 'size-duration':
                target = (r"(?:具有)?(?:特定|一定)?(?:持续时间|时长)(?:为)?\s*" + b
                          + r"\s*的雪崩(?:的|，其)?平均(?:大小|尺寸|规模)(?:为)?\s*" + a)
            elif kind == 'threshold-window':
                target = (r"在每(?:个|一)时间步(?:中)?[，,]每个节点(?:将|都会)(?:以)?与其(?:自身)?(?:在)?过去\s*" + b
                          + r"\s*个?时间步内?的平均(?:活动|活跃度|活性)(?:水平)?(?:相)?对应的概率[，,]?"
                          + r"将其(?:自身|个体)?的?激活阈值\s*" + a
                          + r"\s*(?:提高|提升|增加)(?:到|至)\s*1(?![0-9])")
            else:
                target = (r"第五行?和第六行用于(?:通过)?类似(?:的)?但?由\s*" + b
                          + r"\s*天的?风(?:进行)?监督的?训练(?:对|用于)\s*" + re.escape(group['year'])
                          + r"\s*年的?\s*" + a + r"\s*天的?风[（(](?:真实|实际)[)）]\s*预测")
            candidates = list(re.finditer(target, output))
            if len(candidates) > 1:
                return False
            if candidates:
                match = candidates[0]
                # These literal-role grammars cannot cross punctuation-defined
                # sentences or consume any third formula/style token.
                if _CANONICAL.findall(match[0]) != [group['b'], group['a']]:
                    return False
                replacements.append((match.start(), match.end(), group[0]))
    if not replacements:
        return False
    rebased = output
    previous = len(output) + 1
    for left, right, source_group in sorted(replacements, reverse=True):
        if right > previous:
            return False
        rebased = rebased[:left] + source_group + rebased[right:]
        previous = left
    return _CANONICAL.findall(rebased) == _CANONICAL.findall(source)


def _formula_descriptor_atoms(ti):
    matches = list(_FORMULA.finditer(ti.unicode))
    known = {p.placeholder for p in _formula(ti.placeholders)}
    atoms = []
    for left, right in zip(matches, matches[1:]):
        descriptor = ti.unicode[left.end():right.start()].strip()
        if descriptor in {'loc', 'pred'} and left[0] in known and right[0] in known:
            atoms.append((left[0], descriptor, right[0]))
    return atoms


def _check_formula_descriptors(ti, output):
    for left, descriptor, right in _formula_descriptor_atoms(ti):
        source_pattern = re.escape(left) + r'\s*' + re.escape(descriptor) + r'\s*' + re.escape(right)
        if len(re.findall(source_pattern, output)) != len(re.findall(source_pattern, ti.unicode)):
            raise PlaceholderIntegrityError('formula-descriptor-pairing')


def normalize_output(ti, output: str) -> str:
    """Canonicalize known aliases, then require the exact source token skeleton.

    Never erase an unknown tag or formula. It must cause a retry, since deleting
    it could also delete mathematical content or hide a hallucinated passage.
    """
    if not isinstance(output, str) or not output.strip():
        raise PlaceholderIntegrityError("empty-output")
    for pattern in _PROMPT_ECHO_GROUPS:
        if pattern.search(output) and not pattern.search(ti.unicode):
            raise PlaceholderIntegrityError("translation-prompt-echo")
    if _encoded_tokens(output) != _encoded_tokens(ti.unicode):
        raise PlaceholderIntegrityError("encoded-source-token-changed")
    literal_tokens = _literal_tokens(ti)
    source, _ = _shield(ti.unicode, literal_tokens)
    text, literals = _shield(output, literal_tokens)
    rich_ids = {str(p.id): p for p in _rich(ti.placeholders)}
    formula_ids = {str(p.id): p for p in _formula(ti.placeholders)}

    def formula_token(match):
        number = match.group(1)
        if number not in formula_ids:
            raise PlaceholderIntegrityError("unexpected-formula")
        return formula_ids[number].placeholder

    text = _FORMULA.sub(formula_token, text)
    stack = []

    def style_token(match):
        token = match.group(0)
        opening = _OPEN.fullmatch(token)
        if opening:
            number = opening.group(3)
            if number not in rich_ids:
                raise PlaceholderIntegrityError("unexpected-style")
            if stack:
                raise PlaceholderIntegrityError("nested-style")
            stack.append(opening.group(1).lower())
            return rich_ids[number].left_placeholder
        closing = _CLOSE.fullmatch(token)
        if closing:
            if not stack or stack.pop() != closing.group(1).lower():
                raise PlaceholderIntegrityError("unbalanced-style")
            return "</style>"
        raise PlaceholderIntegrityError("unknown-markup")

    text = _TAG.sub(style_token, text)
    if stack:
        raise PlaceholderIntegrityError("unbalanced-style")
    expected = _CANONICAL.findall(source)
    actual = _CANONICAL.findall(text)
    if actual != expected:
        source_formula = _FORMULA.findall(source)
        actual_formula = _FORMULA.findall(text)
        if actual_formula != source_formula:
            if not (_safe_atomic_prose_permutation(ti, source, text)
                    or _safe_percentage_reference_permutation(ti, source, text)
                    or _safe_variable_measurement_permutation(ti, source, text)
                    or _safe_accent_word_permutation(ti, source, text)
                    or _safe_scientific_role_permutation(ti, source, text)):
                raise PlaceholderIntegrityError("formula-count-or-order")
        else:
            raise PlaceholderIntegrityError("style-count-or-order")
    anchors = _source_script_bindings(ti)
    if anchors:
        # Even unchanged ID order cannot justify attaching a subscript to the
        # other physical quantity. Validate pairing separately from order.
        for placeholder in _formula(ti.placeholders):
            if str(placeholder.id) not in anchors:
                continue
            matches = list(re.finditer(r"(?<![A-Za-z0-9_])([A-Za-z])" + re.escape(placeholder.placeholder), text))
            if len(matches) != 1 or matches[0][1] != anchors[str(placeholder.id)]:
                raise PlaceholderIntegrityError("atomic-variable-pairing")
    _check_unit_script_bindings(ti, text)
    _check_formula_descriptors(ti, text)
    for number, (base, unit) in _assigned_script_atoms(ti).items():
        pattern = r"(?<![A-Za-z0-9_])" + re.escape(base + "{v" + number + "}") + r"\s*" + re.escape(unit) + r"(?![A-Za-z0-9_])"
        if len(re.findall(pattern, text)) != 1:
            raise PlaceholderIntegrityError("assigned-variable-pairing")
    _check_classifications(ti, _restore(text, literals))
    _check_technical_literals(ti, _restore(text, literals))
    _check_citations(ti, _restore(text, literals))
    _check_measurements_and_standards(ti, _restore(text, literals))
    _check_parenthesis_boundaries(ti, _restore(text, literals))
    _check_math_primes(ti, _restore(text, literals))
    _check_accent_words(ti, _restore(text, literals))
    # Upstream's style regex is deliberately single-line. Normalize only inside
    # engine wrappers so a model-added line break cannot silently remove styling.
    text = re.sub(r"(<style id='\d+'>)(.*?)(</style>)",
                  lambda m: m[1] + re.sub(r"[\r\n]+", " ", m[2]) + m[3],
                  text, flags=re.S)
    return _restore(text, literals)


def _record_pending(paragraph):
    with _lock:
        original = paragraph.unicode if isinstance(paragraph.unicode, str) else ""
        label = str(getattr(paragraph, "layout_label", "other"))
        if not re.fullmatch(r"[A-Za-z_ -]{1,48}", label):
            label = "other"
        state = _states.setdefault(id(paragraph), {
            "validated": False, "reasons": set(),
            "sourceParagraphSHA256": hashlib.sha256(original.encode("utf-8")).hexdigest(),
            "layoutLabel": label})
        state["validated"] = False


def _record_failure(paragraph, error):
    category = error.category if isinstance(error, PlaceholderIntegrityError) else "translation-request-failed"
    with _lock:
        state = _states.setdefault(id(paragraph), {"validated": False, "reasons": set()})
        state["validated"] = False
        state["reasons"].add(category)
        _counters["validationFailures"] += 1


def _record_validated(paragraph):
    with _lock:
        _states.setdefault(id(paragraph), {"reasons": set()})["validated"] = True


def _retry_translation(translator, ti, tracker, attempt):
    repair_rules = ("Placeholder repair rules: The previous response did not preserve the source placeholders. "
               "Copy every {vN}, <style id='N'> and </style> token exactly once, "
               "in its original order. Do not change style to span or translate "
               "tag names. Do not expand formula tokens into text. Return only "
               "the translated paragraph, without JSON or markdown. Preserve "
               "safety classification identifiers such as Zone 1/Division 2 and "
               "Class I, Div. 1 exactly as written in the source, including "
               "their numbers and combinations; never translate these labels. "
               "Preserve every parenthetical author-year citation exactly, including "
               "all author names, years, separators and the order of citation groups. "
               "Copy complete numeric measurements, decimal points, SI units and standard identifiers exactly. "
               "A formula token may contain the integer part of a decimal: never add or remove digits or punctuation beside it. "
               "Keep any unmatched source opening or closing parenthesis unmatched; its other half may be in another paragraph.")
    if str(getattr(translator.translation_config, "lang_out", "")).lower().startswith("zh"):
        repair_rules += (" Translate every ordinary English prose word into Chinese, including modifiers and prepositions within otherwise Chinese sentences. "
                         "Keep actual mathematical variables, proper names, protocol names and identifiers unchanged.")
    # BabelDOC's template ends in the actual source text. Never append any
    # instructions after it: models correctly interpret that suffix as source.
    prompt = repair_rules + _contextual_term_hint(translator, ti) + "\n\n" + translator.generate_prompt_for_llm(ti.unicode, translate_input=ti)
    with _lock:
        _counters["retryRequests"] += 1
    llm_tracker = tracker.new_llm_translate_tracker()
    llm_tracker.set_input(prompt)
    response = translator.translate_engine.llm_translate(
        prompt, ignore_cache=True,
        rate_limit_params={"paragraph_token_count": translator.calc_token_count(ti.unicode)})
    llm_tracker.set_output(response)
    return response


def _source_order_grammar_hint(ti):
    """Suggest equivalent Chinese source-order grammar for proven source clauses.

    This never authorizes a permutation. These are positive full source phrase
    matches; every actual response still passes exact token count/order and
    original-object validation. No model-generated text drives the rules.
    """
    hints = []
    source = ti.unicode
    known = {p.placeholder for p in _formula(ti.placeholders)}
    def genuine(match, names):
        return (match is not None and len({match[n] for n in names}) == len(names)
                and all(match[n] in known and source.count(match[n]) == 1 for n in names))
    size = re.search(r"average size\s*(?P<size>\{v\d+\})\s*of avalanches "
                     r"(?:that have a certain |of )?duration\s*(?P<duration>\{v\d+\})", source, re.I)
    if genuine(size, ('size', 'duration')):
        hints.append(" Source-order grammar: introduce the average size first, then identify its duration. "
                     "Use 雪崩的平均大小" + size['size'] + "，对应于持续时间为" + size['duration']
                     + "的雪崩. Keep the average-size token before the duration token; do not move the duration modifier to the beginning.")
    threshold = re.search(r"increase its individual activation threshold\s*(?P<threshold>\{v\d+\})"
                          r"\s*to 1 with a probability corresponding to its own average activity over the last\s*"
                          r"(?P<window>\{v\d+\})\s*time steps", source)
    if genuine(threshold, ('threshold', 'window')):
        hints.append(" Source-order grammar: state the threshold change before explaining its probability. "
                     "Use 每个节点将自身激活阈值" + threshold['threshold'] + "提高到1，其概率对应于自身的平均活跃度，统计窗口为过去"
                     + threshold['window'] + "个时间步. Keep the average-activity meaning before the window token and the subsequent probability definition and its formulas in source order.")
    forecast = re.search(r"rows are for the\s*(?P<forecast>\{v\d+\})\s*day wind \(real\) prediction "
                         r"in (?P<year>\d{4}) by similar training but supervised by the winds of the\s*"
                         r"(?P<training>\{v\d+\})\s*days", source)
    if genuine(forecast, ('forecast', 'training')):
        hints.append(" Source-order grammar: describe the forecast day first and the supervising day afterward. "
                     "Use 用于" + forecast['year'] + "年的" + forecast['forecast'] + "天风的实际预测，训练方式类似，"
                     "但监督信号来自" + forecast['training'] + "天的风. Do not prepose the supervising-day clause.")
    return "".join(hints)


def _contextual_term_hint(translator, ti):
    hints = []
    atoms = _formula_descriptor_atoms(ti)
    if atoms:
        hints.append(" These source mathematical labels are indivisible: "
                     + ', '.join(left + descriptor + right for left, descriptor, right in atoms)
                     + ". Copy each complete group exactly; loc/pred are mathematical subscripts, not ordinary prose. "
                     "Never omit the label or put explanatory wording between its formula tokens.")
    names = list(dict.fromkeys(v for _, _, v in _technical_name_spans(ti, ti.unicode)))
    if names:
        hints.append(" Technical terminology: preserve these complete protocol names exactly: " + ", ".join(names)
                     + ". Keep each name in the clause describing what it applies to, including the associated protection and installation limitations. "
                     "Do not omit an applicability object or append its name as an unrelated sentence.")
    if _reduction_annotation_spans(ti, ti.unicode):
        hints.append(" Technical terminology: (red.) marks a reduced mathematical value. "
                     "Preserve the literal annotation (red.) immediately after its original variable and small-script token; it is not the color red.")
    if _math_prime_spans(ti, ti.unicode):
        hints.append(" Preserve each mathematical prime, its variable and its original subscript literally. "
                     "Do not append a prime after a subscript or translate a subscript descriptor.")
    bindings = _source_script_bindings(ti)
    if bindings:
        pairs = ", ".join(f"{base}{{v{number}}}" for number, base in bindings.items())
        hints.append(" Keep each small-script placeholder immediately attached to its original Latin base: " + pairs + ".")
    units = []
    for placeholder in _formula(ti.placeholders):
        proof = _script_proof(placeholder)
        if proof and proof["kind"] == "unit":
            units.append(proof["value"] + " " + proof["base"] + placeholder.placeholder)
    if units:
        hints.append(" Keep each small-script placeholder with its complete original measurement and unit: "
                     + ", ".join(units) + ". Do not move an exponent to another measurement or translate its SI unit.")
    chinese_target = str(getattr(translator.translation_config, "lang_out", "")).lower().startswith("zh")
    if chinese_target:
        prose = _prose_text(ti, ti.unicode)
        hints.append(_source_order_grammar_hint(ti))
        numbers = _prose_numeric_literals(ti, ti.unicode)
        if numbers:
            hints.append(" Numeric preservation: copy these plain-text numeric literals with their source multiplicities: "
                         + ", ".join(f"{number} ({count} occurrence(s))" for number, count in numbers.items())
                         + ". Preserve their roles, including training-epoch counts and dates; do not write these values in Chinese number words.")
        identifiers = _prose_scientific_identifiers(ti, ti.unicode)
        if identifiers:
            hints.append(" Preserve the exact spelling and multiplicity of these scientific identifiers: "
                         + ", ".join(f"{name} ({count} occurrence(s))" for name, count in identifiers.items())
                         + ". Their embedded digits belong to the names, never translate them as counts.")
        if _untranslated_numbered_labels(ti, ti.unicode):
            hints.append(" Translate numbered prose reference labels: Figure/Figures as 图, Table/Tables as 表, "
                         "Equation/Equations as 式, Section/Sections as 节, and Appendix/Appendices as 附录; retain each original number.")
        if re.search(r"\bgalvanically\s+isolated\b", prose, re.I):
            hints.append(" Technical terminology: galvanically isolated means 电气隔离. Translate this complete expression into Chinese, including the modifier.")
        if re.search(r"\bexclusively\b", prose, re.I):
            hints.append(" Technical terminology: exclusively is an ordinary adverb meaning 专门 or 仅, not a technical name; translate its meaning with any surrounding qualifier such as almost.")
        if re.match(r"\s*In\s+(?:all|the|this|these|those|such|other|both|each|order)\b", prose):
            hints.append(" The initial word In is an English preposition in this sentence. Translate the entire initial phrase into Chinese; do not leave the word In before the Chinese text.")
        # This source relation puts the maximum connectible pair first and the
        # respective operating conditions second. Give the model a grammatical
        # Chinese subject-first form; retain the strict formula-order gate.
        pair = re.match(
            r"The maximum connectible (?P<a>[A-Za-z])\{v(?P<ai>\d+)\}\s*values and "
            r"(?P<b>[A-Za-z])\{v(?P<bi>\d+)\}\s*values for the respective values "
            r"(?P<c>[A-Za-z])\{v(?P<ci>\d+)\}\s*and "
            r"(?P<d>[A-Za-z])\{v(?P<di>\d+)\}\s*are determined by\b", ti.unicode)
        if pair and len({pair[key + "i"] for key in "abcd"}) == 4 and all(
                bindings.get(pair[key + "i"]) == pair[key] for key in "abcd"):
            atoms = [pair[key] + "{v" + pair[key + "i"] + "}" for key in "abcd"]
            hints.append(" Keep the maximum-connectible pair as the grammatical subject before its operating conditions. "
                         "In Chinese start this sentence with 最大可连接的" + atoms[0] + "值和" + atoms[1]
                         + "值, then state 对应于" + atoms[2] + "和" + atoms[3]
                         + "的相应值. Do not move the operating-condition pair to the beginning. "
                         "Preserve the original order of all subsequent variable tokens and their roles.")
    if chinese_target:
        hints.extend(hint for _, _, hint in _contextual_terms(ti))
    visible, _ = _visible_text_map(ti, ti.unicode)
    if chinese_target and re.search(r"\bnon[- ]intrinsically\s+safe\s+circuits?\b", visible, re.I):
        hints.append(" Technical terminology: non-intrinsically safe circuits means 非本安电路; preserve the negation and the intrinsic-safety category.")
    if (str(getattr(translator.translation_config, "lang_out", "")).lower().startswith("zh")
            and re.search(r"\bunder\s+the\s+umbrella\s+of\b", _prose_text(ti, ti.unicode), re.I)):
        hints.append(" In this source, 'under the umbrella of' is a conceptual idiom, not a technical identifier. "
                     "Translate its meaning naturally as 在…框架下 or 在…范畴内; do not leave umbrella in English.")
    return "".join(hints)


def _new_prose_parentheses_as_commas(text):
    """Normalize only model-added, balanced plain-Chinese aside punctuation.

    Caller must prove every source parenthesis is already an opaque token.
    Preserve the interior byte-for-byte. Nested/unbalanced pairs, Latin labels,
    formulas, protected tokens and mathematical syntax remain failures.
    """
    pairs, opened = [], None
    for index, char in enumerate(text):
        if char in '(（':
            if opened is not None:
                raise PlaceholderIntegrityError('parenthesis-boundary-changed')
            opened = (index, char)
        elif char in ')）':
            if opened is None or (opened[1], char) not in {('(', ')'), ('（', '）')}:
                raise PlaceholderIntegrityError('parenthesis-boundary-changed')
            left = opened[0]
            content = text[left+1:index]
            if (not re.search(r'[\u3400-\u9fff]{2}', content)
                    or not re.fullmatch(r'[\u3400-\u9fff0-9\s，、：；。！？,.!?;:–—]+', content)):
                raise PlaceholderIntegrityError('parenthesis-boundary-changed')
            pairs.append((left, index))
            opened = None
    if opened is not None:
        raise PlaceholderIntegrityError('parenthesis-boundary-changed')
    for left, right in reversed(pairs):
        text = text[:left] + '，' + text[left+1:right] + '，' + text[right+1:]
    return text, len(pairs)


def _whole_paragraph_translation(translator, ti, tracker):
    """Recover one complete paragraph, retaining all original formula objects.

    Rich styles may fall back to the existing paragraph base style. Safety
    identifiers are temporarily opaque, then restored before structural checks.
    No isolated word/date/name is ever submitted as a translation request.
    """
    plain, removed = _without_rich_wrappers(ti)
    source = plain.unicode
    prefix = "PBKEEP"
    while "{" + prefix + "_" in source:
        prefix += "X"
    protected = {}
    pieces, start = [], 0
    for index, (left, right) in enumerate(_protected_literal_spans(plain, source), 1):
        token = "{" + prefix + f"_{index:04d}" + "}"
        pieces.extend((source[start:left], token))
        protected[token] = source[left:right]
        start = right
    pieces.append(source[start:])
    protected_source = "".join(pieces)
    recovery_rules = ("Whole-paragraph recovery rules: Translate the entire input coherently. "
                      "Preserve author names, dates, email addresses and URLs as written. "
                      "Copy every formula placeholder and every {" + prefix + "_NNNN} "
                      "protected-literal token exactly once in the same order. "
                      "Never interpret, translate or expand these tokens. "
                      "Keep any unmatched source parenthesis unchanged; do not invent the other half. "
                      "Return the translation only, without tags or explanations.")
    if str(getattr(translator.translation_config, "lang_out", "")).lower().startswith("zh"):
        recovery_rules += (" Translate every ordinary English prose word into Chinese, including modifiers and prepositions within otherwise Chinese sentences. "
                           "Keep actual mathematical variables, proper names, protocol names and identifiers unchanged.")
    boundary_tokens = [token for token, original in protected.items() if original in ("(", ")", "（", "）")]
    all_source_parentheses_opaque = bool(boundary_tokens) and not re.search(r'[()（）]', protected_source)
    if boundary_tokens:
        visible_source, _ = _visible_text_map(plain, source)
        visible_source = unicodedata.normalize("NFKC", visible_source)
        recovery_rules += (" Cross-page boundary rules: " + ", ".join(boundary_tokens)
                           + " already represents an unmatched source parenthesis at a page boundary. "
                           "Do not add another parenthesis beside or around it. Preserve the original number of all other parentheses. "
                           "The source sentence is incomplete and continues on another page. "
                           "Do not complete the truncated sentence, definition, or missing noun; translate only the given fragment. "
                           f"The visible source contains exactly {visible_source.count('(')} opening and {visible_source.count(')')} closing parentheses in total. "
                           "Keep exactly these totals after the protected tokens are restored. Use commas for any new explanatory Chinese wording; do not add parenthetical asides.")
    request_engine = translator.translate_engine
    structured = False
    if all_source_parentheses_opaque:
        # One complete natural translation, not slots: constrain only punctuation
        # already proven absent outside opaque source atoms. This prevents the
        # model from inventing explanatory parentheses at a cross-page boundary
        # without constraining clause order or assigning prose to formula slots.
        response_format = _complete_translation_schema()
        request_engine = _structured_translation_engine(request_engine, response_format)
        structured = request_engine is not translator.translate_engine
        if structured:
            recovery_rules += (' Output format: return exactly one JSON object with a single key "translation". '
                               'Its value must contain the complete natural paragraph translation as one string, '
                               'with all formula and protected-literal tokens preserved. No other keys or commentary.')
    prompt = recovery_rules + _contextual_term_hint(translator, plain) + "\n\n" + translator.generate_prompt_for_llm(protected_source, translate_input=plain)
    with _lock:
        _counters["wholeParagraphRecoveryRequests"] += 1
    llm_tracker = tracker.new_llm_translate_tracker()
    llm_tracker.set_input(prompt)
    response = request_engine.llm_translate(
        prompt, ignore_cache=True,
        rate_limit_params={"paragraph_token_count": translator.calc_token_count(source)})
    llm_tracker.set_output(response)
    if structured:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise PlaceholderIntegrityError('whole-recovery-duplicate-key')
                result[key] = value
            return result
        try:
            value = json.loads(response, object_pairs_hook=unique_object)
        except (TypeError, ValueError):
            raise PlaceholderIntegrityError('whole-recovery-invalid-json') from None
        if not isinstance(value, dict) or set(value) != {'translation'} or not isinstance(value['translation'], str):
            raise PlaceholderIntegrityError('whole-recovery-response-shape')
        response = value['translation']
    if not isinstance(response, str):
        raise PlaceholderIntegrityError("empty-output")
    tokens = re.findall(r"\{" + prefix + r"_\d+\}", response)
    old_tokens = Counter(re.findall(r"\{PBKEEPX*_\d+\}", source))
    all_tokens = Counter(re.findall(r"\{PBKEEPX*_\d+\}", response))
    if tokens != list(protected) or all_tokens != old_tokens + Counter(protected.keys()):
        raise PlaceholderIntegrityError("protected-literal-token-changed")
    normalized_asides = 0
    if all_source_parentheses_opaque:
        response, normalized_asides = _new_prose_parentheses_as_commas(response)
    for token in boundary_tokens:
        for side in (0, 1):
            original_sides = protected_source.split(token, 1)
            output_sides = response.split(token, 1)
            original_near = original_sides[side].rstrip()[-1:] if side == 0 else original_sides[side].lstrip()[:1]
            output_near = output_sides[side].rstrip()[-1:] if side == 0 else output_sides[side].lstrip()[:1]
            if output_near in set("()（）") and output_near != original_near:
                raise PlaceholderIntegrityError("parenthesis-boundary-changed")
    for token, original in protected.items():
        response = response.replace(token, original)
    normalized = normalize_output(plain, response)
    if normalized_asides:
        with _lock:
            _counters['normalizedNewProseParenthesisPairs'] += normalized_asides
    return plain, normalized, removed


def _complete_translation_schema():
    return {'type': 'json_schema', 'json_schema': {
        'name': 'pagebridge_complete_translation', 'strict': True, 'schema': {
            'type': 'object', 'properties': {'translation': {
                'type': 'string', 'minLength': 1, 'pattern': r'^[^()（）]*$'}},
            'required': ['translation'], 'additionalProperties': False}}}


def _structured_translation_engine(engine, response_format):
    """Apply the structured output contract to this request only.

    The pinned OpenAI-compatible provider copies its options into the SDK call.
    A shallow clone keeps the same client, rate limiter and atomic counters but
    does not change shared options while other paragraphs are being translated.
    Unknown engines keep their existing interface and face the same local gates.
    Unsupported schemas fail safely through the normal paragraph failure path.
    """
    # Avoid importing a provider (and initializing its cache) for unrelated
    # engines. The actual supported instance necessarily loaded this class.
    if not any(cls.__name__ == 'OpenAITranslator'
               and cls.__module__ == 'pdf2zh_next.translator.translator_impl.openai'
               for cls in type(engine).__mro__):
        return engine
    from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
    if not isinstance(engine, OpenAITranslator):
        return engine
    structured = copy.copy(engine)
    structured.options = dict(engine.options, response_format=response_format)
    structured.enable_json_mode = False
    return structured


def _guard_parse(self, ti, output, tracker=None, llm_translate_tracker=None):
    output = normalize_output(ti, output)
    shielded, literals = _shield(output, _literal_tokens(ti))
    compositions = _originals["parse"](self, ti, shielded, tracker, llm_translate_tracker)
    by_id = {str(p.id): p.formula for p in _formula(ti.placeholders)}
    # normalize_output already validates count and source-variable pairing.
    # For the narrow valid prose permutation, verify each original object in
    # the actual token order, not just membership in a set of formula objects.
    expected = [by_id[number] for number in _FORMULA.findall(shielded)]
    actual = [c.pdf_formula for c in compositions if c.pdf_formula is not None]
    if len(actual) != len(expected) or any(a is not b for a, b in zip(actual, expected)):
        raise PlaceholderIntegrityError("formula-composition-mismatch")
    for composition in compositions:
        text = composition.pdf_same_style_unicode_characters
        if text:
            text.unicode = _restore(text.unicode, literals)
    return compositions


def _guard_pre(self, paragraph, tracker, *args, **kwargs):
    with _lock:
        previous = _states.get(id(paragraph))
        previous_validated = previous and previous["validated"]
    _record_pending(paragraph)
    try:
        if str(getattr(paragraph, "layout_label", "")).lower().replace(" ", "_") == "fallback_line":
            # Diagram/table fragments are not independent semantic sentences.
            # Preserve their original glyph coordinates instead of translating
            # isolated words such as "non-", "lead", or "Ex ia".
            with _lock:
                _counters["preservedFragmentLabels"] += 1
            result = (None, None)
        else:
            result = _originals["pre"](self, paragraph, tracker, *args, **kwargs)
        if result[0] is not None:
            if _preserve_publication_metadata(result[1], getattr(paragraph, "layout_label", None)):
                with _lock:
                    _counters["preservedMetadataParagraphs"] += 1
                result = (None, None)
            elif _pure_technical_expression(result[1]):
                with _lock:
                    _counters["preservedTechnicalExpressions"] += 1
                    if _pure_descriptor_relation(result[1]):
                        _counters["preservedDescriptorRelations"] += 1
                result = (None, None)
            else:
                font_map = kwargs.get("page_font_map", args[0] if args else {})
                xobj_maps = kwargs.get("xobj_font_map", args[1] if len(args) > 1 else {})
                font_map = xobj_maps.get(getattr(paragraph, "xobj_id", None), font_map)
                promoted, promoted_count = _promote_rich_scripts(result[1], paragraph, font_map)
                normalized, removed = _without_rich_wrappers(promoted, whitespace_only=True)
                if removed or promoted_count or normalized.unicode != result[0]:
                    tracker.set_input(normalized.unicode)
                    tracker.set_placeholders(normalized.placeholders)
                    with _lock:
                        if removed:
                            _counters["whitespaceStyleWrappersRemoved"] += removed
                            _counters["whitespaceNormalizedParagraphs"] += 1
                        if promoted_count:
                            _counters["promotedSmallScripts"] += promoted_count
                    result = (normalized.unicode, normalized)
    except Exception:
        _record_failure(paragraph, PlaceholderIntegrityError("paragraph-preparation-failed"))
        raise
    if result[0] is None:
        with _lock:
            if previous is None:
                _states.pop(id(paragraph), None)
            elif previous_validated:
                _states[id(paragraph)]["validated"] = True
    return result


def _guard_post(self, paragraph, tracker, ti, translated_text):
    _record_pending(paragraph)
    fixed_label = _fixed_label_translation(self, ti)
    candidate = fixed_label if fixed_label is not None else translated_text
    if fixed_label is not None:
        # A spaced Latin label may have a separate rich style for each letter.
        # Translate the exact recognized label using the original base style;
        # do not force a different number of Chinese characters into those runs.
        # This is the same source-only style reduction as paragraph recovery.
        ti, removed = _without_rich_wrappers(ti)
        with _lock:
            _counters["fixedLabelTranslations"] += 1
            if removed:
                _counters["fixedLabelRichTextFallbackParagraphs"] += 1
    compositions = None
    original_unicode = paragraph.unicode
    original_compositions = paragraph.pdf_paragraph_composition
    repaired = False
    for attempt in range(3):
        try:
            normalized = normalize_output(ti, candidate)
            _check_body_translation(self, paragraph, ti, normalized)
            compositions = self.parse_translate_output(ti, normalized, tracker, tracker.last_llm_translate_tracker())
            if normalized != candidate:
                with _lock:
                    _counters["normalizedResponses"] += 1
            candidate = normalized
            break
        except PlaceholderIntegrityError as error:
            _record_failure(paragraph, error)
            if attempt < 2:
                try:
                    candidate = _retry_translation(self, ti, tracker, attempt)
                    repaired = True
                except Exception as request_error:
                    _record_failure(paragraph, request_error)
                    break
    if compositions is None:
        try:
            recovered_input, candidate, removed = _whole_paragraph_translation(self, ti, tracker)
            _check_body_translation(self, paragraph, recovered_input, candidate)
            compositions = self.parse_translate_output(recovered_input, candidate, tracker, tracker.last_llm_translate_tracker())
            with _lock:
                _counters["wholeParagraphRecoveredParagraphs"] += 1
                if removed:
                    _counters["richTextFallbackParagraphs"] += 1
            repaired = True
        except Exception as error:
            _record_failure(paragraph, error)
            # A structured slot decoder only guarantees JSON shape, not the
            # correct assignment of natural-language meaning around formulas.
            # Real local probes produced valid slots with omitted/misassigned
            # content, so that diagnostic strategy is never a production fallback.
            paragraph.unicode = original_unicode
            paragraph.pdf_paragraph_composition = original_compositions
            raise PlaceholderIntegrityError("unresolved-paragraph") from None
    for composition in compositions:
        text = composition.pdf_same_style_unicode_characters
        if text and text.pdf_style is None:
            text.pdf_style = paragraph.pdf_style
    tracker.set_output(candidate)
    paragraph.unicode = candidate
    paragraph.pdf_paragraph_composition = compositions
    _record_validated(paragraph)
    if _FORMULA.findall(ti.unicode) != _FORMULA.findall(candidate):
        with _lock:
            if _safe_percentage_reference_permutation(ti, ti.unicode, candidate):
                _counters["percentageReferenceOrderAdjustments"] += 1
            elif _safe_variable_measurement_permutation(ti, ti.unicode, candidate):
                _counters["variableMeasurementOrderAdjustments"] += 1
            elif _safe_accent_word_permutation(ti, ti.unicode, candidate):
                _counters["accentWordOrderAdjustments"] += 1
            elif _safe_scientific_role_permutation(ti, ti.unicode, candidate):
                _counters["scientificRoleOrderAdjustments"] += 1
            else:
                _counters["atomicVariableOrderAdjustments"] += 1
    if repaired:
        with _lock:
            _counters["repairedParagraphs"] += 1
    return True


def install():
    """Install once inside the process actually executing BabelDOC translation."""
    global _installed
    with _lock:
        if _installed:
            return
        from importlib.metadata import version
        if version("babeldoc") != "0.6.2" or version("pdf2zh-next") != "2.9.0":
            raise RuntimeError("Translation guard requires dependency re-audit")
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        _originals.update(parse=ILTranslator.parse_translate_output, pre=ILTranslator.pre_translate_paragraph)
        ILTranslator.parse_translate_output = _guard_parse
        ILTranslator.pre_translate_paragraph = _guard_pre
        ILTranslator.post_translate_paragraph = _guard_post
        _installed = True


def snapshot():
    with _lock:
        unresolved = [state for state in _states.values() if not state["validated"]]
        reasons = Counter(reason for state in unresolved for reason in state["reasons"])
        return {"protocolVersion": 1, "guardVersion": GUARD_VERSION,
                "guardInstalled": _installed, "preparedParagraphs": len(_states),
                "validatedParagraphs": len(_states) - len(unresolved),
                "unresolvedCount": len(unresolved), "unresolvedReasons": dict(reasons),
                "unresolvedParagraphs": [{"sourceParagraphSHA256": state.get("sourceParagraphSHA256"),
                                          "layoutLabel": state.get("layoutLabel", "other"),
                                          "reasons": sorted(state["reasons"])} for state in unresolved],
                "counters": dict(_counters), "rawDocumentTextStored": False}


def write_report(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = snapshot()
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return report
