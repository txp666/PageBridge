#!/usr/bin/env python3
"""Credential-free parser subprocess, executed by the optional MinerU runtime."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.metadata
import inspect
import json
import math
from pathlib import Path
import sys


@contextmanager
def capture_inline_geometry():
    """Retain actual 4.0.12 spans before its public schema drops their boxes."""
    from mineru.backend.analysis.pdf.text import content
    original = content._apply_block_content_and_line_metadata
    if tuple(inspect.signature(original).parameters) != ("page_model_list", "block_lines", "page_size"):
        raise RuntimeError("unsupported_inline_geometry_interface")
    captured = []

    def wrapped(page_model_list, block_lines, page_size):
        result = original(page_model_list, block_lines, page_size)
        width, height = page_size
        for lines in block_lines.values():
            for line in lines:
                for span in line.spans:
                    if str(span.type) != "inline_equation" or not str(span.content or "").strip():
                        continue
                    box = list(span.bbox)
                    if (len(box) == 4 and all(math.isfinite(v) for v in box) and
                            0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height):
                        captured.append({"latex": str(span.content).strip(),
                                         "bbox": [v / (width if i % 2 == 0 else height) for i, v in enumerate(box)]})
        return result

    content._apply_block_content_and_line_metadata = wrapped
    try:
        yield captured
    finally:
        content._apply_block_content_and_line_metadata = original


def attach_inline_geometry(data, captured):
    """Bind explicit formulas one-to-one using their containing block and LaTeX."""
    remaining = list(captured)
    def visit(item, parent_bbox=None):
        if isinstance(item, list):
            for child in item:
                visit(child, parent_bbox)
        elif isinstance(item, dict):
            if item.get("type") == "equation_inline" and parent_bbox:
                for index, formula in enumerate(remaining):
                    box = formula["bbox"]
                    cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
                    if (formula["latex"] == item.get("content", "").strip() and
                            parent_bbox[0] <= cx <= parent_bbox[2] and parent_bbox[1] <= cy <= parent_bbox[3]):
                        item["bbox"] = box
                        remaining.pop(index)
                        break
            else:
                visit(item.get("content", []), item.get("bbox", parent_bbox))
    for page in data.get("pages", []):
        visit(page.get("blocks", []))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--input")
    parser.add_argument("--page", type=int)
    parser.add_argument("--output")
    args = parser.parse_args()
    if importlib.metadata.version("mineru") != "4.0.12":
        return 2
    if args.check:
        for line in (Path(__file__).with_name("mineru-requirements.txt")).read_text().splitlines():
            if "==" in line and not line.startswith("#"):
                name, version = line.split("==", 1)
                if importlib.metadata.version(name) != version:
                    return 2
        from mineru.parser import parse  # Verify the actual import surface.
        return 0
    if not args.input or not args.output or not args.page or args.page < 1:
        return 2
    from mineru.parser import parse
    from mineru.parser.writer import FileBasedDataWriter
    with capture_inline_geometry() as formulas:
        result = parse(args.input, tier="basic", ocr_mode="auto", image_analysis=True,
                       page_range=str(args.page))
    # Raw parser artifacts remain temporary and are never consumed as HTML by a UI.
    result.save(FileBasedDataWriter(args.output))
    path = Path(args.output) / "middle_json.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not any(page.get("page_idx") == args.page - 1 for page in data.get("pages", [])):
        return 3
    attach_inline_geometry(data, formulas)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # The parent receives only the exit status, not document text or logs.
        raise SystemExit(1) from None
