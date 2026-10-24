#!/usr/bin/env python3
"""Archive the verified Mac app and the current, non-ignored project source."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import stat
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parent.parent


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def finish(temporary, destination):
    # link() is atomic and refuses an existing release filename.
    os.link(temporary, destination)
    temporary.unlink()
    sidecar = destination.with_name(destination.name + ".sha256")
    with sidecar.open("x") as stream:
        stream.write(f"{checksum(destination)}  {destination.name}\n")
    print(f"{destination.name}: {destination.stat().st_size:,} bytes")


def source_files():
    # Include the working copy of tracked files AND new non-ignored files. git
    # archive HEAD would silently omit unreleased application source.
    result = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                            cwd=ROOT, check=True, capture_output=True)
    allowed_roots = {"macOS", "engine", "scripts", "docs", "assets", "demos", ".github"}
    allowed_files = {".gitignore", "VERSION.json", "update-config.json", "LICENSE", "README.md", "CHANGELOG.md",
                     "DESIGN.md", "CONTRIBUTING.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md"}
    banned_parts = {".git", "node_modules", ".venv", ".build", "__pycache__", ".cache", ".tools", ".artifacts"}
    files = []
    for name in sorted(set(os.fsdecode(raw) for raw in result.stdout.split(b"\0") if raw)):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError("Unsafe source path")
        if relative.parts[0] not in allowed_roots and name not in allowed_files:
            raise RuntimeError(f"Unreviewed source root: {name}")
        if banned_parts.intersection(relative.parts):
            raise RuntimeError(f"Build/cache files must not be tracked: {name}")
        if relative.suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".mobileprovision", ".keychain", ".keychain-db"}:
            raise RuntimeError(f"Credential-shaped file must not be tracked: {name}")
        if relative.name == ".env" or relative.name.startswith(".env.") and relative.name != ".env.example":
            raise RuntimeError(f"Environment file must not be tracked: {name}")
        path = ROOT / relative
        if not path.exists():
            continue  # Deleted working-tree files are not current source.
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Only ordinary project files may be exported: {name}")
        files.append((name, path))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--app-only", action="store_true")
    mode.add_argument("--source-only", action="store_true")
    args = parser.parse_args()
    release = json.loads((ROOT / "VERSION.json").read_text())
    version = release["version"]
    if not version or any(c not in "0123456789." for c in version):
        raise RuntimeError("Invalid release version")
    out = ROOT / "dist"
    out.mkdir(exist_ok=True)
    jobs = []
    if not args.source_only:
        jobs.append(("app", out / f"PageBridge-{version}-macOS-arm64.zip"))
    if not args.app_only:
        jobs.append(("source", out / f"PageBridge-{version}-source.zip"))
    for _, target in jobs:
        if target.exists() or target.with_name(target.name + ".sha256").exists():
            raise RuntimeError(f"Refusing to overwrite an existing release: {target.name}")
    for kind, target in jobs:
        temporary = target.with_name("." + target.name + ".tmp")
        if temporary.exists():
            raise RuntimeError(f"A previous archive is still present: {temporary.name}")
        try:
            if kind == "app":
                app = out / "PageBridge.app"
                info = plistlib.loads((app / "Contents/Info.plist").read_bytes())
                if (info["CFBundleShortVersionString"], info["CFBundleVersion"]) != (version, release["build"]):
                    raise RuntimeError("Application version/build differs from VERSION.json")
                subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True)
                subprocess.run(["ditto", "-c", "-k", "--norsrc", "--keepParent", str(app), str(temporary)], check=True)
            else:
                files = source_files()
                manifest = {"version": version, "build": release["build"], "files": {}}
                prefix = f"PageBridge-{version}-source/"
                with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                    for name, path in files:
                        data = path.read_bytes()
                        mode = stat.S_IMODE(path.stat().st_mode)
                        manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "mode": mode}
                        entry = zipfile.ZipInfo(prefix + name, (2026, 1, 1, 0, 0, 0))
                        entry.external_attr = (stat.S_IFREG | mode) << 16
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        archive.writestr(entry, data)
                    archive.writestr(prefix + "SOURCE_MANIFEST.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            with zipfile.ZipFile(temporary) as archive:
                if archive.testzip() is not None:
                    raise RuntimeError("Archive CRC check failed")
            finish(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()


if __name__ == "__main__":
    main()
