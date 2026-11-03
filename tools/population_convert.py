"""Generate Content/pb/population.json from the sourced census table and the shipped map (Sprint 14).

    uv run python tools/population_convert.py --artifact <hex_terrain.json> --people-per-agriculture N --out population.json

Reads data/population/census_1930.tsv (one row per province: the WHOLE unit's census figure, with its
year, source and note) and growth_1930.tsv (natural increase by nation, with its source), and the map
artifact. One rule is a judgement listed for review rather than a fact:

  start_population = census x the share of the province's polygon inside the map frame. A province the
      map crops carries only its on-map share, which assumes its people are spread evenly across it.

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
INDUSTRY = ROOT / "data" / "population" / "industry_1930.tsv"   # province -> starting points; absent until ruled
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


def convert(artifact: dict, census: list[dict], growth: list[dict], industry: list[dict], polygons: dict,
            people_per_agriculture: int) -> tuple[dict | None, list[tuple[str, str, str]]]:
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
        rated.add(n)
    for n in sorted({p["nation"] for p in provinces.values()} - rated):
        bad.add("X206", n, "owns a province on the map and has no growth row: add its natural increase, with a source")

    points: dict[str, int] = {}
    for r in industry:
        pid, v = r["province"], r["start_industry"].strip()
        if pid in points:
            bad.add("X203", pid, "has two industry rows: keep one")
        elif not v.isdigit():
            bad.add("X205", pid, f'"{v}" is not a whole number of points: write digits only')
        else:
            points[pid] = int(v)
    known_industry = {r["province"] for r in industry}
    for pid in sorted(set(provinces) - known_industry):
        bad.add("X209", pid, "is on the map and has no industry row: add one, 0 if it has none")

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
                     "start_industry": points.get(pid, 0)})
    if bad.problems:
        return None, sorted(bad.problems)
    return {"format": "pb-content/population/1",
            "nations": [{"nation": n, "growth_per_10k_per_year": rates[n]}
                        for n in sorted({p["nation"] for p in provinces.values()})],
            "provinces": rows,
            "ceiling": {"people_per_agriculture": people_per_agriculture}}, []


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--artifact", required=True, type=Path)
    ap.add_argument("--people-per-agriculture", required=True, type=int)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--census", type=Path, default=CENSUS)
    ap.add_argument("--growth", type=Path, default=GROWTH)
    ap.add_argument("--industry", type=Path, default=INDUSTRY)
    a = ap.parse_args(argv[1:])
    doc, problems = convert(json.loads(a.artifact.read_text(encoding="utf-8")), read_tsv(a.census), read_tsv(a.growth),
                            read_tsv(a.industry) if a.industry.exists() else [],
                            json.loads(POLYGONS.read_text(encoding="utf-8")), a.people_per_agriculture)
    for code, where, detail in problems:
        print(f"{code} {where}: {detail}")
    if doc is None:
        print(f"REFUSED: {len(problems)} problem(s); nothing written")
        return 1
    a.out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {a.out}: {len(doc['provinces'])} provinces, {len(doc['nations'])} nations, "
          f"{sum(p['start_population'] for p in doc['provinces'])} people")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
