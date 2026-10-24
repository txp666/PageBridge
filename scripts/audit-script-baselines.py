#!/usr/bin/env python3
"""Read-only source-aware screening of Latin scripts and mm² unit powers in PDFs.

Compare page/base/script groups, not paragraph token order. This flags missing,
flattened or reversed scripts; it does not prove mathematical or semantic
equivalence. Requires PyMuPDF. No OCR, model call, or PDF modification.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

import pymupdf


PARAMETERS = {
    "minimumScriptSizeRatio": 0.30,
    "maximumScriptSizeRatio": 0.85,
    "minimumGapEm": -0.15,
    "maximumGapEm": 0.60,
    "maximumBaselineDistanceEm": 0.90,
    "flatDirectionThresholdEm": 0.06,
    "baselineDifferenceToleranceEm": 0.10,
    "sizeRatioDifferenceTolerance": 0.15,
}
LATIN = re.compile(r"^[A-Za-z]$")
SCRIPT = re.compile(r"^[A-Za-z0-9]$")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def selected_pages(value, count):
    if value == "all":
        return list(range(1, count + 1))
    pages = set()
    for part in value.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise ValueError("Pages must be all or a list such as 1,3-5.")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= count:
            raise ValueError(f"Page range outside 1..{count}: {part}")
        pages.update(range(start, end + 1))
    return sorted(pages)


def characters(page):
    result = []
    for block_index, block in enumerate(page.get_text("rawdict")["blocks"]):
        for line_index, line in enumerate(block.get("lines", [])):
            horizontal = tuple(line.get("dir", (1, 0))) == (1, 0)
            for span in line["spans"]:
                for char in span["chars"]:
                    if span["size"] <= 0:
                        continue
                    result.append({"index": len(result), "text": char["c"],
                                   "font": span["font"], "size": span["size"],
                                   "origin": list(char["origin"]), "bbox": list(char["bbox"]),
                                   "block": block_index, "line": line_index,
                                   "horizontal": horizontal})
    return result


def adjoining_letter(chars, current, side, *, comparable_size=True):
    """Reject ordinary word endings and multi-character descriptors/scripts."""
    for other in chars:
        if other["index"] == current["index"] or not SCRIPT.fullmatch(other["text"]):
            continue
        if comparable_size and abs(other["size"] / current["size"] - 1) > 0.08:
            continue
        if abs(other["origin"][1] - current["origin"][1]) > current["size"] * 0.08:
            continue
        gap = (current["bbox"][0] - other["bbox"][2] if side == "left"
               else other["bbox"][0] - current["bbox"][2])
        if -current["size"] * 0.08 <= gap <= current["size"] * 0.08:
            return True
    return False


def direction(delta):
    threshold = PARAMETERS["flatDirectionThresholdEm"]
    return "subscript" if delta > threshold else "superscript" if delta < -threshold else "flat"


def observations(page, *, source):
    chars = characters(page)
    pairs, ambiguous, excluded = [], [], defaultdict(int)
    bases = [c for c in chars if c["horizontal"] and LATIN.fullmatch(c["text"])
             and not adjoining_letter(chars, c, "left")
             and not adjoining_letter(chars, c, "right")]
    # A measured square-millimetre unit has two baseline m glyphs, not an
    # isolated Latin base. Recognize only this complete, proven source shape;
    # ordinary word endings and arbitrary multi-character scripts stay out.
    for last in chars:
        if not last["horizontal"] or last["text"] != "m" or adjoining_letter(chars, last, "right"):
            continue
        preceding = [first for first in chars if first["index"] != last["index"]
                     and first["horizontal"] and first["text"] == "m"
                     and first["font"] == last["font"]
                     and abs(first["size"] / last["size"] - 1) < .05
                     and abs(first["origin"][1] - last["origin"][1]) < last["size"] * .05
                     and -.1 * last["size"] < last["bbox"][0] - first["bbox"][2] < .15 * last["size"]]
        if len(preceding) != 1 or adjoining_letter(chars, preceding[0], "left"):
            continue
        first = preceding[0]
        bases.append({**last, "text": "mm", "kind": "square-millimetre-unit",
                      "bbox": [first["bbox"][0], min(first["bbox"][1], last["bbox"][1]),
                               last["bbox"][2], max(first["bbox"][3], last["bbox"][3])],
                      "componentGlyphs": [first, last]})
    for script in chars:
        if not script["horizontal"] or not SCRIPT.fullmatch(script["text"]):
            continue
        if adjoining_letter(chars, script, "left") or adjoining_letter(chars, script, "right"):
            excluded["word-or-multicharacter-script"] += 1
            continue
        candidates = []
        for base in bases:
            if base.get("kind") == "square-millimetre-unit" and script["text"] != "2":
                continue
            ratio = script["size"] / base["size"]
            delta = (script["origin"][1] - base["origin"][1]) / base["size"]
            gap = (script["bbox"][0] - base["bbox"][2]) / base["size"]
            if (PARAMETERS["minimumScriptSizeRatio"] <= ratio < PARAMETERS["maximumScriptSizeRatio"]
                    and PARAMETERS["minimumGapEm"] <= gap <= PARAMETERS["maximumGapEm"]
                    and abs(delta) <= PARAMETERS["maximumBaselineDistanceEm"]):
                candidates.append((base, ratio, delta, gap))
        if not candidates:
            continue
        if len(candidates) != 1:
            ambiguous.append({"reason": "multiple-nearby-base-candidates", "script": script,
                              "candidateBases": [item[0] for item in candidates]})
            continue
        base, ratio, delta, gap = candidates[0]
        pair = {"base": base, "script": script, "sizeRatio": ratio,
                "baselineDeltaPoints": script["origin"][1] - base["origin"][1],
                "baselineDeltaEm": delta, "horizontalGapEm": gap, "direction": direction(delta),
                "nearbyExtractedText": "".join(c["text"] for c in chars[max(0, base["index"] - 35):script["index"] + 36])}
        if source and direction(delta) == "flat":
            ambiguous.append({"reason": "source-small-glyph-has-no-clear-script-direction", **pair})
        else:
            pairs.append(pair)
    return {"pairs": pairs, "ambiguous": ambiguous, "excluded": dict(excluded)}


def group_key(pair):
    return pair["base"]["text"], pair["script"]["text"]


def ambiguous_keys(observed):
    keys = set()
    for item in observed["ambiguous"]:
        for base in item.get("candidateBases", [item.get("base")]):
            if base:
                keys.add((base["text"], item["script"]["text"]))
    return keys


def compare_page(page_number, before, after, *, identical_file):
    groups = defaultdict(lambda: {"source": [], "translated": []})
    for role, observed in (("source", before), ("translated", after)):
        for pair in observed["pairs"]:
            groups[group_key(pair)][role].append(pair)
    uncertain = ambiguous_keys(before) | ambiguous_keys(after)
    results = []
    for key, values in sorted(groups.items()):
        # Match repeated identical base/script labels by their baseline
        # distribution, not their occurrence/reading order. This accommodates
        # legal clause reordering but cannot establish equation identity.
        source = sorted(values["source"], key=lambda p: (p["baselineDeltaEm"], p["sizeRatio"]))
        target = sorted(values["translated"], key=lambda p: (p["baselineDeltaEm"], p["sizeRatio"]))
        issues, matches = [], []
        if not source:
            status = "review"
            issues.append("new-target-group-or-unscored-source-ambiguity")
        else:
            status = "pass"
        if len(source) != len(target):
            issues.append("count-mismatch")
        for original, translated in zip(source, target):
            differences = []
            delta_difference = abs(original["baselineDeltaEm"] - translated["baselineDeltaEm"])
            size_difference = abs(original["sizeRatio"] - translated["sizeRatio"])
            if original["direction"] != translated["direction"]:
                differences.append("script-direction-changed")
            if delta_difference > PARAMETERS["baselineDifferenceToleranceEm"]:
                differences.append("baseline-magnitude-changed")
            if size_difference > PARAMETERS["sizeRatioDifferenceTolerance"]:
                differences.append("relative-script-size-changed")
            issues.extend(differences)
            matches.append({"source": original, "translated": translated,
                            "baselineDifferenceEm": delta_difference, "sizeRatioDifference": size_difference,
                            "baseFontChanged": original["base"]["font"] != translated["base"]["font"],
                            "issues": differences})
        if issues and source:
            status = "review" if key in uncertain else "fail"
        if identical_file and key in uncertain:
            # Ambiguous source observations are explicitly unscored. They do
            # not constitute a change when both byte-identical PDFs are used.
            status = "pass" if not issues else "identity-unscored"
        results.append({"base": key[0], "script": key[1], "sourceCount": len(source),
                        "translatedCount": len(target), "status": status,
                        "issues": sorted(set(issues)), "matches": matches,
                        "unmatchedSource": source[len(matches):], "unmatchedTranslated": target[len(matches):]})
    return {"page": page_number, "groups": results,
            "sourceAmbiguous": before["ambiguous"], "translatedAmbiguous": after["ambiguous"],
            "sourceExcludedCounts": before["excluded"], "translatedExcludedCounts": after["excluded"]}


def run(args):
    original, translated, output = (p.expanduser().resolve() for p in (args.original, args.translated, args.output))
    if output.suffix != ".json" or output in (original, translated):
        raise ValueError("--output must be a separate .json report path, never a PDF input.")
    hashes = {"original": digest(original), "translated": digest(translated)}
    same = hashes["original"] == hashes["translated"]
    with pymupdf.open(original) as source, pymupdf.open(translated) as target:
        pages = selected_pages(args.pages, len(source))
        page_count_matches = len(source) == len(target)
        results = []
        missing_pages = []
        for number in pages:
            if number > len(target):
                missing_pages.append(number)
                continue
            before = observations(source[number - 1], source=True)
            after = observations(target[number - 1], source=False)
            results.append(compare_page(number, before, after, identical_file=same))
    failures = [{"page": page["page"], **{k: group[k] for k in ("base", "script", "issues")}}
                for page in results for group in page["groups"] if group["status"] == "fail"]
    reviews = [{"page": page["page"], **{k: group[k] for k in ("base", "script", "issues")}}
               for page in results for group in page["groups"] if group["status"] in ("review", "identity-unscored")]
    unchanged = digest(original) == hashes["original"] and digest(translated) == hashes["translated"]
    source_ambiguous = sum(len(page["sourceAmbiguous"]) for page in results)
    target_ambiguous = sum(len(page["translatedAmbiguous"]) for page in results)
    hard_failure = bool(failures or missing_pages or not page_count_matches or not unchanged)
    needs_review = not same and bool(reviews or source_ambiguous or target_ambiguous)
    summary = {"auditPassed": not hard_failure and not needs_review,
               "disposition": "fail" if hard_failure else "needs-review" if needs_review else "identity-match" if same else "screening-pass",
               "requiresManualReview": hard_failure or needs_review,
               "identicalInputBytes": same, "inputHashesUnchanged": unchanged, "pageCountsMatch": page_count_matches,
               "checkedPages": len(results), "sourcePairs": sum(group["sourceCount"] for page in results for group in page["groups"]),
               "translatedPairs": sum(group["translatedCount"] for page in results for group in page["groups"]),
               "failureGroups": failures, "reviewGroups": reviews, "missingTargetPages": missing_pages,
               "sourceAmbiguousCount": source_ambiguous, "translatedAmbiguousCount": target_ambiguous}
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "readOnly": True, "modelCalls": 0,
              "source": {"path": str(original), "sha256": hashes["original"]},
              "translated": {"path": str(translated), "sha256": hashes["translated"]},
              "scriptSHA256": digest(Path(__file__)), "pagesRequested": pages,
              "parameters": PARAMETERS, "summary": summary, "pages": results,
              "limitations": [
                  "Screening only; passing does not prove complete mathematical or semantic equivalence.",
                  "Scope is horizontal isolated ASCII Latin bases with adjacent single ASCII letter/digit smaller glyphs, plus complete two-glyph mm units with a smaller 2 exponent. No OCR, arbitrary multi-character descriptors, legacy-font remapping or equation reconstruction.",
                  "Repeated page/base/script groups are matched by normalized baseline distributions, not paragraph reading order. Equal aggregate distributions cannot prove the correct equation or occurrence owns each script.",
                  "Ambiguous nearby bases and source small glyphs without clear direction are explicitly unscored/reviewed; they are not automatically accepted as mathematical scripts.",
                  "Thresholds are geometric triage tolerances in units of the base font size, not a typographic or physical correctness specification.",
                  "This does not check primes, operators, relation direction, whole equation order, clipping, or complete glyph coverage. Combine with structural, numerical and rendered visual review.",
              ]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Script baseline screening", "", f"Audit passed: **{summary['auditPassed']}**; checked pages: {len(results)}.",
             f"Confident source pairs: {summary['sourcePairs']}; target observations: {summary['translatedPairs']}.", "",
             "| Page | Base + script | Status | Source / target | Issues |", "|---|---|---|---|---|"]
    for page in results:
        for group in page["groups"]:
            lines.append(f"| {page['page']} | {group['base']} / {group['script']} | {group['status']} | "
                         f"{group['sourceCount']} / {group['translatedCount']} | {', '.join(group['issues'])} |")
    lines.extend(["", f"Ambiguous source / target observations: {summary['sourceAmbiguousCount']} / {summary['translatedAmbiguousCount']}.",
                  "Full glyph font/size/origin/box data, normalized offsets and ambiguous candidates are in the JSON report.", "", "## Limits", ""]
                 + ["- " + limit for limit in report["limitations"]])
    output.with_suffix(".md").write_text("\n".join(lines) + "\n")
    print(json.dumps({"report": str(output), **summary}, ensure_ascii=False, indent=2))
    return 1 if hard_failure else 2 if needs_review else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True, type=Path)
    parser.add_argument("--translated", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="Separate .json report; companion .md is also written.")
    parser.add_argument("--pages", default="all", help="all or 1-based comma/range list, e.g. 21,26,33-35")
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
