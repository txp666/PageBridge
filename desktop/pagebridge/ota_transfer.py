"""Stream a signed-size OTA archive in order using up to four HTTP ranges."""
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPException
from queue import SimpleQueue
import sys
from threading import Event
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

LANES = 4
SEGMENT_BYTES = 2 * 1024 * 1024
READ_BYTES = 128 * 1024


class UpdateError(ValueError):
    pass


def allowed_url(url):
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in {
            "pagebridge.modusignal.cn", "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com"
        } and parts.port in (None, 443) and not parts.username and not parts.password and not parts.fragment)
    except ValueError:
        return False


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise UpdateError("更新服务重定向到了不受信任的地址。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_response(url, byte_range=None):
    if not allowed_url(url):
        raise UpdateError("更新地址无效。")
    platform = "Mac" if sys.platform == "darwin" else "Windows"
    headers = {"User-Agent": f"PageBridge-{platform}-Updater/1", "Accept-Encoding": "identity"}
    if byte_range is None:
        headers["Cache-Control"] = "no-cache"
    else:
        start, end = byte_range
        headers["Range"] = f"bytes={start}-{end}"
    response = build_opener(SafeRedirect()).open(Request(url, headers=headers), timeout=15)
    if (not allowed_url(response.geturl()) or response.status not in ((200, 206) if byte_range else (200,))
            or response.headers.get("Content-Encoding", "identity").lower() != "identity"):
        response.close()
        raise UpdateError("更新服务返回了不支持的响应。")
    return response


def _check_length(response, expected):
    length = response.headers.get("Content-Length")
    if length is not None and length != str(expected):
        raise UpdateError("安装包大小与签名信息不一致。")


def download(url, size, write, *, opener=open_response, checkpoint=lambda: None):
    """Only the caller writes ordered bytes. Keep at most two windows in memory."""
    segment = min(SEGMENT_BYTES, (size + LANES - 1) // LANES)
    stopped = Event()

    def read_segment(start, first, blocks):
        end = min(start + segment, size) - 1
        offset = start
        try:
            for attempt in range(3):
                checkpoint()
                if stopped.is_set():
                    return
                try:
                    response = first if first is not None and attempt == 0 else opener(url, (offset, end))
                    with response as part:
                        if part.status != 206 or part.headers.get("Content-Range") != f"bytes {offset}-{end}/{size}":
                            raise UpdateError("更新服务返回的分段与请求不一致。")
                        _check_length(part, end - offset + 1)
                        while offset <= end:
                            checkpoint()
                            if stopped.is_set():
                                return
                            block = part.read(min(READ_BYTES, end - offset + 1))
                            if not block:
                                raise EOFError("Incomplete update range")
                            offset += len(block)
                            if offset > end and part.read(1):
                                raise UpdateError("安装包超出签名指定的大小。")
                            blocks.put(block)
                        return
                except (OSError, HTTPException, EOFError) as error:
                    if isinstance(error, HTTPError):
                        error.close()
                    if attempt == 2 or offset > end or isinstance(error, HTTPError) and error.code < 500:
                        raise
        finally:
            blocks.put(None)

    for attempt in range(3):
        checkpoint()
        try:
            first = opener(url, (0, segment - 1))
            break
        except HTTPError as error:
            error.close()
            if attempt == 2 or error.code < 500:
                raise
        except (OSError, HTTPException):
            if attempt == 2:
                raise
    with first:
        if first.status == 200:
            _check_length(first, size)
            received = 0
            while True:
                checkpoint()
                block = first.read(min(READ_BYTES, size - received + 1))
                checkpoint()
                if not block:
                    break
                received += len(block)
                if received > size:
                    raise UpdateError("安装包超出签名指定的大小。")
                write(block)
            if received != size:
                raise UpdateError("安装包大小与签名信息不一致。")
            return

        pool = ThreadPoolExecutor(max_workers=LANES, thread_name_prefix="PageBridge-range")
        pending = deque()

        def write_next():
            blocks, task = pending.popleft()
            while (block := blocks.get()) is not None:
                checkpoint()
                write(block)
            task.result()

        try:
            for start in range(0, size, segment):
                blocks = SimpleQueue()
                task = pool.submit(read_segment, start, first if start == 0 else None, blocks)
                pending.append((blocks, task))
                if len(pending) == 2 * LANES:
                    write_next()
            while pending:
                write_next()
        finally:
            stopped.set()
            pool.shutdown(wait=True, cancel_futures=True)
