"""Main pipeline: MapSpec → Data → Grid → Terrain → Render → Export.

Para Bellum fork — extends the upstream pipeline with:
    - OSM landuse / settlement / road / rail / waterway / bridge layers
    - Full Para Bellum JSON schema output via game_data_exporter
    - BiomeClassifier replacing the hash-based TerrainClassifier

Rendering (PNG, PDF, HTML) is preserved from upstream for debug/QA use.
The visual renderer still uses the upstream TerrainType enum internally;
Para Bellum Biome data lives only in the JSON output.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from wargame_cartographer.config.map_spec import MapSpec
from wargame_cartographer.geo.elevation import ElevationProcessor
from wargame_cartographer.geo.projection import select_crs
from wargame_cartographer.geo.vector import load_vector_data, load_osm_layers
from wargame_cartographer.hex.grid import HexGrid
from wargame_cartographer.hex.sampler import HexSampler
from wargame_cartographer.rendering.renderer import MapRenderer, RenderContext
from wargame_cartographer.rendering.styles import get_style
from wargame_cartographer.terrain.types import Biome


def run_pipeline(
    spec_path: str | Path,
    status_callback: Callable[[str], None] | None = None,
) -> dict:
    """Execute the full Para Bellum map generation pipeline.

    Returns dict with: hex_count, biome_distribution, output_files
    """

    def status(msg: str):
        if status_callback:
            status_callback(msg)

    # Structured per-stage log: name, elapsed seconds, input/output counts.
    # Returned in the result dict and echoed through the status callback.
    stage_log: list[dict] = []
    _stage_t0 = time.perf_counter()

    def stage_done(name: str, **counts):
        nonlocal _stage_t0
        elapsed = time.perf_counter() - _stage_t0
        entry = {"stage": name, "elapsed_s": round(elapsed, 2), **counts}
        stage_log.append(entry)
        kv = " ".join(f"{k}={v}" for k, v in counts.items())
        status(f"[stage {name}] {elapsed:.2f}s {kv}".rstrip())
        _stage_t0 = time.perf_counter()

    # 1. Load spec
    status("Loading map specification...")
    spec = MapSpec.from_yaml(spec_path)
    style = get_style(spec.designer_style, font_scale=spec.font_scale)
    stage_done("load_spec", bbox=f"{spec.bbox.min_lon},{spec.bbox.min_lat},{spec.bbox.max_lon},{spec.bbox.max_lat}")

    # 2. Build hex grid
    status(f"Building hex grid ({spec.hex_size_km} km hexes)...")
    crs = select_crs(spec.bbox) if spec.crs is None else None
    grid = HexGrid(bbox=spec.bbox, hex_size_km=spec.hex_size_km, crs=crs)
    status(f"Grid ready: {grid.hex_count} hexes")
    stage_done("build_grid", hexes=grid.hex_count)

    # 3. Download Natural Earth vector data (upstream layers)
    status("Downloading Natural Earth data...")
    vector_data = None
    try:
        vector_data = load_vector_data(spec.bbox, spec)
    except Exception as e:
        status(f"Natural Earth data partially loaded: {e}")
    stage_done("natural_earth", loaded=vector_data is not None)

    # 4. Download OSM layers (Para Bellum addition)
    status("Downloading OSM layers (landuse, settlements, roads, rails, waterways)...")
    osm_data = None
    try:
        osm_data = load_osm_layers(spec.bbox)
        status(
            f"OSM ready: "
            f"{len(osm_data.landuse)} landuse polygons, "
            f"{len(osm_data.settlements)} settlements, "
            f"{len(osm_data.roads)} road segments, "
            f"{len(osm_data.railways)} rail segments, "
            f"{len(osm_data.waterways)} waterways"
        )
    except Exception as e:
        status(f"OSM data partially loaded: {e}")
    stage_done(
        "osm_layers",
        landuse=len(osm_data.landuse) if osm_data else 0,
        settlements=len(osm_data.settlements) if osm_data else 0,
        roads=len(osm_data.roads) if osm_data else 0,
        railways=len(osm_data.railways) if osm_data else 0,
        waterways=len(osm_data.waterways) if osm_data else 0,
    )

    # 4b. Load 1930 political boundaries (repo-committed, AD-018)
    status("Loading 1930 political boundaries...")
    boundaries_gdf = None
    try:
        from wargame_cartographer.geo.boundaries import load_boundaries_1930
        boundaries_gdf = load_boundaries_1930()
        status(f"Boundaries ready: {len(boundaries_gdf)} countries")
    except Exception as e:
        status(f"1930 boundaries unavailable: {e}")
    stage_done("boundaries_1930", countries=len(boundaries_gdf) if boundaries_gdf is not None else 0)

    # 4c. Load 1930 strategic resources (repo-committed, F-2)
    status("Loading 1930 strategic resources...")
    resources_gdf = None
    try:
        from wargame_cartographer.geo.resources import load_resources_1930
        resources_gdf = load_resources_1930()
        status(f"Resources ready: {len(resources_gdf)} features")
    except Exception as e:
        status(f"1930 resources unavailable: {e}")
    stage_done("resources_1930", features=len(resources_gdf) if resources_gdf is not None else 0)

    # 4d. Load 1930 provinces (repo-committed, AD-023/027)
    status("Loading 1930 provinces...")
    provinces = None
    try:
        from wargame_cartographer.geo.provinces import load_provinces
        provinces = load_provinces()
        status(f"Provinces ready: {len(provinces.gdf)} provinces")
    except Exception as e:
        status(f"1930 provinces unavailable: {e}")
    stage_done("provinces_1930", provinces=len(provinces.gdf) if provinces is not None else 0)

    # 5. Get elevation + hillshade
    status("Processing elevation data...")
    elev_proc = ElevationProcessor()
    elevation, elev_metadata = elev_proc.get_elevation(spec.bbox)
    hillshade = elev_proc.compute_hillshade(
        elevation,
        azimuth=style.hillshade_azimuth,
        altitude=style.hillshade_altitude,
    ) if spec.show_elevation_shading else None
    stage_done("elevation", shape=f"{elevation.shape[0]}x{elevation.shape[1]}")

    # 6. Classify terrain per hex (Para Bellum BiomeClassifier)
    status("Classifying hex terrain (biome, settlement, infrastructure)...")
    sampler = HexSampler()
    hex_terrain = sampler.build_hex_terrain(
        grid, spec.bbox,
        vector_data=vector_data,
        osm_data=osm_data,
        boundaries_gdf=boundaries_gdf,
        resources_gdf=resources_gdf,
        provinces=provinces,
    )

    # Biome distribution summary
    biome_counts: dict[str, int] = {}
    for info in hex_terrain.values():
        b = info.get("biome")
        name = b.value if isinstance(b, Biome) else str(b)
        biome_counts[name] = biome_counts.get(name, 0) + 1

    settled = sum(1 for i in hex_terrain.values() if i.get("settlement_type", "none") != "none")
    with_country = sum(1 for i in hex_terrain.values() if i.get("country_at_start"))
    stage_done("sample_hexes", hexes=len(hex_terrain), settled=settled, with_country=with_country)

    # 7. Render (upstream renderer — uses biome→legacy terrain mapping)
    status("Rendering map layers...")

    # Map biome back to legacy terrain string for renderer compatibility
    render_terrain = _biome_to_render_terrain(hex_terrain)

    context = RenderContext(
        spec=spec,
        grid=grid,
        hex_terrain=render_terrain,
        style=style,
        elevation=elevation,
        hillshade=hillshade,
        elevation_metadata=elev_metadata,
        vector_data=vector_data,
    )
    renderer = MapRenderer()
    fig = renderer.render(context)
    stage_done("render")

    # 8. Export
    output_dir = Path(spec.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = (
        "".join(c if c.isalnum() or c in "_-" else "_" for c in spec.name)[:40].lower()
    )

    output_files = {}

    if "png" in spec.output_formats:
        status("Exporting PNG...")
        from wargame_cartographer.output.static_exporter import export_png
        png_path = export_png(fig, output_dir / f"{safe_name}_map.png", dpi=spec.dpi)
        output_files["png"] = str(png_path.resolve())

    if "pdf" in spec.output_formats:
        status("Exporting PDF...")
        from wargame_cartographer.output.static_exporter import export_pdf
        pdf_path = export_pdf(fig, output_dir / f"{safe_name}_map.pdf")
        output_files["pdf"] = str(pdf_path.resolve())

    if "html" in spec.output_formats:
        status("Exporting interactive HTML...")
        from wargame_cartographer.output.html_exporter import export_html
        html_path = export_html(
            grid, hex_terrain, spec,
            output_dir / f"{safe_name}_interactive.html"
        )
        output_files["html"] = str(html_path.resolve())

    if "json" in spec.output_formats:
        status("Exporting Para Bellum hex JSON...")
        from wargame_cartographer.output.game_data_exporter import export_game_data
        json_path = export_game_data(
            grid, hex_terrain, spec,
            output_dir / f"{safe_name}_hex_terrain.json"
        )
        output_files["json"] = str(json_path.resolve())

    plt.close(fig)
    stage_done("export", formats=",".join(output_files.keys()))
    total_s = round(sum(e["elapsed_s"] for e in stage_log), 2)
    status(f"Done! ({total_s}s total)")

    return {
        "hex_count": grid.hex_count,
        "biome_distribution": dict(sorted(biome_counts.items())),
        "output_files": output_files,
        "stage_log": stage_log,
    }


# ---------------------------------------------------------------------------
# Renderer compatibility shim
# ---------------------------------------------------------------------------

# Maps our Biome values back to the legacy TerrainType strings the renderer
# expects. The renderer is visual-only — this mapping only affects PNG/HTML.
_BIOME_TO_RENDER: dict[str, str] = {
    "plains":           "clear",
    "steppe":           "clear",
    "forest":           "forest",
    "jungle":           "forest",
    "rainforest":       "forest",
    "desert":           "desert",
    "badlands":         "rough",
    "savanna":          "clear",
    "hill":             "rough",
    "mountain":         "mountain",
    "highland_plateau": "rough",
    "glacier":          "mountain",
    "tundra":           "rough",
    "taiga":            "forest",
    "marsh":            "marsh",
    "swamp":            "marsh",
    "mangrove":         "marsh",
    "beach":            "clear",
    "atoll":            "clear",
    "volcanic_island":  "rough",
    "water":            "water",
    "coastal_water":    "water",
    "lake":             "water",
    "urban":            "urban",
}


def _biome_to_render_terrain(
    hex_terrain: dict[tuple[int, int], dict],
) -> dict[tuple[int, int], dict]:
    """Convert Para Bellum hex_terrain to renderer-compatible format."""
    from wargame_cartographer.terrain.types import Biome as B

    # Import legacy TerrainType for renderer
    try:
        # The upstream types.py is now replaced — use a string-based shim
        from enum import Enum
        class _LegacyTerrain(str, Enum):
            WATER    = "water"
            CLEAR    = "clear"
            ROUGH    = "rough"
            FOREST   = "forest"
            MOUNTAIN = "mountain"
            MARSH    = "marsh"
            DESERT   = "desert"
            URBAN    = "urban"

        render = {}
        for (q, r), info in hex_terrain.items():
            biome = info.get("biome")
            biome_str = biome.value if isinstance(biome, B) else str(biome)
            legacy_str = _BIOME_TO_RENDER.get(biome_str, "clear")
            legacy = _LegacyTerrain(legacy_str)

            render[(q, r)] = {
                "terrain":           legacy,
                "elevation_m":       info.get("elevation_m", 0),
                "slope_deg":         info.get("slope_deg", 0),
                "movement_cost":     1 if legacy_str in ("clear", "urban") else
                                     2 if legacy_str in ("rough", "forest", "desert") else
                                     3 if legacy_str in ("mountain", "marsh") else 99,
                "defensive_modifier": 0,
                "blocks_los":        legacy_str in ("forest", "mountain", "urban"),
            }
        return render
    except Exception:
        # Hard fallback: return hex_terrain unchanged, renderer will handle
        return hex_terrain