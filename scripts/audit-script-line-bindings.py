#!/usr/bin/env python3
"""No-model audit of source-proven script line-layout bindings."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
import layout_guard


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--pages',default='1-68')
    args=parser.parse_args()
    original=layout_guard.register_script_bindings
    rows=[]; failures=[]; relocated=[]
    def register(page):
        from babeldoc.format.pdf.document_il import Box,PdfStyle
        from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting,TypesettingUnit
        from types import SimpleNamespace
        import pymupdf
        formulas=[c.pdf_formula for p in page.pdf_paragraph for c in p.pdf_paragraph_composition if c.pdf_formula]
        snapshots={id(f):[(id(c),c.char_unicode,asdict(c.box),c.pdf_style.font_id,c.pdf_style.font_size,
                          id(c.pdf_style.graphic_state)) for c in f.pdf_character] for f in formulas}
        before=set(layout_guard._script_bindings)
        count=original(page)
        if original(page): failures.append({'page':page.page_number+1,'reason':'not-idempotent'})
        for p in page.pdf_paragraph:
            for comp in p.pdf_paragraph_composition:
                f=comp.pdf_formula
                if f is None or id(f) not in layout_guard._script_bindings or id(f) in before:continue
                binding=layout_guard._script_bindings[id(f)]
                chars=[c for v in p.pdf_paragraph_composition for c in layout_guard._composition_characters(v)]
                i=next(i for i,c in enumerate(chars) if c is f.pdf_character[0])
                base=chars[i-1]
                rows.append(dict(page=page.page_number+1,sourceSHA256=hashlib.sha256((p.unicode or '').encode()).hexdigest(),
                                 base=base.char_unicode,formulaText=''.join(c.char_unicode or '' for c in f.pdf_character),
                                 sourceDelta=f.pdf_character[0].box.y-base.box.y,glyphCount=len(f.pdf_character)))
                if page.page_number+1!=39:continue
                font=pymupdf.Font('helv')
                original_font=SimpleNamespace(name=binding[2])
                def text(value):return TypesettingUnit(unicode=value,font=font,original_font=original_font,
                                      font_size=binding[3],style=base.pdf_style,xobj_id=binding[4])
                b=text(base.char_unicode);s=TypesettingUnit(formular=f);dot=text('.')
                setter=object.__new__(Typesetting);setter.font_mapper=SimpleNamespace(base_font=font)
                for scale in (.5,1,1.5):
                    for english in (True,False):
                        atom=layout_guard._bound_typesetting_units([b,s])[-1]
                        if not isinstance(atom,layout_guard._BoundScriptUnit):
                            failures.append(dict(page=39,reason='not-bound'));continue
                        box=Box(0,0,atom.width*scale+1,100)
                        prefix=[dot]*max(1,int((box.x2-2*b.width*scale-1)/(dot.width*scale)))
                        result,fit=layout_guard._guard_layout_typesetting_units(setter,prefix+[b,s],box,scale,1.5,p,english)
                        delta=result[-1].formular.pdf_character[0].box.y-result[-2].box.y
                        expected=(f.pdf_character[0].box.y-base.box.y)*scale
                        ok=fit and abs(delta-expected)<.05 and result[-1].box.x2<=box.x2+.01
                        relocated.append(dict(base=binding[1],formulaText=rows[-1]['formulaText'],scale=scale,
                            english=english,expected=expected,actual=delta,pass_=ok))
                        if not ok:failures.append(dict(page=39,reason='relocate',expected=expected,actual=delta))
        for f in formulas:
            after=[(id(c),c.char_unicode,asdict(c.box),c.pdf_style.font_id,c.pdf_style.font_size,
                    id(c.pdf_style.graphic_state)) for c in f.pdf_character]
            if after!=snapshots[id(f)]:failures.append(dict(page=page.page_number+1,reason='source-mutation'))
        return count
    layout_guard.register_script_bindings=register
    spec=importlib.util.spec_from_file_location('binding_inspect',ROOT/'scripts/inspect-pdf-translation-input.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    out=Path(args.output)
    sys.argv=[str(spec.origin),'--input',args.input,'--output',str(out.with_suffix('.capture.json')),
              '--pages',args.pages,'--index-only','--match','.']
    mod.main()
    report=dict(source=args.input,sourceSHA256=hashlib.sha256(Path(args.input).read_bytes()).hexdigest(),
                layoutSHA256=hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest(),
                networkDisabled=True,translationRequests=0,bindings=rows,realP39Relocations=relocated,
                failures=failures,layoutSnapshot=layout_guard.snapshot())
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(output=str(out),bindings=len(rows),relocations=len(relocated),failures=len(failures))))


if __name__=='__main__':main()
