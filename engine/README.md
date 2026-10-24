# PageBridge Python engine

The native app calls this worker through a versioned NDJSON pipe. The worker
uses the official `pdf2zh_next.high_level.do_translate_async_stream` API;
Swift owns import, Keychain, scheduling, cancellation deadlines, the PDFKit
reader and export. The browser prototype is not part of this runtime.

The current source targets **0.1.4, under validation**. Real-document results
and final package status belong in [the verification record](../docs/VERIFICATION.md),
not in a test-count claim that becomes stale as the code changes.

## Locked runtime

| Component | Version | Purpose |
|---|---|---|
| CPython | 3.12.12 | Private arm64 macOS runtime |
| pdf2zh-next | 2.9.0 | PDFMathTranslate-next Python API |
| BabelDOC | 0.6.2 | PDF parsing, translation and typesetting backend |
| PyMuPDF | 1.25.2 | PDF inspection and link annotations |
| bitstring | 4.3.0 | Compatible `BitStream` implementation |

[`.python-version`](.python-version) pins the interpreter patch version;
[`uv.lock`](uv.lock) records the complete dependency graph. Setup runs
`uv pip check`; do not bypass dependency conflicts with `--no-deps`.
The guards depend on the audited upstream data structures, so upgrading an
engine version requires renewed source, test and real-PDF validation.

