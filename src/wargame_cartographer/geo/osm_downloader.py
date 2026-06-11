"""OSM data downloader for Para Bellum pipeline.

Fetches landuse, settlements, roads, railways, and waterways from
the Overpass API. All results are cached to disk keyed by bbox hash.

This module extends the upstream downloader.py — it does not replace it.
The upstream DataDownloader handles Natural Earth + basic OSM ports.
This module handles the additional OSM layers Para Bellum needs.

OUT OF SCOPE (not fetched here):
    - GADM political boundaries (see geo/boundaries.py, Sprint 2)
    - Strategic resources (manual GeoJSON overlay, Sprint 2)
    - Historical WW2 boundary overrides (Sprint 2)
"""

from __future__ import annotations

import hashlib
import time
from pathlib import Path

import geopandas as gpd
import requests
from rich.console import Console
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.ops import unary_union

from wargame_cartographer.config.map_spec import BoundingBox

console = Console()

DEFAULT_CACHE_DIR = Path.home() / "wargame-cartographer" / "cache" / "osm_pb"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_MAX_AGE_DAYS = 30  # OSM data is stable enough for a month


def _bbox_hash(bbox: BoundingBox) -> str:
    key = f"{bbox.min_lon:.4f},{bbox.min_lat:.4f},{bbox.max_lon:.4f},{bbox.max_lat:.4f}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _is_fresh(path: Path, max_age_days: int = CACHE_MAX_AGE_DAYS) -> bool:
    if not path.exists():
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400
    return age_days < max_age_days


def _overpass_query(query: str, timeout: int = 60) -> dict:
    """Execute an Overpass API query and return parsed JSON."""
    resp = requests.post(
        OVERPASS_URL,
        data={"data": query},
        timeout=timeout + 10,
        headers={"User-Agent": "para-bellum-cartographer/0.1 (game dev pipeline)"},
    )
    resp.raise_for_status()
    return resp.json()


def _element_to_geometry(element: dict):
    """Convert an Overpass element to a Shapely geometry, or None."""
    etype = element.get("type")

    if etype == "node":
        lat = element.get("lat")
        lon = element.get("lon")
        if lat is not None and lon is not None:
            return Point(lon, lat)

    elif etype in ("way", "relation"):
        # Ways with geometry (when [out:geom] is used)
        geometry = element.get("geometry")
        if geometry:
            coords = [(g["lon"], g["lat"]) for g in geometry if "lon" in g and "lat" in g]
            if len(coords) >= 2:
                if coords[0] == coords[-1] and len(coords) >= 4:
                    return Polygon(coords)
                return LineString(coords)

        # Center point fallback
        center = element.get("center")
        if center:
            return Point(center["lon"], center["lat"])

    return None


