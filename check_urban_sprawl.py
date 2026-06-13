"""Validate F-1 multi-hex urban sprawl (AD-014).

Usage: uv run python check_urban_sprawl.py [output_json]

A city's footprint = all hexes whose settlement.parent_city == that city
(includes the centroid, which also carries its own parent_city). Prints the
footprint size + anthrome breakdown for the DoD target cities and the Ruhr,
and a PASS/FAIL line. Exit 0 = all targets met.
"""

import json
import sys
from collections import Counter

OUTPUT = sys.argv[1] if len(sys.argv) > 1 else \
    'output/para_bellum_benelux_germany_test_hex_terrain.json'

with open(OUTPUT, encoding='utf-8') as f:
    data = json.load(f)
hexes = data['hexes']

print(f'{OUTPUT}')
print(f'schema {data["schema_version"]}, {len(hexes)} hexes\n')

# Footprint per parent_city
by_city: dict[str, list] = {}
for h in hexes:
    pc = h['settlement'].get('parent_city', '')
    if pc:
        by_city.setdefault(pc, []).append(h)

suburb_n = sum(1 for h in hexes if h['settlement']['type'] == 'suburb')
print(f'cities with a multi-hex footprint: {sum(1 for v in by_city.values() if len(v) >= 2)}')
print(f'total suburb hexes: {suburb_n}\n')


def footprint(name_substr):
    """Union footprint of all parent_city names containing the substring."""
    hs = [h for pc, v in by_city.items() if name_substr.lower() in pc.lower() for h in v]
    return hs


# DoD per-city targets: (label, name substring, min hexes)
TARGETS = [
    ('Brussels',  'Brux',       3),
    ('Antwerp',   'Antwerp',    2),  # Antwerpen
    ('Amsterdam', 'Amsterdam',  3),
    ('Cologne',   'Köln',       3),
]
fails = []
for label, sub, need in TARGETS:
    hs = footprint(sub)
    # Antwerpen spelled with 'Antwerp' prefix
    if not hs and label == 'Antwerp':
        hs = footprint('Antwerp')
    n = len(hs)
    anthromes = Counter(h['settlement']['anthrome'] for h in hs)
    ok = n >= need
    if not ok:
        fails.append(f'{label} {n}/{need}')
    print(f'  {"PASS" if ok else "FAIL"}  {label:<10} {n} hexes (need >={need})  anthromes={dict(anthromes)}')

# Ruhr region: union of footprints in the Essen-Dortmund-Duisburg corridor box
RUHR_BOX = (6.5, 51.25, 7.65, 51.62)  # lon_min, lat_min, lon_max, lat_max
ruhr = [h for h in hexes
        if h['settlement'].get('parent_city')
        and RUHR_BOX[0] <= h['geo']['center_lon'] <= RUHR_BOX[2]
        and RUHR_BOX[1] <= h['geo']['center_lat'] <= RUHR_BOX[3]]
ruhr_ok = len(ruhr) >= 10
if not ruhr_ok:
    fails.append(f'Ruhr {len(ruhr)}/10')
ruhr_cities = sorted({h['settlement']['parent_city'] for h in ruhr})
print(f'  {"PASS" if ruhr_ok else "FAIL"}  Ruhr       {len(ruhr)} urban hexes (need >=10)  cities={ruhr_cities}')

# Anthrome sanity: every footprint hex has an urban anthrome; metro cores exist
all_fp = [h for v in by_city.values() for h in v]
metro_n = sum(1 for h in all_fp if h['settlement']['anthrome'] == 'metro')
bad_anthrome = [h['id'] for h in all_fp
                if h['settlement']['anthrome'] not in
                ('metro', 'industrial', 'residential', 'outskirts')]
print(f'\nfootprint hexes: {len(all_fp)}, metro cores: {metro_n}, '
      f'bad anthrome: {len(bad_anthrome)}')

# Regression guard (adversarial review): industrial landuse outranks the
# <3 km metro rule, so dominant-industrial hexes read 'industrial' rather than
# being masked to 'metro' near a core. Asserted bbox-wide (grid alignment +
# center-sampling decide which specific city carries one), and in the Ruhr —
# the canonical heavy-industry region — when that region is in the bbox.
ind_total = sum(1 for h in all_fp if h['settlement']['anthrome'] == 'industrial')
ruhr_ind = sum(1 for h in ruhr if h['settlement']['anthrome'] == 'industrial')
print(f'industrial footprint hexes (bbox-wide): {ind_total}; in Ruhr: {ruhr_ind}')
# Only enforce when the map is large enough to contain the industrial regions
# (the Belgium fast-iteration bbox is too small for a meaningful floor).
if len(hexes) > 1500:
    if ind_total < 8:
        fails.append(f'too few industrial footprint hexes ({ind_total}); '
                     'industrial-before-metro ordering may have regressed')
    if ruhr and ruhr_ind == 0:
        fails.append('Ruhr has no industrial hex (heavy-industry region expected)')

print('\n' + '=' * 50)
if fails or bad_anthrome:
    print(f'OVERALL: FAIL ({fails})')
    sys.exit(1)
print('OVERALL: PASS (all urban-sprawl targets met)')
