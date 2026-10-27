"""Immutable structured reading artifacts; no semantic acceptance rules.

Only identity, source ownership, bounded structure and paths are checked here.
The original PDF supplies table/formula pictures; model text is never HTML.
"""
from __future__ import annotations

import copy
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile

import pymupdf

from .core import AppError, _inside, _result_staging_directory, _unredirected, atomic_json, sha256

ARTIFACT_TYPE = "structured-reading"
MAX_READING_BYTES = 64 * 1024 * 1024
ACTIONS = frozenset({"reading_page", "reading_block_image"})


def read_artifact(path, document):
    path = Path(path)
    try:
        if not _unredirected(path) or path.stat().st_size > MAX_READING_BYTES:
            raise ValueError()
        artifact = json.loads(path.read_text(encoding="utf-8"))
        if (artifact.get("schemaVersion") != 1 or artifact.get("artifactType") != ARTIFACT_TYPE
                or artifact.get("sourceSHA256") != document["input_sha256"]
                or artifact.get("pageCount") != document["page_count"]
                or artifact.get("status") not in {"completed", "partial"}
                or not isinstance(artifact.get("pages"), list)):
            raise ValueError()
        ids, numbers = set(), set()
        for page in artifact["pages"]:
            number = page.get("pageNumber")
            if (type(number) is not int or not 1 <= number <= document["page_count"] or number in numbers
                    or not isinstance(page.get("blocks"), list)):
                raise ValueError()
            numbers.add(number)
            if page.get("decorationsCoordinateSpace") not in (None, "page"):
                raise ValueError()
            for dimension in ("width", "height"):
                value = page.get(dimension)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                    raise ValueError()
            for block in page["blocks"]:
                identifier = block.get("id")
                if (not isinstance(identifier, str) or not identifier or len(identifier) > 256 or identifier in ids
                        or not isinstance(block.get("kind"), str)
                        or not isinstance(block.get("sourceText"), str)
                        or block.get("status") not in {"translated", "source_fallback", "preserved"}
                        or (block.get("translatedText") is not None and not isinstance(block["translatedText"], str))):
                    raise ValueError()
                ids.add(identifier)
                bbox = block.get("bbox")
                if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4
                        or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in bbox)
                        or not 0 <= bbox[0] < bbox[2] <= page["width"] + 1
                        or not 0 <= bbox[1] < bbox[3] <= page["height"] + 1):
                    raise ValueError()
            if "backgroundImage" in page:
                encoded, positioned = page["backgroundImage"], page.get("backgroundBlocks")
                if not isinstance(encoded, str) or not 44 <= len(encoded) <= 16 * 1024 * 1024:
                    raise ValueError()
                header = base64.b64decode(encoded[:44], validate=True)
                if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
                    raise ValueError()
                width, height = int.from_bytes(header[16:20], "big"), int.from_bytes(header[20:24], "big")
                if not width or not height or width * height > 16_000_000:
                    raise ValueError()
                if (not isinstance(positioned, list) or any(not isinstance(x, str) for x in positioned)
                        or len(positioned) != len(set(positioned))
                        or not set(positioned).issubset({b["id"] for b in page["blocks"]})):
                    raise ValueError()
        selected = artifact.get("selectedPages")
        if not numbers or not isinstance(selected, list) or any(type(p) is not int for p in selected) or set(selected) != numbers or len(selected) != len(numbers):
            raise ValueError()
        return artifact
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise AppError("对照阅读文件无效或与原文不匹配。") from None


def committed_artifact(store, document_id):
    doc = store.get(document_id)
    path = doc.get("reading_path")
    expected = store.root / "documents" / document_id / "results"
    if not path or not _inside(path, expected):
        raise AppError("尚未生成对照阅读稿。")
    try:
        if sha256(doc["input_path"]) != doc["input_sha256"]:
            raise AppError("原文发生变化，无法对应阅读稿。")
        if doc.get("reading_sha256") and sha256(path) != doc["reading_sha256"]:
            raise AppError("对照阅读文件发生变化，请重新生成。")
    except OSError:
        raise AppError("原文或阅读稿文件不可用，请重新打开文档。") from None
    return doc, read_artifact(path, doc)


