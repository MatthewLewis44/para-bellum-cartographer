"""1930 political boundaries for Para Bellum.

Loads ``data/boundaries/boundaries_1930.geojson`` — repo-committed,
hand-authored from Natural Earth ne_10m_admin_0_countries (public domain,
AD-018) — and provides point-in-polygon country assignment for hex centers.

These are MODERN borders used as a 1930 stopgap, valid for the current
western-front bbox (1930 western German borders ≈ modern; the significant
1930 differences are on eastern borders outside the bbox). The previous
source (historical-basemaps world_1930) was CC BY-NC-SA — non-commercial,
cannot ship — and was removed per AD-018.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from rich.console import Console
from shapely.geometry import Point
from shapely.prepared import prep

console = Console()

# Repo-committed boundary file (public domain — see AD-018). Resolved
# relative to the repo root (…/src/wargame_cartographer/geo/boundaries.py
# → three parents up), falling back to the working directory.
_REPO_ROOT = Path(__file__).resolve().parents[3]
BOUNDARIES_FILE = _REPO_ROOT / "data" / "boundaries" / "boundaries_1930.geojson"


def load_boundaries_1930(path: Path | None = None) -> gpd.GeoDataFrame:
    """Load the repo-committed 1930 boundaries.

    Returns a GeoDataFrame (EPSG:4326) with columns:
        geometry      — country polygon/multipolygon
        country_code  — ISO3 code
        country_name  — display name
    """
    path = Path(path) if path is not None else BOUNDARIES_FILE
    if not path.exists():
        fallback = Path.cwd() / "data" / "boundaries" / "boundaries_1930.geojson"
        if fallback.exists():
            path = fallback
        else:
            raise FileNotFoundError(
                f"Boundary file not found: {path} (see data/boundaries/, AD-018)"
            )

    gdf = gpd.read_file(path)
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
