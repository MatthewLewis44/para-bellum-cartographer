"""Sprint 2 done gate — runs all output checks and prints one PASS/FAIL summary.

Usage: uv run python validate_sprint2.py
Exit code 0 = all checks pass, 1 = at least one failure.

Country count tolerances are calibrated to the Belgium test bbox
(2.5–6.4 E, 49.5–51.5 N) and cross-validated against modern Natural Earth.

Recalibrated for AD-013 (Sprint 3): hex size is 10 km flat-to-flat, so this
bbox now yields ~775 hexes (was 280). Absolute count bands scaled ~2.77x;
settlement/river checks are percentage-based so they survive future hex-size
or bbox tweaks. River % is INTENTIONALLY ~17% here (not the old 25%): river-
touching hexes scale ~linearly with hex size while total hexes scale
quadratically, so finer hexes give a lower river fraction for the identical
set of strategic rivers (MIN_WATERWAY_TOTAL_M unchanged at 110 km).
"""

import json
import math
import sys

OUTPUT = 'output/para_bellum_belgium_test_hex_terrain.json'

with open(OUTPUT, encoding='utf-8') as f:
    data = json.load(f)

hexes = data['hexes']
checks: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str):
    checks.append((name, ok, detail))


# --- Schema ---------------------------------------------------------------
check('schema_version is 1.0.1',
      data['schema_version'] == '1.0.1',
      f"got {data['schema_version']}")

legacy = any('country_1939' in h['political'] for h in hexes)
check('no legacy country_1939 keys', not legacy, 'legacy key present' if legacy else 'clean')

# --- Settlements ----------------------------------------------------------
cities = [h for h in hexes if h['settlement']['type'] in ('city', 'metropolis')]
city_names = {h['settlement']['name'] for h in cities}
tagged = [h for h in hexes if h['settlement']['type'] != 'none']
untagged_pct = 100.0 * (len(hexes) - len(tagged)) / len(hexes)

tagged_pct = 100.0 * len(tagged) / len(hexes)
check('>= 5 cities/metropolises', len(cities) >= 5, f'{len(cities)} cities')
check('Brussels present', any('Bruxelles' in n or 'Brussel' in n for n in city_names), str(sorted(city_names))[:80])
check('Antwerp present', any('Antwerpen' in n or 'Antwerp' in n for n in city_names), '')
check('settlement-tagged hexes < 22% of map', tagged_pct < 22.0,
      f'{len(tagged)} tagged ({tagged_pct:.1f}%)')
check('> 60% hexes untagged', untagged_pct > 60.0, f'{untagged_pct:.1f}% untagged')

# --- Rivers ---------------------------------------------------------------
river_hexes = [h for h in hexes if h['terrain']['river_edges']]
river_pct = 100.0 * len(river_hexes) / len(hexes)
check('river hexes 10-25% (strategic, AD-013 scale)', 10.0 <= river_pct <= 25.0,
      f'{len(river_hexes)} hexes ({river_pct:.1f}%)')
bad_edges = [e for h in hexes for e in h['terrain']['river_edges'] if not (0 <= e <= 5)]
check('river edge indices all 0-5', not bad_edges, f'bad: {bad_edges[:5]}')

# --- Boundaries -----------------------------------------------------------
counts: dict[str, int] = {}
for h in hexes:
    c = h['political']['country_at_start']
    counts[c] = counts.get(c, 0) + 1

expected = {'BEL': (300, 400), 'FRA': (195, 275), 'NLD': (45, 95),
            'DEU': (35, 75), 'LUX': (18, 42)}
for code, (lo, hi) in expected.items():
    n = counts.get(code, 0)
    check(f'{code} hex count in [{lo},{hi}]', lo <= n <= hi, f'{code}={n}')

land_empty = sum(1 for h in hexes
                 if not h['flags']['is_water'] and not h['political']['country_at_start'])
check('land hexes without country <= 2 (border precision)', land_empty <= 2,
      f'{land_empty} empty land hexes')


def closest_hex(lat, lon):
    return min(hexes, key=lambda h: math.hypot(
        h['geo']['center_lat'] - lat,
        (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat))))


check('Brussels hex is BEL',
      closest_hex(50.85, 4.35)['political']['country_at_start'] == 'BEL', '')
check('Maastricht hex is NLD',
      closest_hex(50.85, 5.69)['political']['country_at_start'] == 'NLD', '')

# --- Biome distribution ---------------------------------------------------
biomes: dict[str, int] = {}
for h in hexes:
    b = h['terrain']['biome']
    biomes[b] = biomes.get(b, 0) + 1

water_n = sum(1 for h in hexes if h['flags']['is_water'])
top_biome, top_n = max(biomes.items(), key=lambda kv: kv[1])
check('water hex count sane (22-55)', 22 <= water_n <= 55, f'{water_n} water hexes')
check('no biome > 70% of map', top_n / len(hexes) <= 0.70, f'{top_biome}={top_n}')
check('urban hexes present', biomes.get('urban', 0) > 0, f"urban={biomes.get('urban', 0)}")
check('forest hexes present (Ardennes)', biomes.get('forest', 0) > 0,
      f"forest={biomes.get('forest', 0)}")

# --- Summary ---------------------------------------------------------------
print(f'Sprint 2 validation — {OUTPUT}')
print(f'{len(hexes)} hexes, schema {data["schema_version"]}\n')
failed = 0
for name, ok, detail in checks:
    mark = 'PASS' if ok else 'FAIL'
    if not ok:
        failed += 1
    suffix = f'  [{detail}]' if detail else ''
    print(f'  {mark}  {name}{suffix}')

print(f'\n{"=" * 50}')
if failed:
    print(f'OVERALL: FAIL ({failed}/{len(checks)} checks failed)')
    sys.exit(1)
print(f'OVERALL: PASS ({len(checks)}/{len(checks)} checks)')
