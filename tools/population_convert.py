"""Generate Content/pb/population.json from the sourced census table and the shipped map (Sprint 14).

    uv run python tools/population_convert.py --artifact <hex_terrain.json> --people-per-agriculture N --start-ic-per-nation N --out population.json

Reads data/population/census_1930.tsv (one row per province: the WHOLE unit's figure, with its year, source,
note and basis) and growth_1930.tsv (natural increase by nation, with its source and basis), and the map
artifact. A figure's basis is required, so a figure can't pass for what it isn't: census or derived for a
province, measured or declared_zero for a growth rate. The basis stays in these tables, and a successful run
prints every derived figure and declared zero; population.json itself carries numbers only.

Two rules are judgements listed for review rather than facts:

  start_population = census x the share of the province's polygon inside the map frame. A province the
      map crops carries only its on-map share, which assumes its people are spread evenly across it.
  start_industry   = THE V0 DISTRIBUTION: every nation gets the same --start-ic-per-nation points, shared
      across its provinces in proportion to start_population, not a historical figure. A historical table
      replaces this rule later. Rounded by largest remainder so each nation's total is exact: each province
      takes the floor of its quota, then the largest remainders take one more each, ties to the lower id.

Refuses rather than guesses, with a code and the fix (content/population/convert_codes.json), and reports
every problem at once. Writes nothing on a refusal.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from pyproj import Geod
from shapely.geometry import box, shape

ROOT = Path(__file__).resolve().parents[1]
CENSUS = ROOT / "data" / "population" / "census_1930.tsv"
GROWTH = ROOT / "data" / "population" / "growth_1930.tsv"
POLYGONS = ROOT / "data" / "boundaries" / "provinces_1930.geojson"
SHARE_SCALE = 100_000   # the on-map share is stored whole, so the arithmetic below stays integer
GEOD = Geod(ellps="WGS84")


class Refusals:
    def __init__(self):
        self.problems: list[tuple[str, str, str]] = []

    def add(self, code: str, where: str, detail: str) -> None:
        self.problems.append((code, where, detail))


def read_tsv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def read_map(artifact: dict) -> dict[str, dict]:
    """Province id -> its owner. A hex with no province (sea, unowned) is skipped."""
    out: dict[str, dict] = {}
    for h in artifact["hexes"]:
        pr = h["political"].get("province_at_start")
        if not pr:
            continue
        out.setdefault(pr, {"nation": h["political"].get("country_at_start")})
    return out


def area_km2(geom) -> float:
    return abs(GEOD.geometry_area_perimeter(geom)[0]) / 1e6


def frame_share(geom, bounds: dict) -> int:
    """The share of a polygon inside the map frame, in SHARE_SCALE parts."""
    frame = box(bounds["min_lon"], bounds["min_lat"], bounds["max_lon"], bounds["max_lat"])
    return min(SHARE_SCALE, round(area_km2(geom.intersection(frame)) / area_km2(geom) * SHARE_SCALE))


def share_points(people: dict[str, int], total: int) -> dict[str, int]:
    """total points across provinces by population, largest remainder, in exact integers. Ties go to the lower id."""
    whole = sum(people.values())
    points = {p: total * n // whole for p, n in people.items()}
    spare = total - sum(points.values())
    by_remainder = sorted(people, key=lambda p: (-(total * people[p] % whole), p))
    for p in by_remainder[:spare]:
        points[p] += 1
    return points


def census_gap(r: dict) -> str:
    """Why a province figure's provenance falls short, or empty if it doesn't."""
    basis = r["basis"].strip()
    if basis not in ("census", "derived"):
        return f'has basis "{basis}": write census, or derived with the method in the note'
    if not (r["source_url"].strip() and r["census_year"].strip()):
        return "has a figure with no source or year: add both"
    if basis == "derived" and not r["note"].strip():
        return "is derived and its note is empty: state the method and its weakness"
    return ""


def growth_gap(r: dict, rate: int) -> str:
    """Why a growth rate's provenance falls short, or empty if it doesn't. A declared zero says no growth is
    modelled; it is not a measurement, so it needs its reason rather than a source."""
    basis = r["basis"].strip()
    if basis == "measured":
        if not (r["source_url"].strip() and r["year"].strip()):
            return "is measured with no source or year: add both, or declare a zero with its reason"
    elif basis == "declared_zero":
        if rate != 0:
            return f"declares a zero and its rate is {rate}: write 0, or mark it measured with a source"
        if not r["note"].strip():
            return "declares a zero and its note is empty: say why no figure is modelled"
    else:
        return f'has basis "{basis}": write measured or declared_zero'
    return ""


def convert(artifact: dict, census: list[dict], growth: list[dict], polygons: dict,
            people_per_agriculture: int, start_ic_per_nation: int) -> tuple[dict | None, list[tuple[str, str, str]]]:
    bad = Refusals()
    provinces = read_map(artifact)
    geoms = {f["properties"]["province_id"]: f["geometry"] for f in polygons["features"]}

    figures: dict[str, int] = {}
    seen: set[str] = set()
    for r in census:
        pid = r["province"]
        if pid in seen:
            bad.add("X203", pid, "has two census rows: keep one")
        seen.add(pid)
        if pid not in provinces:
            bad.add("X202", pid, "is in the census table but not on this map: remove the row, or correct the id")
            continue
        fig = r["population"].strip()
        if not fig:
            bad.add("X204", pid, f'has no census figure: source one, or rule a fallback. The table says: {r["note"]}')
        elif not fig.isdigit() or int(fig) < 1:
            bad.add("X205", pid, f'"{fig}" is not a whole number of people: write digits only')
        else:
            figures[pid] = int(fig)
            gap = census_gap(r)
            if gap:
                bad.add("X209", pid, gap)
    for pid in sorted(set(provinces) - seen):
        bad.add("X201", pid, "is on the map and has no census row: add one, empty if no figure is known, with why")

    rates: dict[str, int] = {}
    rated: set[str] = set()
    for r in growth:
        n, v = r["nation"], r["growth_per_10k_per_year"].strip()
        if n in rated:
            bad.add("X203", n, "has two growth rows: keep one")
        elif not v.isdigit():
            bad.add("X205", n, f'"{v}" is not a whole, non-negative rate per 10,000: write digits only')
        else:
            rates[n] = int(v)
            gap = growth_gap(r, rates[n])
            if gap:
                bad.add("X210", n, gap)
        rated.add(n)
    for n in sorted({p["nation"] for p in provinces.values()} - rated):
        bad.add("X206", n, "owns a province on the map and has no growth row: add its natural increase, with a source")

    rows = []
    bounds = artifact["map_metadata"]["bounds"]
    for pid in sorted(provinces):
        if pid not in geoms:
            bad.add("X207", pid, f"has no polygon in {POLYGONS.name}, so its on-map share cannot be measured: add one")
            continue
        if pid not in figures:
            continue
        share = frame_share(shape(geoms[pid]), bounds)
        people = figures[pid] * share // SHARE_SCALE
        if people < 1:
            bad.add("X208", pid, f"has {share} of {SHARE_SCALE} parts on the map, which leaves nobody: "
                                 f"correct the polygon, or take the province off the map")
            continue
        rows.append({"province": pid, "nation": provinces[pid]["nation"], "start_population": people,
                     "start_industry": 0})
    if bad.problems:
        return None, sorted(bad.problems)
    for n in {r["nation"] for r in rows}:
        mine = {r["province"]: r["start_population"] for r in rows if r["nation"] == n}
        points = share_points(mine, start_ic_per_nation)
        for r in rows:
            if r["nation"] == n:
                r["start_industry"] = points[r["province"]]
    return {"format": "pb-content/population/1",
            "nations": [{"nation": n, "growth_per_10k_per_year": rates[n]}
                        for n in sorted({p["nation"] for p in provinces.values()})],
            "provinces": rows,
            "ceiling": {"people_per_agriculture": people_per_agriculture}}, []


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifact", required=True, type=Path)
    ap.add_argument("--people-per-agriculture", required=True, type=int)
    ap.add_argument("--start-ic-per-nation", required=True, type=int)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--census", type=Path, default=CENSUS)
    ap.add_argument("--growth", type=Path, default=GROWTH)
    a = ap.parse_args(argv[1:])
    census, growth = read_tsv(a.census), read_tsv(a.growth)
    doc, problems = convert(json.loads(a.artifact.read_text(encoding="utf-8")), census, growth,
                            json.loads(POLYGONS.read_text(encoding="utf-8")), a.people_per_agriculture,
                            a.start_ic_per_nation)
    for code, where, detail in problems:
        print(f"{code} {where}: {detail}")
    if doc is None:
        print(f"REFUSED: {len(problems)} problem(s); nothing written")
        return 1
    for r in census:
        if r["basis"].strip() == "derived":
            print(f'DERIVED {r["province"]}: {r["population"].strip()}')
    for r in growth:
        if r["basis"].strip() == "declared_zero":
            print(f'DECLARED ZERO {r["nation"]}: {r["note"].strip()}')
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {a.out}: {len(doc['provinces'])} provinces, {len(doc['nations'])} nations, "
          f"{sum(p['start_population'] for p in doc['provinces'])} people")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
