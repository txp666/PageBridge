#!/usr/bin/env python3
"""One request per process. Protocol stdout is isolated from third-party output."""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

import glossary

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 1_048_576
EXPECTED = {"PyMuPDF": "1.25.2", "httpx": "0.28.1", "psutil": "7.2.2"}


class WorkerError(Exception):
    """Only fixed, non-secret messages should be constructed here."""


class ProtocolInput:
    """Bounded raw pipe input, including an optional cancellation control line.

    Raw reads avoid a daemon reader retaining Python's BufferedReader lock when
    a desktop host keeps stdin open after a completed request.
    """
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.pending = b""
        self._read = lambda count: os.read(self.descriptor, count)
        if sys.platform == "win32":
            # A pending synchronous read of stdin can block native-library
            # startup (observed in NumPy's multiarray extension). Peek first so
            # the control thread never leaves a pending read on that handle.
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

            def read_windows(count):
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
                    if error in (109, 232):  # Broken pipe / no data after close.
                        return b""
                    raise ctypes.WinError(error)
                return buffer.raw[:received.value]

            self._read = read_windows

    def readline(self):
        while b"\n" not in self.pending and len(self.pending) <= MAX_REQUEST_BYTES:
            chunk = self._read(min(4096, MAX_REQUEST_BYTES + 1 - len(self.pending)))
            if not chunk:
                line, self.pending = self.pending, b""
                return line
            self.pending += chunk
        if b"\n" in self.pending:
            line, self.pending = self.pending.split(b"\n", 1)
            return line + b"\n"
        line, self.pending = self.pending, b""
        return line

    def watch_cancel(self, request, loop, task):
        try:
            while raw := self.readline():
                if len(raw) > MAX_REQUEST_BYTES:
                    return
                try:
                    control = json.loads(raw)
                except (ValueError, UnicodeError):
                    continue
                if (isinstance(control, dict) and control.get("protocolVersion") == PROTOCOL_VERSION
                        and control.get("action") == "cancel"
                        and control.get("jobId") == request["jobId"]
                        and control.get("attemptId") == request["attemptId"]):
                    loop.call_soon_threadsafe(task.cancel)
                    return
        except (OSError, RuntimeError):
            # Pipe/loop teardown after the one request has already completed.
            return


def require_text(data, key, limit=256):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise WorkerError(f"{key} 格式无效。")
    return value.strip()


PROVIDER_ENDPOINTS = {
    "deepSeek": ("https://api.deepseek.com", "https://api.deepseek.com/v1"),
    "alibaba": ("https://dashscope.aliyuncs.com/compatible-mode/v1",),
    "siliconFlow": ("https://api.siliconflow.cn/v1",),
    "openAI": ("https://api.openai.com/v1",),
}


def token_limit_key(service):
    modern_openai = service.get("provider") == "openAI" and service["model"].startswith(("gpt-6", "gpt-5", "o"))
    return "max_completion_tokens" if modern_openai else "max_tokens"


def provider_request_options(service, *, probe=False):
    """No credentials or URL changes; only documented options for exact official presets."""
    provider = service.get("provider", "custom")
    result = {}
    model = service["model"]
    if probe:
        result[token_limit_key(service)] = 1024
    if service["kind"] == "ollama" or (provider == "openAI" and model.startswith("gpt-6")):
        result["reasoning_effort"] = "none"
    if service["kind"] == "ollama":
        # Local model manifests may enable a repetition penalty (the tested
        # Qwen model defaults to 1.5). Translation must retain repeated numbers,
        # terms and formula markers instead of discouraging their recurrence.
        result["presence_penalty"] = 0
        if not probe:
            # A pathological paragraph must stop rather than consume the
            # model's entire context window. A length finish fails that block.
            result["max_tokens"] = 8192
    if provider == "deepSeek":
        result["thinking"] = {"type": "disabled"}
    elif provider == "alibaba" and model in ("qwen-plus", "qwen-flash"):
        result["enable_thinking"] = False
    if provider == "siliconFlow" and model == "deepseek-ai/DeepSeek-V4-Flash":
        result["enable_thinking"] = False
    return result


