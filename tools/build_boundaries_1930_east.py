"""Rebuild data/boundaries/boundaries_1930.geojson with real 1930 eastern
borders from OpenHistoricalMap (AD-035, Sprint 6 P0-C).

Source: OHM Overpass API. OHM data is CC0 (public domain dedication,
verified 2026-07-03 at openhistoricalmap.org/copyright) — AD-018 compliant.

What this does:
  * KEEPS the existing NE-derived geometry byte-for-byte for countries whose
    1930 borders equal modern ones at sub-hex accuracy:
    BEL NLD LUX FRA CHE ITA AUT.
  * REPLACES DEU with the OHM Deutsches Reich 1922-06-20..1935-03-01 polygon
    (rel 2696515): full eastern extent (Pomerania, Silesia, East Prussia),
    Danzig/Memel/Saar correctly excluded.
  * ADDS SAA (Saar Basin territory, League-administered 1920-1935) as the
    difference (modern NE DEU) - (OHM Reich), clipped to the Saar area.
  * ADDS the 1930 eastern/framed countries from pinned OHM relations:
    POL DZG CSK HUN LTU LVA DNK SWE ROU YUG SOV (SOV bbox-clipped; the full
    USSR relation is impractically large).

Fail-loud self-checks (AD-030): per-country geodesic area bands and a
town-allegiance PIP table covering every load-bearing 1930 line. Any failure
aborts without writing.

Usage: uv run python tools/build_boundaries_1930_east.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import requests
from pyproj import Geod
from shapely.geometry import (
    LineString, MultiLineString, Point, box, mapping, shape,
)
from shapely.ops import linemerge, polygonize, unary_union

ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = ROOT / "data" / "boundaries" / "boundaries_1930.geojson"
CACHE = Path.home() / "wargame-cartographer" / "cache" / "boundaries" / "ohm_1930"
OVERPASS = "https://overpass-api.openhistoricalmap.org/api/interpreter"
SCENARIO_DATE = "1930-01-01"

# Expanded-bbox frame used only for clipping the SOV strip.
EAST_BBOX = box(5.8, 46.3, 26.9, 56.0)

KEEP = ["BEL", "NLD", "LUX", "FRA", "CHE", "ITA", "AUT"]

# Pinned OHM relations (probed 2026-07-03; each verified to span 1930-01-01).
OHM_COUNTRIES = {
    # code: (relation id, 1930 name, expected geodesic area km2 band)
    "DEU": (2696515, "Deutsches Reich", (420_000, 500_000)),
    "POL": (2692205, "Polska (Second Republic)", (350_000, 420_000)),
    "DZG": (2691478, "Freie Stadt Danzig", (1_500, 2_500)),
    "CSK": (2692233, "Československá republika", (125_000, 155_000)),
    "HUN": (2695633, "Magyar Királyság", (85_000, 100_000)),
    "LTU": (2692218, "Lietuva (incl. Memel Territory)", (50_000, 62_000)),
    "LVA": (2704702, "Latvija", (58_000, 72_000)),
    # NB: OHM country relations trace MARITIME (territorial-water) borders,
    # not coastlines — assembled areas exceed land area for coastal states.
    # Harmless for the pipeline: country PIP runs for LAND hex centers only.
    # Bands below are gross-error tripwires sized for water-inclusive areas.
    "DNK": (2850547, "Danmark", (38_000, 70_000)),   # clipped to Europe below
    "SWE": (2692524, "Sverige", (400_000, 500_000)),
    "ROU": (2693259, "Regatul României (incl. Bessarabia)", (270_000, 320_000)),
    "YUG": (2747831, "Kraljevina Jugoslavija", (230_000, 265_000)),
}
SOV_RELATION = 2851157  # Soviet Union 1926..1939 — bbox-clipped

# Town-allegiance verification (PIP against the assembled polygons). Every
# 1930 line the sprint brief calls load-bearing has at least one pair here.
TOWN_CHECKS = [
    # (name, lon, lat, expected country code)
    ("Berlin", 13.40, 52.52, "DEU"),
    ("Stettin", 14.55, 53.43, "DEU"),
    ("Breslau", 17.03, 51.11, "DEU"),
    ("Oppeln", 17.93, 50.67, "DEU"),
    ("Gleiwitz", 18.67, 50.29, "DEU"),
    ("Beuthen", 18.92, 50.35, "DEU"),
    ("Schneidemühl", 16.74, 53.15, "DEU"),
    ("Königsberg", 20.51, 54.71, "DEU"),
    ("Allenstein", 20.48, 53.78, "DEU"),
    ("Elbing", 19.40, 54.16, "DEU"),
    ("Köln (west sanity)", 6.96, 50.94, "DEU"),
    ("Saarbrücken", 7.00, 49.23, "SAA"),
    ("Saarlouis", 6.75, 49.31, "SAA"),
    ("Trier (not Saar)", 6.64, 49.76, "DEU"),
    ("Kaiserslautern (not Saar)", 7.77, 49.44, "DEU"),
    ("Danzig", 18.65, 54.35, "DZG"),
    ("Zoppot", 18.56, 54.44, "DZG"),
    ("Gdynia", 18.53, 54.52, "POL"),
    ("Toruń", 18.60, 53.01, "POL"),
    ("Bydgoszcz", 18.00, 53.12, "POL"),
    ("Poznań", 16.93, 52.41, "POL"),
    ("Katowice", 19.02, 50.26, "POL"),
    ("Königshütte/Chorzów", 18.95, 50.30, "POL"),
    ("Rybnik", 18.54, 50.10, "POL"),
    ("Wilno", 25.28, 54.69, "POL"),
    ("Lwów", 24.03, 49.84, "POL"),
    ("Brześć", 23.65, 52.10, "POL"),
    ("Kaunas", 23.90, 54.90, "LTU"),
    ("Memel/Klaipėda", 21.13, 55.71, "LTU"),
    ("Daugavpils", 26.53, 55.87, "LVA"),
    ("Praha", 14.42, 50.09, "CSK"),
    ("Eger/Cheb (Sudetenland)", 12.37, 50.08, "CSK"),
    ("Reichenberg/Liberec (Sudetenland)", 15.06, 50.77, "CSK"),
    ("Bratislava", 17.11, 48.14, "CSK"),
    ("Košice", 21.26, 48.72, "CSK"),
    ("Užhorod (Ruthenia)", 22.30, 48.62, "CSK"),
    ("Wien", 16.37, 48.21, "AUT"),
    ("Sopron (stayed Hungarian 1921)", 16.59, 47.68, "HUN"),
    ("Budapest", 19.04, 47.50, "HUN"),
    ("Flensburg (1920 plebiscite, German)", 9.44, 54.78, "DEU"),
    ("Sønderborg (1920 plebiscite, Danish)", 9.79, 54.91, "DNK"),
    ("Malmö", 13.00, 55.60, "SWE"),
    ("Cernăuți (Bukovina, Romanian)", 25.94, 48.29, "ROU"),
    ("Kamianets-Podilskyi (Soviet side of Zbruch)", 26.58, 48.68, "SOV"),
    ("Maribor", 15.65, 46.55, "YUG"),
]


def overpass(query: str, cache_name: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE / f"{cache_name}.json"
    if cache_file.exists():
        return json.loads(cache_file.read_text(encoding="utf-8"))
    r = requests.post(OVERPASS, data={"data": query}, timeout=600)
    r.raise_for_status()
    d = r.json()
    if not d.get("elements"):
        raise RuntimeError(f"OHM returned no elements for {cache_name}")
    cache_file.write_text(json.dumps(d), encoding="utf-8")
    return d


def check_dates(tags: dict, label: str) -> None:
    start = tags.get("start_date", "")
    end = tags.get("end_date", "9999")
    if not (start <= SCENARIO_DATE < end):
        raise RuntimeError(
            f"{label}: relation validity [{start}, {end}) does not cover "
            f"{SCENARIO_DATE} — re-pin the relation id.")


def assemble(elements: list, label: str):
    """Polygonize a relation's outer/inner member ways (probe-validated)."""
    outers, inners = [], []
    for e in elements:
        for m in e.get("members", []):
            if m.get("type") == "way" and "geometry" in m:
                coords = [(p["lon"], p["lat"]) for p in m["geometry"]]
                if len(coords) < 2:
                    continue
                (inners if m.get("role") == "inner" else outers).append(
                    LineString(coords))
    if not outers:
        raise RuntimeError(f"{label}: no outer ways")
    outer_polys = list(polygonize(linemerge(MultiLineString(outers))))
    if not outer_polys:
        raise RuntimeError(f"{label}: outer ways did not polygonize")
    geom = unary_union(outer_polys)
    if inners:
        inner_polys = list(polygonize(linemerge(MultiLineString(inners))))
        if inner_polys:
            geom = geom.difference(unary_union(inner_polys))
    if not geom.is_valid:
        geom = geom.buffer(0)
    return geom


