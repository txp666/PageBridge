"""Translated and bilingual PDFs rebuilt from a committed reading draft.

Each translated page starts from the original page itself, so photos, line art
and untranslated text stay vector. Only the glyphs the reader draws over are
removed, exactly as for the reading background; translations become real,
searchable text fitted into their source boxes. No service or network is used.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import io
import os
from pathlib import Path
import re
import tempfile

import pymupdf

from reading_background import _rect, glyph_owners, remove_text, spans_in, translated_blocks, visible_glyphs
from structured_reading import ReadingError, load_reading

ARTIFACT_TYPE = "reading-export"
FORMULA_SCALE = 4  # Inline formula crops at 288 dpi.
CAP_HEIGHT = .7  # em
PLACEHOLDER = "placeholder.png"
SOURCE_REGIONS = ("table", "formula", "figure", "code")


def _color(value):
    if isinstance(value, list) and len(value) == 3 and all(type(v) in (int, float) and 0 <= v <= 255 for v in value):
        return [round(v) for v in value]
    return None


def _overlays(block):
    # Same blocks the reader canvas draws over (ReadingCanvasPlacement.overlaysTranslation).
    return (block.get("kind") in ("text", "heading", "list") and block.get("status") == "translated"
            and isinstance(block.get("translatedText"), str) and block["translatedText"].strip()
            and _rect(block.get("bbox")) is not None)


def _center(rect):
    return (rect.tl + rect.br) / 2


def _formula_images(page, block):
    """Crop each inline formula at the readers' rectangle, before its glyphs are removed."""
    images, area, origin = {}, pymupdf.Rect(0, 0, page.rect.width, page.rect.height), page.rect.tl
    for formula in block.get("inlineMath", []):
        box = _rect(formula.get("bbox")) if isinstance(formula, dict) else None
        if box is None or not isinstance(formula.get("token"), str) or not formula["token"]:
            continue
        rect = (box + (-.75, -.75, .75, .75)) & area
        if rect.is_empty:
            continue
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(FORMULA_SCALE, FORMULA_SCALE), clip=rect + (*origin, *origin),
                                   colorspace=pymupdf.csRGB, alpha=False)
        images[formula["token"]] = (pixmap.tobytes("png"), rect.width, rect.height)
    return images


def _body(block, images, size, placed):
    """The translation as HTML. MuPDF sits an inline image on the baseline, which lifts a
    formula by its descent, so each marker becomes a transparent placeholder for the part
    above the baseline; its crop is drawn afterwards, centred on the cap height like the
    macOS reader. Formulas without a crop stay readable as LaTeX."""
    def text(value):
        return html.escape(value).replace("\n", "<br>")
    formulas = {f["token"]: f for f in block.get("inlineMath", []) if isinstance(f, dict) and isinstance(f.get("token"), str) and f["token"]}
    template = block.get("translatedTemplate")
    if not formulas or not isinstance(template, str):
        return text(block["translatedText"])
    parts = []
    for piece in re.split("(" + "|".join(map(re.escape, formulas)) + ")", template):
        if piece not in formulas:
            parts.append(text(piece))
        elif piece in images:
            png, width, height = images[piece]
            parts.append(f'<img src="{PLACEHOLDER}" style="width:{width:.2f}pt;height:{(height + CAP_HEIGHT * size) / 2:.2f}pt">')
            placed.append((png, width, height, size))
        else:
            parts.append(text("$" + str(formulas[piece].get("latex", "")) + "$"))
    return "".join(parts)


def _css(block, size, background):
    color = _color(block.get("foregroundColor"))
    if color is None:
        # Reader rule: light text on a dark filled region, otherwise black.
        rgb = background or [255, 255, 255]
        color = [255] * 3 if rgb[0] * .299 + rgb[1] * .587 + rgb[2] * .114 < 128 else [0] * 3
    # Regular weight throughout: the built-in CJK fallback has no bold, and
    # bold Latin inside regular Chinese headings reads as random emphasis.
    return ("body {margin: 0; padding: 0; font-family: sans-serif; line-height: 1.15; "
            f"font-size: {size:.2f}pt; color: rgb({color[0]}, {color[1]}, {color[2]})}}")


