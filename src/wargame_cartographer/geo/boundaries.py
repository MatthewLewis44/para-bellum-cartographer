"""1930 political boundaries for Para Bellum.

Downloads ``world_1930.geojson`` from the aourednik/historical-basemaps
project — real 1930 country borders, not a modern approximation — and
provides point-in-polygon country assignment for hex centers.

The file is external data: it is cached under the user cache directory
(like SRTM / OSM layers) and must never be committed to the repo.

Historical data is static, so the cache has no TTL — delete the cached
file manually to force a re-download.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import requests
from rich.console import Console
from shapely.geometry import Point
from shapely.prepared import prep

console = Console()

BOUNDARIES_1930_URL = (
    "https://raw.githubusercontent.com/aourednik/historical-basemaps"
    "/master/geojson/world_1930.geojson"
)
DEFAULT_CACHE_DIR = Path.home() / "wargame-cartographer" / "cache" / "boundaries"

# Country names exactly as they appear in the world_1930.geojson NAME field
# (verified against the file), mapped to ISO3 codes. Extend this map as the
# game bbox grows; unmapped countries resolve to "" (treated as no-country).
_NAME_TO_ISO3: dict[str, str] = {
    "Belgium":     "BEL",
    "Netherlands": "NLD",
    "Luxembourg":  "LUX",
    "Germany":     "DEU",
    "France":      "FRA",
}


def download_boundaries_1930(cache_dir: Path | None = None) -> gpd.GeoDataFrame:
    """Download (or load cached) 1930 world boundaries.

    Returns a GeoDataFrame (EPSG:4326) with columns:
        geometry      — country polygon/multipolygon
        country_code  — ISO3 code from _NAME_TO_ISO3, "" if unmapped
        country_name  — NAME field as in the source file
    """
    cache_dir = Path(cache_dir) if cache_dir is not None else DEFAULT_CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / "world_1930.geojson"

    if not path.exists():
        console.print("  Downloading 1930 world boundaries...", style="dim")
        resp = requests.get(
            BOUNDARIES_1930_URL,
            timeout=120,
            headers={"User-Agent": "para-bellum-cartographer/0.1 (game dev pipeline)"},
        )
        resp.raise_for_status()
        path.write_bytes(resp.content)

    gdf = gpd.read_file(path)
    gdf = gdf.rename(columns={"NAME": "country_name"})
    gdf["country_code"] = gdf["country_name"].map(_NAME_TO_ISO3).fillna("")
    return gdf[["geometry", "country_code", "country_name"]]


# Prepared-geometry cache keyed by GeoDataFrame identity, so repeated
# assign_country() calls over the same boundaries prep each polygon once.
_PREP_CACHE: dict[int, list] = {}


def _prepared_geoms(boundaries_gdf: gpd.GeoDataFrame) -> list:
    key = id(boundaries_gdf)
    cached = _PREP_CACHE.get(key)
    if cached is None or len(cached) != len(boundaries_gdf):
        cached = [prep(g) for g in boundaries_gdf.geometry.values]
        _PREP_CACHE.clear()  # only one boundary set is live at a time
        _PREP_CACHE[key] = cached
    return cached


# Coastal snap tolerance (degrees, ~0.2° ≈ 20 km ≈ 2 hexes). The 1930
# dataset's coastline is coarse (BORDERPRECISION=3): real coastal land
# (Den Helder, Wadden islands, East Frisia) and post-1930 polders
# (Flevoland) fall just outside every polygon. A hex center is a 10 km
# quantized sample — snap such points to the nearest country within
# tolerance instead of leaving inhabited coast country-less.
COASTAL_SNAP_DEG = 0.2


def assign_country(
    lon: float,
    lat: float,
    boundaries_gdf: gpd.GeoDataFrame,
) -> str:
    """Return the ISO3 country code containing point (lon, lat), or "".

    Uses the GeoDataFrame spatial index to shortlist candidate polygons and
    prepared geometries for the containment test, so per-point cost stays
    O(1)-ish at the 100k-hex scale. Points not covered by any polygon snap
    to the nearest country within COASTAL_SNAP_DEG (coarse-coastline guard);
    beyond that, empty string (open sea / unmapped country).
    """
    if boundaries_gdf is None or boundaries_gdf.empty:
        return ""

    pt = Point(lon, lat)
    prepared = _prepared_geoms(boundaries_gdf)
    codes = boundaries_gdf["country_code"].values

    for idx in boundaries_gdf.sindex.query(pt):
        # covers() includes the boundary itself, unlike contains()
        if prepared[idx].covers(pt):
            code = codes[idx]
            if code:
                return str(code)

    # Coastal snap: nearest polygon within tolerance.
    best_code = ""
    best_dist = COASTAL_SNAP_DEG
    geoms = boundaries_gdf.geometry.values
    for idx in boundaries_gdf.sindex.query(pt.buffer(COASTAL_SNAP_DEG)):
        if not codes[idx]:
            continue
        d = geoms[idx].distance(pt)
        if d < best_dist:
            best_dist = d
            best_code = str(codes[idx])
    return best_code
