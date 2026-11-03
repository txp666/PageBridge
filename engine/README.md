# PageBridge Python engine

Both desktop hosts run `worker.py` as a separate process and talk to it over a versioned NDJSON pipe. The macOS Swift host owns import, Keychain, scheduling, cancellation deadlines, the PDFKit reader and save dialogs; the Windows host (`desktop/`) uses the same worker with Windows process handling.

`translate` produces a structured reading draft (MinerU parsing plus per-passage translation, documented in [STRUCTURED_READING.md](STRUCTURED_READING.md)); `export_reading` turns a committed draft into a translated or bilingual PDF.

## Locked runtime

| Component | Version | Purpose |
|---|---|---|
| CPython | 3.12.12 | Private runtime (macOS arm64, Windows x64) |
| PyMuPDF | 1.25.2 | PDF inspection, rendering, reading backgrounds and PDF export (AGPL-3.0) |
| httpx | 0.28.1 | Chat Completions requests |
| psutil | 7.2.2 | Stopping a timed-out page parser's process tree |
| cryptography | 50.0.2 | Windows update signatures (`desktop/pagebridge/updates.py`) and release scripts; not used by the worker |

[`.python-version`](.python-version) pins the interpreter; [`uv.lock`](uv.lock) records the full dependency graph. Setup runs `uv pip check`; do not bypass conflicts with `--no-deps`. Changing a pinned version also means updating `EXPECTED` in `worker.py` and `auditedEngineVersions` in `scripts/bundle-profile.json`; packaging rejects a mismatch. The MinerU parser has its own runtime and lock (see [STRUCTURED_READING.md](STRUCTURED_READING.md#runtime-preparation)). Licences: [third-party notices](../THIRD_PARTY_NOTICES.md).

## Setup and packaging

```sh
bash engine/setup.sh
```

Setup uses an available uv or installs uv 0.9.17 under `engine/.tools`, requests managed Python 3.12.12, checks the interpreter and writes credential-free `runtime-info.json`. Set `PAGEBRIDGE_BOOTSTRAP_PYTHON` only when a different bootstrap Python is needed.

On Windows, use a checkout without the macOS `.venv` or `.tools` directories:

```powershell
.\engine\setup-windows.ps1
```

It selects the locked **Windows x64 CPython 3.12.12** (also on Windows 11 ARM), creates `engine/.venv/Scripts/python.exe` and `engine/runtime-info.json`, and refuses to overwrite a macOS environment. The interpreter ABI is checked with `sysconfig.get_platform()` (`win-amd64`) because `platform.machine()` reports the host under emulation.

Packaging copies the engine files listed in `scripts/bundle-profile.json` (`engineFiles`) and prunes pip, package tests and development resources while keeping runtime code and license metadata. The Mac app launches `runtime/bin/python3 -B` with `PYTHONHOME` set to its private runtime; the Windows host launches `python.exe -B -u worker.py` with UTF-8 pipes. Do not modify a signed bundle in place; rebuild, sign and verify instead.

## Request and response protocol

Start one worker process per request and write one UTF-8 JSON line to stdin. API keys stay in this pipe and in memory, never in arguments, environment variables or saved request files.

```json
{"protocolVersion":1,"jobId":"job-1","attemptId":"attempt-1","action":"translate","inputPath":"/absolute/source.pdf","outputDirectory":"/absolute/attempt-output","sourceLanguage":"en","targetLanguage":"zh-CN","pages":null,"service":{"kind":"ollama","provider":"ollama","baseURL":"http://127.0.0.1:11434","model":"qwen3.5:35b-a3b-int4","apiKey":"","timeoutSeconds":300,"concurrency":1}}
```

| Action | Behaviour |
|---|---|
| `health` | Checks pinned versions and native imports; no service request |
| `probe` | Sends a fixed English sentence and requires Chinese in the response; no user PDF is sent |
| `prepare_reading` | Installs the MinerU runtime if needed; no service required |
| `translate` | Validates input and pages, parses and translates a reading draft and publishes a result directory atomically |
| `export_reading` | Writes a translated or bilingual PDF from a committed reading draft; no service or network |

`kind` is `ollama` or `openai`. Ollama accepts only loopback hosts and uses its OpenAI-compatible `/v1` endpoint. Remote services require HTTPS and a key; supply the Base URL, not a `/chat/completions` URL. Timeouts are 10–600 seconds and concurrency 1–8. Optional `provider` names a preset (`deepSeek`, `alibaba`, `siliconFlow`, `openAI`, `ollama`, `custom`); known online presets must match their official endpoint, and other regions or gateways use `custom`. Known provider/model pairs receive matching request options; arbitrary model IDs carry no compatibility claim. Model-list discovery is a host request, not a worker action.

The local Ollama preset disables presence penalties and caps each translation response at 8192 tokens (probes: 1024). Length-truncated or content-filtered responses are rejected.

`pages` is null (all pages) or a range such as `1,3-5`.

Every stdout line includes `protocolVersion`, `jobId`, `attemptId`, a monotonic `sequence` and `type` (`ready`, `stage`, `progress`, `result`, `error`). Progress stays below 1 until verification, and exactly one terminal `result` or `error` is emitted; results carry `engineVersion`. Fixed messages replace upstream diagnostic strings. Retries need a new attempt ID; earlier successful attempts are never overwritten.

### Reading export

```json
{"protocolVersion":1,"jobId":"job-2","attemptId":"attempt-2","action":"export_reading","inputPath":"/absolute/source.pdf","readingPath":"/absolute/attempt-output/result-attempt-1/reading.json","outputPath":"/absolute/Exports/source-translated.pdf","kind":"translated"}
```

`kind` is `translated` (same page count and order; unselected pages are copied unchanged) or `bilingual` (every page of the original, each selected page followed by its translation). The draft must be a structured-reading artifact (`schemaVersion` 1) whose `sourceSHA256` and `pageCount` match the PDF. `outputPath` must end in `.pdf`, have an existing parent folder and differ from `inputPath`. The PDF is written to a temporary file in the target folder, reopened to check its page count and then renamed over the target, so the target is never half-written. Events are one `progress` per selected page (`正在导出第 n / N 页`), a `save` stage, then `result` with `artifactType: "reading-export"`, `outputPath` and `pageCount`. How pages are built is described in [ARCHITECTURE.md](../docs/ARCHITECTURE.md#导出译文-pdf-与双语-pdf).

## Cancellation, diagnostics and storage

On macOS the worker leads its own process group; cancellation sends SIGTERM, then SIGKILL after the host's deadline. On Windows, a host keeps stdin open and may send this control line (also accepted on macOS):

```json
{"protocolVersion":1,"jobId":"job-1","attemptId":"attempt-1","action":"cancel"}
```

Only a matching protocol, job and attempt cancels. Graceful cancellation emits one `error` event with `stage: "cancelled"` and exits with code 130; EOF alone does not cancel. Windows hosts contain the worker in a Job Object and terminate it if cleanup exceeds the deadline. Control-pipe reads use `PeekNamedPipe` before `ReadFile`, so a pending stdin read cannot block native DLL initialization. Hosts reject stale attempt IDs and late results; forced termination may leave a temporary directory but never a published result.

Native and upstream stdout/stderr are redirected at descriptor level, so only the protocol reaches the host. Error messages exclude provider response bodies, document text and secrets; HTTP redirects are rejected.

## Tests and real runs

```sh
engine/.venv/bin/python -B -m unittest discover -s engine/tests -v
# Real check: translate page 1 of a demo with local Ollama, then export both PDFs (needs the MinerU runtime):
engine/.venv/bin/python -B engine/verify_real.py --page 1
```

Tests use synthetic fixtures and demo PDFs for protocol and URL validation, provider options, secret handling, redirects, cancellation, output containment, reading backgrounds and export page rules; they do not measure real translation quality. The real check's options are described in the [development guide](../docs/DEVELOPMENT.md#mineru-阅读稿).
