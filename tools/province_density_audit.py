"""Province-density audit: hexes per province, for every nation on the map.

Under AD-M09 building slots are per PROVINCE, and capture flips per province.
So hexes-per-province is not a cosmetic statistic — it is industrial capacity
per unit area and capture granularity, and a nation authored at a finer
subdivision tier than its neighbours silently receives more of both.

This tool MEASURES. It never re-authors anything.

Two figures are reported per nation, and the second is the one to read:

  * hexes / province over ALL framed hexes — the raw figure, which a nation the
    bbox cut in half will understate badly. Only a sliver of France and Italy is
    framed, and their provinces are sliced with it.
  * hexes / province over WHOLE provinces only — provinces whose authored
    polygon lies (almost) entirely inside the frame. This is the comparable
    number. A nation with no whole province in frame is reported as such rather
    than given a misleading figure.

Usage: uv run python tools/province_density_audit.py [artifact.json]
"""

from __future__ import annotations

import collections
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROV_GEO = ROOT / "data" / "boundaries" / "provinces_1930.geojson"
DEFAULT_ARTIFACT = ROOT / "output" / "para_bellum_east_expansion_hex_terrain.json"

# A province counts as "whole" when this much of its authored polygon area sits
# inside the map bbox. Not 100%: a coastal province's polygon runs to a
# generalised coastline the bbox clips marginally.
WHOLE_FRACTION = 0.97


