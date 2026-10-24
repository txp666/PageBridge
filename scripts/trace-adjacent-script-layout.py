#!/usr/bin/env python3
"""Trace real source TI before/after script promotion, no model requests."""
from dataclasses import asdict
import copy, hashlib, importlib.util, json, re, sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
import translation_guard,layout_guard


def main():
    import argparse
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    rows=[];original=translation_guard._promote_rich_scripts
    def promote(ti,p,fonts=None):
        result,count=original(ti,p,fonts)
        if 'Maximum output' not in (p.unicode or ''):return result,count
        from babeldoc.format.pdf.document_il import Box
        from babeldoc.format.pdf.document_il.midend.il_translator import ILTranslator
        from babeldoc.format.pdf.document_il.midend.typesetting import Typesetting
        import pymupdf
        il=object.__new__(ILTranslator)
        il._formula_placeholder_pattern=re.compile(r'\{v\d+\}')
        il._style_left_placeholder_pattern=re.compile(r"<style[^>]*>")
        il._style_right_placeholder_pattern=re.compile(r'</style>')
        font=pymupdf.Font('helv');t=object.__new__(Typesetting)
        t.font_mapper=SimpleNamespace(base_font=font,map=lambda *_:font)
        row=dict(source=p.unicode,beforeTI=ti.unicode,afterTI=result.unicode,nativeFormulas=[],variants=[])
        for h in ti.placeholders:
            if hasattr(h,'formula'):
                f=h.formula
                row['nativeFormulas'].append(dict(id=h.id,text=''.join(c.char_unicode or '' for c in f.pdf_character),
                    box=asdict(f.box),x_offset=f.x_offset,y_offset=f.y_offset,
                    glyphs=[dict(char=c.char_unicode,box=asdict(c.box),font=c.pdf_style.font_id,size=c.pdf_style.font_size) for c in f.pdf_character]))
        for name,item in [('rich-before',ti),('promoted-after',result)]:
            pp=copy.copy(p);pp.pdf_paragraph_composition=il.parse_translate_output(item,item.unicode)
            units=t.create_typesetting_units(pp,fonts)
            grouped=layout_guard._bound_typesetting_units(units)
            for wrapper in (False,True):
                fn=layout_guard._guard_layout_typesetting_units if wrapper else layout_guard._original_layout_typesetting_units
                out,fit=fn(t,units,Box(0,0,600,100),1,1.5,pp,False)
                rendered=[c for u in out for c in u.render()[0]]
                row['variants'].append(dict(name=name,wrapper=wrapper,wrappedCount=sum(isinstance(u,layout_guard._BoundScriptUnit) for u in grouped),
                    fits=fit,glyphs=[dict(char=c.char_unicode,y=c.box.y,x=c.box.x,font=c.pdf_style.font_id,size=c.pdf_style.font_size) for c in rendered]))
        rows.append(row);return result,count
    translation_guard._promote_rich_scripts=promote
    spec=importlib.util.spec_from_file_location('identity_trace',ROOT/'scripts/audit-guard-source.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    out=Path(a.output);sys.argv=[str(spec.origin),'--input',a.input,'--output',str(out.with_name(out.stem+'-identity.json')),'--pages','14'];mod.main()
    out.write_text(json.dumps(dict(layoutSHA=hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest(),guardSHA=hashlib.sha256((ROOT/'engine/translation_guard.py').read_bytes()).hexdigest(),modelCalls=0,rows=rows),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(output=str(out),rows=len(rows))))


if __name__=='__main__':main()
