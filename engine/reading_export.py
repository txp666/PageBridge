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
import math
import os
from pathlib import Path
import re
import tempfile

import pymupdf

from reading_background import _rect, glyph_owners, remove_text, source_bold, spans_in, translated_blocks, visible_glyphs
from structured_reading import ReadingError, load_reading

ARTIFACT_TYPE = "reading-export"
FORMULA_SCALE = 4  # Inline formula crops at 288 dpi.
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


def _formula_images(page, block, archive, block_index):
    """Give Story the actual crops so its line boxes include each formula's full height."""
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
        filename = f"formula-{block_index}-{len(images)}.png"
        archive.add(pixmap.tobytes("png"), filename)
        images[formula["token"]] = (filename, rect.width, rect.height)
    return images


def _body(block, images):
    """The translated passage with inline formulas at the translated token positions."""
    def text(value):
        return html.escape(value.replace("\r\n", "\n").replace("\r", "\n")).replace("\n", "<br>")
    formulas = {f["token"]: f for f in block.get("inlineMath", []) if isinstance(f, dict) and isinstance(f.get("token"), str) and f["token"]}
    template = block.get("translatedTemplate")
    if not formulas or not isinstance(template, str):
        return text(block["translatedText"])
    parts = []
    for piece in re.split("(" + "|".join(map(re.escape, formulas)) + ")", template):
        if piece not in formulas:
            parts.append(text(piece))
        elif piece in images:
            filename, width, height = images[piece]
            parts.append(f'<img src="{filename}" style="width:{width:.3f}pt;height:{height:.3f}pt">')
        else:
            parts.append(text("$" + str(formulas[piece].get("latex", "")) + "$"))
    return "".join(parts)


def _embolden(page):
    """Stroke and fill the existing glyphs, including CJK faces without a bold font.
    This stream is generated solely by Story on our own transparent overlay."""
    doc = page.parent
    for xref in page.get_contents():
        stream = doc.xref_stream(xref).replace(b"BT\n", b"BT\n2 Tr 1 j\n")
        stream = re.sub(rb"(?m)^([\d.]+ [\d.]+ [\d.]+) rg$", rb"\1 rg \1 RG", stream)
        # The stroke follows the fitted font size and the source foreground colour.
        stream = re.sub(rb"/\w+ ([\d.]+) Tf", lambda m: m[0] + f" {float(m[1]) * .035:.3f} w".encode(), stream)
        doc.update_stream(xref, stream)


def _css(block, size, background, indent):
    color = _color(block.get("foregroundColor"))
    if color is None:
        # Reader rule: light text on a dark filled region, otherwise black.
        rgb = background or [255, 255, 255]
        color = [255] * 3 if rgb[0] * .299 + rgb[1] * .587 + rgb[2] * .114 < 128 else [0] * 3
    line_height = 1.35 if block.get("inlineMath") else 1.15
    return (f"body {{margin: 0; padding: 0; font-family: sans-serif; line-height: {line_height}; "
            f"font-size: {size:.2f}pt; color: rgb({color[0]}, {color[1]}, {color[2]})}}"
            f" p {{margin: 0; text-indent: {indent:.3f}pt}}")


def estimated_font_size(block):
    """Font size from the region's area and character count, for text without a text layer
    (scans); within ±25% of the real size on the demo pages."""
    text = (block.get("sourceText") or "").strip()
    bbox = block.get("bbox")
    if block.get("kind") not in {"text", "heading", "list"} or len(text) < 3 or not bbox or len(bbox) != 4:
        return None
    width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    if width <= 0 or height <= 0:
        return None
    return min(height / 1.2, math.sqrt(width * height / (len(text) * 0.6)))


