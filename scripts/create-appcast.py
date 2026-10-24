#!/usr/bin/env python3
"""Create and verify a Sparkle-signed appcast for the current app archive.

Local releases may use --account for Keychain. CI reads SPARKLE_PRIVATE_KEY
only into a mode-0600 temporary file and removes it even if signing fails.
The generated XML must never be edited after signing.
"""
import argparse
import base64
import html
import json
import os
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parents[1]
SPARKLE = "{http://www.andymatuschak.org/xml-namespaces/sparkle}"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def release_notes(version):
    text = (ROOT / "CHANGELOG.md").read_text()
    match = re.search(r"^## " + re.escape(version) + r"(?: /[^\n]*)?[^\n]*\n(.*?)(?=^## |\Z)",
                      text, re.M | re.S)
    require(match is not None, "No release notes for current version")
    return match.group(1).strip()


def validate_signing_key(secret, public_key):
    try:
        decoded = base64.b64decode(secret, validate=True)
    except ValueError:
        raise ValueError("Invalid Sparkle private-key encoding") from None
    # Sparkle 2.10 generate_keys emits a 32-byte seed. Legacy exports contain
    # the expanded private key followed by a public key (96 bytes total).
    require(len(decoded) in (32, 96), "Missing or invalid Sparkle signing key")
    if len(decoded) == 32:
        actual = Ed25519PrivateKey.from_private_bytes(decoded).public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    else:
        actual = decoded[-32:]
    require(base64.b64encode(actual).decode() == public_key,
            "Signing key does not match the configured public key")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    keys = parser.add_mutually_exclusive_group()
    keys.add_argument("--account", help="Sparkle signing Keychain account")
    keys.add_argument("--key-file", type=Path, help="Existing protected Sparkle private-key file")
    parser.add_argument("--tools", type=Path, help="Official Sparkle bin directory (defaults to SwiftPM artifact)")
    args = parser.parse_args()
    release = json.loads((ROOT / "VERSION.json").read_text())
    config = json.loads((ROOT / "update-config.json").read_text())
    version, build = release["version"], str(release["build"])
    require(re.fullmatch(r"\d+\.\d+\.\d+", version) and build.isdecimal(), "Invalid release version/build")
    archive = args.dist.resolve() / f"PageBridge-{version}-macOS-arm64.zip"
    destination = args.dist.resolve() / "appcast.xml"
    require(archive.is_file(), "App ZIP must be packaged before creating its appcast")
    require(not destination.exists(), "Refusing to overwrite existing appcast")
    repository = config["repository"]
    require(re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository), "Invalid repository")
    base_url = "https://github.com/" + repository
    require(config["feedURL"] == base_url + "/releases/latest/download/appcast.xml", "Unexpected feed URL")
    with zipfile.ZipFile(archive) as zipped:
        app_info = plistlib.loads(zipped.read("PageBridge.app/Contents/Info.plist"))
        require(app_info["CFBundleShortVersionString"] == version and app_info["CFBundleVersion"] == build,
                "App ZIP version does not match VERSION.json")
        require(app_info.get("SUPublicEDKey") == config["publicEDKey"], "App ZIP uses a different public key")
        require(app_info.get("SUFeedURL") == config["feedURL"] and app_info.get("SURequireSignedFeed") is True,
                "App ZIP does not require the configured signed feed")
        framework_info = plistlib.loads(zipped.read("PageBridge.app/Contents/Frameworks/Sparkle.framework/Versions/B/Resources/Info.plist"))
        require(framework_info["CFBundleShortVersionString"] == config["sparkleVersion"], "Sparkle framework version differs")
    tools = args.tools
    if tools is None:
        candidates = list((ROOT / "macOS/.build/artifacts").glob("*/Sparkle/bin/generate_appcast"))
        require(len(candidates) == 1, "Resolve the pinned Sparkle Swift package first, or pass --tools")
        tools = candidates[0].parent
    require((tools / "generate_appcast").is_file() and (tools / "sign_update").is_file(), "Missing Sparkle tools")
    secret = ""
    with tempfile.TemporaryDirectory(prefix="pagebridge-appcast-", dir=args.dist.resolve()) as name:
        temporary = Path(name)
        signing = []
        if args.account:
            signing = ["--account", args.account]
        else:
            if args.key_file:
                require(not args.key_file.is_symlink() and args.key_file.stat().st_mode & 0o077 == 0,
                        "Private key file must have mode 0600 or stricter")
                secret = args.key_file.read_text().strip()
            else:
                secret = os.environ.get("SPARKLE_PRIVATE_KEY", "").strip()
            # Sparkle's malformed-key diagnostic can include its input. Validate
            # here first; never print or pass the private key as an argument.
            validate_signing_key(secret, config["publicEDKey"])
            key = temporary / "signing.key"
            with open(key, "x", opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
                stream.write(secret)
            signing = ["--ed-key-file", str(key)]
        # Only the app ZIP belongs in this folder. Source archives must not be
        # interpreted as installable updates by generate_appcast.
        feeds = temporary / "feed"
        feeds.mkdir()
        os.link(archive, feeds / archive.name)
        notes = release_notes(version)
        (feeds / (archive.stem + ".html")).write_text("<h2>PageBridge " + html.escape(version)
            + "</h2><pre>" + html.escape(notes) + "</pre>\n")
        commands = [[str(tools / "generate_appcast"), *signing,
                     "--download-url-prefix", base_url + f"/releases/download/v{version}/",
                     "--link", base_url + f"/releases/tag/v{version}",
                     "--maximum-deltas", "0", "--maximum-versions", "1",
                     "--versions", build, "--embed-release-notes", str(feeds)],
                    [str(tools / "sign_update"), *signing, "--verify", str(feeds / "appcast.xml")]]
        environment = dict(os.environ)
        environment.pop("SPARKLE_PRIVATE_KEY", None)
        for command in commands:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, env=environment)
            if result.returncode:
                diagnostic = result.stdout.replace(secret, "[redacted]") if secret else result.stdout
                raise RuntimeError("Sparkle signing/verification failed:\n" + diagnostic[-4000:])
        # Verify the archive signature with the same official tool too.
        import xml.etree.ElementTree as ET
        enclosure = ET.parse(feeds / "appcast.xml").find("./channel/item/enclosure")
        require(enclosure is not None, "Generated appcast has no archive")
        command = [str(tools / "sign_update"), *signing, "--verify", str(archive), enclosure.attrib[SPARKLE + "edSignature"]]
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment)
        require(result.returncode == 0, "Official Sparkle archive signature verification failed")
        os.link(feeds / "appcast.xml", destination)
    print(json.dumps({"appcast": str(destination), "version": version, "build": build,
                      "feedSignatureVerified": True, "archiveSignatureVerified": True}))


if __name__ == "__main__":
    main()
