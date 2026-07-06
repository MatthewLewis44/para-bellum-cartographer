"""Para Bellum hex sampler.

Samples all geographic data layers per hex and produces the full
per-hex data dict consumed by game_data_exporter.py.

Each hex goes through these per-hex stages in order:
    1. Water detection (Natural Earth land polygon)
    2. Elevation + slope sampling (SRTM)
    3. Landuse classification (OSM landuse/natural areas)
    4. Settlement assignment (OSM place nodes)
    5. Biome classification (BiomeClassifier)
    6. Vegetation + moisture derivation
    7. Road/rail level (OSM highways + railways)
    8. Rivers (AD-026 node model): has_river/river_name from the AD-029 selected
       set (Natural Earth scalerank rivers + OSM canals); river_edges kept as a
       render-direction hint
    9. Bridge detection (OSM bridge=yes)
   10. Country + province at start (1930 boundaries / provinces, AD-018/023/027)
   11. Strategic resources (hand-authored 1930 layer, F-2)

Global reconcile passes (over the whole grid, after per-hex sampling):
    - Coastal flag (land hexes adjacent to a water hex)
    - Multi-hex urban sprawl (AD-014)
    - admin_tier capital / sub_capital (AD-023/027)

OUT OF SCOPE (NOT sampled here — AD-036): port / airfield / fortification are
    starting-infrastructure fields filled from AUTHORED construction-system
    scenario data (like resources_1930.geojson), NOT detected from OSM/OHM.
    They stay inert (False/False/"none"). Player-placed buildings are Unity
    runtime state.
"""

from __future__ import annotations

import math

import numpy as np
from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.prepared import prep

