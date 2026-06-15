"""Hex-for-hex diff of two pipeline output JSONs (Sprint 4 T7).

Usage: uv run python compare_hex_outputs.py <baseline.json> <candidate.json>

Compares the per-hex records field-by-field, ignoring volatile top-level
metadata (generated_at). Exit 0 = identical hex set + fields, 1 = any diff.
"""

import json
import sys

a = json.load(open(sys.argv[1], encoding='utf-8'))
b = json.load(open(sys.argv[2], encoding='utf-8'))

ha = {h['id']: h for h in a['hexes']}
hb = {h['id']: h for h in b['hexes']}

print(f'baseline: {len(ha)} hexes ({sys.argv[1]})')
print(f'candidate: {len(hb)} hexes ({sys.argv[2]})')

only_a = sorted(set(ha) - set(hb))
only_b = sorted(set(hb) - set(ha))
if only_a:
    print(f'  HEXES ONLY IN BASELINE ({len(only_a)}): {only_a[:10]}')
if only_b:
    print(f'  HEXES ONLY IN CANDIDATE ({len(only_b)}): {only_b[:10]}')

diffs = 0
field_diff_counts: dict[str, int] = {}
examples: list[str] = []
for hid in sorted(set(ha) & set(hb)):
    fa, fb = json.dumps(ha[hid], sort_keys=True), json.dumps(hb[hid], sort_keys=True)
    if fa == fb:
        continue
    diffs += 1
    # find which leaf fields differ
    def flat(d, pfx=''):
        out = {}
        for k, v in d.items():
            if isinstance(v, dict):
                out.update(flat(v, f'{pfx}{k}.'))
            else:
                out[f'{pfx}{k}'] = v
        return out
    da, db = flat(ha[hid]), flat(hb[hid])
    for k in set(da) | set(db):
        if da.get(k) != db.get(k):
            field_diff_counts[k] = field_diff_counts.get(k, 0) + 1
            if len(examples) < 12:
                examples.append(f'  {hid}.{k}: {da.get(k)!r} -> {db.get(k)!r}')

print(f'\nhexes differing: {diffs}')
if field_diff_counts:
    print('field diff counts:', dict(sorted(field_diff_counts.items(), key=lambda kv: -kv[1])))
    print('examples:')
    for e in examples:
        print(e)

ok = not (only_a or only_b or diffs)
print('\n' + ('IDENTICAL' if ok else 'DIFFERENT'))
sys.exit(0 if ok else 1)
