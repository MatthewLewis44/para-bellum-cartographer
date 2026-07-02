"""OSM data downloader for Para Bellum pipeline.

Fetches landuse, settlements, roads, railways, waterways, and bridges from
the Overpass API. All results are cached to disk keyed by bbox hash.

Large bboxes are split into a grid of sub-bbox queries (Overpass times out
on country-scale requests): each sub-bbox is fetched separately with delays
between queries, retried with exponential backoff on 504/timeout, cached
individually (partial failures don't lose completed parts), then merged
with OSM-id deduplication for ways that span sub-bbox seams. Because the
sub-bboxes tile the full bbox exactly and Overpass matches elements with
at least one node inside the filter box, the merged result is a superset
of what the single query would return — no features are lost at seams.

This module extends the upstream downloader.py — it does not replace it.
The upstream DataDownloader handles Natural Earth + basic OSM ports.
This module handles the additional OSM layers Para Bellum needs.

OUT OF SCOPE (not fetched here):
    - 1930 political boundaries (see geo/boundaries.py)
    - Strategic resources (manual GeoJSON overlay, Sprint 3)
    - Historical WW2 boundary overrides (Sprint 3)
"""

from __future__ import annotations

import hashlib
import math
import time
from pathlib import Path
from typing import Callable

import geopandas as gpd
import pandas as pd
import requests
from rich.console import Console
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.ops import unary_union

from wargame_cartographer.config.map_spec import BoundingBox

console = Console()

DEFAULT_CACHE_DIR = Path.home() / "wargame-cartographer" / "cache" / "osm_pb"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
CACHE_MAX_AGE_DAYS = 30  # OSM data is stable enough for a month

# --- Sub-bbox splitting (AD-008) -------------------------------------------
# A query bbox with any edge longer than this is split into a grid of
# sub-bboxes no larger than this edge. 2.2 deg keeps each sub-query well
# under the Belgium test bbox (3.9 x 2.0 deg) that Overpass handled in one
# shot, with margin for denser regions (Ruhr, Randstad).
MAX_QUERY_EDGE_DEG = 2.2
# Delay between consecutive live sub-queries — be a good citizen to the
# free Overpass instance.
SUBQUERY_DELAY_S = 3.0
# Backoff schedule for retries on 504 / 429 / timeout.
RETRY_DELAYS_S = (10, 30, 90)


def _bbox_hash(bbox: BoundingBox) -> str:
    key = f"{bbox.min_lon:.4f},{bbox.min_lat:.4f},{bbox.max_lon:.4f},{bbox.max_lat:.4f}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _is_fresh(path: Path, max_age_days: int = CACHE_MAX_AGE_DAYS) -> bool:
    if not path.exists():
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400
    return age_days < max_age_days


def _split_bbox(bbox: BoundingBox, max_edge_deg: float = MAX_QUERY_EDGE_DEG) -> list[BoundingBox]:
    """Split a bbox into a grid of sub-bboxes with edges <= max_edge_deg.

    Returns [bbox] unchanged when no split is needed. Sub-bboxes tile the
    original exactly (shared edges, no gaps, no overlap).
    """
    lon_span = bbox.max_lon - bbox.min_lon
    lat_span = bbox.max_lat - bbox.min_lat
    nx = max(1, math.ceil(lon_span / max_edge_deg))
    ny = max(1, math.ceil(lat_span / max_edge_deg))
    if nx * ny <= 1:
        return [bbox]

    subs = []
    for ix in range(nx):
        for iy in range(ny):
            subs.append(BoundingBox(
                min_lon=bbox.min_lon + lon_span * ix / nx,
                max_lon=bbox.min_lon + lon_span * (ix + 1) / nx,
                min_lat=bbox.min_lat + lat_span * iy / ny,
                max_lat=bbox.min_lat + lat_span * (iy + 1) / ny,
            ))
    return subs


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


