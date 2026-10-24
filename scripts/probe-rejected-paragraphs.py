#!/usr/bin/env python3
"""Sequential local-only recovery probes for the four known 68-page rejections."""
from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import tempfile
from types import SimpleNamespace
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))


def main():
    target = ROOT / 'validation/tag-fix/rejected-68-local-probe.json'
    capture = json.loads((ROOT / 'validation/tag-fix/debug-68-rejected.json').read_text())
    rejected = json.loads((ROOT / 'validation/tag-fix/full-68-final/rejected-quality-5B87DBE8-829E-479C-B6E9-24C854035092.json').read_text())
    hashes = {p['sourceParagraphSHA256'] for p in rejected['unresolvedParagraphs']}
    run = json.loads((ROOT / 'validation/tag-fix/full-68-final/run.json').read_text())
    guard_path = Path(run['app']) / 'Contents/Resources/engine/translation_guard.py'
    spec = importlib.util.spec_from_file_location('candidate_guard_probe', guard_path)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    with tempfile.TemporaryDirectory(prefix='pagebridge-probe-') as tmp:
        import babeldoc.const
        babeldoc.const.CACHE_FOLDER = Path(tmp)
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        import worker
        settings = worker.build_settings(dict(sourceLanguage='en', targetLanguage='zh',
            service=dict(kind='ollama',baseURL='http://127.0.0.1:11434/v1',
                apiKey='unused-local',model='qwen3.5:35b-a3b-int4',timeoutSeconds=180,concurrency=1)), Path(tmp))
        translator = object.__new__(ILTranslator)
        translator.translation_config = SimpleNamespace(lang_out='zh',
            custom_system_prompt=settings.translation.custom_system_prompt,
            add_formula_placehold_hint=False)
        translator._cached_glossaries = []
        translator.calc_token_count = lambda text: max(1, len(text) // 4)
        results = []
        class Engine:
            def llm_translate(self, prompt, **kwargs):
                request = urllib.request.Request('http://127.0.0.1:11434/v1/chat/completions',
                    data=json.dumps(dict(model='qwen3.5:35b-a3b-int4',temperature=0,
                        reasoning_effort='none',messages=[dict(role='user',content=prompt)])).encode(),
                    headers={'Content-Type':'application/json'},method='POST')
                with urllib.request.urlopen(request, timeout=180) as response:
                    return json.load(response)['choices'][0]['message']['content'].strip()
        translator.translate_engine = Engine()
        for item in capture['captured']:
            digest = hashlib.sha256(item['paragraphText'].encode()).hexdigest()
            if digest not in hashes:
                continue
            placeholders = []
            for entry in item['placeholders']:
                assert entry['type'] == 'formula', 'These four audited inputs have no rich styles.'
                placeholders.append(SimpleNamespace(id=entry['id'], placeholder=entry['placeholder'],
                    formula=SimpleNamespace(pdf_character=[SimpleNamespace(char_unicode=c['text']) for c in entry['characters']])))
            ti = ILTranslator.TranslateInput(item['translationInput'], placeholders, None)
            result = dict(page=item['page'],sourceParagraphSHA256=digest,
                sourceParagraph=item['paragraphText'],translationInput=ti.unicode,
                formulaPlaceholders=[dict(token=p.placeholder,text=''.join(c.char_unicode for c in p.formula.pdf_character)) for p in placeholders],
                sourceClassifications=guard._classification_spans(ti,ti.unicode))
            tracker_item = SimpleNamespace(set_input=lambda text:result.update(prompt=text),
                set_output=lambda text:result.update(response=text))
            tracker = SimpleNamespace(new_llm_translate_tracker=lambda:tracker_item)
            try:
                _, normalized, _ = guard._whole_paragraph_translation(translator,ti,tracker)
                result.update(normalizedOutput=normalized, failure=None)
            except Exception as error:
                result['failure'] = getattr(error,'category',type(error).__name__)
            restored = result.get('response','')
            # This reproduces the exact restoration, even when subsequent
            # normalize_output rejects the already restored response.
            spans = (guard._protected_literal_spans(ti,ti.unicode)
                     if hasattr(guard, '_protected_literal_spans') else
                     [(left,right) for left,right,_ in guard._classification_spans(ti,ti.unicode)])
            for index,(left,right) in enumerate(spans,1):
                restored=restored.replace('{PBKEEP_'+f'{index:04d}'+'}',ti.unicode[left:right])
            result['restoredOutput']=restored
            result['outputClassifications']=guard._classification_spans(ti,restored)
            result['sourceFormulaOrder']=guard._FORMULA.findall(ti.unicode)
            result['outputFormulaOrder']=guard._FORMULA.findall(restored)
            results.append(result)
            target.write_text(json.dumps(dict(localModel='qwen3.5:35b-a3b-int4',
                endpoint='http://127.0.0.1:11434/v1/chat/completions',
                guardSHA256=hashlib.sha256(guard_path.read_bytes()).hexdigest(),
                sequential=True,results=results),ensure_ascii=False,indent=2)+'\n')
            print(json.dumps(dict(page=item['page'],failure=result['failure'],report=str(target)),ensure_ascii=False),flush=True)


if __name__ == '__main__':
    main()