GEOD = Geod(ellps="WGS84")


def area_km2(geom) -> float:
    return abs(GEOD.geometry_area_perimeter(geom)[0]) / 1e6


# Post-assembly clips for realm relations that span beyond Europe.
CLIPS = {
    "DNK": box(7.0, 54.0, 16.0, 58.5),   # Denmark proper (the 1930 relation
                                          # includes Greenland/Iceland realm)
}


def fetch_country(code: str) -> tuple:
    rel_id, name, band = OHM_COUNTRIES[code]
    d = overpass(f"[out:json][timeout:300];relation({rel_id});out geom;",
                 f"country_{code}_{rel_id}")
    rel = next(e for e in d["elements"] if e["type"] == "relation")
    check_dates(rel.get("tags", {}), f"{code}/{name}")
    geom = assemble(d["elements"], f"{code}/{name}")
    if code in CLIPS:
        geom = geom.intersection(CLIPS[code])
    a = area_km2(geom)
    lo, hi = band
    if not (lo <= a <= hi):
        raise RuntimeError(
            f"{code}/{name}: assembled area {a:,.0f} km² outside expected "
            f"[{lo:,}, {hi:,}] — ring assembly is incomplete or wrong.")
    print(f"  {code}: {name} — {a:,.0f} km² OK")
    return geom, name


