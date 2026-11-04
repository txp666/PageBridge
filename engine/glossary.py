"""User terminology: parse the glossary text and pick the entries a passage uses.

One entry per line. ``source = target`` (also ``=>``, ``→`` or a tab, as pasted
from a spreadsheet) fixes a translation; a line with only ``source`` keeps the
term in its original form. Blank lines and lines starting with ``#`` are ignored.
"""
import re

MAX_TEXT = 100_000
MAX_ENTRIES = 5000
MAX_TERM = 200
MAX_PER_PASSAGE = 40
SEPARATOR = re.compile(r"\t|\s*=>\s*|\s*→\s*|\s*=\s*")


def parse(text):
    entries = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = SEPARATOR.split(line, maxsplit=1)
        source = parts[0].strip()
        target = parts[1].strip() if len(parts) > 1 else ""
        if not source or len(source) > MAX_TERM or len(target) > MAX_TERM:
            continue
        # A later line for the same term replaces the earlier one.
        entries.pop(source.casefold(), None)
        entries[source.casefold()] = (source, target or None)
        if len(entries) >= MAX_ENTRIES:
            break
    return [compile_entry(source, target) for source, target in entries.values()]


def compile_entry(source, target):
    # Latin-script terms match whole words with common English inflections, so
    # "motor" does not hit "motorway" while "topology" finds "topologies" and
    # "bioinformatics" finds "bioinformatic". CJK terms have no word boundaries.
    stem, ending = source, ""
    if re.search(r"\w$", source, re.ASCII):
        lower = source.lower()
        if re.search(r"[^aeiou]y$", lower):
            stem, ending = source[:-1], r"(?:y|ies)"
        elif lower.endswith("ics"):
            stem, ending = source[:-1], r"s?"
        else:
            ending = r"(?:e?s)?"
        ending += r"(?![A-Za-z0-9])"
    pattern = re.escape(stem) + ending
    if re.match(r"\w", source, re.ASCII):
        pattern = r"(?<![A-Za-z0-9])" + pattern
    # Acronyms and mixed-case names (PET, CAN, mRNA, EtherCAT) must not match
    # ordinary words; other terms ignore case.
    flags = 0 if re.search(r"[A-Z]", source[1:]) else re.IGNORECASE
    return source, target, re.compile(pattern, flags)


def matching(entries, passage):
    found = [(source, target) for source, target, pattern in entries if pattern.search(passage)]
    # Longer terms first: "servo motor" must win over "motor" when both apply.
    found.sort(key=lambda entry: -len(entry[0]))
    return found[:MAX_PER_PASSAGE]


def instructions(found):
    if not found:
        return ""
    lines = [f'- {source} → {target}' if target and target != source else f'- {source} → {source} (keep as written, do not translate)'
             for source, target in found]
    return ("Mandatory user terminology. It overrides your own word choice: wherever a listed term occurs, "
            "including plural or inflected forms, render it exactly as given after the arrow; "
            "a term marked keep stays in the original language and spelling.\n" + "\n".join(lines) + "\n")


def missed(found, translation):
    text = translation.casefold()
    # A kept term may appear in any inflected form the source used.
    return [(source, target) for source, target in found
            if (target.casefold() not in text if target else not compile_entry(source, None)[2].search(translation))]


def revision(missed_terms):
    lines = [f'- {source} → {target or source}' for source, target in missed_terms]
    return ("Your translation did not follow the mandatory terminology for:\n" + "\n".join(lines)
            + "\nReturn the complete corrected translation of the same passage only, with no explanation.")
