#!/usr/bin/env python3
"""Read-only PDF integrity/translation triage, with page renders for human review.

Requires PyMuPDF and Pillow; prefers Poppler's pdftoppm for independent rendering.
This does not grade semantic accuracy, prove formula equivalence, or certify layout.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata

import fitz
from PIL import Image, ImageChops, ImageDraw


CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\U00020000-\U0002fa1f]")
LATIN_WORD = re.compile(r"[A-Za-z]+(?:[-'][A-Za-z]+)*")
NUMBER = re.compile(r"(?<![A-Za-z\d])\d+(?:[.,]\d+)*(?:[eE][+-]?\d+)?(?![A-Za-z\d])")
MATH_SYMBOL = re.compile(r"[=<>≤≥≠≈∑∏∫√±×÷∂∇∞∈∉∀∃∝∧∨∩∪α-ωΑ-Ω]")
MATH_FONT = re.compile(r"(?:math|symbol|cmmi|cmsy|cmex|msam|msbm)", re.I)
# Include known model mutations, without flagging ordinary prose such as “公式”.
MARKERS = {
    "translation_instruction_echo": re.compile(r"仅翻译此(?:段|运行|文本)|不添加任何标签|这是来自科学\s*PDF\s*的(?:一段|一个)|Return the translation only|This is one text run from a scientific PDF|Whole-paragraph recovery rules|整段恢复规则", re.I),
    "recovery_literal_token": re.compile(r"\{PBKEEPX*_\d+\}", re.I),
    "xml_style_tag": re.compile(
        r"[<{]\s*/?\s*(?:span|style|stlye|sytle|sty|font|formula|math|sub|sup|风格|样式|公式|公式占位符)"
        r"(?=[\s\d='\">}/]|id\b)[^<>{}\n]{0,140}[>}]?", re.I
    ),
    "style_attribute_fragment": re.compile(r"\b(?:style|span|font|stlye|风格|样式)\s+i\s*d\s*=\s*['\"]?\d+", re.I),
    "formula_placeholder": re.compile(
        r"(?:\{\{?\s*(?:v|f|formula|math|公式|公式占位符)[ _-]*\d+\s*\}\}?|"
        r"\[\[\s*(?:formula|math|公式)[ _-]*\d+\s*\]\]|"
        r"\b(?:FORMULA|MATH)_\d+\b)", re.I
    ),
    "think_tag": re.compile(r"</?\s*think\s*>", re.I),
    "angle_tag_candidate": re.compile(
        r"<\s*/?\s*[a-zA-Z][a-zA-Z _-]{1,25}(?:\s+[^<>]{0,110}|=['\"][^<>]{0,110})?>"
    ),
}


def normalize(text: str) -> str:
    """PDF font encodings sometimes expose a space as U+0003, etc."""
    text = unicodedata.normalize("NFKC", text)
    return "".join(" " if unicodedata.category(c).startswith("C") else c for c in text)


def compact(text: str) -> str:
    return re.sub(r"\s+", "", normalize(text))


def marker_hits(text: str) -> list[dict]:
    text = html.unescape(normalize(text))
    hits = []
    occupied = []
    for kind, pattern in MARKERS.items():
        for match in pattern.finditer(text):
            if any(start <= match.start() < end for start, end in occupied):
                continue
            occupied.append(match.span())
            hits.append({"kind": kind, "offset": match.start(), "token": match.group()[:150],
                         "context": text[max(0, match.start()-24):match.end()+30]})
    return sorted(hits, key=lambda hit: hit["offset"])


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def parse_pages(value: str, count: int) -> list[int]:
    if value == "all":
        return list(range(1, count+1))
    pages = set()
    for chunk in value.split(","):
        if not chunk.strip():
            continue
        endpoints = chunk.split("-", 1)
        start = int(endpoints[0])
        end = int(endpoints[-1])
        if start < 1 or end > count or end < start:
            raise ValueError(f"Focus page range outside 1..{count}: {chunk}")
        pages.update(range(start, end+1))
    return sorted(pages)


def render_document(document, path: Path, folder: Path, dpi: int, renderer: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    if renderer == "poppler":
        run = subprocess.run(["pdftoppm", "-r", str(dpi), "-png", str(path), str(folder/"page")],
                             capture_output=True, text=True)
        if run.returncode:
            raise RuntimeError(f"Poppler failed for {path.name}: {run.stderr[-2000:]}")
        paths = sorted(folder.glob("page-*.png"), key=lambda p: int(p.stem.split("-")[-1]))
        if len(paths) != len(document):
            raise RuntimeError(f"Expected {len(document)} page renders, got {len(paths)} in {folder}")
        return paths
    paths = []
    for i, page in enumerate(document, 1):
        path = folder/f"page-{i:03}.png"
        page.get_pixmap(dpi=dpi, alpha=False).save(path)
        paths.append(path)
    return paths


def render_focus(document, source: Path, output: Path, page_number: int, dpi: int, renderer: str):
    output.parent.mkdir(parents=True, exist_ok=True)
    if renderer == "poppler":
        run = subprocess.run(["pdftoppm", "-f", str(page_number), "-l", str(page_number),
                              "-singlefile", "-r", str(dpi), "-png", str(source), str(output.with_suffix(""))],
                             capture_output=True, text=True)
        if run.returncode:
            raise RuntimeError(f"Poppler focus render failed: {run.stderr[-2000:]}")
    else:
        document[page_number-1].get_pixmap(dpi=dpi, alpha=False).save(output)


def raster_metrics(path: Path) -> dict:
    with Image.open(path) as image:
        hist = image.convert("L").histogram()
        count = sum(hist)
        return {"renderOK": True, "widthPixels": image.width, "heightPixels": image.height,
                "nonWhitePixelFraction": round(sum(hist[:246])/count, 6), "renderPath": str(path)}


def compare_renders(expected_path: Path, actual_path: Path) -> dict:
    """Compare decoded RGB pixels, ignoring PNG metadata; no tolerance threshold."""
    with Image.open(expected_path) as expected_image, Image.open(actual_path) as actual_image:
        expected, actual = expected_image.convert("RGB"), actual_image.convert("RGB")
        result = {"expectedSizePixels": list(expected.size), "actualSizePixels": list(actual.size),
                  "sizeMatches": expected.size == actual.size, "pixelsMatchExactly": False,
                  "changedPixelCount": None, "changedPixelFraction": None,
                  "meanAbsoluteError": None, "maxAbsoluteError": None}
        if expected.size != actual.size:
            return result
        channels = ImageChops.difference(expected, actual).split()
        maximum = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        histogram = maximum.histogram()
        count = expected.width * expected.height
        changed = sum(histogram[1:])
        absolute_error = sum(value * occurrences for channel in channels
                             for value, occurrences in enumerate(channel.histogram()))
        result.update(pixelsMatchExactly=changed == 0, changedPixelCount=changed,
                      changedPixelFraction=changed / count,
                      meanAbsoluteError=absolute_error / (count * 3),
                      maxAbsoluteError=max(value for value, occurrences in enumerate(histogram) if occurrences))
        return result


def page_metrics(page, render_path: Path) -> tuple[dict, str]:
    text = page.get_text(sort=True)
    norm = normalize(text)
    images = page.get_image_info(hashes=True)
    out_of_bounds, math_fonts = [], []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if not span["text"].strip():
                    continue
                rect = fitz.Rect(span["bbox"])
                # Glyph bounding boxes may extend slightly beyond the crop box.
                if not (page.rect + (-2, -2, 2, 2)).contains(rect):
                    out_of_bounds.append({"bbox": [round(v, 2) for v in rect], "text": span["text"][:60]})
                if MATH_FONT.search(span["font"]):
                    math_fonts.append(span["text"])
    math = Counter(MATH_SYMBOL.findall(norm))
    result = {
        "page": page.number+1, "widthPoints": round(page.rect.width, 3),
        "heightPoints": round(page.rect.height, 3), "rotation": page.rotation,
        "characters": len(compact(text)), "cjkCharacters": len(CJK.findall(norm)),
        "latinWords": len(LATIN_WORD.findall(norm)),
        # Translation between alphabetic languages and Chinese must not use a raw length ratio.
        "languageAwareUnits": len(CJK.findall(norm))+len(LATIN_WORD.findall(norm)),
        "replacementCharacters": text.count("\ufffd"), "markerHits": marker_hits(text),
        "braceTextCandidates": re.findall(r"\{[^{}]{0,60}[\u4e00-\u9fff][^{}]{0,60}\}", norm),
        "numbers": dict(Counter(NUMBER.findall(norm))), "mathSymbols": dict(math),
        "parenthesisBalance": norm.count("(")-norm.count(")"),
        "mathFontText": math_fonts, "imageOccurrences": len(images),
        "imageDigests": sorted(set(image["digest"].hex() for image in images)),
        "vectorDrawingCount": len(page.get_drawings()), "outOfPageSpans": out_of_bounds,
        **raster_metrics(render_path),
    }
    return result, text


def counter_retention(source: dict, target: dict) -> dict:
    s, t = Counter(source), Counter(target)
    common = sum((s & t).values())
    return {"sourceCount": sum(s.values()), "translatedCount": sum(t.values()),
            "retainedCount": common, "retention": round(common/sum(s.values()), 4) if s else None,
            "missing": dict(s-t), "added": dict(t-s)}


def compare_pages(source: dict, target: dict, source_text: str, target_text: str) -> dict:
    numbers = counter_retention(source["numbers"], target["numbers"])
    symbols = counter_retention(source["mathSymbols"], target["mathSymbols"])
    units_ratio = target["languageAwareUnits"]/max(1, source["languageAwareUnits"])
    return {
        "page": source["page"],
        "geometryMatches": all(abs(source[k]-target[k]) < 0.02 for k in ["widthPoints", "heightPoints"])
                           and source["rotation"] == target["rotation"],
        "normalizedTextIdentical": compact(source_text) == compact(target_text),
        "sourceMarkers": len(source["markerHits"]), "translatedMarkers": len(target["markerHits"]),
        "newBraceTextCandidates": max(0, len(target["braceTextCandidates"])-len(source["braceTextCandidates"])),
        "possibleNewBlankPage": source["nonWhitePixelFraction"] > 0.003 and target["nonWhitePixelFraction"] < 0.0003,
        "possibleUntranslatedText": source["latinWords"] >= 60 and target["cjkCharacters"] == 0,
        "languageAwareUnitRatio": round(units_ratio, 4),
        "possibleSevereTextLoss": source["languageAwareUnits"] >= 60 and units_ratio < 0.2,
        "numberComparison": numbers, "mathSymbolComparison": symbols,
        # One missing citation year or changed parameter is material even when
        # the page retains most other numbers. Differences are review prompts,
        # not automatic failures: PDF encodings and formatting can also differ.
        "numericReviewSuggested": bool(numbers["missing"] or numbers["added"]),
        "parenthesisBalance": {"source": source["parenthesisBalance"], "translated": target["parenthesisBalance"]},
        "formulaReviewSuggested": bool(symbols["missing"] or symbols["added"])
            or abs(target["parenthesisBalance"]) > abs(source["parenthesisBalance"]),
        "sourceImageDigestsMissing": sorted(set(source["imageDigests"])-set(target["imageDigests"])),
        "newOutOfPageSpans": max(0, len(target["outOfPageSpans"])-len(source["outOfPageSpans"])),
    }


def contact_sheets(source_paths: list[Path], translated_paths: list[Path], folder: Path) -> list[str]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for start in range(0, min(len(source_paths), len(translated_paths)), 12):
        pair_count = min(12, len(source_paths)-start, len(translated_paths)-start)
        canvas = Image.new("RGB", (1040, ((pair_count+2)//3)*386), "#ededed")
        draw = ImageDraw.Draw(canvas)
        for offset in range(min(12, len(source_paths)-start, len(translated_paths)-start)):
            row, col = divmod(offset, 3)
            x, y = col*346, row*386
            draw.text((x+8, y+7), f"Page {start+offset+1}  source | translated", fill="black")
            for side, path in enumerate((source_paths[start+offset], translated_paths[start+offset])):
                with Image.open(path) as img:
                    img = img.convert("RGB")
                    img.thumbnail((166, 354))
                    canvas.paste(img, (x+4+side*172, y+27))
        path = folder/f"pages-{start+1:03}-{min(start+12, len(source_paths)):03}.jpg"
        canvas.save(path, quality=90)
        paths.append(str(path))
    return paths


def run(args):
    sources = {"original": Path(args.original).expanduser().resolve(),
               "translated": Path(args.translated).expanduser().resolve()}
    if args.dual:
        sources["dual"] = Path(args.dual).expanduser().resolve()
    output = Path(args.output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    renderer = args.renderer
    if renderer == "auto":
        renderer = "poppler" if shutil.which("pdftoppm") else "pymupdf"
    if renderer == "poppler" and not shutil.which("pdftoppm"):
        raise RuntimeError("pdftoppm unavailable; choose --renderer pymupdf explicitly or install Poppler")
    documents, hashes, files, texts, renders = {}, {}, {}, {}, {}
    for role, path in sources.items():
        hashes[role] = digest(path)
        documents[role] = doc = fitz.open(path)
        if doc.is_encrypted:
            raise ValueError(f"Encrypted PDF unsupported: {path}")
        renders[role] = render_document(doc, path, output/"low"/role, args.low_dpi, renderer)
        measured = [page_metrics(page, png) for page, png in zip(doc, renders[role])]
        files[role] = {"path": str(path), "sha256": hashes[role], "bytes": path.stat().st_size,
                       "pageCount": len(doc), "repairedOnOpen": doc.is_repaired,
                       "pages": [item[0] for item in measured]}
        texts[role] = [item[1] for item in measured]
    focus = parse_pages(args.focus_pages, len(documents["original"]))
    for role in ("original", "translated"):
        for page in focus:
            if page <= len(documents[role]):
                render_focus(documents[role], sources[role], output/"focus"/f"{role}-{page:03}.png", page, args.high_dpi, renderer)
                (output/"focus"/f"{role}-{page:03}.txt").write_text(texts[role][page-1], encoding="utf-8")
    comparisons = [compare_pages(s, t, st, tt) for s, t, st, tt in zip(
        files["original"]["pages"], files["translated"]["pages"], texts["original"], texts["translated"])]
    dual_check = None
    if "dual" in documents:
        n = len(documents["original"])
        dual_check = {"expectedPageCount": n*2, "pageCountMatches": len(documents["dual"]) == n*2,
                      "assumedOrder": "source then translated, alternating", "sourceMismatchPages": [],
                      "translatedMismatchPages": [], "geometryMismatchPages": [],
                      "sourceRasterReviewPages": [], "translatedRasterReviewPages": [],
                      "rasterComparisons": [], "rasterDPI": args.low_dpi,
                      "rasterDifferencePolicy": "Every decoded-pixel difference is a review prompt; no tolerance threshold is applied."}
        if dual_check["pageCountMatches"]:
            for i in range(min(n, len(documents["translated"]))):
                for role, j in (("original", i*2), ("translated", i*2+1)):
                    if compact(texts[role][i]) != compact(texts["dual"][j]):
                        dual_check["sourceMismatchPages" if role == "original" else "translatedMismatchPages"].append(i+1)
                    expected, actual = files[role]["pages"][i], files["dual"]["pages"][j]
                    if (any(abs(expected[k]-actual[k]) >= 0.02 for k in ("widthPoints", "heightPoints"))
                            or expected["rotation"] != actual["rotation"]):
                        dual_check["geometryMismatchPages"].append(j+1)
                    raster = compare_renders(renders[role][i], renders["dual"][j])
                    dual_check["rasterComparisons"].append({"role": role, "page": i+1,
                                                          "dualPage": j+1, **raster})
                    if not raster["pixelsMatchExactly"]:
                        dual_check["sourceRasterReviewPages" if role == "original"
                                   else "translatedRasterReviewPages"].append(i+1)
    summary = {"pageCountsMatch": len(documents["original"]) == len(documents["translated"]),
               "allPagesRendered": all(p["renderOK"] for f in files.values() for p in f["pages"]),
               "allInputHashesUnchanged": all(digest(sources[role]) == sha for role, sha in hashes.items()),
               "translatedMarkerCount": sum(len(p["markerHits"]) for p in files["translated"]["pages"])}
    fields = {"geometryMismatchPages": lambda p: not p["geometryMatches"],
              "markerPages": lambda p: p["translatedMarkers"] > 0,
              "braceTextReviewPages": lambda p: p["newBraceTextCandidates"] > 0,
              "possibleNewBlankPages": lambda p: p["possibleNewBlankPage"],
              "possibleUntranslatedTextPages": lambda p: p["possibleUntranslatedText"],
              "possibleSevereTextLossPages": lambda p: p["possibleSevereTextLoss"],
              "numericReviewPages": lambda p: p["numericReviewSuggested"],
              "formulaReviewPages": lambda p: p["formulaReviewSuggested"],
              "missingImageReviewPages": lambda p: bool(p["sourceImageDigestsMissing"]),
              "newOutOfPageSpanReviewPages": lambda p: p["newOutOfPageSpans"] > 0}
    summary.update({name: [p["page"] for p in comparisons if check(p)] for name, check in fields.items()})
    if dual_check:
        summary.update(dualSourceRasterReviewPages=dual_check["sourceRasterReviewPages"],
                       dualTranslatedRasterReviewPages=dual_check["translatedRasterReviewPages"])
    report = {"checkedAt": datetime.now(timezone.utc).isoformat(), "label": args.label,
              "readOnlySourcePDFs": True, "renderer": renderer, "lowDPI": args.low_dpi,
              "highDPI": args.high_dpi, "focusPages": focus, "summary": summary,
              "files": files, "pageComparisons": comparisons, "dualCheck": dual_check,
              "contactSheets": contact_sheets(renders["original"], renders["translated"], output/"contacts"),
              "limitations": [
                  "Heuristics flag pages for review; they are not proof of semantic correctness, formula equivalence or acceptable typography.",
                  "CJK characters and Latin words are counted as language-aware units. Raw character ratios are not used for text-loss flags.",
                  "Number/symbol extraction can differ for font encodings, line wrapping, translated dates and thousands separators; inspect the source and translated render.",
                  "Number/symbol Counters do not verify signs, mathematical primes, Latin variable letters, sub/superscript binding or which value belongs to which variable. Inspect every focused equation even when numeric/formula review lists are empty.",
                  "Exit code 0 means no configured structural failure was detected, not that all review prompts were resolved or that the translation was accepted.",
                  "Image digest matches establish preservation of embedded image pixels, not vector figures, captions, clipping or placement.",
                  "Bilingual raster comparisons ignore PNG metadata and flag every decoded-pixel difference without a tolerance threshold. Differences require review; they can reflect source-page reconstruction or antialiasing as well as wrong content/order. A low-DPI pixel match cannot prove fine glyph or formula equivalence.",
                  "Out-of-page span detection does not detect all overlaps, missing glyphs, reading-order changes or font-size changes.",
                  "Tag detector includes known style/formula marker forms and mutations; arbitrary model-generated malformed tags may require visual review.",
                  "Absence of Chinese may be expected on references, figure-only pages or untranslated names and identifiers.",
              ]}
    (output/"report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    lines = [f"# PDF quality triage: {args.label}", "", f"Renderer: {renderer}; all pages {args.low_dpi} dpi, focus pages {args.high_dpi} dpi.", "",
             "These automated checks identify review candidates; they do not certify translation semantics or layout.", "", "| Check | Result |", "|---|---|"]
    for key, value in summary.items():
        lines.append(f"| {key} | {json.dumps(value, ensure_ascii=False)} |")
    lines.extend(["", "## Visual review", "", "Review the paired contact sheets and full-resolution focus images before accepting the result.", ""])
    for path in report["contactSheets"]:
        lines.append(f"- [{Path(path).name}]({Path(path).relative_to(output)})")
    lines.extend(["", "## Limits", ""]+[f"- {line}" for line in report["limitations"]])
    (output/"report.md").write_text("\n".join(lines)+"\n", encoding="utf-8")
    for doc in documents.values():
        doc.close()
    print(json.dumps({"report": str(output/"report.json"), **summary}, ensure_ascii=False, indent=2))
    hard_failure = not summary["pageCountsMatch"] or not summary["allInputHashesUnchanged"] or bool(summary["geometryMismatchPages"])
    if dual_check:
        hard_failure = hard_failure or not dual_check["pageCountMatches"] or any(
            dual_check[key] for key in ("sourceMismatchPages", "translatedMismatchPages", "geometryMismatchPages"))
    if args.fail_on_markers and summary["translatedMarkerCount"]:
        return 2
    return 1 if hard_failure else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--original", required=True)
    parser.add_argument("--translated", required=True)
    parser.add_argument("--dual")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--label", default="PDF translation")
    parser.add_argument("--focus-pages", default="1", help="1-based comma/range list, e.g. 1,4,10-12 or all")
    parser.add_argument("--low-dpi", type=int, default=36)
    parser.add_argument("--high-dpi", type=int, default=144)
    parser.add_argument("--renderer", choices=("auto", "poppler", "pymupdf"), default="auto")
    parser.add_argument("--fail-on-markers", action="store_true")
    return run(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())
