import json

with open('output/para_bellum_belgium_test_hex_terrain.json') as f:
    data = json.load(f)

hexes = data['hexes']
print('Hex count:', data['map_metadata']['hex_count'])
print('Biome distribution:', data['map_metadata']['biome_distribution'])

print()
print('=== Hexes with settlements ===')
shown = 0
for h in hexes:
    if h['settlement']['type'] != 'none' and shown < 8:
        s = h['settlement']
        i = h['infrastructure']
        t = h['terrain']
        print(f"  {h['id']}  biome={t['biome']:<8}  {s['type']:<10}  '{s['name']}'  road={i['road']}  rail={i['rail']}")
        shown += 1

print()
print('=== Hexes with roads ===')
shown = 0
for h in hexes:
    if h['infrastructure']['road'] != 'none' and shown < 8:
        i = h['infrastructure']
        t = h['terrain']
        print(f"  {h['id']}  biome={t['biome']:<8}  road={i['road']:<8}  rail={i['rail']:<8}  river_edges={t['river_edges']}")
        shown += 1

print()
print('=== Hexes with rivers ===')
shown = 0
for h in hexes:
    if h['terrain']['river_edges'] and shown < 8:
        t = h['terrain']
        i = h['infrastructure']
        print(f"  {h['id']}  biome={t['biome']:<8}  edges={t['river_edges']}  bridge={i['bridge']}")
        shown += 1

print()
print('=== Hexes with rail ===')
shown = 0
for h in hexes:
    if h['infrastructure']['rail'] != 'none' and shown < 8:
        i = h['infrastructure']
        t = h['terrain']
        s = h['settlement']
        print(f"  {h['id']}  biome={t['biome']:<8}  rail={i['rail']:<8}  settlement={s['name']}")
        shown += 1

print()
print('=== Urban hexes ===')
for h in hexes:
    if h['terrain']['biome'] == 'urban':
        s = h['settlement']
        i = h['infrastructure']
        print(f"  {h['id']}  {s['type']:<10}  '{s['name']}'  road={i['road']}  rail={i['rail']}  port={i['port']}")