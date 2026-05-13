"""Interactive HTML output via Folium/Leaflet.

Rewritten for Para Bellum to use Biome-based colors and rich hex tooltips.
"""

from __future__ import annotations

from pathlib import Path

import folium

from wargame_cartographer.config.map_spec import MapSpec
from wargame_cartographer.hex.grid import HexGrid
from wargame_cartographer.hex.geojson import hex_grid_to_geojson


# ---------------------------------------------------------------------------
# Para Bellum biome color palette
# Designed for clarity on a Leaflet map at strategic zoom
# ---------------------------------------------------------------------------
BIOME_COLORS: dict[str, str] = {
    # Temperate
    "plains":           "#E8DFB0",  # Warm wheat — open farmland
    "steppe":           "#D4C882",  # Dry yellow-green
    "forest":           "#4A7C4E",  # Dark green
    "jungle":           "#2D6B35",  # Deep jungle green
    "rainforest":       "#1A5C28",  # Very dark green

    # Arid
    "desert":           "#E8C97A",  # Sand yellow
    "badlands":         "#C4956A",  # Eroded ochre
    "savanna":          "#C8B85A",  # Dry grass

    # Highland
    "hill":             "#B5A882",  # Khaki brown
    "mountain":         "#8A7D72",  # Grey-brown rock
    "highland_plateau": "#9E9080",  # Flat grey-brown
    "glacier":          "#D8EEF5",  # Ice blue-white
    "tundra":           "#A8B89A",  # Muted grey-green

    # Cold forest
    "taiga":            "#3A6B42",  # Dark conifer green

    # Wetlands
    "marsh":            "#7A9E7E",  # Muted swamp green
    "swamp":            "#4E7A52",  # Darker forested wetland
    "mangrove":         "#3D6B4F",  # Coastal dark green

    # Coastal & amphibious
    "beach":            "#F0E0A0",  # Sand
    "atoll":            "#E8D890",  # Coral sand
    "volcanic_island":  "#6B5B4E",  # Dark volcanic

    # Water
    "water":            "#4A90C4",  # Ocean blue
    "coastal_water":    "#7AB8D8",  # Lighter coastal blue
    "lake":             "#6AAFC8",  # Lake blue

    # Urban
    "urban":            "#C8B4A0",  # Warm grey-pink
}

# Settlement dot colors (overlaid on hex fill)
SETTLEMENT_COLORS: dict[str, str] = {
    "metropolis": "#1A1A1A",
    "city":       "#333333",
    "town":       "#555555",
    "village":    "#888888",
    "none":       "",
}


def export_html(
    grid: HexGrid,
    hex_terrain: dict[tuple[int, int], dict],
    spec: MapSpec,
    path: Path,
) -> Path:
    """Export interactive HTML map with Para Bellum biome colors."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    center_lat, center_lon = spec.bbox.center()
    zoom = _zoom_for_extent(spec.bbox.width_km())

    m = folium.Map(
        location=[center_lat, center_lon],
        zoom_start=zoom,
        tiles="CartoDB Positron",
    )

    # Build GeoJSON with Para Bellum data
    geojson_data = hex_grid_to_geojson(grid, hex_terrain)

    def style_function(feature):
        biome = feature["properties"].get("biome", "plains")
        color = BIOME_COLORS.get(biome, "#E8DFB0")
        return {
            "fillColor":   color,
            "color":       "#6B5C3E",   # warm brown grid lines
            "weight":      0.8,
            "fillOpacity": 0.75,
        }

    def highlight_function(feature):
        return {
            "fillColor":   "#FFD700",
            "color":       "#B8860B",
            "weight":      2,
            "fillOpacity": 0.9,
        }

    tooltip = folium.GeoJsonTooltip(
        fields=[
            "hex_id", "biome", "elevation_m", "elevation_tier",
            "vegetation", "moisture", "river_edges", "bridge",
            "settlement", "road", "rail", "country",
        ],
        aliases=[
            "Hex", "Biome", "Elevation (m)", "Tier",
            "Vegetation", "Moisture", "River edges", "Bridge",
            "Settlement", "Road", "Rail", "Country (1939)",
        ],
        localize=True,
        sticky=True,
        style=(
            "background-color: white; color: #333; font-family: monospace; "
            "font-size: 12px; padding: 8px; border-radius: 4px; "
            "box-shadow: 0 2px 6px rgba(0,0,0,0.3);"
        ),
    )

    folium.GeoJson(
        geojson_data,
        name="Hex Grid",
        style_function=style_function,
        highlight_function=highlight_function,
        tooltip=tooltip,
    ).add_to(m)

    # Legend
    _add_legend(m)

    # Title
    if spec.title:
        subtitle_html = (
            f'<br><span style="font-size:12px;color:#666;">'
            f'{spec.subtitle}</span>'
            if getattr(spec, "subtitle", None) else ""
        )
        title_html = f"""
        <div style="position:fixed;top:10px;left:60px;z-index:1000;
                    background:white;padding:8px 16px;border-radius:4px;
                    box-shadow:0 2px 6px rgba(0,0,0,0.3);
                    font-family:sans-serif;">
            <strong style="font-size:16px;">{spec.title}</strong>
            {subtitle_html}
            <br><span style="font-size:11px;color:#999;">1 September 1939</span>
        </div>
        """
        m.get_root().html.add_child(folium.Element(title_html))

    folium.LayerControl().add_to(m)
    m.save(str(path))
    return path


def _add_legend(m: folium.Map) -> None:
    """Add a biome color legend to the map."""
    # Show the most common v1 biomes
    legend_items = [
        ("plains",        "Plains"),
        ("hill",          "Hill"),
        ("forest",        "Forest"),
        ("marsh",         "Marsh / Swamp"),
        ("desert",        "Desert"),
        ("urban",         "Urban"),
        ("beach",         "Beach"),
        ("water",         "Water"),
        ("coastal_water", "Coastal Water"),
        ("glacier",       "Glacier"),
        ("tundra",        "Tundra"),
    ]

    items_html = "\n".join(
        f'<div style="display:flex;align-items:center;margin:3px 0;">'
        f'<div style="width:16px;height:16px;background:{BIOME_COLORS[b]};'
        f'border:1px solid #999;margin-right:6px;flex-shrink:0;"></div>'
        f'<span>{label}</span></div>'
        for b, label in legend_items
        if b in BIOME_COLORS
    )

    legend_html = f"""
    <div style="position:fixed;bottom:30px;right:10px;z-index:1000;
                background:white;padding:10px 14px;border-radius:6px;
                box-shadow:0 2px 8px rgba(0,0,0,0.3);
                font-family:sans-serif;font-size:12px;min-width:140px;">
        <strong style="display:block;margin-bottom:6px;">Terrain</strong>
        {items_html}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))


def _zoom_for_extent(width_km: float) -> int:
    if width_km < 10:   return 13
    if width_km < 50:   return 11
    if width_km < 200:  return 9
    if width_km < 500:  return 7
    if width_km < 2000: return 5
    return 3