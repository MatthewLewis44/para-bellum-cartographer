"""Strategic resources for Para Bellum (F-2, hand-authored 1930-era data).

Loads ``data/resources/resources_1930.geojson`` — repo-committed, authored
from public-domain historical industrial geography (AD-018). Coal/steel/iron
basins (polygons) and major works (points). The sampler tags each hex's
``resources.{coal,steel,iron,oil}`` from this layer via point-in-polygon
(for basins) and point-in-hex (for works).

Authoring is intentionally separate from the pipeline so resources can be
hand-curated and reviewed without code changes (baseline/corrections split,
AD-017).
"""

from __future__ import annotations

from pathlib import Path

import geopandas as gpd
from rich.console import Console

console = Console()

_REPO_ROOT = Path(__file__).resolve().parents[3]
RESOURCES_FILE = _REPO_ROOT / "data" / "resources" / "resources_1930.geojson"

# Resource types the schema carries (resources.{coal,steel,iron,oil}).
RESOURCE_TYPES = ("coal", "steel", "iron", "oil")


def load_resources_1930(path: Path | None = None) -> gpd.GeoDataFrame:
    """Load the repo-committed 1930 strategic-resources layer.

    Returns a GeoDataFrame (EPSG:4326) with columns:
        geometry, resource_type, name, country
    Empty (not an error) if the file is absent — resources then default false.
    """
    path = Path(path) if path is not None else RESOURCES_FILE
    if not path.exists():
        fallback = Path.cwd() / "data" / "resources" / "resources_1930.geojson"
        path = fallback if fallback.exists() else path
    if not path.exists():
        console.print(
            f"  [yellow]Resources layer not found: {path} — resources default false[/yellow]"
        )
        return gpd.GeoDataFrame(
            columns=["geometry", "resource_type", "name", "country"], crs="EPSG:4326"
        )

    gdf = gpd.read_file(path)
    keep = [c for c in ("geometry", "resource_type", "name", "country") if c in gdf.columns]
    return gdf[keep]
