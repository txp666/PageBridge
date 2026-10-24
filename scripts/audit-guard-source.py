#!/usr/bin/env python3
"""Check actual source TI identity with the guard; zero translation requests.

Runs the ordinary PDF parse/layout/formula pipeline with cached local assets,
then normalizes each prepared TI against its own Unicode. This tests structural
false rejections only, not whether English prose is an acceptable translation.
"""
import argparse
from collections import Counter
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'engine'))


class AuditComplete(Exception):
    pass


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--pages', required=True)
    args = parser.parse_args()
    source, target = args.input.resolve(), args.output.resolve()
    if not target.is_relative_to(ROOT / 'validation'):
        parser.error('Output must be inside this checkout validation directory.')
    source_hash = sha(source.read_bytes())
    engine_hashes = {name:sha((ROOT/'engine'/name).read_bytes())
                     for name in ('worker.py','translation_guard.py','layout_guard.py')}
    target.parent.mkdir(parents=True, exist_ok=True)
    records, failures = [], []
    totals = Counter()
    with tempfile.TemporaryDirectory(prefix='pagebridge-source-guard-audit-') as tmp:
        temporary = Path(tmp)
        cache = temporary/'cache'
        cache.mkdir()
        for name in ('fonts','models','tiktoken'):
            shutil.copytree(Path.home()/'.cache/babeldoc'/name,cache/name)
        import babeldoc.const
        babeldoc.const.CACHE_FOLDER = cache
        babeldoc.const.TIKTOKEN_CACHE_FOLDER = cache/'tiktoken'
        os.environ['TIKTOKEN_CACHE_DIR'] = str(cache/'tiktoken')
        import worker
        import layout_guard
        import translation_guard
        with patch.object(Path,'home',return_value=temporary/'home'):
            from pdf2zh_next.high_level import create_babeldoc_config
            from pdf2zh_next.translator.base_translator import BaseTranslator
        from babeldoc.format.pdf.high_level import translate
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator, ParagraphTranslateTracker
        from babeldoc.format.pdf.document_il.midend.il_translator_llm_only import ILTranslatorLLMOnly
        layout_guard.install()
        translation_guard.install()

        def forbidden(*a,**kw):
            raise RuntimeError('Network access disabled by source audit')

        class NoTranslation(BaseTranslator):
            name = 'source_identity_audit'
            def __init__(self):
                self.lang_in,self.lang_out = 'en','zh'
            def do_translate(self,*a,**kw):
                raise RuntimeError('Translation requests forbidden by source audit')
            def do_llm_translate(self,text,rate_limit_params=None):
                if text is not None:
                    raise RuntimeError('Translation requests forbidden by source audit')
                return None

        request=dict(sourceLanguage='en',targetLanguage='zh',pages=args.pages,
            service=dict(kind='ollama',baseURL='http://127.0.0.1:9/v1',
                model='source-identity-no-translation',apiKey='unused',timeoutSeconds=10,concurrency=1))
        settings=worker.build_settings(request,temporary/'output')
        with patch('pdf2zh_next.high_level.get_translator',return_value=NoTranslation()), \
             patch('pdf2zh_next.high_level.get_term_translator',return_value=None), \
             patch.object(socket.socket,'connect',forbidden), \
             patch.object(socket,'create_connection',forbidden):
            config=create_babeldoc_config(settings,source)

        def inspect(self,docs):
            il=self.il_translator if hasattr(self,'il_translator') else self
            for page in docs.page:
                fonts={f.font_id:f for f in page.pdf_font}
                xobj_fonts={o.xobj_id:{**fonts,**{f.font_id:f for f in o.pdf_font}}
                            for o in page.pdf_xobject}
                for paragraph in page.pdf_paragraph:
                    original=paragraph.unicode or ''
                    record=dict(page=page.page_number+1,layoutLabel=paragraph.layout_label,
                        sourceParagraphSHA256=sha(original.encode()),sourceLength=len(original))
                    before=translation_guard._counters.copy()
                    totals['paragraphs']+=1
                    try:
                        text,ti=il.pre_translate_paragraph(paragraph,ParagraphTranslateTracker(),fonts,xobj_fonts)
                    except Exception as exc:
                        record.update(disposition='preparation-failed',errorType=type(exc).__name__,error=str(exc))
                        failures.append({**record,'paragraphText':original})
                        totals['preparationFailures']+=1
                        records.append(record)
                        continue
                    increments=dict(translation_guard._counters-before)
                    record['preparationCounters']=increments
                    if ti is None:
                        record['disposition']='source-preserved-or-upstream-skipped'
                        totals['preservedOrSkipped']+=1
                        records.append(record)
                        continue
                    totals['preparedTI']+=1
                    record.update(translationInputSHA256=sha(ti.unicode.encode()),
                        translationInputLength=len(ti.unicode),placeholderCount=len(ti.placeholders))
                    try:
                        normalized=translation_guard.normalize_output(ti,ti.unicode)
                        record.update(disposition='identity-passed',exactTextIdentity=normalized==ti.unicode)
                        totals['identityPassed']+=1
                        if normalized!=ti.unicode:
                            totals['sourceCanonicalized']+=1
                    except Exception as exc:
                        record.update(disposition='identity-rejected',errorType=type(exc).__name__,error=str(exc))
                        failures.append({**record,'paragraphText':original,'translationInput':ti.unicode,
                            'placeholders':[p.to_dict() for p in ti.placeholders]})
                        totals['identityRejected']+=1
                    records.append(record)
            raise AuditComplete()

        logging.getLogger().setLevel(logging.ERROR)
        with patch.object(ILTranslatorLLMOnly,'translate',inspect), \
             patch.object(ILTranslator,'translate',inspect), \
             patch.object(socket.socket,'connect',forbidden), \
             patch.object(socket,'create_connection',forbidden):
            try:
                translate(config)
            except AuditComplete:
                pass
        assert records,'No source paragraphs were audited'
        report=dict(source=str(source),sourceSHA256=source_hash,
            sourceUnchanged=source_hash==sha(source.read_bytes()),pages=args.pages,
            networkDisabled=True,translationRequests=0,bodyTranslationGateInvoked=False,
            engineSourceSHA256=engine_hashes,
            engineSourcesUnchanged=all(sha((ROOT/'engine'/k).read_bytes())==v for k,v in engine_hashes.items()),
            totals=dict(totals),sourceIdentityPassed=not failures,failures=failures,paragraphAudits=records,
            preparationCounters=dict(translation_guard._counters),layoutGuardSnapshot=layout_guard.snapshot(),
            limitations=['This is an identity/structure audit; no assertion about target-language prose or final rendered layout is made.',
                         'Paragraph text is stored only for failures; successful paragraph entries retain hashes and counts.'])
        target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
        print(json.dumps({k:report[k] for k in ('sourceIdentityPassed','translationRequests','totals','engineSourcesUnchanged')},ensure_ascii=False))


if __name__=='__main__':
    main()
