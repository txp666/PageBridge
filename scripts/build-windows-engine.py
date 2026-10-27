#!/usr/bin/env python3
"""Assemble the locked x64 worker into a relocatable Windows private runtime."""
from __future__ import annotations

import argparse
from importlib import metadata
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
ENGINE_FILES = ('worker.py', 'translation_guard.py', 'layout_guard.py', 'pdf_links.py', 'structured_reading.py', 'reading_background.py', 'reading_tables.py', 'reading_text_effects.py', 'mineru_bridge.py', 'setup-mineru.py', 'mineru-requirements.txt', 'mineru-models.json', 'structured-reading.schema.json', 'vendor/uv-LICENSE-MIT', 'vendor/uv-LICENSE-APACHE', 'pyproject.toml', 'uv.lock')


def canonical(value):
    return re.sub(r"[-_.]+", "-", value).lower()


def footprint(root):
    files = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
    return {"files": len(files), "bytes": sum(p.stat().st_size for p in files)}


def check_interpreter_abi(info, executable):
    # platform.machine() describes the native OS on Windows ARM, even while
    # x64 Python is running under emulation. Check the process and PE instead.
    if info.get("pythonPlatform") != "win-amd64" or info.get("processBits") != 64:
        raise ValueError("Expected a 64-bit win-amd64 Python process.")
    with Path(executable).open("rb") as handle:
        if handle.read(2) != b"MZ":
            raise ValueError("The interpreter is not a Windows executable.")
        handle.seek(0x3c)
        offset = struct.unpack("<I", handle.read(4))[0]
        handle.seek(offset)
        if handle.read(4) != b"PE\0\0" or struct.unpack("<H", handle.read(2))[0] != 0x8664:
            raise ValueError("The interpreter PE machine must be AMD64.")


def check_tree(root):
    for path in root.rglob("*"):
        if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
            raise ValueError(f"Runtime contains a link or junction: {path.name}")


def rename_with_retry(source, target, timeout=60):
    # DLL image sections can remain briefly locked after a Windows process
    # exits, especially while antivirus scans a fresh runtime. Retry only the relevant Windows errors; never delete the source
    # or mask a persistent failure with an attempted cleanup.
    deadline = time.monotonic() + timeout
    while True:
        try:
            return source.rename(target)
        except OSError as error:
            if getattr(error, "winerror", None) not in (5, 32, 33) or time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def create_stage(parent):
    # Python 3.12's Windows mkdtemp uses mode 0700 and a protected DACL.
    # Shipping binaries must inherit the build directory's ordinary access,
    # so an elevated build remains executable from the same user's desktop.
    stage = Path(parent) / (".pagebridge-engine-stage-" + uuid.uuid4().hex)
    stage.mkdir()
    return stage


