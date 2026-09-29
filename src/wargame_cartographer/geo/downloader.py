"""Data download and cache management for geographic data."""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import requests
from rich.console import Console

from wargame_cartographer.config.defaults import (
    CACHE_MAX_AGE_DAYS,
    NATURAL_EARTH_LAYERS,
)
from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.manifest import record_part

console = Console()

DEFAULT_CACHE_DIR = Path.home() / "wargame-cartographer" / "cache"


def _bbox_hash(bbox: BoundingBox) -> str:
    key = f"{bbox.min_lon:.4f},{bbox.min_lat:.4f},{bbox.max_lon:.4f},{bbox.max_lat:.4f}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _is_fresh(path: Path, max_age_days: int = CACHE_MAX_AGE_DAYS) -> bool:
    if not path.exists():
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400
    return age_days < max_age_days


# --- Natural Earth layer freshness (AD-041) --------------------------------
#
# NE layers unpack into a DIRECTORY, and a directory's mtime does not move when
# the files inside it are overwritten. `_is_fresh(cache_path)` on the directory
# therefore reported the age of the FIRST extraction forever: once a layer aged
# past the TTL it re-downloaded on EVERY run and never came back. A 15-tile
# Belgium streaming run pulled land and lakes 30 times, and ne_10m_rivers — the
# AD-029 river SELECTION source, i.e. map content — came down on every run.
# Found by the AD-040 manifest, which marked those layers `role: fetched`.
#
# Freshness now comes from an explicit stamp written after a COMPLETED
# extraction, which also fixes the quieter half of the bug: a half-extracted
# directory used to look like a good cache.
_NE_STAMP_SUFFIX = ".fetched"


def _ne_stamp_path(layer_dir: Path) -> Path:
    """Stamp for an unpacked NE layer, kept BESIDE the directory, not inside.

    Inside would change the directory's content hash on every refresh, and that
    hash is what an artifact manifest records for this part (AD-040).
    """
    return layer_dir.with_name(layer_dir.name + _NE_STAMP_SUFFIX)


def _write_ne_stamp(layer_dir: Path, when: float | None = None) -> None:
    """Record that ``layer_dir`` holds a complete extraction as of ``when``."""
    stamp = _ne_stamp_path(layer_dir)
    try:
        stamp.touch()
        if when is not None:
            os.utime(stamp, (when, when))
    except OSError:
        pass  # a lost stamp costs one re-download, never correctness


def ne_fetch_time(layer_dir: Path) -> float | None:
    """Epoch seconds when this NE layer was last extracted, or None if never.

    A cache written before the stamp existed adopts the newest file inside it
    as its fetch time and is stamped with that, backdated — so landing this fix
    re-dates existing caches instead of re-downloading them.
    """
    if not layer_dir.is_dir():
        return None
    stamp = _ne_stamp_path(layer_dir)
    if stamp.exists():
        return stamp.stat().st_mtime
    files = [f for f in layer_dir.glob("*") if f.is_file()]
    if not files:
        return None
    newest = max(f.stat().st_mtime for f in files)
    _write_ne_stamp(layer_dir, newest)
    return newest


def ne_layer_is_fresh(layer_dir: Path,
                      max_age_days: int = CACHE_MAX_AGE_DAYS) -> bool:
    fetched = ne_fetch_time(layer_dir)
    return fetched is not None and (time.time() - fetched) / 86400 < max_age_days


class DataDownloader:
    """Fetch and cache geographic data."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "vector").mkdir(exist_ok=True)
        (self.cache_dir / "osm").mkdir(exist_ok=True)
        (self.cache_dir / "elevation").mkdir(exist_ok=True)

    def get_natural_earth(
        self, layer: str, bbox: BoundingBox | None = None
    ) -> gpd.GeoDataFrame:
        """Download and cache Natural Earth vector data, clipped to bbox."""
        if layer not in NATURAL_EARTH_LAYERS:
            raise ValueError(f"Unknown layer: {layer}. Available: {list(NATURAL_EARTH_LAYERS.keys())}")

        cache_path = self.cache_dir / "vector" / f"ne_10m_{layer}"

        if not ne_layer_is_fresh(cache_path):
            url = NATURAL_EARTH_LAYERS[layer]
            console.print(f"  Downloading Natural Earth {layer}...", style="dim")
            resp = requests.get(url, timeout=120)
            resp.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                cache_path.mkdir(parents=True, exist_ok=True)
                zf.extractall(cache_path)
            _write_ne_stamp(cache_path)   # only after a COMPLETE extraction
            record_part(cache_path, layer=f"natural_earth:{layer}", role="fetched")

        shp_files = list(cache_path.glob("*.shp"))
        if not shp_files:
            raise FileNotFoundError(f"No .shp file found in {cache_path}")
        # The whole unpacked shapefile directory is the consumed part: .shp
        # alone would miss the .dbf attributes the pipeline reads (scalerank).
        record_part(cache_path, layer=f"natural_earth:{layer}")

        gdf = gpd.read_file(shp_files[0])
        if bbox is not None:
            gdf = gdf.cx[bbox.min_lon:bbox.max_lon, bbox.min_lat:bbox.max_lat]
        return gdf

    def get_cities(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Get cities using Natural Earth populated_places (fast, no OSM needed)."""
        cache_key = f"cities_{_bbox_hash(bbox)}"
        cache_path = self.cache_dir / "osm" / f"{cache_key}.gpkg"

        if _is_fresh(cache_path):
            return gpd.read_file(cache_path)

        console.print("  Loading cities from Natural Earth...", style="dim")
        try:
            gdf = self.get_natural_earth("populated_places", bbox)
            if not gdf.empty:
                # Keep relevant columns
                cols = ["geometry", "NAME", "POP_MAX", "FEATURECLA"]
                existing = [c for c in cols if c in gdf.columns]
                gdf = gdf[existing].copy()
                # Rename for consistency
                if "NAME" in gdf.columns:
                    gdf = gdf.rename(columns={"NAME": "name"})
                if "POP_MAX" in gdf.columns:
                    gdf = gdf.rename(columns={"POP_MAX": "population"})
                gdf.to_file(cache_path, driver="GPKG")
            return gdf
        except Exception as e:
            console.print(f"  [yellow]City data failed: {e}[/yellow]")
            return gpd.GeoDataFrame()

    # NOTE (AD-036): port detection was RETIRED in Sprint 7. Starting
    # infrastructure (ports/airfields/fortifications) is authored
    # construction-system scenario data, not something to sniff from modern
    # OSM. The old get_ports()/_overpass_to_gdf() Overpass path is gone; the
    # `infrastructure.port` schema field stays inert (False) until the
    # construction system fills it from authored data. Do NOT re-add
    # OSM/OHM port detection here.
