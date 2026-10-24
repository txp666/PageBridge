#!/usr/bin/env python3
"""Adopt explicitly reviewed full-document runs, retaining every prior result.

Run only after quitting PageBridge, with --run for each accepted run.json.
This is a local maintenance command; normal app translations use Repository.commit.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MODULES = ('worker.py', 'translation_guard.py', 'layout_guard.py', 'pdf_links.py')


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def verify_runtime(run):
    modules = run.get('engineModulesSHA256', {})
    for name in RUNTIME_MODULES:
        require(modules.get(name) == sha(ROOT / 'engine' / name),
                'Run used different engine source: ' + name)


def require_app_stopped():
    processes = subprocess.run(['/bin/ps', '-axo', 'comm='], check=True,
                               capture_output=True, text=True).stdout.splitlines()
    if any(Path(command.strip()).name == 'PageBridge' for command in processes):
        raise RuntimeError('Quit PageBridge before adopting reviewed results; its in-memory index may be stale.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='append', required=True, type=Path)
    args = parser.parse_args()
    managed = Path.home() / 'Library/Application Support/PageBridge'
    # The lock is a stable sidecar, not documents.json, which is atomically
    # replaced. It serializes concurrent maintenance commands for this store.
    with (managed / 'documents.adoption.lock').open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another result adoption is already running.') from None
        require_app_stopped()
        adopt_runs(args.run, managed)


def adopt_runs(run_files, managed):
    index = managed / 'documents.json'
    original_index = index.read_bytes()
    original_index_sha = hashlib.sha256(original_index).hexdigest()
    records = json.loads(original_index)
    if any(item['status'] in ('running', 'cancelling') for item in records):
        raise RuntimeError('A document is still running; do not replace the index.')
    plans = []
    for run_file in run_files:
        run_file = run_file.resolve()
        require(run_file.is_relative_to(ROOT / 'validation/tag-fix'), 'Run is outside the validation directory.')
        run = json.loads(run_file.read_text())
        require(run['exitCode'] == 0 and run['result']['type'] == 'result', 'Run did not succeed.')
        require(run['selectedPages'] is None, 'Only fully retranslated documents may be adopted.')
        require(run.get('runtimeSourcesUnchanged') is True, 'Run must attest code was unchanged from start to finish.')
        verify_runtime(run)
        require(run['quality']['guardInstalled'] and run['quality']['unresolvedCount'] == 0,
                'Run did not pass the translation guard.')
        require(run['model'] == 'qwen3.5:35b-a3b-int4' and run['service'] == 'ollama', 'Unexpected translation service.')
        matches = [r for r in records if r['inputPath'] == run['source']]
        require(len(matches) == 1, 'Expected a unique existing document.')
        record = matches[0]
        require(record['inputSHA256'] == run['sourceSHA256'] == sha(record['inputPath']), 'Original PDF changed.')
        mono = Path(run['result']['translatedPath']).resolve()
        dual = Path(run['result']['bilingualPath']).resolve()
        require(mono.is_relative_to(run_file.parent) and dual.is_relative_to(run_file.parent), 'Result is outside its run directory.')
        require(sha(mono) == run['manifest']['translatedSHA256'], 'Translated PDF changed.')
        require(sha(dual) == run['manifest']['bilingualSHA256'], 'Bilingual PDF changed.')
        require(json.loads((mono.parent / 'translation-quality.json').read_text()) == run['quality'], 'Quality record changed.')
        with pymupdf.open(mono) as pdf:
            require(pdf.page_count == record['pageCount'], 'Translated page count differs.')
        with pymupdf.open(dual) as pdf:
            require(pdf.page_count == record['pageCount'] * 2, 'Bilingual page count differs.')
        attempt = str(uuid.uuid4()).upper()
        destination = Path(record['inputPath']).parent / 'results' / attempt
        require(destination.is_relative_to(managed / 'documents') and not destination.exists(), 'Invalid result destination.')
        # Build a complete Swift OutputManifest for first translations as well
        # as replacements. Do not inherit stale cloud metadata from old output.
        manifest = dict(attemptID=attempt, createdAt=time.time() - 978307200,
                        inputSHA256=record['inputSHA256'], documentID=record['id'],
                        translatedSHA256=run['manifest']['translatedSHA256'],
                        bilingualSHA256=run['manifest']['bilingualSHA256'],
                        pageCount=record['pageCount'], bilingualPageCount=record['pageCount']*2,
                        sourceLanguage='en', targetLanguage='zh',
                        engineVersion=run['result']['engineVersion'],
                        service=dict(id=str(uuid.uuid4()).upper(), name='本机 Ollama',
                                     kind='ollama', provider='ollama',
                                     baseURL='http://127.0.0.1:11434/v1',
                                     model=run['model'], concurrency=2, timeoutSeconds=300))
        plans.append((record, mono, dual, destination, manifest, attempt, run_file, run))
    require(len({item[0]['id'] for item in plans}) == len(plans), 'Duplicate documents in adoption plan.')
    backup = ROOT / 'validation/tag-fix' / ('documents-before-adoption-' + uuid.uuid4().hex + '.json')
    backup.write_bytes(original_index)
    adopted = []
    for record, mono, dual, destination, manifest, attempt, run_file, run in plans:
        stage = destination.with_name('.' + destination.name + '-staging')
        stage.parent.mkdir(parents=True, exist_ok=True)
        stage.mkdir()
        shutil.copyfile(mono, stage / 'translated.pdf')
        shutil.copyfile(dual, stage / 'bilingual.pdf')
        shutil.copyfile(mono.parent / 'translation-quality.json', stage / 'translation-quality.json')
        (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
        require(sha(stage / 'translated.pdf') == manifest['translatedSHA256']
                and sha(stage / 'bilingual.pdf') == manifest['bilingualSHA256'], 'Copied result hash differs.')
        require(json.loads((stage / 'translation-quality.json').read_text()) == run['quality'],
                'Copied quality record differs.')
        stage.rename(destination)
        previous = {k: record.get(k) for k in ('translatedPath', 'bilingualPath', 'attemptID')}
        record.update(translatedPath=str(destination / 'translated.pdf'),
                      bilingualPath=str(destination / 'bilingual.pdf'), attemptID=attempt,
                      status='completed', sourceLanguage='en', targetLanguage='zh')
        record.pop('lastError', None)
        adopted.append(dict(documentID=record['id'], name=record['name'],
                            previous=previous, destination=str(destination), run=str(run_file)))
    temporary_index = managed / ('documents-' + uuid.uuid4().hex + '.json.tmp')
    try:
        temporary_index.write_text(json.dumps(records, ensure_ascii=False, indent=2) + '\n')
        require_app_stopped()
        for record, mono, dual, destination, manifest, attempt, run_file, run in plans:
            require(json.loads(run_file.read_text()) == run, 'Run record changed before adoption.')
            require(sha(record['inputPath']) == record['inputSHA256'], 'Original changed before adoption.')
            require(sha(destination / 'translated.pdf') == manifest['translatedSHA256']
                    and sha(destination / 'bilingual.pdf') == manifest['bilingualSHA256'], 'Copied result changed before adoption.')
            verify_runtime(run)
        require(index.read_bytes() == original_index, 'Document index changed; new results retained but not selected.')
        temporary_index.replace(index)
    finally:
        temporary_index.unlink(missing_ok=True)
    report = dict(adoptedAt=datetime.now(timezone.utc).isoformat(), backup=str(backup),
                  originalIndexSHA256=original_index_sha,
                  appStoppedAtCommit=True, priorResultsRetained=True, documents=adopted)
    report_path = ROOT / 'validation/tag-fix' / ('adoption-' + uuid.uuid4().hex + '.json')
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    report['reportPath'] = str(report_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
