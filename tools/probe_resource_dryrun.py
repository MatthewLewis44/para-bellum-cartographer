"""Pre-Sprint 9.0 dry run: predict the new resource tagging without re-tiling.

The grid is unchanged (same bbox, same hex_size_km), so the shipped artifact's
hex centres ARE the centres the regenerated artifact will have. Re-running the
sampler's assignment rule against those centres predicts the new tagging
exactly, for the cost of a few seconds instead of a 205-tile re-tile.

Usage: uv run python tools/probe_resource_dryrun.py
Read-only.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402
from shapely.prepared import prep  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "output" / "para_bellum_east_expansion_hex_terrain.json"
RES = REPO / "data" / "resources" / "resources_1930.geojson"
TYPES = ("coal", "steel", "iron", "oil")
NATIONS = ("DEU", "POL", "CSK", "AUT")


def main() -> int:
    with open(ARTIFACT, encoding="utf-8") as f:
        data = json.load(f)
    hexes = data["hexes"]
    centres = [(h["geo"]["center_lon"], h["geo"]["center_lat"]) for h in hexes]

    gdf = gpd.read_file(RES)
    print(f"{RES.name}: {len(gdf)} features "
          f"({sum(gdf.geometry.geom_type == 'Polygon')} polygons, "
          f"{sum(gdf.geometry.geom_type == 'Point')} points)\n")

    tagged: list[set] = [set() for _ in hexes]
    per_feature: list[tuple[str, str, str, int]] = []

    for _, row in gdf.iterrows():
        geom, rtype = row.geometry, str(row["resource_type"]).lower()
        name, ctry = row["name"], row.get("country", "")
        n = 0
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            pg = prep(geom)
            for i, (lon, lat) in enumerate(centres):
                if pg.covers(Point(lon, lat)):
                    tagged[i].add(rtype)
                    n += 1
        else:  # Point -> nearest centre (the sampler's containing-hex lookup)
            lon0, lat0 = geom.x, geom.y
            best, bi = 1e9, None
            for i, (lon, lat) in enumerate(centres):
                d = (lat - lat0) ** 2 + ((lon - lon0) * 0.64) ** 2
                if d < best:
                    best, bi = d, i
            if bi is not None:
                tagged[bi].add(rtype)
                n = 1
        per_feature.append((rtype, name, ctry, n))

    print(f"{'type':<6} {'hexes':>5}  feature")
    print("-" * 78)
    for rtype, name, ctry, n in per_feature:
        flag = "   <-- TAGS NOTHING" if n == 0 else ""
        print(f"{rtype:<6} {n:>5}  {name} [{ctry}]{flag}")

    totals = Counter()
    by_country: dict[str, Counter] = defaultdict(Counter)
    by_prov: dict[str, Counter] = defaultdict(Counter)
    for h, ts in zip(hexes, tagged):
        c = h["political"]["country_at_start"] or "(none)"
        p = h["political"]["province_at_start"] or "(none)"
        for t in ts:
            totals[t] += 1
            by_country[c][t] += 1
            by_prov[p][t] += 1

    print("\nPREDICTED TOTALS: " + "  ".join(f"{t}={totals[t]}" for t in TYPES))

    print("\nby country:")
    print(f"  {'ctry':<6} " + " ".join(f"{t:>6}" for t in TYPES) + "   types")
    for c in sorted(by_country, key=lambda k: -sum(by_country[k].values())):
        ntypes = sum(1 for t in TYPES if by_country[c][t])
        print(f"  {c:<6} " + " ".join(f"{by_country[c][t]:>6}" for t in TYPES)
              + f"   {ntypes}")

    print("\nby province (non-empty):")
    for p in sorted(by_prov, key=lambda k: -sum(by_prov[k].values())):
        row = "  ".join(f"{t}={by_prov[p][t]}" for t in TYPES if by_prov[p][t])
        print(f"  {p:<28} {row}")

    print("\ngate preview:")
    ok = True
    for t in TYPES:
        good = totals[t] > 0
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {t} present somewhere ({totals[t]})")
    for n in NATIONS:
        ntypes = sum(1 for t in TYPES if by_country[n][t])
        good = ntypes >= 3
        ok &= good
        print(f"  [{'PASS' if good else 'FAIL'}] {n} has >= 3 distinct types ({ntypes})")
    print("\n" + ("DRY RUN OK" if ok else "DRY RUN WOULD FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
