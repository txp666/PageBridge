"""Provider profiles, bounded HTTP clients and operating-system credentials."""
from __future__ import annotations

import copy
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .core import AppError, atomic_json


PRESETS = {
    "ollama": {"name": "Ollama · 本机", "kind": "ollama", "baseURL": "http://127.0.0.1:11434", "models": ["qwen3.5:35b-a3b-int4"]},
    "deepSeek": {"name": "DeepSeek", "kind": "openai", "baseURL": "https://api.deepseek.com", "models": ["deepseek-flash", "deepseek-v4-pro"]},
    "alibaba": {"name": "阿里云百炼（北京）", "kind": "openai", "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": ["qwen-plus", "qwen-flash"]},
    "siliconFlow": {"name": "硅基流动（中国站）", "kind": "openai", "baseURL": "https://api.siliconflow.cn/v1", "models": ["deepseek-ai/DeepSeek-V4-Flash"]},
    "openAI": {"name": "OpenAI", "kind": "openai", "baseURL": "https://api.openai.com/v1", "models": ["gpt-6-luna"]},
    "custom": {"name": "自定义 API", "kind": "openai", "baseURL": "https://api.example.com/v1", "models": []},
}


def new_profile(provider="ollama"):
    if provider not in PRESETS:
        raise AppError("不支持的服务厂商。")
    preset = PRESETS[provider]
    return dict(id=str(uuid.uuid4()), provider=provider, name=preset["name"], kind=preset["kind"], baseURL=preset["baseURL"],
                model=next(iter(preset["models"]), ""), timeoutSeconds=120, concurrency=1 if provider == "ollama" else 2)


def validate_profile(profile, require_model=True):
    # An explicit allowlist prevents accidental API keys entering the JSON file.
    fields = ("id", "name", "provider", "kind", "baseURL", "model", "timeoutSeconds", "concurrency")
    try:
        p = {key: profile[key] for key in fields}
        uuid.UUID(p["id"])
        for key in ("name", "baseURL", "model"):
            if not isinstance(p[key], str):
                raise ValueError()
            p[key] = p[key].strip()
        p["baseURL"] = p["baseURL"].rstrip("/")
        if not 1 <= len(p["name"]) <= 48 or p["provider"] not in PRESETS or PRESETS[p["provider"]]["kind"] != p["kind"]:
            raise ValueError()
        if len(p["model"]) > 256 or (require_model and not p["model"]) or any(ord(c) < 32 for c in p["model"]):
            raise ValueError()
        url = urllib.parse.urlsplit(p["baseURL"])
        _ = url.port
        local = url.hostname in {"localhost", "127.0.0.1", "::1"}
        if url.scheme not in {"https", "http"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError()
        if url.scheme == "http" and not local:
            raise AppError("远程服务必须使用 HTTPS；HTTP 仅支持本机地址。")
        if p["kind"] == "ollama" and not local:
            raise AppError("Ollama 配置只连接本机；远程服务请使用自定义 HTTPS API。")
        if url.path.lower().endswith("/chat/completions"):
            raise AppError("请填写 Base URL，移除末尾的 /chat/completions。")
        allowed = {PRESETS[p["provider"]]["baseURL"]}
        if p["provider"] == "deepSeek":
            allowed.add("https://api.deepseek.com/v1")
        if p["provider"] not in {"custom", "ollama"} and p["baseURL"] not in allowed:
            raise AppError("厂商与服务地址不匹配；修改地址请使用自定义 API。")
        if isinstance(p["timeoutSeconds"], bool) or not math.isfinite(p["timeoutSeconds"]) or not 10 <= p["timeoutSeconds"] <= 600:
            raise ValueError()
        if type(p["concurrency"]) is not int or not 1 <= p["concurrency"] <= 8:
            raise ValueError()
        return p
    except AppError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError):
        raise AppError("服务配置无效，请检查名称、地址、模型、超时和并发数。") from None


