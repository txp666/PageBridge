"""Assemble a local, ad-hoc signed Mac app with a private Python runtime."""
from pathlib import Path
import argparse
import json
import os
import plistlib
import shutil
import subprocess
from bundle_profile import prune_runtime

ROOT = Path(__file__).resolve().parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, default=ROOT / "dist/PageBridge.app")
args = parser.parse_args()
app = args.output.resolve()
if app.parent != ROOT / "dist" or app.suffix != ".app":
    parser.error("Output must be an .app directly inside the project's dist directory.")
info = json.loads((ROOT / "engine/runtime-info.json").read_text())
profile = json.loads((ROOT / "scripts/bundle-profile.json").read_text())
stage = app.with_name("." + app.stem + "-staging.app")
if stage.exists():
    shutil.rmtree(stage)
contents = stage / "Contents"
resources = contents / "Resources"
engine = resources / "engine"
runtime = engine / "runtime"
(contents / "MacOS").mkdir(parents=True)
resources.mkdir()
executable = ROOT / "macOS/.build/release/PageBridge"
shutil.copy2(executable, contents / "MacOS/PageBridge")
subprocess.run([str(ROOT / "engine/.venv/bin/python"), str(ROOT / "scripts/build-icons.py")], check=True)
shutil.copy2(ROOT / "assets/PageBridge.icns", resources / "PageBridge.icns")
shutil.copy2(ROOT / "assets/PageBridge-icon.png", resources / "PageBridgeIcon.png")
shutil.copytree(ROOT / "demos", resources / "demos", ignore=shutil.ignore_patterns(".DS_Store"))

metadata = {
    "CFBundleName": "PageBridge", "CFBundleDisplayName": "PageBridge",
    "CFBundleIdentifier": "tech.pagebridge.mac", "CFBundleExecutable": "PageBridge",
    "CFBundlePackageType": "APPL", "CFBundleShortVersionString": "0.1.4",
    "CFBundleVersion": "6", "CFBundleIconFile": "PageBridge.icns",
    "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True,
    "LSMultipleInstancesProhibited": True,
    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    "NSLocalNetworkUsageDescription": "连接你配置的本机或局域网翻译模型服务。",
    "CFBundleDocumentTypes": [{"CFBundleTypeName": "PDF Document", "LSItemContentTypes": ["com.adobe.pdf"],
                               "CFBundleTypeRole": "Viewer", "LSHandlerRank": "Alternate"}],
}
with (contents / "Info.plist").open("wb") as handle:
    plistlib.dump(metadata, handle)

ignore = shutil.ignore_patterns("__pycache__", "*.pyc", ".DS_Store")
print("Copying standalone Python runtime…", flush=True)
base = Path(info["standaloneBase"])
shutil.copytree(base, runtime, symlinks=True, ignore=ignore)
packages = runtime / "lib/python3.12/site-packages"
shutil.copytree(Path(info["sitePackages"]), packages, dirs_exist_ok=True, symlinks=True, ignore=ignore)
print("Pruning unused web UI, provider SDKs and development resources…", flush=True)
profile_report = prune_runtime(runtime, profile, info["versions"])
(engine / "bundle-profile.json").write_text(json.dumps(profile, indent=2) + "\n")
(ROOT / "validation").mkdir(parents=True, exist_ok=True)
(ROOT / "validation" / (app.stem + "-bundle-profile.json")).write_text(json.dumps(profile_report, indent=2) + "\n")
for relative in ["worker.py", "translation_guard.py", "layout_guard.py", "pdf_links.py", "pyproject.toml", "uv.lock"]:
    shutil.copy2(ROOT / "engine" / relative, engine / relative)
(engine / "runtime-info.json").write_text(json.dumps({"pythonVersion": info["pythonVersion"], "architecture": info["architecture"], "versions": info["versions"]}, indent=2))
(resources / "Third-Party-Notices.txt").write_text(
    "PageBridge local development build\n\n"
    "PDFMathTranslate-next 2.9.0: AGPL-3.0, https://github.com/PDFMathTranslate-next/PDFMathTranslate-next\n"
    "BabelDOC 0.6.2: AGPL-3.0, https://github.com/funstory-ai/BabelDOC\n"
    "Python package source and wheel license notices are included in engine/runtime/lib/python3.12/site-packages.\n"
    "The dependency lock and PageBridge worker source are included in engine/.\n"
    "The native worker runtime profile is recorded in engine/bundle-profile.json.\n"
    "Unused web UI/provider SDKs and development/test resources are excluded from this application.\n"
    "Public sample PDFs are bundled unchanged; titles, authors, sources and individual licenses are in demos/README.md and demos/catalog.json.\n"
    "This local build uses ad-hoc signing, not Developer ID signing or notarization.\n"
)

# A bundle must not depend on the developer's venv or an absolute external symlink.
for directory, dirs, files in os.walk(runtime):
    for name in dirs + files:
        path = Path(directory) / name
        if path.is_symlink():
            target = os.readlink(path)
            if os.path.isabs(target):
                original = Path(target)
                if original.is_relative_to(base):
                    internal = runtime / original.relative_to(base)
                    path.unlink(); path.symlink_to(os.path.relpath(internal, path.parent))
                else:
                    raise RuntimeError(f"External runtime symlink: {path.name}")

print("Signing bundled native extensions…", flush=True)
mach_o_magic = {b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
for directory, _, files in os.walk(runtime):
    for name in files:
        path = Path(directory) / name
        if path.is_symlink():
            continue
        with path.open("rb") as handle:
            magic = handle.read(4)
        if magic in mach_o_magic:
            subprocess.run(["codesign", "--force", "--sign", "-", str(path)], check=True, capture_output=True)
subprocess.run(["codesign", "--force", "--sign", "-", str(stage)], check=True)
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(stage)], check=True)
if app.exists():
    shutil.rmtree(app)
stage.rename(app)
print(str(app), flush=True)
