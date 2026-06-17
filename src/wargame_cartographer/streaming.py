"""Tiled / streaming pipeline orchestrator (Sprint 4, T2/T4/T5/T6).

Processes any bbox at bounded RAM (< 4 GB/tile, < 6 GB global) by tiling the
per-hex sampling and discarding intermediate state between tiles. Output is
hex-for-hex identical to the monolithic pipeline (the per-hex pass-1 body and
the global coastal/sprawl passes are the SAME code; see hex/sampler.py).

Architecture (see docs/streaming-pipeline-design.md):
  GLOBAL once : hex grid, boundaries, resources, settlements→hex, AD-011
                waterway filter (streamed from parts).
  PER ~1° TILE: read landuse/roads/rails/bridges slices from the cached
                sub-bbox PARTS with a bbox(+0.2° margin) filter (never merging
                the whole layer); elevation via get_elevation(tile+margin,
                allow_synthetic=False); NE land/lakes/ports clipped to
                tile+margin (so land detection is tile-local). Sample only the
                tile's hexes (run_global_passes=False); pickle the tile; discard.
  MERGE       : assemble all tiles in grid order, run coastal + sprawl globally,
                export the final JSON.
"""

from __future__ import annotations

import gc
import math
import pickle
from pathlib import Path
from typing import Callable

import geopandas as gpd
import pandas as pd
from types import SimpleNamespace

from wargame_cartographer.config.map_spec import BoundingBox, MapSpec
from wargame_cartographer.geo.boundaries import load_boundaries_1930
from wargame_cartographer.geo.downloader import DataDownloader
from wargame_cartographer.geo.osm_downloader import OSMDownloader, DEFAULT_CACHE_DIR
from wargame_cartographer.geo.projection import select_crs
from wargame_cartographer.geo.resources import load_resources_1930
from wargame_cartographer.geo.urban_global import apply_global_passes
from wargame_cartographer.geo.waterways_global import compute_filtered_waterways
from wargame_cartographer.hex.grid import HexGrid
from wargame_cartographer.hex.sampler import (
    HexSampler,
    _assign_settlements_to_hexes,
    _assign_resources_to_hexes,
)
from wargame_cartographer.memory import working_set_mb

# Bump when the per-tile sampling logic changes, to invalidate stale tile caches.
STREAMING_VERSION = "s5.0"  # Sprint 5: rivers + province + admin_tier fields invalidate s4.x tiles
TILE_DEG = 1.0
MARGIN_DEG = 0.2
TILE_RAM_BUDGET_MB = 4096
GLOBAL_RAM_BUDGET_MB = 6144

_TILE_LOCAL_LAYERS = ("landuse", "roads", "railways", "bridges")
_LAYER_EMPTY_COLS = {
    "landuse": ["geometry", "osm_id", "landuse_type"],
    "roads": ["geometry", "osm_id", "road_level"],
    "railways": ["geometry", "osm_id", "rail_level"],
    "bridges": ["geometry", "osm_id", "name"],
}


def _bbox_hash(bbox: BoundingBox) -> str:
    import hashlib
    key = f"{bbox.min_lon:.4f},{bbox.min_lat:.4f},{bbox.max_lon:.4f},{bbox.max_lat:.4f}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _overlaps(a: BoundingBox, b: BoundingBox) -> bool:
    return not (a.max_lon < b.min_lon or a.min_lon > b.max_lon
                or a.max_lat < b.min_lat or a.min_lat > b.max_lat)


def _read_layer_slice(dl: OSMDownloader, spec_bbox: BoundingBox,
                      layer: str, margin: BoundingBox) -> gpd.GeoDataFrame:
    """Read a layer's features intersecting ``margin`` from the cached parts.

    Reads each overlapping sub-bbox part gpkg with a pyogrio bbox filter (only
    the slice is materialized), concatenates, and dedups ways by osm_id (a way
    spanning a part seam appears in both parts). Never merges the whole layer.
    """
    cols = _LAYER_EMPTY_COLS[layer]
    bb = (margin.min_lon, margin.min_lat, margin.max_lon, margin.max_lat)
    frames = []
    for sub, path in dl.part_descriptors(spec_bbox, layer):
        if not _overlaps(sub, margin):
            continue
        try:
            g = gpd.read_file(path, bbox=bb)
        except Exception:
            continue
        if len(g):
            frames.append(g)
    if not frames:
        return gpd.GeoDataFrame(columns=cols, crs="EPSG:4326")
    out = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
    if "osm_id" in out.columns:
        out = out.drop_duplicates(subset="osm_id").reset_index(drop=True)
    return out


