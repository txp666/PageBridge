"""Inspect and clear app-managed Windows storage."""
import asyncio
import os
from pathlib import Path
import shutil
import uuid

from structured_reading import ENGINE, installation_lock, runtime_directory

from .core import AppError, _unredirected, sha256


CLEARABLE = frozenset(("readingHistory", "taskFiles", "searchIndex", "pagePreviews", "minerUDownloads"))
DOWNLOAD_CACHES = frozenset(("uv-cache", "ms-cache", "hf-cache"))


def children(folder):
    if not folder.is_dir() or not _unredirected(folder):
        return []
    return [path for path in folder.iterdir() if not path.is_symlink() and not path.is_junction()]


def managed_directory(path):
    try:
        uuid.UUID(path.name)
        return path.is_dir()
    except ValueError:
        return False


def files(folder, *, onerror=None):
    for path, directories, names in os.walk(folder, onerror=onerror):
        base = Path(path)
        directories[:] = [name for name in directories
                          if not (base / name).is_symlink() and not (base / name).is_junction()]
        yield from (base / name for name in names)


def has_pending_reading(attempt, saved):
    def raise_error(error):
        raise error
    try:
        return any(path.name == "reading.json" and not matches_saved_reading(path, saved)
                   for path in files(attempt, onerror=raise_error))
    except OSError:
        return True


def matches_saved_reading(staged, saved):
    def identical(source, target):
        return (_unredirected(source) and _unredirected(target) and source.is_file()
                and target.is_file() and source.stat().st_size == target.stat().st_size
                and sha256(source) == sha256(target))
    if not identical(staged, saved):
        return False
    return all(identical(image, saved.with_name(image.name)) for image in staged.parent.iterdir()
               if image.name.startswith("background-") and image.suffix == ".png")


def locations(root, documents):
    current = [Path(doc["reading_path"]).resolve() for doc in documents if doc.get("reading_path")]
    managed = root / "documents"
    groups = {}
    def add(category, path):
        groups.setdefault(category, []).append(path)

    seen = set()
    for doc in documents:
        folder = Path(doc["input_path"]).parent
        if folder.parent != managed or folder in seen or not _unredirected(folder):
            continue
        seen.add(folder)
        index = folder / "search-text.json"
        if index.is_file() and not index.is_symlink():
            add("searchIndex", index)
        for result in filter(managed_directory, children(folder / "results")):
            referenced = any(path.is_relative_to(result) for path in current)
            if referenced or (result / "reading.json").is_file():
                add("currentReadings" if referenced else "readingHistory", result)
        for attempt in filter(managed_directory, children(folder / "attempts")):
            saved = folder / "results" / attempt.name / "reading.json"
            add("pendingReadings" if has_pending_reading(attempt, saved) else "taskFiles", attempt)
    runtime = runtime_directory(working_directory=ENGINE)
    # The parser's installation is shared by document libraries.
    runtimes = [runtime] if os.environ.get("PAGEBRIDGE_MINERU_RUNTIME") else children(runtime.parent.resolve())
    for runtime in runtimes:
        for item in children(runtime):
            add("minerUDownloads" if item.name in DOWNLOAD_CACHES else "minerU", item)
    previews = root / "preview-cache"
    if previews.is_dir() and _unredirected(previews):
        add("pagePreviews", previews)
    return groups


def file_bytes(path):
    try:
        return 0 if path.is_symlink() else path.stat().st_size
    except OSError:
        return 0


def usage(store):
    return {category: {"bytes": sum(sum(file_bytes(file) for file in files(item)) if item.is_dir() else file_bytes(item)
                                   for item in items),
                       "count": len(items), "location": str(items[0].parent if category == "minerU" else items[0])}
            for category, items in locations(store.root, store.documents()).items()}


def recycle(path):
    import ctypes
    from ctypes import wintypes

    class FileOperation(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", wintypes.LPVOID), ("lpszProgressTitle", wintypes.LPCWSTR)]

    source = ctypes.create_unicode_buffer(str(path) + "\0")
    operation = FileOperation(wFunc=3, pFrom=ctypes.cast(source, wintypes.LPCWSTR),
                              fFlags=0x0040 | 0x0010 | 0x0004 | 0x0400)  # Allow undo, suppress shell UI.
    delete = ctypes.WinDLL("shell32").SHFileOperationW
    delete.argtypes = [ctypes.POINTER(FileOperation)]
    delete.restype = ctypes.c_int
    if delete(ctypes.byref(operation)) or operation.fAnyOperationsAborted:
        raise OSError("Windows could not recycle this reading draft")


def clear(store, category):
    if not isinstance(category, str) or category not in CLEARABLE:
        raise AppError("请在文档库管理此类文件。")
    documents = store.documents()
    if any(doc["status"] in ("running", "cancelling") for doc in documents):
        raise AppError("请在当前任务结束后清理缓存。")
    targets = locations(store.root, documents).get(category, ())
    if category == "minerUDownloads":
        return asyncio.run(clear_mineru_downloads(targets))
    return remove_items(targets, recycle_item=category == "readingHistory")


def remove_items(items, *, recycle_item=False):
    removed = failed = 0
    for item in items:
        try:
            if recycle_item:
                recycle(item)
            elif item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except OSError:
            failed += 1
    return {"removed": removed, "failed": failed}


async def clear_mineru_downloads(items):
    groups = {}
    for item in items:
        groups.setdefault(item.parent, []).append(item)
    total = {"removed": 0, "failed": 0}
    for runtime, caches in groups.items():
        if not _unredirected(runtime / "install.lock"):
            total["failed"] += len(caches)
            continue
        try:
            async with installation_lock(runtime, wait=False):
                result = remove_items(caches)
                for key in total:
                    total[key] += result[key]
        except OSError:
            total["failed"] += len(caches)
    return total
