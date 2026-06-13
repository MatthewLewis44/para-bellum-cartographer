"""Validate F-2 strategic resources ingest.

Usage: uv run python check_resources.py [output_json]

Spot-checks the DoD resource hexes (Essen coal+steel, Liège steel,
Saarbrücken coal) and prints the resource hex distribution. Exit 0 = pass.
"""

import json
import math
import sys

OUTPUT = sys.argv[1] if len(sys.argv) > 1 else \
    'output/para_bellum_benelux_germany_test_hex_terrain.json'

with open(OUTPUT, encoding='utf-8') as f:
    data = json.load(f)
hexes = data['hexes']


def closest(lat, lon):
    return min(hexes, key=lambda h: math.hypot(
        h['geo']['center_lat'] - lat,
        (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat))))


print(f'{OUTPUT}')
print(f'schema {data["schema_version"]}, {len(hexes)} hexes\n')

counts = {r: sum(1 for h in hexes if h['resources'].get(r)) for r in
          ('coal', 'steel', 'iron', 'oil')}
print(f'resource hex counts: {counts}\n')

# (label, lat, lon, required resources present)
SPOT = [
    ('Essen',        51.45, 7.01, ('coal', 'steel')),
    ('Liège',        50.61, 5.54, ('steel',)),
    ('Saarbrücken',  49.41, 6.99, ('coal',)),   # at the bbox south edge
    ('Charleroi',    50.41, 4.44, ('coal',)),
]
fails = []
for label, lat, lon, need in SPOT:
    h = closest(lat, lon)
    res = h['resources']
    have = [r for r in ('coal', 'steel', 'iron', 'oil') if res.get(r)]
    ok = all(res.get(r) for r in need)
    # Saarbrücken sits at/below min_lat 49.4; tolerate if its nearest hex is
    # >15 km away (genuinely outside coverage).
    dkm = math.hypot((h['geo']['center_lat'] - lat) * 111,
                     (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat)) * 111)
    if not ok and label == 'Saarbrücken' and dkm > 15:
        print(f'  SKIP  {label:<12} nearest hex {dkm:.0f} km away (below bbox) — out of scope')
        continue
    if not ok:
        fails.append(f'{label} needs {need}, has {have}')
    print(f'  {"PASS" if ok else "FAIL"}  {label:<12} hex {h["id"]} resources={have} (need {list(need)})')

print('\n' + '=' * 50)
if fails:
    print(f'OVERALL: FAIL ({fails})')
    sys.exit(1)
print('OVERALL: PASS (resource spot-checks met)')
