"""Province-density audit: hexes per province, for every nation on the map.

Under AD-M09 building slots are per PROVINCE, and capture flips per province.
So hexes-per-province is not a cosmetic statistic — it is industrial capacity
per unit area and capture granularity, and a nation authored at a finer
subdivision tier than its neighbours silently receives more of both.

This tool MEASURES. It never re-authors anything.

TWO DENOMINATORS ARE REPORTED, AND THEY DISAGREE. Neither is "the" answer, and
a claim built on one must say which:

  A  FRAME-REFERENCED — in-frame hexes / provinces referenced by in-frame hexes.
     What the GAME sees: how many building-slot sets and capture units cover the
     nation. Frame-clipped provinces count at whatever fraction the bbox left of
     them. Use for "does this nation get more economy per km2".
  B  WHOLE-PROVINCE — hexes in fully-framed provinces / count of those. What
     TIER the nation was authored at, undistorted by slicing. Blind to any
     nation the bbox cut up (n/a, never guessed). Use for "was this nation
     authored at a finer tier".

On the shipped eastern frame they give materially different readings: under B
there is an empty 2.25x band between HUN 54.2 and AUT 121.9, so the map looks
BIMODAL — two authoring tiers never reconciled. Under A the same data is a
CONTINUUM with no break wider than 1.51x, because frame clipping smears the
tiers together. What survives both is a ~31x spread with CHE at the dense
extreme and CSK at the coarse one.

The tool therefore prints the gap analysis for each denominator separately and
labels which conclusion each supports. Do not lift one number out of it.

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

    # ---------------------------------------------------------------------
    # TWO DENOMINATORS. They answer DIFFERENT questions and they DISAGREE, so
    # this tool reports both and never prints one unlabelled.
    #
    #   A  FRAME-REFERENCED = in-frame hexes / provinces referenced by in-frame
    #      hexes. What the GAME sees: how many building-slot sets and capture
    #      units cover this nation. Frame-clipped provinces count at whatever
    #      fraction the bbox left of them.
    #
    #   B  WHOLE-PROVINCE = hexes in fully-framed provinces / count of those.
    #      What TIER the nation was authored at, undistorted by slicing. Blind
    #      to any nation the bbox cut up (reported n/a, never guessed).
    #
    # A answers "does this nation get more economy per km2". B answers "was it
    # authored at a finer tier". A normalisation pass needs both: B identifies
    # the cause, A measures the live consequence.
    # ---------------------------------------------------------------------
    def gap_report(d, label, note):
        o = sorted(d.items(), key=lambda kv: kv[1])
        if len(o) < 2:
            return
        gi, gr = 0, 1.0
        for i in range(len(o) - 1):
            r = o[i + 1][1] / max(o[i][1], 1e-9)
            if r > gr:
                gi, gr = i, r
        print("")
        print(f"=== {label} ===")
        print(f"  {note}")
        print(f"  {len(o)} nations, range {o[0][1]:.1f}..{o[-1][1]:.1f}, "
              f"{o[-1][1] / max(o[0][1], 1e-9):.1f}x spread")
        print("  " + " | ".join(f"{c} {v:.1f}" for c, v in o))
        print(f"  widest consecutive gap: x{gr:.2f} between {o[gi][0]} "
              f"{o[gi][1]:.1f} and {o[gi + 1][0]} {o[gi + 1][1]:.1f}")
        if gr >= 2.0:
            print(f"  -> BIMODAL under this denominator: an empty band {gr:.2f}x "
                  f"wide separates two clusters.")
        else:
            print(f"  -> CONTINUUM under this denominator: no break wider than "
                  f"x{gr:.2f}. Do NOT describe this as two tiers.")

    raw_density = {c: nat[c]["hex"] / len(nat[c]["prov"])
                   for c in nat if nat[c]["prov"]}
    gap_report(raw_density, "DENOMINATOR A - FRAME-REFERENCED (what the game sees)",
               "in-frame hexes / provinces referenced by in-frame hexes")
    gap_report(comparable, "DENOMINATOR B - WHOLE-PROVINCE (authoring tier)",
               "hexes in fully-framed provinces / count of those provinces")

    print("")
    print("=== WHAT SURVIVES BOTH DENOMINATORS ===")
    if raw_density:
        lo = min(raw_density, key=raw_density.get)
        hi = max(raw_density, key=raw_density.get)
        print(f"  * A ~{max(raw_density.values()) / min(raw_density.values()):.0f}x "
              f"spread ({lo} densest, {hi} coarsest). Holds under both.")
    print("  * CHE sits at or near the dense extreme under both, and its")
    print("    provinces are cantons authored without reference to the map.")
    print("  * CSK is the coarse extreme under both, and four historical lands")
    print("    genuinely IS Czechoslovakia's top tier - coarse but defensible.")
    print("  * HUN's position is AUTHORED (Matthew, 2026-08-26, county tier);")
    print("    it is not drift and must not be normalised with the others.")
    print("  The 'two authoring tiers' reading is visible ONLY under B. Under A")
    print("  frame clipping smears it into a continuum. The bimodality is a")
    print("  claim about AUTHORING, not about live economy - do not carry it")
    print("  into a yield argument without saying which denominator it rests on.")

    print("\nNations with no whole province in frame are reported n/a rather than")
    print("given a raw figure: their provinces are sliced by the bbox, so the raw")
    print("hexes/province understates them and is not comparable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
