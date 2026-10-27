"""Assemble a local, ad-hoc signed Mac app with a private Python runtime."""
from pathlib import Path
import argparse
import base64
import json
import os
import plistlib
import shutil
import subprocess
from urllib.parse import urlparse
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
release = json.loads((ROOT / "VERSION.json").read_text())
updates = json.loads((ROOT / "update-config.json").read_text())
feed = urlparse(updates["feedURL"])
if feed.scheme != "https" or not feed.hostname or feed.username or feed.password:
    raise RuntimeError("The update feed must use an HTTPS URL without credentials.")
if len(base64.b64decode(updates["publicEDKey"], validate=True)) != 32:
    raise RuntimeError("Sparkle requires a 32-byte Ed25519 public key.")
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

# SwiftPM verifies the checksum of its pinned binary artifact. Copy the actual
# framework tree, preserving version symlinks and helper executable modes.
sparkle_candidates = list((ROOT / "macOS/.build/artifacts").glob(
    "*/Sparkle/Sparkle.xcframework/macos-arm64_x86_64/Sparkle.framework"))
if len(sparkle_candidates) != 1:
    raise RuntimeError("Expected one resolved Sparkle framework; run the Swift build first.")
sparkle_source = sparkle_candidates[0]
sparkle_info = plistlib.loads((sparkle_source / "Resources/Info.plist").read_bytes())
if sparkle_info.get("CFBundleShortVersionString") != updates["sparkleVersion"]:
    raise RuntimeError("Resolved Sparkle framework differs from the pinned update version.")
frameworks = contents / "Frameworks"
frameworks.mkdir()
sparkle = frameworks / "Sparkle.framework"
shutil.copytree(sparkle_source, sparkle, symlinks=True, ignore=shutil.ignore_patterns(".DS_Store"))
for path in sparkle.rglob("*"):
    if path.is_symlink() and (os.path.isabs(os.readlink(path)) or not path.resolve().is_relative_to(sparkle.resolve())):
        raise RuntimeError("Sparkle contains an external framework symlink.")
sparkle_licenses = list((ROOT / "macOS/.build/checkouts").glob("*/LICENSE"))
sparkle_licenses = [path for path in sparkle_licenses if path.parent.name.lower() == "sparkle"]
if len(sparkle_licenses) != 1:
    raise RuntimeError("The pinned Sparkle checkout license is missing.")
shutil.copy2(sparkle_licenses[0], resources / "Sparkle-LICENSE.txt")

# SwiftPM can add artifact and compiler-runtime search paths that only exist on
# the build machine. Resolve the active toolchain instead of hardcoding Xcode's
# installation path; keep the OS runtime and embedded-framework search paths.
swift_compiler = Path(subprocess.run(["xcrun", "--find", "swift"], check=True,
                                     capture_output=True, text=True).stdout.strip())
toolchain_lib = swift_compiler.resolve().parent.parent / "lib"
toolchain_swift_dirs = [path.resolve() for path in toolchain_lib.glob("swift*")
                        if path.is_dir() and not path.resolve().is_relative_to(Path("/usr/lib"))]
load_commands = subprocess.run(["otool", "-l", str(contents / "MacOS/PageBridge")],
                               check=True, capture_output=True, text=True).stdout.splitlines()
for index, line in enumerate(load_commands):
    if line.strip() == "cmd LC_RPATH":
        for detail in load_commands[index + 1:index + 4]:
            if detail.strip().startswith("path "):
                rpath = detail.strip()[5:].rsplit(" (offset", 1)[0]
                rpath_path = Path(rpath)
                is_build_path = rpath_path.is_absolute() and rpath_path.is_relative_to(ROOT / "macOS/.build")
                is_toolchain_path = rpath_path.is_absolute() and any(
                    rpath_path.resolve().is_relative_to(directory) for directory in toolchain_swift_dirs)
                if is_build_path or is_toolchain_path:
                    subprocess.run(["install_name_tool", "-delete_rpath", rpath,
                                    str(contents / "MacOS/PageBridge")], check=True)

