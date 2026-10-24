#!/usr/bin/env python3
"""Audit actual source promoted script proof + geometry, with zero model calls."""
import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'engine'))
import translation_guard


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--pages',default='14,44')
    args=parser.parse_args()
    rows=[];failures=[]
    original=translation_guard._promote_rich_scripts
    def promote(ti,paragraph,font_map=None):
        from babeldoc.format.pdf.document_il.midend.typesetting import TypesettingUnit
        source_placeholders={p.id:p for p in ti.placeholders}
        source_glyphs={id(c):translation_guard._script_glyph_state(c)
                       for p in ti.placeholders
                       for c in (p.composition.pdf_character if hasattr(p,'composition') else p.formula.pdf_character)}
        result,count=original(ti,paragraph,font_map)
        for p in result.placeholders:
            proof=getattr(p,'pagebridge_script_proof',None)
            if not proof:continue
            translation_guard._script_proof(p)
            glyphs=p.formula.pdf_character
            script=next(c for c in glyphs if c.char_unicode and c.char_unicode.strip())
            base_count=len(proof['glyphStates'])-len(glyphs)
            bases=[c for c,state in proof['glyphStates'][:base_count]]
            base=bases[-1]
            original_ids=source_placeholders[p.id]
            original_glyphs=(original_ids.composition.pdf_character if hasattr(original_ids,'composition') else original_ids.formula.pdf_character)
            identical=len(glyphs)==len(original_glyphs) and all(a is b for a,b in zip(glyphs,original_glyphs))
            unchanged=all(source_glyphs[id(c)]==translation_guard._script_glyph_state(c) for c in glyphs)
            if not identical or not unchanged:failures.append(dict(reason='glyph-identity-or-state-changed',id=p.id))
            def char(c):
                return dict(unicode=c.char_unicode,glyph=c.pdf_character_id,fontID=c.pdf_style.font_id,
                            fontName=getattr((font_map or {}).get(c.pdf_style.font_id),'name',None),
                            fontSize=c.pdf_style.font_size,box=asdict(c.box),xobj=c.xobj_id)
            r=dict(paragraphSHA256=hashlib.sha256((paragraph.unicode or '').encode()).hexdigest(),
                   sourceParagraph=paragraph.unicode,translationInput=result.unicode,placeholderID=p.id,
                   kind=proof['kind'],base=proof['base'],value=proof.get('value'),baseGlyphs=[char(c) for c in bases],
                   scriptGlyphs=[char(c) for c in glyphs],originalGlyphObjectsPreserved=identical,
                   originalGlyphStatesPreserved=unchanged,formulaOffset=dict(x=p.formula.x_offset,y=p.formula.y_offset),
                   sourceBaselineDelta=script.box.y-base.box.y,relocations=[])
            for scale in (.5,1,1.5):
                moved=TypesettingUnit(formular=p.formula).relocate(100,200,scale).formular
                delta=moved.pdf_character[glyphs.index(script)].box.y-200
                expected=(script.box.y-base.box.y)*scale
                correct=abs(delta-expected)<.05
                r['relocations'].append(dict(scale=scale,expected=expected,actual=delta,passed=correct))
                if not correct:failures.append(dict(reason='baseline-changed',id=p.id,expected=expected,actual=delta))
            rows.append(r)
        return result,count
    translation_guard._promote_rich_scripts=promote
    spec=importlib.util.spec_from_file_location('promoted_identity_audit',ROOT/'scripts/audit-guard-source.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    out=Path(args.output)
    identity=out.with_name(out.stem+'-identity.json')
    sys.argv=[str(spec.origin),'--input',args.input,'--output',str(identity),'--pages',args.pages]
    mod.main()
    identity_report=json.loads(identity.read_text())
    out.write_text(json.dumps(dict(source=args.input,sourceSHA256=identity_report['sourceSHA256'],
          pages=args.pages,engineSourceSHA256=identity_report['engineSourceSHA256'],
          engineSourcesUnchanged=identity_report['engineSourcesUnchanged'],networkDisabled=True,translationRequests=0,
          sourceIdentityPassed=identity_report['sourceIdentityPassed'],identityTotals=identity_report['totals'],
          promotedSourceProofs=rows,failures=failures),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(sourceProofs=len(rows),failures=len(failures),output=str(out))))


if __name__=='__main__':main()