def commit_reading(store, document_id, attempt_id, event):
    with store._lock:
        doc = store.get(document_id)
        if (doc.get("attempt_id") != attempt_id or doc["status"] != "running"
                or event.get("attemptId") != attempt_id or event.get("type") != "result"
                or event.get("artifactType") != ARTIFACT_TYPE or not isinstance(event.get("jobId"), str) or not event["jobId"]):
            raise AppError("对照阅读结果与当前任务不匹配。")
        staging = store.root / "documents" / document_id / "attempts" / attempt_id
        reading = Path(event.get("readingPath", "")).absolute()
        manifest_path = Path(event.get("manifestPath", "")).absolute()
        if (not _unredirected(staging) or not all(_inside(p, staging) and _unredirected(p) and p.is_file() for p in (reading, manifest_path))
                or reading.parent != manifest_path.parent):
            raise AppError("对照阅读输出路径超出任务目录。")
        if sha256(doc["input_path"]) != doc["input_sha256"]:
            raise AppError("原文发生变化，未提交阅读稿。")
        artifact = read_artifact(reading, doc)
        digest = sha256(reading)
        try:
            if manifest_path.stat().st_size > 1024 * 1024:
                raise ValueError()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (manifest.get("protocolVersion") != 1 or manifest.get("artifactType") != ARTIFACT_TYPE
                    or manifest.get("attemptId") != attempt_id or manifest.get("jobId") != event["jobId"]
                    or manifest.get("sourceSHA256") != doc["input_sha256"] or manifest.get("sourcePages") != doc["page_count"]
                    or manifest.get("readingSHA256") != digest or event.get("readingSHA256") != digest):
                raise ValueError()
        except (OSError, ValueError, TypeError, AttributeError):
            raise AppError("对照阅读清单与当前任务不匹配。") from None
        results = store.root / "documents" / document_id / "results"
        if not _unredirected(results):
            raise AppError("阅读稿目录被重定向。")
        results.mkdir(exist_ok=True)
        destination = results / attempt_id
        if destination.exists():
            raise AppError("当前阅读稿结果已存在。")
        temporary = _result_staging_directory(results)
        moved = False
        try:
            shutil.copyfile(reading, temporary / "reading.json")
            if sha256(temporary / "reading.json") != digest or sha256(doc["input_path"]) != doc["input_sha256"]:
                raise AppError("提交期间文件发生变化，旧结果未被覆盖。")
            atomic_json(temporary / "manifest.json", manifest)
            os.replace(temporary, destination)
            moved = True
            blocks = [block for page in artifact["pages"] for block in page["blocks"]]
            state = copy.deepcopy(store._state)
            updated = store._find(state, document_id)
            updated.update(status=artifact["status"], last_error=None, reading_path=str(destination / "reading.json"),
                           reading_sha256=digest, reading_source_language=artifact.get("sourceLanguage", "en"),
                           reading_target_language=artifact.get("targetLanguage", "zh"), preferred_artifact="reading", reading_pages=artifact["selectedPages"],
                           reading_failed_blocks=sum(b["status"] == "source_fallback" for b in blocks),
                           source_language=artifact.get("sourceLanguage", "en"), target_language=artifact.get("targetLanguage", "zh"))
            store._write(state)
            return copy.deepcopy(updated)
        except BaseException:
            if moved:
                shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def page_result(store, request):
    doc, artifact = committed_artifact(store, request["document_id"])
    number = request.get("page", 0)
    if type(number) is not int or not 0 <= number < doc["page_count"]:
        raise AppError("阅读页码超出原文范围。")
    selected = next((p for p in artifact["pages"] if p["pageNumber"] == number + 1), None)
    # The canvas and the original raster use the same rotated CropBox geometry,
    # including pages outside this translation's selection.
    blocks = copy.deepcopy(selected["blocks"]) if selected else []
    with pymupdf.open(doc["input_path"]) as pdf:
        source = pdf[number]
        rect = source.rect
        _add_source_font_sizes(source, blocks)
    decoration_image, decoration_error = None, None
    background_image = _background_image(store, doc, selected) if selected and selected.get("backgroundImage") else None
    if selected and selected.get("decorations") and not background_image:
        try:
            decoration_image = _decoration_image(store, doc, selected)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            decoration_error = "边框预览暂时无法恢复，原 PDF 仍可阅读。"
    return dict(page=number, page_count=doc["page_count"], selected_pages=artifact["selectedPages"],
                width=rect.width, height=rect.height,
                decorations=selected.get("decorations", []) if selected else [],
                decorationsImage=decoration_image, decorations_error=decoration_error,
                backgroundImage=background_image, backgroundBlocks=selected.get("backgroundBlocks", []) if selected else [],
                status=artifact["status"], page_status=selected.get("status", "completed") if selected else "not_selected",
                error=selected.get("error") if selected else None, blocks=blocks,
                source_sha256=doc["input_sha256"], artifact_sha256=doc["reading_sha256"])