def _start_size(block, glyphs):
    """Character-weighted median native font size in the region, as both readers use;
    small print keeps its own size (5 pt floor) and fitting may still shrink it so nothing is cut."""
    sizes = sorted(glyph.size for glyph in glyphs if glyph.size > 0)
    size = sizes[len(sizes) // 2] if sizes else estimated_font_size(block) or (18 if block.get("kind") == "heading" else 12)
    return max(5, size)


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
    owners = glyph_owners(glyphs, translated if native else drawn)
    if native:
        owned = [glyph for block in drawn for glyph in owners[block["id"]]]
        formulas = [f["bbox"] for block in drawn for f in block.get("inlineMath", []) if isinstance(f, dict) and _rect(f.get("bbox"))]
        if formulas:
            expanded = [pymupdf.Rect(box) + (-.75, -.75, .75, .75) for box in formulas]
            # Only erase the centers of neighboring prose glyphs before taking
            # the crops. Full font boxes can also touch a fraction's denominator.
            probes = []
            for glyph in owned:
                if any(box.intersects(glyph.local) for box in expanded) and not any(box.contains(_center(glyph.local)) for box in expanded):
                    x, y = _center(glyph.native)
                    probes.append(pymupdf.Rect(x - .05, y - .05, x + .05, y + .05))
            if probes:
                remove_text(page, probes, [], [])
    else:
        # A scan: cover each region with its sampled colour, as the reader does.
        # An invisible OCR layer under it is removed so search finds only the translation.
        rotation, origin = page.rotation_matrix, page.rect.tl
        hidden = [pymupdf.Rect(bounds) for span in page.get_texttrace() for _, _, _, bounds in span["chars"]
                  if any(region.contains(_center(pymupdf.Rect(bounds) * rotation) - origin) for region in regions)]
        if hidden:
            remove_text(page, hidden, [], [])
    archive = pymupdf.Archive()
    crops = [_formula_images(page, block, archive, index) for index, block in enumerate(drawn)]
    if native:
        remove_text(page, [glyph.native for glyph in owned], spans_in(page, spans, regions), formulas, keep)
    items, fills = {False: [], True: []}, []
    holes = [_rect(block.get("bbox")) for block in page_result["blocks"] if block.get("kind") in SOURCE_REGIONS]
    for block, rect, images in zip(drawn, regions, crops):
        background = None
        if not native:
            background = _color(block.get("backgroundColor")) or [255, 255, 255]
            # Source regions stay visible unless this text is part of that region (a caption inside a figure).
            for part in _subtract(rect, [hole for hole in holes if hole and not hole.contains(_center(rect))]):
                fills.append((part, background))
        chars = owners[block["id"]]
        size = _start_size(block, chars)
        bold = block.get("sourceBold")
        if bold is None:
            bold = source_bold(chars)
        if bold is None:
            bold = block["kind"] == "heading"
        indent = block.get("firstLineIndent", 0)
        body = _body(block, images)
        if indent:
            body = "<p>" + body.replace("<br>", "</p><p>") + "</p>"
        items[bold].append((rect, body, _css(block, size, background, indent)))
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    weights = [bold for bold in items if items[bold]]
    for bold in weights:
        device = writer.begin_page(area)
        for rect, body, css in items[bold]:
            story = pymupdf.Story(body, user_css=css, archive=archive)
            # Fit each complete passage and its actual formula images into its region.
            fit = story.fit_scale(pymupdf.Rect(0, 0, rect.width, rect.height), scale_min=1)
            scale = 1 / fit.parameter
            story.draw(device, pymupdf.Matrix(scale, 0, 0, scale, rect.x0, rect.y0))
        writer.end_page()
    writer.close()
    with pymupdf.open(stream=buffer.getvalue(), filetype="pdf") as overlay:
        if items[True]:
            _embolden(overlay[-1])
        for rect, rgb in fills:
            overlay[0].draw_rect(rect, color=None, fill=[value / 255 for value in rgb], overlay=False)
        overlay.subset_fonts()
        # The overlay is drawn in displayed page space. Map it onto the unrotated page.
        rotation = page.rotation
        page.set_rotation(0)
        for layer in range(len(overlay)):
            page.show_pdf_page(page.rect, overlay, layer, rotate=rotation)
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