def fetch_sov_clipped():
    """SOV in-bbox strip: clip the USSR border ways to the bbox and cut the
    bbox frame with them; keep pieces containing known Soviet points."""
    s, w, n, e = (EAST_BBOX.bounds[1], EAST_BBOX.bounds[0],
                  EAST_BBOX.bounds[3], EAST_BBOX.bounds[2])
    d = overpass(
        f"[out:json][timeout:600];relation({SOV_RELATION});"
        f"way(r)({s},{w},{n},{e});out geom;",
        f"country_SOV_{SOV_RELATION}_clip")
    lines = []
    for el in d["elements"]:
        if el.get("type") == "way" and "geometry" in el:
            coords = [(p["lon"], p["lat"]) for p in el["geometry"]]
            if len(coords) >= 2:
                lines.append(LineString(coords))
    if not lines:
        raise RuntimeError("SOV: no border ways in bbox")
    cutters = unary_union([EAST_BBOX.boundary] + lines)
    pieces = list(polygonize(cutters))
    sov_pts = [Point(26.58, 48.68)]           # Kamianets-Podilskyi
    not_sov = [Point(25.28, 54.69), Point(24.03, 49.84),   # Wilno, Lwów (POL)
               Point(26.53, 55.87), Point(25.94, 48.29)]   # Daugavpils, Cernăuți
    keep = [p for p in pieces
            if any(p.contains(pt) for pt in sov_pts)
            and not any(p.contains(pt) for pt in not_sov)]
    if not keep:
        raise RuntimeError("SOV: no bbox piece contains the Soviet test point")
    geom = unary_union(keep)
    print(f"  SOV: clipped strip — {area_km2(geom):,.0f} km² in-bbox")
    return geom