from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.geo.boundaries import assign_country
from wargame_cartographer.geo.provinces import assign_province, assign_admin_tiers
from wargame_cartographer.geo.elevation import ElevationProcessor, SRTM_VOID
from wargame_cartographer.hex.grid import HexGrid
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
        boundaries_gdf=None,   # 1930 political boundaries (geo/boundaries.py)
        resources_gdf=None,    # 1930 strategic resources (geo/resources.py)
        provinces=None,        # 1930 ProvinceSet (geo/provinces.py, AD-023/027)
        *,
        hex_keys=None,           # iterable of (q,r) to sample; None = all cells
        precomputed=None,        # dict of reusable global tables (streaming mode)
        run_global_passes=True,  # run pass-2 coastal + pass-3 sprawl in-place
        allow_synthetic_elevation=False,  # AD-030 fail-loud default (was True)
    ) -> dict[tuple[int, int], dict]:
        """Build per-hex data for the grid (or a hex subset, for tiling).

        Returns dict keyed by (q, r) → hex data dict matching the Para Bellum
        JSON schema fields.

        Streaming/tiled mode (T2): pass ``hex_keys`` = the tile's hexes,
        ``precomputed`` = global tables computed once (settlement_by_hex,
        resource_by_hex, land_prep, lake_prep) so a settlement/boundary just
        outside the tile cannot change a hex inside it, and
        ``run_global_passes=False`` to defer coastal + sprawl to the global
        merge. The per-hex pass-1 body is identical to the monolithic path, so
        output matches hex-for-hex (the streaming orchestrator feeds it
        tile-sliced ``osm_data`` and per-tile elevation via ``bbox``).
        """
        precomputed = precomputed or {}

        # ----------------------------------------------------------------
        # 1. Load elevation + slope
        # ----------------------------------------------------------------
        if "elevation" in precomputed:
            # Streaming passes a windowed read of the full-bbox DEM so per-tile
            # elevation/slope is byte-identical to the monolithic raster.
            elevation, elev_metadata = precomputed["elevation"]
        else:
            elevation, elev_metadata = self.elevation_proc.get_elevation(
                bbox, allow_synthetic=allow_synthetic_elevation
            )
        slope_grid = self.elevation_proc.compute_slope(
            elevation, elev_metadata["transform"]
        )

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

        # (Ports are NOT sampled — AD-036. vector_data.ports is empty.)

        # Streaming mode reuses globally-prepped geometry so a feature just
        # outside a tile cannot change a hex inside it (and avoids re-prepping
        # the continent-scale Natural Earth land union per tile).
        if "land_prep" in precomputed:
            land_prep = precomputed["land_prep"]
        if "lake_prep" in precomputed:
            lake_prep = precomputed["lake_prep"]

        # Assign each settlement node to its containing hex once (O(settlements)),
        # keeping only the most significant settlement per hex. Replaces the old
        # per-hex nearest-node scan, which was O(hexes × settlements) and let
        # village nodes outcompete city nodes for the same hex. Settlement and
        # resource assignment are GLOBAL (cheap) — reuse the precomputed tables
        # in streaming mode rather than recomputing per tile.
        if "settlement_by_hex" in precomputed:
            settlement_by_hex = precomputed["settlement_by_hex"]
        else:
            settlement_by_hex = {}
            if settlements_gdf is not None:
                settlement_by_hex = _assign_settlements_to_hexes(grid, settlements_gdf)

        # 1930 political boundaries — prep geometry + spatial index once,
        # not per-hex (assign_country caches prepared polygons internally).
        has_boundaries = boundaries_gdf is not None and not boundaries_gdf.empty
        if has_boundaries:
            boundaries_gdf.sindex  # build index up front
            assign_country(0.0, 0.0, boundaries_gdf)  # warm prepared-geometry cache

        # 1930 strategic resources — assign each basin/works to its hexes once.
        if "resource_by_hex" in precomputed:
            resource_by_hex = precomputed["resource_by_hex"]
        else:
            resource_by_hex = {}
            if resources_gdf is not None and not resources_gdf.empty:
                resource_by_hex = _assign_resources_to_hexes(grid, resources_gdf)

        # ----------------------------------------------------------------
        # 3. First pass — classify each hex (all cells, or the tile subset)
        # ----------------------------------------------------------------
        result: dict[tuple[int, int], dict] = {}

        keys = grid.cells.keys() if hex_keys is None else hex_keys
        for (q, r) in keys:
            cell = grid.cells[(q, r)]
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
            if elev_m == SRTM_VOID:
                # Void at the hex center: no data -> 0.0, same convention as
                # an out-of-raster sample. The elevation-plausibility gate
                # reports improbable values; never ship the sentinel.
                elev_m = 0.0
            # v1.0.5 (AD-032): below-sea-level land keeps its true signed
            # elevation (Dutch polders, Hambach pit) — the old max(0, elev)
            # clamp discarded data the era's inundation gameplay may need.

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

            if not is_water and not is_lake:
                match = settlement_by_hex.get((q, r))
                if match is not None:
                    has_settlement = True
                    settlement_place_type = match["place_type"]
                    settlement_name = match["name"]
                    settlement_type_str = match["settlement_type"]
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

            # -- Road / rail level --
            # WGS84 hex polygon (matches roads/rails CRS, EPSG:4326), computed
            # once per hex. (Was guarded by `'hex_poly_wgs84' not in dir()`,
            # which leaked the previous hex's polygon across iterations when a
            # hex had rails but no roads — harmless while both layers are
            # always present together, but a landmine the tiling would expose.)
            hex_poly_wgs84 = None
            road_level = "none"
            if not is_water and not is_lake and roads_gdf is not None:
                hex_poly_wgs84 = _hex_polygon_wgs84(q, r, grid)
                road_level = _best_road_in_hex(hex_poly_wgs84, roads_gdf)

            rail_level = "none"
            if not is_water and not is_lake and railways_gdf is not None:
                if hex_poly_wgs84 is None:
                    hex_poly_wgs84 = _hex_polygon_wgs84(q, r, grid)
                rail_level = _best_rail_in_hex(hex_poly_wgs84, railways_gdf)

            # -- Rivers (AD-026): hex-center node model + render-hint edges --
            river_edges: list[int] = []
            has_river = False
            river_name = ""
            if waterways_gdf is not None:
                river_edges, has_river, river_name = _river_for_hex(
                    q, r, grid, waterways_gdf
                )

            # -- Bridge --
            bridge = False
            if river_edges and bridges_gdf is not None:
                bridge = _feature_within_radius(pt, bridges_gdf, grid.hex_radius_m)

            # -- Anthrome --
            anthrome = _derive_anthrome(biome, landuse_type, settlement_type_str)

            # -- Country at start (1930 boundaries) --
            country_code = ""
            if has_boundaries and not is_water and not is_lake:
                country_code = assign_country(
                    cell.center_lon, cell.center_lat, boundaries_gdf
                )

            # -- Province at start (1930 provinces, AD-023/027) --
            # Tile-local PIP against the GLOBAL province polygons (same pattern
            # as country_at_start), RESTRICTED to the hex's country so the
            # 0.2° snap can never cross a 1930 border (Sprint 6 review fix).
            # admin_tier (capital/sub_capital) is deferred to the global
            # reconcile pass (needs all settlement nodes).
            province_id = ""
            if provinces is not None and not is_water and not is_lake:
                province_id = assign_province(
                    cell.center_lon, cell.center_lat, provinces,
                    country=country_code,
                ) or ""

            result[(q, r)] = {
                # Core terrain
                "biome":          biome,
                "elevation_m":    round(elev_m, 1),
                "slope_deg":      round(slope_deg, 2),
                "vegetation":     vegetation.value,
                "moisture":       moisture.value,
                "is_coastal":     False,  # pass 2
                "river_edges":    river_edges,
                "has_river":      has_river,
                "river_name":     river_name,
                # Settlement
                "settlement_type":  settlement_type_str,
                "settlement_name":  settlement_name,
                "population_class": pop_class,
                "anthrome":         anthrome,
                "parent_city":      "",     # filled by urban-sprawl pass (AD-014)
                "distance_from_centroid_km": None,
                # Internal: dominant landuse at hex center (used by the coastal
                # beach upgrade and the urban-sprawl gate; exporter ignores it).
                "landuse_type":     landuse_type,
                # Infrastructure. port / airfield / fortification are NOT
                # detected by the pipeline (AD-036): starting infrastructure is
                # authored construction-system scenario data (like resources),
                # not something to sniff from modern OSM/OHM. They stay inert
                # here and are filled from authored data when that system
                # exists. Do NOT re-add detection. bridge/road/rail are terrain
                # facts and ARE sampled.
                "road":      road_level,
                "rail":      rail_level,
                "bridge":    bridge,
                "port":      False,        # AD-036 — authored, not detected
                "airfield":  False,        # AD-036 — authored, not detected
                "fortification": "none",   # AD-036 — authored, not detected
                # Resources — booleans from the hand-authored 1930 layer (F-2);
                # agriculture/industry derived from OSM landuse.
                "oil":            "oil" in resource_by_hex.get((q, r), ()),
                "coal":           "coal" in resource_by_hex.get((q, r), ()),
                "steel":          "steel" in resource_by_hex.get((q, r), ()),
                "iron":           "iron" in resource_by_hex.get((q, r), ()),
                "agriculture":    landuse_type == "farmland",
                "industry_level": 1 if landuse_type == "industrial" else 0,
                # Political (1930 boundaries + provinces, AD-023/027)
                "country_at_start":  country_code,
                "province_at_start": province_id,
            }

        # ----------------------------------------------------------------
        # Global passes — coastal flag (pass 2) and urban sprawl (pass 3) both
        # need cross-hex (and in streaming, cross-tile) knowledge. The streaming
        # orchestrator skips them here and runs them once over the merged grid.
        # ----------------------------------------------------------------
        if run_global_passes:
            _assign_coastal(result, grid)
            _assign_urban_sprawl(result, grid, settlement_by_hex)
            # admin_tier (capital/sub_capital) needs the whole grid + all
            # settlement nodes — a global reconcile, like coastal/sprawl.
            if provinces is not None:
                assign_admin_tiers(result, grid, provinces, settlements_gdf)

        return result


