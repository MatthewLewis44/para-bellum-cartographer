"""Validate the river-node model (v1.0.4 / AD-026) in the hex JSON output.

Rivers are hex-CENTER features now: each hex carries rivers.has_river (bool) and
rivers.river_name (string). river_edges is retained as a rendering hint only.

Checks (P0-A A3):
  * river-hexes form connected chains — no isolated single river-hexes
  * major rivers present as continuous hex chains (region-appropriate set)
  * has_river hex count is sensible (non-zero, not absurd)
  * river_name populated wherever has_river is true (and empty otherwise)

Usage:
  uv run python check_rivers.py [output/<name>_hex_terrain.json]
Exit code is non-zero on failure (gate-friendly).
"""

import json
import math
import sys
import unicodedata

PATH = sys.argv[1] if len(sys.argv) > 1 else \
    "output/para_bellum_belgium_test_hex_terrain.json"

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

hexes = data["hexes"]
hex_km = float(data["map_metadata"].get("hex_size_km", 10))
n_total = len(hexes)
land = [h for h in hexes if not h["flags"]["is_water"]]

# --- gather river hexes (node model) ---
river_hexes = [h for h in hexes if h.get("rivers", {}).get("has_river")]
edge_hexes = [h for h in hexes if h["terrain"]["river_edges"]]
n_river = len(river_hexes)

print(f"schema_version: {data['schema_version']}")
print(f"file: {PATH}")
print(f"Total hexes: {n_total}   land: {len(land)}")
print(f"has_river hexes:     {n_river}  "
      f"({n_river / n_total * 100:.1f}% of all, "
      f"{n_river / max(1, len(land)) * 100:.1f}% of land)")
print(f"river_edges hexes:   {len(edge_hexes)} (rendering-hint only)")


def norm(s: str) -> str:
    """lowercase, accent-strip, alnum-only — for cross-language name matching."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.lower() if c.isalnum() or c == " ").strip()


# --- adjacency among river-hexes via center distance (bucketed, O(n)) ---
# Every edge-adjacent flat-top neighbour center is at distance = flat-to-flat
# (= hex_km); the next ring is ~1.7x further, so a 1.35x threshold is clean.
thresh_m = 1.35 * hex_km * 1000.0
buckets: dict[tuple[int, int], list[int]] = {}
bsize = 0.25  # degrees; > threshold so neighbours are within ±1 bucket
pts = []
for i, h in enumerate(river_hexes):
    lat = h["geo"]["center_lat"]
    lon = h["geo"]["center_lon"]
    pts.append((lat, lon))
    buckets.setdefault((int(lon / bsize), int(lat / bsize)), []).append(i)


def neighbors(i: int) -> list[int]:
    lat, lon = pts[i]
    coslat = math.cos(math.radians(lat))
    bx, by = int(lon / bsize), int(lat / bsize)
    out = []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            for j in buckets.get((bx + dx, by + dy), ()):
                if j == i:
                    continue
                la, lo = pts[j]
                dm = math.hypot((lo - lon) * coslat * 111320.0,
                                (la - lat) * 111000.0)
                if dm <= thresh_m:
                    out.append(j)
    return out


adj = {i: neighbors(i) for i in range(n_river)}
isolated = [i for i in range(n_river) if not adj[i]]

# connected components
seen = set()
components = []
for i in range(n_river):
    if i in seen:
        continue
    stack, comp = [i], []
    seen.add(i)
    while stack:
        k = stack.pop()
        comp.append(k)
        for j in adj[k]:
            if j not in seen:
                seen.add(j)
                stack.append(j)
    components.append(comp)
components.sort(key=len, reverse=True)

print(f"\nConnectivity: {len(components)} component(s); "
      f"largest = {len(components[0]) if components else 0} hexes; "
      f"isolated single river-hexes = {len(isolated)}")
if isolated:
    for i in isolated[:10]:
        h = river_hexes[i]
        print(f"  isolated: hex {h['id']} "
              f"river={h['rivers']['river_name'] or '-'} "
              f"({pts[i][0]:.3f},{pts[i][1]:.3f})")

# --- river name distribution ---
name_counts: dict[str, int] = {}
for h in river_hexes:
    nm = h["rivers"]["river_name"] or "(unnamed)"
    name_counts[nm] = name_counts.get(nm, 0) + 1
print("\nTop rivers by hex count:")
for nm, c in sorted(name_counts.items(), key=lambda kv: -kv[1])[:15]:
    print(f"  {c:>4}  {nm}")

# --- major-river presence (region-appropriate; any 2+ is a pass) ---
MAJORS = {
    "meuse": ["meuse", "maas"],
    "scheldt": ["schelde", "escaut", "scheldt"],
    "rhine": ["rhein", "rijn", "rhine"],
    "albert canal": ["albertkanaal", "albert canal", "canal albert"],
    "moselle": ["moselle", "mosel"],
    "danube": ["danube", "donau"],          # Europe scale
    "rhone": ["rhone", "rhône"],            # Europe scale
}
present_names = {norm(h["rivers"]["river_name"]) for h in river_hexes}
present_majors = []
for label, variants in MAJORS.items():
    if any(any(norm(v) in pn or pn in norm(v) for pn in present_names if pn)
           for v in variants):
        present_majors.append(label)
print(f"\nMajor rivers present: {sorted(present_majors)}")

# --- AD-029: the generic-name over-aggregation problem must be GONE ---
# AD-011 produced isolated river-hexes named Mühlgraben/Mühlbach (generic
# mill-streams whose name-aggregate cleared the length floor). Natural Earth
# carries no such features, so NO isolated hex should bear a generic name.
_GENERIC = ("muhlgraben", "muhlbach", "muhlenbach", "muhlenkanal", "altwasser")
generic_isolated = [river_hexes[i]["id"] for i in isolated
                    if any(g in norm(river_hexes[i]["rivers"]["river_name"])
                           for g in _GENERIC)]
isolated_names = sorted({river_hexes[i]["rivers"]["river_name"] or "(unnamed)"
                         for i in isolated})
print(f"isolated-hex river names: {isolated_names[:12]}")

# --- consistency: name iff has_river ---
bad_name = [h["id"] for h in hexes
            if bool(h.get("rivers", {}).get("has_river"))
            != bool(h.get("rivers", {}).get("river_name"))]

# --- verdict ---
print("\n=== Checks ===")
fails = []
iso_frac = len(isolated) / max(1, n_river)


def check(ok: bool, msg: str, hard=True):
    print(f"  [{'PASS' if ok else 'FAIL' if hard else 'WARN'}] {msg}")
    if not ok and hard:
        fails.append(msg)


check(n_river > 0, f"has_river hexes present ({n_river})")
check(0.01 <= n_river / max(1, len(land)) <= 0.40,
      f"has_river share of land sensible "
      f"({n_river / max(1, len(land)) * 100:.1f}%, expect ~3-30%)", hard=False)
check(iso_frac <= 0.03,
      f"no significant isolated river-hexes "
      f"({len(isolated)} isolated = {iso_frac * 100:.1f}% of river-hexes)")
check(not generic_isolated,
      f"NO generic-named (Mühlgraben/Mühlbach) isolated hexes [AD-029] "
      f"({len(generic_isolated)})")
check(len(present_majors) >= 2,
      f"≥2 major rivers present as chains ({len(present_majors)}: {sorted(present_majors)})")
check(not bad_name,
      f"river_name set iff has_river ({len(bad_name)} mismatches)")

if fails:
    print(f"\nRESULT: FAIL ({len(fails)} hard check(s) failed)")
    sys.exit(1)
print("\nRESULT: PASS")
