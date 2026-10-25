"""PDF navigation, selectable text, and revision-bound reading annotations.

Wire geometry is measured in PDF points from the rotated crop page's top-left,
matching the PNG preview. PDF annotations are converted back to unrotated page
space at export. Source files are never edited by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid
from urllib.parse import urlsplit

import pymupdf

from .core import AppError, atomic_json, sha256

READING_ACTIONS = frozenset({"document_navigation", "page_text", "annotations", "save_annotation",
                            "delete_annotation", "export_annotations", "export_notes", "search_document"})
KINDS = {"highlight", "underline", "note", "bookmark"}
MAX_ANNOTATION_BYTES = 700_000
MAX_TEXT_BYTES = 850_000
MAX_TOKENS = 10_000
MAX_REGIONS = 500


def _integer(value, message, low=0, high=None):
    if type(value) is not int or value < low or (high is not None and value > high):
        raise AppError(message)
    return value


def _string(value, limit, message):
    if not isinstance(value, str) or len(value) > limit or "\x00" in value:
        raise AppError(message)
    return value


def _uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise AppError("批注标识无效。") from None
    return value


def _rect_json(rect):
    return {"x": round(rect.x0, 4), "y": round(rect.y0, 4),
            "width": round(rect.width, 4), "height": round(rect.height, 4)}


def _display_rect(value, page):
    if not isinstance(value, dict):
        raise AppError("批注选区无效。")
    numbers = [value.get(key) for key in ("x", "y", "width", "height")]
    if any(type(n) not in (int, float) or not math.isfinite(n) for n in numbers):
        raise AppError("批注选区坐标无效。")
    x, y, width, height = numbers
    bounds = page.rect
    if (width < 0.001 or height < 0.001 or x < -0.5 or y < -0.5
            or x + width > bounds.width + 0.5 or y + height > bounds.height + 0.5):
        raise AppError("批注选区超出页面范围。")
    rect = pymupdf.Rect(x, y, x + width, y + height) & bounds
    if rect.is_empty:
        raise AppError("批注选区无效。")
    return rect


@contextmanager
def _source(store, request, require_revision=False):
    variant = request.get("variant", "original")
    if not isinstance(variant, str) or variant not in {"original", "translated"}:
        raise AppError("不支持的 PDF 变体。")
    record = store.get(request.get("document_id"))
    path = record["input_path" if variant == "original" else "translated_path"]
    if not path:
        raise AppError("尚未生成译文。")
    source = Path(path).resolve()
    if not source.is_relative_to(store.root / "documents"):
        raise AppError("PDF 存储路径无效。")
    try:
        digest = sha256(source)
        if require_revision and request.get("source_sha256") != digest:
            raise AppError("PDF 内容已变化，请重新打开文档。旧批注仍保留在原版本中。")
        pdf = pymupdf.open(source)
    except AppError:
        raise
    except Exception:
        raise AppError("无法读取 PDF，请检查文件是否存在及是否有访问权限。") from None
    try:
        if not pdf.is_pdf or pdf.is_encrypted or pdf.needs_pass or pdf.page_count < 1:
            raise AppError("无法读取加密或无页面的 PDF。")
        yield record, source, pdf, {"document_id": record["id"], "variant": variant, "source_sha256": digest}
    finally:
        pdf.close()


def _annotation_file(store, binding):
    _uuid(binding["document_id"])
    path = store.root / "annotations" / binding["document_id"] / binding["variant"] / (binding["source_sha256"] + ".json")
    # Reject redirected parents even if the link happens to remain in this library.
    for component in (path, *path.parents):
        if component == store.root:
            break
        if component.is_symlink() or component.resolve() != component.absolute():
            raise AppError("批注目录被重定向，已停止保存。")
    if not path.resolve().is_relative_to(store.root):
        raise AppError("批注存储路径无效。")
    return path


def _normalise_annotation(value, pdf, previous=None, from_storage=False):
    if not isinstance(value, dict) or not isinstance(value.get("kind"), str) or value["kind"] not in KINDS:
        raise AppError("批注类型无效。")
    kind = value["kind"]
    page_number = _integer(value.get("page"), "批注页码超出文档范围。", high=pdf.page_count - 1)
    regions = value.get("regions", [])
    if not isinstance(regions, list) or len(regions) > MAX_REGIONS:
        raise AppError("批注选区过多，请分段标注。")
    clean = []
    for region in regions:
        if not isinstance(region, dict):
            raise AppError("批注选区无效。")
        number = _integer(region.get("page"), "批注页码超出文档范围。", high=pdf.page_count - 1)
        clean.append({"page": number, "rect": _rect_json(_display_rect(region.get("rect"), pdf[number]))})
    if kind in {"highlight", "underline"} and not clean:
        raise AppError("请先选择 PDF 中的文字，再添加高亮或下划线。")
    if clean and clean[0]["page"] != page_number:
        raise AppError("批注起始页与选区不一致。")
    if kind == "bookmark" and clean:
        raise AppError("书签不应包含文字选区。")
    note = _string(value.get("note", ""), 8_000, "笔记内容不能超过 8000 字。")
    selected = _string(value.get("selected_text", ""), 12_000, "所选文字过长，请分段标注。")
    identifier = _uuid(value["id"]) if "id" in value else str(uuid.uuid4())
    now = time.time()
    if from_storage:
        created, modified = value.get("created_at"), value.get("modified_at")
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0 for v in (created, modified)):
            raise AppError("批注时间无效。")
    else:
        created = previous["created_at"] if previous else now
        modified = now
    return {"id": identifier, "kind": kind, "page": page_number, "regions": clean,
            "selected_text": selected, "note": note, "created_at": created, "modified_at": modified}


def _load(store, binding, pdf):
    path = _annotation_file(store, binding)
    if not path.exists():
        return []
    try:
        if path.stat().st_size > MAX_ANNOTATION_BYTES:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("binding") != binding or not isinstance(data.get("annotations"), list):
            raise ValueError()
        items = data["annotations"]
        if len(items) > 2000:
            raise ValueError()
        items = [_normalise_annotation(item, pdf, from_storage=True) for item in items]
        if len({item["id"] for item in items}) != len(items):
            raise ValueError()
        return items
    except (OSError, ValueError, TypeError, KeyError):
        raise AppError("批注文件损坏或与当前 PDF 不匹配，未覆盖原文件。") from None


def _envelope(binding, pdf, items):
    return {"source_sha256": binding["source_sha256"], "variant": binding["variant"],
            "page_count": pdf.page_count, "annotations": copy.deepcopy(items),
            "bookmarks": sorted({item["page"] for item in items if item["kind"] == "bookmark"})}


def _save(store, binding, source, items):
    data = {"version": 1, "binding": binding, "annotations": items}
    # Bound the snapshot before committing, so a successful save always fits in
    # the bridge response and the user can still reopen/export all saved notes.
    if len(items) > 2000 or len(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")) > MAX_ANNOTATION_BYTES:
        raise AppError("本文档的批注较多，请先导出笔记或精简内容后重试。")
    if sha256(source) != binding["source_sha256"]:
        raise AppError("PDF 内容已变化，未保存到其他版本。请重新打开文档。")
    atomic_json(_annotation_file(store, binding), data)


def _page_text(pdf, binding, request):
    number = _integer(request.get("page", 0), "页码超出文档范围。", high=pdf.page_count - 1)
    page = pdf[number]
    tokens = []
    budget = 0
    truncated = False
    line_number = 0
    raw = page.get_text("rawdict", sort=True, flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    for block in raw.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for char in span.get("chars", []):
                    text = char.get("c", "")
                    if not text or "bbox" not in char:
                        continue
                    rect = pymupdf.Rect(char["bbox"]) * page.rotation_matrix
                    rect &= page.rect
                    if rect.is_empty or not all(math.isfinite(v) for v in rect):
                        continue
                    token = {"text": text, "rect": _rect_json(rect), "line": line_number}
                    size = len(json.dumps(token, ensure_ascii=False).encode("utf-8")) + 2
                    if len(tokens) >= MAX_TOKENS or budget + size > MAX_TEXT_BYTES:
                        truncated = True
                        break
                    tokens.append(token)
                    budget += size
                if truncated:
                    break
            line_number += 1
            if truncated:
                break
        if truncated:
            break
    return {"source_sha256": binding["source_sha256"], "page": number,
            "width": page.rect.width, "height": page.rect.height,
            "tokens": tokens, "links": _page_links(page, pdf.page_count), "truncated": truncated}


def _page_links(page, page_count):
    links = []
    budget = 0
    for link in page.get_links()[:500]:
        # PyMuPDF already returns link rectangles in the rotated display space.
        rect = pymupdf.Rect(link.get("from", (0, 0, 0, 0))) & page.rect
        if rect.is_empty or not all(math.isfinite(v) for v in rect):
            continue
        item = {"rect": _rect_json(rect)}
        if link.get("kind") == pymupdf.LINK_GOTO and 0 <= link.get("page", -1) < page_count:
            item.update(kind="page", page=link["page"])
        elif link.get("kind") == pymupdf.LINK_URI:
            uri = link.get("uri", "")
            try:
                parts = urlsplit(uri)
                if (len(uri) > 4096 or any(ord(c) < 32 for c in uri)
                        or parts.scheme.lower() not in {"https", "http", "mailto"}
                        or (parts.scheme.lower() in {"https", "http"} and not parts.hostname)
                        or parts.username or parts.password):
                    continue
            except ValueError:
                continue
            item.update(kind="url", url=uri)
        else:
            continue  # Never launch PDF embedded executables, files, or scripts.
        budget += len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
        if budget > 40_000:
            break
        links.append(item)
    return links


def _search_document(pdf, binding, request):
    query = _string(request.get("query"), 200, "请输入不超过 200 字的搜索词。").strip()
    if not query:
        raise AppError("请输入搜索词。")
    start = _integer(request.get("start_page", 0), "搜索页码无效。", high=pdf.page_count - 1)
    # Batches yield the local RPC between scans so rendering/notes remain usable.
    end = min(pdf.page_count, start + 24)
    hits, budget, truncated = [], 0, False
    for number in range(start, end):
        page = pdf[number]
        matches = page.search_for(query)
        if not matches:
            continue
        rects = [_rect_json(r * page.rotation_matrix & page.rect) for r in matches[:200]]
        text = " ".join(page.get_text("text").split())
        index = text.casefold().find(query.casefold())
        excerpt = text[max(0, index - 60):max(0, index - 60) + 220]
        item = {"page": number, "text": excerpt, "rects": rects, "count": len(matches)}
        size = len(json.dumps(item, ensure_ascii=False).encode("utf-8")) + 2
        if budget + size > 650_000:
            end = number
            break
        budget += size
        hits.append(item)
        truncated |= len(matches) > 200
    return {"source_sha256": binding["source_sha256"], "hits": hits,
            "next_page": end if end < pdf.page_count else None,
            "page_count": pdf.page_count, "truncated": truncated}


def _navigation(pdf, binding):
    entries = []
    budget = 0
    truncated = False
    # Flattened depth is the same representation used by the Mac navigation UI.
    for row in pdf.get_toc(simple=True):
        depth, title, destination = row[:3]
        if type(destination) is not int or not 1 <= destination <= pdf.page_count:
            continue
        entry = {"id": len(entries), "title": str(title or "未命名章节")[:240],
                 "page": destination - 1, "depth": min(13, max(0, int(depth) - 1))}
        size = len(json.dumps(entry, ensure_ascii=False).encode("utf-8")) + 2
        if len(entries) >= 2000 or budget + size > MAX_TEXT_BYTES:
            truncated = True
            break
        entries.append(entry)
        budget += size
    return {"source_sha256": binding["source_sha256"], "page_count": pdf.page_count,
            "entries": entries, "truncated": truncated}


def _destination(store, source, value, suffix):
    if not isinstance(value, str) or not value or not Path(value).is_absolute():
        raise AppError("请选择本地的绝对保存路径。")
    target = Path(value).expanduser().resolve()
    if target.is_relative_to(store.root) or target == source or target.suffix.lower() != suffix:
        raise AppError("请另存到应用工作目录之外，不能覆盖原文或译文，并使用正确文件扩展名。")
    if not target.parent.is_dir() or target.is_dir():
        raise AppError("导出目录不存在或保存位置无效。")
    # An external hard link to an input/translated PDF is protected as well.
    if target.exists():
        for record in store.documents():
            for key in ("input_path", "translated_path", "bilingual_path"):
                if record.get(key) and Path(record[key]).exists() and os.path.samefile(target, record[key]):
                    raise AppError("不能覆盖原文或译文，请选择新的保存文件。")
    return target


def _markdown(record, binding, items):
    title = record["name"].replace("\r", " ").replace("\n", " ")
    lines = ["# " + title, "", "来源：" + ("原文" if binding["variant"] == "original" else "译文"),
             "", "PDF SHA-256：`" + binding["source_sha256"] + "`", ""]
    names = {"highlight": "高亮", "underline": "下划线", "note": "笔记", "bookmark": "书签"}
    for item in sorted(items, key=lambda item: (item["page"], item["created_at"])):
        lines += [f"## 第 {item['page'] + 1} 页 · {names[item['kind']]}", ""]
        if item["selected_text"]:
            lines += ["> " + line for line in item["selected_text"].splitlines()] + [""]
        if item["note"]:
            lines += [item["note"], ""]
    return "\n".join(lines)


def _annotate_pdf(pdf, items):
    for item in items:
        if item["kind"] == "bookmark":
            continue
        content = item["note"] or item["selected_text"]
        if item["kind"] == "note":
            page = pdf[item["page"]]
            if item["regions"]:
                rect = _display_rect(item["regions"][0]["rect"], page)
                point = rect.tl
            else:
                point = pymupdf.Point(min(18, page.rect.width / 3), min(18, page.rect.height / 3))
            annotation = page.add_text_annot(point * page.derotation_matrix, content, icon="Note")
            annotation.set_colors(stroke=(1, 0.8, 0))
            annotation.set_info(title="PageBridge", subject="笔记", content=content)
            annotation.update()
        else:
            for region in item["regions"]:
                page = pdf[region["page"]]
                rect = _display_rect(region["rect"], page) * page.derotation_matrix
                # Text geometry from rotated display is restored before annotation
                # creation. Imported annotations remain part of the PDF untouched.
                annotation = page.add_highlight_annot(rect) if item["kind"] == "highlight" else page.add_underline_annot(rect)
                annotation.set_colors(stroke=(1, 0.85, 0) if item["kind"] == "highlight" else (0.95, 0.2, 0.2))
                annotation.set_info(title="PageBridge", subject=item["kind"], content=content)
                annotation.update()


def _export(store, record, source, pdf, binding, items, request):
    as_pdf = request["action"] == "export_annotations"
    target = _destination(store, source, request.get("destination"), ".pdf" if as_pdf else ".md")
    fd, temporary = tempfile.mkstemp(prefix=".pagebridge-annotations-", suffix=target.suffix, dir=target.parent)
    os.close(fd)
    try:
        if as_pdf:
            _annotate_pdf(pdf, items)
            pdf.save(temporary, garbage=3, deflate=True)
        else:
            Path(temporary).write_text(_markdown(record, binding, items), encoding="utf-8")
        if sha256(source) != binding["source_sha256"]:
            raise AppError("PDF 内容已变化，未导出其他版本的批注。")
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return {"path": str(target), "count": len(items), "source_sha256": binding["source_sha256"]}


def dispatch_reading(store, request):
    """Called under bridge.library_lock, including atomic annotation mutations."""
    action = request.get("action")
    if not isinstance(action, str) or action not in READING_ACTIONS:
        raise AppError("不支持的阅读操作。")
    mutable = action in {"save_annotation", "delete_annotation", "export_annotations", "export_notes"}
    with _source(store, request, require_revision=mutable) as (record, source, pdf, binding):
        if action == "document_navigation":
            return _navigation(pdf, binding)
        if action == "page_text":
            return _page_text(pdf, binding, request)
        if action == "search_document":
            return _search_document(pdf, binding, request)
        items = _load(store, binding, pdf)
        if action == "save_annotation":
            candidate = request.get("annotation")
            if not isinstance(candidate, dict):
                raise AppError("批注内容无效。")
            identifier = candidate.get("id")
            previous = next((item for item in items if item["id"] == identifier), None)
            if identifier is not None and previous is None:
                raise AppError("批注已不存在，请刷新后重试。")
            value = _normalise_annotation(candidate, pdf, previous=previous)
            if value["kind"] == "bookmark" and any(item["kind"] == "bookmark" and item["page"] == value["page"] and item["id"] != value["id"] for item in items):
                raise AppError("该页已有书签。")
            items = [value if item["id"] == identifier else item for item in items] if previous else [*items, value]
            _save(store, binding, source, items)
        elif action == "delete_annotation":
            identifier = _uuid(request.get("annotation_id"))
            if not any(item["id"] == identifier for item in items):
                raise AppError("批注已不存在，请刷新后重试。")
            items = [item for item in items if item["id"] != identifier]
            _save(store, binding, source, items)
        elif action in {"export_annotations", "export_notes"}:
            return _export(store, record, source, pdf, binding, items, request)
        return _envelope(binding, pdf, items)
