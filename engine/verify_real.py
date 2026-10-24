"""Create an original test PDF and verify a real local-model translation.

Not a mock: exits nonzero unless the worker returns readable, changed PDFs
containing Chinese. Requests use no user's documents or API credentials.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import pymupdf

parser = argparse.ArgumentParser()
parser.add_argument("--model", default="qwen3.5:35b-a3b-int4")
parser.add_argument("--base-url", default="http://127.0.0.1:11434")
parser.add_argument("--kind", default="ollama", choices=("ollama", "openai"), help="OpenAI mode uses a dummy key against a local compatible server only")
parser.add_argument("--pages", default=None, help="Optional page selection, e.g. 2")
parser.add_argument("--source-pages", type=int, default=1, choices=(1, 2))
parser.add_argument("--output", type=Path, default=Path(__file__).parent / ".artifacts" / "real-translation")
args = parser.parse_args()
if args.kind == "openai":
    from urllib.parse import urlsplit
    if urlsplit(args.base_url).hostname not in ("localhost", "127.0.0.1", "::1"):
        parser.error("This validation script permits OpenAI-compatible mode only on loopback")
args.output.mkdir(parents=True, exist_ok=True)
source = args.output.resolve() / "control-valve-original.pdf"
with pymupdf.open() as pdf:
    page = pdf.new_page()
    page.insert_text((60, 80), "Control Valve - Translation Test", fontsize=18)
    page.insert_textbox(pymupdf.Rect(60, 120, 530, 340),
                        "The control valve regulates water flow in the pipeline.\n\n"
                        "The pressure sensor measures the inlet pressure. The controller adjusts the valve opening.\n\n"
                        "Test conditions: pressure 2.0 bar, temperature 25 C, flow rate 10 m3/h.\n\n"
                        "Always disconnect power before maintenance.", fontsize=12)
    if args.source_pages == 2:
        page = pdf.new_page()
        page.insert_text((60, 80), "Pressure Sensor - Selected Page", fontsize=18)
        page.insert_textbox(pymupdf.Rect(60, 120, 530, 340),
                            "The pressure sensor converts process pressure into an electrical signal.\n\n"
                            "The operating range is 0 to 10 bar. Check the zero point before calibration.", fontsize=12)
    pdf.save(source)
source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
attempt = "real-" + uuid.uuid4().hex[:12]
request = {"protocolVersion": 1, "jobId": "real-validation", "attemptId": attempt, "action": "translate",
           "inputPath": str(source), "outputDirectory": str(args.output.resolve()), "sourceLanguage": "en", "targetLanguage": "zh-CN", "pages": args.pages,
           "service": {"kind": args.kind, "baseURL": args.base_url, "model": args.model, "apiKey": "local-verification" if args.kind == "openai" else "", "timeoutSeconds": 300, "concurrency": 1}}
started = time.monotonic()
process = subprocess.Popen([sys.executable, str(Path(__file__).parent / "worker.py")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
process.stdin.write(json.dumps(request) + "\n")
process.stdin.close()
events = []
for line in process.stdout:
    event = json.loads(line)
    events.append(event)
    print(line.rstrip(), flush=True)
exit_code = process.wait()
diagnostics = process.stderr.read()
if diagnostics:
    print(diagnostics, file=sys.stderr)
(args.output / "events.ndjson").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events))
if exit_code or not events or events[-1]["type"] != "result":
    raise SystemExit("Real translation did not complete successfully.")
result = events[-1]
translated = Path(result["translatedPath"])
with pymupdf.open(translated) as pdf:
    text = "\n".join(page.get_text() for page in pdf)
    preview_index = 1 if args.source_pages == 2 and args.pages == "2" else 0
    pdf[preview_index].get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5)).save(args.output / "translated-page.png")
    assert pdf.page_count == args.source_pages
    if args.source_pages == 2 and args.pages == "2":
        with pymupdf.open(source) as original:
            assert pdf[0].get_text() == original[0].get_text(), "Unselected page was changed"
assert any("\u3400" <= char <= "\u9fff" for char in text), "Translated PDF has no Chinese text"
assert hashlib.sha256(translated.read_bytes()).hexdigest() != source_hash, "Output equals source"
assert hashlib.sha256(source.read_bytes()).hexdigest() == source_hash, "Source was modified"
with pymupdf.open(result["bilingualPath"]) as pdf:
    assert pdf.page_count == args.source_pages * 2
report = {"verifiedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "seconds": round(time.monotonic() - started, 2), "model": args.model,
          "sourceSHA256": source_hash, "translatedPath": str(translated), "bilingualPath": result["bilingualPath"], "translatedText": text,
          "engineVersion": result.get("engineVersion"), "serviceKind": args.kind, "sourcePages": args.source_pages, "selectedPages": args.pages,
          "sourceUnchanged": True, "realModelRequest": True}
(args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
print(json.dumps(report, ensure_ascii=False, indent=2))
