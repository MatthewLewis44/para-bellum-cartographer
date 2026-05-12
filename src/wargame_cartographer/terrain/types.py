"""Terrain types and modifier enums for Para Bellum.

PIPELINE SCOPE (this file):
    Defines the data contract between the cartography pipeline and Unity.
    All enums here are stored in the hex JSON and loaded by the game engine.

OUT OF SCOPE FOR PIPELINE (implemented in Unity / game engine):
    - Tactical battle map selection logic (biome + modifiers → map pool)
    - Season and weather effects on terrain (runtime, not stored per hex)
    - Time-of-day modifiers (runtime)
    - Nation-specific terrain overlays (hedgerows, rice paddies, kolkhoz
      fields — applied by Unity based on controlling nation, not stored here)
    - Player-placed objects (bridges, buildings, fortification upgrades)
    - Tactical map procedural generation
    - Movement cost calculation with modifier stacking (game rules, not data)
"""

from __future__ import annotations
from enum import Enum


# ---------------------------------------------------------------------------
# Biome — primary terrain classification (24 world-scalable types)
# ---------------------------------------------------------------------------
# v1 scope (Europe + Med + N. Africa) will realistically assign:
#   plains, steppe, forest, desert, badlands, hill, mountain,
#   marsh, swamp, tundra, urban, beach, water, coastal_water, lake, glacier
#
# Post-v1 biomes (enum defined now for global scalability, pipeline will not
# assign these in Sprint 1 region): jungle, taiga, savanna, mangrove,
#   rainforest, atoll, volcanic_island, highland_plateau
# ---------------------------------------------------------------------------

class Biome(Enum):
    # --- Temperate & Grasslands ---
    PLAINS          = "plains"           # Open farmland, cropland, grassland. Max maneuver.
    STEPPE          = "steppe"           # Drier grassland, shrubland, veldt. Similar to plains but harsher.
    FOREST          = "forest"           # Boreal or deciduous forest. Concealment, slows armor.
    JUNGLE          = "jungle"           # Tropical jungle. Near-zero visibility, infantry only. [post-v1]
    RAINFOREST      = "rainforest"       # Dense tropical rainforest. Impassable to vehicles. [post-v1]

    # --- Arid & Semi-Arid ---
    DESERT          = "desert"           # Sand desert / erg. Heat attrition, open LOS.
    BADLANDS        = "badlands"         # Arid eroded plateau, ravines, karst. Broken LOS.
    SAVANNA         = "savanna"          # Sub-Saharan / Australian dry grassland. [post-v1]

    # --- Highland & Mountain ---
    HILL            = "hill"             # Rolling hills. Ridgelines, saddles, tactical positioning.
    MOUNTAIN        = "mountain"         # Rugged mountain. Trails only, severe LOS block.
    HIGHLAND_PLATEAU= "highland_plateau" # High elevation but flat (Anatolian plateau, Altiplano).
    GLACIER         = "glacier"          # Alpine ice / icecap. Impassable.
    TUNDRA          = "tundra"           # Flat frozen ground, permafrost, treeless.
    TAIGA           = "taiga"            # Snowy coniferous forest. Forest + winter combined. [post-v1]

    # --- Wetlands ---
    MARSH           = "marsh"            # Open wetland, bog, low-lying flooded ground.
    SWAMP           = "swamp"            # Forested wetland. Concealment + difficult movement.
    MANGROVE        = "mangrove"         # Coastal tropical flooded forest. Naval + land hybrid. [post-v1]

    # --- Coastal & Amphibious ---
    BEACH           = "beach"            # Coastal landing zone. Amphibious transition terrain.
    ATOLL           = "atoll"            # Coral reef island. Tiny land mass, beach + shallow water. [post-v1]
    VOLCANIC_ISLAND = "volcanic_island"  # High-relief island, steep jungle interior. [post-v1]

    # --- Water ---
    WATER           = "water"            # Open sea. Naval only.
    COASTAL_WATER   = "coastal_water"    # Shallow coastal / strait / channel. Naval only.
    LAKE            = "lake"             # Inland water body. Impassable to land units.

    # --- Urban (subtype detail handled by settlement.anthrome) ---
    URBAN           = "urban"            # Any built-up area. Anthrome field determines tactical subtype.


# Biomes active in v1 map region (Europe + Med + N. Africa).
# Pipeline classifier will only assign these for Sprint 1.
V1_BIOMES: frozenset[Biome] = frozenset([
    Biome.PLAINS, Biome.STEPPE, Biome.FOREST, Biome.DESERT, Biome.BADLANDS,
    Biome.HILL, Biome.MOUNTAIN, Biome.HIGHLAND_PLATEAU, Biome.GLACIER,
    Biome.TUNDRA, Biome.MARSH, Biome.SWAMP, Biome.URBAN, Biome.BEACH,
    Biome.WATER, Biome.COASTAL_WATER, Biome.LAKE,
])

# Biomes that are impassable to all land units
IMPASSABLE_BIOMES: frozenset[Biome] = frozenset([
    Biome.WATER, Biome.COASTAL_WATER, Biome.LAKE, Biome.GLACIER,
])

# Biomes that are water (naval movement applies)
WATER_BIOMES: frozenset[Biome] = frozenset([
    Biome.WATER, Biome.COASTAL_WATER, Biome.LAKE,
])


