"""Vector data loading for Para Bellum.

Extends the upstream VectorData with OSMLayerData — the additional OSM
layers needed for full hex tagging (landuse, settlements, roads, rails,
waterways, bridges).

The upstream load_vector_data() is preserved for renderer compatibility.
Para Bellum pipeline calls load_osm_layers() in addition.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import geopandas as gpd

from wargame_cartographer.config.map_spec import BoundingBox, MapSpec
from wargame_cartographer.geo.downloader import DataDownloader
from wargame_cartographer.geo.osm_downloader import OSMDownloader


# ---------------------------------------------------------------------------
# Upstream VectorData (unchanged — renderer depends on this)
# ---------------------------------------------------------------------------

@dataclass
class VectorData:
    """Natural Earth vector data for a map region (upstream, renderer uses this)."""
    coastline: gpd.GeoDataFrame
    land: gpd.GeoDataFrame
    rivers: gpd.GeoDataFrame
    lakes: gpd.GeoDataFrame
    countries: gpd.GeoDataFrame
    cities: gpd.GeoDataFrame
    ports: gpd.GeoDataFrame


# ---------------------------------------------------------------------------
# Para Bellum OSM layer data
# ---------------------------------------------------------------------------

@dataclass
class OSMLayerData:
    """OSM-derived layers for Para Bellum hex tagging."""
    landuse:     gpd.GeoDataFrame = field(default_factory=gpd.GeoDataFrame)
    settlements: gpd.GeoDataFrame = field(default_factory=gpd.GeoDataFrame)
    roads:       gpd.GeoDataFrame = field(default_factory=gpd.GeoDataFrame)
    railways:    gpd.GeoDataFrame = field(default_factory=gpd.GeoDataFrame)
    waterways:   gpd.GeoDataFrame = field(default_factory=gpd.GeoDataFrame)
    bridges:     gpd.GeoDataFrame = field(default_factory=gpd.GeoDataFrame)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_vector_data(bbox: BoundingBox, spec: MapSpec) -> VectorData:
    """Load Natural Earth vector data (upstream, unchanged)."""
    dl = DataDownloader()

    coastline = dl.get_natural_earth("coastline", bbox)
    land      = dl.get_natural_earth("land", bbox)
    rivers    = dl.get_natural_earth("rivers", bbox) if spec.show_rivers else gpd.GeoDataFrame()
    lakes     = dl.get_natural_earth("lakes", bbox)
    countries = dl.get_natural_earth("countries", bbox)
    cities    = dl.get_cities(bbox) if spec.show_cities else gpd.GeoDataFrame()
    # Ports are NOT detected (AD-036) — starting infrastructure is authored
    # construction-system data. The field stays for the renderer's dataclass
    # but is always empty; `infrastructure.port` is inert in the JSON.
    ports     = gpd.GeoDataFrame()

    return VectorData(
        coastline=coastline,
        land=land,
        rivers=rivers,
        lakes=lakes,
        countries=countries,
        cities=cities,
        ports=ports,
    )


def load_osm_layers(bbox: BoundingBox) -> OSMLayerData:
    """Load all Para Bellum OSM layers for a bbox.

    Each layer is fetched from Overpass API and cached locally.
    Failures are non-fatal — the layer returns an empty GeoDataFrame
    and the sampler falls back gracefully.
    """
    dl = OSMDownloader()

    return OSMLayerData(
        landuse     = dl.get_landuse(bbox),
        settlements = dl.get_settlements(bbox),
        roads       = dl.get_roads(bbox),
        railways    = dl.get_railways(bbox),
        # AD-029: river SELECTION is now Natural Earth scalerank + OSM canals
        # (geo.rivers_global), applied in pipeline.run_pipeline. Here we only
        # ensure the OSM waterway PARTS are cached (merge=False, no merge spike);
        # the canal pass reuses them. The old AD-011 merged river filter is gone.
        waterways   = dl.get_waterways(bbox, merge=False),
        bridges     = dl.get_bridges(bbox),
    )