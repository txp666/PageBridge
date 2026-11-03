# Structured reading engine

`worker.py` `action: "translate"` takes the usual document, output-directory, language, selected-page and service fields. It leaves the original PDF unchanged and publishes `reading.json` plus an identity/hash-bound `manifest.json` in a new attempt directory. The result event carries `artifactType: "structured-reading"`, `readingPath`, `readingSHA256`, `manifestPath`, page counts and completion counters. `pageCount` is the source PDF's total; `selectedPages` and `pages` list only the selected physical pages. The machine-readable contract is [`structured-reading.schema.json`](structured-reading.schema.json). A committed draft is exported to a translated or bilingual PDF with `export_reading` ([protocol](README.md#reading-export)).

## Parsing

The parser is the optional **MinerU 4.0.12 Basic / ONNX CPU** runtime with automatic text/OCR routing. Each selected physical page is parsed in its own subprocess; a failed page yields an explicit page fallback and parsing continues. Source coordinates are kept only when supplied reliably; a block without a display bbox is exposed through the client's unpositioned full-text entry.

- **Lists and indexes** use the original PDF's native text lines when available. Font spans within a line stay one passage; numeric section and page labels keep their own coordinates. Dot-leader index lines use their character boxes to separate title, number prefix and page-number suffix; the leaders stay in the original page. Nothing is evenly divided or inferred.
- **Tables with native text** keep the original numbers, grid and cell coordinates. Text labels become individually positioned `sourceType: "table_cell"` blocks; the parent `native_table` is not sent to the model. A label may keep bounded `translationContext` for diagnosis, but it is never sent to the translator (neighbouring numbers were copied into short labels when it was). The table prompt translates only the literal fragment and preserves units, acronyms and unmatched parentheses. Numeric tokens stay source pixels. Tables without native text keep their source region plus auxiliary translated plain text.
- **Formulas and figures** remain source content; clients render those regions from their own original PDF. No parser HTML or image path is trusted by a UI.

## Display fields

- `backgroundImage` (pages with native text): a base64 PNG rendered from an in-memory copy with only the mapped translated glyphs removed by transparent redaction. Photos, watermarks, fills, table values and fine paths remain. A paired uniform-colour soft-mask text shadow may be removed with its text. `backgroundBlocks` names the only blocks drawn on top; nothing is filled with solid rectangles.
- `foregroundColor` keeps the mapped native text colour. Unchanged text, rotated captions and headings that mix several text colours stay original pixels; their translations remain available in the detail view.
- `backgroundColor` (text, heading and list blocks): channel-wise median of samples just outside the region on a low-resolution render, in the same rotated/cropped `page.rect` space. Used for image-only scanned pages; errors default to white.
- `sourceFontSize` is added only by the Windows desktop page response (character-weighted median native font size of the region); it is not written to the artifact.
- Page-local coordinates subtract the displayed page origin after rotation. `decorationsCoordinateSpace: "page"` marks normalized decoration paths.

## Translation and retry

Every non-empty text, heading, list and table-label block is translated independently as a complete passage. Pure decimal numbers, dotted section numbers, parser `page_number` blocks and parser `code` blocks stay `preserved` and are not sent. Formula markers are mentioned in the prompt only when the block has real formula attachments; the response must contain exactly the source marker multiset. An unknown, duplicated, missing or unfinished marker, or an empty, truncated or failed response, makes only that block `source_fallback`. There is no numeric or semantic scoring and no whole-document quality gate; "completed" means processing finished.

To retry blocks, send the same request with the committed `readingPath` and `retryBlockIds: ["p00001-b00002"]`. The engine verifies the source hash and languages, reuses the stored source passages without reparsing, updates only those blocks and publishes a new complete artifact; the previous artifact is unchanged. A changed model is recorded per block. A page with no extracted blocks needs a full regeneration, which replaces the artifact and its page selection.

The pinned Basic OCR dictionary has no Arabic alphabet. When `sourceLanguage` is `ar` and a selected page has no extractable Arabic letters, that page becomes `source_fallback` with a capability message and no blocks, without OCR or translation. If every selected page is in this category, runtime preparation is not started.

## Runtime preparation

`action: "prepare_reading"` needs no translation service; a reading request also prepares the runtime when needed. For development:

```sh
printf '{"protocolVersion":1,"jobId":"dev","attemptId":"prepare","action":"prepare_reading"}\n' |
  PAGEBRIDGE_MINERU_RUNTIME="$PWD/validation/my-mineru-runtime" \
  PAGEBRIDGE_UV="$PWD/engine/.tools/bin/uv" \
  engine/.venv/bin/python engine/worker.py
```

The default location is `~/Library/Application Support/PageBridge/mineru/4.0.12` on macOS, `%LOCALAPPDATA%\PageBridge\mineru\4.0.12` on Windows and the XDG data directory elsewhere; `PAGEBRIDGE_MINERU_RUNTIME` overrides it. uv is taken from `PAGEBRIDGE_UV`, the app's bundled `tools/uv` (`uv.exe` on Windows), `engine/.tools/bin/uv` or `PATH`. The runtime has its own managed CPython 3.12.12 (`python/`), `.venv/`, models (`home/models/`) and download caches, independent of the app's Python. The app bundles only the integration files and uv, not the model weights.

Preparation creates the environment, installs [`mineru-requirements.txt`](mineru-requirements.txt) (pinned versions, separate from `uv.lock`), then downloads the 13 Basic model and configuration files listed with sizes and SHA-256 in [`mineru-models.json`](mineru-models.json) (818 MB). Models come from ModelScope first, then Hugging Face (both serve the same files; the manifest hashes are checked either way); progress is reported as `正在下载 MinerU 模型：x / 818 MB`. If a source fails or no bytes arrive for 120 seconds, the next source is tried; if both fail, the error asks the user to check the network or proxy. The latest attempt's installer output is kept in `setup.log` in the runtime folder (parser output is never logged). Files are verified, imports and package versions are checked, and `ready.json` (bound to the requirements and model-manifest hashes) is written atomically. An OS file lock prevents concurrent installation; interrupted setup resumes. On disk the runtime takes about 3.2 GB including caches.

Parsing runs offline with local models and never calls a remote parser. Python-path variables and API/token/secret environment variables are removed from child environments; translation credentials stay in the main worker's request memory and are never written to artifacts.

## Tests

`engine/tests/test_structured_reading.py` covers partial block/page completion, retry without reparsing, unchanged source identity, cancellation before publication, redirect and truncated-response handling, coordinates, model file integrity and isolated runtime preparation. The process-tree test needs process enumeration permission and is skipped in restricted sandboxes.