def normalized_service(service):
    if not isinstance(service, dict):
        raise WorkerError("缺少翻译服务配置。")
    kind = service.get("kind")
    if kind not in ("openai", "ollama"):
        raise WorkerError("不支持此翻译服务类型。")
    raw_url = require_text(service, "baseURL", 2048)
    try:
        url = urlsplit(raw_url)
        _ = url.port
    except ValueError:
        raise WorkerError("服务地址格式无效。") from None
    local = url.hostname in ("localhost", "127.0.0.1", "::1")
    if url.scheme not in ("https", "http") or not url.hostname:
        raise WorkerError("服务地址须使用 HTTPS 或本机 HTTP。")
    if url.username or url.password or url.query or url.fragment:
        raise WorkerError("服务地址不能包含账号、密码、查询参数或锚点。")
    if url.scheme == "http" and not local:
        raise WorkerError("远程服务必须使用 HTTPS。")
    if kind == "ollama" and not local:
        raise WorkerError("Ollama 必须使用本机回环地址。")
    path = url.path.rstrip("/")
    if path.endswith("/chat/completions"):
        raise WorkerError("请填写 Base URL，不要包含 /chat/completions。")
    # Ollama implements OpenAI chat completions; both kinds share one response contract.
    if kind == "ollama":
        if path.endswith("/api"):
            path = path[:-4]
        if not path.endswith("/v1"):
            path += "/v1"
    endpoint = urlunsplit((url.scheme, url.netloc, path, "", ""))
    provider = service.get("provider")
    if provider is None:
        provider = "ollama" if kind == "ollama" else next((name for name, endpoints in PROVIDER_ENDPOINTS.items() if endpoint in endpoints), "custom")
    if provider not in (*PROVIDER_ENDPOINTS, "ollama", "custom"):
        raise WorkerError("不支持此服务厂商。")
    if (provider == "ollama") != (kind == "ollama") or (provider in PROVIDER_ENDPOINTS and endpoint not in PROVIDER_ENDPOINTS[provider]):
        raise WorkerError("服务厂商与地址不匹配；其他地址请使用自定义 API。")
    model = require_text(service, "model", 256)
    if any(ord(character) < 32 or ord(character) == 127 for character in model):
        raise WorkerError("模型名称格式无效。")
    api_key = service.get("apiKey") or ""
    if not isinstance(api_key, str) or len(api_key) > 16384:
        raise WorkerError("API Key 格式无效。")
    if any(ord(character) < 32 or ord(character) == 127 for character in api_key.strip()):
        raise WorkerError("API Key 格式无效。")
    if kind == "openai" and not api_key.strip():
        raise WorkerError("请填写 API Key。")
    timeout = service.get("timeoutSeconds", 120)
    concurrency = service.get("concurrency", 2)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 10 <= timeout <= 600:
        raise WorkerError("超时时间须介于 10 至 600 秒之间。")
    if isinstance(concurrency, bool) or not isinstance(concurrency, int) or not 1 <= concurrency <= 8:
        raise WorkerError("并发数须介于 1 至 8 之间。")
    return {"kind": kind, "provider": provider, "baseURL": endpoint, "model": model,
            "apiKey": api_key.strip() or "ollama-local", "timeoutSeconds": timeout, "concurrency": concurrency}


