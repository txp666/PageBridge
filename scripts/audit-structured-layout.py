#!/usr/bin/env python3
"""Verify actual list/caption resegmentation preserves every source glyph."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))


def load_script(name):
    spec=importlib.util.spec_from_file_location(name.replace('-','_'),ROOT/'scripts'/name)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--pages',required=True)
    args=parser.parse_args()
    import layout_guard
    index_audit=load_script('audit-index-preservation.py')
    inspector=load_script('inspect-pdf-translation-input.py')
    records=[]

    def glyphs(page):
        result=list(page.pdf_character)
        for p in page.pdf_paragraph:
            for comp in p.pdf_paragraph_composition:
                if comp.pdf_line:result.extend(comp.pdf_line.pdf_character)
                elif comp.pdf_character:result.append(comp.pdf_character)
                elif comp.pdf_formula:result.extend(comp.pdf_formula.pdf_character)
        return result

    def paragraph_state(p):
        return dict(text=p.unicode,box=asdict(p.box) if p.box else None,label=p.layout_label)

    def wrapper(name,original):
        def audited(finder,page):
            before_glyphs=glyphs(page)
            states={id(c):index_audit.character_state(c) for c in before_glyphs}
            paragraphs={id(p):paragraph_state(p) for p in page.pdf_paragraph}
            count=original(finder,page)
            after_glyphs=glyphs(page)
            after={id(c):index_audit.character_state(c) for c in after_glyphs}
            after_paragraphs={id(p):paragraph_state(p) for p in page.pdf_paragraph}
            passed=(len(before_glyphs)==len(after_glyphs)==len(states)==len(after) and states==after)
            changed=[dict(before=state,after=after_paragraphs.get(key))
                     for key,state in paragraphs.items() if state!=after_paragraphs.get(key)]
            records.append(dict(page=page.page_number+1,repair=name,matched=count,
                glyphIdentityAndPropertiesUnchanged=passed,glyphCount=len(before_glyphs),
                changedParagraphs=changed))
            return count
        return audited

    target=args.output.resolve()
    original_closers=layout_guard.merge_caption_fragments
    original_equations=layout_guard.split_structured_entries
    argv=['inspect','--input',str(args.input),'--output',str(target.with_suffix('.capture.json')),
          '--pages',args.pages,'--index-only','--match','.']
    with patch.object(layout_guard,'merge_caption_fragments',wrapper('captionFragments',original_closers)), \
         patch.object(layout_guard,'split_structured_entries',wrapper('structuredEntries',original_equations)), \
         patch.object(sys,'argv',argv):
        inspector.main()
    report=dict(source=str(args.input.resolve()),pages=args.pages,translationRequests=0,
        layoutGuardSHA256=hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest(),
        auditPassed=all(r['glyphIdentityAndPropertiesUnchanged'] for r in records),
        matches=[r for r in records if r['matched']],nonmatchingChecks=sum(not r['matched'] for r in records),
        snapshot=layout_guard.snapshot())
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:report[k] for k in ('auditPassed','nonmatchingChecks','snapshot')}))


if __name__=='__main__':main()