class OSMDownloader:
    """Fetch and cache OSM data layers for Para Bellum hex pipeline."""

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Landuse / natural cover
    # ------------------------------------------------------------------

    def get_landuse(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Fetch OSM landuse and natural area polygons."""
        cache_path = self.cache_dir / f"landuse_{_bbox_hash(bbox)}.gpkg"
        if _is_fresh(cache_path):
            return gpd.read_file(cache_path)
    
        console.print("  Fetching OSM landuse/natural areas...", style="dim")

        b = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
        # Fetch only the most strategically relevant landuse types
        # Use 'out body' (no geometry) then separate geometry fetch for efficiency
        query = f"""
    [out:json][timeout:120];
    (
      way["natural"~"^(wood|wetland|sand|glacier|beach)$"]({b});
      way["landuse"~"^(forest|farmland|residential|industrial|wetland|military)$"]({b});
      relation["natural"~"^(wood|wetland)$"]({b});
      relation["landuse"~"^(forest|residential|industrial)$"]({b});
    );
    out geom qt;
    """
        try:
            data = _overpass_query(query, timeout=120)
        except Exception as e:
            console.print(f"  [yellow]Landuse fetch failed: {e}[/yellow]")
            return gpd.GeoDataFrame(
                columns=["geometry", "landuse_type"], crs="EPSG:4326"
            )

        records = []
        for el in data.get("elements", []):
            geom = _element_to_geometry(el)
            if geom is None:
                continue
            tags = el.get("tags", {})
            landuse_type = _classify_landuse_tag(tags)
            if landuse_type is None:
                continue
            records.append({"geometry": geom, "landuse_type": landuse_type})

        if not records:
            console.print("  [yellow]Landuse: no polygons returned[/yellow]")
            return gpd.GeoDataFrame(
                columns=["geometry", "landuse_type"], crs="EPSG:4326"
            )

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        gdf = gdf[gdf.geometry.geom_type.isin(
            ["Polygon", "MultiPolygon"]
        )].reset_index(drop=True)

        console.print(
            f"  Landuse: {len(gdf)} polygons "
            f"({gdf.landuse_type.value_counts().to_dict()})",
            style="dim"
        )

        if not gdf.empty:
            gdf.to_file(cache_path, driver="GPKG")

        return gdf

    # ------------------------------------------------------------------
    # Settlements (place nodes)
    # ------------------------------------------------------------------

    def get_settlements(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Fetch OSM place nodes (cities, towns, villages, hamlets).

        Returns GeoDataFrame with columns:
            geometry, name, place_type, population
        where place_type is: city | town | village | hamlet
        """
        cache_path = self.cache_dir / f"settlements_{_bbox_hash(bbox)}.gpkg"
        if _is_fresh(cache_path):
            return gpd.read_file(cache_path)

        console.print("  Fetching OSM settlements (place nodes)...", style="dim")

        b = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
        query = f"""
[out:json][timeout:60];
(
  node["place"~"^(city|town|village)$"]({b});
);
out body;
"""
        try:
            data = _overpass_query(query, timeout=60)
        except Exception as e:
            console.print(f"  [yellow]Settlement fetch failed: {e}[/yellow]")
            return gpd.GeoDataFrame(columns=["geometry", "name", "place_type", "population"], crs="EPSG:4326")

        records = []
        for el in data.get("elements", []):
            geom = _element_to_geometry(el)
            if geom is None:
                continue
            tags = el.get("tags", {})
            place = tags.get("place", "village")

            # Normalise to our enum
            place_type = {
                "city":    "city",
                "town":    "town",
                "village": "village",
                "hamlet":  "village",   # hamlet → village tier
                "suburb":  "town",      # suburb → town tier
                "borough": "town",
            }.get(place, "village")

            try:
                pop = int(tags.get("population", 0))
            except (ValueError, TypeError):
                pop = 0

            # Drop hamlets and low-population villages
            if place in ("hamlet", "suburb", "borough"):
                continue
            if place == "village" and 0 < pop < 500:
                continue

            records.append({
                "geometry":   geom,
                "name":       tags.get("name", tags.get("name:en", "")),
                "place_type": place_type,
                "population": pop,
            })

        if not records:
            return gpd.GeoDataFrame(columns=["geometry", "name", "place_type", "population"], crs="EPSG:4326")

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        gdf.to_file(cache_path, driver="GPKG")
        return gdf

    # ------------------------------------------------------------------
    # Roads
    # ------------------------------------------------------------------

    def get_roads(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Fetch OSM highway linestrings.

        Returns GeoDataFrame with columns: geometry, road_level
        where road_level is: highway | paved | dirt
        """
        cache_path = self.cache_dir / f"roads_{_bbox_hash(bbox)}.gpkg"
        if _is_fresh(cache_path):
            return gpd.read_file(cache_path)

        console.print("  Fetching OSM roads...", style="dim")

        b = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
        query = f"""
[out:json][timeout:90];
(
  way["highway"~"^(motorway|trunk|primary|secondary|motorway_link|trunk_link|primary_link|secondary_link)$"]({b});
);
out geom;
"""
        try:
            data = _overpass_query(query, timeout=90)
        except Exception as e:
            console.print(f"  [yellow]Road fetch failed: {e}[/yellow]")
            return gpd.GeoDataFrame(columns=["geometry", "road_level"], crs="EPSG:4326")

        records = []
        for el in data.get("elements", []):
            geom = _element_to_geometry(el)
            if geom is None or geom.geom_type != "LineString":
                continue
            tags = el.get("tags", {})
            hw = tags.get("highway", "")
            road_level = _classify_road(hw)
            records.append({"geometry": geom, "road_level": road_level})

        if not records:
            return gpd.GeoDataFrame(columns=["geometry", "road_level"], crs="EPSG:4326")

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        gdf.to_file(cache_path, driver="GPKG")
        return gdf

    # ------------------------------------------------------------------
    # Railways
    # ------------------------------------------------------------------

    def get_railways(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Fetch OSM railway linestrings.

        Returns GeoDataFrame with columns: geometry, rail_level
        where rail_level is: double | standard | narrow
        """
        cache_path = self.cache_dir / f"railways_{_bbox_hash(bbox)}.gpkg"
        if _is_fresh(cache_path):
            return gpd.read_file(cache_path)

        console.print("  Fetching OSM railways...", style="dim")

        b = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
        query = f"""
[out:json][timeout:60];
(
  way["railway"~"^(rail|narrow_gauge|light_rail|subway|tram)$"]({b});
);
out geom;
"""
        try:
            data = _overpass_query(query, timeout=60)
        except Exception as e:
            console.print(f"  [yellow]Railway fetch failed: {e}[/yellow]")
            return gpd.GeoDataFrame(columns=["geometry", "rail_level"], crs="EPSG:4326")

        records = []
        for el in data.get("elements", []):
            geom = _element_to_geometry(el)
            if geom is None or geom.geom_type != "LineString":
                continue
            tags = el.get("tags", {})
            railway = tags.get("railway", "rail")
            tracks = int(tags.get("tracks", 1))
            gauge = tags.get("gauge", "1435")  # standard gauge in mm

            # Classify
            if railway == "narrow_gauge" or (gauge.isdigit() and int(gauge) < 1000):
                rail_level = "narrow"
            elif tracks >= 2:
                rail_level = "double"
            else:
                rail_level = "standard"

            records.append({"geometry": geom, "rail_level": rail_level})

        if not records:
            return gpd.GeoDataFrame(columns=["geometry", "rail_level"], crs="EPSG:4326")

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        gdf.to_file(cache_path, driver="GPKG")
        return gdf

    # ------------------------------------------------------------------
    # Waterways (for river_edges)
    # ------------------------------------------------------------------

    # Strategic-significance floor for waterways: a named river/canal only
    # appears on the map when its total named length within the fetched
    # area exceeds this (geodesic metres). OSM tags 2 m brooks as
    # waterway=river, so tag-level filters can't separate the Meuse from a
    # drainage ditch — but majors accumulate >100 km of named ways while
    # brooks total a few km. Globally scalable: no name lists, no
    # bbox-specific logic. Calibrated on the Belgium test bbox: keeps
    # Meuse/Maas, Schelde, Sambre, Ourthe, Albertkanaal, Oise, Semois,
    # Chiers → 71/280 river hexes (25%), zero isolated river hexes.
    MIN_WATERWAY_TOTAL_M = 110_000

    def get_waterways(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Fetch OSM waterway linestrings (rivers, canals) of strategic size.

        Returns GeoDataFrame with columns: geometry, waterway_type, name
        where waterway_type is: river | canal

        Only named waterways whose per-name total geodesic length within the
        bbox exceeds MIN_WATERWAY_TOTAL_M are kept — at 10 km hex scale a
        river edge must represent a real crossing obstacle, not every named
        stream Belgium drains through.
        """
        cache_path = self.cache_dir / f"waterways_{_bbox_hash(bbox)}.gpkg"
        if _is_fresh(cache_path):
            return gpd.read_file(cache_path)

        console.print("  Fetching OSM waterways...", style="dim")

        b = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
        query = f"""
[out:json][timeout:120];
(
  way["waterway"="river"]["name"]({b});
  way["waterway"="canal"]["name"]({b});
);
out geom;
"""
        try:
            data = _overpass_query(query, timeout=120)
        except Exception as e:
            console.print(f"  [yellow]Waterway fetch failed: {e}[/yellow]")
            return gpd.GeoDataFrame(columns=["geometry", "waterway_type", "name"], crs="EPSG:4326")

        records = []
        for el in data.get("elements", []):
            geom = _element_to_geometry(el)
            if geom is None or geom.geom_type not in ("LineString", "MultiLineString"):
                continue
            tags = el.get("tags", {})
            records.append({
                "geometry":      geom,
                "waterway_type": tags.get("waterway", "river"),
                "name":          tags.get("name", ""),
            })

        if not records:
            return gpd.GeoDataFrame(columns=["geometry", "waterway_type", "name"], crs="EPSG:4326")

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")

        # Significance filter: per-name total geodesic length. Geodesic (not
        # EPSG:3857) so the threshold means the same thing at any latitude.
        from pyproj import Geod
        geod = Geod(ellps="WGS84")
        lengths = gdf.geometry.apply(geod.geometry_length)
        name_total = lengths.groupby(gdf["name"]).sum()
        keep_names = set(name_total[name_total > self.MIN_WATERWAY_TOTAL_M].index)
        kept = gdf[gdf["name"].isin(keep_names)].reset_index(drop=True)

        console.print(
            f"  Waterways: {len(gdf)} ways / {gdf['name'].nunique()} names fetched; "
            f"kept {len(kept)} ways / {len(keep_names)} names over "
            f"{self.MIN_WATERWAY_TOTAL_M / 1000:.0f} km total "
            f"({sorted(keep_names)})",
            style="dim",
        )
        gdf = kept

        if gdf.empty:
            return gpd.GeoDataFrame(columns=["geometry", "waterway_type", "name"], crs="EPSG:4326")

        gdf.to_file(cache_path, driver="GPKG")
        return gdf

    # ------------------------------------------------------------------
    # Bridges
    # ------------------------------------------------------------------

    def get_bridges(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Fetch OSM bridge nodes/ways crossing waterways.

        Returns GeoDataFrame with columns: geometry (Point at bridge center)
        Used by sampler to set infrastructure.bridge = true on river hexes.
        """
        cache_path = self.cache_dir / f"bridges_{_bbox_hash(bbox)}.gpkg"
        if _is_fresh(cache_path):
            return gpd.read_file(cache_path)

        console.print("  Fetching OSM bridges...", style="dim")

        b = f"{bbox.min_lat},{bbox.min_lon},{bbox.max_lat},{bbox.max_lon}"
        query = f"""
[out:json][timeout:60];
(
  way["bridge"="yes"]["highway"]({b});
  way["bridge"="yes"]["railway"]({b});
);
out center;
"""
        try:
            data = _overpass_query(query, timeout=60)
        except Exception as e:
            console.print(f"  [yellow]Bridge fetch failed: {e}[/yellow]")
            return gpd.GeoDataFrame(columns=["geometry", "name"], crs="EPSG:4326")

        records = []
        for el in data.get("elements", []):
            center = el.get("center")
            if center:
                tags = el.get("tags", {})
                records.append({
                    "geometry": Point(center["lon"], center["lat"]),
                    "name": tags.get("name", ""),
                })

        if not records:
            return gpd.GeoDataFrame(columns=["geometry", "name"], crs="EPSG:4326")

        gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
        gdf.to_file(cache_path, driver="GPKG")
        return gdf


# ---------------------------------------------------------------------------
# Tag classification helpers
# ---------------------------------------------------------------------------

def _classify_landuse_tag(tags: dict) -> str | None:
    """Map OSM tags to our landuse_type string. Returns None to skip."""
    landuse = tags.get("landuse", "")
    natural = tags.get("natural", "")

    # Natural tags take priority for terrain classification
    natural_map = {
        "wood":      "forest",
        "wetland":   "wetland",
        "heath":     "heath",
        "scrub":     "scrub",
        "grassland": "grass",
        "sand":      "sand",
        "bare_rock": "sand",   # rocky bare ground → same as sand for our purposes
        "glacier":   "glacier",
        "beach":     "beach",
        "water":     "water",
    }
    if natural in natural_map:
        return natural_map[natural]

    landuse_map = {
        "forest":      "forest",
        "farmland":    "farmland",
        "farmyard":    "farmland",
        "meadow":      "grass",
        "grass":       "grass",
        "village_green": "grass",
        "allotments":  "farmland",
        "residential": "residential",
        "industrial":  "industrial",
        "commercial":  "residential",
        "retail":      "residential",
        "military":    "military",
        "quarry":      "quarry",
        "wetland":     "wetland",
    }
    if landuse in landuse_map:
        return landuse_map[landuse]

    return None


def _classify_road(highway_tag: str) -> str:
    """Map OSM highway tag to road_level string."""
    if highway_tag in ("motorway", "trunk", "motorway_link", "trunk_link"):
        return "highway"
    if highway_tag in ("primary", "secondary", "primary_link", "secondary_link"):
        return "paved"
    return "none"