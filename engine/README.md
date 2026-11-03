# PageBridge Python engine

Both desktop hosts run `worker.py` as a separate process and talk to it over a versioned NDJSON pipe. The macOS Swift host owns import, Keychain, scheduling, cancellation deadlines, the PDFKit reader and export; the Windows host (`desktop/`) uses the same worker with Windows process handling.

The worker has two output modes:

- **Reflowed PDF** (`outputMode: "pdf"`, default): the official `pdf2zh_next.high_level.do_translate_async_stream` API of PDFMathTranslate-next / BabelDOC, with the content, layout and link protection described below.
- **Structured reading** (`outputMode: "reading"`): MinerU parsing plus per-passage translation, documented in [STRUCTURED_READING.md](STRUCTURED_READING.md).

## Locked runtime

| Component | Version | Purpose |
|---|---|---|
| CPython | 3.12.12 | Private runtime (macOS arm64, Windows x64) |
| pdf2zh-next | 2.9.0 | PDFMathTranslate-next Python API |
| BabelDOC | 0.6.2 | PDF parsing, translation and typesetting backend |
| PyMuPDF | 1.25.2 | PDF inspection, rendering and link annotations |
| bitstring | 4.3.0 | Compatible `BitStream` implementation |

[`.python-version`](.python-version) pins the interpreter; [`uv.lock`](uv.lock) records the full dependency graph. Setup runs `uv pip check`; do not bypass conflicts with `--no-deps`. The guards depend on audited upstream data structures, so upgrading an engine dependency requires renewed source review, tests and real-PDF validation. Upstream references: [pdf2zh-next dependencies](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/v2.9.0/pyproject.toml), [BabelDOC dependencies](https://github.com/funstory-ai/BabelDOC/blob/v0.6.2/pyproject.toml), [Python API](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/v2.9.0/docs/en/advanced/API/python.md). Both projects are AGPL-3.0; see [third-party notices](../THIRD_PARTY_NOTICES.md).

## Setup and packaging

```sh
bash engine/setup.sh
bash engine/setup.sh --assets   # optional: pre-download upstream layout/font/CMap assets
```

Setup uses an available uv or installs uv 0.9.17 under `engine/.tools`, requests managed Python 3.12.12, checks the interpreter and writes credential-free `runtime-info.json`. Set `PAGEBRIDGE_BOOTSTRAP_PYTHON` only when a different bootstrap Python is needed.

On Windows, use a checkout without the macOS `.venv` or `.tools` directories:

```powershell
.\engine\setup-windows.ps1
.\engine\setup-windows.ps1 -Assets   # optional asset warm-up
```

It selects the locked **Windows x64 CPython 3.12.12** (also on Windows 11 ARM), creates `engine/.venv/Scripts/python.exe` and `engine/runtime-info.json`, and refuses to overwrite a macOS environment. The interpreter ABI is checked with `sysconfig.get_platform()` (`win-amd64`) because `platform.machine()` reports the host under emulation.

Packaging copies the engine files listed in `scripts/bundle-profile.json` (`engineFiles`) and prunes unused Web UI, SDK and development resources while keeping runtime code and license metadata. The Mac app launches `runtime/bin/python3 -B` with `PYTHONHOME` set to its private runtime; the Windows host launches `python.exe -B -u worker.py` with UTF-8 pipes. Do not modify a signed bundle in place; rebuild, sign and verify instead.

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
| `translate` | Validates input and pages, translates (`outputMode` `pdf` or `reading`), validates and publishes a result directory atomically |

`kind` is `ollama` or `openai`. Ollama accepts only loopback hosts and uses its OpenAI-compatible `/v1` endpoint. Remote services require HTTPS and a key; supply the Base URL, not a `/chat/completions` URL. Timeouts are 10–600 seconds and concurrency 1–8. Optional `provider` names a preset (`deepSeek`, `alibaba`, `siliconFlow`, `openAI`, `ollama`, `custom`); known online presets must match their official endpoint, and other regions or gateways use `custom`. Known provider/model pairs receive matching request options; arbitrary model IDs carry no compatibility claim. Model-list discovery is a host request, not a worker action.

The local Ollama preset disables presence penalties and caps each translation response at 8192 tokens (probes: 1024). Length-truncated or content-filtered responses are rejected. Local SDK timeout retries are disabled; paragraph repair still runs.

For reflowed PDFs, `pages` is null or a range such as `1,3-5`; both outputs keep the full page count (N translated pages, 2N alternating bilingual pages) and unselected pages stay in source form. If no selected page has extractable text, the request is rejected before the translation stack loads.

Every stdout line includes `protocolVersion`, `jobId`, `attemptId`, a monotonic `sequence` and `type` (`ready`, `stage`, `progress`, `result`, `error`). Progress stays below 1 until verification, and exactly one terminal `result` or `error` is emitted. Fixed messages replace upstream diagnostic strings. A reflowed-PDF result includes `engineVersion`, `translatedPath` and `bilingualPath`; outputs are `translated.pdf`, `bilingual.pdf`, `translation-quality.json` and `manifest.json` (input/output hashes, pages, model and engine versions, no credentials) inside `result-<attemptId>`. Retries need a new attempt ID; earlier successful attempts are never overwritten.

## Content and layout protection

`translation_guard.py` runs inside the spawned BabelDOC translation process. Before a model reply is accepted it checks placeholder structure, original formula objects, numeric values and multiplicity, technical identifiers, parenthesis boundaries, script relationships and ordinary-English residuals (names, metadata, units and variables are allowed to stay). Failures trigger whole-paragraph recovery with source context; anything still unresolved blocks publication.

`layout_guard.py` uses source glyphs and geometry to protect proven subscripts, superscripts, squared/cubed units, legacy math fonts, list markers, contents and index structure, and copyright metadata. Ambiguous glyphs are never replaced with invented text.

The narrower repairs share one rule: each needs source reading order, geometry, font resources and glyph/text mapping to agree, keeps original glyph objects and target boxes, and is disabled on any ambiguity. They cover:

- read-only context from the continuous source edge of a two-column continuation (never joining text across a formula);
- a sentence split at a column, page or figure-plate boundary, completed by moving the short fragment's original glyphs to the other side;
- two short boxes holding one sentence translated once and laid out across both original boxes (skipped when a source link is involved; text must fit at no less than the larger of 75 % of the source size and 7 pt);
- mis-split headings, captions, year ranges, formula descriptors and decimals rejoined to their native objects; body-text wraps kept as separate physical lines;
- list separation by source lines and hanging indents, including legacy fonts that encode bullets as letters;
- name/diacritic glyph groups, document-defined terms and explicit quantity labels (`Power P`) constrained only where the source proves them;
- numeric tokens and English request views used only for proven grammars; the original input stays the validation authority.

`pdf_links.py` maps source paragraphs and lines to the reflowed output and restores supported URI and page links for both halves of the bilingual file. Source rectangles are reused only when the visible region is unchanged; ambiguous or zero-area annotations are counted separately, and internal destinations have page precision. Rebuilt PDFs are saved to temporary siblings and all MuPDF handles are closed before replacing outputs (required on Windows).

`translation-quality.json` records paragraph, recovery and link counters without document text or keys. Structural checks do not prove semantic accuracy; text, numbers, formulas, lists, figures and link targets still need review.

## Cancellation, diagnostics and storage

On macOS the worker leads its own process group; cancellation sends SIGTERM, then SIGKILL after the host's deadline. On Windows, a host keeps stdin open and may send this control line (also accepted on macOS):

```json
{"protocolVersion":1,"jobId":"job-1","attemptId":"attempt-1","action":"cancel"}
```

Only a matching protocol, job and attempt cancels. Graceful cancellation emits one `error` event with `stage: "cancelled"` and exits with code 130; EOF alone does not cancel. Windows hosts contain the worker in a Job Object and terminate it if cleanup exceeds the deadline. Control-pipe reads use `PeekNamedPipe` before `ReadFile`, because a pending stdin read blocked NumPy DLL initialization. Hosts reject stale attempt IDs and late results; forced termination may leave a temporary directory but never a published result.

Native and upstream stdout/stderr are redirected at descriptor level, so only the protocol reaches the host. Error messages exclude provider response bodies, document text and secrets; HTTP redirects are rejected.

The upstream may create `~/.config/pdf2zh/default/` on import, keeps assets in `~/.cache/babeldoc` and has a translation cache in `~/.cache/pdf2zh_next`. The worker builds settings directly, disables GUI config saving and sets `ignore_cache=True`; existing cache files are not removed.

## Tests and real runs

```sh
engine/.venv/bin/python -B -m unittest discover -s engine/tests -v
# Short real smoke test on a synthetic PDF (needs a local Ollama model):
engine/.venv/bin/python -B engine/verify_real.py --model qwen3.5:35b-a3b-int4
```

Tests use synthetic fixtures for protocol and URL validation, provider options, secret handling, redirects, cancellation, output containment and content/layout/link regressions; they do not measure real translation quality. Full-document runs and checks (`scripts/retranslate-pdfs.py`, `scripts/check-pdf-quality.py`) are described in the [development guide](../docs/DEVELOPMENT.md).