def _start_size(block, glyphs):
    """Character-weighted median native font size in the region, as both readers use,
    with the readers' minimum; fitting may still shrink it so nothing is cut."""
    rect = pymupdf.Rect(block["bbox"])
    sizes = sorted(size for _, local, _, size in glyphs if rect.contains(_center(local)) and size > 0)
    size = sizes[len(sizes) // 2] if sizes else 18 if block.get("kind") == "heading" else 12
    return max(6.5 if block.get("sourceType") == "table_cell" else 8, size)


def _subtract(rect, holes):
    parts = [rect]
    for hole in holes:
        remaining = []
        for part in parts:
            cut = part & hole
            if cut.is_empty:
                remaining.append(part)
                continue
            remaining.extend(piece for piece in (pymupdf.Rect(part.x0, part.y0, part.x1, cut.y0),
                                                 pymupdf.Rect(part.x0, cut.y1, part.x1, part.y1),
                                                 pymupdf.Rect(part.x0, cut.y0, cut.x0, cut.y1),
                                                 pymupdf.Rect(cut.x1, cut.y0, part.x1, cut.y1)) if not piece.is_empty)
        parts = remaining
    return parts


def translate_page(document, index, page_result, keep):
    """Replace one page's translated regions in place."""
    page = document[index]
    area = pymupdf.Rect(0, 0, page.rect.width, page.rect.height)
    native = "backgroundBlocks" in page_result
    if native:
        # Text the reader draws over: its glyphs were removed from the background image.
        ids = set(page_result["backgroundBlocks"]) if isinstance(page_result["backgroundBlocks"], list) else set()
        translated = translated_blocks(page_result)
        drawn = [block for block in translated if block["id"] in ids and _overlays(block)]
    else:
        drawn = [block for block in page_result["blocks"] if _overlays(block)]
    drawn = [block for block in drawn if not (pymupdf.Rect(block["bbox"]) & area).is_empty]
    if not drawn:
        return
    links = page.get_links()
    glyphs, spans = visible_glyphs(page)
    regions = [pymupdf.Rect(block["bbox"]) & area for block in drawn]
    if native:
        owners = glyph_owners(glyphs, translated)
        owned = [glyph for block in drawn for glyph in owners[block["id"]]]
        formulas = [f["bbox"] for block in drawn for f in block.get("inlineMath", []) if isinstance(f, dict) and _rect(f.get("bbox"))]
        inside = [any((pymupdf.Rect(box) + (-.75, -.75, .75, .75)).contains(_center(glyph[1])) for box in formulas) for glyph in owned]
        # Prose first, so a formula crop shows the formula, not the ends of neighbouring words.
        remove_text(page, [glyph[0] for glyph, formula in zip(owned, inside) if not formula],
                    spans_in(page, spans, regions), [], keep)
    else:
        # A scan: cover each region with its sampled colour, as the reader does.
        # An invisible OCR layer under it is removed so search finds only the translation.
        rotation, origin = page.rotation_matrix, page.rect.tl
        hidden = [pymupdf.Rect(bounds) for span in page.get_texttrace() for _, _, _, bounds in span["chars"]
                  if any(region.contains(_center(pymupdf.Rect(bounds) * rotation) - origin) for region in regions)]
        if hidden:
            remove_text(page, hidden, [], [])
    crops = [_formula_images(page, block) for block in drawn]
    if native and formulas:
        remove_text(page, [glyph[0] for glyph, formula in zip(owned, inside) if formula], [], formulas)
    archive, items, fills, placed = pymupdf.Archive(), [], [], []
    # One fully transparent pixel (Pixmap.clear_with keeps alpha opaque).
    archive.add(pymupdf.Pixmap(pymupdf.csRGB, 1, 1, b"\xff\xff\xff\x00", True).tobytes("png"), PLACEHOLDER)
    holes = [_rect(block.get("bbox")) for block in page_result["blocks"] if block.get("kind") in SOURCE_REGIONS]
    for block, rect, images in zip(drawn, regions, crops):
        background = None
        if not native:
            background = _color(block.get("backgroundColor")) or [255, 255, 255]
            # Source regions stay visible unless this text is part of that region (a caption inside a figure).
            for part in _subtract(rect, [hole for hole in holes if hole and not hole.contains(_center(rect))]):
                fills.append((part, background))
        size = _start_size(block, glyphs)
        items.append((rect, _body(block, images, size, placed), _css(block, size, background)))
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    device = writer.begin_page(area)
    for rect, body, css in items:
        story = pymupdf.Story(body, user_css=css, archive=archive)
        # Shrink until the whole translation fits; nothing is dropped or truncated.
        fit = story.fit_scale(pymupdf.Rect(0, 0, rect.width, rect.height), scale_min=1)
        scale = 1 / fit.parameter
        story.draw(device, pymupdf.Matrix(scale, 0, 0, scale, rect.x0, rect.y0))
    writer.end_page()
    writer.close()
    # One overlay per page shares its fonts; subset here so original fonts are never touched.
    with pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as overlay:
        # Placeholders are the overlay's only images, drawn in story order.
        boxes = [pymupdf.Rect(info["bbox"]) for info in overlay[0].get_image_info()]
        if len(boxes) != len(placed):
            raise RuntimeError("formula placeholders")
        for box, (png, width, height, size) in zip(boxes, placed):
            scale = box.width / width
            middle = box.y1 - CAP_HEIGHT * size * scale / 2
            overlay[0].insert_image(pymupdf.Rect(box.x0, middle - height * scale / 2, box.x1, middle + height * scale / 2), stream=png)
        for rect, rgb in fills:
            overlay[0].draw_rect(rect, color=None, fill=[value / 255 for value in rgb], overlay=False)
        overlay.subset_fonts()
        # The overlay is drawn in displayed page space. show_pdf_page misplaces
        # content on rotated pages with a crop box, so map it on the unrotated page.
        rotation = page.rotation
        page.set_rotation(0)
        page.show_pdf_page(page.rect, overlay, 0, rotate=rotation)
        page.set_rotation(rotation)
    # Redaction drops links over removed text; their targets still apply to the translation.
    # Reloading also drops MuPDF's stale link list, which later page copies would read.
    page = document.reload_page(page)
    present = {(link["kind"], tuple(round(value, 2) for value in link["from"])) for link in page.get_links()}
    for link in links:
        if (link["kind"], tuple(round(value, 2) for value in link["from"])) not in present:
            page.insert_link(link)


def _target(request):
    output, source = Path(request["outputPath"]), Path(request["inputPath"])
    if output.suffix.lower() != ".pdf":
        raise ReadingError("导出文件名须以 .pdf 结尾。")
    if not output.parent.is_dir():
        raise ReadingError("导出文件夹不存在，请重新选择保存位置。")
    if output == source or (output.exists() and source.exists() and os.path.samefile(output, source)):
        raise ReadingError("不能覆盖原 PDF，请选择其他保存位置。")
    if output.is_dir():
        raise ReadingError("导出位置无效，请重新选择保存位置。")
    return output


async def _checkpoint():
    # sleep(0) resumes this task before a pending SIGTERM or control-line cancel is
    # dispatched, so a short export never saw it; a timer lets the loop run them first.
    await asyncio.sleep(0.01)


async def export(request, events):
    output = _target(request)
    try:
        # One in-memory copy: the hash, every page and both outputs come from the same bytes.
        data = Path(request["inputPath"]).read_bytes()
        work = pymupdf.open(stream=data, filetype="pdf")
        if work.needs_pass or not work.page_count:
            raise ValueError()
    except Exception:  # Includes MuPDF errors, which are not all RuntimeError.
        raise ReadingError("无法读取原 PDF，请检查文件是否损坏或需要密码。") from None
    with work:
        count = work.page_count
        reading = load_reading(request["readingPath"], hashlib.sha256(data).hexdigest(), count)
        pages = {page["pageNumber"]: page for page in reading["pages"]}
        selected = reading["selectedPages"]
        usage = {}
        for index in range(count):
            for xref, *_ in work[index].get_images(full=True):
                usage.setdefault(xref, set()).add(index)
        try:
            for position, number in enumerate(selected):
                events.emit("progress", stage="export", progress=0.9 * position / len(selected),
                            message=f"正在导出第 {position + 1} / {len(selected)} 页")
                translate_page(work, number - 1, pages[number],
                               {xref for xref, used in usage.items() if used != {number - 1}})
                await _checkpoint()
            events.emit("stage", stage="save", progress=0.9, message="正在保存导出 PDF。")
            if request["kind"] == "bilingual":
                result = pymupdf.open(stream=data, filetype="pdf")
                # Insert from the end so earlier positions stay valid; original pages keep their links.
                for number in reversed(selected):
                    result.insert_pdf(work, from_page=number - 1, to_page=number - 1, start_at=number)
            else:
                result = work
        except Exception:
            raise ReadingError("无法生成导出 PDF，请重试或重新生成阅读稿。") from None
        expected = count + (len(selected) if request["kind"] == "bilingual" else 0)
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=".pagebridge-export-", suffix=".pdf", dir=output.parent)
            os.close(descriptor)
            try:
                result.save(temporary, garbage=4, deflate=True)
            finally:
                if result is not work:
                    result.close()
            with pymupdf.open(temporary) as check:
                if check.page_count != expected or check.needs_pass:
                    raise ReadingError("导出的 PDF 未通过页数校验，请重试。")
            await _checkpoint()  # Honour a pending cancellation before replacing the target.
            os.replace(temporary, output)
        except ReadingError:
            raise
        except OSError:
            raise ReadingError("无法写入导出文件，请检查保存位置的权限与磁盘空间。") from None
        except Exception:
            raise ReadingError("无法生成导出 PDF，请重试或重新生成阅读稿。") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.remove(temporary)
    events.emit("result", artifactType=ARTIFACT_TYPE, progress=1.0, outputPath=str(output), pageCount=expected,
                message="双语 PDF 已导出。" if request["kind"] == "bilingual" else "译文 PDF 已导出。")