def _add_source_font_sizes(page, blocks):
    """Match the Mac reader's character-weighted median source font per region.

    This is display metadata derived from the owned original PDF, never a
    change to the immutable reading artifact. Scans and rotated pages retain
    the reader's existing fallback sizes when native fonts are unavailable.
    """
    for block in blocks:
        block.pop("sourceFontSize", None)
    positioned = [b for b in blocks if b.get("kind") in {"text", "heading", "list"} and b.get("bbox")]
    if not positioned or page.rotation % 360:
        return
    raw = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    glyphs = []
    for native in raw.get("blocks", []):
        for line in native.get("lines", []):
            for span in line.get("spans", []):
                size = span.get("size", 0)
                if not isinstance(size, (int, float)) or not math.isfinite(size) or size <= 0:
                    continue
                for char in span.get("chars", []):
                    if not char.get("c") or "bbox" not in char:
                        continue
                    rect = pymupdf.Rect(char["bbox"])
                    glyphs.append(((rect.tl + rect.br) / 2, size))
    for block in positioned:
        bounds = pymupdf.Rect(block["bbox"]) & page.rect
        sizes = sorted(size for point, size in glyphs if bounds.contains(point))
        if sizes:
            block["sourceFontSize"] = sizes[len(sizes) // 2]


def _background_image(store, document, page):
    cache = store.root / "preview-cache"
    if not _unredirected(cache):
        raise AppError("预览缓存目录被重定向。")
    cache.mkdir(exist_ok=True)
    key = hashlib.sha256((document["reading_sha256"] + str(page["pageNumber"])).encode()).hexdigest()
    target = cache / ("reading-background-" + key + ".png")
    if target.exists():
        if not _unredirected(target):
            raise AppError("预览缓存文件被重定向。")
        return str(target)
    data = base64.b64decode(page["backgroundImage"], validate=True)
    fd, name = tempfile.mkstemp(prefix=".reading-background-", suffix=".png", dir=cache)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(name, target)
    finally:
        Path(name).unlink(missing_ok=True)
    return str(target)


def _decoration_image(store, document, page):
    """Restore source pixels along paths, never repaint alpha or fill regions.

    New paths use displayed page-local points. Legacy paths retain the native
    PDF page origin, which is removed before drawing the same local mask.
    Strokes expand by half a point; fill boundaries use a one-point band.
    """
    cache = store.root / "preview-cache"
    if not _unredirected(cache):
        raise AppError("预览缓存目录被重定向。")
    cache.mkdir(exist_ok=True)
    key = hashlib.sha256(json.dumps([document["input_sha256"], document["reading_sha256"], page["pageNumber"], "decoration-mask-v4-local-paths"]).encode()).hexdigest()
    target = cache / ("reading-decorations-" + key + ".png")
    if target.exists():
        if not _unredirected(target):
            raise AppError("预览缓存文件被重定向。")
        return str(target)
    with pymupdf.open(document["input_path"]) as pdf, pymupdf.open() as mask_pdf:
        source = pdf[page["pageNumber"] - 1]
        origin = source.rect.tl
        path_origin = pymupdf.Point(0, 0) if page.get("decorationsCoordinateSpace") == "page" else origin
        mask = mask_pdf.new_page(width=source.rect.width, height=source.rect.height)
        mask.draw_rect(mask.rect, color=None, fill=(0, 0, 0))
        # finish() isolates each path's drawing state and accumulates commands.
        # Commit that stream once: thousands of page /Contents insertions make
        # dense vector pages disproportionately slow without changing pixels.
        shape = mask.new_shape()
        for decoration in page["decorations"]:
            current = start = None
            for command in decoration["commands"]:
                operation, points = command["op"], command["points"]
                if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in points):
                    raise ValueError("Invalid decoration coordinate")
                if operation == "M" and len(points) == 2:
                    current = start = pymupdf.Point(points) - path_origin
                elif operation == "L" and len(points) == 2 and current is not None:
                    end = pymupdf.Point(points) - path_origin; shape.draw_line(current, end); current = end
                elif operation == "C" and len(points) == 6 and current is not None:
                    end = pymupdf.Point(points[4:6]) - path_origin
                    shape.draw_bezier(current, pymupdf.Point(points[:2]) - path_origin, pymupdf.Point(points[2:4]) - path_origin, end); current = end
                elif operation == "Z" and not points and current is not None and start is not None:
                    shape.draw_line(current, start); current = start
                else:
                    raise ValueError("Invalid decoration command")
            width = decoration.get("width", 0)
            dash = decoration.get("dash", [])
            phase = decoration.get("dashPhase", 0)
            if not math.isfinite(width) or width < 0 or not math.isfinite(phase) or any(not math.isfinite(value) or value < 0 for value in dash):
                raise ValueError("Invalid decoration stroke")
            dashes = "[" + " ".join(str(value) for value in dash) + "] " + str(phase) if dash else None
            band_width = 1 if decoration.get("kind") == "fill_boundary" else width + .5
            shape.finish(color=(1, 1, 1), width=band_width, dashes=dashes, lineCap=decoration.get("lineCap", 0),
                         lineJoin=decoration.get("lineJoin", 0), closePath=False)
        shape.commit()
        # Inline formula pictures move with their translated paragraph. Do not
        # restore a fraction bar (or another native path) at its old location.
        for block in page["blocks"]:
            if (block.get("kind") not in {"text", "heading", "list"} or block.get("status") != "translated"
                    or not (block.get("translatedText") or "").strip()):
                continue
            for formula in block.get("inlineMath", []):
                bbox = formula.get("bbox")
                if not bbox or len(bbox) != 4:
                    continue
                rect = (pymupdf.Rect(bbox) + (-.75, -.75, .75, .75)) & mask.rect
                if not rect.is_empty:
                    mask.draw_rect(rect, color=None, fill=(0, 0, 0))
        scale = min(1800 / source.rect.width, 2400 / source.rect.height)
        matrix = pymupdf.Matrix(scale, scale)
        original = source.get_pixmap(matrix=matrix, colorspace=pymupdf.csRGB, alpha=False)
        # Native paths use the displayed page's coordinates, which can start
        # away from (0, 0). Match the original raster's exact device rectangle,
        # including fractional-origin rounding; resizing a mask would move lines.
        mask_matrix = pymupdf.Matrix(scale, scale).pretranslate(origin.x, origin.y)
        stencil = mask.get_pixmap(matrix=mask_matrix, colorspace=pymupdf.csGRAY, alpha=False)
        restored = pymupdf.Pixmap(original, stencil)
        fd, name = tempfile.mkstemp(prefix=".reading-decorations-", suffix=".png", dir=cache)
        os.close(fd)
        try:
            restored.save(name); os.replace(name, target)
        finally:
            Path(name).unlink(missing_ok=True)
    return str(target)


