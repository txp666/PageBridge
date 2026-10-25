#!/usr/bin/env python3
"""Verify release archives, checksums and OTA signatures with the public key.

Use engine/.venv/bin/python, which provides the locked cryptography package.
--check-latest rejects a build which would roll back the current public feed.
--published additionally downloads every public asset and checks bytes, SHA-256
and HTTP Range support anonymously. GH_TOKEN, when supplied, is used only for
GitHub REST metadata on api.github.com, never for asset/feed downloads.
"""
import argparse
import base64
import hashlib
import http.client
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import re
import time
import urllib.error
from urllib.parse import urlparse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).resolve().parents[1]
SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"


def dual_platform(version):
    require(re.fullmatch(r"\d+\.\d+\.\d+", version) is not None, "Invalid release version")
    return tuple(map(int, version.split("."))) >= (0, 4, 0)


def archive_names(version):
    kinds = ["macOS-arm64"]
    if dual_platform(version):
        kinds.append("windows-x64")
    return [f"PageBridge-{version}-{kind}.zip" for kind in kinds + ["source", "third-party-sources"]]


def asset_names(version):
    payloads = payload_names(version)
    return payloads + [name + ".sha256" for name in payloads] + ["appcast.xml"]


def payload_names(version):
    names = archive_names(version)
    if dual_platform(version):
        names.append(f"PageBridge-{version}-windows-x64-setup.exe")
    return names


