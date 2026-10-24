#!/usr/bin/env python3
"""Read-only regression audit for the three supplied document P14 quantities.

This is a validation fixture, not a generic recognition rule or production fix.
Every expected measurement must be found exactly once in both PDFs.
"""
import argparse
import hashlib
import json
import unicodedata
from pathlib import Path

import fitz


def inspect(path):
    result = []
    with fitz.open(path) as document:
        chars = [dict(c, font=span['font'], size=span['size'])
                 for block in document[13].get_text('rawdict')['blocks']
                 for line in block.get('lines', [])
                 for span in line['spans'] for c in span['chars']]
        for variable, suffix in [('U', '10.5V'), ('I', '17.1mA'), ('P', '45mW')]:
            matches = []
            expected = variable + 'o=' + suffix
            for base in chars:
                if base['c'] != variable:
                    continue
                x, y = base['origin']
                nearby = sorted([c for c in chars
                                 if x - .05 <= c['origin'][0] <= x + 160
                                 and abs(c['origin'][1] - y) <= 5.0
                                 and not c['c'].isspace()
                                 and not unicodedata.category(c['c']).startswith('C')],
                                key=lambda c: c['origin'][0])
                visible = ''.join(c['c'] for c in nearby)
                if not visible.startswith(expected):
                    continue
                selected = nearby[:len(expected)]
                body = [c for c in selected if c is not selected[1]]
                deltas = [c['origin'][1] - y for c in body]
                script_delta = selected[1]['origin'][1] - y
                matches.append(dict(text=expected, bodyBaselineDeltas=deltas,
                                    scriptBaselineDelta=script_delta,
                                    bodyAligned=max(abs(v) for v in deltas) < .05,
                                    scriptLowered=script_delta > .2 * base['size'],
                                    glyphs=selected))
            result.append(dict(expected=expected, matches=matches,
                               passed=len(matches) == 1 and matches[0]['bodyAligned']
                               and matches[0]['scriptLowered']))
    return dict(path=str(path), SHA256=hashlib.sha256(path.read_bytes()).hexdigest(),
                page=14, measurements=result, passed=all(r['passed'] for r in result))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original', type=Path, required=True)
    parser.add_argument('--translated', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, translated = inspect(args.original), inspect(args.translated)
    result = dict(readOnly=True, modelCalls=0, source=source, translated=translated,
                  passed=source['passed'] and translated['passed'],
                  scope='Known P14 validation fixture; not a general mathematical equivalence check.')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(passed=result['passed'], output=str(args.output))))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