Upstream references: [pdf2zh-next v2.9.0 dependencies](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/v2.9.0/pyproject.toml),
[BabelDOC v0.6.2 dependencies](https://github.com/funstory-ai/BabelDOC/blob/v0.6.2/pyproject.toml),
and [the Python API](https://github.com/PDFMathTranslate-next/PDFMathTranslate-next/blob/v2.9.0/docs/en/advanced/API/python.md).
Both projects use AGPL-3.0; a separate worker does not remove their license
requirements. See [third-party sources](../docs/THIRD_PARTY.md).

## Setup and packaging

Run from the repository root:

```sh
bash engine/setup.sh
# Optional: pre-download and verify upstream layout/font/CMap assets.
bash engine/setup.sh --assets
```

Setup uses an available uv or installs uv 0.9.17 beneath `engine/.tools`.
It requests managed Python 3.12.12, checks the resulting interpreter, and
writes credential-free `runtime-info.json` for packaging. Set
`PAGEBRIDGE_BOOTSTRAP_PYTHON` only when a different bootstrap Python is needed.

After the required real-PDF acceptance, `bash scripts/build-app.sh` creates the
native bundle. It copies the private Python base and selected packages, and
includes `worker.py`, `translation_guard.py`, `layout_guard.py` and
`pdf_links.py`. The profile in `scripts/bundle-profile.json` removes unused
Web UI/SDK/development resources while retaining runtime code and license
metadata. Ollama language models are not bundled.

The app launches `runtime/bin/python3 -B` with `PYTHONHOME` set to its private
runtime and bytecode writing disabled. Do not modify a signed bundle in place;
rebuild, sign and verify after changing its contents.

## Request and response protocol

Start a new worker process for each request and write exactly one UTF-8 JSON
line to stdin. Keep API keys in this private pipe and memory, never in command
arguments, environment variables or saved request files.

```json
{"protocolVersion":1,"jobId":"job-1","attemptId":"attempt-1","action":"translate","inputPath":"/absolute/source.pdf","outputDirectory":"/absolute/attempt-output","sourceLanguage":"en","targetLanguage":"zh-CN","pages":null,"service":{"kind":"ollama","provider":"ollama","baseURL":"http://127.0.0.1:11434","model":"qwen3.5:35b-a3b-int4","apiKey":"","timeoutSeconds":300,"concurrency":1}}
```

| Action | Behavior |
|---|---|
| `health` | Checks pinned versions and core native imports; no service or model request required |
| `probe` | Sends a fixed English sentence and requires Chinese in the actual response; no user PDF is sent |
| `translate` | Validates input/pages, runs the official API, validates both PDFs and publishes a result directory atomically |

`kind` is `ollama` or `openai`. Ollama accepts only loopback hosts and uses the
OpenAI-compatible `/v1` endpoint for translation. Remote OpenAI-compatible
services require HTTPS and a key. Supply the Base URL, not a full
`/chat/completions` URL. Requests support a timeout of 10–600 seconds and
concurrency of 1–8.

The local Ollama preset disables presence penalties and caps each translation
response at 8192 tokens (connection probes use 1024). Length-truncated or
content-filtered responses are rejected before the upstream text adapter can
discard their finish status. Local SDK timeout retries are disabled; explicit
paragraph repair still runs. This is an output bound, not a document-wide time
limit. Custom and online providers retain their normal SDK retry settings.

Optional `provider` identifies a preset (`deepSeek`, `alibaba`, `siliconFlow`,
`openAI`, `ollama`, `custom`). Known online presets must match their official
endpoint; different regions or gateways use `custom`. Missing provider fields
remain compatible with older saved configurations. Model-list discovery is a
separate native request initiated by the user; it is not a worker action.
Known provider/model pairs receive the corresponding request options. No
compatibility claim is made for arbitrary model IDs or reasoning modes.

`pages` is null or a range such as `1,3-5`. Both outputs retain the full input
page count: N source pages yield N translated pages and 2N bilingual pages.
Bilingual pages alternate source and translation; unselected pages remain in
their source form.

Every stdout line includes `protocolVersion`, `jobId`, `attemptId`, a monotonic
`sequence`, and `type`. Types are `ready`, `stage`, `progress`, `result`, `error`.
Progress is 0…1 and remains below completion until verification. Exactly one
terminal `result` or `error` is emitted. Successful translation results include
`engineVersion`, `translatedPath` and `bilingualPath`; health/probe results do
not include PDF paths. Fixed messages replace arbitrary upstream diagnostic
strings.

Outputs are `translated.pdf`, `bilingual.pdf`, `translation-quality.json` and
`manifest.json` inside `result-<attemptId>`. The manifest contains input/output
hashes, pages, model and engine versions, but no credentials. A new attempt ID
is required for retries; successful old attempts are not overwritten.

## Content and layout protection

`translation_guard.py` runs in the actual spawned BabelDOC translation process.
It checks placeholder structure before parsing model output, then validates
original formula objects, numeric measurements, technical identifiers,
parenthesis boundaries and supported script relationships. Ordinary English
residuals are checked separately from names, metadata, units and variables.
Recovery retains source context and original formula objects. Only narrowly
proved source relations permit reordering; a plausible-looking model answer is
not enough. Unresolved content blocks publication.

At a source-proven two-column continuation, the guard supplies bounded,
read-only prose context before batch scheduling. It takes only the continuous
source edge before the first formula or after the last formula; it never
removes a formula and joins the text around it. Ambiguous reading order,
geometry or glyph/text mapping disables that context. Context does not change
paragraph boundaries or authorize importing neighboring formulas or numbers.

Source-proven standalone `with` and `and` connectors are handled before the
upstream minimum-length filter can skip them. Complete journal footers and
publication-date metadata remain in their original form. During whole-paragraph
recovery, numeric literals become opaque tokens only when the source grammar
proves the supported two-group comparison with an elided second verb. Other
counts and years remain visible to the model. Restored output still passes the
numeric multiplicity guard; tokens and role hints do not prove semantic fidelity.

`layout_guard.py` uses source glyphs and geometry to protect source-proven
subscripts, superscripts, squared/cubed units, legacy mathematical fonts,
list markers, contents/index structure and copyright metadata. Lists that were
merged by extraction can be separated using their original line structure;
hanging indentation is handled during typesetting. Ambiguous glyphs are not
replaced with invented text. Some source structures, diagram fragments and
metadata deliberately stay in the source language.

A year range incorrectly split into three adjacent source paragraphs can be
rejoined only when the original line shares its baseline, font, size and
XObject, and its glyph adjacency is unambiguous. The repair retains original
glyph objects and their order; it does not synthesize a missing year or dash.

`pdf_links.py` captures source-to-target paragraph/line relationships and
restores supported URI and document-page links after reflow. It maps the source
and translated halves of the bilingual file separately. Source rectangle
coordinates are reused only when the visible region is proven unchanged.
Original standalone glyphs retained for copyright/index content are tracked by
object identity and complete state, so harmless PDF coordinate rounding does
not drop their links.
Ambiguous, unsupported or zero-area source annotations are counted separately;
link counts alone do not establish that a hotspot or destination is correct.
Internal destinations currently have page precision.

`translation-quality.json` records paragraph/recovery counters and link
restoration statistics without document text or API keys. Temporary link
mapping stores geometry rather than document prose or URL contents. Structural
checks do not prove semantic accuracy: final text, numbers, formulas, lists,
figures and clickable targets still need source/target review.

## Cancellation, diagnostics and storage

The worker has its own process group. Cancellation sends SIGTERM to the group,
allows cleanup, then uses SIGKILL after the parent's deadline if needed. Swift
rejects stale attempt IDs and late results after cancellation. Forced termination
may leave a temporary directory, but cannot be treated as successful publication.

Native/upstream stdout and stderr are redirected at descriptor level; only the
versioned protocol reaches the parent pipe. Classified error messages exclude
provider response bodies, document text and secrets. Probe and the pinned
translation client's HTTP redirects are rejected. Upstream rate-limit retries
can still delay a task, so cancellation remains available.

The upstream may create `~/.config/pdf2zh/default/` on import, keeps assets in
`~/.cache/babeldoc`, and has a translation cache at `~/.cache/pdf2zh_next`.
The worker constructs settings directly, disables automatic GUI configuration
saving and uses `ignore_cache=True` for translation cache reads and writes.
Historical cache files are not removed or encrypted. Do not claim that every
file lives under the app's Application Support folder.

## Verification

```sh
engine/.venv/bin/python -B -m unittest discover -s engine/tests -v
# Source-mode full-paper run: a new output directory is required.
engine/.venv/bin/python -B scripts/retranslate-pdfs.py \
  --source-engine --input demos/pdfs/avalanches.pdf \
  --output validation/engine-avalanches-run
```

Tests cover protocol and URL validation, provider options, model/secret handling,
HTTP redirect rejection, cancellation, output containment and content/layout/link
regressions. Synthetic fixtures establish those behaviors, not the quality of an
actual model translation. The full-paper runner records input and runtime module
hashes before/after the run; changed modules invalidate its acceptance evidence.

An optional short source-runtime smoke test creates an original synthetic PDF:

```sh
engine/.venv/bin/python -B engine/verify_real.py \
  --model qwen3.5:35b-a3b-int4
```

That test needs an already available local Ollama model. It verifies a functional
translation, not broad fidelity or performance. Full review and final-bundle
commands are in [the development guide](../docs/DEVELOPMENT.md). No real cloud
account is verified merely by exercising an OpenAI-compatible local endpoint.
