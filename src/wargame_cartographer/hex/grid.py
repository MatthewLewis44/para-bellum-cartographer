"""Hex grid with axial coordinates, flat-top layout, and wargame numbering."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pyproj
from shapely.geometry import Polygon

from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.geo.projection import make_transformer, select_crs


@dataclass
class HexCell:
    """A single hex in the grid."""

    q: int  # Axial column
    r: int  # Axial row
    center_x: float  # Projected CRS x (meters)
    center_y: float  # Projected CRS y (meters)
    center_lon: float  # WGS84 longitude
    center_lat: float  # WGS84 latitude


class HexGrid:
    """Flat-top hex grid covering a geographic bounding box.

    Uses axial (q, r) coordinates internally. Flat-top means columns run
    vertically, which is the standard wargame convention (SPI, GMT, AH).

    Wargame display numbering: 4-digit CCRR where CC = column (1-indexed),
    RR = row (1-indexed). E.g., column 20, row 13 = "2013".
    """

    def __init__(
        self,
        bbox: BoundingBox,
        hex_size_km: float,
        crs: pyproj.CRS | None = None,
    ):
        self.bbox = bbox
        # AD-013: hex_size_km is the FLAT-TO-FLAT distance between the two
        # parallel horizontal edges (= 2 × apothem), the canonical wargame
        # hex scale. Circumradius (center-to-vertex) = flat_to_flat / √3.
        self.hex_flat_to_flat_m = hex_size_km * 1000.0
        self.hex_radius_m = self.hex_flat_to_flat_m / math.sqrt(3.0)
        self.crs = crs or select_crs(bbox)
        self._to_proj = make_transformer("EPSG:4326", self.crs)
        self._to_geo = make_transformer(self.crs, "EPSG:4326")

        self.cells: dict[tuple[int, int], HexCell] = {}
        self._col_offset = 0
        self._row_offset = 0
        self._num_cols = 0
        self._num_rows = 0

        self._build_grid()

    def _build_grid(self):
        """Generate hex grid covering the bbox in projected coordinates."""
        r = self.hex_radius_m

        # Flat-top hex spacing
        col_spacing = 1.5 * r  # Horizontal distance between column centers
        row_spacing = math.sqrt(3) * r  # Vertical distance between row centers

        # Project the full bbox boundary (not just two opposite corners) to
        # the CRS. For a bbox wider than a UTM zone the projection curves,
        # so the SW/NE corners under-cover the bulging SE/NW edges — leaving
        # an uncovered wedge (Frankfurt fell in exactly this gap once AD-013
        # shrank the padding). Sampling all four edges captures the true
        # projected extent, guaranteeing every in-bbox point is tiled.
        n = 32
        ts = [i / n for i in range(n + 1)]
        d_lon = self.bbox.max_lon - self.bbox.min_lon
        d_lat = self.bbox.max_lat - self.bbox.min_lat
        edge_lons: list[float] = []
        edge_lats: list[float] = []
        for t in ts:
            lon_t = self.bbox.min_lon + d_lon * t
            lat_t = self.bbox.min_lat + d_lat * t
            # bottom & top edges (vary lon), then left & right edges (vary lat)
            edge_lons += [lon_t, lon_t, self.bbox.min_lon, self.bbox.max_lon]
            edge_lats += [self.bbox.min_lat, self.bbox.max_lat, lat_t, lat_t]
        xs, ys = self._to_proj.transform(edge_lons, edge_lats)
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)

        # Add half-hex padding
        min_x -= r
        min_y -= r
        max_x += r
        max_y += r

        # Determine grid extents in axial coords
        q_min = int(math.floor(min_x / col_spacing))
        q_max = int(math.ceil(max_x / col_spacing))
        r_min = int(math.floor(min_y / row_spacing))
        r_max = int(math.ceil(max_y / row_spacing))

        self._col_offset = q_min
        self._row_offset = r_min

        # Keep a hex if its center lies within the lon/lat bbox expanded by a
        # ~one-circumradius margin (so points on the bbox edge are covered by
        # a hex whose center may sit just outside). Testing lon/lat membership
        # (not the projected rectangle) gives tight, gap-free coverage that
        # follows the bbox instead of over-reaching the projected corners.
        lat_margin = r / 111_000.0
        lon_margin = r / (111_000.0 * max(0.2, math.cos(math.radians(self.bbox.max_lat))))
        lon_lo = self.bbox.min_lon - lon_margin
        lon_hi = self.bbox.max_lon + lon_margin
        lat_lo = self.bbox.min_lat - lat_margin
        lat_hi = self.bbox.max_lat + lat_margin

        for q in range(q_min, q_max + 1):
            for row in range(r_min, r_max + 1):
                # Flat-top: odd columns are shifted down by half a row
                cx = q * col_spacing
                cy = row * row_spacing
                if q % 2 != 0:
                    cy += row_spacing / 2.0

                lon, lat = self._to_geo.transform(cx, cy)
                if lon_lo <= lon <= lon_hi and lat_lo <= lat <= lat_hi:
                    cell = HexCell(
                        q=q, r=row,
                        center_x=cx, center_y=cy,
                        center_lon=lon, center_lat=lat,
                    )
                    self.cells[(q, row)] = cell

        # Compute grid dimensions for numbering
        if self.cells:
            qs = [k[0] for k in self.cells]
            rs = [k[1] for k in self.cells]
            self._num_cols = max(qs) - min(qs) + 1
            self._num_rows = max(rs) - min(rs) + 1

    def wargame_number(self, q: int, r: int) -> str:
        """Convert grid (q, r) to wargame display number (CCRR, 1-indexed)."""
        col = q - self._col_offset + 1
        row = r - self._row_offset + 1
        if self._num_cols > 99 or self._num_rows > 99:
            return f"{col:03d}{row:03d}"
        return f"{col:02d}{row:02d}"

    def hex_vertices(self, q: int, r: int) -> list[tuple[float, float]]:
        """Return 6 vertices of a flat-top hex in projected coordinates."""
        cell = self.cells[(q, r)]
        verts = []
        for i in range(6):
            angle_deg = 60 * i
            angle_rad = math.radians(angle_deg)
            vx = cell.center_x + self.hex_radius_m * math.cos(angle_rad)
            vy = cell.center_y + self.hex_radius_m * math.sin(angle_rad)
            verts.append((vx, vy))
        return verts

    def hex_polygon(self, q: int, r: int) -> Polygon:
        """Return Shapely polygon for a hex in projected CRS."""
        return Polygon(self.hex_vertices(q, r))

    def neighbors(self, q: int, r: int) -> list[tuple[int, int]]:
        """Return (q, r) tuples of the 6 neighbors that exist in the grid."""
        # Flat-top offset grid neighbor directions depend on column parity
        if q % 2 == 0:
            directions = [
                (1, 0), (1, -1), (0, -1), (-1, -1), (-1, 0), (0, 1)
            ]
        else:
            directions = [
                (1, 1), (1, 0), (0, -1), (-1, 0), (-1, 1), (0, 1)
            ]
        result = []
        for dq, dr in directions:
            nq, nr = q + dq, r + dr
            if (nq, nr) in self.cells:
                result.append((nq, nr))
        return result

    def distance(self, q1: int, r1: int, q2: int, r2: int) -> float:
        """Approximate distance in meters between two hex centers."""
        c1 = self.cells.get((q1, r1))
        c2 = self.cells.get((q2, r2))
        if c1 is None or c2 is None:
            return float("inf")
        dx = c1.center_x - c2.center_x
        dy = c1.center_y - c2.center_y
        return math.sqrt(dx * dx + dy * dy)

    def all_hexes(self) -> list[tuple[int, int]]:
        """All (q, r) pairs in the grid."""
        return list(self.cells.keys())

    @property
    def hex_count(self) -> int:
        return len(self.cells)
