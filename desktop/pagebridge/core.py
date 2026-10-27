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
import threading
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


class DocumentStore:
    """Atomic, independently versioned Windows library; callers receive copies."""

    def __init__(self, data_root, recover_interrupted=True):
        self.root = Path(data_root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "desktop-library.json"
        self._lock = threading.RLock()
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
                for field in ("input_path", "translated_path", "bilingual_path", "reading_path"):
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
        with self._lock:
            return copy.deepcopy(self._state["documents"])

    def folders(self):
        with self._lock:
            return copy.deepcopy(self._state["folders"])

    def get(self, document_id):
        with self._lock:
            return copy.deepcopy(self._find(self._state, document_id))

    def page_info(self, document_id, variant="original"):
        """Return displayed page geometry without text extraction or rasterizing.

        Page.rect includes crop and rotation, matching the raster preview. The
        reader can allocate every page's space before rendering visible pages.
        """
        if variant not in {"original", "translated"}:
            raise AppError("不支持的 PDF 变体。")
        doc = self.get(document_id)
        source = doc["input_path" if variant == "original" else "translated_path"]
        if not source:
            raise AppError("尚未生成译文。")
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
        with self._lock:
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
        with self._lock:
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
                copied_digest = sha256(target)
                if copied_digest != digest:
                    raise AppError("导入期间原文件发生变化，请重试。")
                document = dict(id=identifier, name=source.name, page_count=pages, input_path=str(target), input_sha256=digest,
                                status="ready", translated_path=None, bilingual_path=None, folder_id=None, favorite=False,
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
        name = str(name).strip()
        if not name or len(name) > 240 or any(ord(c) < 32 for c in name):
            raise AppError("请输入有效文档名称（1–240 字）。")
        return self._change(document_id, lambda d: d.update(name=name))

    def set_favorite(self, document_id, value):
        return self._change(document_id, lambda d: d.update(favorite=bool(value)))

    def create_folder(self, name):
        name = str(name).strip()
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise AppError("请输入有效文件夹名称（1–100 字）。")
        with self._lock:
            if any(f["name"].casefold() == name.casefold() for f in self._state["folders"]):
                raise AppError("已有同名文件夹。")
            state = copy.deepcopy(self._state)
            folder = {"id": str(uuid.uuid4()), "name": name}
            state["folders"].append(folder)
            self._write(state)
            return copy.deepcopy(folder)

    def rename_folder(self, folder_id, name):
        if not isinstance(name, str):
            raise AppError("请输入有效文件夹名称（1–100 字）。")
        name = name.strip()
        if not name or len(name) > 100 or any(ord(c) < 32 for c in name):
            raise AppError("请输入有效文件夹名称（1–100 字）。")
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            if folder_id is not None and folder_id not in {f["id"] for f in self._state["folders"]}:
                raise AppError("文件夹已不存在。")
            return self._change(document_id, lambda d: d.update(folder_id=folder_id))

    def trash(self, document_id, value=True):
        return self._change(document_id, lambda d: d.update(trashed=bool(value)))

    def update_reading(self, document_id, page):
        return self._change(document_id, lambda d: d.update(current_page=max(1, min(int(page), d["page_count"])), last_opened=time.time()), allow_busy=True)

    def new_attempt(self, document_id):
        with self._lock:
            doc = self.get(document_id)
            if doc["status"] in ("running", "cancelling") or doc.get("trashed"):
                raise AppError("当前文档无法开始新翻译。")
            attempt = str(uuid.uuid4())
            directory = self.root / "documents" / document_id / "attempts" / attempt
            if not _unredirected(directory):
                raise AppError("翻译任务目录被重定向。")
            directory.mkdir(parents=True, exist_ok=False)
            return attempt, directory

    def mark_running(self, document_id, attempt_id):
        uuid.UUID(attempt_id)
        expected = self.root / "documents" / document_id / "attempts" / attempt_id
        if not expected.is_dir() or not _unredirected(expected):
            raise AppError("翻译任务目录不存在。")
        return self._change(document_id, lambda d: d.update(status="running", attempt_id=attempt_id, last_error=None))

    def finish_failure(self, document_id, message, cancelled=False):
        doc = self.get(document_id)
        previous = bool(doc.get("translated_path") and Path(doc["translated_path"]).is_file())
        message = str(message or "翻译未完成。")[:2000]
        if "完整性校验" in message:
            message = "译文未通过完整性校验，本次未提交译文。" + ("上次成功结果仍可使用。" if previous else "原文仍可阅读，尚未生成可用译文。")
        return self._change(document_id, lambda d: d.update(status="cancelled" if cancelled else "failed", last_error="翻译已取消。" if cancelled else message), allow_busy=True)

    def commit_result(self, document_id, attempt_id, event):
        if event.get("artifactType") == "structured-reading":
            from .structured import commit_reading
            return commit_reading(self, document_id, attempt_id, event)
        with self._lock:
            doc = self.get(document_id)
            if (doc.get("attempt_id") != attempt_id or doc["status"] != "running" or event.get("attemptId") != attempt_id
                    or event.get("type") != "result" or not isinstance(event.get("jobId"), str) or not event["jobId"]):
                raise AppError("翻译结果与当前任务不匹配，未提交。")
            uuid.UUID(attempt_id)
            staging = self.root / "documents" / document_id / "attempts" / attempt_id
            if not _unredirected(staging):
                raise AppError("翻译任务目录被重定向。")
            mono = Path(event.get("translatedPath", "")).resolve()
            dual = Path(event.get("bilingualPath", "")).resolve()
            if mono == dual or not all(_inside(p, staging) and p.is_file() for p in (mono, dual)):
                raise AppError("翻译输出路径超出任务目录。")
            if sha256(doc["input_path"]) != doc["input_sha256"]:
                raise AppError("原文副本发生变化，未提交译文。")
            if checked_pdf(mono) != doc["page_count"] or checked_pdf(dual) != doc["page_count"] * 2:
                raise AppError("输出页数与原文不一致，未提交译文。")
            manifest_path = mono.parent / "manifest.json"
            try:
                if not _unredirected(manifest_path) or manifest_path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError()
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                quality = manifest["qualityGuard"]
                if (manifest.get("protocolVersion") != 1 or manifest.get("attemptId") != attempt_id or manifest.get("jobId") != event["jobId"]
                        or manifest.get("sourceSHA256") != doc["input_sha256"] or manifest.get("sourcePages") != doc["page_count"]
                        or manifest.get("translatedSHA256") != sha256(mono) or manifest.get("bilingualSHA256") != sha256(dual)
                        or quality.get("guardInstalled") is not True or quality.get("layoutGuard", {}).get("guardInstalled") is not True
                        or type(quality.get("unresolvedCount")) is not int or quality["unresolvedCount"] != 0
                        or type(quality.get("validatedParagraphs")) is not int or type(quality.get("preparedParagraphs")) is not int
                        or quality["preparedParagraphs"] < 0 or quality["preparedParagraphs"] != quality.get("validatedParagraphs")):
                    raise ValueError()
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                raise AppError("翻译校验清单无效，未提交译文。") from None
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
                shutil.copyfile(mono, temporary / "translated.pdf")
                shutil.copyfile(dual, temporary / "bilingual.pdf")
                if (sha256(temporary / "translated.pdf") != manifest["translatedSHA256"]
                        or sha256(temporary / "bilingual.pdf") != manifest["bilingualSHA256"]
                        or sha256(doc["input_path"]) != doc["input_sha256"]):
                    raise AppError("提交期间文件发生变化，旧译文未被覆盖。")
                atomic_json(temporary / "manifest.json", manifest)
                os.replace(temporary, destination)
                moved = True
                state = copy.deepcopy(self._state)
                updated = self._find(state, document_id)
                updated.update(status="completed", last_error=None, preferred_artifact="pdf", translated_path=str(destination / "translated.pdf"),
                               bilingual_path=str(destination / "bilingual.pdf"), source_language=manifest.get("sourceLanguage", "en"),
                               target_language=manifest.get("targetLanguage", "zh"))
                self._write(state)
                return copy.deepcopy(updated)
            except BaseException:
                if moved:
                    shutil.rmtree(destination, ignore_errors=True)
                raise
            finally:
                shutil.rmtree(temporary, ignore_errors=True)

    def export_pdf(self, document_id, kind, destination):
        fields = {"original": "input_path", "translated": "translated_path", "bilingual": "bilingual_path"}
        if kind not in fields:
            raise AppError("不支持的导出类型。")
        doc = self.get(document_id)
        if not doc.get(fields[kind]):
            raise AppError("尚未生成可导出的译文。")
        source = Path(doc[fields[kind]]).resolve()
        target = Path(destination).expanduser().resolve()
        if _inside(target, self.root) or source == target or target == Path(doc["input_path"]).resolve():
            raise AppError("请选择工作目录之外的保存位置，不能覆盖原文或应用工作文件。")
        checked_pdf(source)
        if not target.parent.is_dir():
            raise AppError("导出目录不存在。")
        fd, temporary = tempfile.mkstemp(prefix=".pagebridge-export-", suffix=".pdf", dir=target.parent)
        os.close(fd)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return target
