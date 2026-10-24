#!/usr/bin/env python3
"""Serial local model probes for four previously rejected PLOS paragraphs.

Reads local, source-only TI captures. Stores paper text/responses in validation
for review; never modifies the sources, managed document library or app bundle.
This is a diagnostic probe, not PDF acceptance or original-object rendering QA.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--strategy', choices=('whole',), default='whole')
    args = parser.parse_args()
    target = args.output.resolve()
    if not target.is_relative_to(ROOT / 'validation'):
        parser.error('Output must remain in this checkout validation directory.')
    import translation_guard as guard
    digest = hashlib.sha256((ROOT / 'engine/translation_guard.py').read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='pagebridge-paper-recovery-') as tmp:
        import babeldoc.const
        babeldoc.const.CACHE_FOLDER = Path(tmp)
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, FormulaPlaceholder, ParagraphTranslateTracker
        from babeldoc.format.pdf.document_il import PdfCharacter, PdfFormula, PdfStyle, Box
        import worker
        settings = worker.build_settings(dict(sourceLanguage='en', targetLanguage='zh',
            service=dict(kind='ollama', baseURL='http://127.0.0.1:11434/v1',
                apiKey='unused-local', model='qwen3.5:35b-a3b-int4', timeoutSeconds=180, concurrency=1)), Path(tmp))
        translator = object.__new__(ILTranslator)
        translator.translation_config = SimpleNamespace(lang_out='zh',
            custom_system_prompt=settings.translation.custom_system_prompt, add_formula_placehold_hint=False)
        translator._cached_glossaries = []
        translator.calc_token_count = lambda text: max(1, len(text) // 4)
        current = {}
        from pdf2zh_next.translator.translator_impl.openai import OpenAITranslator

        class Engine(OpenAITranslator):
            # Use the same audited provider type/options path as the application,
            # with a recording transport for this standalone diagnostic.
            def __init__(self):
                self.options = {}
                self.enable_json_mode = False

            def llm_translate(self, prompt, **kwargs):
                current['prompt'] = prompt
                payload = dict(model='qwen3.5:35b-a3b-int4', temperature=0, presence_penalty=0,
                    reasoning_effort='none', messages=[dict(role='user', content=prompt)], **self.options)
                request = urllib.request.Request('http://127.0.0.1:11434/v1/chat/completions',
                    data=json.dumps(payload).encode(),
                    headers={'Content-Type': 'application/json'}, method='POST')
                with urllib.request.urlopen(request, timeout=180) as response:
                    result = json.load(response)['choices'][0]['message']['content'].strip()
                current['response'] = result
                current.setdefault('calls', []).append(dict(prompt=prompt, response=result,
                    presencePenalty=payload['presence_penalty'], responseFormat=payload.get('response_format')))
                return result
        translator.translate_engine = Engine()
        records = []
        for name, predicates in (
            ('v2-avalanches-rejected-source.json', [(4, 'where activation thresholds'), (6, 'Figure 3. Critical exponents')]),
            ('v2-wind-rejected-source.json', [(8, '94) large training'), (8, 'After the row of variable labels')]),
        ):
            capture_path = ROOT / 'validation/tag-fix' / name
            capture = json.loads(capture_path.read_text())
            assert capture['networkDisabled'] and capture['translationRequests'] == 0
            for page, prefix in predicates:
                matches = [r for r in capture['captured'] if r['page'] == page and (r.get('translationInput') or '').startswith(prefix)]
                assert len(matches) == 1, (name, prefix, len(matches))
                row = matches[0]
                placeholders = []
                for entry in row['placeholders']:
                    assert entry['type'] == 'formula', 'Probe requires the source formulas-only inputs.'
                    chars = [PdfCharacter(char_unicode=c['text'], pdf_character_id=c['glyph'],
                        pdf_style=PdfStyle(font_id=c['fontId'], font_size=c['fontSize']),
                        box=Box(**c['box'])) for c in entry['characters']]
                    placeholders.append(FormulaPlaceholder(entry['id'], PdfFormula(pdf_character=chars),
                        entry['placeholder'], entry['regex_pattern']))
                ti = ILTranslator.TranslateInput(row['translationInput'], placeholders,
                    PdfStyle(font_id=row['baseStyle']['font_id'], font_size=row['baseStyle']['font_size']))
                current.clear()
                current.update(capture=name, captureSHA256=hashlib.sha256(capture_path.read_bytes()).hexdigest(),
                    page=page, sourceParagraphSHA256=hashlib.sha256(row['paragraphText'].encode()).hexdigest(),
                    translationInput=ti.unicode,
                    formulas=[dict(token=p.placeholder, visible=''.join(c.char_unicode or '' for c in p.formula.pdf_character)) for p in placeholders])
                try:
                    plain, normalized, _ = guard._whole_paragraph_translation(translator, ti, ParagraphTranslateTracker())
                    guard._check_body_translation(translator, SimpleNamespace(layout_label=row['layoutLabel']), plain, normalized)
                    current.update(normalizedOutput=normalized, failure=None)
                except Exception as exc:
                    current['failure'] = getattr(exc, 'category', type(exc).__name__)
                records.append(dict(current))
                assert hashlib.sha256((ROOT / 'engine/translation_guard.py').read_bytes()).hexdigest() == digest, 'Guard changed during serial probes'
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(dict(guardSHA256=digest, model='qwen3.5:35b-a3b-int4',
                    localOnly=True, serial=True, strategy=args.strategy, results=records), ensure_ascii=False, indent=2)+'\n')
                print(json.dumps(dict(capture=name, page=page, failure=current['failure']), ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
