"""Para Bellum hex sampler.

Samples all geographic data layers per hex and produces the full
per-hex data dict consumed by game_data_exporter.py.

Each hex goes through these stages in order:
    1. Water detection (Natural Earth land polygon)
    2. Elevation + slope sampling (SRTM)
    3. Landuse classification (OSM landuse/natural areas)
    4. Settlement assignment (OSM place nodes)
    5. Biome classification (BiomeClassifier)
    6. Vegetation + moisture derivation
    7. Road/rail level (OSM highways + railways)
    8. River edge detection (OSM waterways × hex edges)
    9. Bridge detection (OSM bridge=yes)
    10. Coastal flag (hexes adjacent to water hexes)
    11. Port detection (upstream DataDownloader ports layer)

OUT OF SCOPE (not sampled here — Sprint 2 or Unity):
    - GADM political boundaries (country/province) — Sprint 2
    - Strategic resources (oil/coal/steel) — Sprint 2, manual GeoJSON
    - Fortification layer — Sprint 2, manual GeoJSON
    - Player-placed buildings — Unity runtime
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.prepared import prep

from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.geo.elevation import ElevationProcessor
from wargame_cartographer.hex.grid import HexGrid
from wargame_cartographer.hex.coords import (
    offset_neighbors,
    CUBE_DIRECTIONS,
    offset_to_cube,
    cube_to_offset,
    OffsetCoord,
)
from wargame_cartographer.terrain.classifier import BiomeClassifier
from wargame_cartographer.terrain.types import (
    Biome,
    WATER_BIOMES,
    IMPASSABLE_BIOMES,
)


class HexSampler:
    """Sample all geo layers per hex and produce Para Bellum hex data dicts."""

    def __init__(self):
        self.elevation_proc = ElevationProcessor()
        self.classifier = BiomeClassifier()

    def build_hex_terrain(
        self,
        grid: HexGrid,
        bbox: BoundingBox,
        vector_data=None,      # upstream VectorData (Natural Earth layers)
        osm_data=None,         # OSMLayerData (our new OSM layers)
    ) -> dict[tuple[int, int], dict]:
        """Build complete per-hex data for every hex in the grid.

        Returns dict keyed by (q, r) → hex data dict matching the
        Para Bellum JSON schema fields (excluding political/resources,
        which are added by Sprint 2 pipeline stages).
        """

        # ----------------------------------------------------------------
        # 1. Load elevation + slope
        # ----------------------------------------------------------------
        elevation, elev_metadata = self.elevation_proc.get_elevation(bbox)
        slope_grid = self.elevation_proc.compute_slope(elevation)

        # ----------------------------------------------------------------
        # 2. Build spatial indexes from vector layers
        # ----------------------------------------------------------------
        land_prep = None
        lake_prep = None

        if vector_data is not None:
            if hasattr(vector_data, "land") and not vector_data.land.empty:
                try:
                    land_prep = prep(unary_union(vector_data.land.geometry))
                except Exception:
                    pass
            if hasattr(vector_data, "lakes") and not vector_data.lakes.empty:
                try:
                    lake_prep = prep(unary_union(vector_data.lakes.geometry))
                except Exception:
                    pass

        # OSM layers — may be None if download failed
        landuse_sindex = None
        landuse_gdf = None
        settlements_gdf = None
        roads_gdf = None
        railways_gdf = None
        waterways_gdf = None
        bridges_gdf = None
        ports_gdf = None

        if osm_data is not None:
            if hasattr(osm_data, "landuse") and not osm_data.landuse.empty:
                landuse_gdf = osm_data.landuse
                landuse_sindex = landuse_gdf.sindex

            if hasattr(osm_data, "settlements") and not osm_data.settlements.empty:
                settlements_gdf = osm_data.settlements

            if hasattr(osm_data, "roads") and not osm_data.roads.empty:
                roads_gdf = osm_data.roads

            if hasattr(osm_data, "railways") and not osm_data.railways.empty:
                railways_gdf = osm_data.railways

            if hasattr(osm_data, "waterways") and not osm_data.waterways.empty:
                waterways_gdf = osm_data.waterways

            if hasattr(osm_data, "bridges") and not osm_data.bridges.empty:
                bridges_gdf = osm_data.bridges

        # Ports from upstream vector_data
        if vector_data is not None and hasattr(vector_data, "ports"):
            if not vector_data.ports.empty:
                ports_gdf = vector_data.ports

        # ----------------------------------------------------------------
        # 3. First pass — classify every hex
        # ----------------------------------------------------------------
        result: dict[tuple[int, int], dict] = {}

        for (q, r), cell in grid.cells.items():
            pt = Point(cell.center_lon, cell.center_lat)

            # -- Water detection --
            is_water = False
            if land_prep is not None:
                is_water = not land_prep.contains(pt)
            else:
                # Fallback: negative SRTM elevation = ocean
                elev_sample = self.elevation_proc.sample_at_point(
                    elevation, elev_metadata, cell.center_lon, cell.center_lat
                )
                is_water = elev_sample <= 0

            is_lake = False
            if not is_water and lake_prep is not None:
                is_lake = lake_prep.contains(pt)

            # -- Elevation + slope --
            elev_m = self.elevation_proc.sample_at_point(
                elevation, elev_metadata, cell.center_lon, cell.center_lat
            )
            elev_m = max(0.0, elev_m) if not is_water else elev_m

            transform = elev_metadata["transform"]
            slope_deg = _sample_max_slope_in_hex(
                slope_grid, transform, grid.hex_polygon(q, r), grid
            )

            # -- Landuse --
            landuse_type = None
            if not is_water and not is_lake and landuse_gdf is not None:
                landuse_type = _sample_landuse(pt, landuse_gdf, landuse_sindex)

            # -- Settlement --
            has_settlement = False
            settlement_place_type = None
            settlement_name = ""
            settlement_type_str = "none"
            pop_class = 0

            if not is_water and not is_lake and settlements_gdf is not None:
                threshold_deg = (grid.hex_radius_m / 111320.0)
                match = _find_nearest_settlement(
                    pt, settlements_gdf, threshold_deg
                )
                if match is not None:
                    has_settlement = True
                    settlement_place_type = match["place_type"]
                    settlement_name = match["name"]
                    settlement_type_str = _place_to_settlement_type(
                        settlement_place_type, match.get("population", 0)
                    )
                    pop_class = _pop_class(settlement_type_str)

            # -- Biome --
            biome = self.classifier.classify(
                elevation_m=elev_m,
                slope_deg=slope_deg,
                is_water=is_water,
                is_lake=is_lake,
                is_coastal=False,  # filled in pass 2
                landuse_type=landuse_type,
                has_settlement=has_settlement,
                settlement_place_type=settlement_place_type,
                lat=cell.center_lat,
                lon=cell.center_lon,
            )

            vegetation = self.classifier.classify_vegetation(landuse_type, biome)
            moisture = self.classifier.classify_moisture(
                biome, landuse_type, elev_m, cell.center_lat, cell.center_lon
            )

            # -- Road level --
            # Use WGS84 hex polygon to match roads_gdf CRS (EPSG:4326)
            road_level = "none"
            if not is_water and not is_lake and roads_gdf is not None:
                hex_poly_wgs84 = _hex_polygon_wgs84(q, r, grid)
                road_level = _best_road_in_hex(hex_poly_wgs84, roads_gdf)

            # -- Rail level --
            rail_level = "none"
            if not is_water and not is_lake and railways_gdf is not None:
                if 'hex_poly_wgs84' not in dir():
                    hex_poly_wgs84 = _hex_polygon_wgs84(q, r, grid)
                rail_level = _best_rail_in_hex(hex_poly_wgs84, railways_gdf)

            # -- River edges --
            river_edges: list[int] = []
            if waterways_gdf is not None:
                river_edges = _river_edges_for_hex(
                    q, r, grid, waterways_gdf
                )

            # -- Bridge --
            bridge = False
            if river_edges and bridges_gdf is not None:
                bridge = _hex_has_bridge(pt, bridges_gdf, grid.hex_radius_m)

            # -- Port --
            port = False
            if ports_gdf is not None:
                port = _hex_has_point_feature(pt, ports_gdf, grid.hex_radius_m * 0.6)

            # -- Anthrome --
            anthrome = _derive_anthrome(biome, landuse_type, settlement_type_str)

            result[(q, r)] = {
                # Core terrain
                "biome":          biome,
                "elevation_m":    round(elev_m, 1),
                "slope_deg":      round(slope_deg, 2),
                "vegetation":     vegetation.value,
                "moisture":       moisture.value,
                "is_coastal":     False,  # pass 2
                "river_edges":    river_edges,
                # Settlement
                "settlement_type":  settlement_type_str,
                "settlement_name":  settlement_name,
                "population_class": pop_class,
                "anthrome":         anthrome,
                # Infrastructure
                "road":      road_level,
                "rail":      rail_level,
                "bridge":    bridge,
                "port":      port,
                "airfield":  False,       # Sprint 2 — manual layer
                "fortification": "none",  # Sprint 2 — manual layer
                # Resources (Sprint 2)
                "oil":            False,
                "coal":           False,
                "steel":          False,
                "agriculture":    landuse_type == "farmland",
                "industry_level": 1 if landuse_type == "industrial" else 0,
                # Political (Sprint 2 — GADM)
                "country_1939":   "",
                "province":       "",
            }

        # ----------------------------------------------------------------
        # 4. Second pass — coastal flag
        # A land hex is coastal if any of its 6 neighbors is a water hex.
        # ----------------------------------------------------------------
        water_qr = {
            (q, r) for (q, r), d in result.items()
            if d["biome"] in WATER_BIOMES
        }

        for (q, r), data in result.items():
            if data["biome"] in WATER_BIOMES:
                continue
            col = q - grid._col_offset + 1
            row = r - grid._row_offset + 1
            for nbr in offset_neighbors(col, row):
                nq = nbr.col + grid._col_offset - 1
                nr = nbr.row + grid._row_offset - 1
                if (nq, nr) in water_qr:
                    data["is_coastal"] = True
                    # Upgrade beach classification
                    if (data["biome"] == Biome.PLAINS
                            and data.get("landuse_type") == "beach"):
                        data["biome"] = Biome.BEACH
                    break

        return result


# ---------------------------------------------------------------------------
# Spatial sampling helpers
# ---------------------------------------------------------------------------

def _sample_landuse(
    pt: Point,
    landuse_gdf,
    sindex,
) -> str | None:
    """Return the dominant landuse_type string for a point, or None."""
    candidates = list(sindex.intersection(pt.bounds))
    if not candidates:
        return None
    for idx in candidates:
        geom = landuse_gdf.iloc[idx].geometry
        if geom.contains(pt):
            return landuse_gdf.iloc[idx]["landuse_type"]
    return None


def _find_nearest_settlement(pt: Point, settlements_gdf, threshold_deg: float):
    """Find the nearest settlement within threshold_deg of pt."""
    best = None
    best_dist = float("inf")
    for _, row in settlements_gdf.iterrows():
        d = pt.distance(row.geometry)
        if d < threshold_deg and d < best_dist:
            best_dist = d
            best = row
    return best


def _place_to_settlement_type(place_type: str, population: int) -> str:
    """Resolve settlement type from place_type + population."""
    if place_type == "city":
        if population > 300_000:
            return "metropolis"
        return "city"
    if place_type == "town":
        return "town"
    return "village"


def _pop_class(settlement_type: str) -> int:
    return {"none": 0, "village": 1, "town": 2, "city": 3, "metropolis": 5}.get(
        settlement_type, 0
    )


def _best_road_in_hex(hex_poly, roads_gdf) -> str:
    """Return the best road level of any road intersecting this hex polygon."""
    priority = {"highway": 3, "paved": 2, "dirt": 1, "none": 0}
    best = "none"
    try:
        candidates = list(roads_gdf.sindex.intersection(hex_poly.bounds))
        for idx in candidates:
            row = roads_gdf.iloc[idx]
            if hex_poly.intersects(row.geometry):
                level = row.get("road_level", "none")
                if priority.get(level, 0) > priority.get(best, 0):
                    best = level
    except Exception:
        pass
    return best


def _best_rail_in_hex(hex_poly, railways_gdf) -> str:
    """Return the best rail level of any railway intersecting this hex polygon."""
    priority = {"double": 3, "standard": 2, "narrow": 1, "none": 0}
    best = "none"
    try:
        candidates = list(railways_gdf.sindex.intersection(hex_poly.bounds))
        for idx in candidates:
            row = railways_gdf.iloc[idx]
            if hex_poly.intersects(row.geometry):
                level = row.get("rail_level", "standard")
                if priority.get(level, 0) > priority.get(best, 0):
                    best = level
    except Exception:
        pass
    return best


def _river_edges_for_hex(q: int, r: int, grid: HexGrid, waterways_gdf) -> list[int]:
    """Return list of edge indices (0-5) where a waterway crosses the hex boundary.

    Edge 0 = NE, clockwise: NE=0, E=1, SE=2, SW=3, W=4, NW=5.
    Only edges shared with a non-water neighbor are candidates
    (rivers don't cross water-hex edges).
    """
    hex_poly = grid.hex_polygon(q, r)
    verts = grid.hex_vertices(q, r)

    # The 6 edges as LineStrings (projected CRS)
    edges = []
    for i in range(6):
        v1 = verts[i]
        v2 = verts[(i + 1) % 6]
        edges.append(LineString([v1, v2]))

def _river_edges_for_hex(q: int, r: int, grid: HexGrid, waterways_gdf) -> list[int]:
    """Return list of edge indices (0-5) where a waterway crosses the hex boundary."""
    from pyproj import Transformer
    from shapely.geometry import LineString, Polygon

    # Build hex edges in WGS84 to match waterways_gdf CRS
    to_geo = grid._to_geo
    verts_proj = grid.hex_vertices(q, r)
    verts_wgs84 = [to_geo.transform(x, y) for x, y in verts_proj]

    hex_poly_wgs84 = Polygon(verts_wgs84)
    edges = []
    for i in range(6):
        v1 = verts_wgs84[i]
        v2 = verts_wgs84[(i + 1) % 6]
        edges.append(LineString([v1, v2]))

    crossed: list[int] = []
    candidates = list(waterways_gdf.sindex.intersection(hex_poly_wgs84.bounds))

    for idx in candidates:
        wway = waterways_gdf.iloc[idx].geometry
        if wway is None or not hex_poly_wgs84.intersects(wway):
            continue
        for edge_idx, edge in enumerate(edges):
            if edge.intersects(wway) and edge_idx not in crossed:
                crossed.append(edge_idx)

    return sorted(crossed)
    crossed: list[int] = []
    candidates = list(waterways_gdf.sindex.intersection(hex_poly.bounds))

    for idx in candidates:
        wway = waterways_gdf.iloc[idx].geometry
        if wway is None:
            continue
        # Project waterway geometry to grid CRS
        try:
            if wway.geom_type == "LineString":
                proj_coords = [to_proj.transform(x, y) for x, y in wway.coords]
                proj_wway = LineString(proj_coords)
            else:
                continue
        except Exception:
            continue

        if not hex_poly.intersects(proj_wway):
            continue

        # Check which edges it crosses
        for edge_idx, edge in enumerate(edges):
            if edge.intersects(proj_wway) and edge_idx not in crossed:
                crossed.append(edge_idx)

    return sorted(crossed)


def _hex_has_bridge(center: Point, bridges_gdf, radius_m: float) -> bool:
    """True if any bridge point is within the hex radius."""
    threshold_deg = radius_m / 111320.0
    candidates = list(bridges_gdf.sindex.intersection(
        (center.x - threshold_deg, center.y - threshold_deg,
         center.x + threshold_deg, center.y + threshold_deg)
    ))
    for idx in candidates:
        if center.distance(bridges_gdf.iloc[idx].geometry) < threshold_deg:
            return True
    return False


def _hex_has_point_feature(center: Point, gdf, threshold_deg: float) -> bool:
    """True if any point feature in gdf is within threshold_deg of center."""
    candidates = list(gdf.sindex.intersection(
        (center.x - threshold_deg, center.y - threshold_deg,
         center.x + threshold_deg, center.y + threshold_deg)
    ))
    for idx in candidates:
        row = gdf.iloc[idx]
        if hasattr(row.geometry, "x"):
            if center.distance(row.geometry) < threshold_deg:
                return True
    return False


def _derive_anthrome(biome: Biome, landuse_type: str | None, settlement_type: str) -> str:
    """Derive anthrome string from biome + landuse + settlement."""
    if biome != Biome.URBAN and settlement_type == "none":
        if landuse_type == "farmland":
            return "cropland"
        if landuse_type == "military":
            return "fortified"
        if landuse_type == "quarry":
            return "mining"
        return "none"

    if landuse_type == "industrial":
        return "industrial"
    if settlement_type in ("metropolis", "city"):
        return "metro"
    if settlement_type == "town":
        return "residential"
    return "none"

def _hex_polygon_wgs84(q: int, r: int, grid: HexGrid):
    """Return hex polygon in WGS84 (EPSG:4326) for intersection with OSM data."""
    from pyproj import Transformer
    from shapely.geometry import Polygon

    verts_proj = grid.hex_vertices(q, r)
    to_geo = grid._to_geo  # projected → WGS84 transformer on the grid
    verts_wgs84 = [to_geo.transform(x, y) for x, y in verts_proj]
    # transform returns (lon, lat) — shapely wants (x, y) = (lon, lat) ✓
    return Polygon(verts_wgs84)   

def _sample_max_slope_in_hex(
    slope_grid: np.ndarray,
    transform,
    hex_poly_proj,
    grid: HexGrid,
) -> float:
    """Sample the 90th percentile slope within the hex polygon bounds.

    Converts projected hex bounds to WGS84 before indexing into the
    raster (which is in EPSG:4326 / degrees).
    """
    # Convert projected bounds to WGS84
    to_geo = grid._to_geo
    minx, miny, maxx, maxy = hex_poly_proj.bounds
    lon_min, lat_min = to_geo.transform(minx, miny)
    lon_max, lat_max = to_geo.transform(maxx, maxy)

    # Ensure correct ordering
    lon_min, lon_max = min(lon_min, lon_max), max(lon_min, lon_max)
    lat_min, lat_max = min(lat_min, lat_max), max(lat_min, lat_max)

    # Convert WGS84 bounds to raster pixel indices
    col_min, row_min = ~transform * (lon_min, lat_max)  # top-left
    col_max, row_max = ~transform * (lon_max, lat_min)  # bottom-right

    row_min = max(0, int(np.floor(row_min)))
    row_max = min(slope_grid.shape[0] - 1, int(np.ceil(row_max)))
    col_min = max(0, int(np.floor(col_min)))
    col_max = min(slope_grid.shape[1] - 1, int(np.ceil(col_max)))

    if row_min >= row_max or col_min >= col_max:
        return 0.0

    patch = slope_grid[row_min:row_max + 1, col_min:col_max + 1]
    if patch.size == 0:
        return 0.0

    return float(np.percentile(patch, 90))