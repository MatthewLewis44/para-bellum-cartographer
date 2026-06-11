import json

with open('output/para_bellum_belgium_test_hex_terrain.json') as f:
    data = json.load(f)

hexes = data['hexes']
river_hexes = [h for h in hexes if h['terrain']['river_edges']]
bridge_hexes = [h for h in hexes if h['infrastructure']['bridge']]

print(f'Total hexes: {len(hexes)}')
print(f'Hexes with river edges: {len(river_hexes)} ({len(river_hexes)/len(hexes)*100:.1f}%)')
print(f'Hexes with bridges: {len(bridge_hexes)}')
print()
print('River hexes:')
for h in river_hexes:
    name = h['settlement']['name'] or ''
    print(f"  {h['id']}  edges={h['terrain']['river_edges']}  bridge={h['infrastructure']['bridge']}  {name}")