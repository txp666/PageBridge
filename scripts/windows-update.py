#!/usr/bin/env python3
"""Sign the verified Windows installer with a domain-separated Ed25519 manifest.

The existing SPARKLE_PRIVATE_KEY secret is read in memory only. It is never
written to disk, printed, or forwarded to a command or subprocess.
"""
import argparse
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "txp666/PageBridge"
NAME = "windows-update.json"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def contract():
    # Use the exact client parser and canonical signature bytes; this module is
    # intentionally independent of PDF libraries and imports cryptography lazily.
    return load_module("pagebridge_update_contract", Path(__file__).resolve().parents[1] / "desktop/pagebridge/updates.py")


def verify_manifest(raw, public_key):
    return contract().verify_manifest(raw, public_key)


def signing_key(secret, public_key):
    try:
        seed = base64.b64decode(secret, validate=True)
    except (ValueError, TypeError):
        raise ValueError("Invalid update signing-key encoding") from None
    require(len(seed) == 32, "Windows update signing requires a 32-byte Ed25519 seed")
    private = Ed25519PrivateKey.from_private_bytes(seed)
    actual = base64.b64encode(private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode("ascii")
    require(actual == public_key, "Update signing key does not match the configured public key")
    return private


def sign_payload(payload, secret, public_key):
    private = signing_key(secret, public_key)
    signature = private.sign(contract().signature_message(payload))
    raw = (json.dumps(dict(payload=payload, signature=base64.b64encode(signature).decode("ascii")),
                      ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("ascii")
    # Fail before creating any artifact if the client would reject the envelope,
    # unknown fields, types, URLs, ranges or the signature.
    require(verify_manifest(raw, public_key) == payload, "Client rejected the generated update payload")
    return raw


def verify_installer_binding(raw, public_key, release, installer):
    payload = verify_manifest(raw, public_key)
    require(payload["version"] == release["version"] and payload["build"] == int(release["build"]),
            "Windows update version/build differs from release")
    require(payload["minWindowsBuild"] == 19041, "Windows update minimum OS differs from the application")
    require(installer.name == f"PageBridge-{release['version']}-windows-x64-setup.exe", "Unexpected update installer name")
    with installer.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    require(payload["bytes"] == installer.stat().st_size and payload["sha256"] == digest,
            "Windows update installer bytes/checksum differ")
    return payload


def generate(dist, source_root):
    release = json.loads((source_root / "VERSION.json").read_text())
    config = json.loads((source_root / "update-config.json").read_text())
    require(config["repository"] == REPOSITORY, "Unexpected Windows update repository")
    destination = dist / NAME
    require(not destination.exists(), "Refusing to overwrite an existing signed Windows update manifest")
    version = release["version"]
    installer = dist / f"PageBridge-{version}-windows-x64-setup.exe"
    packages = load_module("windows_package", Path(__file__).with_name("windows_package.py"))
    checked = packages.verify_installer(installer, source_root)
    require(checked["status"] == "passed", "Windows installer must pass verification before signing")
    base = "https://github.com/" + REPOSITORY
    payload = dict(schema=1, platform="windows-x64", version=version, build=int(release["build"]),
                   minWindowsBuild=19041, url=base + f"/releases/download/v{version}/" + installer.name,
                   sha256=checked["sha256"], bytes=checked["bytes"], releaseNotesURL=base + f"/releases/tag/v{version}")
    # Scope the secret to this in-process signing call. Verification above does
    # not run subprocesses or network requests with the key in their environment.
    raw = sign_payload(payload, os.environ.get("SPARKLE_PRIVATE_KEY", "").strip(), config["publicEDKey"])
    verify_installer_binding(raw, config["publicEDKey"], release, installer)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=dist, prefix=".windows-update-", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw); stream.flush()
        os.link(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return dict(status="passed", version=version, build=int(release["build"]), manifest=NAME,
                manifestSHA256=hashlib.sha256(raw).hexdigest(), installerSHA256=payload["sha256"],
                installerBytes=payload["bytes"], signatureVerified=True, clientParserVerified=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--source-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, help="Write public verification evidence (never key material)")
    args = parser.parse_args()
    result = generate(args.dist.resolve(), args.source_root.resolve())
    report = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    print(report, end="")


if __name__ == "__main__":
    main()
