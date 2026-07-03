"""Para Bellum hex data exporter.

Produces the canonical hex JSON consumed by Unity 6.6 LTS.
This is the contract between the cartography pipeline and the game engine.
Schema version: see SCHEMA_VERSION below (currently 1.0.5).

OUT OF SCOPE (not written here, implemented in Unity or later pipeline stages):
    - Tactical battle map selection logic
    - Strategic resource layer (Sprint 2 — manual GeoJSON overlay)
    - Historical WW2 boundary overrides (Sprint 2)
    - Player-placed infrastructure (Unity runtime)
    - Nation-specific tactical overlays (Unity runtime)
    - SQLite output (planned for production — JSON for Sprint 1 dev)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from wargame_cartographer.config.map_spec import MapSpec
from wargame_cartographer.hex.grid import HexGrid
from wargame_cartographer.terrain.types import (
    Biome,
    ElevationTier,
    VegetationLevel,
    MoistureLevel,
    BIOME_BASE_MOVEMENT,
    BIOME_BASE_DEFENSE,
    IMPASSABLE_BIOMES,
    WATER_BIOMES,
)
from wargame_cartographer.infrastructure.types import (
    RoadLevel,
    RailLevel,
    FortificationLevel,
    SettlementType,
    Anthrome,
)


# ---------------------------------------------------------------------------
# Schema version — bump when any field is added/removed/renamed.
# Unity C# loader checks this on load and rejects incompatible versions.
# ---------------------------------------------------------------------------
SCHEMA_VERSION = "1.0.5"


def _safe_enum_value(val, default: str) -> str:
    """Return .value for an Enum, str() for a string, or default if None."""
    if val is None:
        return default
    if hasattr(val, "value"):
        return val.value
    return str(val)


def _elevation_tier(elevation_m: float, slope_deg: float) -> ElevationTier:
    """Derive ElevationTier from raw SRTM data.

    Slope drives tier — elevation only matters for highland plateau detection.
    A flat high plain (Denver, Anatolia) is FLAT tier, not MOUNTAINOUS.
    """
    if slope_deg < 3.0:
        if elevation_m > 1500.0:
            return ElevationTier.HIGHLAND_PLATEAU
        return ElevationTier.FLAT
    elif slope_deg < 10.0:
        return ElevationTier.HILLY
    elif slope_deg < 20.0:
        return ElevationTier.MOUNTAINOUS
    else:
        return ElevationTier.RUGGED


def export_game_data(
    grid: HexGrid,
    hex_terrain: dict[tuple[int, int], dict],
    spec: MapSpec,
    output_path: Path,
) -> Path:
    """Export full Para Bellum hex JSON for all hexes in the grid.

    Args:
        grid:         HexGrid instance (upstream, provides cells + geometry)
        hex_terrain:  Dict keyed by (q, r) → per-hex data dict from pipeline
        spec:         MapSpec (YAML config)
        output_path:  Destination .json file path

    Returns:
        Resolved path to written file.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # --- Compute grid col/row bounds for metadata ---
    if grid.cells:
        qs = [k[0] for k in grid.cells]
        rs = [k[1] for k in grid.cells]
        col_min = min(qs) - grid._col_offset + 1
        col_max = max(qs) - grid._col_offset + 1
        row_min = min(rs) - grid._row_offset + 1
        row_max = max(rs) - grid._row_offset + 1
    else:
        col_min = col_max = row_min = row_max = 0

    # --- Build hex list ---
    hexes = []
    biome_counts: dict[str, int] = {}
    n_defaulted = 0  # grid cells missing from hex_terrain (AD-030)

    for (q, r), cell in grid.cells.items():
        if (q, r) not in hex_terrain:
            n_defaulted += 1
        info = hex_terrain.get((q, r), {})

        # --- Coords ---
        col = q - grid._col_offset + 1
        row = r - grid._row_offset + 1
        # v1.0.5 (AD-031): delimited id — the packed CCCRR format overflowed
        # (rows >= 100 in shipped wceurope produced ambiguous mixed-width ids
        # whose lexicographic order no longer matched (col,row)). Display /
        # debug only; consumers key on `coords`.
        hex_id = f"{col}_{row}"

        # --- Terrain ---
        # Biome: pipeline stages write info["biome"] as a Biome enum or string.
        # Fall back to mapping legacy TerrainType strings if present.
        biome_raw = info.get("biome") or info.get("terrain")
        biome = _resolve_biome(biome_raw)
        biome_str = biome.value
        biome_counts[biome_str] = biome_counts.get(biome_str, 0) + 1

        elevation_m = float(info.get("elevation_m", 0.0))
        slope_deg = float(info.get("slope_deg", 0.0))
        elev_tier = _elevation_tier(elevation_m, slope_deg)

        vegetation = _safe_enum_value(info.get("vegetation"), VegetationLevel.SPARSE.value)
        moisture = _safe_enum_value(info.get("moisture"), MoistureLevel.TEMPERATE.value)

        is_coastal = bool(info.get("is_coastal", False))
        river_edges = info.get("river_edges", [])  # list of ints 0-5 (render hint)

        # --- Rivers (v1.0.4, AD-026: hex-center node model) ---
        # has_river: does an AD-029-selected river pass through this hex.
        # river_name: primary river by longest in-hex run. river_edges stays in
        # `terrain` as a rendering direction hint only; its gameplay role is
        # superseded by has_river (AD-026).
        has_river = bool(info.get("has_river", False))
        river_name = info.get("river_name", "") or ""

        # --- Political ---
        country = info.get("country_at_start", "")
        province = info.get("province_at_start", "")
        # --- Settlement ---
        settlement_type = _safe_enum_value(
            info.get("settlement_type"), SettlementType.NONE.value
        )
        settlement_name = info.get("settlement_name", "")
        anthrome = _safe_enum_value(info.get("anthrome"), Anthrome.NONE.value)
        # Multi-hex urban sprawl (v1.0.2, AD-014). Always present for a stable
        # contract: parent_city "" and distance_from_centroid_km null unless the
        # hex belongs to a city footprint.
        parent_city = info.get("parent_city", "") or ""
        distance_from_centroid_km = info.get("distance_from_centroid_km")

        # Population class: derive from settlement type if not explicitly set
        pop_class = info.get("population_class")
        if pop_class is None:
            pop_class = _default_pop_class(settlement_type)

        # --- Infrastructure ---
        road = _safe_enum_value(info.get("road"), RoadLevel.NONE.value)
        rail = _safe_enum_value(info.get("rail"), RailLevel.NONE.value)
        bridge = bool(info.get("bridge", False))
        port = bool(info.get("port", False))
        airfield = bool(info.get("airfield", False))
        fortification = _safe_enum_value(
            info.get("fortification"), FortificationLevel.NONE.value
        )

        # --- Resources (hand-authored 1930 layer, F-2; iron new in v1.0.2) ---
        oil = bool(info.get("oil", False))
        coal = bool(info.get("coal", False))
        steel = bool(info.get("steel", False))
        iron = bool(info.get("iron", False))
        agriculture = bool(info.get("agriculture", False))
        industry_level = int(info.get("industry_level", 0))

        # --- Derived flags ---
        is_water = biome in WATER_BIOMES
        is_impassable = biome in IMPASSABLE_BIOMES

        # --- Administrative tier (v1.0.3; capital/sub_capital from AD-023 in
        # Sprint 5) --- The province reconcile pass sets admin_tier directly on
        # the hex when provinces are loaded; otherwise fall back to the
        # population-derived default.
        admin_tier = info.get("admin_tier")
        if admin_tier is None:
            admin_tier = _admin_tier(is_water, country, settlement_type)

        # Base movement cost from biome (Unity applies modifier stack at runtime)
        base_movement = BIOME_BASE_MOVEMENT.get(biome, 1)
        base_defense = BIOME_BASE_DEFENSE.get(biome, 0)

        # --- Assemble hex object ---
        hex_obj = {
            "id": hex_id,
            "coords": {
                "col": col,
                "row": row,
            },
            "geo": {
                "center_lat": round(cell.center_lat, 6),
                "center_lon": round(cell.center_lon, 6),
                "elevation_m": round(elevation_m, 1),
                "slope_deg": round(slope_deg, 2),
            },
            "terrain": {
                "biome": biome_str,
                "elevation_tier": elev_tier.value,
                "vegetation": vegetation,
                "moisture": moisture,
                "is_coastal": is_coastal,
                "river_edges": river_edges,
            },
            "rivers": {
                "has_river": has_river,
                "river_name": river_name,
            },
            "political": {
                "country_at_start": country,
                "province_at_start": province,
            },
            "settlement": {
                "type": settlement_type,
                "name": settlement_name,
                "population_class": pop_class,
                "anthrome": anthrome,
                "parent_city": parent_city,
                "distance_from_centroid_km": distance_from_centroid_km,
                "admin_tier": admin_tier,
            },
            "infrastructure": {
                "road": road,
                "rail": rail,
                "bridge": bridge,
                "port": port,
                "airfield": airfield,
                "fortification": fortification,
            },
            "resources": {
                "oil": oil,
                "coal": coal,
                "steel": steel,
                "iron": iron,
                "agriculture": agriculture,
                "industry_level": industry_level,
            },
            "movement": {
                "base_cost": base_movement,
                "base_defense": base_defense,
            },
            "flags": {
                "is_water": is_water,
                "is_impassable": is_impassable,
                "is_coastal": is_coastal,
            },
        }

        hexes.append(hex_obj)

    # A grid cell absent from hex_terrain is silently exported as a default
    # plains hex — a completeness hole. Fail loud (AD-030).
    if n_defaulted:
        raise RuntimeError(
            f"export_game_data: {n_defaulted} of {len(grid.cells)} grid cells "
            f"were missing from hex_terrain and would default to plains hexes "
            f"(AD-030). Aborting rather than shipping filler terrain."
        )

    # v1.0.5 (AD-031): sort numerically by (col, row) — deterministic and
    # monotone in coords (the old string sort broke once id widths mixed).
    hexes.sort(key=lambda h: (h["coords"]["col"], h["coords"]["row"]))

    # --- Top-level document ---
    document = {
        "schema_version": SCHEMA_VERSION,
        "map_metadata": {
            "name": spec.name,
            "title": getattr(spec, "title", spec.name),
            "scenario_date": "1930-01-01",
            "hex_size_km": spec.hex_size_km,
            "hex_size_miles": round(spec.hex_size_km * 0.621371, 2),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "0.1.0",
            "data_sources": {
                "terrain": "OpenStreetMap (ODbL)",
                "elevation": "SRTM 1-arcsec (NASA, public domain)",
                "boundaries": ("1930 borders: OpenHistoricalMap (CC0) east + "
                               "Natural Earth (public domain) west, AD-035"),
                "provinces": ("1930 provinces: OpenHistoricalMap admin_level=4 "
                              "(CC0) for DEU/POL/CSK/AUT + Natural Earth "
                              "admin_1 derived for BEL/NLD/FRA/LUX (AD-027/035)"),
                "resources": "hand-authored 1930 layer (public domain, F-2)",
            },
            "bounds": {
                "min_lon": spec.bbox.min_lon,
                "min_lat": spec.bbox.min_lat,
                "max_lon": spec.bbox.max_lon,
                "max_lat": spec.bbox.max_lat,
            },
            "grid": {
                "orientation": "flat_top",
                # AD-012: flat-top odd-q offset layout (odd columns shifted).
                # The previous "odd_row_east" label was incorrect.
                "offset": "odd_q",
                "col_min": col_min,
                "col_max": col_max,
                "row_min": row_min,
                "row_max": row_max,
                "num_cols": col_max - col_min + 1,
                "num_rows": row_max - row_min + 1,
            },
            "hex_count": len(hexes),
            "biome_distribution": dict(sorted(biome_counts.items())),
        },
        "hexes": hexes,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(document, f, indent=2, ensure_ascii=False)

    return output_path.resolve()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_biome(raw) -> Biome:
    """Resolve a biome from pipeline data.

    Accepts: Biome enum, biome string value, or legacy TerrainType string.
    Falls back to PLAINS for unknown values (logged separately by classifier).
    """
    if raw is None:
        return Biome.PLAINS

    if isinstance(raw, Biome):
        return raw

    # Try direct biome string match
    val = raw.value if hasattr(raw, "value") else str(raw)

    for b in Biome:
        if b.value == val:
            return b

    # Legacy TerrainType → Biome mapping (upstream classifier output)
    _LEGACY_MAP = {
        "clear":    Biome.PLAINS,
        "rough":    Biome.HILL,
        "forest":   Biome.FOREST,
        "mountain": Biome.MOUNTAIN,
        "marsh":    Biome.MARSH,
        "desert":   Biome.DESERT,
        "urban":    Biome.URBAN,
        "water":    Biome.WATER,
    }
    return _LEGACY_MAP.get(val, Biome.PLAINS)


def _admin_tier(is_water: bool, country: str, settlement_type: str) -> str:
    """Administrative tier default (schema v1.0.3).

    capital / sub_capital are reserved for a future political layer and are
    never assigned by the pipeline yet. Defaults:
      water or no-country hex      -> "none"
      currently-settled land hex   -> "urban"
      unsettled non-water land hex -> "rural"
    """
    if is_water or not country:
        return "none"
    if settlement_type and settlement_type != "none":
        return "urban"
    return "rural"


def _default_pop_class(settlement_type_str: str) -> int:
    """Derive population class integer from settlement type string."""
    return {
        "none":       0,
        "village":    1,
        "town":       2,
        "city":       3,
        "metropolis": 5,
    }.get(settlement_type_str, 0)