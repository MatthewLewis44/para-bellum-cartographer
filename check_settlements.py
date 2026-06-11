import json

with open('output/para_bellum_belgium_test_hex_terrain.json') as f:
    data = json.load(f)

hexes = data['hexes']

print('=== All settlements by type ===')
cities = []
towns = []
villages = []

for h in hexes:
    s = h['settlement']
    if s['type'] == 'none':
        continue
    entry = (h['id'], s['type'], s['name'], h['terrain']['biome'],
             h['infrastructure']['road'], h['infrastructure']['rail'])
    if s['type'] in ('city', 'metropolis'):
        cities.append(entry)
    elif s['type'] == 'town':
        towns.append(entry)
    else:
        villages.append(entry)

print(f'\nCities/Metropolises ({len(cities)}):')
for hid, stype, name, biome, road, rail in sorted(cities, key=lambda x: x[2]):
    print(f'  {hid}  {stype:<12}  {name}')

print(f'\nTowns ({len(towns)}):')
for hid, stype, name, biome, road, rail in sorted(towns, key=lambda x: x[2]):
    print(f'  {hid}  {name}')

print(f'\nVillages ({len(villages)}):')
for hid, stype, name, biome, road, rail in sorted(villages, key=lambda x: x[2]):
    print(f'  {hid}  {name}')

print(f'\nTotal settlements: {len(cities)+len(towns)+len(villages)}')
print(f'Total hexes: {len(hexes)}')
print(f'Hexes with no settlement: {sum(1 for h in hexes if h["settlement"]["type"] == "none")}')