"""Global waterway significance filter for the streaming pipeline (Sprint 4 T3).

The AD-011 filter keeps a named river/canal only if its TOTAL geodesic length
across the whole bbox exceeds ``MIN_WATERWAY_TOTAL_M`` (110 km). That total is
inherently global, but it must be computed without ever holding all of a
continent's unfiltered waterways in one in-memory GeoDataFrame (the wall this
sprint kills). So this streams the cached sub-bbox parts twice:

  pass 1 — accumulate name → total geodesic length (floats only, no geometry)
  pass 2 — re-read parts, keep geometries only for names over the threshold

The result is the SAME filtered set the monolithic ``get_waterways`` produces
(same osm_id dedup, same per-name totals); it is passed WHOLE to every tile's
``_river_for_hex`` (bounded — majors only), so river_edges AND the AD-026 node
fields (has_river / river_name) are seam-identical.
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from pyproj import Geod
from rich.console import Console

from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.geo.osm_downloader import OSMDownloader

console = Console()

_EMPTY_COLS = ["geometry", "waterway_type", "name", "osm_id"]


def compute_filtered_waterways(
    bbox: BoundingBox, cache_dir: Path | None = None
) -> gpd.GeoDataFrame:
    """Return the AD-011-filtered significant waterways for ``bbox`` (whole-bbox).

    Ensures the raw waterway parts are cached (merge=False), then streams them.
    """
    dl = OSMDownloader(cache_dir)
    console.print("  [global] waterway significance filter (streamed)...", style="dim")
    dl.get_waterways(bbox, merge=False)  # ensure raw parts cached, no merge
    parts = dl.part_descriptors(bbox, "waterways")
    if not parts:
        return gpd.GeoDataFrame(columns=_EMPTY_COLS, crs="EPSG:4326")

    geod = Geod(ellps="WGS84")

    # Pass 1: per-name total geodesic length (dedup ways by osm_id across parts).
    name_len: dict[str, float] = {}
    seen: set = set()
    for _sub, path in parts:
        gdf = gpd.read_file(path)
        for name, geom, oid in zip(gdf["name"].values, gdf.geometry.values,
                                   gdf["osm_id"].values if "osm_id" in gdf.columns
                                   else [None] * len(gdf)):
            if oid is not None:
                if oid in seen:
                    continue
                seen.add(oid)
            if not name or geom is None:
                continue
            name_len[name] = name_len.get(name, 0.0) + geod.geometry_length(geom)
        del gdf

    keep_names = {n for n, total in name_len.items()
                  if total > OSMDownloader.MIN_WATERWAY_TOTAL_M}
    if not keep_names:
        return gpd.GeoDataFrame(columns=_EMPTY_COLS, crs="EPSG:4326")

    # Pass 2: collect geometries only for kept names (dedup by osm_id again).
    records: list[dict] = []
    seen2: set = set()
    for _sub, path in parts:
        gdf = gpd.read_file(path)
        has_id = "osm_id" in gdf.columns
        for i in range(len(gdf)):
            row = gdf.iloc[i]
            name = row["name"]
            if name not in keep_names:
                continue
            oid = row["osm_id"] if has_id else None
            if oid is not None:
                if oid in seen2:
                    continue
                seen2.add(oid)
            geom = row.geometry
            if geom is None or geom.geom_type not in ("LineString", "MultiLineString"):
                continue
            records.append({
                "geometry": geom,
                "waterway_type": row["waterway_type"],
                "name": name,
                "osm_id": oid,
            })
        del gdf

    kept = gpd.GeoDataFrame(records, crs="EPSG:4326") if records else \
        gpd.GeoDataFrame(columns=_EMPTY_COLS, crs="EPSG:4326")
    console.print(
        f"  [global] waterways: kept {len(kept)} ways over "
        f"{len(keep_names)} names ≥ {OSMDownloader.MIN_WATERWAY_TOTAL_M / 1000:.0f} km",
        style="dim",
    )
    return kept
