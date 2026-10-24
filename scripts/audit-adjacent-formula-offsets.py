#!/usr/bin/env python3
"""Audit source-only adjacent number offsets, including full glyph identity."""
import argparse,hashlib,importlib.util,json,sys
from dataclasses import asdict
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'engine'))
import layout_guard


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',required=True);ap.add_argument('--output',required=True);ap.add_argument('--pages',default='1-68');a=ap.parse_args()
    original=layout_guard.repair_script_following_formula_offsets
    rows=[];failures=[]
    def state(f):
        return dict(box=asdict(f.box) if f.box else None,x_offset=f.x_offset,y_offset=f.y_offset,
                    x_advance=f.x_advance,line_id=f.line_id,is_corner_mark=f.is_corner_mark,
                    glyphs=[dict(identity=id(c),char=c.char_unicode,glyph=c.pdf_character_id,
                           box=asdict(c.box),visual=asdict(c.visual_bbox) if c.visual_bbox else None,
                           font=c.pdf_style.font_id,size=c.pdf_style.font_size,graphicStateID=id(c.pdf_style.graphic_state),
                           xobj=c.xobj_id,advance=c.advance,scale=c.scale,render_order=c.render_order) for c in f.pdf_character])
    def repair(page):
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        formulas=[(p,c.pdf_formula) for p in page.pdf_paragraph for c in p.pdf_paragraph_composition if c.pdf_formula]
        before={id(f):state(f) for p,f in formulas}
        n=original(page)
        for p,f in formulas:
            old=before[id(f)];new=state(f);before_y=old.pop('y_offset');after_y=new.pop('y_offset')
            if old!=new:failures.append(dict(page=page.page_number+1,reason='non-offset-mutation'))
            if before_y==after_y:continue
            chars=[c for comp in p.pdf_paragraph_composition for c in layout_guard._composition_characters(comp)]
            first=next(i for i,c in enumerate(chars) if c is f.pdf_character[0]);base=chars[first-2];script=chars[first-1]
            row=dict(page=page.page_number+1,paragraphSHA256=hashlib.sha256((p.unicode or '').encode()).hexdigest(),
                     sourceText=p.unicode,base=base.char_unicode,script=script.char_unicode,
                     formulaText=''.join(c.char_unicode or '' for c in f.pdf_character),before=before_y,after=after_y,
                     sourceBodyY=base.box.y,sourceScriptY=script.box.y,glyphs=new['glyphs'],relocations=[])
            for scale in (.5,1,1.5):
                moved=TypesettingUnit(formular=f).relocate(100,200,scale).formular
                deltas=[c.box.y-200 for c in moved.pdf_character]
                expected=[(c.box.y-base.box.y)*scale for c in f.pdf_character]
                passed=all(abs(x-y)<.05 for x,y in zip(deltas,expected))
                row['relocations'].append(dict(scale=scale,actual=deltas,expected=expected,passed=passed))
                if not passed:failures.append(dict(page=page.page_number+1,reason='bad-relocation'))
            rows.append(row)
        if original(page):failures.append(dict(page=page.page_number+1,reason='not-idempotent'))
        return n
    layout_guard.repair_script_following_formula_offsets=repair
    spec=importlib.util.spec_from_file_location('adjacent_inspect',ROOT/'scripts/inspect-pdf-translation-input.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    out=Path(a.output);sys.argv=[str(spec.origin),'--input',a.input,'--output',str(out.with_suffix('.capture.json')),'--pages',a.pages,'--index-only','--match','.'];mod.main()
    out.write_text(json.dumps(dict(source=a.input,sourceSHA256=hashlib.sha256(Path(a.input).read_bytes()).hexdigest(),
           layoutSHA256=hashlib.sha256((ROOT/'engine/layout_guard.py').read_bytes()).hexdigest(),networkDisabled=True,
           translationRequests=0,repairs=rows,failures=failures,layoutSnapshot=layout_guard.snapshot()),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(repairs=len(rows),failures=len(failures),output=str(out))))


if __name__=='__main__':main()
