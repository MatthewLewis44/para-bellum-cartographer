"""River SELECTION for Para Bellum (AD-029): Natural Earth scalerank + OSM canals.

Supersedes the OSM-derived AD-011 geodesic-length heuristic as the river
SELECTION mechanism. Two sources, unioned:

  * NATURAL RIVERS — Natural Earth ``rivers_lake_centerlines`` filtered by the
    dataset's curated ``scalerank`` (<= ``river_scalerank_max``). Curated
    significance, globally consistent, no generic-name false positives
    (the Mühlgraben/Mühlbach problem is gone — those names are simply not in
    Natural Earth).
  * CANALS — Natural Earth has NO canals, but the Albert Canal (and other
    strategic canals) are required majors. So canals are still sourced from OSM,
    kept by the AD-011 per-name geodesic length filter — RETAINED for canals
    only, where generic-name over-aggregation is not a problem (canals are few
    and named). This is the "minimal necessary OSM path" AD-029 anticipates.

The combined set carries a ``name`` column and is fed WHOLE to the AD-026 node
computation (``sampler._river_for_hex``) — identically to the monolithic pass and
to every streaming tile (AD-025) — so has_river / river_name / river_edges are
seam-identical. The AD-026 node model itself is unchanged; only the SOURCE of
which rivers exist changes.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
import pandas as pd
from pyproj import Geod
from rich.console import Console

from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.geo.downloader import DataDownloader
from wargame_cartographer.geo.osm_downloader import OSMDownloader

console = Console()

_COLS = ["geometry", "name", "waterway_type", "scalerank"]
_LINES = ("LineString", "MultiLineString")


def _empty() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(columns=_COLS, crs="EPSG:4326")


def compute_selected_rivers(
    bbox: BoundingBox, scalerank_max: int = 8, cache_dir: Path | None = None
) -> gpd.GeoDataFrame:
    """Return the AD-029 selected rivers+canals for ``bbox`` (whole-bbox).

    NE rivers with ``scalerank <= scalerank_max`` UNION OSM significant canals.
    """
    ne_rivers = _select_ne_rivers(bbox, scalerank_max, cache_dir)
    canals = _select_osm_canals(bbox, cache_dir)
    frames = [f for f in (ne_rivers, canals) if not f.empty]
    if not frames:
        return _empty()
    combined = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    console.print(
        f"  [global] rivers (AD-029): {len(ne_rivers)} NE ways (scalerank<="
        f"{scalerank_max}) + {len(canals)} OSM canal ways = {len(combined)} selected",
        style="dim",
    )
    return combined


def _select_ne_rivers(
    bbox: BoundingBox, scalerank_max: int, cache_dir: Path | None
) -> gpd.GeoDataFrame:
    """Natural Earth rivers with scalerank <= threshold (the AD-029 core)."""
    dl = DataDownloader(cache_dir) if cache_dir else DataDownloader()
    g = dl.get_natural_earth("rivers", bbox)
    if g.empty or "scalerank" not in g.columns:
        return _empty()
    g = g[g["scalerank"] <= scalerank_max]
    g = g[g.geometry.notna() & g.geometry.geom_type.isin(_LINES)]
    # Keep only NAMED rivers — an unnamed river-hex would violate the
    # name-iff-has_river contract and is undisplayable in Unity. At scalerank<=8
    # every genuinely-significant NE river is named (verified 0 unnamed in the
    # Belgium/Benelux/Europe bboxes); this only guards a future sparsely-named
    # region. (review finding L1)
    if "name" not in g.columns:
        return _empty()
    g = g[g["name"].notna() & (g["name"].astype(str).str.strip() != "")]
    if g.empty:
        return _empty()
    return gpd.GeoDataFrame({
        "geometry": g.geometry.values,
        "name": g["name"].astype(str).values,
        "waterway_type": "river",
        "scalerank": g["scalerank"].astype(int).values,
    }, crs="EPSG:4326")


def _select_osm_canals(
    bbox: BoundingBox, cache_dir: Path | None
) -> gpd.GeoDataFrame:
    """OSM waterway=canal kept by AD-011 per-name geodesic length (canals only).

    Streamed over the cached waterway parts (so the streaming RAM budget holds);
    only the ``waterway_type == "canal"`` subset is considered, so river names
    (the Mühlgraben source) never enter selection here.
    """
    dl = OSMDownloader(cache_dir) if cache_dir else OSMDownloader()
    dl.get_waterways(bbox, merge=False)  # ensure raw parts cached
    parts = dl.part_descriptors(bbox, "waterways")
    if not parts:
        return _empty()
    geod = Geod(ellps="WGS84")

    # pass 1: per-canal-name total geodesic length (dedup ways by osm_id)
    name_len: dict[str, float] = {}
    seen: set = set()
    for _sub, path in parts:
        gdf = gpd.read_file(path)
        if "waterway_type" in gdf.columns:
            gdf = gdf[gdf["waterway_type"] == "canal"]
        if "name" in gdf.columns:
            gdf = gdf[gdf["name"].notna()]  # robust NaN/None name guard (review L2)
        if gdf.empty:
            continue
        has_id = "osm_id" in gdf.columns
        for name, geom, oid in zip(
            gdf["name"].values, gdf.geometry.values,
            gdf["osm_id"].values if has_id else [None] * len(gdf),
        ):
            if oid is not None:
                if oid in seen:
                    continue
                seen.add(oid)
            if not name or geom is None:
                continue
            name_len[name] = name_len.get(name, 0.0) + geod.geometry_length(geom)
        del gdf

    keep = {n for n, t in name_len.items() if t > OSMDownloader.MIN_WATERWAY_TOTAL_M}
    if not keep:
        return _empty()

    # pass 2: geometries for kept canal names
    records: list[dict] = []
    seen2: set = set()
    for _sub, path in parts:
        gdf = gpd.read_file(path)
        if "waterway_type" in gdf.columns:
            gdf = gdf[gdf["waterway_type"] == "canal"]
        if "name" in gdf.columns:
            gdf = gdf[gdf["name"].notna()]  # robust NaN/None name guard (review L2)
        if gdf.empty:
            continue
        has_id = "osm_id" in gdf.columns
        for i in range(len(gdf)):
            row = gdf.iloc[i]
            name = row["name"]
            if name not in keep:
                continue
            oid = row["osm_id"] if has_id else None
            if oid is not None:
                if oid in seen2:
                    continue
                seen2.add(oid)
            geom = row.geometry
            if geom is None or geom.geom_type not in _LINES:
                continue
            records.append({
                "geometry": geom, "name": name,
                "waterway_type": "canal", "scalerank": 7,
            })
        del gdf

    return gpd.GeoDataFrame(records, crs="EPSG:4326") if records else _empty()
