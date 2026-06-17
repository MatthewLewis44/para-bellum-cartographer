"""Extend data/boundaries/boundaries_1930.geojson with CHE / AUT / ITA (P0-C).

The W+C Europe run produced empty-country land hexes over Switzerland, Austria
and northern Italy because the 1930 boundary stopgap only had the 5 western
countries (AD-018). This appends those three from Natural Earth admin_0 (public
domain), a 1930 stopgap valid for the bbox:

  * Switzerland — borders stable since the 19th century; modern == 1930.
  * Austria — independent First Republic in 1930 (pre-Anschluss 1938); modern
    borders == 1930 (post-1919: lost South Tyrol to Italy, etc.).
  * Italy — modern polygon == 1930 for the bbox's NORTHERN reach (Piedmont,
    Lombardy, South Tyrol/Alto Adige, all Italian in 1930). 1930 Italy's eastern
    gains (Trieste/Istria, lost 1947) are outside the bbox, so the modern
    polygon is an accurate stopgap here.

APPEND-ONLY: the existing 5 countries' geometry is preserved byte-for-byte, so
the Benelux hex-identical gates are unaffected — only the previously-empty
Alpine/N-Italian hexes change. Provinces for CHE/AUT/ITA are NOT authored this
sprint (C3) — country coverage only.

Usage:  uv run python tools/extend_boundaries_1930.py
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd

from wargame_cartographer.geo.downloader import DataDownloader
from wargame_cartographer.config.map_spec import BoundingBox

BOUNDARIES = Path(__file__).resolve().parents[1] / "data" / "boundaries" / "boundaries_1930.geojson"

ADD = {
    "CHE": ("Switzerland", "Borders stable since 19th c.; modern == 1930."),
    "AUT": ("Austria", "Independent First Republic in 1930 (pre-Anschluss); modern == 1930."),
    "ITA": ("Italy", "Modern polygon == 1930 for the bbox's northern reach (S. Tyrol Italian since 1919)."),
}


def main():
    existing = gpd.read_file(BOUNDARIES)
    have = set(existing["country_code"])
    missing = [c for c in ADD if c not in have]
    if not missing:
        print(f"Already present: {sorted(have)} — nothing to add.")
        return

    dl = DataDownloader()
    ne = dl.get_natural_earth("countries", BoundingBox(min_lon=5, min_lat=42, max_lon=18, max_lat=54))
    fld = "ADM0_A3" if "ADM0_A3" in ne.columns else "ISO_A3"

    new_rows = []
    for code in missing:
        sub = ne[ne[fld] == code]
        if sub.empty:
            print(f"WARNING: {code} not found in Natural Earth admin_0 — skipped")
            continue
        name, note = ADD[code]
        new_rows.append({
            "country_code": code,
            "country_name": name,
            "era": "1930-stopgap-modern",
            "notes": note,
            "geometry": sub.geometry.values[0],
        })

    if not new_rows:
        print("Nothing added.")
        return

    added = gpd.GeoDataFrame(new_rows, crs="EPSG:4326")
    combined = gpd.GeoDataFrame(
        pd.concat([existing, added], ignore_index=True), crs="EPSG:4326"
    )
    combined.to_file(BOUNDARIES, driver="GeoJSON")
    print(f"Added {[r['country_code'] for r in new_rows]}; "
          f"file now has {len(combined)} countries: {sorted(combined['country_code'])}")


if __name__ == "__main__":
    main()
