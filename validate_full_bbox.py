"""Sprint 2 full-bbox validation gate (Benelux + Western Germany).

Usage: uv run python validate_full_bbox.py [output_json]
Exit code 0 = all checks pass, 1 = at least one failure.

Country-count bands are derived from country land areas inside the bbox.
Recalibrated for AD-013 (Sprint 3): hex size is 10 km flat-to-flat ⇒ area
≈ 86.6 km²/hex, so this bbox now yields ~2,479 hexes (was 840 under the old
circumradius misreading — the original sprint estimate of ~2,500 was right
all along). Plus the grid-coverage fix (sampling all four bbox edges) closed
the SE wedge so Frankfurt is now tiled. Bands below follow the measured
geometry; the task brief's "30/30/5/30/5%" split is geometry-blind (Belgium
is 13% of this bbox's area, not 30%).
"""

import json
import math
import sys

OUTPUT = sys.argv[1] if len(sys.argv) > 1 else \
    'output/para_bellum_benelux_germany_test_hex_terrain.json'

with open(OUTPUT, encoding='utf-8') as f:
    data = json.load(f)

hexes = data['hexes']
by_id = {h['id']: h for h in hexes}
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = ''):
    checks.append((name, ok, detail))


def closest_hex(lat: float, lon: float) -> dict:
    return min(hexes, key=lambda h: math.hypot(
        h['geo']['center_lat'] - lat,
        (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat))))


# --- Schema -----------------------------------------------------------------
check('schema_version is 1.0.4', data['schema_version'] == '1.0.4',
      f"got {data['schema_version']}")

# --- Hex count ---------------------------------------------------------------
# 6.3 x 4.2 deg ≈ 230k km² / 86.6 km² per hex ≈ 2,470 (AD-013 flat-to-flat).
check('hex count in [2350, 2650]', 2350 <= len(hexes) <= 2650, f'{len(hexes)} hexes')

# --- Country distribution ----------------------------------------------------
counts: dict[str, int] = {}
for h in hexes:
    c = h['political']['country_at_start']
    counts[c] = counts.get(c, 0) + 1

# Bands recalibrated to AD-013 measured counts (~2,479 hexes): DEU dominates
# (Western Germany + the now-covered Frankfurt wedge), then NLD, BEL, FRA, LUX.
bands = {
    'BEL': (300, 410),
    'NLD': (340, 460),
    'LUX': (20, 42),
    'DEU': (820, 990),
    'FRA': (230, 330),
}
for code, (lo, hi) in bands.items():
    n = counts.get(code, 0)
    check(f'{code} hexes in [{lo},{hi}]', lo <= n <= hi, f'{code}={n}')

water_n = sum(1 for h in hexes if h['flags']['is_water'])
check('water hexes > 300 (North Sea + IJsselmeer)', water_n > 300, f'{water_n} water')

land_empty = sum(1 for h in hexes
                 if not h['flags']['is_water'] and not h['political']['country_at_start'])
check('land hexes without country <= 1% of land',
      land_empty <= (len(hexes) - water_n) * 0.01, f'{land_empty} empty land hexes')

# --- Major city spot checks ---------------------------------------------------
# type accepts city|metropolis: the pipeline types from the OSM population
# tag, which is city-proper (Brussels node says 194k → city, not metropolis).
CITIES = [
    ('Brussels',        50.85, 4.35, ('Bruxelles', 'Brussel'), 'BEL'),
    ('Antwerp',         51.22, 4.40, ('Antwerpen',),           'BEL'),
    ('Amsterdam',       52.37, 4.90, ('Amsterdam',),           'NLD'),
    ('Rotterdam',       51.92, 4.48, ('Rotterdam',),           'NLD'),
    ('Luxembourg City', 49.61, 6.13, ('Luxembourg',),          'LUX'),
    ('Cologne',         50.94, 6.96, ('Köln', 'Cologne'),      'DEU'),
    ('Düsseldorf',      51.23, 6.78, ('Düsseldorf',),          'DEU'),
    ('Frankfurt',       50.11, 8.68, ('Frankfurt',),           'DEU'),
]
for label, lat, lon, name_keys, country in CITIES:
    h = closest_hex(lat, lon)
    s = h['settlement']
    name_ok = any(k in s['name'] for k in name_keys)
    type_ok = s['type'] in ('city', 'metropolis')
    country_ok = h['political']['country_at_start'] == country
    check(f'{label}: tagged city/metropolis, {country}',
          name_ok and type_ok and country_ok,
          f"hex {h['id']}: name='{s['name']}' type={s['type']} "
          f"country={h['political']['country_at_start']}")

