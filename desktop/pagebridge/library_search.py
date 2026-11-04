"""Search the original text and reading-draft translations of every document.

Page text is extracted once per PDF and cached beside it, keyed by the PDF's hash,
in the same search-text.json format the Mac app writes.
"""
import json
from pathlib import Path

import pymupdf

MAX_HITS = 500


def _page_texts(document):
    cache = Path(document["input_path"]).parent / "search-text.json"
    try:
        value = json.loads(cache.read_text(encoding="utf-8"))
        if value.get("inputSHA256") == document["input_sha256"] and isinstance(value.get("pages"), list):
            return [str(p) for p in value["pages"]]
    except (OSError, ValueError, AttributeError):
        pass
    try:
        with pymupdf.open(document["input_path"]) as pdf:
            pages = [page.get_text() for page in pdf]
    except (OSError, RuntimeError, ValueError):
        return []
    try:
        cache.write_text(json.dumps(dict(inputSHA256=document["input_sha256"], pages=pages), ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
    return pages


def _translations(document):
    try:
        value = json.loads(Path(document["reading_path"]).read_text(encoding="utf-8")) if document.get("reading_path") else {}
    except (OSError, ValueError):
        return []
    found = []
    for page in value.get("pages", []):
        text = "\n".join(b["translatedText"] for b in page.get("blocks", []) if isinstance(b.get("translatedText"), str))
        if text and isinstance(page.get("pageNumber"), int):
            found.append((page["pageNumber"], text))
    return found


def _hit(text, needle, document, page, translation):
    index = text.lower().find(needle.lower())
    if index < 0:
        return None
    start, end = max(0, index - 40), min(len(text), index + len(needle) + 80)
    prefix = "…" if start else ""
    snippet = prefix + text[start:end].replace("\n", " ") + ("…" if end < len(text) else "")
    offset = len(prefix) + index - start
    return dict(document_id=document["id"], document_name=document["name"], page=page, snippet=snippet,
                match_start=offset, match_length=len(needle), in_translation=translation)


def search(documents, query):
    needle = (query or "").strip() if isinstance(query, str) else ""
    if len(needle) < 2:
        return dict(hits=[], limited=False)
    hits = []
    for document in documents:
        if document.get("trashed"):
            continue
        for number, text in enumerate(_page_texts(document), 1):
            if hit := _hit(text, needle, document, number, False):
                hits.append(hit)
        for number, text in _translations(document):
            if hit := _hit(text, needle, document, number, True):
                hits.append(hit)
        if len(hits) >= MAX_HITS:
            break
    return dict(hits=hits[:MAX_HITS], limited=len(hits) >= MAX_HITS)
