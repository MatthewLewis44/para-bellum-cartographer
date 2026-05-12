"""Para Bellum hex data exporter.

Produces the canonical hex JSON consumed by Unity 6.6 LTS.
This is the contract between the cartography pipeline and the game engine.
Schema version: 1.0.0

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
SCHEMA_VERSION = "1.0.0"


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

    for (q, r), cell in grid.cells.items():
        info = hex_terrain.get((q, r), {})

        # --- Coords ---
        col = q - grid._col_offset + 1
        row = r - grid._row_offset + 1
        hex_id = f"{col:03d}{row:02d}"

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
        river_edges = info.get("river_edges", [])  # list of ints 0-5

        # --- Political ---
        country = info.get("country_1939", "")
        province = info.get("province", "")

        # --- Settlement ---
        settlement_type = _safe_enum_value(
            info.get("settlement_type"), SettlementType.NONE.value
        )
        settlement_name = info.get("settlement_name", "")
        anthrome = _safe_enum_value(info.get("anthrome"), Anthrome.NONE.value)

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

        # --- Resources (Sprint 2 — manual overlay, default false for now) ---
        oil = bool(info.get("oil", False))
        coal = bool(info.get("coal", False))
        steel = bool(info.get("steel", False))
        agriculture = bool(info.get("agriculture", False))
        industry_level = int(info.get("industry_level", 0))

        # --- Derived flags ---
        is_water = biome in WATER_BIOMES
        is_impassable = biome in IMPASSABLE_BIOMES

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
            "political": {
                "country_1939": country,
                "province": province,
            },
            "settlement": {
                "type": settlement_type,
                "name": settlement_name,
                "population_class": pop_class,
                "anthrome": anthrome,
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

    # Sort by hex ID for deterministic output and Unity binary search
    hexes.sort(key=lambda h: h["id"])

    # --- Top-level document ---
    document = {
        "schema_version": SCHEMA_VERSION,
        "map_metadata": {
            "name": spec.name,
            "title": getattr(spec, "title", spec.name),
            "scenario_date": "1939-09-01",
            "hex_size_km": spec.hex_size_km,
            "hex_size_miles": round(spec.hex_size_km * 0.621371, 2),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "0.1.0",
            "data_sources": {
                "terrain": "OpenStreetMap (ODbL)",
                "elevation": "SRTM3 (NASA, public domain)",
                "boundaries": "GADM 4.1",
                "resources": "not_yet_implemented",
            },
            "bounds": {
                "min_lon": spec.bbox.min_lon,
                "min_lat": spec.bbox.min_lat,
                "max_lon": spec.bbox.max_lon,
                "max_lat": spec.bbox.max_lat,
            },
            "grid": {
                "orientation": "pointy_top",
                "offset": "odd_col_north",
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


def _default_pop_class(settlement_type_str: str) -> int:
    """Derive population class integer from settlement type string."""
    return {
        "none":       0,
        "village":    1,
        "town":       2,
        "city":       3,
        "metropolis": 5,
    }.get(settlement_type_str, 0)