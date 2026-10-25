#!/usr/bin/env python3
"""Private UTF-8 RPC bridge, with optional persistent local-document sessions.

Normal replies: {ok: true, result: ...} / {ok: false, error: ...}.
Translation replies: protocol-v1 NDJSON; terminal result includes document.
Serve starts with action=serve, protocolVersion=1 and a UUID requestId. Each
following local request receives exactly one response with the same requestId.
Credentials are never returned. All request/response bytes are UTF-8.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import uuid

PROTOCOL_FD = None
if __name__ == "__main__":
    # Native PDF diagnostics cannot contaminate JSON or expose document text.
    PROTOCOL_FD = os.dup(sys.stdout.fileno())
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    os.close(null)

import pymupdf

from pagebridge.core import AppError, DocumentStore, sha256
from pagebridge.reading import READING_ACTIONS, dispatch_reading
from pagebridge import assistant
from pagebridge import literature
from pagebridge import updates
from pagebridge.services import ServiceStore, list_models, new_profile, probe_service, worker_service
from pagebridge.worker import WorkerTask


WRITE_LOCK = threading.Lock()
MAX_RESPONSE_BYTES = 1048576
LOCAL_ACTIONS = frozenset({"list", "recover", "import", "get", "rename", "folders",
                           "create_folder", "move", "trash", "favorite", "reading",
                           "export", "render", "page_info", "assistant_history",
                           "assistant_clear", "assistant_preview", "rename_folder", "delete_folder"}) | READING_ACTIONS | literature.LOCAL_ACTIONS | updates.ACTIONS


def make_protocol_reader(descriptor):
    if sys.platform != "win32":
        return lambda count: os.read(descriptor, count)
    # A pending synchronous stdin read can deadlock native-library startup.
    # Peek first, then read only available bytes without retaining a CRT lock.
    import ctypes
    from ctypes import wintypes
    import msvcrt
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                             ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    api.ReadFile.restype = wintypes.BOOL
    api.GetFileType.argtypes = [wintypes.HANDLE]
    api.GetFileType.restype = wintypes.DWORD
    api.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                 ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    api.PeekNamedPipe.restype = wintypes.BOOL
    handle = msvcrt.get_osfhandle(descriptor)
    is_pipe = api.GetFileType(handle) == 3

    def read(count):
        if is_pipe:
            available = wintypes.DWORD()
            while True:
                if not api.PeekNamedPipe(handle, None, 0, None, ctypes.byref(available), None):
                    error = ctypes.get_last_error()
                    if error in (109, 232):
                        return b""
                    raise ctypes.WinError(error)
                if available.value:
                    count = min(count, available.value)
                    break
                time.sleep(0.02)
        buffer = ctypes.create_string_buffer(count)
        received = wintypes.DWORD()
        if not api.ReadFile(handle, buffer, count, ctypes.byref(received), None):
            error = ctypes.get_last_error()
            if error in (109, 232):
                return b""
            raise ctypes.WinError(error)
        return buffer.raw[:received.value]

    return read


READ_PROTOCOL = make_protocol_reader(sys.stdin.fileno()) if __name__ == "__main__" else None


def emit(value):
    data = (json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
    if len(data) > MAX_RESPONSE_BYTES:
        raise AppError("后台响应过大，请缩小文档库后重试。")
    with WRITE_LOCK:
        if PROTOCOL_FD is None:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
        else:
            while data:
                data = data[os.write(PROTOCOL_FD, data):]


def read_line(limit=65536, require_newline=False):
    # Do not prefetch: the translation cancellation reader owns later lines.
    data = bytearray()
    while len(data) <= limit:
        char = (READ_PROTOCOL or make_protocol_reader(sys.stdin.fileno()))(1)
        if not char and data and require_newline:
            raise AppError("请求消息未完整结束。")
        if not char or char == b"\n":
            return bytes(data)
        data.extend(char)
    raise AppError("请求内容过大。")


@contextmanager
def library_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / ".desktop-library.lock").open("a+b") as stream:
        if os.name == "nt":
            import msvcrt
            if stream.seek(0, 2) == 0:
                stream.write(b"0")
                stream.flush()
            stream.seek(0)
            # Windows LK_LOCK retries for ten seconds without a busy spin.
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def data_root(request):
    value = request.get("dataRoot")
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise AppError("必须指定独立的本地应用数据目录。")
    root = Path(value).resolve()
    # Never read/write the existing native Mac user's library during dev tests.
    native_mac = Path.home() / "Library/Application Support/PageBridge"
    if os.name != "nt" and (root == native_mac or root.is_relative_to(native_mac)):
        raise AppError("Windows 测试必须使用独立数据目录，不能打开 Mac 存量文档库。")
    return root


def render(store, request):
    doc = store.get(request["document_id"])
    variant = request.get("variant", "original")
    if variant not in {"original", "translated"}:
        raise AppError("不支持的 PDF 变体。")
    source = doc["input_path" if variant == "original" else "translated_path"]
    if not source:
        raise AppError("尚未生成译文。")
    page_number = request.get("page", 0)
    width = request.get("width", 1100)
    if type(page_number) is not int or type(width) is not int or not 140 <= width <= 1800:
        raise AppError("预览页码或尺寸无效。")
    source_hash = sha256(source)
    cache = store.root / "preview-cache"
    cache.mkdir(exist_ok=True)
    target = cache / f"{source_hash}-{page_number}-{width}.png"
    with pymupdf.open(source) as pdf:
        if pdf.needs_pass or not 0 <= page_number < pdf.page_count:
            raise AppError("预览页码超出文档范围。")
        page = pdf[page_number]
        if page.rect.width <= 0 or page.rect.height <= 0:
            raise AppError("PDF 页面尺寸无效。")
        scale = min(width / page.rect.width, 2400 / page.rect.height)
        if not target.exists():
            pixmap = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            fd, name = tempfile.mkstemp(prefix=".preview-", suffix=".png", dir=cache)
            os.close(fd)
            try:
                pixmap.save(name)
                os.replace(name, target)
            finally:
                Path(name).unlink(missing_ok=True)
        result = {"path": str(target), "page_count": pdf.page_count, "page": page_number,
                  "width": round(page.rect.width * scale), "height": round(page.rect.height * scale)}
    # Keep a bounded cache; files in use by a Windows ImageSource may be locked.
    files = sorted(cache.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
    total = 0
    for file in files:
        total += file.stat().st_size
        if total > 128 * 1024 * 1024 and file != target:
            try:
                file.unlink()
            except OSError:
                pass
    return result


def network_request(request, root):
    # Capture the exact saved destination and credential while locked, then
    # release the document lock before any provider network request.
    with library_lock(root):
        services = ServiceStore(root)
        if request.get("profile"):
            profile = request["profile"]
            key = request.get("apiKey", "")
            if "apiKey" not in request:
                try:
                    previous = services.get(profile["id"])
                except AppError:
                    previous = None
                if previous and all(previous.get(k) == profile.get(k) for k in ("provider", "kind", "baseURL")):
                    key = services.key(profile["id"])
        else:
            profile = services.get(request["service_id"])
            key = services.key(profile["id"])
    return list_models(profile, key) if request["action"] == "list_models" else probe_service(profile, key)


def assistant_request(request, root):
    # The prepared value contains credentials and is never a wire response.
    # Reopen storage after the network call so a clear or another question can
    # invalidate this answer without blocking PDF reading or annotation edits.
    with library_lock(root):
        prepared = assistant.prepare(DocumentStore(root, recover_interrupted=False), ServiceStore(root), request)
    answer = assistant.request_answer(prepared)
    with library_lock(root):
        return assistant.finish(DocumentStore(root, recover_interrupted=False), prepared, answer)


def literature_request(request, root):
    with library_lock(root):
        preferences = literature.LiteratureSettings(root)
        # PDF opening does not need an OpenAlex credential and should still
        # work if that optional credential is temporarily unavailable.
        settings = {name: preferences.value[name] for name in ('source', 'scihub_base_url')}
        key = preferences.key() if request['action'] == 'literature_search' else None
    if request['action'] == 'literature_search':
        return literature.search(request, api_key=key)
    # Resolution/download/validation use their own isolated temporary file and
    # never block the shared reader or hold the library lock across network I/O.
    with literature.open_document(root, request, settings) as opened:
        if isinstance(opened, dict):
            return opened
        with library_lock(root):
            document = DocumentStore(root, recover_interrupted=False).import_pdf(opened.path)
        return {'document': document, 'source': opened.source}


def dispatch(request, root):
    action = request["action"]
    if action in updates.ACTIONS:
        try:
            if action == "update_prepare_install":
                with library_lock(root):
                    if any(doc["status"] in {"running", "cancelling"} for doc in DocumentStore(root, recover_interrupted=False).documents()):
                        raise AppError("请先完成或取消翻译，再安装更新。")
            return updates.dispatch(root, action)
        except updates.UpdateError as error:
            raise AppError(str(error)) from error
    if action == "translate":
        translate(request, root)
        return
    if action in {"list_models", "probe"}:
        return network_request(request, root)
    if action == "assistant_ask":
        return assistant_request(request, root)
    if action in literature.NETWORK_ACTIONS:
        return literature_request(request, root)
    # Per-RPC locking protects independent WinUI child processes from clobbering
    # each other's atomic snapshots. Long translation holds no storage lock.
    with library_lock(root):
        store = DocumentStore(root, recover_interrupted=False)
        identifier = request.get("document_id")
        if action == "list": result = store.documents()
        elif action == "recover":
            updates.recover(root)
            result = DocumentStore(root, recover_interrupted=True).documents()
        elif action == "import": result = store.import_pdf(request["path"])
        elif action == "get": result = store.get(identifier)
        elif action == "rename": result = store.rename(identifier, request["name"])
        elif action == "folders": result = store.folders()
        elif action == "create_folder": result = store.create_folder(request["name"])
        elif action == "rename_folder": result = store.rename_folder(request["folder_id"], request["name"])
        elif action == "delete_folder": result = store.delete_folder(request["folder_id"])
        elif action == "move": result = store.move_to_folder(identifier, request.get("folder_id"))
        elif action == "trash": result = store.trash(identifier, request.get("value", True))
        elif action == "favorite": result = store.set_favorite(identifier, request.get("value", True))
        elif action == "reading": result = store.update_reading(identifier, request["page"])
        elif action == "export": result = str(store.export_pdf(identifier, request["kind"], request["destination"]))
        elif action == "new_attempt":
            attempt, directory = store.new_attempt(identifier)
            result = {"attempt_id": attempt, "directory": str(directory)}
        elif action == "mark_running": result = store.mark_running(identifier, request["attempt_id"])
        elif action == "finish_failure": result = store.finish_failure(identifier, request.get("message"), request.get("cancelled", False))
        elif action == "commit_result": result = store.commit_result(identifier, request["attempt_id"], request["event"])
        elif action == "render": result = render(store, request)
        elif action == "page_info": result = store.page_info(identifier, request.get("variant", "original"))
        elif action in READING_ACTIONS: result = dispatch_reading(store, request)
        elif action == "assistant_history": result = assistant.AssistantStore(store).history(identifier)
        elif action == "assistant_clear": result = assistant.AssistantStore(store).clear(identifier)
        elif action == "assistant_preview": result = assistant.build_context(store, request)
        elif action == "literature_settings": result = literature.LiteratureSettings(root).snapshot()
        elif action == "literature_save_settings": result = literature.LiteratureSettings(root).save(request)
        elif action == "literature_cleanup": result = literature.cleanup(root, request.get("operation_id"))
        elif action == "profiles":
            services = ServiceStore(root)
            result = {"profiles": services.profiles(), "active_id": services.active_id,
                      "credential_storage": services.vault.storage_description}
        elif action == "save_profile": result = ServiceStore(root).save(request["profile"], request.get("apiKey"))
        elif action == "delete_profile":
            ServiceStore(root).delete(request["service_id"])
            result = None
        elif action == "set_active":
            ServiceStore(root).set_active(request["service_id"])
            result = None
        elif action == "new_profile": result = new_profile(request.get("provider", "ollama"))
        else:
            raise AppError("不支持的操作。")
    return result


def translate(request, root):
    identifier = request.get("document_id")
    attempt = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    sequence = 0
    task = None

    def send(event):
        nonlocal sequence
        # Terminal result is withheld until our independent commit has passed.
        if event["type"] in {"result", "error"}:
            return
        sequence = event["sequence"]
        emit(event)

    def terminal(kind, **values):
        emit(dict(protocolVersion=1, jobId=job_id, attemptId=attempt, sequence=sequence + 1, type=kind, **values))

    try:
        with library_lock(root):
            store = DocumentStore(root, recover_interrupted=False)
            doc = store.get(identifier)
            services = ServiceStore(root)
            service_id = request.get("service_id") or services.active_id
            profile = services.get(service_id)
            service = worker_service(profile, services.key(service_id))
            attempt, directory = store.new_attempt(identifier)
            store.mark_running(identifier, attempt)
        envelope = dict(protocolVersion=1, jobId=job_id, attemptId=attempt, action="translate", inputPath=doc["input_path"],
                        outputDirectory=str(directory), sourceLanguage=request.get("sourceLanguage", "en"),
                        targetLanguage=request.get("targetLanguage", "zh"), pages=request.get("pages"), service=service)
        engine_root = Path(__file__).resolve().parent.parent / "engine"
        task = WorkerTask(engine_root, envelope, on_event=send).start()

        def controls():
            while task.running:
                try:
                    line = read_line()
                    if not line:
                        return
                    command = json.loads(line)
                    if command == {"action": "cancel"}:
                        task.cancel()
                except (ValueError, OSError):
                    return

        threading.Thread(target=controls, name="PageBridge-bridge-control", daemon=True).start()
        task.join()
        with library_lock(root):
            store = DocumentStore(root, recover_interrupted=False)
            if task.error or task.cancelled or not task.result:
                document = store.finish_failure(identifier, task.error, task.cancelled)
                terminal("error", stage="cancelled" if task.cancelled else "failed", message=document["last_error"], document=document)
            else:
                document = store.commit_result(identifier, attempt, task.result)
                terminal("result", progress=1.0, message="翻译完成，结果已保存。", document=document,
                         translatedPath=document["translated_path"], bilingualPath=document["bilingual_path"])
    except BaseException as error:
        if task and task.running:
            task.cancel()
            task.join(12)
        message = str(error) if isinstance(error, AppError) else "无法完成翻译，旧译文未被覆盖。"
        try:
            with library_lock(root):
                store = DocumentStore(root, recover_interrupted=False)
                doc = store.get(identifier)
                # A preflight failure must not overwrite a separate live task.
                if doc.get("attempt_id") == attempt:
                    store.finish_failure(identifier, message)
        except Exception:
            pass
        terminal("error", message=message)


def checked_request(raw):
    # Decode explicitly: the wire accepts UTF-8, not json.loads' UTF-16 auto-detect.
    request = json.loads(raw.decode("utf-8"))
    if not isinstance(request, dict) or not isinstance(request.get("action"), str):
        raise AppError("请求格式无效。")
    for old, current in (("documentId", "document_id"), ("serviceId", "service_id")):
        if old in request and current not in request:
            request[current] = request[old]
    return request


def request_identity(request):
    identifier = request.get("requestId")
    if type(request.get("protocolVersion")) is not int or request["protocolVersion"] != 1:
        raise AppError("本地会话协议版本无效。")
    if not isinstance(identifier, str) or len(identifier) != 36:
        raise AppError("本地请求标识无效。")
    try:
        uuid.UUID(identifier)
    except ValueError:
        raise AppError("本地请求标识无效。") from None
    return identifier


def serve(handshake, root):
    identifier = request_identity(handshake)
    emit(dict(ok=True, protocolVersion=1, requestId=identifier, result={"mode": "serve"}))
    while True:
        identifier = None
        try:
            raw = read_line(require_newline=True)
            if not raw:
                return 0
            request = checked_request(raw)
            identifier = request_identity(request)
            if data_root(request) != root:
                raise AppError("本地会话不能切换文档库。")
            if request["action"] not in LOCAL_ACTIONS:
                raise AppError("此操作不能复用文档会话。")
            emit(dict(ok=True, protocolVersion=1, requestId=identifier, result=dispatch(request, root)))
        except Exception as error:
            message = str(error) if isinstance(error, AppError) else "无法完成操作，请检查输入和本地数据目录。"
            emit(dict(ok=False, protocolVersion=1, requestId=identifier, error=message))
            # An invalid frame has no safe matching response identity. End the
            # session rather than consuming any remainder as another request.
            if identifier is None:
                return 1


def main():
    try:
        request = checked_request(read_line())
        root = data_root(request)
        if request["action"] == "serve":
            return serve(request, root)
        result = dispatch(request, root)
        if request["action"] != "translate":
            emit({"ok": True, "result": result})
        return 0
    except BaseException as error:
        emit({"ok": False, "error": str(error) if isinstance(error, AppError) else "无法完成操作，请检查输入和本地数据目录。"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