def prune(runtime, profile, versions):
    runtime = runtime.resolve()
    if runtime.name != "runtime" or not runtime.parent.name.startswith(".pagebridge-engine-stage-"):
        raise ValueError("Only a generated Windows engine staging runtime may be pruned.")
    for name, version in profile["auditedEngineVersions"].items():
        if versions.get(name) != version:
            raise ValueError("Re-audit the runtime profile for " + name)
    check_tree(runtime)
    packages = runtime / "Lib/site-packages"
    before = footprint(runtime)
    removed = []

    def remove(path, reason):
        path = Path(path)
        if not path.resolve().is_relative_to(runtime) or path.resolve() == runtime:
            raise ValueError("Refusing to prune outside the private runtime.")
        if not path.exists():
            return
        removed.append({"path": path.relative_to(runtime).as_posix(), "reason": reason})
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()

    excluded = {canonical(name): reason for reason, names in profile["excludedDistributions"].items() for name in names}
    # Windows does not have the Unix system IANA timezone database.
    excluded.pop("tzdata", None)
    receipts = []
    for dist in metadata.distributions(path=[str(packages)]):
        name = canonical(dist.metadata["Name"])
        if name not in excluded:
            continue
        if dist.files is None:
            raise ValueError("Distribution has no RECORD: " + name)
        for item in dist.files:
            path = Path(dist.locate_file(item))
            if not path.resolve().is_relative_to(runtime) or path.is_dir():
                raise ValueError("Distribution receipt leaves runtime: " + name)
            receipts.append((path, excluded[name]))
    # Validate every receipt before applying any deletion.
    for path, reason in receipts:
        remove(path, reason)
    for package in profile["testDirectoryPackages"]:
        directory = packages / package
        if directory.is_dir():
            for current, dirs, _ in os.walk(directory):
                for name in list(dirs):
                    if name in ("test", "tests"):
                        remove(Path(current) / name, "package-test-data")
                        dirs.remove(name)
    for name in ("Include", "include", "libs", "Doc", "Tools", "Scripts", "tcl",
                 "Lib/ensurepip", "Lib/idlelib", "Lib/tkinter", "Lib/turtledemo", "Lib/turtle.py",
                 "Lib/lib2to3", "Lib/test", "Lib/site-packages/_virtualenv.py", "Lib/site-packages/_virtualenv.pth"):
        remove(runtime / name, "development-resource")
    for pattern in ("DLLs/_tkinter*", "DLLs/tcl*", "DLLs/tk*", "*.pdb"):
        for path in runtime.glob(pattern):
            remove(path, "development-resource")
    for current, dirs, files in os.walk(runtime, topdown=False):
        for name in files:
            if name.endswith((".pyc", ".pyo")):
                remove(Path(current) / name, "bytecode")
        for name in dirs:
            path = Path(current) / name
            if path.exists() and not any(path.iterdir()):
                path.rmdir()
    return {"profile": "windows-x64-native-worker-v1", "before": before, "after": footprint(runtime), "removed": removed}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist/windows-engine")
    parser.add_argument("--python", type=Path, default=ROOT / "engine/.venv/Scripts/python.exe")
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Build the Windows runtime inside Windows; do not copy macOS native libraries.")
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "dist") or output == ROOT / "dist":
        parser.error("Output must be a subdirectory of this checkout's dist directory.")
    python = args.python.resolve()
    info = json.loads(subprocess.check_output([str(python), str(ROOT / "engine/runtime_info.py")], text=True, encoding="utf-8"))
    expected_python = (ROOT / "engine/.python-version").read_text().strip()
    if info["pythonVersion"] != expected_python:
        raise ValueError("Expected the pinned managed CPython runtime.")
    check_interpreter_abi(info, python)
    base, packages = Path(info["standaloneBase"]), Path(info["sitePackages"])
    if not (base / "python.exe").is_file() or not packages.is_dir():
        raise ValueError("Missing Windows base runtime or site-packages.")
    output.parent.mkdir(parents=True, exist_ok=True)
    # Native extension loading still hits MAX_PATH in some dependencies. Keep
    # this staging runtime beside the bundle, not inside two UUID stage names.
    stage = create_stage(ROOT / "dist")
    try:
        runtime = stage / "runtime"
        ignore = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".DS_Store")
        check_tree(base)
        check_tree(packages)
        shutil.copytree(base, runtime, ignore=ignore)
        shutil.copytree(packages, runtime / "Lib/site-packages", dirs_exist_ok=True, ignore=ignore)
        profile = json.loads((ROOT / "scripts/bundle-profile.json").read_text())
        report = prune(runtime, profile, info["versions"])
        for name in ENGINE_FILES:
            (stage / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / "engine" / name, stage / name)
        uv_source = Path(os.environ.get("PAGEBRIDGE_UV", ROOT / "engine/.tools/bin/uv.exe"))
        if not uv_source.is_file():
            resolved_uv = shutil.which("uv")
            if not resolved_uv:
                raise ValueError("uv 0.9.17 is required for the optional reading runtime bootstrap.")
            uv_source = Path(resolved_uv)
        if subprocess.check_output([str(uv_source), "--version"], text=True).split()[:2] != ["uv", "0.9.17"]:
            raise ValueError("Expected uv 0.9.17 for the reading runtime bootstrap.")
        (stage / "tools").mkdir()
        shutil.copy2(uv_source, stage / "tools/uv.exe")
        (stage / "runtime-info.json").write_text(json.dumps({key: info[key] for key in ("pythonVersion", "architecture", "pythonPlatform", "processBits", "nativeOSArchitecture", "versions")}, indent=2) + "\n", encoding="utf-8")
        (stage / "bundle-profile.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        evidence = ROOT / "validation/windows/engine-staging.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([str(python), str(ROOT / "scripts/verify-windows-engine.py"), "--engine", str(stage), "--output", str(evidence)], check=True)
        if output.exists():
            rename_with_retry(output, output.with_name(output.name + ".previous-" + str(time.time_ns())))
        rename_with_retry(stage, output)
        print(json.dumps({"engine": str(output), "footprint": report["after"]}, indent=2))
    except BaseException:
        if stage.exists():
            print(f"Failed build retained for inspection: {stage}", file=sys.stderr)
        raise


if __name__ == "__main__":
    main()
