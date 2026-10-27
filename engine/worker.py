#!/usr/bin/env python3
"""One request per process. Protocol stdout is isolated from third-party output."""
from __future__ import annotations

import asyncio
from functools import wraps
import hashlib
import html
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
import threading
import time
import unicodedata
from urllib.parse import urlsplit, urlunsplit

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 65536
EXPECTED = {"pdf2zh-next": "2.9.0", "babeldoc": "0.6.2", "PyMuPDF": "1.25.2", "bitstring": "4.3.0"}
_UPSTREAM_TRANSLATE_WRAPPER = None
STAGES = {
    "Parse PDF and Create Intermediate Representation": ("parse", "正在解析 PDF。"),
    "DetectScannedFile": ("preflight", "正在检查文档文字层。"),
    "Parse Page Layout": ("layout", "正在分析页面版式。"),
    "Parse Paragraphs": ("paragraphs", "正在识别段落。"),
    "Parse Formulas and Styles": ("formulas", "正在识别公式与样式。"),
    "Translate Paragraphs": ("translate", "正在翻译段落。"),
    "Typesetting": ("typeset", "正在排版译文。"),
    "Add Fonts": ("fonts", "正在嵌入字体。"),
    "Generate drawing instructions": ("render", "正在生成页面。"),
    "Subset font": ("fonts", "正在优化字体。"),
    "Save PDF": ("save", "正在保存 PDF。"),
}


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


def checked_completion_create(create):
    """Do not let the upstream string-only adapter accept a partial response."""
    @wraps(create)
    def checked(*args, **kwargs):
        response = create(*args, **kwargs)
        if any(getattr(choice, 'finish_reason', None) in ('length', 'content_filter')
               for choice in getattr(response, 'choices', [])):
            raise ValueError('translation-response-incomplete')
        return response
    return checked


def install_response_guard():
    from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator
    original = OpenAITranslator.__init__
    if getattr(original, '_pagebridge_checked', False):
        return

    @wraps(original)
    def initialize(self, *args, **kwargs):
        original(self, *args, **kwargs)
        extra = self.options.get('extra_body', {})
        local = urlsplit(str(self.client.base_url)).hostname in ('localhost', '127.0.0.1', '::1')
        if local and extra.get('max_tokens') == 8192 and extra.get('presence_penalty') == 0:
            # These two options are emitted only by our local Ollama preset.
            # Repeating an identical timed-out deterministic request does not
            # repair it; the paragraph guard owns the explicit repair retries.
            self.client.max_retries = 0
        completions = self.client.chat.completions
        completions.create = checked_completion_create(completions.create)
    initialize._pagebridge_checked = True
    OpenAITranslator.__init__ = initialize


def guarded_translate_wrapper(settings, *args):
    """Install the checked adapter inside the actual translation subprocess.

    macOS and Windows use spawn: a parent-only monkeypatch does nothing in the
    process that translates paragraphs. Keep this target at module scope so it
    can be pickled; the original entrypoint is fresh on spawn and saved on fork.
    """
    import pdf2zh_next.high_level as high_level
    import translation_guard
    import layout_guard
    import pdf_links
    original = _UPSTREAM_TRANSLATE_WRAPPER or high_level._translate_wrapper
    layout_guard.install()
    translation_guard.install()
    install_response_guard()
    report_path = Path(settings.translation.output) / "translation-quality.json"
    file, progress_pipe, *remaining = args
    pdf_links.configure(file)
    report_written = False

    def write_report():
        pdf_links.write_mapping(Path(settings.translation.output) / 'link-mapping.json')
        report = translation_guard.write_report(report_path)
        report["layoutGuard"] = layout_guard.snapshot()
        temporary_report = report_path.with_suffix(".json.layout-tmp")
        temporary_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary_report.replace(report_path)

    class CheckedProgressPipe:
        def send(self, event):
            nonlocal report_written
            # The parent receives finish before upstream's child wrapper returns.
            # Publish the report first, so accepting results never races teardown.
            if isinstance(event, dict) and event.get("type") == "finish":
                write_report()
                report_written = True
            return progress_pipe.send(event)

        def __getattr__(self, name):
            return getattr(progress_pipe, name)

    try:
        original(settings, file, CheckedProgressPipe(), *remaining)
    finally:
        if not report_written:
            write_report()


def read_quality_report(path):
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise WorkerError("翻译质量校验未完成，结果未提交。") from None
    if not isinstance(report, dict):
        raise WorkerError("翻译质量校验未完成，结果未提交。")
    counts = [report.get(key) for key in ("preparedParagraphs", "validatedParagraphs", "unresolvedCount")]
    if any(type(value) is not int or value < 0 for value in counts) or counts[0] != counts[1] + counts[2]:
        raise WorkerError("翻译质量校验记录不完整，结果未提交。")
    if report.get("guardInstalled") is not True or report.get("layoutGuard", {}).get("guardInstalled") is not True or counts[2] != 0:
        raise WorkerError("部分段落未通过译文完整性校验，已保留上次结果。请重试或更换模型。")
    return report


