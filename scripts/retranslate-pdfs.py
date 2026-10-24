#!/usr/bin/env python3
"""Run explicit local PDFs through the bundled worker and retain review evidence.

No UI/store mutation, no external service, no API keys. Results are published by
the worker only after its source-aware paragraph and final-PDF quality gates.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def stop_worker_group(process):
    """Terminate only the session created for this run, including descendants."""
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    # The leader can exit before a stuck child. The process group remains ours
    # until its last descendant exits, so clean it up even if poll() is done.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def cancel_on_termination(_signal, _frame):
    raise KeyboardInterrupt('Translation runner terminated.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    runtime = parser.add_mutually_exclusive_group()
    runtime.add_argument('--app', type=Path, help='Use a signed app bundle (the default).')
    runtime.add_argument('--source-engine', action='store_true',
                         help='Validate engine source in its locked development venv before packaging.')
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--pages')
    parser.add_argument('--model', default='qwen3.5:35b-a3b-int4')
    parser.add_argument('--service', choices=['ollama', 'openai'], default='ollama')
    args = parser.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    app = None if args.source_engine else (args.app or ROOT / 'dist/PageBridge.app').resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        parser.error('Output must be a new directory inside this checkout.')
    if not source.is_file():
        parser.error('Input PDF is missing.')
    engine = ROOT / 'engine' if args.source_engine else app / 'Contents/Resources/engine'
    python = engine / ('.venv/bin/python' if args.source_engine else 'runtime/bin/python3')
    if app:
        subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    output.mkdir(parents=True)
    attempt = str(uuid.uuid4()).upper()
    request = dict(protocolVersion=1, action='translate', jobId=str(uuid.uuid4()).upper(),
                   attemptId=attempt, inputPath=str(source), outputDirectory=str(output),
                   sourceLanguage='en', targetLanguage='zh', pages=args.pages,
                   service=dict(kind=args.service, baseURL='http://127.0.0.1:11434/v1',
                                model=args.model, apiKey='local-validation',
                                timeoutSeconds=300, concurrency=2))
    env = {k: v for k, v in os.environ.items() if not (k.endswith(('API_KEY', 'API_TOKEN')) or
           k in ('PYTHONPATH', 'PYTHONHOME', 'VIRTUAL_ENV'))}
    env.update(PYTHONNOUSERSITE='1',
               PYTHONDONTWRITEBYTECODE='1', PATH='/usr/bin:/bin', PYTHONUNBUFFERED='1')
    if app:
        env['PYTHONHOME'] = str(engine / 'runtime')
    source_hash = digest(source)
    source_files = {'workerSHA256': engine / 'worker.py',
                    'guardSHA256': engine / 'translation_guard.py',
                    'layoutGuardSHA256': engine / 'layout_guard.py'}
    runtime_hashes = {key: digest(path) for key, path in source_files.items()}
    module_hashes = {p.name: digest(p) for p in engine.glob('*.py')}
    started = time.monotonic()
    last_print, last_stage, terminal = 0, None, None
    process = None
    previous_sigterm = signal.signal(signal.SIGTERM, cancel_on_termination)
    try:
        with (output / 'events.ndjson').open('w') as events, (output / 'diagnostics.txt').open('w') as diagnostics:
            process = subprocess.Popen([str(python), '-B', '-u', str(engine / 'worker.py')],
                                       cwd=engine, env=env, stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=diagnostics, text=True,
                                       start_new_session=True)
            process.stdin.write(json.dumps(request) + '\n')
            process.stdin.close()
            for line in process.stdout:
                event = json.loads(line)
                events.write(line); events.flush()
                now = time.monotonic()
                if event.get('type') in ('result', 'error'):
                    terminal = event
                if event.get('stage') != last_stage or now - last_print > 25 or terminal:
                    print(json.dumps({k: event[k] for k in ('type', 'stage', 'progress', 'message') if k in event}, ensure_ascii=False), flush=True)
                    last_stage, last_print = event.get('stage'), now
            code = process.wait()
        if source_hash != digest(source):
            raise RuntimeError('Source file changed during translation.')
        if runtime_hashes != {key: digest(path) for key, path in source_files.items()}:
            raise RuntimeError('Bundled translation code changed during this run; results require a fresh run.')
        if module_hashes != {p.name: digest(p) for p in engine.glob('*.py')}:
            raise RuntimeError('Engine modules changed during this run; results require a fresh run.')
        report = dict(createdAt=datetime.now(timezone.utc).isoformat(), app=str(app) if app else None,
                      runtimeMode='source-engine' if args.source_engine else 'signed-app-bundle',
                      engineModulesSHA256=module_hashes,
                      source=str(source), sourceSHA256=source_hash, sourceUnchanged=True,
                      **runtime_hashes, runtimeSourcesUnchanged=True,
                      model=args.model, service=args.service, selectedPages=args.pages,
                      elapsedSeconds=round(time.monotonic() - started, 2),
                      exitCode=code, result=terminal)
        if code == 0 and terminal and terminal.get('type') == 'result':
            result_dir = Path(terminal['translatedPath']).parent
            report['quality'] = json.loads((result_dir / 'translation-quality.json').read_text())
            report['manifest'] = json.loads((result_dir / 'manifest.json').read_text())
        if app:
            subprocess.run(['codesign', '--verify', '--deep', '--strict', str(app)], check=True)
        (output / 'run.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
        print(str(output / 'run.json'), flush=True)
        return 0 if code == 0 and terminal and terminal.get('type') == 'result' else 1
    except BaseException:
        # Ignore repeated termination while reaping the isolated worker session.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        stop_worker_group(process)
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == '__main__':
    raise SystemExit(main())