def parse_request(raw):
    if len(raw) > MAX_REQUEST_BYTES:
        raise WorkerError("请求内容过大。")
    try:
        request = json.loads(raw)
    except (ValueError, UnicodeError):
        raise WorkerError("请求必须是单行 JSON 对象。") from None
    if not isinstance(request, dict) or request.get("protocolVersion") != PROTOCOL_VERSION:
        raise WorkerError("不支持此协议版本。")
    for key in ("jobId", "attemptId"):
        value = require_text(request, key, 128)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise WorkerError(f"{key} 格式无效。")
    if request.get("action") not in ("health", "probe", "translate", "prepare_reading", "export_reading", "suggest_terms"):
        raise WorkerError("不支持此操作。")
    if request["action"] in ("probe", "translate", "suggest_terms"):
        request["service"] = normalized_service(request.get("service"))
    paths = {"translate": ("inputPath", "outputDirectory"),
             "suggest_terms": ("inputPath",) + (("readingPath",) if request.get("readingPath") is not None else ()),
             "export_reading": ("inputPath", "readingPath", "outputPath")}.get(request["action"], ())
    for key in paths:
        path = Path(require_text(request, key, 8192)).expanduser()
        if not path.is_absolute():
            raise WorkerError(f"{key} 必须是绝对路径。")
        request[key] = str(path.resolve())
    if request["action"] == "export_reading" and request.get("kind") not in ("translated", "bilingual"):
        raise WorkerError("不支持此导出形式。")
    if request["action"] == "suggest_terms":
        for key, default in (("sourceLanguage", "en"), ("targetLanguage", "zh")):
            request.setdefault(key, default)
            if not isinstance(request[key], str) or not re.fullmatch(r"[a-zA-Z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?", request[key]):
                raise WorkerError(f"{key} 格式无效。")
        terms = request.get("glossary")
        if terms is not None and (not isinstance(terms, str) or len(terms) > glossary.MAX_TEXT):
            raise WorkerError("术语表格式无效或超过 100,000 字符。")
        limit = request.setdefault("limit", 60)
        if type(limit) is not int or not 1 <= limit <= 150:
            raise WorkerError("术语数量上限无效。")
    if request["action"] == "translate":
        for key, default in (("sourceLanguage", "en"), ("targetLanguage", "zh")):
            request.setdefault(key, default)
            if not isinstance(request[key], str) or not re.fullmatch(r"[a-zA-Z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?", request[key]):
                raise WorkerError(f"{key} 格式无效。")
        terms = request.get("glossary")
        if terms is not None and (not isinstance(terms, str) or len(terms) > glossary.MAX_TEXT):
            raise WorkerError("术语表格式无效或超过 100,000 字符。")
        pages = request.get("pages")
        if pages is not None and (not isinstance(pages, str) or len(pages) > 2048):
            raise WorkerError("页码范围格式无效。")
        retry = request.get("retryBlockIds")
        retry_terms = request.get("retryTerms")
        append = request.get("appendPages")
        reading_path = request.get("readingPath")
        if append is not None and (append is not True or pages is None or retry is not None or retry_terms is not None):
            raise WorkerError("追加翻译需要指定页码。")
        if retry_terms is not None and (retry is not None or not isinstance(retry_terms, list) or not 1 <= len(retry_terms) <= 200
                                        or any(not isinstance(t, str) or not t.strip() or len(t) > 200 for t in retry_terms)):
            raise WorkerError("请提供有效的重译术语。")
        if retry is not None or retry_terms is not None or append or reading_path is not None:
            if retry_terms is None and not append and (
                    not isinstance(retry, list) or not 1 <= len(retry) <= 1000
                    or any(not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value) for value in retry)
                    or len(set(retry)) != len(retry)):
                raise WorkerError("请选择有效的译文块重试。")
            path = Path(require_text(request, "readingPath", 8192)).expanduser()
            if not path.is_absolute():
                raise WorkerError("对照阅读结果路径必须是绝对路径。")
            request["readingPath"] = str(path.resolve())
    return request


class Events:
    def __init__(self, stream, request=None):
        request = request or {}
        self.stream = stream
        self.job_id = request.get("jobId", "unknown")
        self.attempt_id = request.get("attemptId", "unknown")
        self.sequence = 0
        self.terminal = False
        self.engine_version = None

    def emit(self, event_type, **fields):
        if self.terminal:
            return
        if event_type == "result" and self.engine_version is not None:
            fields.setdefault("engineVersion", self.engine_version)
        self.sequence += 1
        event = {"protocolVersion": PROTOCOL_VERSION, "jobId": self.job_id,
                 "attemptId": self.attempt_id, "sequence": self.sequence, "type": event_type, **fields}
        self.stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()
        if event_type in ("result", "error"):
            self.terminal = True


def installed_versions():
    actual = {name: importlib.metadata.version(name) for name in EXPECTED}
    if actual != EXPECTED:
        raise WorkerError("引擎依赖版本与验证版本不一致，请重新安装引擎。")
    return "; ".join(f"{name} {version}" for name, version in actual.items())


def safe_error(error):
    if isinstance(error, WorkerError):
        return str(error)
    # This module defines fixed user-facing errors; arbitrary parser/provider
    # exception text must still stay out of the protocol.
    from structured_reading import ReadingError
    from term_suggestions import SuggestionError
    if isinstance(error, (ReadingError, SuggestionError)):
        return str(error)
    # Classify privately; never echo upstream exceptions, request content or keys.
    message = str(error).lower()
    name = type(error).__name__.lower()
    if "401" in message or "authentication" in name:
        return "服务拒绝了 API Key（401），请检查密钥。"
    if "403" in message:
        return "服务拒绝访问（403），请检查访问权限。"
    if "429" in message or "ratelimit" in name:
        return "服务请求过于频繁（429），请稍后重试或降低并发数。"
    if "404" in message:
        return "未找到 API 接口或模型（404），请检查地址和模型名。"
    if "timeout" in name or "timed out" in message:
        return "服务请求超时，请检查模型和超时设置。"
    if "connect" in name:
        return "无法连接所选翻译服务，请检查服务是否启动及地址设置。"
    if isinstance(error, ImportError):  # Includes importlib.metadata.PackageNotFoundError.
        return "翻译引擎安装不完整，请重新安装引擎。"
    return "翻译失败，请检查 PDF、所选模型及引擎资源。"