def _overpass_query_retry(query: str, timeout: int = 60) -> dict:
    """Overpass query with exponential backoff on rate-limit/timeout errors.

    Retries on HTTP 429/502/503/504 and request timeouts; other errors
    (e.g. 400 bad query) raise immediately.
    """
    last_exc: Exception | None = None
    for attempt, delay in enumerate((0,) + RETRY_DELAYS_S):
        if delay:
            console.print(
                f"  [yellow]Overpass busy — retry {attempt}/{len(RETRY_DELAYS_S)} "
                f"in {delay}s[/yellow]"
            )
            time.sleep(delay)
        try:
            return _overpass_query(query, timeout=timeout)
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status not in (429, 502, 503, 504):
                raise
            last_exc = e
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
    raise last_exc  # all retries exhausted


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

    def __init__(self, cache_dir: Path | None = None):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Generic split-fetch-merge driver
    # ------------------------------------------------------------------

    def part_descriptors(self, bbox: BoundingBox, layer: str) -> list[tuple[BoundingBox, Path]]:
        """Return [(sub_bbox, part_path)] for a layer's sub-bbox part gpkgs.

        Mirrors the naming used by ``_fetch_layer``: ``{layer}_part_<hash>.gpkg``
        when the bbox splits, else the single ``{layer}_<hash>.gpkg``. Only
        existing part files are returned. Used by the streaming pipeline (T2) to
        read each layer per tile from the parts without ever merging the whole
        layer into RAM.
        """
        subs = _split_bbox(bbox)
        out: list[tuple[BoundingBox, Path]] = []
        if len(subs) == 1:
            p = self.cache_dir / f"{layer}_{_bbox_hash(bbox)}.gpkg"
            if p.exists():
                out.append((bbox, p))
            return out
        for sub in subs:
            p = self.cache_dir / f"{layer}_part_{_bbox_hash(sub)}.gpkg"
            if p.exists():
                out.append((sub, p))
        return out

    def _fetch_layer(
        self,
        bbox: BoundingBox,
        layer: str,
        build_query: Callable[[str], str],
        parse_element: Callable[[dict], dict | None],
        columns: list[str],
        timeout: int = 120,
        merge: bool = True,
    ) -> gpd.GeoDataFrame:
        """Fetch one OSM layer, splitting the bbox into sub-queries if large.

        build_query(b)     — Overpass QL for bbox string "minlat,minlon,maxlat,maxlon"
        parse_element(el)  — element → record dict (without osm_id) or None to skip
        columns            — record columns (excl. geometry/osm_id), for empty frames

        Each sub-bbox result is cached individually (layer_part_<hash>.gpkg,
        or a .empty marker), so retries resume instead of refetching. The
        merged, osm_id-deduplicated result is cached under the full-bbox key.

        ``merge=False`` (streaming, T2): only ensure each sub-bbox part is
        fetched+cached; do NOT accumulate parts in memory or build the merged
        layer (that materialization is the 30 GB wall this sprint kills). The
        caller then reads parts per tile via ``part_descriptors`` + a bbox read.
        """
        all_cols = ["geometry", "osm_id"] + columns
        empty = gpd.GeoDataFrame(columns=all_cols, crs="EPSG:4326")

        cache_path = self.cache_dir / f"{layer}_{_bbox_hash(bbox)}.gpkg"
        if merge and _is_fresh(cache_path):
            return gpd.read_file(cache_path)

        subs = _split_bbox(bbox)
        if len(subs) > 1:
            console.print(
                f"  Fetching OSM {layer} in {len(subs)} sub-bbox queries...",
                style="dim",
            )

        frames: list[gpd.GeoDataFrame] = []
        live_fetches = 0
        failed = False  # any sub-bbox fetch raised (AD-030 cache integrity)
        for i, sub in enumerate(subs):
            if len(subs) == 1:
                part_path = cache_path
                marker = cache_path.with_suffix(".empty")
            else:
                part_path = self.cache_dir / f"{layer}_part_{_bbox_hash(sub)}.gpkg"
                marker = self.cache_dir / f"{layer}_part_{_bbox_hash(sub)}.empty"

            if _is_fresh(part_path):
                if merge:
                    frames.append(gpd.read_file(part_path))
                continue
            if _is_fresh(marker):
                continue

            if live_fetches > 0:
                time.sleep(SUBQUERY_DELAY_S)
            live_fetches += 1
            if len(subs) > 1:
                console.print(
                    f"    {layer} part {i + 1}/{len(subs)} "
                    f"({sub.min_lon:.2f},{sub.min_lat:.2f} → "
                    f"{sub.max_lon:.2f},{sub.max_lat:.2f})...",
                    style="dim",
                )
            elif layer != "waterways":
                console.print(f"  Fetching OSM {layer}...", style="dim")

            b = f"{sub.min_lat},{sub.min_lon},{sub.max_lat},{sub.max_lon}"
            try:
                data = _overpass_query_retry(build_query(b), timeout=timeout)
            except Exception as e:
                console.print(f"  [yellow]{layer} fetch failed: {e}[/yellow]")
                # Leave no cache for this part — next run retries it.
                failed = True
                continue

            records = []
            for el in data.get("elements", []):
                rec = parse_element(el)
                if rec is None:
                    continue
                rec["osm_id"] = f"{el.get('type', '?')}/{el.get('id', '?')}"
                records.append(rec)

            if not records:
                marker.touch()
                continue

            part_gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
            part_gdf.to_file(part_path, driver="GPKG")
            if merge:
                frames.append(part_gdf)
            else:
                del part_gdf  # streaming: don't hold parts in RAM

        def _build_merged() -> gpd.GeoDataFrame:
            if not frames:
                return empty
            if len(subs) == 1:
                return frames[0]
            m = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
            if "osm_id" in m.columns:
                m = m.drop_duplicates(subset="osm_id").reset_index(drop=True)
            return m

        # Streaming (merge=False): parts are cached; caller reads them per tile.
        # A failed part must NOT be swallowed — the tile sampler would silently
        # sample a hole. Fail loud so the run aborts and retries (AD-030).
        if not merge:
            if failed:
                raise RuntimeError(
                    f"{layer}: a sub-bbox fetch failed — cached parts are "
                    f"incomplete. Refusing to sample a hole (AD-030 fail-loud); "
                    f"re-run to retry the failed part(s)."
                )
            return empty

        if failed:
            # A part failed: return what we have for THIS run but do NOT write the
            # merged full-bbox cache — otherwise the missing part is never retried
            # while the merged cache is fresh (the poisoning bug, AD-030).
            console.print(
                f"  [red]{layer}: sub-bbox fetch failed — NOT caching the merged "
                f"result so the failed part(s) retry next run.[/red]"
            )
            return _build_merged()  # deliberately NOT persisted

        merged = _build_merged()
        if not merged.empty and len(subs) > 1:
            merged.to_file(cache_path, driver="GPKG")
            console.print(
                f"  {layer}: {len(merged)} features merged from {len(subs)} sub-bboxes",
                style="dim",
            )
        return merged

    # ------------------------------------------------------------------
    # Landuse / natural cover
    # ------------------------------------------------------------------

    def get_landuse(self, bbox: BoundingBox, merge: bool = True) -> gpd.GeoDataFrame:
        """Fetch OSM landuse and natural area polygons."""

        def build_query(b: str) -> str:
            # Fetch only the most strategically relevant landuse types
            return f"""
[out:json][timeout:180];
(
  way["natural"~"^(wood|wetland|sand|glacier|beach)$"]({b});
  way["landuse"~"^(forest|farmland|residential|industrial|wetland|military)$"]({b});
  relation["natural"~"^(wood|wetland)$"]({b});
  relation["landuse"~"^(forest|residential|industrial)$"]({b});
);
out geom qt;
"""

        def parse_element(el: dict) -> dict | None:
            geom = _element_to_geometry(el)
            if geom is None or geom.geom_type not in ("Polygon", "MultiPolygon"):
                return None
            landuse_type = _classify_landuse_tag(el.get("tags", {}))
            if landuse_type is None:
                return None
            return {"geometry": geom, "landuse_type": landuse_type}

        gdf = self._fetch_layer(
            bbox, "landuse", build_query, parse_element,
            columns=["landuse_type"], timeout=180, merge=merge,
        )
        if not gdf.empty:
            console.print(
                f"  Landuse: {len(gdf)} polygons "
                f"({gdf.landuse_type.value_counts().to_dict()})",
                style="dim",
            )
        return gdf

    # ------------------------------------------------------------------
    # Settlements (place nodes)
    # ------------------------------------------------------------------

    def get_settlements(self, bbox: BoundingBox) -> gpd.GeoDataFrame:
        """Fetch OSM place nodes (cities, towns, villages).

        Returns GeoDataFrame with columns:
            geometry, name, place_type, population
        """

        def build_query(b: str) -> str:
            return f"""
[out:json][timeout:90];
(
  node["place"~"^(city|town|village)$"]({b});
);
out body;
"""

        def parse_element(el: dict) -> dict | None:
            geom = _element_to_geometry(el)
            if geom is None:
                return None
            tags = el.get("tags", {})
            place = tags.get("place", "village")

            place_type = {
                "city":    "city",
                "town":    "town",
                "village": "village",
            }.get(place, "village")

            try:
                pop = int(tags.get("population", 0))
            except (ValueError, TypeError):
                pop = 0

            # Drop low-population villages (unknown population passes; the
            # sampler's significance floors handle those downstream).
            if place == "village" and 0 < pop < 500:
                return None

            return {
                "geometry":   geom,
                "name":       tags.get("name", tags.get("name:en", "")),
                "place_type": place_type,
                "population": pop,
            }

        return self._fetch_layer(
            bbox, "settlements", build_query, parse_element,
            columns=["name", "place_type", "population"], timeout=90,
        )

    # ------------------------------------------------------------------
    # Roads
    # ------------------------------------------------------------------

    def get_roads(self, bbox: BoundingBox, merge: bool = True) -> gpd.GeoDataFrame:
        """Fetch OSM highway linestrings.

        Returns GeoDataFrame with columns: geometry, road_level
        where road_level is: highway | paved
        """

        def build_query(b: str) -> str:
            return f"""
[out:json][timeout:120];
(
  way["highway"~"^(motorway|trunk|primary|secondary|motorway_link|trunk_link|primary_link|secondary_link)$"]({b});
);
out geom;
"""

        def parse_element(el: dict) -> dict | None:
            geom = _element_to_geometry(el)
            if geom is None or geom.geom_type != "LineString":
                return None
            hw = el.get("tags", {}).get("highway", "")
            return {"geometry": geom, "road_level": _classify_road(hw)}

        return self._fetch_layer(
            bbox, "roads", build_query, parse_element,
            columns=["road_level"], timeout=120, merge=merge,
        )

    # ------------------------------------------------------------------
    # Railways
    # ------------------------------------------------------------------

    def get_railways(self, bbox: BoundingBox, merge: bool = True) -> gpd.GeoDataFrame:
        """Fetch OSM railway linestrings.

        Returns GeoDataFrame with columns: geometry, rail_level
        where rail_level is: double | standard | narrow
        """

        def build_query(b: str) -> str:
            return f"""
[out:json][timeout:90];
(
  way["railway"~"^(rail|narrow_gauge|light_rail|subway|tram)$"]({b});
);
out geom;
"""

        def parse_element(el: dict) -> dict | None:
            geom = _element_to_geometry(el)
            if geom is None or geom.geom_type != "LineString":
                return None
            tags = el.get("tags", {})
            railway = tags.get("railway", "rail")
            try:
                tracks = int(tags.get("tracks", 1))
            except (ValueError, TypeError):
                tracks = 1
            gauge = tags.get("gauge", "1435")

            if railway == "narrow_gauge" or (gauge.isdigit() and int(gauge) < 1000):
                rail_level = "narrow"
            elif tracks >= 2:
                rail_level = "double"
            else:
                rail_level = "standard"

            return {"geometry": geom, "rail_level": rail_level}

        return self._fetch_layer(
            bbox, "railways", build_query, parse_element,
            columns=["rail_level"], timeout=90, merge=merge,
        )

    # ------------------------------------------------------------------
    # Waterways (for river_edges)
    # ------------------------------------------------------------------

    def get_waterways(self, bbox: BoundingBox, merge: bool = True) -> gpd.GeoDataFrame:
        """Fetch OSM waterway linestrings (rivers, canals) of strategic size.

        Returns GeoDataFrame with columns: geometry, waterway_type, name
        where waterway_type is: river | canal

        Only named waterways whose per-name total geodesic length within the
        bbox exceeds MIN_WATERWAY_TOTAL_M are kept — at 10 km hex scale a
        river edge must represent a real crossing obstacle, not every named
        stream the region drains through. The filter runs at load time on
        the merged (full-bbox) data so per-name totals span sub-bbox seams;
        re-filtering already-filtered cached data is a no-op.

        ``merge=False``: only ensure the raw (unfiltered) parts are cached and
        return empty. Since AD-029, river SELECTION is Natural Earth scalerank
        (``geo.rivers_global.compute_selected_rivers``); this getter's role is to
        cache the OSM waterway parts, from which the AD-029 canal pass selects
        ``waterway=canal`` ways by per-name geodesic length (the AD-011 length
        utility, retained for canals only). The merged AD-011 *river* filter
        below is superseded and no longer feeds selection.
        """

        def build_query(b: str) -> str:
            return f"""
[out:json][timeout:120];
(
  way["waterway"="river"]["name"]({b});
  way["waterway"="canal"]["name"]({b});
);
out geom;
"""

        def parse_element(el: dict) -> dict | None:
            geom = _element_to_geometry(el)
            if geom is None or geom.geom_type not in ("LineString", "MultiLineString"):
                return None
            tags = el.get("tags", {})
            return {
                "geometry":      geom,
                "waterway_type": tags.get("waterway", "river"),
                "name":          tags.get("name", ""),
            }

        console.print("  Fetching OSM waterways...", style="dim")
        # AD-029/AD-030: the AD-011 per-name geodesic-length RIVER significance
        # filter that used to run here (the merge=True branch) is RETIRED — it was
        # unreachable, both production callers pass merge=False (streaming reads
        # parts per tile; rivers_global runs the canal pass). River SELECTION is
        # now Natural Earth scalerank (geo/rivers_global.compute_selected_rivers).
        # MIN_WATERWAY_TOTAL_M survives — the canal pass reuses it.
        return self._fetch_layer(
            bbox, "waterways", build_query, parse_element,
            columns=["waterway_type", "name"], timeout=120, merge=merge,
        )

    # ------------------------------------------------------------------
    # Bridges
    # ------------------------------------------------------------------

    def get_bridges(self, bbox: BoundingBox, merge: bool = True) -> gpd.GeoDataFrame:
        """Fetch OSM bridge ways crossing waterways (as center points).

        Returns GeoDataFrame with columns: geometry (Point), name
        Used by sampler to set infrastructure.bridge = true on river hexes.
        """

        def build_query(b: str) -> str:
            return f"""
[out:json][timeout:90];
(
  way["bridge"="yes"]["highway"]({b});
  way["bridge"="yes"]["railway"]({b});
);
out center;
"""

        def parse_element(el: dict) -> dict | None:
            center = el.get("center")
            if not center:
                return None
            return {
                "geometry": Point(center["lon"], center["lat"]),
                "name": el.get("tags", {}).get("name", ""),
            }

        return self._fetch_layer(
            bbox, "bridges", build_query, parse_element,
            columns=["name"], timeout=90, merge=merge,
        )

    # ------------------------------------------------------------------
    # Streaming support (T2): ensure parts cached without merging
    # ------------------------------------------------------------------

    def ensure_parts(self, bbox: BoundingBox) -> None:
        """Fetch + cache each tile-local layer's sub-bbox parts WITHOUT merging.

        The streaming pipeline then reads parts per tile via ``part_descriptors``
        + a bbox-filtered read, so the full layer is never materialized in RAM.
        Settlements and waterways stay global (handled by the caller).
        """
        for getter in (self.get_landuse, self.get_roads,
                       self.get_railways, self.get_bridges):
            getter(bbox, merge=False)


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
