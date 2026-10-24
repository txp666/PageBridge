#!/usr/bin/env python3
"""Source-only source/relocated baseline audit. Network and model calls disabled."""
import argparse
from dataclasses import asdict,fields
import hashlib, importlib.util, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
import layout_guard

def character_state(c):
    style=c.pdf_style
    return dict(char_unicode=c.char_unicode,pdf_character_id=c.pdf_character_id,
                box=asdict(c.box) if c.box else None,
                visual_bbox=asdict(c.visual_bbox) if c.visual_bbox else None,
                pdf_style=dict(font_id=style.font_id,font_size=style.font_size,
                               graphic_state_identity=id(style.graphic_state)) if style else None,
                xobj_id=c.xobj_id,vertical=c.vertical,advance=c.advance,scale=c.scale,
                render_order=c.render_order,sub_render_order=c.sub_render_order)

def formula_state(f):
    return dict(box=asdict(f.box) if f.box else None,x_offset=f.x_offset,y_offset=f.y_offset,
                x_advance=f.x_advance,line_id=f.line_id,is_corner_mark=f.is_corner_mark,
                pdf_character=[character_state(c) for c in f.pdf_character],
                curve_identities=[id(v) for v in f.pdf_curve],form_identities=[id(v) for v in f.pdf_form])

def chars(comp):
    for f in fields(comp):
        value=getattr(comp,f.name)
        if value is not None:
            return [value] if f.name=='pdf_character' else getattr(value,'pdf_character',[])
    return []

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);ap.add_argument('--pages',default='1-68');a=ap.parse_args()
    report=[];orig_install=layout_guard.install;repairs=[];failures=[]
    if hasattr(layout_guard,'repair_subscript_offsets'):
        original_repair=layout_guard.repair_subscript_offsets
        def repair(page):
            formulas=[c.pdf_formula for p in page.pdf_paragraph for c in p.pdf_paragraph_composition if c.pdf_formula]
            before={id(f):(f,formula_state(f),list(f.pdf_character)) for f in formulas}
            count=original_repair(page)
            for f in formulas:
                original,state,glyphs=before[id(f)]
                after=formula_state(f)
                old=state.pop('y_offset');new=after.pop('y_offset')
                if state!=after or any(a is not b for a,b in zip(glyphs,f.pdf_character)) or len(glyphs)!=len(f.pdf_character):
                    failures.append(dict(page=page.page_number+1,reason='non-offset-source-mutation'))
                if old!=new:
                    repairs.append(dict(page=page.page_number+1,text=''.join(c.char_unicode or '' for c in f.pdf_character),box=asdict(f.box),before=old,after=new))
            if original_repair(page):failures.append(dict(page=page.page_number+1,reason='not-idempotent'))
            return count
        layout_guard.repair_subscript_offsets=repair
    def install():
        orig_install()
        from babeldoc.format.pdf.document_il.midend.styles_and_formulas import StylesAndFormulas
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        original=StylesAndFormulas.process_page
        def process(self,page):
            result=original(self,page)
            fonts={f.font_id:f for f in page.pdf_font}
            xfonts={o.xobj_id:{**fonts,**{f.font_id:f for f in o.pdf_font}} for o in page.pdf_xobject}
            for p in page.pdf_paragraph:
                lookup=xfonts.get(p.xobj_id,fonts)
                allchars=[c for comp in p.pdf_paragraph_composition for c in chars(comp)]
                for i,comp in enumerate(p.pdf_paragraph_composition):
                    f=comp.pdf_formula
                    if not f: continue
                    visible=[c for c in f.pdf_character if c.char_unicode and c.char_unicode.strip()]
                    if not visible or not visible[0].pdf_style: continue
                    c=visible[0]
                    ci=next(j for j,v in enumerate(allchars) if v is c)
                    before=allchars[:ci]
                    base=next((v for v in reversed(before) if v.char_unicode and v.char_unicode.strip()),None)
                    if base is None or not base.pdf_style or c.pdf_style.font_size>=base.pdf_style.font_size*.86: continue
                    item=dict(page=page.page_number+1,paragraphSHA256=hashlib.sha256((p.unicode or '').encode()).hexdigest(),text=p.unicode,formulaText=''.join(v.char_unicode or '' for v in f.pdf_character),formula=formula_state(f),base=character_state(base),baseFont=lookup.get(base.pdf_style.font_id).name if lookup.get(base.pdf_style.font_id) else None,scriptFont=lookup.get(c.pdf_style.font_id).name if lookup.get(c.pdf_style.font_id) else None,sourceDelta=c.box.y-base.box.y,sourceBasePrefix=''.join(v.char_unicode or '' for v in before[-12:]),relocated=[])
                    unit=TypesettingUnit(formular=f)
                    for scale in (.5,1,1.5):
                        moved=unit.relocate(100,200,scale).formular
                        out=moved.pdf_character[f.pdf_character.index(c)]
                        item['relocated'].append(dict(scale=scale,delta=out.box.y-200,expected=(c.box.y-base.box.y)*scale))
                    report.append(item)
            return result
        StylesAndFormulas.process_page=process
    layout_guard.install=install
    spec=importlib.util.spec_from_file_location('offset_inspect',ROOT/'scripts/inspect-pdf-translation-input.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    out=Path(a.output);cap=out.with_suffix('.capture.json');sys.argv=[str(spec.origin),'--input',a.input,'--output',str(cap),'--pages',a.pages,'--index-only','--match','.'];mod.main()
    d=dict(source=a.input,sourceSHA256=hashlib.sha256(Path(a.input).read_bytes()).hexdigest(),layoutSHA256=hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest(),networkDisabled=True,translationRequests=0,candidates=report,repairs=repairs,failures=failures,layoutSnapshot=layout_guard.snapshot())
    out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n');print(json.dumps(dict(candidates=len(report),output=str(out))))
if __name__=='__main__':main()
