#!/usr/bin/python3
"""Exercise an assembled app's real translator using the existing local Ollama.

Only the repository's original synthetic control-valve PDF is accepted. Both
Ollama and custom OpenAI-compatible modes send real requests to a loopback model,
with a placeholder key in the latter. An isolated HOME receives copies of the
existing BabelDOC assets; no user's documents, settings or caches are changed.

Usage: /usr/bin/python3 scripts/verify-slim-translation.py --app /absolute/app.app
Outputs: validation/slim-translation.json and per-run artifacts beneath
validation/slim-translation/. This verifies the worker plus byte-for-byte export
copy, not the macOS file-picker interaction or general translation quality.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import plistlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "engine/.artifacts/real-translation/control-valve-original.pdf"
FIXTURE_SHA256 = "b3aa8098ab7d147fb8e59d8aeb1ed5ddaa8b4b8ebdc413c812a2639f93b45686"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MINIMAL_PATH = "/usr/bin:/bin"

# This wrapper observes the actual bundled worker without changing it. It does
# not record HTTP headers, bodies, API keys, or translated document content.
RUN_WORKER = r'''
import httpx
import json
import pathlib
import runpy
import sys

worker, output = map(pathlib.Path, sys.argv[1:3])
sys.path.insert(0, str(worker.parent))
requests = []
send = httpx.Client.send
async_send = httpx.AsyncClient.send

def item(request):
    return {"method": request.method, "scheme": request.url.scheme,
            "host": request.url.host, "port": request.url.port,
            "path": request.url.path}

def observed_send(self, request, *args, **kwargs):
    entry = item(request)
    requests.append(entry)
    try:
        response = send(self, request, *args, **kwargs)
        entry["status"] = response.status_code
        return response
    except BaseException as error:
        entry["errorType"] = type(error).__name__
        raise

async def observed_async_send(self, request, *args, **kwargs):
    entry = item(request)
    requests.append(entry)
    try:
        response = await async_send(self, request, *args, **kwargs)
        entry["status"] = response.status_code
        return response
    except BaseException as error:
        entry["errorType"] = type(error).__name__
        raise

httpx.Client.send = observed_send
httpx.AsyncClient.send = observed_async_send
try:
    runpy.run_path(str(worker), run_name="__main__")
finally:
    files = {}
    outside = {}
    runtime = pathlib.Path(sys.prefix).resolve()
    engine = worker.parent.resolve()
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path or path.startswith("<"):
            continue
        path = pathlib.Path(path).resolve()
        files[name] = str(path)
        if not path.is_relative_to(runtime) and not path.is_relative_to(engine):
            outside[name] = str(path)
    output.write_text(json.dumps({
        "executable": str(pathlib.Path(sys.executable).resolve()),
        "prefix": str(runtime), "basePrefix": str(pathlib.Path(sys.base_prefix).resolve()),
        "dontWriteBytecode": sys.dont_write_bytecode,
        "noUserSite": bool(sys.flags.no_user_site),
        "importedFileModuleCount": len(files), "moduleFiles": files,
        "modulesOutsideRuntimeOrEngine": outside, "httpRequests": requests,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
'''

AUDIT_PDFS = r'''
import json
import pathlib
import re
import sys
import pymupdf

source, mono, dual, directory = map(pathlib.Path, sys.argv[1:5])
report = {}
with pymupdf.open(source) as original:
    assert original.is_pdf and not original.needs_pass
    assert original.page_count == 1
    source_text = original[0].get_text()
    source_rect = list(original[0].rect)
    report["sourcePages"] = original.page_count
    report["sourceHasExpectedFixtureText"] = "Control Valve - Translation Test" in source_text
    assert report["sourceHasExpectedFixtureText"]
for name, path, count in (("translated", mono, 1), ("bilingual", dual, 2)):
    with pymupdf.open(path) as pdf:
        assert pdf.is_pdf and not pdf.needs_pass and pdf.page_count == count
        texts = []
        for index, page in enumerate(pdf):
            assert list(page.rect) == source_rect
            texts.append(page.get_text())
            image = page.get_pixmap(matrix=pymupdf.Matrix(1, 1), alpha=False)
            assert image.width > 0 and image.height > 0
            image.save(directory / (name + "-page-" + str(index + 1) + ".png"))
        text = "\n".join(texts)
        cjk = len(re.findall(r"[\u3400-\u9fff]", text))
        assert cjk >= 10, "Missing meaningful Chinese output"
        values = {number: bool(re.search(r"(?<![\d.])" + re.escape(number) + r"(?![\d.])", text))
                  for number in ("2.0", "25", "10")}
        assert all(values.values()), "Expected source numbers missing"
        if name == "translated":
            assert re.sub(r"\s+", "", text) != re.sub(r"\s+", "", source_text)
        else:
            assert texts[0] == source_text, "Bilingual original page changed"
        report[name] = {"pages": pdf.page_count, "renderedPages": pdf.page_count,
                        "cjkCharacters": cjk, "sourceNumbersPresent": values,
                        "pageSizePreserved": True,
                        "originalPageTextPreserved": texts[0] == source_text if name == "bilingual" else None}
print(json.dumps(report, ensure_ascii=False))
'''


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def relative_to(path, directory):
    try:
        Path(path).resolve().relative_to(directory.resolve())
        return True
    except ValueError:
        return False


def bytecode_files(app):
    return sorted(str(path.relative_to(app)) for path in app.rglob("*")
                  if path.is_file() and path.suffix in (".pyc", ".pyo"))


def codesign(app, environment):
    completed = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                               env=environment, cwd="/private/tmp", capture_output=True,
                               text=True, timeout=180)
    return {"passed": completed.returncode == 0, "exitCode": completed.returncode,
            "diagnostic": (completed.stdout + completed.stderr).strip()}


def local_model(base_url, model):
    parts = urlsplit(base_url)
    require(parts.scheme == "http" and parts.hostname in LOCAL_HOSTS,
            "Verification only permits a local HTTP Ollama server.")
    require(not parts.username and not parts.password and not parts.query and not parts.fragment,
            "Verification URL must not contain credentials, a query or a fragment.")
    require(parts.path in ("", "/", "/api", "/api/", "/v1", "/v1/"),
            "Verification URL must be the local Ollama base URL.")
    origin = parts.scheme + "://" + parts.netloc
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(origin + "/api/tags"), timeout=15) as response:
        models = json.load(response)["models"]
    require(any(item.get("name") == model or item.get("model") == model for item in models),
            "The requested model is not already installed in local Ollama.")
    return origin


class LocalModelProxy:
    """Observe model HTTP calls from both the parent and spawned workers."""

    def __init__(self, origin, model):
        self.origin, self.model = origin, model
        self.requests = []
        self.lock = threading.Lock()
        owner = self

        class NoRedirect(HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                entry = {"method": "POST", "path": self.path,
                         "upstream": owner.origin, "status": None}
                with owner.lock:
                    owner.requests.append(entry)
                if self.path != "/v1/chat/completions":
                    self.send_error(404)
                    entry["status"] = 404
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    require(0 < length <= 2 * 1024 * 1024, "Invalid request size.")
                    body = self.rfile.read(length)
                    payload = json.loads(body)
                    require(payload.get("model") == owner.model, "Unexpected model.")
                    entry["requestedModelMatches"] = True
                    headers = {"Content-Type": "application/json"}
                    if self.headers.get("Authorization"):
                        headers["Authorization"] = self.headers["Authorization"]
                    opener = build_opener(ProxyHandler({}), NoRedirect())
                    request = Request(owner.origin + "/v1/chat/completions", data=body, headers=headers)
                    try:
                        with opener.open(request, timeout=600) as response:
                            status, result = response.status, response.read()
                            content_type = response.headers.get("Content-Type", "application/json")
                    except HTTPError as error:
                        status, result = error.code, error.read()
                        content_type = error.headers.get("Content-Type", "application/json")
                    entry["status"] = status
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(result)))
                    self.end_headers()
                    self.wfile.write(result)
                except Exception as error:
                    entry["errorType"] = type(error).__name__
                    if entry["status"] is None:
                        entry["status"] = 502
                        self.send_error(502, "Local verification proxy failed")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base_url = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def snapshot(self):
        with self.lock:
            return [dict(entry) for entry in self.requests]


def parse_events(stdout, request, destination):
    require(stdout.endswith("\n"), "Worker protocol did not end with a newline.")
    destination.write_text(stdout, encoding="utf-8")
    events, last_sequence, terminal, last_progress = [], 0, False, 0.0
    for line in stdout.splitlines():
        event = json.loads(line)
        require(isinstance(event, dict), "Worker event is not a JSON object.")
        require(not terminal, "Worker emitted an event after the terminal event.")
        require(all(event.get(key) == request[key] for key in ("protocolVersion", "jobId", "attemptId")),
                "Worker event identity differs from the request.")
        sequence = event.get("sequence")
        require(isinstance(sequence, int) and not isinstance(sequence, bool) and sequence > last_sequence,
                "Worker event sequence is not strictly increasing.")
        kind = event.get("type")
        require(kind in ("ready", "stage", "progress", "result", "error"), "Unknown worker event.")
        require(events or kind == "ready", "Worker did not start with a ready handshake.")
        if kind == "progress":
            progress = event.get("progress")
            require(isinstance(progress, (float, int)) and last_progress <= progress <= 1.0,
                    "Worker progress is invalid or regressed.")
            last_progress = progress
        last_sequence = sequence
        terminal = kind in ("result", "error")
        events.append(event)
    require(events and terminal, "Worker did not produce a terminal event.")
    require(events[-1]["type"] == "result", "Worker failed: " + str(events[-1].get("message", "unknown")))
    return events


def run_mode(kind, proxy, model, directory, python, worker, runtime, environment, timeout):
    directory.mkdir(parents=True)
    request = {"protocolVersion": 1, "jobId": "slim-validation-" + kind,
               "attemptId": "slim-" + uuid.uuid4().hex[:12], "action": "translate",
               "inputPath": str(FIXTURE), "outputDirectory": str(directory),
               "sourceLanguage": "en", "targetLanguage": "zh-CN", "pages": None,
               "service": {"kind": kind, "baseURL": proxy.base_url + ("/v1" if kind == "openai" else ""),
                           "model": model, "apiKey": "local-verification" if kind == "openai" else "",
                           "timeoutSeconds": 300, "concurrency": 1}}
    provenance_path = directory / "runtime-provenance.json"
    request_count_before = len(proxy.snapshot())
    started = time.monotonic()
    process = subprocess.Popen([str(python), "-B", "-u", "-c", RUN_WORKER, str(worker), str(provenance_path)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, env=environment, cwd="/private/tmp", start_new_session=True)
    try:
        stdout, stderr = process.communicate(json.dumps(request) + "\n", timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate(timeout=10)
        (directory / "events.ndjson").write_text(stdout, encoding="utf-8")
        raise RuntimeError(kind + " translation exceeded the verification deadline.")
    seconds = round(time.monotonic() - started, 2)
    (directory / "worker-diagnostics.txt").write_text(stderr, encoding="utf-8")
    events = parse_events(stdout, request, directory / "events.ndjson")
    require(process.returncode == 0, kind + " worker returned a nonzero exit code.")
    result = events[-1]
    mono, dual = Path(result["translatedPath"]), Path(result["bilingualPath"])
    published = directory / ("result-" + request["attemptId"])
    require(mono == published / "translated.pdf" and dual == published / "bilingual.pdf",
            "Worker returned an unexpected output location.")
    require(relative_to(mono, directory) and relative_to(dual, directory), "Output escaped the run directory.")
    manifest = json.loads((published / "manifest.json").read_text(encoding="utf-8"))
    for key in ("protocolVersion", "jobId", "attemptId", "pages", "sourceLanguage", "targetLanguage"):
        require(manifest.get(key) == request[key], "Manifest mismatch: " + key)
    require(manifest.get("sourceSHA256") == FIXTURE_SHA256, "Manifest source hash is wrong.")
    require(manifest.get("sourcePages") == 1 and manifest.get("model") == model, "Manifest source/model mismatch.")
    require(all(token not in json.dumps(manifest).lower() for token in ("apikey", "local-verification")),
            "Manifest contains the placeholder credential.")
    hashes = {"translated": sha256(mono), "bilingual": sha256(dual)}
    require(manifest.get("translatedSHA256") == hashes["translated"] and
            manifest.get("bilingualSHA256") == hashes["bilingual"], "Published hashes differ from manifest.")
    require(all(value != FIXTURE_SHA256 for value in hashes.values()), "Output equals the input PDF.")
    expected_version = "; ".join(name + " " + version for name, version in manifest["engineVersions"].items())
    require(result.get("engineVersion") == expected_version, "Result engine version differs from manifest.")
    audit = subprocess.run([str(python), "-B", "-c", AUDIT_PDFS, str(FIXTURE), str(mono), str(dual), str(directory)],
                           env=environment, cwd="/private/tmp", capture_output=True, text=True, timeout=180)
    require(audit.returncode == 0, "PDF integrity/text/render audit failed: " + audit.stderr.strip())
    pdf_checks = json.loads(audit.stdout)
    export_directory = directory / "exports"
    export_directory.mkdir()
    export_hashes = {}
    for name, source in (("translated", mono), ("bilingual", dual)):
        export = export_directory / ("control-valve-" + name + ".pdf")
        shutil.copyfile(source, export)
        export_hashes[name] = sha256(export)
    require(export_hashes == hashes, "Export copies differ from committed outputs.")
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    require(Path(provenance["prefix"]).resolve() == runtime.resolve() and
            Path(provenance["basePrefix"]).resolve() == runtime.resolve(), "Worker used an external Python runtime.")
    require(provenance["dontWriteBytecode"] and provenance["noUserSite"], "Worker isolation flags are absent.")
    require(not provenance["modulesOutsideRuntimeOrEngine"], "Worker imported modules from outside the application.")
    model_requests = proxy.snapshot()[request_count_before:]
    write_json(directory / "model-http-requests.json", model_requests)
    completions = [entry for entry in model_requests
                   if entry["method"] == "POST" and entry["path"] == "/v1/chat/completions"
                   and entry.get("status") == 200 and entry.get("requestedModelMatches")]
    require(completions, "No successful real local model HTTP request was observed.")
    require(sha256(FIXTURE) == FIXTURE_SHA256, "Original test fixture changed.")
    return {"status": "passed", "serviceKind": kind, "model": model, "seconds": seconds,
            "outputDirectory": str(directory), "translatedPath": str(mono), "bilingualPath": str(dual),
            "events": len(events), "engineVersion": result["engineVersion"], "pdfChecks": pdf_checks,
            "sourceUnchanged": True, "manifestHashesMatch": True, "outputSHA256": hashes,
            "exportSHA256": export_hashes, "exportMethod": "byte-for-byte file copy; no native save dialog",
            "successfulLocalChatCompletions": len(completions), "realModelRequestsObserved": True,
            "modelRequestObserver": "loopback forwarding proxy covering parent and spawned workers",
            "modelHTTPRequestLog": str(directory / "model-http-requests.json"),
            "runtimeProvenance": str(provenance_path),
            "runtimeProvenanceScope": "worker parent process; spawned-child imports are not enumerated",
            "importedFileModuleCount": provenance["importedFileModuleCount"],
            "modulesOutsideRuntimeOrEngine": provenance["modulesOutsideRuntimeOrEngine"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, default=ROOT / "dist/PageBridge.app")
    parser.add_argument("--model", default="qwen3.5:35b-a3b-int4")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--timeout", type=int, default=1200, help="Per-mode wall-clock deadline, in seconds")
    parser.add_argument("--output", type=Path, default=ROOT / "validation/slim-translation")
    parser.add_argument("--report", type=Path, default=ROOT / "validation/slim-translation.json")
    args = parser.parse_args()
    app = args.app.expanduser().resolve()
    engine = app / "Contents/Resources/engine"
    runtime = engine / "runtime"
    python, worker = runtime / "bin/python3", engine / "worker.py"
    run_directory = args.output.resolve() / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8])
    report = {"status": "failed", "checkedAtUTC": datetime.now(timezone.utc).isoformat(),
              "app": str(app), "artifactDirectory": str(run_directory), "sourceFixture": str(FIXTURE),
              "sourceSHA256": FIXTURE_SHA256, "model": args.model, "modes": [],
              "userDocumentsUsed": False, "realAPICredentialsUsed": False,
              "PATH": MINIMAL_PATH, "isolatedTemporaryHome": True,
              "assetCacheMethod": "copy existing BabelDOC assets into temporary HOME; original cache is untouched",
              "translationCacheInitiallyEmpty": True, "pythonArguments": ["-B"],
              "sourceCachePath": str(Path.home() / ".cache/babeldoc")}
    errors = []
    environment, before_bytecode = None, None
    try:
        require(app.is_dir() and python.is_file() and worker.is_file(), "Build the app before verification.")
        require(FIXTURE.is_file() and sha256(FIXTURE) == FIXTURE_SHA256,
                "The original synthetic test PDF is missing or changed; no alternative/user PDF will be used.")
        require(args.timeout >= 60, "Per-mode timeout must be at least 60 seconds.")
        origin = local_model(args.base_url, args.model)
        report["localModelBaseURL"] = origin
        with (app / "Contents/Info.plist").open("rb") as stream:
            info = plistlib.load(stream)
        report["appVersion"] = info.get("CFBundleShortVersionString")
        report["appBuild"] = info.get("CFBundleVersion")
        report["workerSHA256"] = sha256(worker)
        with tempfile.TemporaryDirectory(prefix="pagebridge-slim-check-", dir="/private/tmp") as home_name:
            home = Path(home_name)
            assets = Path.home() / ".cache/babeldoc"
            require(assets.is_dir(), "Existing BabelDOC asset cache was not found; prepare assets first.")
            shutil.copytree(assets, home / ".cache/babeldoc")
            environment = {"PATH": MINIMAL_PATH, "HOME": str(home), "TMPDIR": str(home),
                           "XDG_CACHE_HOME": str(home / ".cache"), "PYTHONHOME": str(runtime),
                           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
            before_bytecode = bytecode_files(app)
            report["codesignBefore"] = codesign(app, environment)
            require(report["codesignBefore"]["passed"], "App signature was invalid before translation.")
            try:
                with LocalModelProxy(origin, args.model) as proxy:
                    for kind in ("ollama", "openai"):
                        print("Running real bundled translation: " + kind, flush=True)
                        mode_directory = run_directory / kind
                        requests_before = len(proxy.snapshot())
                        try:
                            mode = run_mode(kind, proxy, args.model, mode_directory, python, worker, runtime,
                                            environment, args.timeout)
                        except Exception as error:
                            mode = {"status": "failed", "serviceKind": kind,
                                    "outputDirectory": str(mode_directory), "error": str(error)}
                            errors.append(kind + ": " + str(error))
                        finally:
                            write_json(mode_directory / "model-http-requests.json",
                                       proxy.snapshot()[requests_before:])
                        report["modes"].append(mode)
                        print(kind + ": " + mode["status"], flush=True)
                        write_json(args.report.resolve(), report)
            finally:
                report["codesignAfter"] = codesign(app, environment)
                if not report["codesignAfter"]["passed"]:
                    errors.append("App signature failed after translation.")
    except Exception as error:
        errors.append(str(error))
    finally:
        if before_bytecode is not None:
            after_bytecode = bytecode_files(app)
            report["bytecode"] = {"beforeCount": len(before_bytecode), "afterCount": len(after_bytecode),
                                  "noNewBytecode": before_bytecode == after_bytecode}
            if before_bytecode != after_bytecode:
                errors.append("Verification created or removed bytecode in the application.")
        report["sourceUnchanged"] = FIXTURE.is_file() and sha256(FIXTURE) == FIXTURE_SHA256
        if not report["sourceUnchanged"]:
            errors.append("Original synthetic PDF changed.")
        report["status"] = "failed" if errors else "passed"
        report["errors"] = errors
        write_json(args.report.resolve(), report)
    print(str(args.report.resolve()), flush=True)
    for error in errors:
        print(error, file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