def main() -> int:
    art_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ARTIFACT
    art = json.loads(art_path.read_text(encoding="utf-8"))
    b = art["map_metadata"]["bounds"]

    from shapely.geometry import box, shape
    from pyproj import Geod
    geod = Geod(ellps="WGS84")

    def area_km2(g):
        if g.is_empty:
            return 0.0
        parts = list(g.geoms) if g.geom_type == "MultiPolygon" else [g]
        return sum(abs(geod.geometry_area_perimeter(p)[0]) for p in parts) / 1e6

    frame = box(b["min_lon"], b["min_lat"], b["max_lon"], b["max_lat"])
    whole = set()
    prov_country = {}
    for f in json.loads(PROV_GEO.read_text(encoding="utf-8"))["features"]:
        pid = f["properties"]["province_id"]
        prov_country[pid] = f["properties"].get("country", pid.split("_")[0])
        g = shape(f["geometry"])
        if not g.is_valid:
            g = g.buffer(0)
        total = area_km2(g)
        if total > 0 and area_km2(g.intersection(frame)) / total >= WHOLE_FRACTION:
            whole.add(pid)

    nat = collections.defaultdict(
        lambda: {"hex": 0, "land": 0, "prov": set(), "cap": 0, "sub": 0,
                 "res": 0, "whole_hex": 0, "whole_prov": set()})
    for h in art["hexes"]:
        c = h["political"]["country_at_start"]
        if not c:
            continue
        pid = h["political"]["province_at_start"]
        n = nat[c]
        n["hex"] += 1
        if not h["flags"]["is_water"]:
            n["land"] += 1
        if pid:
            n["prov"].add(pid)
            if pid in whole:
                n["whole_prov"].add(pid)
                n["whole_hex"] += 1
        t = h["settlement"]["admin_tier"]
        if t == "capital":
            n["cap"] += 1
        elif t == "sub_capital":
            n["sub"] += 1
        r = h["resources"]
        if any(r[k] for k in ("oil", "coal", "steel", "iron", "agriculture")):
            n["res"] += 1

    print(f"artifact: {art_path.name}   schema {art['schema_version']}   "
          f"{len(art['hexes']):,} hexes")
    print(f"frame: {b['min_lon']}..{b['max_lon']}E  {b['min_lat']}..{b['max_lat']}N")
    print(f"provinces authored: {len(prov_country)}   "
          f"lying (>={WHOLE_FRACTION:.0%}) inside the frame: {len(whole)}\n")

    hdr = (f"{'NAT':<5}{'hexes':>7}{'land':>7}{'prov':>6}{'hex/prov':>10}"
           f"{'whole':>7}{'hex/whole':>11}{'caps':>6}{'subs':>6}{'res':>6}")
    print(hdr)
    print("-" * len(hdr))
    comparable = {}
    for c in sorted(nat, key=lambda k: -nat[k]["hex"]):
        n = nat[c]
        np_ = len(n["prov"])
        nw = len(n["whole_prov"])
        raw = f"{n['hex'] / np_:.1f}" if np_ else "-"
        if nw:
            w = n["whole_hex"] / nw
            comparable[c] = w
            wstr = f"{w:.1f}"
        else:
            wstr = "n/a"
        print(f"{c:<5}{n['hex']:>7}{n['land']:>7}{np_:>6}{raw:>10}"
              f"{nw:>7}{wstr:>11}{n['cap']:>6}{n['sub']:>6}{n['res']:>6}")

    if comparable:
        # The baseline excludes territories with fewer than three whole
        # provinces in frame. Danzig and the Saar are single-province League
        # territories — their "hexes per province" is just their size, and
        # letting them into the median drags the map's characteristic density
        # down by a factor of three and makes half the map look like an outlier.
        MIN_WHOLE = 3
        basis = {c: v for c, v in comparable.items()
                 if len(nat[c]["whole_prov"]) >= MIN_WHOLE}
        excluded = {c: v for c, v in comparable.items() if c not in basis}
        vals = sorted(basis.values())
        mid = vals[len(vals) // 2] if len(vals) % 2 else \
            (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2

        print(f"\ncomparable (whole-province) density across {len(comparable)} "
              f"nations, range {min(comparable.values()):.1f}.."
              f"{max(comparable.values()):.1f} "
              f"({max(comparable.values()) / max(min(comparable.values()), 1e-9):.1f}x spread)")
        print(f"baseline median {mid:.1f} hexes/province, over the "
              f"{len(basis)} nations with >= {MIN_WHOLE} whole provinces in "
              f"frame ({', '.join(sorted(basis))})")
        if excluded:
            print(f"excluded from the baseline (too few whole provinces to be a "
                  f"density at all): "
                  f"{', '.join(f'{c} {v:.1f}' for c, v in sorted(excluded.items()))}")

        # The distribution is BIMODAL, and a median is the wrong summary for it:
        # the map was authored at two different subdivision tiers, and the median
        # just reports which cluster happens to hold more nations right now
        # (adding the eight frame nations moved it from 154 to 54 without any
        # existing nation changing). Find the widest multiplicative gap in the
        # sorted sequence instead — that is the boundary between the tiers, and
        # it is what a normalisation pass would have to close.
        ordered = sorted(comparable.items(), key=lambda kv: kv[1])
        gap_i, gap_ratio = None, 1.0
        for i in range(len(ordered) - 1):
            r = ordered[i + 1][1] / max(ordered[i][1], 1e-9)
            if r > gap_ratio:
                gap_i, gap_ratio = i, r
        print("\nranked, densest first — a LOW number means many small provinces,")
        print("i.e. more building slots and finer capture per unit area:")
        for i, (c, v) in enumerate(ordered):
            tag = "" if c in basis else " (not in baseline)"
            print(f"  {c:<5}{v:>8.1f}   {v / mid:>5.2f}x median{tag}")
            if i == gap_i:
                print(f"  {'':<5}{'':>8}   ---- widest gap: x{gap_ratio:.2f}, "
                      f"nothing authored between {v:.1f} and "
                      f"{ordered[i + 1][1]:.1f} ----")
        if gap_i is not None:
            fine = [c for c, _ in ordered[:gap_i + 1]]
            coarse = [c for c, _ in ordered[gap_i + 1:]]
            print(f"\nTWO AUTHORING TIERS, not one tier with outliers:")
            print(f"  FINE   ({len(fine)}): {', '.join(fine)}")
            print(f"         cantons, departements, counties, provincie, "
                  f"landsdele, ethnographic regions")
            print(f"  COARSE ({len(coarse)}): {', '.join(coarse)}")
            print(f"         Prussian provinces, voivodeships, CSK lands, "
                  f"Bundeslaender, historical provinces")
            print(f"  The gap is x{gap_ratio:.2f} wide and EMPTY. Any per-province")
            print(f"  economic effect inherits this split; a nation in the fine")
            print(f"  tier gets roughly {gap_ratio:.0f}x the building slots and")
            print(f"  capture granularity per unit area of one in the coarse tier.")
    print("\nNations with no whole province in frame are reported n/a rather than")
    print("given a raw figure: their provinces are sliced by the bbox, so the raw")
    print("hexes/province understates them and is not comparable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