# ---------------------------------------------------------------------------
# Global reconcile passes (cross-hex; in streaming, run once over merged grid)
# ---------------------------------------------------------------------------

def _assign_coastal(result, grid) -> None:
    """Pass 2: flag land hexes adjacent to a water hex, and upgrade coastal
    sandy plains to BEACH. Cross-tile in streaming — run over the merged grid.

    Mutates ``result`` in place: sets ``is_coastal`` and may change ``biome``.
    Identical logic to the original inline pass-2.
    """
    water_qr = {
        (q, r) for (q, r), d in result.items()
        if d["biome"] in WATER_BIOMES
    }

    for (q, r), data in result.items():
        if data["biome"] in WATER_BIOMES:
            continue
        # grid.neighbors is THE neighbor implementation (Sprint 6 fix 1) —
        # the old coords.offset_neighbors was parity-inconsistent with the
        # grid layout on odd-q_min bboxes, so is_coastal was computed against
        # the wrong neighbours on Belgium/wceurope-parity grids.
        for (nq, nr) in grid.neighbors(q, r):
            if (nq, nr) in water_qr:
                data["is_coastal"] = True
                # Upgrade beach classification
                if (data["biome"] == Biome.PLAINS
                        and data.get("landuse_type") == "beach"):
                    data["biome"] = Biome.BEACH
                break


