"""Translate one demo page with a real local model, then export both PDFs from that draft.

Not a mock: exits nonzero unless the worker returns a reading draft and both exports
keep the page-count rule with extractable Chinese on the translated page. Needs the
MinerU runtime (PAGEBRIDGE_MINERU_RUNTIME or the app default) and local Ollama.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import pymupdf

ENGINE = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("--pdf", type=Path, default=ENGINE.parent / "demos/pdfs/avalanches.pdf")
parser.add_argument("--page", type=int, default=1)
parser.add_argument("--model", default="qwen3.5:35b-a3b-int4")
parser.add_argument("--base-url", default="http://127.0.0.1:11434")
parser.add_argument("--output", type=Path, default=ENGINE / ".artifacts" / "real-reading")
args = parser.parse_args()
source = args.pdf.resolve()
output = args.output.resolve()
output.mkdir(parents=True, exist_ok=True)
source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
with pymupdf.open(source) as pdf:
    count = pdf.page_count
    original_text = pdf[args.page - 1].get_text()


def worker(action, **fields):
    request = {"protocolVersion": 1, "jobId": "real-validation", "attemptId": "real-" + uuid.uuid4().hex[:12],
               "action": action, "inputPath": str(source), **fields}
    process = subprocess.Popen([sys.executable, "-B", str(ENGINE / "worker.py")], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
    process.stdin.close()
    events = []
    for line in process.stdout:
        events.append(json.loads(line))
        print(line.rstrip(), flush=True)
    code = process.wait()
    if code or not events or events[-1]["type"] != "result":
        raise SystemExit(f"{action} did not complete successfully: {process.stderr.read().strip()}")
    return events[-1]


started = time.monotonic()
reading = worker("translate", outputDirectory=str(output), sourceLanguage="en", targetLanguage="zh-CN", pages=str(args.page),
                 service={"kind": "ollama", "baseURL": args.base_url, "model": args.model, "apiKey": "", "timeoutSeconds": 300, "concurrency": 1})
assert reading["translatedBlocks"] > 0, "No block was translated"
report = {"model": args.model, "source": str(source), "sourceSHA256": source_hash, "page": args.page,
          "readingPath": reading["readingPath"], "translatedBlocks": reading["translatedBlocks"],
          "failedBlocks": reading["failedBlocks"], "translateSeconds": round(time.monotonic() - started, 1),
          "sourceBytes": source.stat().st_size, "exports": {}}
stem = f"{source.stem}-p{args.page}"
with pymupdf.open(source) as pdf:
    pdf[args.page - 1].get_pixmap(dpi=110).save(output / f"{stem}-original.png")
for kind, pages, translated_index in (("translated", count, args.page - 1), ("bilingual", count + 1, args.page)):
    path = output / f"{stem}-{kind}.pdf"
    result = worker("export_reading", readingPath=reading["readingPath"], outputPath=str(path), kind=kind)
    with pymupdf.open(path) as pdf:
        assert result["pageCount"] == pdf.page_count == pages, f"{kind}: {pdf.page_count} pages, expected {pages}"
        text = pdf[translated_index].get_text()
        assert re.search(r"[㐀-鿿]", text), f"{kind}: no extractable Chinese on the translated page"
        assert text != original_text, f"{kind}: translated page equals the original"
        if kind == "bilingual":
            assert pdf[args.page - 1].get_text() == original_text, "bilingual: original page changed"
        pdf[translated_index].get_pixmap(dpi=110).save(output / f"{stem}-{kind}.png")
    report["exports"][kind] = {"path": str(path), "pages": pages, "bytes": path.stat().st_size}
assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash, "Source was modified"
report["seconds"] = round(time.monotonic() - started, 1)
(output / f"{stem}-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