def block_image(store, request):
    doc, artifact = committed_artifact(store, request["document_id"])
    identifier = request.get("block_id")
    match = next(((p, b) for p in artifact["pages"] for b in p["blocks"] if b["id"] == identifier), None)
    if not match or match[1].get("bbox") is None:
        raise AppError("此块没有可定位的原文区域。")
    page, block = match
    # Engine boxes start at the displayed page's upper-left corner; PyMuPDF
    # clipping uses the displayed page coordinates, including a nonzero origin.
    with pymupdf.open(doc["input_path"]) as pdf:
        source = pdf[page["pageNumber"] - 1]
        origin = source.rect.tl
        rect = (pymupdf.Rect(block["bbox"]) + (origin.x, origin.y, origin.x, origin.y)) & source.rect
        if rect.is_empty:
            raise AppError("原文区域超出页面范围。")
        cache = store.root / "preview-cache"
        cache.mkdir(exist_ok=True)
        key = hashlib.sha256((doc["input_sha256"] + json.dumps([page["pageNumber"], list(rect)])).encode()).hexdigest()
        target = cache / ("reading-" + key + ".png")
        if not target.exists():
            scale = min(2, 1800 / max(rect.width, rect.height))
            pixmap = source.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=rect, alpha=False)
            fd, name = tempfile.mkstemp(prefix=".reading-", suffix=".png", dir=cache)
            os.close(fd)
            try:
                pixmap.save(name)
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        return dict(path=str(target), page=page["pageNumber"] - 1, bbox=block["bbox"], width=rect.width, height=rect.height)


def dispatch(store, request):
    return page_result(store, request) if request["action"] == "reading_page" else block_image(store, request)