# ---------------------------------------------------------------------------
# Spatial sampling helpers
# ---------------------------------------------------------------------------

def _sample_landuse(
    pt: Point,
    landuse_gdf,
    sindex,
) -> str | None:
    """Return the dominant landuse_type at a point, or None.

    When the point falls inside several overlapping landuse polygons, the
    **smallest-area** polygon wins (most-specific land use, e.g. an industrial
    parcel inside a residential district), with the landuse_type string as a
    deterministic final tie-break. This makes the result independent of feature
    order in the GeoDataFrame — required so a tiled (bbox-sliced) read yields
    the same answer as the monolithic full read (Sprint 4 streaming, AD-024).
    """
    candidates = list(sindex.intersection(pt.bounds))
    if not candidates:
        return None
    best_type = None
    best_key = None
    for idx in candidates:
        row = landuse_gdf.iloc[idx]
        geom = row.geometry
        if geom is not None and geom.contains(pt):
            key = (geom.area, str(row["landuse_type"]))
            if best_key is None or key < best_key:
                best_key = key
                best_type = row["landuse_type"]
    return best_type


# Settlement significance floors at the 10 km hex scale. A hex only gets a
# settlement tag for strategically relevant centers; smaller places remain
# visible through landuse/anthrome instead. Population bands follow
# infrastructure.types.SettlementType.
_METROPOLIS_MIN_POP = 300_000   # > this → metropolis
_CITY_MIN_POP = 50_000          # ≥ this → city
_TOWN_MIN_POP_BAND = 2_000      # ≥ this → town (band lower bound)
_TOWN_TAG_MIN_POP = 20_000      # towns below this don't tag a hex

_PLACE_TIER = {"city": 3, "town": 2, "village": 1}
_TYPE_TIER = {"metropolis": 5, "city": 4, "town": 3, "village": 2, "none": 0}


def _assign_settlements_to_hexes(grid: HexGrid, settlements_gdf) -> dict[tuple[int, int], dict]:
    """Assign each settlement node to the hex containing it; best wins per hex.

    Returns dict keyed by (q, r) → {place_type, name, population, settlement_type}.

    - Containing hex is found by inverting the flat-top grid layout (the
      nearest hex center is the containing hex in a proper tessellation),
      so this is O(settlements), independent of hex count.
    - Per hex, the most significant settlement wins: higher resolved type
      tier first, then larger population.
    - Significance floor: cities/metropolises always tag; towns only with
      population ≥ _TOWN_TAG_MIN_POP; villages never tag at this hex scale.
    """
    xs, ys = grid._to_proj.transform(
        settlements_gdf.geometry.x.values, settlements_gdf.geometry.y.values
    )

    best: dict[tuple[int, int], dict] = {}
    for x, y, name, place_type, population in zip(
        xs, ys,
        settlements_gdf["name"].values,
        settlements_gdf["place_type"].values,
        settlements_gdf["population"].values,
    ):
        pop = int(population) if population == population else 0  # NaN guard
        stype = _place_to_settlement_type(str(place_type), pop)

        # Significance floor
        if stype == "village":
            continue
        if stype == "town" and pop < _TOWN_TAG_MIN_POP:
            continue

        qr = _point_to_hex(grid, x, y)
        if qr is None:
            continue

        cur = best.get(qr)
        if cur is None or (_TYPE_TIER[stype], pop) > (_TYPE_TIER[cur["settlement_type"]], cur["population"]):
            best[qr] = {
                "place_type": str(place_type),
                "name": str(name),
                "population": pop,
                "settlement_type": stype,
            }
    return best