def artifact_marker_count(text):
    # PDF text extraction sometimes inserts control characters inside tag names.
    clean = re.sub(r"[\x00-\x1f\x7f\u200b\u00ad]", "", html.unescape(unicodedata.normalize("NFKC", text)))
    return len(re.findall(r"<\s*/?\s*(?:span|style|font)\b|[<＜]\s*/?\s*(?:风格|样式)|<\s*/?\s*[a-z][a-z ]{1,16}(?:[=\s][^<>]{0,80})?\s*/?>", clean, re.IGNORECASE))


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


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


def provider_request_options(service, *, probe=False):
    """No credentials or URL changes; only documented options for exact official presets."""
    provider = service.get("provider", "custom")
    result = {}
    model = service["model"]
    if probe:
        modern_openai = provider == "openAI" and model.startswith(("gpt-6", "gpt-5", "o"))
        result["max_completion_tokens" if modern_openai else "max_tokens"] = 1024
    if service["kind"] == "ollama" or (provider == "openAI" and model.startswith("gpt-6")):
        result["reasoning_effort"] = "none"
    if service["kind"] == "ollama":
        # Local model manifests may enable a repetition penalty (the tested
        # Qwen model defaults to 1.5). Translation must retain repeated numbers,
        # terms and formula markers instead of discouraging their recurrence.
        result["presence_penalty"] = 0
        if not probe:
            # A pathological paragraph must stop rather than consume the
            # model's entire context window. A length finish is rejected above.
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
    # Ollama implements OpenAI chat completions. This gives both kinds the same
    # timeout-aware official pdf2zh-next translator and response contract.
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
    if request.get("action") not in ("health", "probe", "translate", "prepare_reading"):
        raise WorkerError("不支持此操作。")
    if request["action"] in ("probe", "translate"):
        request["service"] = normalized_service(request.get("service"))
    if request["action"] == "translate":
        request.setdefault("outputMode", "pdf")
        if request["outputMode"] not in ("pdf", "reading"):
            raise WorkerError("不支持的译文格式。")
        for key in ("inputPath", "outputDirectory"):
            path = Path(require_text(request, key, 8192)).expanduser()
            if not path.is_absolute():
                raise WorkerError(f"{key} 必须是绝对路径。")
            request[key] = str(path.resolve())
        for key, default in (("sourceLanguage", "en"), ("targetLanguage", "zh")):
            request.setdefault(key, default)
            if not isinstance(request[key], str) or not re.fullmatch(r"[a-zA-Z]{2,3}(?:[-_][A-Za-z0-9]{2,8})?", request[key]):
                raise WorkerError(f"{key} 格式无效。")
        pages = request.get("pages")
        if pages is not None and (not isinstance(pages, str) or len(pages) > 2048):
            raise WorkerError("页码范围格式无效。")
        retry = request.get("retryBlockIds")
        reading_path = request.get("readingPath")
        if retry is not None or reading_path is not None:
            if request["outputMode"] != "reading":
                raise WorkerError("仅对照阅读支持按块重试。")
            if (not isinstance(retry, list) or not 1 <= len(retry) <= 1000
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
    if isinstance(error, ReadingError):
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
    if "scanned" in message:
        return "检测到扫描 PDF，当前版本需要可读取文字的 PDF。"
    if "database is locked" in message:
        return "引擎翻译缓存正忙，请等待其他任务完成后重试。"
    if isinstance(error, importlib.metadata.PackageNotFoundError) or isinstance(error, ImportError):
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


def build_settings(request, temporary_output):
    from pdf2zh_next.config.model import SettingsModel
    service = request["service"]
    engine = {"translate_engine_type": "OpenAICompatible", "openai_compatible_base_url": service["baseURL"],
              "openai_compatible_api_key": service["apiKey"], "openai_compatible_model": service["model"],
              "openai_compatible_timeout": str(service["timeoutSeconds"])}
    if service["kind"] == "ollama":
        engine.update(openai_compatible_send_reasoning_effort=True, openai_compatible_reasoning_effort="none",
                      openai_compatible_send_temperature=True, openai_compatible_temperature="0")
    request_options = provider_request_options(service)
    if "reasoning_effort" in request_options:
        engine.update(openai_compatible_send_reasoning_effort=True,
                      openai_compatible_reasoning_effort=request_options["reasoning_effort"])
    settings = SettingsModel.model_validate({
        "basic": {"debug": False, "gui": False},
        "report_interval": 0.2,
        "translation": {"lang_in": request["sourceLanguage"], "lang_out": request["targetLanguage"],
                        "output": str(temporary_output), "qps": service["concurrency"],
                        "pool_max_workers": service["concurrency"], "no_auto_extract_glossary": True,
                        "ignore_cache": True,
                        "custom_system_prompt": "/no_think You are a professional scientific translation engine. Translate the human-readable content faithfully. Preserve personal author names, email addresses and URLs in their original spelling. Preserve classification identifiers such as Zone 1/Division 2 and Class I, Div. 1 verbatim. Preserve every formula token, variable, number and unit. Copy all markup tokens byte-for-byte, including tag names, numeric IDs and quotation marks. Never turn style into span or translate tag names. Return only the requested translation or JSON structure."},
        "pdf": {"pages": request.get("pages"), "no_mono": False, "no_dual": False,
                "watermark_output_mode": "no_watermark", "use_alternating_pages_dual": True,
                "only_include_translated_page": False, "translate_table_text": False,
                "split_short_lines": True, "short_line_split_factor": 0.8,
                "no_remove_non_formula_lines": True},
        "gui_settings": {"disable_config_auto_save": True},
        "translate_engine_settings": engine,
    })
    extra_body = {key: value for key, value in request_options.items() if key != "reasoning_effort"}
    if extra_body:
        # Convert using the upstream transform, then retain its private request options.
        # OpenAISettings survives the worker process pickle without putting keys/options in logs.
        settings.translate_engine_settings = settings.translate_engine_settings.transform()
        settings.translate_engine_settings._openai_extra_body = extra_body
    return settings


def validate_pdf(path, root, expected_pages):
    import pymupdf
    if not path:
        raise WorkerError("引擎未生成所需的两份 PDF。")
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file() or resolved.stat().st_size < 100:
        raise WorkerError("引擎返回的输出路径无效。")
    with pymupdf.open(resolved) as pdf:
        if pdf.needs_pass or not pdf.is_pdf or pdf.page_count != expected_pages:
            raise WorkerError("生成的 PDF 未通过页数或格式验证。")
        # Reading/rendering each output page catches invalid page objects/streams.
        for page in pdf:
            page.get_text()
            page.get_pixmap(matrix=pymupdf.Matrix(0.1, 0.1), alpha=False)
    return resolved


async def translate(request, events):
    global _UPSTREAM_TRANSLATE_WRAPPER
    import pymupdf
    input_path = Path(request["inputPath"])
    output = Path(request["outputDirectory"])
    if not input_path.is_file():
        raise WorkerError("输入 PDF 不存在。")
    events.emit("stage", stage="preflight", message="正在检查输入 PDF。")
    try:
        with pymupdf.open(input_path) as pdf:
            if pdf.needs_pass or not pdf.is_pdf or pdf.page_count < 1:
                raise WorkerError("请使用未加密且至少包含一页的 PDF。")
            page_count = pdf.page_count
            request["pages"] = page_selection(request.get("pages"), page_count)
            selected_pages = ([int(number) - 1 for number in request["pages"].split(",")]
                              if request["pages"] else range(page_count))
            if not any(pdf[index].get_text().strip() for index in selected_pages):
                if request["pages"]:
                    raise WorkerError("所选页面没有可读取的文字，暂不支持纯扫描页。请选择带文字层的页面。")
                raise WorkerError("PDF 没有可读取的文字，暂不支持纯扫描件。")
    except WorkerError:
        raise
    except Exception:
        raise WorkerError("无法读取输入 PDF。") from None
    output.mkdir(parents=True, exist_ok=True)
    destination = output / ("result-" + request["attemptId"])
    if destination.exists():
        raise WorkerError("本次尝试已存在输出，请开始新的翻译尝试。")
    # Reject unusable input before upstream imports can initialize their cache,
    # layout resources or translation provider.
    events.emit("stage", stage="prepare", message="正在加载翻译组件，首次使用可能下载官方版面模型和字体。")
    import pdf2zh_next.high_level as high_level
    if _UPSTREAM_TRANSLATE_WRAPPER is None:
        _UPSTREAM_TRANSLATE_WRAPPER = high_level._translate_wrapper
        high_level._translate_wrapper = guarded_translate_wrapper
    input_hash = sha256(input_path)
    with tempfile.TemporaryDirectory(prefix=".pagebridge-", dir=output) as temporary:
        temporary = Path(temporary)
        settings = build_settings(request, temporary)
        last_stage, last_progress = None, 0.0
        result = None
        events.emit("stage", stage="prepare", message="正在准备版面模型和字体，首次使用可能需要下载资源。")
        stream = high_level.do_translate_async_stream(settings, input_path)
        try:
            async for event in stream:
                kind = event.get("type")
                if kind == "error":
                    raise WorkerError(safe_error(Exception(str(event.get("error", "")))))
                if kind == "finish":
                    result = event.get("translate_result")
                    break
                if kind in ("progress_start", "progress_update", "progress_end"):
                    # Only recognized fixed labels cross the protocol; upstream
                    # strings can contain arbitrary data and are not UI messages.
                    stage, message = STAGES.get(str(event.get("stage", "")), ("translate", "正在处理文档。"))
                    if stage != last_stage:
                        last_stage = stage
                        events.emit("stage", stage=stage, message=message)
                    progress = event.get("overall_progress")
                    if isinstance(progress, (float, int)) and math.isfinite(progress):
                        last_progress = max(last_progress, min(0.98, max(0.0, progress / 100)))
                        events.emit("progress", stage=stage, progress=last_progress, message=message)
        finally:
            await stream.aclose()
        if result is None:
            raise WorkerError("翻译进程已结束，但未返回结果。")
        report_path = temporary / "translation-quality.json"
        try:
            quality = read_quality_report(report_path)
        except WorkerError:
            # Keep text-free diagnostic counters when rejecting a result. Never
            # expose the rejected PDFs as a successful result or retain prompts.
            if report_path.is_file():
                shutil.copyfile(report_path, output / ("rejected-quality-" + request["attemptId"] + ".json"))
            raise
        events.emit("stage", stage="verify", progress=0.99, message="正在验证生成的 PDF。")
        mono = validate_pdf(getattr(result, "no_watermark_mono_pdf_path", None) or getattr(result, "mono_pdf_path", None), temporary, page_count)
        dual = validate_pdf(getattr(result, "no_watermark_dual_pdf_path", None) or getattr(result, "dual_pdf_path", None), temporary, page_count * 2)
        if mono == dual or mono == input_path or dual == input_path:
            raise WorkerError("引擎未生成独立的译文和双语 PDF。")
        import pdf_links
        quality['links'] = pdf_links.restore_links(input_path, mono, dual, temporary / 'link-mapping.json')
        with pymupdf.open(input_path) as original, pymupdf.open(mono) as translated:
            pages = [int(number) - 1 for number in request["pages"].split(",")] if request["pages"] else range(page_count)
            for index in pages:
                if artifact_marker_count(translated[index].get_text()) > artifact_marker_count(original[index].get_text()):
                    raise WorkerError("译文仍含未解析的样式标记，结果未提交。请重试或更换模型。")
            original_text = "".join(original[index].get_text() for index in pages)
            translated_text = "".join(translated[index].get_text() for index in pages)
            if not translated_text.strip() or re.sub(r"\s+", "", original_text) == re.sub(r"\s+", "", translated_text):
                raise WorkerError("输出未包含已翻译的文字，请尝试其他模型。")
            if request["targetLanguage"].lower().startswith("zh") and not re.search(r"[\u3400-\u9fff]", translated_text):
                raise WorkerError("输出没有中文译文，所选模型可能不兼容。")
        # Fixed names and same-volume directory rename publish both outputs together.
        committed = temporary / "committed"
        committed.mkdir()
        shutil.copyfile(mono, committed / "translated.pdf")
        shutil.copyfile(dual, committed / "bilingual.pdf")
        (committed / "translation-quality.json").write_text(json.dumps(quality, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        manifest = {"protocolVersion": 1, "jobId": request["jobId"], "attemptId": request["attemptId"],
                    "sourceSHA256": input_hash, "sourcePages": page_count, "pages": request["pages"],
                    "sourceLanguage": request["sourceLanguage"], "targetLanguage": request["targetLanguage"],
                    "model": request["service"]["model"], "engineVersions": EXPECTED,
                    "qualityGuard": quality,
                    "translatedSHA256": sha256(committed / "translated.pdf"),
                    "bilingualSHA256": sha256(committed / "bilingual.pdf")}
        (committed / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        committed.rename(destination)
        events.emit("result", progress=1.0, translatedPath=str(destination / "translated.pdf"), bilingualPath=str(destination / "bilingual.pdf"), message="翻译完成，译文和双语 PDF 均已通过验证。")


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
        import onnxruntime
        # Importing pdf2zh_next opens a shared SQLite translation cache. Health
        # must remain safe to run while a translation worker owns that cache.
        events.emit("result", engineVersion=version, message="引擎依赖已就绪，首次翻译可能需要下载官方模型和字体。")
    elif request["action"] == "probe":
        await probe(request, events)
    elif request["action"] == "prepare_reading":
        from structured_reading import prepare
        await prepare(events)
    elif request.get("outputMode") == "reading":
        from structured_reading import translate as translate_reading
        await translate_reading(request, events)
    else:
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
        events = Events(protocol, request)
        asyncio.run(run(request, events, control_input))
        return 0
    except (asyncio.CancelledError, KeyboardInterrupt):
        events.emit("error", stage="cancelled", message="翻译已取消。")
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
