"""Terrain classifier for Para Bellum.

Maps real OSM + SRTM data to Biome enum values. No hash-based fake terrain.
Every hex gets a biome from real geographic data.

Classification priority (highest wins):
    1. Water (ocean, lake) — from Natural Earth land polygon
    2. Glacier              — from OSM natural=glacier or elevation > 2800m + slope
    3. Beach                — from OSM natural=beach + is_coastal
    4. Urban                — from OSM settlement (city/town/village) or landuse=residential
    5. Wetland (marsh/swamp)— from OSM natural=wetland / landuse=wetland
    6. Forest               — from OSM natural=wood / landuse=forest
    7. Mountain / Hill      — from SRTM slope_deg thresholds
    8. Desert               — from OSM natural=sand + low moisture region
    9. Tundra               — from elevation + latitude (above treeline)
    10. Plains / Steppe     — fallback for low-slope land

OUT OF SCOPE (handled by Unity at runtime, not stored per hex):
    - Season modifiers (mud, snow cover)
    - Weather effects
    - Nation-specific overlays (hedgerows, paddies)
    - Vegetation/moisture as tactical modifiers (stored separately in hex data)
"""

from __future__ import annotations

from wargame_cartographer.terrain.types import (
    Biome,
    ElevationTier,
    VegetationLevel,
    MoistureLevel,
    V1_BIOMES,
)


