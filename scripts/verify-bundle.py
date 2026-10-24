#!/usr/bin/python3
"""Verify the signed private Python/worker bundle without a developer environment.

Run with /usr/bin/python3 scripts/verify-bundle.py. No API request or PDF
translation is performed. A temporary HOME isolates import-time cache activity.
"""
import ast
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import re
import tempfile
import plistlib

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "dist/PageBridge.app"
ENGINE = APP / "Contents/Resources/engine"
RUNTIME = ENGINE / "runtime"
PYTHON = RUNTIME / "bin/python3"
WORKER = ENGINE / "worker.py"
SOURCE_WORKER = ROOT / "engine/worker.py"
OUTPUT = ROOT / "validation/bundle-check.json"
WORKING_DIRECTORY = Path("/private/tmp")
MINIMAL_PATH = "/usr/bin:/bin"

# Executed only by the Python interpreter inside the assembled application.
# Check every imported file-backed module, not only package metadata.
PROBE = r'''
import importlib
import importlib.metadata
import json
import pathlib
import platform
import sys

root = pathlib.Path(sys.prefix).resolve()
modules = {}
for name in ("pdf2zh_next", "babeldoc", "pymupdf", "onnxruntime", "bitstring"):
    module = importlib.import_module(name)
    origin = getattr(module, "__file__", None)
    paths = [str(pathlib.Path(origin).resolve())] if origin else [str(pathlib.Path(p).resolve()) for p in getattr(module, "__path__", [])]
    if not paths:
        raise RuntimeError("Cannot locate required module: " + name)
    modules[name] = paths

versions = {}
distributions = {}
for name in ("pdf2zh-next", "babeldoc", "PyMuPDF", "onnxruntime", "bitstring"):
    distribution = importlib.metadata.distribution(name)
    versions[name] = distribution.version
    distributions[name] = str(pathlib.Path(distribution.locate_file("")).resolve())

outside = {}
checked = 0
for name, module in list(sys.modules.items()):
    origin = getattr(module, "__file__", None)
    if not origin or origin.startswith("<"):
        continue
    checked += 1
    path = pathlib.Path(origin).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        outside[name] = str(path)

print("PAGEBRIDGE_BUNDLE_PROBE=" + json.dumps({
    "prefix": str(root), "basePrefix": str(pathlib.Path(sys.base_prefix).resolve()),
    "executable": str(pathlib.Path(sys.executable).resolve()),
    "pythonVersion": platform.python_version(), "architecture": platform.machine(),
    "modules": modules, "distributions": distributions, "versions": versions,
    "importedFileModuleCount": checked, "modulesOutsideRuntime": outside,
    "dontWriteBytecode": sys.dont_write_bytecode,
    "noUserSite": bool(sys.flags.no_user_site),
    "installedDistributions": sorted(d.metadata["Name"] for d in importlib.metadata.distributions()),
}, ensure_ascii=False))
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def inside(path, parent):
    try:
        Path(path).resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bytecode_snapshot():
    files = {}
    directories = []
    for directory, dirs, names in os.walk(APP, followlinks=False):
        folder = Path(directory)
        for name in dirs:
            if name == "__pycache__":
                directories.append(str((folder / name).relative_to(APP)))
        for name in names:
            if name.endswith((".pyc", ".pyo")):
                path = folder / name
                stat = path.stat()
                files[str(path.relative_to(APP))] = {
                    "size": stat.st_size, "modifiedNS": stat.st_mtime_ns,
                    "sha256": sha256(path),
                }
    return files, sorted(directories)


def run(command, environment, input_text=None):
    return subprocess.run(command, input=input_text, text=True, capture_output=True,
                          cwd=str(WORKING_DIRECTORY), env=environment, timeout=180)


def verify_signature(environment):
    result = run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(APP)], environment)
    return {"passed": result.returncode == 0, "exitCode": result.returncode,
            "diagnostic": (result.stdout + result.stderr).strip()}


def worker_expected_versions():
    tree = ast.parse(SOURCE_WORKER.read_text(encoding="utf-8"))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "EXPECTED" for target in statement.targets):
            value = ast.literal_eval(statement.value)
            require(isinstance(value, dict), "Worker EXPECTED versions must be a dictionary.")
            return value
    raise RuntimeError("Source worker does not declare expected dependency versions.")


def verify_health(environment):
    request = {"protocolVersion": 1, "jobId": "package-check", "attemptId": "package-check", "action": "health"}
    result = run([str(PYTHON), "-B", "-u", str(WORKER)], environment, json.dumps(request) + "\n")
    require(result.returncode == 0, "Bundled worker health failed (exit %s): %s" % (result.returncode, result.stderr.strip()))
    require(result.stdout.endswith("\n"), "Worker health did not terminate its final NDJSON event.")
    events = []
    last_sequence = -1
    terminal = False
    for line in result.stdout.splitlines():
        require(bool(line), "Worker health produced an empty protocol line.")
        event = json.loads(line)
        require(isinstance(event, dict), "Worker event is not an object.")
        require(not terminal, "Worker emitted an event after its terminal result.")
        require(all(event.get(key) == request[key] for key in ("protocolVersion", "jobId", "attemptId")), "Worker health event identity does not match its request.")
        sequence = event.get("sequence")
        require(isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > last_sequence, "Worker health event sequence is invalid.")
        require(event.get("type") in ("ready", "result"), "Worker health returned an unexpected event or error.")
        require(events or event["type"] == "ready", "Worker did not send its ready handshake first.")
        last_sequence = sequence
        terminal = event["type"] == "result"
        events.append(event)
    require(len(events) == 2 and terminal, "Worker health must emit one ready and one result event.")
    return events[-1], events


def verify_imports(environment, expected):
    result = run([str(PYTHON), "-B", "-c", PROBE], environment)
    require(result.returncode == 0, "Bundled module import probe failed (exit %s): %s" % (result.returncode, result.stderr.strip()))
    prefix = "PAGEBRIDGE_BUNDLE_PROBE="
    lines = [line[len(prefix):] for line in result.stdout.splitlines() if line.startswith(prefix)]
    require(len(lines) == 1, "Bundled module probe did not produce exactly one result.")
    probe = json.loads(lines[0])
    require(Path(probe["prefix"]).resolve() == RUNTIME.resolve(), "Python sys.prefix is outside the private runtime.")
    require(Path(probe["basePrefix"]).resolve() == RUNTIME.resolve(), "Python sys.base_prefix is outside the private runtime.")
    require(inside(probe["executable"], RUNTIME), "Python executable resolves outside the application.")
    require(probe["dontWriteBytecode"] and probe["noUserSite"], "Python bytecode/user-site isolation flags are not active.")
    require(not probe["modulesOutsideRuntime"], "An imported module came from outside the private runtime.")
    for name, paths in probe["modules"].items():
        require(all(inside(path, RUNTIME) for path in paths), "Required module outside runtime: " + name)
    for name, path in probe["distributions"].items():
        require(inside(path, RUNTIME), "Distribution metadata outside runtime: " + name)
    for name, version in expected.items():
        require(probe["versions"].get(name) == version, "Dependency version mismatch: " + name)
    return probe


def main():
    report = {
        "status": "failed", "checkedAtUTC": datetime.now(timezone.utc).isoformat(),
        "bundle": str(APP), "workingDirectory": str(WORKING_DIRECTORY), "PATH": MINIMAL_PATH,
        "PYTHONHOME": str(RUNTIME), "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1",
        "pythonArguments": ["-B"], "isolatedTemporaryHome": True,
        "performsAPIRequests": False, "performsPDFTranslation": False,
    }
    errors = []
    before_files, before_dirs = {}, []
    environment = None
    snapshot_taken = False
    try:
        require(APP.is_dir(), "Application bundle is missing; build it before verification.")
        require(PYTHON.is_file() and os.access(PYTHON, os.X_OK), "Private Python interpreter is missing or not executable.")
        require(WORKER.is_file(), "Bundled worker is missing.")
        with tempfile.TemporaryDirectory(prefix="pagebridge-bundle-check-", dir=str(WORKING_DIRECTORY)) as home:
            # Do not inherit a venv, PYTHONPATH, proxy settings, provider secrets,
            # user caches or developer-installed Python packages.
            environment = {
                "PATH": MINIMAL_PATH, "HOME": home, "TMPDIR": home,
                "XDG_CACHE_HOME": str(Path(home) / "cache"),
                "PYTHONHOME": str(RUNTIME), "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
            }
            before_files, before_dirs = bytecode_snapshot()
            snapshot_taken = True
            report["codesignBefore"] = verify_signature(environment)
            require(report["codesignBefore"]["passed"], "Application signature failed before running verification.")
            try:
                source_info = json.loads((ROOT / "engine/runtime-info.json").read_text(encoding="utf-8"))
                bundled_info = json.loads((ENGINE / "runtime-info.json").read_text(encoding="utf-8"))
                for key in ("pythonVersion", "architecture", "versions"):
                    require(bundled_info.get(key) == source_info.get(key), "Bundled runtime metadata differs from source: " + key)
                expected = dict(source_info["versions"])
                expected.update(worker_expected_versions())
                report["expectedVersions"] = expected
                report["workerSHA256"] = sha256(WORKER)
                report["sourceWorkerSHA256"] = sha256(SOURCE_WORKER)
                report["sourceWorkerMatches"] = report["workerSHA256"] == report["sourceWorkerSHA256"]
                require(report["sourceWorkerMatches"], "Bundled worker differs from current worker source.")
                guard = ENGINE / "translation_guard.py"
                require(guard.is_file(), "Bundled translation guard is missing.")
                report["guardSHA256"] = sha256(guard)
                report["sourceGuardMatches"] = sha256(ROOT / "engine/translation_guard.py") == report["guardSHA256"]
                require(report["sourceGuardMatches"], "Bundled translation guard differs from current source.")
                layout_guard = ENGINE / "layout_guard.py"
                require(layout_guard.is_file(), "Bundled layout guard is missing.")
                report["layoutGuardSHA256"] = sha256(layout_guard)
                require(report["layoutGuardSHA256"] == sha256(ROOT / "engine/layout_guard.py"), "Bundled layout guard differs from current source.")
                report['engineModulesSHA256'] = {}
                for name in ('worker.py', 'translation_guard.py', 'layout_guard.py', 'pdf_links.py'):
                    require((ENGINE / name).is_file(), 'Missing engine module: ' + name)
                    checksum = sha256(ENGINE / name)
                    require(checksum == sha256(ROOT / 'engine' / name), 'Bundled source differs: ' + name)
                    report['engineModulesSHA256'][name] = checksum
                resources = APP / 'Contents/Resources'
                info = plistlib.loads((APP / 'Contents/Info.plist').read_bytes())
                require(info.get('CFBundleIconFile') == 'PageBridge.icns', 'Application icon is not configured.')
                for name in ('PageBridge.icns', 'PageBridge-icon.png'):
                    target = resources / ('PageBridgeIcon.png' if name.endswith('.png') else name)
                    require(target.is_file() and sha256(target) == sha256(ROOT / 'assets' / name), 'Bundled icon differs: ' + name)
                catalog = json.loads((resources / 'demos/catalog.json').read_text())
                require(catalog == json.loads((ROOT / 'demos/catalog.json').read_text()), 'Bundled demo catalog differs.')
                for demo in catalog['papers']:
                    path = resources / 'demos' / demo['file']
                    require(inside(path, resources / 'demos') and path.is_file(), 'Invalid demo path.')
                    require(sha256(path) == demo['sha256'], 'Bundled demo checksum differs: ' + demo['file'])
                report['resources'] = {'iconMatches': True, 'demoCount': len(catalog['papers']), 'demosVerified': True}
                health, events = verify_health(environment)
                report["health"] = health
                report["healthEvents"] = events
                expected_health = "; ".join(name + " " + version for name, version in worker_expected_versions().items())
                require(health.get("engineVersion") == expected_health, "Worker health returned unexpected dependency versions.")
                require(events[0].get("engineVersion") == expected_health, "Worker ready version does not match its result.")
                probe = verify_imports(environment, expected)
                require(probe["pythonVersion"] == source_info["pythonVersion"], "Bundled Python version does not match the locked runtime.")
                require(probe["architecture"] == source_info["architecture"], "Bundled runtime architecture does not match metadata.")
                report["paths"] = {"prefix": probe["prefix"], "engine": probe["modules"]["pdf2zh_next"][0], "executable": probe["executable"]}
                report["runtimeProbe"] = probe
                profile_path = ENGINE / "bundle-profile.json"
                require(profile_path.is_file(), "Native runtime profile is missing from the application.")
                profile = json.loads(profile_path.read_text(encoding="utf-8"))
                source_profile = json.loads((ROOT / "scripts/bundle-profile.json").read_text(encoding="utf-8"))
                require(profile == source_profile, "Bundled runtime profile differs from current source.")
                canonical = lambda value: re.sub(r"[-_.]+", "-", value).lower()
                installed = {canonical(name) for name in probe["installedDistributions"]}
                excluded = {canonical(name) for names in profile["excludedDistributions"].values() for name in names}
                require(not installed.intersection(excluded), "An excluded dependency remains in the application.")
                report["runtimeProfile"] = {"name": profile["profile"], "sourceMatches": True,
                                            "excludedDistributionsAbsent": True, "excludedCount": len(excluded)}
            finally:
                report["codesignAfter"] = verify_signature(environment)
                require(report["codesignAfter"]["passed"], "Application signature failed after running verification.")
    except Exception as error:
        errors.append(str(error))
    finally:
        if snapshot_taken:
            try:
                after_files, after_dirs = bytecode_snapshot()
                changed = sorted(path for path, value in after_files.items() if before_files.get(path) != value)
                removed = sorted(set(before_files) - set(after_files))
                new_dirs = sorted(set(after_dirs) - set(before_dirs))
                no_writes = not changed and not removed and not new_dirs
                report["bytecode"] = {
                    "beforeCount": len(before_files), "afterCount": len(after_files),
                    "newOrChangedFiles": changed, "removedFiles": removed,
                    "newCacheDirectories": new_dirs, "noBytecodeWritten": no_writes,
                }
                if not no_writes:
                    errors.append("Runtime verification modified bytecode files or created __pycache__ directories inside the application.")
            except Exception as error:
                errors.append("Could not inspect bytecode after verification: " + str(error))
        report["status"] = "failed" if errors else "passed"
        if errors:
            report["errors"] = errors
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        temporary = OUTPUT.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        temporary.replace(OUTPUT)
    if errors:
        print("Bundle verification failed; details: " + str(OUTPUT), file=sys.stderr)
        for error in errors:
            print("- " + error, file=sys.stderr)
        return 1
    print("Bundle verification passed: private imports, locked versions, matching worker, clean bytecode, valid signatures before/after.")
    print(str(OUTPUT))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=APP)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    APP = arguments.app.resolve()
    ENGINE = APP / "Contents/Resources/engine"
    RUNTIME = ENGINE / "runtime"
    PYTHON = RUNTIME / "bin/python3"
    WORKER = ENGINE / "worker.py"
    OUTPUT = arguments.output.resolve()
    raise SystemExit(main())
