"""Signed Windows updates. This module never launches a downloaded program.

The public key and installed identity come from the application bundle. Network
responses cannot override them. Long network operations run outside reader RPCs.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

REPOSITORY = "txp666/PageBridge"
FEED_URL = f"https://github.com/{REPOSITORY}/releases/latest/download/windows-update.json"
DOMAIN = b"PageBridge.Windows.Update.v1\n"
MAX_MANIFEST_BYTES = 32768
MAX_INSTALLER_BYTES = 2 * 1024**3
ACTIONS = frozenset({"update_check", "update_download", "update_status", "update_cancel", "update_prepare_install"})
FIELDS = {"schema", "platform", "version", "build", "minWindowsBuild", "url", "sha256", "bytes", "releaseNotesURL"}
CACHE_FILE = re.compile(r"\.download-.+\.part|PageBridge-.+-setup\.exe")


class UpdateError(ValueError):
    pass


class Cancelled(Exception):
    pass


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise UpdateError("更新信息包含重复字段。")
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(UpdateError("更新信息数值无效。")))
    except UpdateError:
        raise
    except (ValueError, RecursionError) as error:
        # JSONDecodeError, UnicodeError and integer literals beyond Python's
        # digit limit are all ValueErrors.
        raise UpdateError("更新信息格式无效。") from error


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})", value):
        raise UpdateError("更新版本号无效。")
    parts = tuple(map(int, value.split(".")))
    if any(part > 65535 for part in parts):
        raise UpdateError("更新版本号超出范围。")
    return parts


def signature_message(payload):
    return DOMAIN + json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")


def verify_manifest(raw: bytes, public_key: str) -> dict:
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_MANIFEST_BYTES:
        raise UpdateError("更新信息大小无效。")
    envelope = _json(raw)
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "signature"}:
        raise UpdateError("更新信息字段无效。")
    payload = envelope["payload"]
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise UpdateError("更新内容字段无效。")
    if type(payload["schema"]) is not int or payload["schema"] != 1 or payload["platform"] != "windows-x64":
        raise UpdateError("更新信息不适用于当前平台。")
    version_tuple(payload["version"])
    for name, minimum, maximum in (("build", 1, 65535), ("minWindowsBuild", 19041, 999999), ("bytes", 1, MAX_INSTALLER_BYTES)):
        if type(payload[name]) is not int or not minimum <= payload[name] <= maximum:
            raise UpdateError("更新信息数值无效。")
    if not isinstance(payload["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", payload["sha256"]):
        raise UpdateError("安装包校验信息无效。")
    version = payload["version"]
    base = f"https://github.com/{REPOSITORY}/releases"
    if payload["url"] != f"{base}/download/v{version}/PageBridge-{version}-windows-x64-setup.exe" or payload["releaseNotesURL"] != f"{base}/tag/v{version}":
        raise UpdateError("更新下载地址不属于此应用的正式发布。")
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        key = base64.b64decode(public_key, validate=True)
        signature = base64.b64decode(envelope["signature"], validate=True)
        if len(key) != 32 or len(signature) != 64:
            raise ValueError("length")
        Ed25519PublicKey.from_public_bytes(key).verify(signature, signature_message(payload))
    except Exception as error:
        raise UpdateError("更新信息签名验证失败，已阻止下载。") from error
    return payload


def allowed_url(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"
        } and parts.port in (None, 443) and not parts.username and not parts.password and not parts.fragment)
    except ValueError:
        return False


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise UpdateError("更新服务重定向到了不受信任的地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_response(url):
    if not allowed_url(url):
        raise UpdateError("更新地址无效。")
    response = build_opener(SafeRedirect()).open(Request(url, headers={
        "User-Agent": "PageBridge-Windows-Updater/1", "Accept-Encoding": "identity",
        "Cache-Control": "no-cache",
    }), timeout=15)
    if not allowed_url(response.geturl()) or response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
        response.close()
        raise UpdateError("更新服务返回了不支持的响应。")
    return response


class UpdateManager:
    def __init__(self, root, resources=None, *, opener=open_response, windows_build=None):
        self.root = Path(root)
        self.resources = Path(resources) if resources else Path(__file__).resolve().parents[2]
        config = _json((self.resources / "update-config.json").read_bytes())
        installed = _json((self.resources / "VERSION.json").read_bytes())
        if config.get("repository") != REPOSITORY:
            raise UpdateError("应用更新配置无效，请重新安装正式版本。")
        self.public_key = config["publicEDKey"]
        self.current_version = installed["version"]
        version_tuple(self.current_version)
        self.current_build = int(installed["build"])
        if not 1 <= self.current_build <= 65535:
            raise UpdateError("应用版本信息无效。")
        self.windows_build = windows_build if windows_build is not None else (sys.getwindowsversion().build if sys.platform == "win32" else 0)
        self.opener = opener
        self.lock = threading.RLock()
        self.cancelled = threading.Event()
        self.thread = None
        self.raw = None
        self.target = None
        self.value = dict(state="idle", currentVersion=self.current_version, currentBuild=self.current_build,
                          update=None, received=0, total=0, error=None)

    def status(self):
        with self.lock:
            return copy.deepcopy(self.value)

    def _set(self, **values):
        with self.lock:
            self.value.update(values)

    def _ensure_idle(self):
        if self.thread is not None and self.thread.is_alive():
            raise UpdateError("更新操作正在进行，请稍候。")

    def _start(self, state, operation):
        with self.lock:
            self._ensure_idle()
            self.cancelled = threading.Event()
            self.value.update(state=state, error=None, received=0)
            def run():
                try:
                    operation()
                except Cancelled:
                    self._set(state="cancelled", error=None)
                except Exception as error:
                    message = str(error) if isinstance(error, UpdateError) else "无法连接更新服务，请检查网络后重试。"
                    if isinstance(error, HTTPError) and error.code == 404:
                        message = "尚未找到 Windows 更新信息，请稍后重试。"
                    self._set(state="cancelled" if self.cancelled.is_set() else "error", error=None if self.cancelled.is_set() else message)
            self.thread = threading.Thread(target=run, name="PageBridge-update", daemon=True)
            self.thread.start()
            return self.status()

    def _checkpoint(self, deadline):
        if self.cancelled.is_set():
            raise Cancelled()
        if time.monotonic() > deadline:
            raise UpdateError("更新操作超时，请重试。")

    def check(self):
        def run():
            self._set(update=None, total=0)
            self.raw = None
            self.target = None
            prune_cache(self.root)
            deadline = time.monotonic() + 45
            with self.opener(FEED_URL) as response:
                chunks, count = [], 0
                while True:
                    self._checkpoint(deadline)
                    chunk = response.read(min(4096, MAX_MANIFEST_BYTES + 1 - count))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    count += len(chunk)
                    if count > MAX_MANIFEST_BYTES:
                        raise UpdateError("更新信息过大。")
                raw = b"".join(chunks)
            self._checkpoint(deadline)
            payload = verify_manifest(raw, self.public_key)
            newest, current = version_tuple(payload["version"]), version_tuple(self.current_version)
            build = payload["build"]
            if build > self.current_build and newest < current:
                raise UpdateError("更新信息版本低于当前应用，已阻止降级。")
            if build <= self.current_build and (newest > current or (build == self.current_build and newest != current)):
                raise UpdateError("更新版本与构建号不一致。")
            with self.lock:
                self._checkpoint(deadline)
                if build <= self.current_build:
                    # A feed behind this build (CDN lag right after a release, or
                    # a newer local build) offers nothing; it is not an error.
                    self._set(state="current", update=payload if build == self.current_build else None)
                else:
                    self.raw = raw
                    self._set(state="unsupported" if self.windows_build < payload["minWindowsBuild"] else "available",
                              update=payload, total=payload["bytes"])
        return self._start("checking", run)

    def _cache(self):
        self.root.mkdir(parents=True, exist_ok=True)
        cache = self.root / "updates"
        if cache.is_symlink():
            raise UpdateError("更新缓存目录无效。")
        cache.mkdir(exist_ok=True)
        if cache.resolve().parent != self.root.resolve():
            raise UpdateError("更新缓存目录无效。")
        return cache

    def download(self):
        with self.lock:
            self._ensure_idle()
            if self.value["state"] not in {"available", "cancelled", "error", "ready"} or self.raw is None:
                raise UpdateError("请先检查可用更新。")
            payload = verify_manifest(self.raw, self.public_key)
            if self.windows_build < payload["minWindowsBuild"]:
                raise UpdateError("此更新需要较新版本的 Windows。")
        def run():
            cache = self._cache()
            self.target = None
            prune_cache(self.root)
            fd, name = tempfile.mkstemp(prefix=".download-", suffix=".part", dir=cache)
            temporary = Path(name)
            deadline = time.monotonic() + 3600
            digest, received = hashlib.sha256(), 0
            try:
                with os.fdopen(fd, "wb") as stream, self.opener(payload["url"]) as response:
                    content_length = response.headers.get("Content-Length")
                    if content_length is not None and content_length != str(payload["bytes"]):
                        raise UpdateError("安装包大小与签名信息不一致。")
                    while True:
                        self._checkpoint(deadline)
                        block = response.read(256 * 1024)
                        self._checkpoint(deadline)
                        if not block:
                            break
                        received += len(block)
                        if received > payload["bytes"]:
                            raise UpdateError("安装包超出签名指定的大小。")
                        digest.update(block)
                        stream.write(block)
                        self._set(received=received)
                    stream.flush()
                    os.fsync(stream.fileno())
                if received != payload["bytes"] or digest.hexdigest() != payload["sha256"]:
                    raise UpdateError("安装包校验失败，已删除下载内容。")
                # Cancellation and committing a verified EXE are one ordered
                # transition. A cancel acknowledged before this lock wins;
                # after committing, cancel returns the already-ready state.
                with self.lock:
                    self._checkpoint(deadline)
                    target = cache / f"PageBridge-{payload['version']}-{payload['sha256'][:16]}-setup.exe"
                    if target.is_symlink():
                        raise UpdateError("更新缓存文件无效。")
                    os.replace(temporary, target)
                    self.target = target
                    self._set(state="ready")
            finally:
                temporary.unlink(missing_ok=True)
        return self._start("downloading", run)

    def cancel(self):
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                self.cancelled.set()
            return self.status()

    def prepare_install(self):
        with self.lock:
            self._ensure_idle()
            if self.value["state"] != "ready" or self.raw is None or self.target is None:
                raise UpdateError("请先下载并验证安装包。")
            try:
                return self._prepared_file()
            except (OSError, UpdateError) as error:
                self.target = None
                prune_cache(self.root)
                self._set(state="error", error="安装包不可用或已发生变化，请重新下载。")
                raise UpdateError(self.value["error"]) from error

    def _prepared_file(self):
        payload = verify_manifest(self.raw, self.public_key)
        target = self.target
        if target.is_symlink() or target.resolve().parent != self._cache().resolve():
            raise UpdateError("安装包路径无效。")
        digest, size = hashlib.sha256(), 0
        with target.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                size += len(block)
                if size > payload["bytes"]:
                    break
                digest.update(block)
        if size != payload["bytes"] or digest.hexdigest() != payload["sha256"]:
            self._set(state="error", error="安装包已发生变化，请重新下载。")
            raise UpdateError("安装包已发生变化，请重新下载。")
        return dict(path=str(target.resolve()), sha256=payload["sha256"], bytes=size,
                    version=payload["version"], build=payload["build"])


def prune_cache(root):
    """Delete partial downloads and installers that no manager references.

    A killed reader process skips its download cleanup, and an installer stays
    after the update it delivered. Windows refuses to delete a file that is
    still open or running, such as an installer that has not exited yet.
    """
    root = Path(root)
    cache = root / "updates"
    try:
        if cache.is_symlink() or not cache.is_dir() or cache.resolve().parent != root.resolve():
            return
        entries = list(cache.iterdir())
    except OSError:
        return
    for path in entries:
        if CACHE_FILE.fullmatch(path.name):
            try:
                path.unlink()
            except OSError:
                pass


_MANAGERS = {}


def recover(root):
    # Runs when the reader starts. A live manager owns its cache; otherwise
    # every cached file was left behind by an earlier process.
    if str(Path(root).resolve()) not in _MANAGERS:
        prune_cache(root)


def dispatch(root, action):
    key = str(Path(root).resolve())
    manager = _MANAGERS.get(key)
    if manager is None:
        manager = _MANAGERS[key] = UpdateManager(root)
    return getattr(manager, {"update_check": "check", "update_download": "download", "update_status": "status",
                             "update_cancel": "cancel", "update_prepare_install": "prepare_install"}[action])()