def _point_to_hex(grid: HexGrid, x: float, y: float) -> tuple[int, int] | None:
    """Return the (q, r) of the hex containing projected point (x, y), or None.

    In a flat-top tessellation the containing hex is the one with the nearest
    center, so we check the ≤9 candidate cells around the approximate axial
    coordinates instead of searching the whole grid.
    """
    r_m = grid.hex_radius_m
    col_sp = 1.5 * r_m
    row_sp = math.sqrt(3) * r_m

    best_qr = None
    best_d = float("inf")
    q0 = int(math.floor(x / col_sp))
    for q in (q0 - 1, q0, q0 + 1):
        yy = y - (row_sp / 2.0 if q % 2 != 0 else 0.0)
        r0 = int(math.floor(yy / row_sp))
        for rr in (r0, r0 + 1):
            cell = grid.cells.get((q, rr))
            if cell is None:
                continue
            d = math.hypot(cell.center_x - x, cell.center_y - y)
            if d < best_d:
                best_d = d
                best_qr = (q, rr)
    # A point inside a hex is within hex_radius of its center.
    return best_qr if best_d <= r_m else None


def _place_to_settlement_type(place_type: str, population: int) -> str:
    """Resolve settlement type from population bands (see SettlementType).

    Known population decides the type outright — OSM place tags are noisy
    (e.g. 79k-inhabitant 'town' nodes). Unknown population (0) falls back
    to the OSM place tag.
    """
    if population > 0:
        if population > _METROPOLIS_MIN_POP:
            return "metropolis"
        if population >= _CITY_MIN_POP:
            return "city"
        if population >= _TOWN_MIN_POP_BAND:
            return "town"
        return "village"
    if place_type in ("city", "town"):
        return place_type
    return "village"


def _pop_class(settlement_type: str) -> int:
    return {"none": 0, "village": 1, "town": 2, "city": 3, "metropolis": 5}.get(
        settlement_type, 0
    )


# ---------------------------------------------------------------------------
# Multi-hex urban sprawl (AD-014)
# ---------------------------------------------------------------------------
# Population-scaled footprint radii (km from the city centroid hex). Chosen so
# that, gated by contiguous urban landuse, metropolises and large cities span
# several hexes while small cities stay compact. Tunable.
_SPRAWL_RADIUS_KM = {
    "metropolis": 14.0,   # >300k
    "city_large": 11.0,   # city node, population >= 150k
    "city":        8.0,   # city node, population < 150k or unknown
}
# Distance bands for anthrome / population_class within a footprint.
_METRO_CORE_KM = 3.0      # <this from centroid -> metro anthrome
_INNER_RING_KM = 6.0      # <this -> population_class 3, else 2
# Within this distance of a city centroid, OPEN developable land (plains/
# steppe) counts as peri-urban "outskirts" even without built-up landuse.
# This captures a major city's immediate fringe — whose 10 km hex centers
# often fall on green belt / farmland between built-up patches — without
# absorbing real terrain obstacles (forest, water, wetland, hills).
_OPEN_FRINGE_KM = 11.0
_OPEN_FRINGE_BIOMES = (Biome.PLAINS, Biome.STEPPE)


def _is_built_up(data: dict) -> bool:
    """True if a hex is urban built-up land (urban biome or res/industrial landuse)."""
    if data["biome"] == Biome.URBAN:
        return True
    return data.get("landuse_type") in ("residential", "industrial")


def _sprawl_radius_km(settlement_type: str, population: int) -> float:
    if settlement_type == "metropolis":
        return _SPRAWL_RADIUS_KM["metropolis"]
    if settlement_type == "city":
        return _SPRAWL_RADIUS_KM["city_large"] if population >= 150_000 \
            else _SPRAWL_RADIUS_KM["city"]
    return 0.0  # towns/villages do not sprawl


