"""Pre-Sprint 9.0 probe: current resource state + 1930 boundary attribution.

Usage:
    uv run python tools/probe_resource_attribution.py

1. Reports the shipped east artifact's resource counts by type, by country and
   by province (verifies the brief's numbers against the actual data).
2. Resolves every Pre-Sprint-9.0 candidate deposit coordinate to the country
   the 1930 boundaries file actually puts it in — the boundaries file is the
   authority on ownership, not the feature's `country` property.
Read-only. Writes nothing.
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ARTIFACT = REPO / "output" / "para_bellum_east_expansion_hex_terrain.json"
BOUNDS = REPO / "data" / "boundaries" / "boundaries_1930.geojson"
BBOX = (5.8, 46.3, 26.9, 56.0)  # min_lon, min_lat, max_lon, max_lat

TYPES = ("coal", "steel", "iron", "oil")

# (label, lon, lat, intended 1930 owner, resource type)
CANDIDATES = [
    # --- coal ---
    ("Upper Silesia — Katowice",       19.02, 50.26, "POL", "coal"),
    ("Upper Silesia — Królewska Huta", 18.95, 50.30, "POL", "coal"),
    ("Upper Silesia — Beuthen (DE)",   18.92, 50.35, "DEU", "coal"),
    ("Upper Silesia — Gleiwitz (DE)",  18.67, 50.29, "DEU", "coal"),
    ("Upper Silesia — Rybnik",         18.55, 50.10, "POL", "coal"),
    ("Dąbrowa basin — Sosnowiec",      19.15, 50.29, "POL", "coal"),
    ("Dąbrowa basin — Dąbrowa Górn.",  19.28, 50.33, "POL", "coal"),
    ("Lower Silesia — Waldenburg",     16.28, 50.77, "DEU", "coal"),
    ("Ostrava–Karviná — Ostrava",      18.29, 49.83, "CSK", "coal"),
    ("Ostrava–Karviná — Karviná",      18.55, 49.85, "CSK", "coal"),
    ("Kladno",                         14.10, 50.15, "CSK", "coal"),
    ("North Bohemian lignite — Most",  13.63, 50.50, "CSK", "coal"),
    ("North Bohemian lignite — Teplice", 13.82, 50.64, "CSK", "coal"),
    ("North Bohemian lignite — Sokolov", 12.64, 50.18, "CSK", "coal"),
    ("Central German lignite — Halle", 11.97, 51.48, "DEU", "coal"),
    ("Central German lignite — Bitterfeld", 12.32, 51.62, "DEU", "coal"),
    ("Central German lignite — Leipzig/Borna", 12.50, 51.12, "DEU", "coal"),
    ("Lusatian lignite — Senftenberg", 13.99, 51.52, "DEU", "coal"),
    ("Lusatian lignite — Spremberg",   14.37, 51.57, "DEU", "coal"),
    ("Zwickau hard coal",              12.50, 50.72, "DEU", "coal"),
    ("Wałbrzych/Neurode",              16.50, 50.57, "DEU", "coal"),
    # --- iron ---
    ("Salzgitter",                     10.35, 52.15, "DEU", "iron"),
    ("Peine",                          10.23, 52.32, "DEU", "iron"),
    ("Siegerland — Siegen",             8.02, 50.87, "DEU", "iron"),
    ("Erzberg (Eisenerz)",             14.88, 47.54, "AUT", "iron"),
    ("Częstochowa",                    19.12, 50.81, "POL", "iron"),
    ("Nučice (Prague basin ore)",      14.20, 49.99, "CSK", "iron"),
    # --- steel works (points) ---
    ("Königshütte/Chorzów works",      18.95, 50.30, "POL", "steel"),
    ("Huta Pokój (Nowy Bytom)",        18.90, 50.26, "POL", "steel"),
    ("Huta Baildon (Katowice)",        18.99, 50.26, "POL", "steel"),
    ("Vítkovice (Ostrava)",            18.28, 49.81, "CSK", "steel"),
    ("Třinec works",                   18.67, 49.68, "CSK", "steel"),
    ("Škoda (Plzeň)",                  13.38, 49.74, "CSK", "steel"),
    ("Donawitz (Leoben)",              15.07, 47.38, "AUT", "steel"),
    ("Hermann-Göring/Salzgitter?",     10.35, 52.15, "DEU", "steel"),
    ("Bismarckhütte (Hajduki)",        18.93, 50.27, "POL", "steel"),
    ("Huta Bankowa (Dąbrowa)",         19.28, 50.33, "POL", "steel"),
    ("Poldi Kladno works",             14.10, 50.14, "CSK", "steel"),
    ("Riesa (Saxony) works",           13.29, 51.31, "DEU", "steel"),
    ("Bochumer Verein",                 7.22, 51.48, "DEU", "steel"),
    ("Hüttenwerk Peine",               10.23, 52.32, "DEU", "steel"),
    ("Linz (no works in 1930)",        14.29, 48.31, "AUT", "steel"),
    # --- oil ---
    ("Borysław",                       23.36, 49.29, "POL", "oil"),
    ("Drohobycz",                      23.51, 49.35, "POL", "oil"),
    ("Tustanowice",                    23.40, 49.27, "POL", "oil"),
    ("Bitków (E. Galicia)",            24.42, 48.63, "POL", "oil"),
    ("Krosno / Jasło (W. Galicia)",    21.77, 49.69, "POL", "oil"),
    ("Zistersdorf (Lower Austria)",    16.76, 48.54, "AUT", "oil"),
    ("Ploiești (OUT OF BBOX check)",   26.02, 44.94, "ROU", "oil"),
]


def in_bbox(lon: float, lat: float) -> bool:
    return BBOX[0] <= lon <= BBOX[2] and BBOX[1] <= lat <= BBOX[3]


def report_artifact() -> None:
    if not ARTIFACT.exists():
        print(f"(artifact missing: {ARTIFACT})\n")
        return
    with open(ARTIFACT, encoding="utf-8") as f:
        data = json.load(f)
    hexes = data["hexes"]
    print(f"{ARTIFACT.name}: schema {data['schema_version']}, {len(hexes)} hexes")

    counts = {r: sum(1 for h in hexes if h["resources"].get(r)) for r in TYPES}
    agri = sum(1 for h in hexes if h["resources"].get("agriculture"))
    print(f"  totals: {counts}  agriculture={agri}\n")

    by_country: dict[str, Counter] = defaultdict(Counter)
    for h in hexes:
        c = h["political"]["country_at_start"] or "(none)"
        for r in TYPES:
            if h["resources"].get(r):
                by_country[c][r] += 1
    print("  resource hexes by country (current artifact):")
    for c in sorted(by_country, key=lambda k: -sum(by_country[k].values())):
        row = "  ".join(f"{r}={by_country[c][r]}" for r in TYPES if by_country[c][r])
        print(f"    {c:<6} {row}")
    print()


def report_boundaries() -> None:
    gdf = gpd.read_file(BOUNDS)
    code_col = next((c for c in ("country_code", "iso_a3", "code", "ADM0_A3", "id")
                     if c in gdf.columns), None)
    name_col = next((c for c in ("name", "NAME", "country") if c in gdf.columns), None)
    print(f"boundaries_1930.geojson: {len(gdf)} features, "
          f"columns={list(gdf.columns)}")
    if code_col:
        print(f"  codes: {sorted(gdf[code_col].dropna().unique())}\n")

    print(f"{'candidate':<36} {'lon':>7} {'lat':>6} {'want':>5} {'bounds':>7} "
          f"{'bbox':>5}  type")
    print("-" * 84)
    for label, lon, lat, want, rtype in CANDIDATES:
        pt = Point(lon, lat)
        hit = gdf[gdf.geometry.covers(pt)]
        if len(hit):
            got = "/".join(str(hit.iloc[i][code_col] if code_col else
                               hit.iloc[i][name_col]) for i in range(len(hit)))
        else:
            got = "-none-"
        flag = "" if got == want else "  <-- MISMATCH"
        print(f"{label:<36} {lon:>7.2f} {lat:>6.2f} {want:>5} {got:>7} "
              f"{'in' if in_bbox(lon, lat) else 'OUT':>5}  {rtype}{flag}")


if __name__ == "__main__":
    report_artifact()
    report_boundaries()
