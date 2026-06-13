"""Infrastructure and settlement type enums for Para Bellum.

PIPELINE SCOPE (this file):
    Data that the cartography pipeline derives from OSM and stores in hex JSON.
    These are static geographic facts as of the scenario date (1930-01-01).

OUT OF SCOPE FOR PIPELINE (implemented in Unity / game engine):
    - Player-placed buildings (factories, supply depots, HQ buildings)
    - Bridge construction / destruction (dynamic, runtime state)
    - Fortification upgrades (player action, not base map data)
    - Tactical map selection logic using anthrome field
    - Nation-specific overlays (hedgerows, paddies) derived from controlling
      nation at runtime — NOT stored here, applied by Unity on battle start
    - Port capacity / naval supply (game rules, not pipeline data)

TACTICAL BATTLE MAP HINTS (for Unity team reference):
    The following field combinations are intended to drive tactical map pool
    selection. Final logic lives in Unity, not this pipeline:

    river_edges non-empty + bridge=false  → river assault map pool
    river_edges non-empty + bridge=true   → bridge battle map pool
    is_coastal=true + port=true           → port / amphibious map pool
    is_coastal=true + port=false          → beach landing map pool
    fortification != none                 → fortified line map pool
    anthrome=industrial                   → factory / industrial map pool
    anthrome=metro                        → urban canyon map pool
    anthrome=mining                       → quarry / tiered terrain map pool
"""

from __future__ import annotations
from enum import Enum


# ---------------------------------------------------------------------------
# Road infrastructure level
# Derived from OSM highway tags.
# ---------------------------------------------------------------------------

class RoadLevel(Enum):
    NONE    = "none"     # No road
    DIRT    = "dirt"     # Unpaved track, farm path (OSM: track, path)
    PAVED   = "paved"    # Paved secondary/primary road (OSM: secondary, primary)
    HIGHWAY = "highway"  # Autobahn / motorway class (OSM: motorway, trunk)


# ---------------------------------------------------------------------------
# Rail infrastructure level
# Derived from OSM railway tags.
# ---------------------------------------------------------------------------

class RailLevel(Enum):
    NONE     = "none"     # No rail
    NARROW   = "narrow"   # Narrow gauge (lower capacity, common in colonies)
    STANDARD = "standard" # Standard gauge single track
    DOUBLE   = "double"   # Double-track main line (high capacity)


# ---------------------------------------------------------------------------
# Fortification level
# Derived from OSM military tags + historical data layer (manual GeoJSON).
# Player upgrades to this are handled by Unity at runtime.
# ---------------------------------------------------------------------------

class FortificationLevel(Enum):
    NONE      = "none"      # No fortification
    FIELD     = "field"     # Hasty field fortifications (foxholes, wire)
    PERMANENT = "permanent" # Concrete fortifications (Maginot, Atlantic Wall,
                            # Siegfried Line, Mannerheim Line)


# ---------------------------------------------------------------------------
# Settlement type
# Derived from OSM place nodes + area tags.
# Fine-grained subtype (anthrome) for urban hexes drives tactical map selection.
# ---------------------------------------------------------------------------

class SettlementType(Enum):
    NONE       = "none"       # No settlement
    VILLAGE    = "village"    # < 2,000 population
    TOWN       = "town"       # 2,000 – 50,000
    CITY       = "city"       # 50,000 – 300,000
    METROPOLIS = "metropolis" # > 300,000 (Berlin, Paris, London, Moscow)


# ---------------------------------------------------------------------------
# Anthrome (anthropogenic biome subtype)
# Only meaningful when settlement.type != none OR biome == URBAN.
# Drives tactical battle map pool selection in Unity.
# Pipeline derives this from OSM landuse tags within the hex.
#
# NOTE: Player-placed buildings (factories, depots) are added on top of this
# base anthrome in the game engine. A hex with anthrome=residential can have
# a player-built factory on it — Unity handles that separately.
# ---------------------------------------------------------------------------

class Anthrome(Enum):
    NONE         = "none"         # No significant human modification
    RESIDENTIAL  = "residential"  # Housing, suburbs — medium cover, street fighting
    INDUSTRIAL   = "industrial"   # Factories, warehouses, rail yards — complex cover
    METRO        = "metro"        # Dense urban core — urban canyons, vertical fire
    CROPLAND     = "cropland"     # Fields, farms — open with ditch/hedge features
    PADDY        = "paddy"        # Rice paddies, irrigation ditches — [post-v1, SE Asia]
    MINING       = "mining"       # Quarry, mine complex — tiered terrain, depressions
    MANGROVE     = "mangrove"     # Coastal flooded forest — naval+land hybrid [post-v1]
    FORTIFIED    = "fortified"    # Pre-built defensive line (Maginot, Atlantic Wall)


# ---------------------------------------------------------------------------
# Population class (0–5 integer)
# Used by Unity for settlement rendering scale on the strategic map.
# Derived from OSM population tags where available.
# ---------------------------------------------------------------------------

SETTLEMENT_POPULATION_CLASS: dict[SettlementType, int] = {
    SettlementType.NONE:       0,
    SettlementType.VILLAGE:    1,
    SettlementType.TOWN:       2,
    SettlementType.CITY:       3,
    SettlementType.METROPOLIS: 5,  # 4 reserved for major cities within METROPOLIS tier
}