# --- River chains --------------------------------------------------------------
river = {h['id'] for h in hexes if h['terrain']['river_edges']}


def components(parity_flip: bool):
    def nbrs(hid):
        col, row = int(hid[:3]), int(hid[3:])
        odd = (col % 2 == 1) != parity_flip
        deltas = ([(1, 1), (1, 0), (0, -1), (-1, 0), (-1, 1), (0, 1)] if odd
                  else [(1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (0, 1)])
        return [f'{col + dc:03d}{row + dr:02d}' for dc, dr in deltas
                if f'{col + dc:03d}{row + dr:02d}' in by_id]

    comp_of: dict[str, int] = {}
    comps: list[set] = []
    for h in sorted(river):
        if h in comp_of:
            continue
        stack, comp = [h], set()
        while stack:
            cur = stack.pop()
            if cur in comp_of:
                continue
            comp_of[cur] = len(comps)
            comp.add(cur)
            stack.extend(n for n in nbrs(cur) if n in river and n not in comp_of)
        comps.append(comp)
    return comp_of, comps


# Grid column parity depends on the grid offset, which varies per bbox —
# evaluate both interpretations and use the one with fewer isolated hexes.
results = [components(False), components(True)]
comp_of, comps = min(results, key=lambda t: sum(1 for c in t[1] if len(c) == 1))


def all_neighbors(hid, parity_flip):
    """The 6 neighbor IDs (in-grid or not) — for interior/boundary test."""
    col, row = int(hid[:3]), int(hid[3:])
    odd = (col % 2 == 1) != parity_flip
    deltas = ([(1, 1), (1, 0), (0, -1), (-1, 0), (-1, 1), (0, 1)] if odd
              else [(1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (0, 1)])
    return [f'{col + dc:03d}{row + dr:02d}' for dc, dr in deltas]


# An isolated river hex on the GRID BOUNDARY is legitimate: its river simply
# continues off-map. Only flag INTERIOR isolated hexes (all 6 neighbors exist
# in the grid) — those indicate spurious scattered river tagging.
parity = 0 if results[0][1] is comps else 1  # which parity won above
all_isolated = [next(iter(c)) for c in comps if len(c) == 1]
interior_isolated = [h for h in all_isolated
                     if all(n in by_id for n in all_neighbors(h, bool(parity)))]
check('no INTERIOR isolated single-hex river edges', not interior_isolated,
      f'interior isolated: {interior_isolated[:8]} (all isolated: {all_isolated[:8]})')

RIVER_POINTS = [
    ('Meuse at Liège',      50.64, 5.57),
    ('Meuse at Venlo',      51.37, 6.17),
    ('Rhine at Cologne',    50.94, 6.96),
    ('Rhine at Bonn',       50.73, 7.10),
    ('Scheldt at Antwerp',  51.22, 4.40),
    ('Albertkanaal/Hasselt', 50.93, 5.34),
]
for label, lat, lon in RIVER_POINTS:
    h = closest_hex(lat, lon)
    on_river = h['id'] in river
    chain = len(comps[comp_of[h['id']]]) if on_river else 0
    check(f'{label}: river hex in chain of >= 3',
          on_river and chain >= 3, f"hex {h['id']} chain_size={chain}")

# --- Proportions ----------------------------------------------------------------
# Post-AD-013 river % runs ~20% here (16.6% Belgium): river-touching hexes
# scale ~linearly with hex size while total hexes scale quadratically, so the
# finer grid gives a lower fraction for the same strategic rivers.
river_pct = 100 * len(river) / len(hexes)
check('river hexes 12-28% (AD-013 scale)',
      12 <= river_pct <= 28, f'{len(river)} hexes, {river_pct:.1f}%')

# 40% cap: NL Randstad + Ruhr genuinely carpet the map with >=20k towns.
# Pre-F-1 this runs ~17%; F-1 multi-hex urban will raise it (suburb hexes) —
# the cap keeps headroom for that while still catching a tagging regression.
# A regression in the significance floors would blow well past 40%.
settled = sum(1 for h in hexes if h['settlement']['type'] != 'none')
check('settlement hexes <= 40% (dense Randstad/Ruhr region)',
      settled <= 0.40 * len(hexes),
      f'{settled} hexes, {100 * settled / len(hexes):.1f}%')

cities_n = sum(1 for h in hexes if h['settlement']['type'] in ('city', 'metropolis'))
check('>= 25 cities/metropolises at this scale', cities_n >= 25, f'{cities_n}')

bridges = sum(1 for h in hexes if h['infrastructure']['bridge'])
check('bridge hexes roughly track river hexes (>= 50%)',
      bridges >= 0.5 * len(river), f'{bridges} bridges vs {len(river)} river hexes')

# --- Multi-hex urban sprawl (F-1, AD-014) -----------------------------------------
# A city's footprint = hexes sharing its parent_city (centroid + suburb ring).
by_city: dict[str, list] = {}
for h in hexes:
    pc = h['settlement'].get('parent_city', '')
    if pc:
        by_city.setdefault(pc, []).append(h)


def footprint_size(*substrs):
    return sum(1 for pc, v in by_city.items()
               if any(s.lower() in pc.lower() for s in substrs) for _ in v)


for label, need, subs in [
    ('Brussels', 3, ('Brux',)),
    ('Antwerp', 2, ('Antwerp',)),       # Antwerpen
    ('Amsterdam', 3, ('Amsterdam',)),
    ('Cologne', 3, ('Köln',)),
]:
    n = footprint_size(*subs)
    check(f'{label} spans >= {need} hexes', n >= need, f'{n} hexes')

ruhr = [h for h in hexes
        if h['settlement'].get('parent_city')
        and 6.5 <= h['geo']['center_lon'] <= 7.65
        and 51.25 <= h['geo']['center_lat'] <= 51.62]
check('Ruhr region >= 10 urban hexes', len(ruhr) >= 10, f'{len(ruhr)} hexes')

fp_hexes = [h for v in by_city.values() for h in v]
bad_anthrome = [h['id'] for h in fp_hexes
                if h['settlement']['anthrome'] not in
                ('metro', 'industrial', 'residential', 'outskirts')]
check('all footprint hexes have an urban anthrome', not bad_anthrome,
      f'{len(fp_hexes)} footprint hexes, {len(bad_anthrome)} bad')

suburb_no_parent = [h['id'] for h in hexes
                    if h['settlement']['type'] == 'suburb'
                    and not h['settlement'].get('parent_city')]
check('every suburb hex has a parent_city', not suburb_no_parent,
      f'{len(suburb_no_parent)} orphan suburbs')

# --- Strategic resources (F-2) ----------------------------------------------------
res_counts = {r: sum(1 for h in hexes if h['resources'].get(r))
              for r in ('coal', 'steel', 'iron', 'oil')}
check('coal hexes present (Ruhr/Saar/Sambre/Limburg)', res_counts['coal'] >= 10,
      f"coal={res_counts['coal']}")
check('steel hexes present (Ruhr/Liège works)', res_counts['steel'] >= 3,
      f"steel={res_counts['steel']}")
check('iron hexes present (Lorraine)', res_counts['iron'] >= 1,
      f"iron={res_counts['iron']}")

essen = closest_hex(51.45, 7.01)
check('Essen hex has coal AND steel',
      essen['resources'].get('coal') and essen['resources'].get('steel'),
      f"hex {essen['id']} coal={essen['resources'].get('coal')} steel={essen['resources'].get('steel')}")
liege = closest_hex(50.61, 5.54)
check('Liège hex has steel', liege['resources'].get('steel'),
      f"hex {liege['id']} steel={liege['resources'].get('steel')}")

# --- Rivers: hex-center node model (P0-A, AD-026) ---------------------------------
has_river = [h for h in hexes if h.get('rivers', {}).get('has_river')]
check('has_river hexes present', len(has_river) > 0, f'{len(has_river)} hexes')
name_mismatch = [h['id'] for h in hexes
                 if bool(h.get('rivers', {}).get('has_river'))
                 != bool(h.get('rivers', {}).get('river_name'))]
check('river_name set iff has_river', not name_mismatch, f'{len(name_mismatch)} mismatches')

# --- Provinces + admin tiers (P0-B, AD-023/AD-027) --------------------------------
from collections import defaultdict
provs = sorted({h['political']['province_at_start'] for h in hexes
                if h['political']['province_at_start']})
cap_by_prov = defaultdict(int)
sub_total = 0
for h in hexes:
    t = h['settlement']['admin_tier']
    if t == 'capital':
        cap_by_prov[h['political']['province_at_start']] += 1
    elif t == 'sub_capital':
        sub_total += 1
n_caps = sum(1 for p in provs if cap_by_prov[p] >= 1)
multi_cap = [p for p in provs if cap_by_prov[p] > 1]
land = [h for h in hexes if not h['flags']['is_water'] and h['political']['country_at_start']]
no_prov = [h for h in land if not h['political']['province_at_start']]
bad_settled = [h['id'] for h in hexes
               if h['political']['province_at_start'] and h['settlement']['type'] != 'none'
               and h['settlement']['admin_tier'] not in ('capital', 'sub_capital', 'urban')]
rural_named = [h['id'] for h in hexes
               if h['settlement']['admin_tier'] == 'rural' and h['settlement']['name']]

# "30+ capitals" is a property of the AUTHORED province layer; a clipped bbox
# only frames the capitals inside it (the Benelux bbox omits 8 southern-French /
# Hannover / Saar capitals). So the totals gate reads the metadata; the run gate
# is structural (no province with >1 capital; every framed province has one).
import os as _os
_mdp = 'data/boundaries/provinces_1930_metadata.json'
authored_provs = authored_caps = authored_subs = 0
if _os.path.exists(_mdp):
    _md = json.load(open(_mdp, encoding='utf-8'))
    authored_provs = len(_md['provinces'])
    authored_caps = sum(1 for p in _md['provinces'] if p.get('capital', {}).get('city_name'))
    authored_subs = sum(len(p.get('sub_capitals', [])) for p in _md['provinces'])
framed = [p for p in provs if cap_by_prov[p]]
check('30+ provinces tagged in run', len(provs) >= 30, f'{len(provs)} provinces')
check('authored layer has 30+ capitals', authored_caps >= 30,
      f'{authored_caps} authored, {n_caps} framed in this bbox')
check('authored layer has ~50-80 sub-capitals', 50 <= authored_subs <= 80, f'{authored_subs}')
check('no province has >1 capital hex', not multi_cap, f'{len(multi_cap)}: {multi_cap[:5]}')
check('every framed province has exactly one capital',
      all(cap_by_prov[p] == 1 for p in framed), f'{len(framed)} framed')
check('sub-capital hexes present (30+)', sub_total >= 30, f'{sub_total} sub-capitals')
check('province coverage of land >= 98%',
      len(no_prov) <= 0.02 * max(1, len(land)),
      f'{len(no_prov)} of {len(land)} land hexes uncovered')
check('settled in-province hexes are capital/sub/urban', not bad_settled,
      f'{len(bad_settled)} violations')
check('no rural hex carries a settlement name', not rural_named, f'{len(rural_named)}')

# province spot-checks
for label, lat, lon, want in [
    ('Köln -> DEU_RHEINLAND', 50.94, 6.96, 'DEU_RHEINLAND'),
    ('Münster -> DEU_WESTFALEN', 51.96, 7.63, 'DEU_WESTFALEN'),
    ('Maastricht -> NLD_LIMBURG', 50.85, 5.69, 'NLD_LIMBURG'),
    ('Liège -> BEL_LIEGE', 50.63, 5.57, 'BEL_LIEGE'),
]:
    h = closest_hex(lat, lon)
    check(f'{label}', h['political']['province_at_start'] == want,
          f"hex {h['id']} -> {h['political']['province_at_start'] or '(none)'}")

# --- Summary ----------------------------------------------------------------------
print(f'Full-bbox validation — {OUTPUT}')
print(f'{len(hexes)} hexes, schema {data["schema_version"]}')
print(f'country distribution: { {k or "(water/none)": v for k, v in sorted(counts.items(), key=lambda kv: -kv[1])} }\n')
failed = 0
for name, ok, detail in checks:
    mark = 'PASS' if ok else 'FAIL'
    if not ok:
        failed += 1
    print(f'  {mark}  {name}' + (f'  [{detail}]' if detail else ''))

print('\n' + '=' * 50)
if failed:
    print(f'OVERALL: FAIL ({failed}/{len(checks)} checks failed)')
    sys.exit(1)
print(f'OVERALL: PASS ({len(checks)}/{len(checks)} checks)')