def main() -> int:
    existing = json.loads(BOUNDARIES.read_text(encoding="utf-8"))
    features = [f for f in existing["features"]
                if f["properties"]["country_code"] in KEEP]
    kept = sorted(f["properties"]["country_code"] for f in features)
    print(f"kept as-is (1930 == modern at sub-hex): {kept}")
    modern_deu = next(shape(f["geometry"]) for f in existing["features"]
                      if f["properties"]["country_code"] == "DEU")

    print("fetching OHM 1930 relations:")
    geoms: dict[str, tuple] = {}
    for code in OHM_COUNTRIES:
        geoms[code] = fetch_country(code)
    sov = fetch_sov_clipped()

    # SAA = modern DEU minus the Reich, clipped to the Saar area.
    saar_area = box(6.3, 49.1, 7.5, 49.65)
    saa = modern_deu.difference(geoms["DEU"][0]).intersection(saar_area)
    saa = max(saa.geoms, key=lambda g: g.area) if saa.geom_type == "MultiPolygon" else saa
    a = area_km2(saa)
    if not (1_500 <= a <= 2_600):   # historical Saar Basin ~1,912 km²
        raise RuntimeError(f"SAA: derived area {a:,.0f} km² implausible")
    print(f"  SAA: Saar Basin territory (derived) — {a:,.0f} km² OK")

    # --- Town-allegiance verification over the FULL new layer -------------
    def assigned(pt: Point) -> str:
        for code, (g, _) in geoms.items():
            if g.contains(pt):
                return code
        if saa.contains(pt):
            return "SAA"
        if sov.contains(pt):
            return "SOV"
        for f in features:
            if shape(f["geometry"]).contains(pt):
                return f["properties"]["country_code"]
        return ""

    failures = []
    for name, lon, lat, want in TOWN_CHECKS:
        got = assigned(Point(lon, lat))
        ok = got == want
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got or '(none)'} "
              f"(want {want})")
        if not ok:
            failures.append(name)
    if failures:
        print(f"\nABORT: {len(failures)} town checks failed — nothing written.")
        return 1

    # --- Write -------------------------------------------------------------
    def feat(code, geom, cname, notes):
        return {"type": "Feature",
                "properties": {"country_code": code, "country_name": cname,
                               "era": "1930",
                               "notes": notes},
                "geometry": mapping(geom)}

    ohm_note = ("OpenHistoricalMap (CC0 public domain dedication), extracted "
                f"{date.today().isoformat()}, relation pinned (AD-035)")
    for code, (geom, cname) in geoms.items():
        rel_id = OHM_COUNTRIES[code][0]
        features.append(feat(code, geom, cname, f"{ohm_note}; rel {rel_id}"))
    features.append(feat("SAA", saa, "Saar Basin Territory (League of Nations)",
                         "Derived: modern NE DEU minus OHM Reich (AD-035); "
                         "returns to Germany 1935-03-01"))
    features.append(feat("SOV", sov, "Soviet Union",
                         f"{ohm_note}; rel {SOV_RELATION}, clipped to the "
                         "expanded bbox (Riga-line strip)"))

    out = {"type": "FeatureCollection",
           "name": "para_bellum_boundaries_1930",
           "crs": existing.get("crs"),
           "features": features}
    BOUNDARIES.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    codes = [f["properties"]["country_code"] for f in features]
    print(f"\nwrote {BOUNDARIES.name}: {len(features)} countries: {codes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
