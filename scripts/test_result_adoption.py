"""Exercise first/repeated result adoption and reject changed engine evidence."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pymupdf

spec = importlib.util.spec_from_file_location('adoption', Path(__file__).with_name('adopt-reviewed-results.py'))
adoption = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adoption)


class AdoptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.managed = self.root / 'managed'
        self.folder = self.managed / 'documents' / 'doc'
        self.folder.mkdir(parents=True)
        self.source = self.folder / 'input.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((50, 50), 'Source document')
            pdf.save(self.source)
        (self.root / 'engine').mkdir()
        for name in adoption.RUNTIME_MODULES:
            (self.root / 'engine' / name).write_text('# locked runtime\n')
        self.run_folder = self.root / 'validation' / 'tag-fix' / 'accepted'
        self.run_folder.mkdir(parents=True)
        self.mono = self.run_folder / 'translated.pdf'
        self.dual = self.run_folder / 'bilingual.pdf'
        self.mono.write_bytes(self.source.read_bytes())
        with pymupdf.open(self.source) as source, pymupdf.open() as pdf:
            pdf.insert_pdf(source); pdf.insert_pdf(source); pdf.save(self.dual)
        self.quality = dict(guardInstalled=True, unresolvedCount=0)
        (self.run_folder / 'translation-quality.json').write_text(json.dumps(self.quality))
        self.run = dict(exitCode=0, result=dict(type='result', translatedPath=str(self.mono),
                        bilingualPath=str(self.dual), engineVersion='fixture'), selectedPages=None,
                        runtimeSourcesUnchanged=True,
                        engineModulesSHA256={name: adoption.sha(self.root / 'engine' / name) for name in adoption.RUNTIME_MODULES},
                        quality=self.quality, model='qwen3.5:35b-a3b-int4', service='ollama',
                        source=str(self.source), sourceSHA256=adoption.sha(self.source),
                        manifest=dict(translatedSHA256=adoption.sha(self.mono), bilingualSHA256=adoption.sha(self.dual)))
        self.run_file = self.run_folder / 'run.json'
        self.run_file.write_text(json.dumps(self.run))
        self.index = self.managed / 'documents.json'
        self.record = dict(id='00000000-0000-0000-0000-000000000001', name='Example.pdf', pageCount=1,
                           status='ready', inputPath=str(self.source), inputSHA256=adoption.sha(self.source))
        self.index.write_text(json.dumps([self.record]))

    def adopt(self):
        with patch.object(adoption, 'ROOT', self.root), patch.object(adoption, 'require_app_stopped'), contextlib.redirect_stdout(io.StringIO()):
            adoption.adopt_runs([self.run_file], self.managed)

    def test_first_then_repeated_result_preserves_previous_files(self):
        self.adopt()
        first = json.loads(self.index.read_text())[0]
        old_path = Path(first['translatedPath'])
        old_hash = adoption.sha(old_path)
        manifest = json.loads(old_path.with_name('manifest.json').read_text())
        self.assertEqual(manifest['service']['provider'], 'ollama')
        self.assertNotIn('apiKey', manifest['service'])
        self.assertEqual(first['status'], 'completed')
        self.adopt()
        second = json.loads(self.index.read_text())[0]
        self.assertNotEqual(first['translatedPath'], second['translatedPath'])
        self.assertEqual(adoption.sha(old_path), old_hash)
        self.assertEqual(len(list((self.root / 'validation/tag-fix').glob('adoption-*.json'))), 2)

    def test_reject_changed_link_code_without_mutating_index(self):
        before = self.index.read_bytes()
        (self.root / 'engine/pdf_links.py').write_text('# changed\n')
        with self.assertRaisesRegex(RuntimeError, 'pdf_links.py'):
            self.adopt()
        self.assertEqual(self.index.read_bytes(), before)
        self.assertFalse((self.folder / 'results').exists())

    def test_reject_partial_document_without_mutating_index(self):
        before = self.index.read_bytes()
        self.run['selectedPages'] = '1'
        self.run_file.write_text(json.dumps(self.run))
        with self.assertRaisesRegex(RuntimeError, 'fully retranslated'):
            self.adopt()
        self.assertEqual(self.index.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