def helper(filename):
    spec = importlib.util.spec_from_file_location(filename, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_zip_members(zipped):
    names = zipped.namelist()
    require(len(names) == len(set(names)), "Duplicate ZIP member")
    require(all(not name.startswith(("/", "\\")) and "\\" not in name
                and not re.match(r"^[A-Za-z]:", name) and ".." not in Path(name).parts
                for name in names), "Unsafe ZIP member")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def network_attempt(operation, *, retry_missing=False):
    """Retry transient transport/CDN errors, never integrity failures."""
    for attempt in range(4):
        try:
            return operation()
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as error:
            if isinstance(error, urllib.error.HTTPError):
                retry = error.code == 429 or 500 <= error.code <= 599 or (retry_missing and error.code == 404)
            else:
                retry = True
            if not retry or attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch(url, *, limit=4 * 1024 * 1024, headers=None, retry_missing=False):
    def operation():
        request = urllib.request.Request(url, headers={"User-Agent": "PageBridge-release-verifier/1",
                                                       "Cache-Control": "no-cache", **(headers or {})})
        with urllib.request.urlopen(request, timeout=120) as response:
            require(response.geturl().startswith("https://"), "Insecure release redirect")
            data = response.read(limit + 1)
            require(len(data) <= limit, "Release metadata exceeds limit")
            return data
    return network_attempt(operation, retry_missing=retry_missing)


def is_github_api_url(url):
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc == "api.github.com" and not parsed.fragment


class GitHubAPIRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        # Never allow an Authorization header to follow an asset/CDN redirect.
        require(is_github_api_url(newurl), "GitHub metadata redirect left api.github.com")
        return super().redirect_request(request, response, code, message, headers, newurl)


def fetch_github_api(url, *, limit=4 * 1024 * 1024, retry_missing=False):
    require(is_github_api_url(url), "GitHub metadata URL must use https://api.github.com")
    headers = {"User-Agent": "PageBridge-release-verifier/1", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "Cache-Control": "no-cache"}
    token = os.environ.get("GH_TOKEN", "").strip()
    if token:
        require(not any(character.isspace() for character in token), "Invalid GitHub metadata token")
        headers["Authorization"] = "Bearer " + token
    opener = urllib.request.build_opener(GitHubAPIRedirectHandler())
    def operation():
        request = urllib.request.Request(url, headers=headers)
        with opener.open(request, timeout=120) as response:
            require(is_github_api_url(response.geturl()), "GitHub metadata response left api.github.com")
            data = response.read(limit + 1)
            require(len(data) <= limit, "GitHub metadata exceeds limit")
            return data
    return network_attempt(operation, retry_missing=retry_missing)


def verify_public_asset(url, expected):
    def download():
        request = urllib.request.Request(url, headers={"User-Agent": "PageBridge-release-verifier/1", "Cache-Control": "no-cache"})
        checksum, size = hashlib.sha256(), 0
        with urllib.request.urlopen(request, timeout=120) as response:
            require(response.geturl().startswith("https://"), "Insecure asset redirect")
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                require(size <= expected["bytes"], "Public asset exceeds expected size")
                checksum.update(chunk)
        require(size == expected["bytes"] and checksum.hexdigest() == expected["sha256"], "Public asset checksum differs")
    network_attempt(download, retry_missing=True)
    if expected["name"].endswith((".zip", ".exe")):
        def ranged_download():
            request = urllib.request.Request(url, headers={"Range": "bytes=0-31", "User-Agent": "PageBridge-release-verifier/1",
                                                           "Cache-Control": "no-cache"})
            with urllib.request.urlopen(request, timeout=120) as response:
                require(response.geturl().startswith("https://"), "Insecure ranged redirect")
                require(response.status == 206 and response.headers.get("Content-Range") == f"bytes 0-31/{expected['bytes']}",
                        "Public download lacks HTTP Range support")
                require(len(response.read(33)) == 32, "Invalid ranged response body")
        network_attempt(ranged_download, retry_missing=True)
        expected["httpRangeVerified"] = True
    expected["publicSHA256Verified"] = True


def verify_latest_feed(url, expected, key):
    for attempt in range(4):
        data = fetch(url, retry_missing=True)
        # A stale, correctly signed previous feed may be visible at the latest
        # redirect briefly. A bad signature is never excused as CDN propagation.
        signed_feed(data, key)
        if data == expected:
            return
        if attempt < 3:
            time.sleep(2 ** attempt)
    raise ValueError("Latest feed differs from release feed after bounded CDN retries")


def signed_feed(data, key):
    # Sparkle 2.10 SPUExtractSignedFeed.m: all bytes before the final signature
    # comment are signed, including the signing-warning comment and newlines.
    prefix = b"<!-- sparkle-signatures:\n"
    offset = data.rfind(prefix)
    require(offset > 0, "Appcast lacks its required inline signature")
    content = data[:offset]
    block = data[offset + len(prefix):]
    match = re.fullmatch(rb"edSignature:\s*([A-Za-z0-9+/=]+)\s*\nlength:\s*(\d+)\s*\n-->\s*", block)
    require(match is not None, "Malformed appcast signature block")
    require(len(content) == int(match[2]), "Appcast signed length differs")
    key.verify(base64.b64decode(match[1], validate=True), content)
    feed = ET.fromstring(content)
    require(feed.tag == "rss", "Unexpected appcast document")
    return feed


def current_notes(version, source_root=ROOT):
    contents = (source_root / "CHANGELOG.md").read_text()
    match = re.search(r"^## " + re.escape(version) + r"(?: /[^\n]*)?[^\n]*\n(.*?)(?=^## |\Z)",
                      contents, re.M | re.S)
    require(match is not None, "Missing release notes")
    return match.group(1).strip() + "\n"


def verify_source(path, version, source_root=ROOT):
    with zipfile.ZipFile(path) as zipped:
        verify_zip_members(zipped)
        prefix = f"PageBridge-{version}-source/"
        manifest = json.loads(zipped.read(prefix + "SOURCE_MANIFEST.json"))
        require(manifest["version"] == version, "Source manifest version differs")
        require(set(zipped.namelist()) == {prefix + name for name in manifest["files"]} | {prefix + "SOURCE_MANIFEST.json"},
                "Source archive inventory differs from manifest")
        for name, entry in manifest["files"].items():
            data = zipped.read(prefix + name)
            require(hashlib.sha256(data).hexdigest() == entry["sha256"] and len(data) == entry["bytes"],
                    "Source file differs from manifest: " + name)
        for name in ("VERSION.json", "update-config.json", ".github/workflows/release.yml"):
            require(zipped.read(prefix + name) == (source_root / name).read_bytes(), "Source release configuration differs: " + name)
        if dual_platform(version):
            required = {"LICENSE", "engine/uv.lock", "engine/pyproject.toml", "engine/.python-version",
                        "engine/setup.sh", "engine/setup-windows.ps1", "engine/worker.py",
                        "macOS/Package.swift", "macOS/Package.resolved", "desktop/bridge.py",
                        "windows/PageBridge.Windows/PageBridge.Windows.csproj",
                        "windows/PageBridge.Windows/packages.lock.json", "scripts/build-windows.ps1",
                        "scripts/windows-installer.iss", "scripts/setup-inno.ps1"}
            require(required.issubset(manifest["files"]), "Corresponding source is missing a platform or dependency lock")
            release = json.loads((source_root / "VERSION.json").read_text())
            require(str(manifest.get("build")) == str(release["build"]), "Source manifest build differs")
            # The manifest alone is self-reported: compare its complete inventory
            # and every byte against the immutable tag checkout used by CI.
            files = helper("package-release.py").source_files(source_root)
            require(set(manifest["files"]) == {name for name, _ in files}, "Source archive omits or adds project files")
            for name, source in files:
                require(zipped.read(prefix + name) == source.read_bytes(), "Source differs from tag checkout: " + name)
    return len(manifest["files"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--source-root", type=Path, default=ROOT, help="Checked-out release tag source (defaults to this repository)")
    parser.add_argument("--check-latest", action="store_true")
    parser.add_argument("--published", action="store_true")
    parser.add_argument("--require-latest", action="store_true", help="Require this published tag to be the latest release (publication CI)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--notes-output", type=Path, help="Write current changelog section for gh release --notes-file")
    args = parser.parse_args()
    require(not args.require_latest or args.published, "--require-latest requires --published")
    source_root = args.source_root.resolve()
    release = json.loads((source_root / "VERSION.json").read_text())
    config = json.loads((source_root / "update-config.json").read_text())
    version, build = release["version"], str(release["build"])
    require(re.fullmatch(r"\d+\.\d+\.\d+", version) and build.isdecimal(), "Invalid release version/build")
    repository = config["repository"]
    base = "https://github.com/" + repository
    api = "https://api.github.com/repos/" + repository
    require(config["feedURL"] == base + "/releases/latest/download/appcast.xml", "Unexpected configured feed")
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(config["publicEDKey"], validate=True))
    report = {"version": version, "build": build, "repository": repository, "assets": []}
    filenames = payload_names(version)
    for name in filenames:
        path = args.dist / name
        checksum = digest(path)
        sidecar = path.with_name(name + ".sha256")
        require(sidecar.read_text() == checksum + "  " + name + "\n", "SHA-256 sidecar differs: " + name)
        if name.endswith(".zip"):
            with zipfile.ZipFile(path) as zipped:
                require(zipped.testzip() is None, "ZIP CRC failure: " + name)
                verify_zip_members(zipped)
        for asset in (path, sidecar):
            report["assets"].append({"name": asset.name, "bytes": asset.stat().st_size, "sha256": digest(asset)})
    app = args.dist / filenames[0]
    with zipfile.ZipFile(app) as zipped:
        info = plistlib.loads(zipped.read("PageBridge.app/Contents/Info.plist"))
        require(info["CFBundleShortVersionString"] == version and info["CFBundleVersion"] == build, "App version differs")
        require(info.get("SUPublicEDKey") == config["publicEDKey"] and info.get("SUFeedURL") == config["feedURL"], "App OTA configuration differs")
        require(info.get("SURequireSignedFeed") is True and info.get("SUVerifyUpdateBeforeExtraction") is True,
                "App does not enforce feed/archive verification")
    if dual_platform(version):
        report["windows"] = helper("windows_package.py").verify_archive(
            args.dist / f"PageBridge-{version}-windows-x64.zip", source_root)
        report["windowsInstaller"] = helper("windows_package.py").verify_installer(
            args.dist / f"PageBridge-{version}-windows-x64-setup.exe", source_root)
    report["sourceFileCount"] = verify_source(args.dist / f"PageBridge-{version}-source.zip", version, source_root)
    with zipfile.ZipFile(args.dist / f"PageBridge-{version}-third-party-sources.zip") as zipped:
        prefix = f"PageBridge-{version}-third-party-sources/"
        manifest = json.loads(zipped.read(prefix + "manifest.json"))
        require(manifest["release"] == version, "Third-party source release differs")
        expected = json.loads((source_root / "scripts/third-party-sources.json").read_text())["packages"]
        require(manifest["packages"] == expected, "Third-party source pins differ")
        for package in expected:
            with zipped.open(prefix + package["filename"]) as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
            require(checksum == package["sha256"], "Third-party source hash differs")
        require(zipped.read(prefix + "engine-uv.lock") == (source_root / "engine/uv.lock").read_bytes(), "Third-party lock differs")
    feed_path = args.dist / "appcast.xml"
    feed = signed_feed(feed_path.read_bytes(), key)
    items = feed.findall("./channel/item")
    require(len(items) == 1, "Expected exactly one current appcast item")
    item = items[0]
    enclosure = item.find("enclosure")
    require(enclosure is not None, "Appcast has no archive enclosure")
    require(item.findtext(SPARKLE + "version") == build, "Appcast build differs")
    require(item.findtext(SPARKLE + "shortVersionString") == version, "Appcast version differs")
    require(enclosure.attrib["url"] == base + f"/releases/download/v{version}/" + app.name, "Appcast download URL differs")
    require(int(enclosure.attrib["length"]) == app.stat().st_size, "Appcast archive length differs")
    require(item.findtext(SPARKLE + "minimumSystemVersion") == info["LSMinimumSystemVersion"], "Appcast minimum macOS differs")
    key.verify(base64.b64decode(enclosure.attrib[SPARKLE + "edSignature"], validate=True), app.read_bytes())
    report.update(feedSignatureVerified=True, archiveSignatureVerified=True)
    report["assets"].append({"name": "appcast.xml", "bytes": feed_path.stat().st_size, "sha256": digest(feed_path)})
    if args.check_latest:
        try:
            latest = json.loads(fetch_github_api(api + "/releases/latest"))
        except urllib.error.HTTPError as error:
            if error.code != 404:
                raise
            report["previousRelease"] = None
        else:
            old_feed = signed_feed(fetch(config["feedURL"]), key)
            builds = [entry.findtext(SPARKLE + "version", "") for entry in old_feed.findall("./channel/item")]
            require(builds and all(value.isdecimal() for value in builds), "Previous feed has invalid build numbers")
            require(int(build) > max(map(int, builds)), "New build must exceed every build in the current public feed")
            report["previousRelease"] = latest["tag_name"]
    if args.published:
        public = json.loads(fetch_github_api(api + f"/releases/tags/v{version}", retry_missing=True))
        require(not public["draft"] and not public["prerelease"], "Release is not publicly stable")
        require(public["tag_name"] == "v" + version, "Public release tag differs")
        assets = {asset["name"]: asset for asset in public["assets"]}
        require(set(assets) == {asset["name"] for asset in report["assets"]}, "Public release assets differ")
        for expected in report["assets"]:
            asset = assets[expected["name"]]
            require(asset["size"] == expected["bytes"] and asset["state"] == "uploaded", "Public asset size/state differs")
            require(asset.get("digest") == "sha256:" + expected["sha256"], "Public asset API checksum differs")
            url = base + f"/releases/download/v{version}/" + expected["name"]
            verify_public_asset(url, expected)
        latest = json.loads(fetch_github_api(api + "/releases/latest", retry_missing=True))
        if args.require_latest or latest["tag_name"] == "v" + version:
            require(latest["tag_name"] == "v" + version, "Published tag is not the latest release")
            verify_latest_feed(config["feedURL"], feed_path.read_bytes(), key)
        else:
            # Historical releases remain independently verifiable after an OTA
            # upgrade. Still authenticate the current feed and reject rollback.
            current_feed = signed_feed(fetch(config["feedURL"], retry_missing=True), key)
            current_builds = [entry.findtext(SPARKLE + "version", "") for entry in current_feed.findall("./channel/item")]
            require(current_builds and all(number.isdecimal() for number in current_builds)
                    and min(map(int, current_builds)) >= int(build), "Latest feed rolled back behind verified release")
        report["latestRelease"] = latest["tag_name"]
        report["publishedURL"] = public["html_url"]
    if args.notes_output:
        args.notes_output.parent.mkdir(parents=True, exist_ok=True)
        args.notes_output.write_text(current_notes(version, source_root))
    output = json.dumps(report, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output)
    print(output)


if __name__ == "__main__":
    main()
