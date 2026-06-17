"""Validate country_at_start assignment in the hex JSON output.

Usage: uv run python check_boundaries.py [output/<name>_hex_terrain.json]

Spot-checks adapt to the output's coverage: the CHE/AUT/ITA checks (P0-C, AD-028)
only assert when the run's bbox actually reaches those cities (e.g. the W+C
Europe run), and are skipped on a Benelux/Belgium output.
"""

import json
import math
import sys

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    'output/para_bellum_belgium_test_hex_terrain.json'

with open(PATH, encoding='utf-8') as f:
    data = json.load(f)

hexes = data['hexes']
print(f'schema_version: {data["schema_version"]}   file: {PATH}')
print(f'Total hexes: {len(hexes)}')

counts: dict[str, int] = {}
for h in hexes:
    c = h['political']['country_at_start']
    counts[c] = counts.get(c, 0) + 1

print('\n=== Hexes per country_at_start ===')
for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    print(f'  {c or "(empty)":<8} {n}')

water = sum(1 for h in hexes if h['flags']['is_water'])
land_empty = sum(1 for h in hexes
                 if not h['flags']['is_water'] and h['political']['country_at_start'] == '')
print(f'\nwater hexes: {water}   LAND hexes with empty country: {land_empty}')


def closest(lat, lon):
    return min(hexes, key=lambda h: math.hypot(
        h['geo']['center_lat'] - lat,
        (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat))))


def covered(lat, lon, tol=0.4):
    h = closest(lat, lon)
    return math.hypot(h['geo']['center_lat'] - lat,
                      (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat))) <= tol


fails = []
print('\n=== Spot checks ===')
# always-on (western front)
WEST = [
    ('Brussels  (50.85, 4.35)', 50.85, 4.35, 'BEL'),
    ('Maastricht(50.85, 5.69)', 50.85, 5.69, 'NLD'),
    ('Aachen    (50.78, 6.08)', 50.78, 6.08, 'DEU'),
]
# P0-C (only assert if the run reaches them — Salzburg, not Vienna which is east of 15E)
EAST = [
    ('Zürich    (47.37, 8.54)', 47.37, 8.54, 'CHE'),
    ('Salzburg  (47.80, 13.04)', 47.80, 13.04, 'AUT'),
    ('Milan     (45.46, 9.19)', 45.46, 9.19, 'ITA'),
]
for label, lat, lon, want in WEST:
    h = closest(lat, lon)
    got = h['political']['country_at_start'] or '(empty)'
    ok = got == want
    if not ok:
        fails.append(label)
    print(f'  [{"PASS" if ok else "FAIL"}] {label} -> {got} (want {want})')

for label, lat, lon, want in EAST:
    if not covered(lat, lon):
        print(f'  [skip] {label} — outside this run\'s bbox')
        continue
    h = closest(lat, lon)
    got = h['political']['country_at_start'] or '(empty)'
    ok = got == want
    if not ok:
        fails.append(label)
    print(f'  [{"PASS" if ok else "FAIL"}] {label} -> {got} (want {want})')

# If the run reaches the Alpine region, those countries must now be populated.
if covered(47.37, 8.54):
    for code in ('CHE', 'AUT', 'ITA'):
        n = counts.get(code, 0)
        ok = n > 0
        if not ok:
            fails.append(f'{code} hexes present')
        print(f'  [{"PASS" if ok else "FAIL"}] {code} land hexes present ({n})')

print('\nRESULT:', 'FAIL' if fails else 'PASS')
sys.exit(1 if fails else 0)