async def probe(request, events):
    import httpx
    service = request["service"]
    events.emit("stage", stage="probe", message="正在使用所选模型测试短句翻译。")
    payload = {"model": service["model"], "stream": False,
               "messages": [{"role": "system", "content": "Translate the user's English sentence into Simplified Chinese. Return only the translation. /no_think"},
                            {"role": "user", "content": "The control valve regulates water flow."}]}
    payload.update(provider_request_options(service, probe=True))
    async with httpx.AsyncClient(timeout=service["timeoutSeconds"], follow_redirects=False, trust_env=False) as client:
        response = await client.post(service["baseURL"] + "/chat/completions", json=payload,
                                     headers={"Authorization": "Bearer " + service["apiKey"]})
        if 300 <= response.status_code < 400:
            raise WorkerError("服务返回了重定向，请使用最终服务的 Base URL。")
        response.raise_for_status()
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError):
            raise WorkerError("服务响应不兼容 Chat Completions 接口。") from None
    if not isinstance(content, str) or not re.search(r"[\u3400-\u9fff]", content):
        raise WorkerError("模型已响应，但未返回要求的中文译文。")
    # Do not forward arbitrary provider response text into the protocol.
    events.emit("result", message="模型已真实返回测试短句的中文译文。")


def page_selection(value, count):
    if value is None:
        return None
    parts = value.split(",")
    if not parts:
        raise WorkerError("页码范围格式无效。")
    selected = set()
    for part in parts:
        match = re.fullmatch(r"\s*(\d+)(?:\s*-\s*(\d+))?\s*", part)
        if not match:
            raise WorkerError("请使用 1,3-5 这样的页码范围。")
        start, end = int(match[1]), int(match[2] or match[1])
        if not 1 <= start <= end <= count:
            raise WorkerError("页码范围超出了文档页数。")
        selected.update(range(start, end + 1))
    return ",".join(map(str, sorted(selected)))


async def run(request, events, control_input=None):
    task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, task.cancel)
    if control_input is not None and request["action"] != "health":
        threading.Thread(target=control_input.watch_cancel, args=(request, loop, task),
                         name="pagebridge-cancel", daemon=True).start()
    version = installed_versions()
    events.engine_version = version
    events.emit("ready", engineVersion=version)
    if request["action"] == "health":
        import pymupdf
        import psutil
        events.emit("result", message="引擎依赖已就绪，首次翻译会准备 MinerU 本地识别组件。")
    elif request["action"] == "probe":
        await probe(request, events)
    elif request["action"] == "prepare_reading":
        from structured_reading import prepare
        await prepare(events)
    elif request["action"] == "export_reading":
        from reading_export import export
        await export(request, events)
    elif request["action"] == "suggest_terms":
        from term_suggestions import suggest
        await suggest(request, events)
    else:
        from structured_reading import translate
        await translate(request, events)


def main():
    if hasattr(os, "setsid"):
        try:
            os.setsid()
        except PermissionError:
            pass  # Parent already launched this worker as a process-group leader.
    # Duplicate the original pipes before redirecting both raw FDs. Python,
    # native extensions and spawned children cannot pollute NDJSON or leak logs.
    protocol = os.fdopen(os.dup(sys.stdout.fileno()), "w", encoding="utf-8", buffering=1)
    diagnostics = os.fdopen(os.dup(sys.stderr.fileno()), "w", encoding="utf-8", buffering=1)
    null = os.open(os.devnull, os.O_WRONLY)
    os.dup2(null, 1)
    os.dup2(null, 2)
    os.close(null)
    events = Events(protocol)
    action = None
    try:
        control_input = ProtocolInput(sys.stdin.fileno())
        raw = control_input.readline()
        try:
            envelope = json.loads(raw)
            if isinstance(envelope, dict) and all(isinstance(envelope.get(key), str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", envelope[key]) for key in ("jobId", "attemptId")):
                events = Events(protocol, envelope)
        except (ValueError, UnicodeError):
            pass
        request = parse_request(raw)
        action = request["action"]
        events = Events(protocol, request)
        asyncio.run(run(request, events, control_input))
        return 0
    except (asyncio.CancelledError, KeyboardInterrupt):
        events.emit("error", stage="cancelled", message="导出已取消。" if action == "export_reading" else "翻译已取消。")
        return 130
    except BaseException as error:
        message = safe_error(error)
        diagnostics.write("PageBridge worker: " + message + "\n")
        events.emit("error", message=message)
        return 1
    finally:
        protocol.close()
        diagnostics.close()


if __name__ == "__main__":
    raise SystemExit(main())
