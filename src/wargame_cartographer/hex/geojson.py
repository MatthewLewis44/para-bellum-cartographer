"""Export hex grid as GeoJSON for Folium interactive maps.

Rewritten for Para Bellum to use Biome enum and full hex data schema.
"""

from __future__ import annotations

from wargame_cartographer.hex.grid import HexGrid
from wargame_cartographer.terrain.types import Biome, BIOME_BASE_MOVEMENT, BIOME_BASE_DEFENSE


def hex_grid_to_geojson(
    grid: HexGrid,
    hex_terrain: dict[tuple[int, int], dict],
) -> dict:
    """Convert hex grid + terrain data to a GeoJSON FeatureCollection."""
    features = []

    for (q, r), cell in grid.cells.items():
        # Hex polygon in WGS84
        verts_proj = grid.hex_vertices(q, r)
        verts_geo = []
        for vx, vy in verts_proj:
            lon, lat = grid._to_geo.transform(vx, vy)
            verts_geo.append([lon, lat])
        verts_geo.append(verts_geo[0])  # close ring

        info = hex_terrain.get((q, r), {})

        # Resolve biome
        biome_raw = info.get("biome")
        if isinstance(biome_raw, Biome):
            biome_str = biome_raw.value
        elif biome_raw is not None:
            biome_str = str(biome_raw)
        else:
            biome_str = "plains"

        # Col/row for display
        col = q - grid._col_offset + 1
        row = r - grid._row_offset + 1
        hex_id = f"{col:03d}{row:02d}"

        # Settlement display
        settlement_type = info.get("settlement_type", "none")
        settlement_name = info.get("settlement_name", "")
        settlement_display = settlement_name if settlement_name else settlement_type

        # Infrastructure display
        road = info.get("road", "none")
        rail = info.get("rail", "none")
        river_edges = info.get("river_edges", [])
        bridge = info.get("bridge", False)

        feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [verts_geo],
            },
            "properties": {
                "hex_id":          hex_id,
                "col":             col,
                "row":             row,
                "biome":           biome_str,
                "elevation_m":     info.get("elevation_m", 0),
                "slope_deg":       info.get("slope_deg", 0),
                "elevation_tier":  info.get("elevation_tier", "flat")
                                   if isinstance(info.get("elevation_tier"), str)
                                   else getattr(info.get("elevation_tier"), "value", "flat"),
                "vegetation":      info.get("vegetation", "light")
                                   if isinstance(info.get("vegetation"), str)
                                   else getattr(info.get("vegetation"), "value", "light"),
                "moisture":        info.get("moisture", "temperate")
                                   if isinstance(info.get("moisture"), str)
                                   else getattr(info.get("moisture"), "value", "temperate"),
                "is_coastal":      info.get("is_coastal", False),
                "river_edges":     str(river_edges) if river_edges else "none",
                "bridge":          "yes" if bridge else "no",
                "settlement":      settlement_display,
                "road":            road,
                "rail":            rail,
                "country":         info.get("country_at_start", ""),
                "province":        info.get("province_at_start", ""),
            },
        }
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }