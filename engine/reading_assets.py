"""Page-sized PNGs referenced by the reading draft's content hashes."""

import base64
import hashlib
from pathlib import Path
import re

MAX_PNG_BYTES = 16 * 1024 * 1024


def background_path(reading_path, page_number):
    return Path(reading_path).with_name(f"background-{page_number}.png")


def valid_background_digest(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def valid_png_header(data):
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return False
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return 0 < width <= 8000 and 0 < height <= 8000 and width * height <= 16_000_000


def read_background_file(path, digest):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Invalid reading background file")
    with path.open("rb") as stream:
        data = stream.read(MAX_PNG_BYTES + 1)
    if (len(data) > MAX_PNG_BYTES or not valid_png_header(data)
            or hashlib.sha256(data).hexdigest() != digest):
        raise ValueError("Invalid reading background data")
    return data


def read_background(reading_path, page):
    if digest := page.get("backgroundSHA256"):
        return read_background_file(background_path(reading_path, page["pageNumber"]), digest)
    encoded = page.get("backgroundImage")
    if encoded is not None:
        if not isinstance(encoded, str) or not 44 <= len(encoded) <= MAX_PNG_BYTES:
            raise ValueError("Invalid embedded background")
        data = base64.b64decode(encoded, validate=True)
        if not valid_png_header(data):
            raise ValueError("Invalid embedded background data")
        return data
    return None


def write_background(reading_path, page, data):
    background_path(reading_path, page["pageNumber"]).write_bytes(data)
    page["backgroundSHA256"] = hashlib.sha256(data).hexdigest()
    page.pop("backgroundImage", None)
