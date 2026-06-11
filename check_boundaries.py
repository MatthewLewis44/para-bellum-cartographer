"""Validate country_at_start assignment in the hex JSON output.

Usage: uv run python check_boundaries.py
"""

import json
import math

with open('output/para_bellum_belgium_test_hex_terrain.json', encoding='utf-8') as f:
    data = json.load(f)

hexes = data['hexes']

print(f'schema_version: {data["schema_version"]}')
print(f'Total hexes: {len(hexes)}')

counts: dict[str, int] = {}
for h in hexes:
    c = h['political']['country_at_start']
    counts[c] = counts.get(c, 0) + 1

print('\n=== Hexes per country_at_start ===')
for c, n in sorted(counts.items(), key=lambda kv: -kv[1]):
    label = c if c else '(empty)'
    print(f'  {label:<8} {n}')

empty = counts.get('', 0)
water = sum(1 for h in hexes if h['flags']['is_water'])
print(f'\nEmpty country_at_start: {empty} (water hexes in output: {water})')

land_empty = sum(
    1 for h in hexes
    if not h['flags']['is_water'] and h['political']['country_at_start'] == ''
)
print(f'LAND hexes with empty country: {land_empty}')


def closest_hex(lat: float, lon: float) -> dict:
    return min(
        hexes,
        key=lambda h: math.hypot(
            h['geo']['center_lat'] - lat,
            (h['geo']['center_lon'] - lon) * math.cos(math.radians(lat)),
        ),
    )


print('\n=== Spot checks ===')
for label, lat, lon in [
    ('Brussels   (50.85, 4.35)', 50.85, 4.35),
    ('Maastricht (50.85, 5.69)', 50.85, 5.69),  # NLD check inside bbox
    ('Aachen     (50.78, 6.08)', 50.78, 6.08),  # DEU check inside bbox
]:
    h = closest_hex(lat, lon)
    print(
        f'  {label} -> hex {h["id"]} '
        f'country={h["political"]["country_at_start"] or "(empty)"} '
        f'settlement={h["settlement"]["name"] or "-"}'
    )
