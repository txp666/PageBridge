#!/usr/bin/env python3
"""Credential-free parser subprocess, executed by the optional MinerU runtime."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import importlib.metadata
import inspect
import json
import math
import os
from pathlib import Path
import sys


@contextmanager
def capture_inline_geometry(text_lines=None):
    """Retain the parser's formula spans and paragraph line boxes before serialization."""
    from mineru.backend.analysis.pdf.text import content
    original = content._apply_block_content_and_line_metadata
    if tuple(inspect.signature(original).parameters) != ("page_model_list", "block_lines", "page_size"):
        raise RuntimeError("unsupported_inline_geometry_interface")
    captured = []

    def wrapped(page_model_list, block_lines, page_size):
        result = original(page_model_list, block_lines, page_size)
        width, height = page_size
        if text_lines is not None:
            for item in page_model_list:
                if item.get("type") == "text":
                    text_lines.extend(line["bbox"] for line in item.get("lines", []) if line.get("bbox"))
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


def parse_page(input_path, number, output):
    from mineru.parser import parse
    from mineru.parser.writer import FileBasedDataWriter
    text_lines = []
    with capture_inline_geometry(text_lines) as formulas:
        result = parse(input_path, tier="basic", ocr_mode="auto", image_analysis=True, page_range=str(number))
    # Raw parser artifacts remain temporary and are never consumed as HTML by a UI.
    result.save(FileBasedDataWriter(str(output)))
    path = output / "middle_json.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    page = next((page for page in data.get("pages", []) if page.get("page_idx") == number - 1), None)
    if page is None:
        return 3
    attach_inline_geometry(data, formulas)
    page["sourceTextLines"] = text_lines
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return 0


def serve_pages(input_path, output):
    # Only single-line status replies go back to the parent. MinerU and its
    # rendering children may write directly to the original stdout descriptor.
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb", buffering=0) as replies:
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        for line in sys.stdin:
            number = int(line)
            if number < 1:
                return 2
            code = parse_page(input_path, number, output / f"page-{number}")
            if code:
                return code
            replies.write(b"0\n")
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--input")
    parser.add_argument("--output", type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--page", type=int)
    mode.add_argument("--serve", action="store_true")
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
    if not args.input or not args.output:
        return 2
    if args.serve:
        return serve_pages(args.input, args.output)
    if not args.page or args.page < 1:
        return 2
    return parse_page(args.input, args.page, args.output)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        # The parent receives only the exit status, not document text or logs.
        raise SystemExit(1) from None