def _assign_urban_sprawl(result, grid, settlement_by_hex) -> None:
    """Tag multi-hex urban footprints around city/metropolis nodes (AD-014).

    For each city/metropolis seed hex, BFS outward through CONTIGUOUS urban
    hexes within a population-scaled radius. Each footprint hex is claimed by
    its nearest seed; ring hexes become `suburb` with the parent city name and
    a distance-and-landuse-derived anthrome. The centroid hex keeps its own
    city/metropolis type. Reuses the settlement + landuse data already sampled
    — no new OSM fetch. O(seeds × footprint), scales to the 100k-hex target.
    """
    seeds = [(qr, settlement_by_hex.get(qr, {}).get("population", 0) or 0)
             for qr, d in result.items()
             if d["settlement_type"] in ("city", "metropolis")]
    if not seeds:
        return

    # claim: (q,r) -> {seed, dist_km, name, seed_type}
    claims: dict[tuple[int, int], dict] = {}

    for seed_qr, pop in seeds:
        seed_type = result[seed_qr]["settlement_type"]
        radius_km = _sprawl_radius_km(seed_type, pop)
        if radius_km <= 0.0:
            continue
        seed_name = result[seed_qr]["settlement_name"]

        # Contiguous BFS gated by (distance <= radius) and (built-up land).
        visited = {seed_qr}
        frontier = [seed_qr]
        while frontier:
            cur = frontier.pop()
            sq, sr = seed_qr
            cq, cr = cur
            dist_km = grid.distance(sq, sr, cq, cr) / 1000.0
            in_footprint = (cur == seed_qr) or (
                dist_km <= radius_km and (
                    _is_built_up(result[cur])
                    or (dist_km <= _OPEN_FRINGE_KM
                        and result[cur]["biome"] in _OPEN_FRINGE_BIOMES)
                )
            )
            if not in_footprint:
                continue
            prev = claims.get(cur)
            if prev is None or dist_km < prev["dist_km"]:
                claims[cur] = {"seed": seed_qr, "dist_km": dist_km,
                               "name": seed_name, "seed_type": seed_type}
            for nb in grid.neighbors(cq, cr):
                if nb not in visited and nb in result:
                    visited.add(nb)
                    frontier.append(nb)

    # Apply claims to the per-hex data.
    for qr, claim in claims.items():
        data = result[qr]
        dist_km = claim["dist_km"]
        is_centroid = (claim["seed"] == qr)
        lu = data.get("landuse_type")

        # Anthrome per the AD-014 distance + dominant-landuse table.
        # Industrial landuse wins at ANY distance: a port/factory district at
        # the city core (Antwerp, Duisburg, Rotterdam) is tactically an
        # industrial map (AD-015), not a city-centre map — so the industrial
        # test precedes the <3 km metro test. Non-industrial cores read metro.
        if lu == "industrial":
            data["anthrome"] = "industrial"
        elif dist_km < _METRO_CORE_KM:
            data["anthrome"] = "metro"
        elif lu == "residential":
            data["anthrome"] = "residential"
        else:
            data["anthrome"] = "outskirts"

        data["parent_city"] = claim["name"]
        data["distance_from_centroid_km"] = round(dist_km, 1)

        if not is_centroid and data["settlement_type"] not in ("city", "metropolis"):
            # Ring hex of the conurbation: absorb as a suburb of the city.
            data["settlement_type"] = "suburb"
            data["population_class"] = 3 if dist_km < _INNER_RING_KM else 2


def _assign_resources_to_hexes(grid, resources_gdf) -> dict[tuple[int, int], set]:
    """Assign each 1930 resource feature to the hex(es) it covers (F-2).

    Polygons (coal/iron basins) tag every hex whose center falls inside;
    points (steel/iron works) tag the single hex containing the point.
    Returns (q, r) -> set of resource_type strings. Resources are sparse
    (a handful of features) so the polygon test is O(hexes × polygons).
    """
    out: dict[tuple[int, int], set] = {}

    polygons = []  # (prepared_geom, rtype)
    points = []    # (lon, lat, rtype)
    for _, row in resources_gdf.iterrows():
        geom = row.geometry
        rtype = str(row.get("resource_type", "")).lower()
        if geom is None or rtype not in ("coal", "steel", "iron", "oil"):
            continue
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            polygons.append((prep(geom), rtype))
        elif geom.geom_type == "Point":
            points.append((geom.x, geom.y, rtype))

    # Points (works) -> containing hex
    for lon, lat, rtype in points:
        x, y = grid._to_proj.transform(lon, lat)
        qr = _point_to_hex(grid, x, y)
        if qr is not None:
            out.setdefault(qr, set()).add(rtype)

    # Polygons (basins) -> hexes with center inside
    if polygons:
        for (q, r), cell in grid.cells.items():
            pt = Point(cell.center_lon, cell.center_lat)
            for prepared, rtype in polygons:
                if prepared.covers(pt):
                    out.setdefault((q, r), set()).add(rtype)
    return out


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


