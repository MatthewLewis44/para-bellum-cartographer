"""Validate F-2 strategic resources ingest.

Usage: uv run python check_resources.py [output_json]

Gates (exit non-zero on failure):
  1. EVERY resource type appears on at least one hex. A zero-count resource
     is a hard failure — this is the gate that would have caught the missing
     oil that blocked Sprint 9 (Pre-Sprint 9.0).
  2. Per-nation coverage floor: every nation in NATION_FLOOR that is framed
     by this artifact holds at least one hex of at least three DISTINCT
     resource types. This is the gate that encodes "contested scarcity
     exists" — a division costing three things must be buildable by more
     than one power.
  3. Spot-checks that named 1930 districts land on the right hexes. Checks
     whose nearest hex is far away are SKIPped, not failed, so the same
     table works for every bbox.

Also REPORTS (does not assert) the full resource-hex matrix by resource x
country and by resource x province. Those numbers are a deliverable: the PM
tunes base yields against them.
"""

import json
import math
import sys
from collections import Counter, defaultdict

OUTPUT = sys.argv[1] if len(sys.argv) > 1 else \
    'output/para_bellum_east_expansion_hex_terrain.json'

TYPES = ('coal', 'steel', 'iron', 'oil')

# Nations that must hold >= this many distinct resource types, IF the
# artifact frames them with at least MIN_LAND_HEXES land hexes. Nations the
# bbox doesn't reach are skipped, so Belgium/Benelux artifacts still pass.
# AUT 3 -> 2 (AD-044): its third type was steel, now authored mills.
NATION_FLOOR = {'DEU': 3, 'POL': 3, 'CSK': 3, 'AUT': 2}
MIN_LAND_HEXES = 200

# (label, lat, lon, required resources present). No steel: the works points
# retired into authored mills (AD-044); see tools/build_facilities_1930.py.
SPOT = [
    # western (Sprint 2/3)
    ('Essen',        51.45,  7.01, ('coal',)),
    ('Saarbrücken',  49.41,  6.99, ('coal',)),
    ('Charleroi',    50.41,  4.44, ('coal',)),
    # eastern (Pre-Sprint 9.0)
    ('Katowice',     50.26, 19.02, ('coal',)),
    ('Chorzów',      50.30, 18.95, ('coal',)),
    ('Ostrava',      49.82, 18.29, ('coal',)),
    ('Most/Brüx',    50.53, 13.64, ('coal',)),
    ('Senftenberg',  51.52, 13.99, ('coal',)),
    ('Częstochowa',  50.81, 19.10, ('iron',)),
    ('Erzberg',      47.54, 14.88, ('iron',)),
    ('Borysław',     49.29, 23.36, ('oil',)),
]

with open(OUTPUT, encoding='utf-8') as f:
    data = json.load(f)
hexes = data['hexes']
land = [h for h in hexes if not h['flags']['is_water']]


def closest(lat, lon):
    return min(hexes, key=lambda h: math.hypot(
        h['geo']['center_lat'] - lat,
        (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat))))


def km_to(h, lat, lon):
    return math.hypot((h['geo']['center_lat'] - lat) * 111,
                      (h['geo']['center_lon'] - lon)
                      * math.cos(math.radians(lat)) * 111)


fails = []


def check(label, ok, detail=''):
    print(f'  {"PASS" if ok else "FAIL"}  {label}' + (f'  — {detail}' if detail else ''))
    if not ok:
        fails.append(label)


print(f'{OUTPUT}')
print(f'schema {data["schema_version"]}, {len(hexes)} hexes '
      f'({len(land)} land)\n')

# --- tallies ---------------------------------------------------------------
counts = {r: sum(1 for h in hexes if h['resources'].get(r)) for r in TYPES}
agri = sum(1 for h in hexes if h['resources'].get('agriculture'))

by_country: dict[str, Counter] = defaultdict(Counter)
by_prov: dict[str, Counter] = defaultdict(Counter)
land_by_country: Counter = Counter()
for h in land:
    c = h['political']['country_at_start'] or '(none)'
    land_by_country[c] += 1
for h in hexes:
    c = h['political']['country_at_start'] or '(none)'
    p = h['political']['province_at_start'] or '(none)'
    for r in TYPES:
        if h['resources'].get(r):
            by_country[c][r] += 1
            by_prov[p][r] += 1

print(f'resource hex counts: {counts}   (agriculture={agri}, derived from '
      f'OSM landuse — not from the authored layer)\n')

# --- REPORT: the matrix the PM tunes yields against ------------------------
print('=' * 66)
print('RESOURCE-HEX MATRIX BY NATION')
print('=' * 66)
hdr = f'  {"nation":<8} {"land":>6} ' + ' '.join(f'{r:>6}' for r in TYPES) + '   types'
print(hdr)
for c in sorted(by_country, key=lambda k: -sum(by_country[k].values())):
    ntypes = sum(1 for r in TYPES if by_country[c][r])
    print(f'  {c:<8} {land_by_country[c]:>6} '
          + ' '.join(f'{by_country[c][r]:>6}' for r in TYPES)
          + f'   {ntypes}')
nations_with_none = sorted(c for c in land_by_country
                           if land_by_country[c] >= MIN_LAND_HEXES
                           and not by_country[c])
if nations_with_none:
    print(f'  (framed nations with NO resources at all: '
          f'{", ".join(nations_with_none)})')

print()
print('=' * 66)
print('RESOURCE-HEX MATRIX BY PROVINCE (non-empty)')
print('=' * 66)
for p in sorted(by_prov, key=lambda k: (-sum(by_prov[k].values()), k)):
    row = '  '.join(f'{r}={by_prov[p][r]}' for r in TYPES if by_prov[p][r])
    print(f'  {p:<30} {row}')

# --- GATE 1: no resource type may be absent --------------------------------
print()
print('=' * 66)
print('GATE 1 — every resource type present on at least one hex')
print('=' * 66)
for r in TYPES:
    check(f'{r} present', counts[r] > 0, f'{counts[r]} hexes')

# --- GATE 2: per-nation coverage floor -------------------------------------
print()
print('=' * 66)
print(f'GATE 2 — framed nations hold >= 3 distinct resource types')
print('=' * 66)
for nation, floor in NATION_FLOOR.items():
    if land_by_country[nation] < MIN_LAND_HEXES:
        print(f'  SKIP  {nation} not framed by this bbox '
              f'({land_by_country[nation]} land hexes)')
        continue
    have = [r for r in TYPES if by_country[nation][r]]
    check(f'{nation} holds >= {floor} distinct resource types',
          len(have) >= floor, f'{len(have)}: {have}')

# --- GATE 3: spot checks ---------------------------------------------------
print()
print('=' * 66)
print('GATE 3 — named 1930 districts land on the right hexes')
print('=' * 66)
for label, lat, lon, need in SPOT:
    h = closest(lat, lon)
    dkm = km_to(h, lat, lon)
    if dkm > 15:
        print(f'  SKIP  {label:<12} nearest hex {dkm:.0f} km away — outside this bbox')
        continue
    res = h['resources']
    have = [r for r in TYPES if res.get(r)]
    ok = all(res.get(r) for r in need)
    check(f'{label:<12} needs {list(need)}', ok, f'hex {h["id"]} has {have}')

print('\n' + '=' * 66)
if fails:
    print(f'OVERALL: FAIL ({len(fails)}) — {fails}')
    sys.exit(1)
print('OVERALL: PASS (all resource gates met)')
