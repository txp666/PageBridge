#!/usr/bin/env python3
"""Check a relocated Windows worker without calling a model or reading user data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ENGINE_FILES = ('worker.py', 'translation_guard.py', 'layout_guard.py', 'pdf_links.py', 'structured_reading.py', 'reading_background.py', 'reading_tables.py', 'reading_text_effects.py', 'mineru_bridge.py', 'setup-mineru.py', 'mineru-requirements.txt', 'mineru-models.json', 'structured-reading.schema.json', 'vendor/uv-LICENSE-MIT', 'vendor/uv-LICENSE-APACHE', 'pyproject.toml', 'uv.lock')


def pe_machine(path):
    with Path(path).open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("Not a Windows PE executable: " + Path(path).name)
        handle.seek(0x3c)
        offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(offset)
        if handle.read(4) != b"PE\0\0":
            raise ValueError("Invalid PE signature.")
        return struct.unpack("<H", handle.read(2))[0]


def verify(engine):
    if sys.platform != "win32":
        raise ValueError("Run this verification inside Windows.")
    engine = Path(engine).resolve()
    runtime = engine / "runtime"
    python = runtime / "python.exe"
    if pe_machine(python) != 0x8664:
        raise ValueError("The private interpreter must be Windows x64.")
    for path in runtime.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError("The runtime contains a link or junction.")
    for path in (runtime / "Lib/site-packages").glob("*.pth"):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith(("#", "import ", "import\t")):
                continue
            resolved = path.parent / line
            if Path(line).is_absolute() or not resolved.resolve().is_relative_to(runtime):
                raise ValueError("A .pth path escapes the private runtime.")
    if subprocess.check_output([str(engine / "tools/uv.exe"), "--version"], text=True).split()[:2] != ["uv", "0.9.17"]:
        raise ValueError("Missing or mismatched optional reading bootstrap.")
    source = {}
    for name in ENGINE_FILES:
        actual = hashlib.sha256((engine / name).read_bytes()).hexdigest()
        expected = hashlib.sha256((ROOT / "engine" / name).read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError("Bundled source mismatch: " + name)
        source[name] = actual
    with tempfile.TemporaryDirectory(prefix="pagebridge-win-verify-") as temporary:
        home = Path(temporary)
        environment = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
        environment.update(HOME=temporary, USERPROFILE=temporary, APPDATA=temporary, LOCALAPPDATA=temporary,
                           PYTHONHOME=str(runtime), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
        probe = """import json, pathlib, sys, site, sysconfig, struct, platform, importlib.metadata
import pymupdf, pdf2zh_next, babeldoc, onnxruntime, bitstring, httpx, cryptography
import cryptography.hazmat.bindings._rust as crypto_native
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives import hashes
from cryptography.exceptions import InvalidSignature
key = Ed25519PrivateKey.generate()
signature = key.sign(b'PageBridge update verification probe')
key.public_key().verify(signature, b'PageBridge update verification probe')
try:
 key.public_key().verify(signature, b'altered update')
except InvalidSignature:
 pass
else:
 raise RuntimeError('Ed25519 accepted a modified message')
sha = hashes.Hash(hashes.SHA256()); sha.update(b'abc')
if sha.finalize().hex() != 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad':
 raise RuntimeError('SHA256 known-answer check failed')
modules = [pymupdf, pdf2zh_next, babeldoc, onnxruntime, bitstring, httpx, cryptography, crypto_native]
print(json.dumps({'prefix': sys.prefix, 'executable': sys.executable, 'userSiteEnabled': site.ENABLE_USER_SITE,
 'pythonPlatform': sysconfig.get_platform(), 'processBits': struct.calcsize('P')*8, 'nativeOSArchitecture': platform.machine(),
 'updateCrypto': {'ed25519': 'passed', 'sha256': 'passed'},
 'paths': {m.__name__: str(pathlib.Path(m.__file__).resolve()) for m in modules},
 'versions': {n:importlib.metadata.version(n) for n in ['pdf2zh-next','babeldoc','PyMuPDF','bitstring']}}))
"""
        result = subprocess.run([str(python), "-B", "-c", probe], cwd=home, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=90)
        if result.returncode:
            raise ValueError("Private runtime import probe failed: " + result.stderr[-5000:])
        imports = json.loads(result.stdout)
        if imports["pythonPlatform"] != "win-amd64" or imports["processBits"] != 64:
            raise ValueError("The bundled Python process is not 64-bit win-amd64.")
        if imports["userSiteEnabled"] or not Path(imports["prefix"]).resolve().is_relative_to(runtime):
            raise ValueError("Python isolation check failed.")
        if any(not Path(path).is_relative_to(runtime) for path in imports["paths"].values()):
            raise ValueError("A runtime import came from outside the bundle.")
        expected_versions = {"pdf2zh-next": "2.9.0", "babeldoc": "0.6.2", "PyMuPDF": "1.25.2", "bitstring": "4.3.0"}
        if imports["versions"] != expected_versions:
            raise ValueError("Private engine versions differ from the audited lock.")
        request = {"protocolVersion": 1, "jobId": "windows-package-check", "attemptId": "windows-package-check", "action": "health"}
        result = subprocess.run([str(python), "-B", "-u", str(engine / "worker.py")], input=json.dumps(request) + "\n", cwd=home, env=environment, capture_output=True, text=True, encoding="utf-8", timeout=120, check=True)
        events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
        if not events or events[0]["type"] != "ready" or events[-1]["type"] != "result":
            raise ValueError("The bundled worker did not complete health successfully.")
        previous = 0
        for event in events:
            if (event.get("protocolVersion") != 1 or event.get("jobId") != request["jobId"] or event.get("attemptId") != request["attemptId"] or event.get("sequence", 0) <= previous):
                raise ValueError("Worker health protocol identity/sequence mismatch.")
            previous = event["sequence"]
        if sum(e["type"] in ("result", "error") for e in events) != 1:
            raise ValueError("Worker sent multiple terminal events.")
    if any(runtime.rglob("*.pyc")):
        raise ValueError("Verification wrote bytecode into the packaged runtime.")
    return {"status": "passed", "architecture": "win-x64", "imports": imports, "sourceSHA256": source,
            "healthEvents": events, "performsAPIRequests": False, "performsPDFTranslation": False}


def main():
    # GitHub's English Windows runner otherwise uses cp1252 for redirected
    # stdout, while the worker health message intentionally contains Chinese.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = verify(args.engine)
    content = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8")
    print(content)


if __name__ == "__main__":
    main()