class BiomeClassifier:
    """Classify a hex into a Biome from geographic data.

    Receives pre-computed boolean flags from HexSampler (which does the
    spatial intersection work). This class only contains decision logic.
    """

    # Slope thresholds (degrees)
    SLOPE_MOUNTAIN  = 20.0   # Above → mountain
    SLOPE_HILL      = 8.0    # Above → hill
    SLOPE_HILLY     = 3.0    # Above → at minimum hilly elevation tier

    # Elevation thresholds (metres)
    ELEV_MOUNTAIN   = 800.0  # Mountain classification floor
    ELEV_TREELINE   = 1800.0 # Above treeline — tundra/glacier candidate
    ELEV_GLACIER    = 2800.0 # Near-certain glacier if also low slope

    # Latitude threshold for tundra (above this latitude, low veg = tundra)
    LAT_TUNDRA      = 65.0

    def classify(
        self,
        *,
        # SRTM-derived
        elevation_m: float,
        slope_deg: float,
        # From Natural Earth land polygon
        is_water: bool,
        is_lake: bool,
        is_coastal: bool,
        # From OSM landuse layer
        landuse_type: str | None,   # forest/farmland/wetland/residential/industrial/
                                    # heath/scrub/grass/sand/glacier/beach/water/military/quarry
        # From OSM settlement layer
        has_settlement: bool,
        settlement_place_type: str | None,  # city/town/village
        # Geographic context
        lat: float,
        lon: float,
    ) -> Biome:
        """Classify a single hex into a Biome.

        Args are all pre-computed by HexSampler — no spatial ops here.
        """

        # --- 1. Water ---
        if is_water:
            if is_coastal:
                return Biome.COASTAL_WATER
            return Biome.WATER

        if is_lake:
            return Biome.LAKE

        # --- 2. Glacier ---
        if landuse_type == "glacier":
            return Biome.GLACIER
        if elevation_m > self.ELEV_GLACIER and slope_deg < 10.0:
            return Biome.GLACIER

        # --- 3. Beach ---
        if landuse_type == "beach" and is_coastal:
            return Biome.BEACH

        # --- 4. Urban ---
        # Cities and towns → always urban biome
        # Villages → plains biome (settlement data still tagged, just not urban biome)
        # Industrial landuse → urban regardless of settlement
        if has_settlement and settlement_place_type in ("city", "town"):
            return Biome.URBAN
        if landuse_type == "industrial":
            return Biome.URBAN

        # --- 5. Wetland ---
        if landuse_type == "wetland":
            # Forested wetland → swamp; open wetland → marsh
            if _has_tree_cover(elevation_m, lat):
                return Biome.SWAMP
            return Biome.MARSH

        # --- 6. Forest ---
        if landuse_type == "forest":
            # High-latitude coniferous forest under heavy snow → taiga (post-v1)
            # For v1 scope just return forest
            return Biome.FOREST

        # --- 7. Mountain / Hill (slope-driven) ---
        if slope_deg >= self.SLOPE_MOUNTAIN:
            return Biome.MOUNTAIN
        if elevation_m >= self.ELEV_MOUNTAIN and slope_deg >= 5.0:
            return Biome.MOUNTAIN
        if slope_deg >= self.SLOPE_HILL:
            return Biome.HILL

        # --- 8. Highland Plateau ---
        # Flat but very high — Anatolian plateau, Atlas mountains plateau
        if elevation_m >= 1500.0 and slope_deg < self.SLOPE_HILLY:
            return Biome.HIGHLAND_PLATEAU

        # --- 9. Tundra ---
        # Above treeline at high latitude, not glaciated
        if elevation_m >= self.ELEV_TREELINE and lat >= 55.0:
            return Biome.TUNDRA
        if lat >= self.LAT_TUNDRA and landuse_type in (None, "grass", "heath", "scrub"):
            return Biome.TUNDRA

        # --- 10. Desert ---
        if landuse_type == "sand":
            return Biome.DESERT
        # Arid zone heuristic: North Africa + Middle East low elevation
        if lat < 32.0 and lon > -5.0 and elevation_m < 500.0 and slope_deg < 5.0:
            return Biome.DESERT

        # --- 11. Steppe ---
        # Dry grassland: heath/scrub OSM tags, or Eastern Europe low elevation
        if landuse_type in ("heath", "scrub"):
            return Biome.STEPPE
        if lon > 25.0 and elevation_m < 300.0 and slope_deg < 5.0:
            # East of 25°E = steppe belt (Ukraine, Hungary, Romania lowlands)
            return Biome.STEPPE

        # --- 12. Plains (default for temperate land) ---
        return Biome.PLAINS

    def classify_vegetation(
        self,
        landuse_type: str | None,
        biome: Biome,
    ) -> VegetationLevel:
        """Derive vegetation level from landuse and biome."""
        if biome in (Biome.WATER, Biome.COASTAL_WATER, Biome.LAKE,
                     Biome.GLACIER, Biome.DESERT, Biome.BEACH):
            return VegetationLevel.BARE

        if biome in (Biome.FOREST, Biome.SWAMP, Biome.JUNGLE,
                     Biome.RAINFOREST, Biome.TAIGA, Biome.MANGROVE):
            return VegetationLevel.DENSE

        if landuse_type in ("forest", "wetland"):
            return VegetationLevel.DENSE
        if landuse_type in ("farmland", "grass", "residential", "industrial"):
            return VegetationLevel.LIGHT
        if landuse_type in ("heath", "scrub", "sand"):
            return VegetationLevel.SPARSE
        if biome in (Biome.TUNDRA, Biome.STEPPE, Biome.BADLANDS):
            return VegetationLevel.SPARSE
        if biome == Biome.URBAN:
            return VegetationLevel.BARE

        return VegetationLevel.LIGHT  # temperate plains default

    def classify_moisture(
        self,
        biome: Biome,
        landuse_type: str | None,
        elevation_m: float,
        lat: float,
        lon: float,
    ) -> MoistureLevel:
        """Derive moisture level from biome and geographic context."""
        if biome in (Biome.MARSH, Biome.SWAMP, Biome.MANGROVE):
            return MoistureLevel.FLOODED
        if landuse_type == "wetland":
            return MoistureLevel.FLOODED

        if biome == Biome.DESERT:
            return MoistureLevel.ARID
        if biome == Biome.STEPPE:
            return MoistureLevel.DRY
        if biome == Biome.TUNDRA:
            return MoistureLevel.DRY

        # North Africa / Middle East arid zone
        if lat < 30.0 and lon > -5.0:
            return MoistureLevel.ARID

        # Highland rain / wet Atlantic coasts
        if elevation_m > 600.0:
            return MoistureLevel.WET
        if lon < 5.0 and lat > 45.0:
            # Atlantic-facing Western Europe — wetter
            return MoistureLevel.WET

        return MoistureLevel.TEMPERATE


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _has_tree_cover(elevation_m: float, lat: float) -> bool:
    """Heuristic: would this location naturally have trees?
    Used to distinguish swamp (forested) from marsh (open).
    """
    # Above treeline → no trees
    if elevation_m > 1800.0:
        return False
    # High arctic → no trees
    if lat > 68.0:
        return False
    return True