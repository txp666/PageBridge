"""Windows desktop library. No access to the native macOS application's data."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid

import pymupdf


class AppError(ValueError):
    pass


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pagebridge-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_file(target, write):
    """Replace target with a sibling file that write(path) has completed."""
    fd, temporary = tempfile.mkstemp(prefix=".pagebridge-", suffix=Path(target).suffix, dir=Path(target).parent)
    os.close(fd)
    try:
        write(temporary)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def checked_pdf(path):
    try:
        with pymupdf.open(path) as document:
            if not document.is_pdf or document.is_encrypted or document.needs_pass or document.page_count < 1:
                raise AppError("请选择未加密且至少有一页的 PDF。")
            return document.page_count
    except AppError:
        raise
    except Exception:
        raise AppError("无法读取 PDF，文件可能损坏。") from None


def _inside(path, root):
    return Path(path).resolve().is_relative_to(Path(root).resolve())


def _unredirected(path):
    path = Path(path).absolute()
    return not path.is_symlink() and path.resolve() == path


def _result_staging_directory(parent):
    if os.name != "nt":
        return Path(tempfile.mkdtemp(prefix=".commit-", dir=parent))
    # On Windows mode-0700 temporary directories have an explicit OWNER RIGHTS
    # ACL. An elevated owner can be Administrators, denying the same user's
    # limited desktop token access after commit. Inherit the already controlled
    # parent ACL instead; grant no new principals or change existing folders.
    for _ in range(10):
        directory = parent / (".commit-" + uuid.uuid4().hex)
        try:
            directory.mkdir(mode=0o777)
            return directory
        except FileExistsError:
            continue
    raise AppError("无法创建独立的翻译提交目录。")


def _name(value, limit, message):
    name = value.strip() if isinstance(value, str) else ""
    if not name or len(name) > limit or any(ord(c) < 32 for c in name):
        raise AppError(message)
    return name


class DocumentStore:
    """Atomic, independently versioned Windows library; callers receive copies."""

    def __init__(self, data_root, recover_interrupted=False):
        self.root = Path(data_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "desktop-library.json"
        self._state = self._load()
        # A killed worker cannot remain a live job after application restart.
        changed = False
        for doc in self._state["documents"]:
            if recover_interrupted and doc["status"] in ("running", "cancelling"):
                doc.update(status="interrupted", last_error="上次翻译已中断，可以重新开始。")
                changed = True
        if changed:
            atomic_json(self.path, self._state)

    def _load(self):
        if not self.path.exists():
            return {"version": 1, "documents": [], "folders": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or not isinstance(data.get("documents"), list) or not isinstance(data.get("folders"), list):
                raise ValueError()
            ids = set()
            for d in data["documents"]:
                if not isinstance(d, dict) or not isinstance(d.get("id"), str) or d["id"] in ids:
                    raise ValueError()
                uuid.UUID(d["id"])
                ids.add(d["id"])
                if not isinstance(d.get("name"), str) or not isinstance(d.get("page_count"), int) or d["page_count"] < 1:
                    raise ValueError()
                if d.get("status") not in {"ready", "running", "cancelling", "completed", "partial", "failed", "cancelled", "interrupted"}:
                    raise ValueError()
                if (not isinstance(d.get("input_sha256"), str) or len(d["input_sha256"]) != 64
                        or any(c not in "0123456789abcdef" for c in d["input_sha256"])
                        or not isinstance(d.get("input_path"), str) or not d["input_path"]):
                    raise ValueError()
                for field in ("input_path", "reading_path"):
                    if d.get(field) and not _inside(d[field], self.root / "documents"):
                        raise ValueError()
            folder_ids = set()
            for f in data["folders"]:
                uuid.UUID(f["id"])
                if not isinstance(f.get("name"), str) or not f["name"].strip() or f["id"] in folder_ids:
                    raise ValueError()
                folder_ids.add(f["id"])
            return data
        except (ValueError, TypeError, KeyError, OSError):
            raise AppError("文档库损坏或版本不兼容，未覆盖原文件。请备份并恢复 desktop-library.json。") from None

    def _write(self, state):
        atomic_json(self.path, state)
        self._state = state

    def documents(self):
        return copy.deepcopy(self._state["documents"])

    def folders(self):
        return copy.deepcopy(self._state["folders"])

    def get(self, document_id):
        return copy.deepcopy(self._find(self._state, document_id))

    def page_info(self, document_id):
        """Return displayed page geometry without text extraction or rasterizing.

        Page.rect includes crop and rotation, matching the raster preview. The
        reader can allocate every page's space before rendering visible pages.
        """
        source = self.get(document_id)["input_path"]
        try:
            with pymupdf.open(source) as pdf:
                if not pdf.is_pdf or pdf.needs_pass or pdf.is_encrypted or pdf.page_count < 1:
                    raise AppError("无法读取 PDF 页面信息。")
                pages = []
                for number in range(pdf.page_count):
                    rect = pdf[number].rect
                    if not all(math.isfinite(size) and size > 0 for size in (rect.width, rect.height)):
                        raise AppError("PDF 页面尺寸无效。")
                    pages.append({"page": number, "width": rect.width, "height": rect.height, "rotation": pdf[number].rotation})
                return {"page_count": pdf.page_count, "pages": pages}
        except AppError:
            raise
        except Exception:
            raise AppError("无法读取 PDF 页面信息，请检查文件是否存在或是否有访问权限。") from None

    @staticmethod
    def _find(state, document_id):
        for doc in state["documents"]:
            if doc["id"] == document_id:
                return doc
        raise AppError("文档已不存在。")

    def _change(self, document_id, operation, allow_busy=False):
        state = copy.deepcopy(self._state)
        document = self._find(state, document_id)
        if not allow_busy and document["status"] in ("running", "cancelling"):
            raise AppError("请等待翻译完成或先取消翻译。")
        operation(document)
        self._write(state)
        return copy.deepcopy(document)

    def import_pdf(self, path):
        source = Path(path).expanduser().resolve()
        if not source.is_file() or not 0 < source.stat().st_size <= 500 * 1024 * 1024:
            raise AppError("请选择不超过 500 MB 的本地 PDF 文件。")
        if not _unredirected(self.root / "documents"):
            raise AppError("文档存储目录被重定向，已停止导入。")
        digest = sha256(source)
        for doc in self._state["documents"]:
            if not doc.get("trashed") and doc["input_sha256"] == digest and Path(doc["input_path"]).is_file() and sha256(doc["input_path"]) == digest:
                return copy.deepcopy(doc)
        identifier = str(uuid.uuid4())
        folder = self.root / "documents" / identifier
        folder.mkdir(parents=True, exist_ok=False)
        target = folder / "input.pdf"
        try:
            shutil.copyfile(source, target)
            pages = checked_pdf(target)
            if sha256(target) != digest:
                raise AppError("导入期间原文件发生变化，请重试。")
            document = dict(id=identifier, name=source.name, page_count=pages, input_path=str(target), input_sha256=digest,
                            status="ready", folder_id=None, favorite=False,
                            trashed=False, current_page=1, imported_at=time.time(), last_opened=time.time(), last_error=None,
                            source_language="en", target_language="zh", attempt_id=None)
            state = copy.deepcopy(self._state)
            state["documents"].append(document)
            self._write(state)
            return copy.deepcopy(document)
        except BaseException:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    def rename(self, document_id, name):
        name = _name(name, 240, "请输入有效文档名称（1–240 字）。")
        return self._change(document_id, lambda d: d.update(name=name))

    def set_favorite(self, document_id, value):
        return self._change(document_id, lambda d: d.update(favorite=bool(value)))

    def create_folder(self, name):
        name = _name(name, 100, "请输入有效文件夹名称（1–100 字）。")
        if any(f["name"].casefold() == name.casefold() for f in self._state["folders"]):
            raise AppError("已有同名文件夹。")
        state = copy.deepcopy(self._state)
        folder = {"id": str(uuid.uuid4()), "name": name}
        state["folders"].append(folder)
        self._write(state)
        return copy.deepcopy(folder)

    def rename_folder(self, folder_id, name):
        name = _name(name, 100, "请输入有效文件夹名称（1–100 字）。")
        state = copy.deepcopy(self._state)
        folder = next((f for f in state["folders"] if f["id"] == folder_id), None)
        if folder is None:
            raise AppError("文件夹已不存在。")
        if any(f["id"] != folder_id and f["name"].casefold() == name.casefold() for f in state["folders"]):
            raise AppError("已有同名文件夹。")
        folder["name"] = name
        self._write(state)
        return copy.deepcopy(folder)

    def delete_folder(self, folder_id):
        state = copy.deepcopy(self._state)
        if not any(f["id"] == folder_id for f in state["folders"]):
            raise AppError("文件夹已不存在。")
        state["folders"] = [f for f in state["folders"] if f["id"] != folder_id]
        for document in state["documents"]:
            if document.get("folder_id") == folder_id:
                document["folder_id"] = None
        # Folders are metadata only; originals, outputs and annotations stay
        # in their document directories, including trashed or running items.
        self._write(state)
        return {"deleted_id": folder_id}

    def move_to_folder(self, document_id, folder_id):
        if folder_id is not None and folder_id not in {f["id"] for f in self._state["folders"]}:
            raise AppError("文件夹已不存在。")
        return self._change(document_id, lambda d: d.update(folder_id=folder_id))

    def trash(self, document_id, value=True):
        return self._change(document_id, lambda d: d.update(trashed=bool(value)))

    def update_reading(self, document_id, page):
        return self._change(document_id, lambda d: d.update(current_page=max(1, min(int(page), d["page_count"])), last_opened=time.time()), allow_busy=True)

    def new_attempt(self, document_id):
        """Create a private attempt directory and mark the document running."""
        doc = self.get(document_id)
        if doc["status"] in ("running", "cancelling") or doc.get("trashed"):
            raise AppError("当前文档无法开始新翻译。")
        attempt = str(uuid.uuid4())
        directory = self.root / "documents" / document_id / "attempts" / attempt
        if not _unredirected(directory):
            raise AppError("翻译任务目录被重定向。")
        directory.mkdir(parents=True, exist_ok=False)
        self._change(document_id, lambda d: d.update(status="running", attempt_id=attempt, last_error=None))
        return attempt, directory

    def finish_failure(self, document_id, message, cancelled=False):
        message = str(message or "翻译未完成。")[:2000]
        return self._change(document_id, lambda d: d.update(status="cancelled" if cancelled else "failed", last_error="翻译已取消。" if cancelled else message), allow_busy=True)

    def commit_result(self, document_id, attempt_id, event):
        from .structured import commit_reading
        return commit_reading(self, document_id, attempt_id, event)

    def publish_result(self, document_id, attempt_id, manifest, files, fields):
        """Copy verified outputs into results/<attempt>, then record fields(destination).

        files maps each result name to its staged source and expected SHA-256.
        A failed library write removes the published directory again.
        """
        doc = self.get(document_id)
        results = self.root / "documents" / document_id / "results"
        if not _unredirected(results):
            raise AppError("译文结果目录被重定向。")
        results.mkdir(exist_ok=True)
        destination = results / attempt_id
        if destination.exists():
            raise AppError("当前翻译结果已存在。")
        temporary = _result_staging_directory(results)
        moved = False
        try:
            for name, (source, _) in files.items():
                shutil.copyfile(source, temporary / name)
            if (any(sha256(temporary / name) != digest for name, (_, digest) in files.items())
                    or sha256(doc["input_path"]) != doc["input_sha256"]):
                raise AppError("提交期间文件发生变化，旧译文未被覆盖。")
            atomic_json(temporary / "manifest.json", manifest)
            os.replace(temporary, destination)
            moved = True
            state = copy.deepcopy(self._state)
            updated = self._find(state, document_id)
            updated.update(fields(destination))
            self._write(state)
            return copy.deepcopy(updated)
        except BaseException:
            if moved:
                shutil.rmtree(destination, ignore_errors=True)
            raise
        finally:
            shutil.rmtree(temporary, ignore_errors=True)

    def export_pdf(self, document_id, kind, destination):
        if kind != "original":
            raise AppError("不支持的导出类型。")
        source = Path(self.get(document_id)["input_path"]).resolve()
        target = Path(destination).expanduser().resolve()
        if _inside(target, self.root) or source == target:
            raise AppError("请选择工作目录之外的保存位置，不能覆盖原文或应用工作文件。")
        checked_pdf(source)
        if not target.parent.is_dir():
            raise AppError("导出目录不存在。")
        atomic_file(target, lambda temporary: shutil.copyfile(source, temporary))
        return target