class CredentialVault:
    """Cred Manager on Windows; deliberate nonpersistent fallback for dev hosts."""
    _session = {}
    _lock = threading.RLock()
    persistent = os.name == "nt"
    storage_description = "Windows 凭据管理器" if persistent else "当前会话内存（退出后需重新填写密钥）"

    def __init__(self):
        if not self.persistent:
            return

        class CREDENTIALW(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD),
                        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD),
                        ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p),
                        ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
        self._Credential = CREDENTIALW
        self._api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        self._api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(CREDENTIALW))]
        self._api.CredReadW.restype = wintypes.BOOL
        self._api.CredWriteW.argtypes = [ctypes.POINTER(CREDENTIALW), wintypes.DWORD]
        self._api.CredWriteW.restype = wintypes.BOOL
        self._api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._api.CredDeleteW.restype = wintypes.BOOL
        self._api.CredFree.argtypes = [ctypes.c_void_p]

    @staticmethod
    def _target(identifier):
        return "PageBridge/Desktop/" + str(uuid.UUID(identifier))

    def get(self, identifier):
        target = self._target(identifier)
        with self._lock:
            if not self.persistent:
                return self._session.get(target, "")
            credential = ctypes.POINTER(self._Credential)()
            if not self._api.CredReadW(target, 1, 0, ctypes.byref(credential)):
                if ctypes.get_last_error() == 1168:
                    return ""
                raise AppError("无法读取 Windows 凭据管理器，未使用明文后备存储。")
            try:
                return ctypes.string_at(credential.contents.CredentialBlob, credential.contents.CredentialBlobSize).decode("utf-8")
            finally:
                self._api.CredFree(credential)

    def set(self, identifier, secret):
        if not isinstance(secret, str) or len(secret.encode("utf-8")) > 2560 or any(c in secret for c in "\r\n\0"):
            raise AppError("API Key 格式无效。")
        if not secret:
            return self.delete(identifier)
        target = self._target(identifier)
        with self._lock:
            if not self.persistent:
                self._session[target] = secret
                return
            raw = secret.encode("utf-8")
            blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
            credential = self._Credential(Type=1, TargetName=target, UserName="PageBridge", Persist=2,
                                          CredentialBlobSize=len(raw), CredentialBlob=blob)
            if not self._api.CredWriteW(ctypes.byref(credential), 0):
                raise AppError("无法保存到 Windows 凭据管理器，未写入明文密钥。")

    def delete(self, identifier):
        target = self._target(identifier)
        with self._lock:
            if not self.persistent:
                self._session.pop(target, None)
            elif not self._api.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != 1168:
                raise AppError("无法删除 Windows 中的旧密钥。")


class ServiceStore:
    def __init__(self, root, vault=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "desktop-services.json"
        self.vault = vault or CredentialVault()
        self._lock = threading.RLock()
        self._state = {"version": 1, "profiles": [], "active_id": None}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if data["version"] != 1 or not isinstance(data["profiles"], list):
                    raise ValueError()
                profiles = [validate_profile(p) for p in data["profiles"]]
                ids = [p["id"] for p in profiles]
                if len(ids) != len(set(ids)) or (data["active_id"] is not None and data["active_id"] not in ids):
                    raise ValueError()
                self._state = {"version": 1, "profiles": profiles, "active_id": data["active_id"]}
            except (OSError, ValueError, TypeError, KeyError):
                raise AppError("服务配置损坏或版本不兼容，未覆盖原文件。") from None

    @property
    def active_id(self):
        return self._state["active_id"]

    def profiles(self):
        with self._lock:
            return copy.deepcopy(self._state["profiles"])

    def get(self, identifier):
        for profile in self.profiles():
            if profile["id"] == identifier:
                return profile
        raise AppError("服务配置已不存在。")

    def key(self, identifier):
        self.get(identifier)
        return self.vault.get(identifier)

    def set_active(self, identifier):
        with self._lock:
            self.get(identifier)
            state = copy.deepcopy(self._state)
            state["active_id"] = identifier
            atomic_json(self.path, state)
            self._state = state

    def save(self, profile, api_key=None):
        profile = validate_profile(profile)
        with self._lock:
            previous = next((p for p in self._state["profiles"] if p["id"] == profile["id"]), None)
            old_key = self.vault.get(profile["id"])
            destination_changed = previous is not None and any(previous[k] != profile[k] for k in ("provider", "kind", "baseURL"))
            key = ("" if destination_changed else old_key) if api_key is None else api_key.strip()
            self.vault.set(profile["id"], key)
            state = copy.deepcopy(self._state)
            state["profiles"] = [p for p in state["profiles"] if p["id"] != profile["id"]] + [profile]
            state["active_id"] = state["active_id"] or profile["id"]
            try:
                atomic_json(self.path, state)
            except BaseException:
                self.vault.set(profile["id"], old_key)
                raise
            self._state = state
            return copy.deepcopy(profile)

    def delete(self, identifier):
        with self._lock:
            self.get(identifier)
            old_key = self.vault.get(identifier)
            self.vault.delete(identifier)
            state = copy.deepcopy(self._state)
            state["profiles"] = [p for p in state["profiles"] if p["id"] != identifier]
            if state["active_id"] == identifier:
                state["active_id"] = state["profiles"][0]["id"] if state["profiles"] else None
            try:
                atomic_json(self.path, state)
            except BaseException:
                self.vault.set(identifier, old_key)
                raise
            self._state = state


def worker_service(profile, api_key):
    profile = validate_profile(profile)
    return {**{key: profile[key] for key in ("kind", "provider", "baseURL", "model", "timeoutSeconds", "concurrency")}, "apiKey": api_key}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AppError("服务发生重定向，已停止请求以避免密钥发往其他地址。")


def _request(profile, api_key, endpoint, body=None):
    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "PageBridge/Windows"}
    if api_key:
        if any(c in api_key for c in "\r\n\0"):
            raise AppError("API Key 格式无效。")
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(endpoint, data=json.dumps(body).encode("utf-8") if body is not None else None, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=profile["timeoutSeconds"]) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise AppError("服务响应过大，已停止读取。")
            return json.loads(raw)
    except AppError:
        raise
    except urllib.error.HTTPError as error:
        raise AppError(f"服务返回 HTTP {error.code}。请检查地址、模型权限和额度。") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise AppError("无法连接服务或响应格式无效，请检查地址与网络。") from None