metadata = {
    "CFBundleName": "PageBridge", "CFBundleDisplayName": "PageBridge",
    "CFBundleIdentifier": "tech.pagebridge.mac", "CFBundleExecutable": "PageBridge",
    "CFBundlePackageType": "APPL", "CFBundleShortVersionString": release["version"],
    "CFBundleVersion": release["build"], "CFBundleIconFile": "PageBridge.icns",
    "LSMinimumSystemVersion": "14.0", "NSHighResolutionCapable": True,
    "LSMultipleInstancesProhibited": True,
    "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
    "NSLocalNetworkUsageDescription": "连接你配置的本机或局域网翻译模型服务。",
    "SUFeedURL": updates["feedURL"],
    "SUPublicEDKey": updates["publicEDKey"],
    "SURequireSignedFeed": True,
    "SUVerifyUpdateBeforeExtraction": True,
    "SUAutomaticallyUpdate": False,
    "SUAllowsAutomaticUpdates": False,
    "SUEnableSystemProfiling": False,
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
for relative in ['worker.py', 'translation_guard.py', 'layout_guard.py', 'pdf_links.py', 'structured_reading.py', 'reading_background.py', 'reading_tables.py', 'reading_text_effects.py', 'mineru_bridge.py', 'setup-mineru.py', 'mineru-requirements.txt', 'mineru-models.json', 'structured-reading.schema.json', 'vendor/uv-LICENSE-MIT', 'vendor/uv-LICENSE-APACHE', 'pyproject.toml', 'uv.lock']:
    (engine / relative).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "engine" / relative, engine / relative)
# The optional parser uses an independent, writable environment. Ship only its
# fixed bootstrap tool, not the parser models or another Python dependency tree.
uv_source = Path(os.environ.get("PAGEBRIDGE_UV", ROOT / "engine/.tools/bin/uv"))
if not uv_source.is_file():
    resolved_uv = shutil.which("uv")
    if not resolved_uv:
        raise RuntimeError("uv 0.9.17 is required to build the optional reading runtime bootstrap.")
    uv_source = Path(resolved_uv)
uv_version = subprocess.run([str(uv_source), "--version"], check=True, capture_output=True, text=True).stdout.split()
if uv_version[:2] != ["uv", "0.9.17"]:
    raise RuntimeError("Expected uv 0.9.17 for the reading runtime bootstrap.")
(engine / "tools").mkdir()
shutil.copy2(uv_source, engine / "tools/uv")
subprocess.run(["codesign", "--force", "--sign", "-", str(engine / "tools/uv")], check=True, capture_output=True)
(engine / "runtime-info.json").write_text(json.dumps({"pythonVersion": info["pythonVersion"], "architecture": info["architecture"], "versions": info["versions"]}, indent=2))
shutil.copy2(ROOT / "LICENSE", resources / "LICENSE")
shutil.copy2(ROOT / "THIRD_PARTY_NOTICES.md", resources / "Third-Party-Notices.txt")
shutil.copy2(ROOT / "VERSION.json", resources / "VERSION.json")
shutil.copy2(ROOT / "update-config.json", resources / "update-config.json")

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
# Sparkle's helpers are separate code objects. Sign from the inside out, without
# --deep signing, preserving the downloader's own entitlement metadata.
print("Signing Sparkle framework and update helpers…", flush=True)
sparkle_version = (sparkle / "Versions/Current").resolve()
for helper in (sparkle_version / "XPCServices/Installer.xpc",
               sparkle_version / "XPCServices/Downloader.xpc",
               sparkle_version / "Autoupdate", sparkle_version / "Updater.app"):
    if not helper.exists():
        raise RuntimeError("Sparkle update helper is missing: " + helper.name)
    command = ["codesign", "--force", "--sign", "-"]
    if helper.name == "Downloader.xpc":
        command.append("--preserve-metadata=entitlements")
    subprocess.run(command + [str(helper)], check=True, capture_output=True)
subprocess.run(["codesign", "--force", "--sign", "-", str(sparkle)], check=True, capture_output=True)
subprocess.run(["codesign", "--force", "--sign", "-", str(stage)], check=True)
subprocess.run(["codesign", "--verify", "--deep", "--strict", str(stage)], check=True)
if app.exists():
    shutil.rmtree(app)
stage.rename(app)
print(str(app), flush=True)
