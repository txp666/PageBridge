#!/usr/bin/env python3
"""Fetch hash-pinned AGPL sources and create the versioned source-material ZIP.

Downloaded code is never executed or extracted onto the filesystem. The three
PyPI pins must agree with uv.lock; MuPDF is pinned to the reviewed official
archive because PyMuPDF's sdist does not include the MuPDF sources.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
import tomllib
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def fetch(package, directory):
    filename = package["filename"]
    require(Path(filename).name == filename, "Unsafe source filename")
    path = directory / filename
    if path.exists():
        require(path.is_file() and not path.is_symlink(), "Source cache must be a regular file")
        require(path.stat().st_size == package["bytes"] and digest(path) == package["sha256"],
                "Cached source differs from pin: " + filename)
        return path
    require(package["url"].startswith("https://"), "Source downloads require HTTPS")
    request = urllib.request.Request(package["url"], headers={"User-Agent": "PageBridge-release/1"})
    with tempfile.NamedTemporaryFile(dir=directory, prefix=".download-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                require(response.geturl().startswith("https://"), "Insecure source redirect")
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    require(size <= package["bytes"], "Source download exceeds pinned size")
                    stream.write(chunk)
            stream.flush()
            require(size == package["bytes"] and digest(temporary) == package["sha256"],
                    "Downloaded source differs from pin: " + filename)
            os.link(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    release = json.loads((ROOT / "VERSION.json").read_text())
    version = release["version"]
    require(re.fullmatch(r"\d+\.\d+\.\d+", version), "Invalid release version")
    config = json.loads((ROOT / "scripts/third-party-sources.json").read_text())
    locked = {item["name"]: item for item in tomllib.loads((ROOT / "engine/uv.lock").read_text())["package"]}
    args.dist.mkdir(parents=True, exist_ok=True)
    archive = args.dist / f"PageBridge-{version}-third-party-sources.zip"
    sidecar = archive.with_name(archive.name + ".sha256")
    require(not archive.exists() and not sidecar.exists(), "Refusing to overwrite existing release archive/checksum")
    directory = args.dist / f"third-party-sources-{version}"
    directory.mkdir(exist_ok=True)
    materials = {}
    for package in config["packages"]:
        if package["name"] != "MuPDF":
            lock = locked[package["name"]]
            require(lock["version"] == package["version"], "Source version differs from uv.lock")
            require(all(lock["sdist"][key] == value for key, value in
                        {"url": package["url"], "hash": "sha256:" + package["sha256"],
                         "size": package["bytes"]}.items()), "Source pin differs from uv.lock")
        path = fetch(package, directory)
        materials[package["filename"]] = path
        with tarfile.open(path, "r:gz") as tar:
            member = tar.getmember(package["license_file"])
            require(member.isfile() and member.size <= 1024 * 1024, "Invalid license member")
            license_data = tar.extractfile(member).read()
            require(hashlib.sha256(license_data).hexdigest() == package["license_sha256"], "License checksum differs")
            for name in package["build_materials"]:
                require(tar.getmember(name).isfile(), "Missing upstream build material: " + name)
        materials[package["name"] + "-LICENSE.txt"] = license_data
        print("Verified source: " + package["name"] + " " + package["version"])
    manifest = {"release": version, "downloaded_code_executed": False,
                "packages": config["packages"], "validation_notes": config["notes"]}
    materials["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode()
    materials["engine-uv.lock"] = ROOT / "engine/uv.lock"
    materials["README.txt"] = (
        f"PageBridge {version} - selected AGPL upstream source materials\n\n"
        "Contains the exact pinned pdf2zh-next, BabelDOC, PyMuPDF and MuPDF sources,\n"
        "their licenses and engine/uv.lock. PageBridge's own source, engine guards,\n"
        "build scripts and installation instructions are in the matching source ZIP.\n"
        "Consult each upstream archive's build instructions. MuPDF is supplied\n"
        "separately because PyMuPDF's source distribution does not embed it.\n"
        "No downloaded source was executed during packaging. This selection is\n"
        "not an exhaustive license audit or a bit-for-bit reproducibility claim.\n"
    ).encode()
    with tempfile.TemporaryDirectory(dir=args.dist, prefix=".sources-") as staging:
        temporary = Path(staging) / archive.name
        prefix = f"PageBridge-{version}-third-party-sources/"
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as output:
            for name, value in sorted(materials.items()):
                info = zipfile.ZipInfo(prefix + name, date_time=(2020, 1, 1, 0, 0, 0))
                info.external_attr = 0o100644 << 16
                with output.open(info, "w", force_zip64=True) as stream:
                    if isinstance(value, Path):
                        with value.open("rb") as source:
                            shutil.copyfileobj(source, stream)
                    else:
                        stream.write(value)
        with zipfile.ZipFile(temporary) as output:
            require(output.testzip() is None, "Source ZIP CRC failure")
        checksum = digest(temporary)
        os.link(temporary, archive)
        with sidecar.open("x") as stream:
            stream.write(checksum + "  " + archive.name + "\n")
    print(json.dumps({"file": str(archive), "bytes": archive.stat().st_size, "sha256": checksum}))


if __name__ == "__main__":
    main()