def _ollama_root(base):
    return base.rsplit("/", 1)[0] if base.rsplit("/", 1)[-1] in {"api", "v1"} else base


def list_models(profile, api_key=""):
    p = validate_profile(profile, require_model=False)
    endpoint = _ollama_root(p["baseURL"]) + "/api/tags" if p["kind"] == "ollama" else p["baseURL"] + "/models"
    if p["provider"] == "siliconFlow":
        endpoint += "?sub_type=chat"
    data = _request(p, api_key, endpoint)
    try:
        entries = data["models" if p["kind"] == "ollama" else "data"]
        if not isinstance(entries, list):
            raise ValueError()
        names = {item.get("name") if p["kind"] == "ollama" else item.get("id") for item in entries if isinstance(item, dict)}
        return sorted(n for n in names if isinstance(n, str) and 0 < len(n) <= 256)[:2000]
    except (KeyError, TypeError, ValueError):
        raise AppError("服务未返回有效的模型列表，可手动填写模型 ID。") from None


def chat_completion(profile, api_key, messages, max_tokens=1200):
    p = validate_profile(profile)
    native = p["kind"] == "ollama"
    endpoint = _ollama_root(p["baseURL"]) + "/api/chat" if native else p["baseURL"] + "/chat/completions"
    body = dict(model=p["model"], messages=messages, stream=False)
    if native:
        body.update(think=False, options={"num_predict": max_tokens, "temperature": 0})
    else:
        modern = p["provider"] == "openAI" and p["model"].startswith(("gpt-6", "gpt-5", "o"))
        body["max_completion_tokens" if modern else "max_tokens"] = max_tokens
        if p["provider"] == "deepSeek":
            body["thinking"] = {"type": "disabled"}
        if p["provider"] == "alibaba" and p["model"] in {"qwen-plus", "qwen-flash"}:
            body["enable_thinking"] = False
        if p["provider"] == "siliconFlow" and p["model"] == "deepseek-ai/DeepSeek-V4-Flash":
            body["enable_thinking"] = False
        if p["provider"] == "openAI" and p["model"].startswith("gpt-6"):
            body["reasoning_effort"] = "none"
    data = _request(p, api_key, endpoint, body)
    try:
        if native:
            if data.get("done") is not True or data.get("done_reason") == "length":
                raise ValueError()
            text = data["message"]["content"]
        else:
            choice = data["choices"][0]
            if choice.get("finish_reason") not in {"stop", None}:
                raise ValueError()
            text = choice["message"]["content"]
        if not isinstance(text, str) or not text.strip() or len(text) > 32000:
            raise ValueError()
        return text.strip()
    except (KeyError, IndexError, TypeError, ValueError):
        raise AppError("模型响应为空、被截断或格式不兼容。") from None


def probe_service(profile, api_key=""):
    return chat_completion(profile, api_key, [{"role": "user", "content": "Translate the following sentence into Simplified Chinese. Reply only with the translation.\nThe document is ready."}], max_tokens=1024)
