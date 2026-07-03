"""Elevation data loading and hillshade computation."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np
from matplotlib.colors import LightSource
from rich.console import Console

from wargame_cartographer.config.map_spec import BoundingBox

console = Console()

#: SRTM nodata / void sentinel. The AWS skadi tiles are void-filled (zero
#: voids measured in the Belgium/Benelux DEMs) but voids are guaranteed
#: somewhere at Europe scale; unmasked they produce huge slope spikes at
#: void edges and a -32768 elevation sample.
SRTM_VOID = -32768


def _bbox_hash(bbox: BoundingBox) -> str:
    key = f"{bbox.min_lon:.4f},{bbox.min_lat:.4f},{bbox.max_lon:.4f},{bbox.max_lat:.4f}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


class ElevationProcessor:
    """Load elevation rasters and compute hillshade."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or (Path.home() / "wargame-cartographer" / "cache" / "elevation")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def get_elevation(
        self, bbox: BoundingBox, allow_synthetic: bool = False
    ) -> tuple[np.ndarray, dict]:
        """Get elevation data for a bounding box.

        Returns (elevation_array, metadata_dict).
        metadata_dict contains 'transform', 'crs', 'bounds', 'resolution'.

        Uses SRTM via rasterio. ``allow_synthetic`` **defaults to False** (AD-030):
        a silent sin/cos synthetic substitution on SRTM failure would be cached
        and shipped, invisibly breaking the hex-for-hex guarantee. Both the
        streaming and monolithic paths fail loud on SRTM failure. Offline dev
        opts in explicitly by passing ``allow_synthetic=True`` (the monolithic
        pipeline exposes this via the ``PARA_BELLUM_ALLOW_SYNTHETIC_ELEVATION=1``
        env var).
        """
        cache_key = f"dem_{_bbox_hash(bbox)}.tif"
        cache_path = self.cache_dir / cache_key

        # Try to load cached GeoTIFF
        if cache_path.exists():
            return self._load_geotiff(cache_path)

        # Try downloading SRTM via rasterio/SRTM
        try:
            return self._download_srtm(bbox, cache_path)
        except Exception as e:
            if not allow_synthetic:
                raise RuntimeError(
                    f"SRTM elevation unavailable for {bbox} and synthetic is "
                    f"disabled (streaming mode): {e}"
                ) from e
            console.print(f"  [yellow]SRTM download failed ({e}), using synthetic elevation[/yellow]")
            return self._synthetic_elevation(bbox)

    def _download_srtm(self, bbox: BoundingBox, cache_path: Path) -> tuple[np.ndarray, dict]:
        """Download SRTM tiles and merge for the bbox."""
        import rasterio
        from rasterio.merge import merge
        from rasterio.warp import calculate_default_transform, reproject, Resampling

        console.print("  Downloading SRTM elevation data...", style="dim")

        # Compute which 1-degree SRTM tiles we need
        lat_min = int(math.floor(bbox.min_lat))
        lat_max = int(math.floor(bbox.max_lat))
        lon_min = int(math.floor(bbox.min_lon))
        lon_max = int(math.floor(bbox.max_lon))

        # Limit: if too many tiles, use synthetic instead
        n_tiles = (lat_max - lat_min + 1) * (lon_max - lon_min + 1)
        if n_tiles > 100:
            console.print(f"  [yellow]Area requires {n_tiles} SRTM tiles (max 100); too large for one merge[/yellow]")
            raise RuntimeError(
                f"Too many SRTM tiles needed: {n_tiles} (max 100). Use the "
                f"streaming pipeline so elevation is read per ~1° tile."
            )

        tile_paths = []
        for lat in range(lat_min, lat_max + 1):
            for lon in range(lon_min, lon_max + 1):
                tile_path = self._download_srtm_tile(lat, lon)
                if tile_path:
                    tile_paths.append(tile_path)

        if not tile_paths:
            raise RuntimeError("No SRTM tiles available for this area")

        # Merge tiles
        datasets = [rasterio.open(p) for p in tile_paths]
        try:
            merged, transform = merge(datasets, bounds=bbox.as_tuple())
            elevation = merged[0]  # First band

            metadata = {
                "transform": transform,
                "crs": "EPSG:4326",
                "bounds": bbox.as_tuple(),
                "resolution": 90,  # meters (approx for SRTM 3 arc-second)
            }

            # Cache the merged result
            with rasterio.open(
                cache_path, "w",
                driver="GTiff",
                height=elevation.shape[0],
                width=elevation.shape[1],
                count=1,
                dtype=elevation.dtype,
                crs="EPSG:4326",
                transform=transform,
            ) as dst:
                dst.write(elevation, 1)

            return elevation, metadata
        finally:
            for ds in datasets:
                ds.close()

    def _download_srtm_tile(self, lat: int, lon: int) -> Path | None:
        """Download a single SRTM 1-degree tile."""
        import requests

        ns = "N" if lat >= 0 else "S"
        ew = "E" if lon >= 0 else "W"
        tile_name = f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}"
        filename = f"{tile_name}.hgt.zip"
        tile_cache = self.cache_dir / f"{tile_name}.hgt"

        if tile_cache.exists():
            return tile_cache

        # Try NASA SRTM v3
        url = f"https://elevation-tiles-prod.s3.amazonaws.com/skadi/{tile_name[:3]}/{tile_name}.hgt.gz"
        try:
            resp = requests.get(url, timeout=60)
            if resp.status_code == 200:
                import gzip
                with open(tile_cache, "wb") as f:
                    f.write(gzip.decompress(resp.content))
                return tile_cache
        except Exception:
            pass

        return None

    def _load_geotiff(self, path: Path) -> tuple[np.ndarray, dict]:
        """Load a cached GeoTIFF."""
        import rasterio

        with rasterio.open(path) as src:
            elevation = src.read(1)
            metadata = {
                "transform": src.transform,
                "crs": str(src.crs),
                "bounds": src.bounds,
                "resolution": src.res[0],
            }
        return elevation, metadata

    def dem_cache_path(self, bbox: BoundingBox) -> Path:
        """Path of the cached full-bbox DEM GeoTIFF for ``bbox``."""
        return self.cache_dir / f"dem_{_bbox_hash(bbox)}.tif"

    def get_elevation_window(
        self, full_bbox: BoundingBox, window_bbox: BoundingBox
    ) -> tuple[np.ndarray, dict]:
        """Read a window of the cached full-bbox DEM (streaming, Sprint 4 T2).

        Returns the EXACT pixels of the monolithic full-bbox raster over
        ``window_bbox``, so per-tile elevation/slope sampling is byte-identical
        to the monolithic run (the per-tile ``merge(bounds=...)`` path produces
        a subtly different pixel grid and was the source of seam diffs). Loads
        only the window into RAM; the full DEM stays on disk. Raises if the full
        DEM is not cached (caller falls back to a per-tile merge for bboxes too
        large to build a full DEM, e.g. Europe).
        """
        import rasterio
        from rasterio.windows import from_bounds, Window

        path = self.dem_cache_path(full_bbox)
        if not path.exists():
            raise FileNotFoundError(f"full DEM not cached: {path}")
        with rasterio.open(path) as src:
            win = from_bounds(
                window_bbox.min_lon, window_bbox.min_lat,
                window_bbox.max_lon, window_bbox.max_lat, src.transform,
            ).round_offsets().round_lengths()
            win = win.intersection(Window(0, 0, src.width, src.height))
            elevation = src.read(1, window=win)
            win_transform = src.window_transform(win)
            res = src.res[0]
        metadata = {
            "transform": win_transform,
            "crs": "EPSG:4326",
            "bounds": window_bbox.as_tuple(),
            "resolution": res,
        }
        return elevation, metadata

    def _synthetic_elevation(self, bbox: BoundingBox) -> tuple[np.ndarray, dict]:
        """Generate synthetic elevation data as a fallback.

        Uses a simple latitude-based model: higher terrain away from coasts.
        This is a placeholder until real SRTM data is available.
        """
        # Create a 500x500 grid
        height, width = 500, 500
        lats = np.linspace(bbox.max_lat, bbox.min_lat, height)
        lons = np.linspace(bbox.min_lon, bbox.max_lon, width)

        # Simple elevation model: Perlin-like noise approximation
        lon_grid, lat_grid = np.meshgrid(lons, lats)
        elevation = (
            200 * np.sin(lat_grid * 3.0) * np.cos(lon_grid * 3.0)
            + 100 * np.sin(lat_grid * 7.0 + lon_grid * 5.0)
            + 50 * np.cos(lat_grid * 11.0 - lon_grid * 9.0)
            + 300  # Base elevation
        )
        elevation = np.clip(elevation, 0, 3000).astype(np.float32)

        from rasterio.transform import from_bounds

        transform = from_bounds(
            bbox.min_lon, bbox.min_lat, bbox.max_lon, bbox.max_lat,
            width, height,
        )

        metadata = {
            "transform": transform,
            "crs": "EPSG:4326",
            "bounds": bbox.as_tuple(),
            "resolution": 90,
        }
        return elevation, metadata

    def compute_hillshade(
        self,
        elevation: np.ndarray,
        azimuth: float = 315.0,
        altitude: float = 45.0,
    ) -> np.ndarray:
        """Compute hillshade from elevation array.

        Returns a 0-255 array suitable for overlay rendering.
        Azimuth 315 (NW light source) is the wargame convention.
        """
        ls = LightSource(azdeg=azimuth, altdeg=altitude)
        # Normalize elevation for hillshade
        if elevation.max() > elevation.min():
            hillshade = ls.hillshade(elevation, vert_exag=2.0)
        else:
            hillshade = np.ones_like(elevation) * 0.5
        return hillshade

    def compute_slope(
        self, elevation: np.ndarray, transform, scale_m: float = 90.0
    ) -> np.ndarray:
        """Compute terrain-scale slope in degrees from an EPSG:4326 DEM.

        Sprint 6 fix (AD-033). The old implementation ran
        ``np.gradient(elevation, 90.0)`` — isotropic 90 m pixels — but the
        rasters are 1-arcsec (~30.9 m N-S; ~19.7 m E-W at 51°N), so slopes
        were underestimated ~3x N-S and ~4.5x E-W. Now:

        - **Per-axis metric pitch from the transform**: N-S pitch
          ``|transform.e| * 111320``; E-W pitch additionally scaled by
          cos(lat) **per row**, so the correction tracks latitude across a
          tile and across Europe.
        - **SRTM voids masked to NaN** before the gradient (no spikes at
          void edges); downstream sampling is nan-aware.
        - **Wide central difference spanning ~scale_m per side** (k pixels,
          k = scale_m / N-S pitch): slope is measured at the ~90 m terrain
          scale the biome classifier was designed for (SRTM3-equivalent),
          not per-pixel micro-relief (embankments, road cuts) — see the
          Sprint 6 slope probe. The stencil is window-invariant, which
          preserves streaming/monolithic hex identity; block-aggregating
          the raster would NOT be (block alignment differs between a full
          raster and a windowed tile read).

        Returns a float32 grid aligned with ``elevation`` (same transform).
        The outer ``k`` rows/cols are NaN — one-sided differences there
        would differ between a windowed read and the full raster. Tile
        margins (0.2 deg >> 90 m) keep sampled hexes clear of the band.
        """
        e = elevation.astype(np.float32)
        e[elevation == SRTM_VOID] = np.nan

        lat_pitch_m = abs(transform.e) * 111_320.0
        k = max(1, round(scale_m / lat_pitch_m))
        if e.shape[0] <= 2 * k or e.shape[1] <= 2 * k:
            return np.full_like(e, np.nan)

        lats = transform.f + transform.e * (np.arange(e.shape[0]) + 0.5)
        lon_pitch_m = (abs(transform.a) * 111_320.0
                       * np.cos(np.radians(lats))).astype(np.float32)

        dy = np.full_like(e, np.nan)
        dx = np.full_like(e, np.nan)
        dy[k:-k, :] = (e[2 * k:, :] - e[:-2 * k, :]) / np.float32(2 * k * lat_pitch_m)
        dx[:, k:-k] = (e[:, 2 * k:] - e[:, :-2 * k]) / (
            np.float32(2 * k) * lon_pitch_m[:, None])
        # In-place: hypot -> arctan -> degrees, reusing dx as the output.
        np.hypot(dx, dy, out=dx)
        np.arctan(dx, out=dx)
        np.degrees(dx, out=dx)
        return dx

    def sample_at_point(
        self, elevation: np.ndarray, metadata: dict, lon: float, lat: float
    ) -> float:
        """Sample elevation at a geographic point."""
        transform = metadata["transform"]
        # Inverse transform: geo → pixel
        col, row = ~transform * (lon, lat)
        row, col = int(round(row)), int(round(col))
        if 0 <= row < elevation.shape[0] and 0 <= col < elevation.shape[1]:
            return float(elevation[row, col])
        return 0.0