def _river_for_hex(
    q: int, r: int, grid: HexGrid, waterways_gdf
) -> tuple[list[int], bool, str]:
    """River data for a hex against the AD-029 selected-river set.

    Returns ``(river_edges, has_river, river_name)``:
      river_edges — edge indices 0-5 the river crosses. Edge i runs between hex
                    vertex i and i+1 (vertices at 60·i° from East), so the order
                    is COUNTERCLOCKWISE: 0=NE, 1=N, 2=NW, 3=SW, 4=S, 5=SE. A
                    RENDERING DIRECTION HINT ONLY (AD-026): which neighbours to
                    draw the river spline toward.
      has_river   — True if any selected river/canal geometry intersects the
                    hex polygon — the hex-center node model (AD-026).
      river_name  — name of the primary river: the one with the longest run
                    INSIDE this hex, so a trunk beats a tributary clipping a
                    corner at a confluence. "" when has_river is False.

    ``waterways_gdf`` is the WHOLE AD-029 selected set (Natural Earth scalerank
    rivers + OSM major canals — bounded). It is passed identically to the
    monolithic pass and to every streaming tile (AD-025), so has_river /
    river_name are seam-identical and automatically consistent with river_edges
    (no separate global pass needed: the set that makes river_edges global is
    the same set that makes has_river global).
    """
    from shapely.affinity import scale
    from shapely.geometry import LineString, Polygon

    to_geo = grid._to_geo
    verts_wgs84 = [to_geo.transform(x, y) for x, y in grid.hex_vertices(q, r)]
    hex_poly = Polygon(verts_wgs84)
    coslat = math.cos(math.radians(hex_poly.centroid.y))
    edges = [LineString([verts_wgs84[i], verts_wgs84[(i + 1) % 6]])
             for i in range(6)]

    geoms = waterways_gdf.geometry.values
    names = waterways_gdf["name"].values if "name" in waterways_gdf.columns else None

    crossed: list[int] = []
    has_river = False
    best_name = ""
    best_run = -1.0
    for idx in waterways_gdf.sindex.intersection(hex_poly.bounds):
        wway = geoms[idx]
        if wway is None or not hex_poly.intersects(wway):
            continue
        has_river = True
        # Primary-river pick: longest run inside the hex. Raw degree-length
        # under-weights E-W vs N-S by cos(lat) (~1.6x at 51 N), which could
        # mislabel a trunk vs tributary at a confluence — scale lon by cos(lat)
        # so the comparison is metric-proportional. Ties break alphabetically.
        inter = hex_poly.intersection(wway)
        run = scale(inter, xfact=coslat, yfact=1.0, origin=(0, 0)).length \
            if not inter.is_empty else 0.0
        nm = str(names[idx]) if names is not None and names[idx] is not None else ""
        if run > best_run or (run == best_run and nm and (not best_name or nm < best_name)):
            best_run, best_name = run, nm
        for edge_idx, edge in enumerate(edges):
            if edge_idx not in crossed and edge.intersects(wway):
                crossed.append(edge_idx)

    return sorted(crossed), has_river, (best_name if has_river else "")


def _feature_within_radius(center: Point, gdf, radius_m: float) -> bool:
    """True if any feature in ``gdf`` lies within ``radius_m`` of ``center``.

    Used for BRIDGE detection (the only caller since AD-036 retired port
    detection). Sprint 6 fix 3: distances are compared in cos(lat)-scaled
    degree space (the old threshold divided radius_m by 111320 and compared
    raw WGS84 degrees — isotropic in degrees, anisotropic in metres, ~0.63x
    the intended E-W reach at 51°N), the same correction ``_river_for_hex``
    applies to river run lengths.
    """
    from shapely.affinity import scale

    lat_deg = radius_m / 111320.0
    coslat = math.cos(math.radians(center.y))
    lon_deg = lat_deg / max(coslat, 0.01)
    candidates = list(gdf.sindex.intersection(
        (center.x - lon_deg, center.y - lat_deg,
         center.x + lon_deg, center.y + lat_deg)
    ))
    if not candidates:
        return False
    center_scaled = Point(center.x * coslat, center.y)
    for idx in candidates:
        geom = gdf.iloc[idx].geometry
        if geom is None:
            continue
        geom_scaled = scale(geom, xfact=coslat, yfact=1.0, origin=(0, 0))
        if center_scaled.distance(geom_scaled) < lat_deg:
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

    # nan-aware: void pixels and the raster-edge stencil band are NaN
    # (compute_slope). An all-NaN patch (fully void hex) reads as 0.0 and is
    # left to the elevation-plausibility gate to flag.
    p90 = np.nanpercentile(patch, 90)
    return float(p90) if np.isfinite(p90) else 0.0