# ---------------------------------------------------------------------------
# ElevationTier — tactical map multiplier
# Derived from SRTM elevation_m + slope_deg. Stored alongside raw elevation_m.
#
# NOTE: Tier is derived by the pipeline classifier, NOT hardcoded per biome.
# A highland plateau (Denver, Anatolia) is FLAT tier despite high elevation.
# Rule: slope_deg drives tier; elevation_m only adds HIGHLAND_PLATEAU at flat+high.
#
# Pipeline derivation logic (implemented in terrain/classifier.py):
#   slope < 3 deg + elevation > 1500m → HIGHLAND_PLATEAU
#   slope < 3 deg                     → FLAT
#   slope < 10 deg                    → HILLY
#   slope < 20 deg                    → MOUNTAINOUS
#   slope >= 20 deg                   → RUGGED
# ---------------------------------------------------------------------------

class ElevationTier(Enum):
    FLAT             = "flat"             # Low slope. Max visibility, fastest movement.
    HILLY            = "hilly"            # Moderate slope. Ridgelines and depressions.
    MOUNTAINOUS      = "mountainous"      # Steep. Severe movement penalty, LOS broken.
    RUGGED           = "rugged"           # Extreme slope. Trails only.
    HIGHLAND_PLATEAU = "highland_plateau" # High elevation but flat. Thin air, weather exposure.


# ---------------------------------------------------------------------------
# VegetationLevel — affects concealment and LOS in tactical maps
# Derived by pipeline from OSM landuse/natural tags + biome.
# ---------------------------------------------------------------------------

class VegetationLevel(Enum):
    BARE    = "bare"    # Rock, sand, urban hardscape, glacier. No concealment.
    SPARSE  = "sparse"  # Scrub, heathland, steppe grass. Low concealment.
    LIGHT   = "light"   # Farmland, open woodland. Moderate concealment.
    DENSE   = "dense"   # Forest, jungle, swamp. High concealment.


# ---------------------------------------------------------------------------
# MoistureLevel — affects mud, attrition, and trafficability
# Derived by pipeline from elevation, OSM water proximity, and biome.
# Season modifier (dry vs wet season mud) is applied at runtime by Unity.
# ---------------------------------------------------------------------------

class MoistureLevel(Enum):
    ARID        = "arid"        # Desert, badlands. High heat attrition.
    DRY         = "dry"         # Steppe, light scrub. Low attrition.
    TEMPERATE   = "temperate"   # Standard European terrain. No modifier.
    WET         = "wet"         # Near rivers, highland rain. Mud risk in autumn/spring.
    FLOODED     = "flooded"     # Marsh, swamp, paddy. Always difficult. Armor risk.


# ---------------------------------------------------------------------------
# Base movement costs by biome (land units, standard equipment)
# These are STARTING VALUES. Unity applies modifier stack at runtime:
#   ElevationTier, MoistureLevel, season, weather, infrastructure, unit type.
#
# NOT stored in hex JSON — Unity reads biome and looks up from game config.
# Stored here as pipeline reference and for Folium debug tooltips.
# ---------------------------------------------------------------------------

BIOME_BASE_MOVEMENT: dict[Biome, int] = {
    Biome.PLAINS:           1,
    Biome.STEPPE:           1,
    Biome.FOREST:           2,
    Biome.JUNGLE:           3,
    Biome.RAINFOREST:       4,   # Effectively impassable to vehicles
    Biome.DESERT:           2,
    Biome.BADLANDS:         2,
    Biome.SAVANNA:          1,
    Biome.HILL:             2,
    Biome.MOUNTAIN:         3,
    Biome.HIGHLAND_PLATEAU: 2,
    Biome.GLACIER:          99,
    Biome.TUNDRA:           2,
    Biome.TAIGA:            3,
    Biome.MARSH:            3,
    Biome.SWAMP:            3,
    Biome.MANGROVE:         3,
    Biome.BEACH:            1,
    Biome.ATOLL:            1,
    Biome.VOLCANIC_ISLAND:  3,
    Biome.WATER:            99,
    Biome.COASTAL_WATER:    99,
    Biome.LAKE:             99,
    Biome.URBAN:            1,
}

BIOME_BASE_DEFENSE: dict[Biome, int] = {
    Biome.PLAINS:           0,
    Biome.STEPPE:           0,
    Biome.FOREST:           1,
    Biome.JUNGLE:           2,
    Biome.RAINFOREST:       2,
    Biome.DESERT:           0,
    Biome.BADLANDS:         1,
    Biome.SAVANNA:          0,
    Biome.HILL:             1,
    Biome.MOUNTAIN:         2,
    Biome.HIGHLAND_PLATEAU: 1,
    Biome.GLACIER:          0,
    Biome.TUNDRA:           0,
    Biome.TAIGA:            1,
    Biome.MARSH:            0,
    Biome.SWAMP:            1,
    Biome.MANGROVE:         1,
    Biome.BEACH:            0,
    Biome.ATOLL:            0,
    Biome.VOLCANIC_ISLAND:  1,
    Biome.WATER:            0,
    Biome.COASTAL_WATER:    0,
    Biome.LAKE:             0,
    Biome.URBAN:            2,
}


# ---------------------------------------------------------------------------
# Convenience lookup
# ---------------------------------------------------------------------------

def biome_from_str(value: str) -> Biome:
    """Look up Biome by string value. Raises ValueError if unknown."""
    for b in Biome:
        if b.value == value:
            return b
    raise ValueError(f"Unknown biome: '{value}'")