def _tile_keys_for_grid(grid: HexGrid) -> dict[tuple[int, int], list]:
    """Group hex (q,r) keys by the integer-degree tile containing the center."""
    tiles: dict[tuple[int, int], list] = {}
    for (q, r), cell in grid.cells.items():
        tk = (int(math.floor(cell.center_lon)), int(math.floor(cell.center_lat)))
        tiles.setdefault(tk, []).append((q, r))
    return tiles


def run_streaming_pipeline(
    spec_path: str | Path,
    status_callback: Callable[[str], None] | None = None,
    *,
    json_only: bool = True,
) -> dict:
    """Run the tiled pipeline. Returns dict with hex_count, output JSON path,
    per-tile and global peak RAM (MB), and a tile count."""

    def status(msg: str):
        if status_callback:
            status_callback(msg)

    spec = MapSpec.from_yaml(spec_path)
    crs = select_crs(spec.bbox) if spec.crs is None else None
    grid = HexGrid(bbox=spec.bbox, hex_size_km=spec.hex_size_km, crs=crs)
    status(f"Grid ready: {grid.hex_count} hexes")

    # ---- GLOBAL small loads ----
    boundaries_gdf = None
    try:
        boundaries_gdf = load_boundaries_1930()
    except Exception as e:
        status(f"boundaries unavailable: {e}")
    resources_gdf = None
    try:
        resources_gdf = load_resources_1930()
    except Exception as e:
        status(f"resources unavailable: {e}")
    provinces = None
    try:
        from wargame_cartographer.geo.provinces import load_provinces
        provinces = load_provinces()
    except Exception as e:
        status(f"provinces unavailable: {e}")

    dl = OSMDownloader()
    status("[global] settlements...")
    settlements_gdf = dl.get_settlements(spec.bbox)  # small, merged is fine
    settlement_by_hex = _assign_settlements_to_hexes(grid, settlements_gdf) \
        if settlements_gdf is not None and not settlements_gdf.empty else {}
    resource_by_hex = _assign_resources_to_hexes(grid, resources_gdf) \
        if resources_gdf is not None and not resources_gdf.empty else {}

    # ---- GLOBAL waterway filter (AD-011, streamed from parts) ----
    filtered_waterways = compute_filtered_waterways(spec.bbox)

    # ---- ensure tile-local OSM parts cached (no merge) ----
    status("[global] ensuring OSM parts cached (no merge)...")
    dl.ensure_parts(spec.bbox)

    precomputed = {
        "settlement_by_hex": settlement_by_hex,
        "resource_by_hex": resource_by_hex,
    }

    # ---- TILE LOOP ----
    tiles = _tile_keys_for_grid(grid)
    tile_dir = DEFAULT_CACHE_DIR / f"tiles_{_bbox_hash(spec.bbox)}"
    tile_dir.mkdir(parents=True, exist_ok=True)
    ne = DataDownloader()
    sampler = HexSampler()

    # Elevation strategy: if a full-bbox DEM can be cached (small enough), read
    # per-tile WINDOWS of it so elevation/slope is byte-identical to monolithic.
    # For Europe-scale bboxes the full DEM exceeds the SRTM tile cap, so fall
    # back to a per-tile merge (no monolithic baseline to match there anyway).
    full_dem = sampler.elevation_proc.dem_cache_path(spec.bbox)
    if not full_dem.exists():
        try:
            sampler.elevation_proc.get_elevation(spec.bbox, allow_synthetic=False)
        except Exception as e:
            status(f"[elev] full DEM not buildable ({e}); per-tile merge mode")
    use_dem_window = full_dem.exists()
    gc.collect()
    status(f"[elev] mode: {'windowed full-DEM' if use_dem_window else 'per-tile merge'}")

    tile_peak_mb = 0.0
    n_tiles = len(tiles)
    tile_files: list[Path] = []
    for i, (tk, hex_keys) in enumerate(sorted(tiles.items())):
        tlon, tlat = tk
        tile_pkl = tile_dir / f"tile_{tlon:+04d}_{tlat:+03d}_{STREAMING_VERSION}.pkl"
        tile_files.append(tile_pkl)
        if tile_pkl.exists():
            continue  # resume (T5)

        margin = BoundingBox(
            min_lon=tlon - MARGIN_DEG, min_lat=tlat - MARGIN_DEG,
            max_lon=tlon + TILE_DEG + MARGIN_DEG, max_lat=tlat + TILE_DEG + MARGIN_DEG,
        )
        status(f"[tile {i + 1}/{n_tiles}] ({tlon},{tlat}) {len(hex_keys)} hexes")

        # tile-local OSM slices (bbox-filtered reads of the parts)
        osm_tile = SimpleNamespace(
            landuse=_read_layer_slice(dl, spec.bbox, "landuse", margin),
            roads=_read_layer_slice(dl, spec.bbox, "roads", margin),
            railways=_read_layer_slice(dl, spec.bbox, "railways", margin),
            bridges=_read_layer_slice(dl, spec.bbox, "bridges", margin),
            waterways=filtered_waterways,        # global filtered set, whole
            settlements=gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"),  # precomputed
        )
        # tile-local NE land/lakes/ports (clipped to margin → tile-local prep)
        try:
            land = ne.get_natural_earth("land", margin)
        except Exception:
            land = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        try:
            lakes = ne.get_natural_earth("lakes", margin)
        except Exception:
            lakes = gpd.GeoDataFrame(geometry=[], crs="EPSG:4326")
        vector_tile = SimpleNamespace(
            land=land, lakes=lakes,
            ports=gpd.GeoDataFrame(geometry=[], crs="EPSG:4326"),
        )

        # Per-tile elevation: windowed read of the full DEM (identical to
        # monolithic) or a per-tile merge fallback (Europe scale).
        if use_dem_window:
            tile_elev = sampler.elevation_proc.get_elevation_window(spec.bbox, margin)
        else:
            tile_elev = sampler.elevation_proc.get_elevation(margin, allow_synthetic=False)
        tile_precomputed = {**precomputed, "elevation": tile_elev}

        tile_result = sampler.build_hex_terrain(
            grid, margin, vector_data=vector_tile, osm_data=osm_tile,
            boundaries_gdf=boundaries_gdf, resources_gdf=resources_gdf,
            provinces=provinces,
            hex_keys=hex_keys, precomputed=tile_precomputed,
            run_global_passes=False, allow_synthetic_elevation=False,
        )
        del tile_elev, tile_precomputed

        ws = working_set_mb()
        tile_peak_mb = max(tile_peak_mb, ws)
        if ws > TILE_RAM_BUDGET_MB:
            raise MemoryError(
                f"Tile ({tlon},{tlat}) working set {ws:.0f} MB exceeds "
                f"{TILE_RAM_BUDGET_MB} MB budget"
            )

        with open(tile_pkl, "wb") as f:
            pickle.dump(tile_result, f, protocol=pickle.HIGHEST_PROTOCOL)
        del tile_result, osm_tile, vector_tile, land, lakes
        gc.collect()

    # ---- MERGE (global reconcile) ----
    status("[merge] assembling tiles in grid order...")
    tile_lookup: dict[tuple[int, int], dict] = {}
    for tile_pkl in tile_files:
        if not tile_pkl.exists():
            continue
        with open(tile_pkl, "rb") as f:
            tile_lookup.update(pickle.load(f))

    # Reassemble in grid.cells iteration order (NOT tile order) — required for
    # deterministic coastal/sprawl tie-breaks at seams.
    result: dict[tuple[int, int], dict] = {}
    for key in grid.cells:
        if key in tile_lookup:
            result[key] = tile_lookup[key]
    del tile_lookup
    gc.collect()

    apply_global_passes(result, grid, settlement_by_hex,
                        provinces=provinces, settlements_gdf=settlements_gdf)
    global_peak_mb = working_set_mb()
    if global_peak_mb > GLOBAL_RAM_BUDGET_MB:
        raise MemoryError(
            f"Global merge working set {global_peak_mb:.0f} MB exceeds "
            f"{GLOBAL_RAM_BUDGET_MB} MB budget"
        )

    # ---- EXPORT ----
    from wargame_cartographer.output.game_data_exporter import export_game_data
    output_dir = Path(spec.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "_-" else "_" for c in spec.name)[:40].lower()
    json_path = export_game_data(
        grid, result, spec, output_dir / f"{safe_name}_hex_terrain.json"
    )
    status("Done (streaming)!")

    return {
        "hex_count": len(result),
        "tiles": n_tiles,
        "tile_peak_mb": round(tile_peak_mb, 0),
        "global_peak_mb": round(global_peak_mb, 0),
        "output_json": str(json_path),
    }
