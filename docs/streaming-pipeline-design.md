# Streaming / Tiled Pipeline — Design (Sprint 4)

**Goal:** process any bbox at peak RAM **< 4 GB per tile / < 6 GB global**, output
**hex-for-hex identical** to the Sprint 3 monolithic pipeline. Refactor only — no
schema change (v1.0.2 locked), no new features, no cross-tile parallelism.

## The wall

At the Benelux+DE bbox (2,479 hexes) peak RAM is **30.4 GB**, dominated by two
all-in-memory structures during the sampling stage:

- **OSM landuse** — 2.24 M Shapely polygons in one GeoDataFrame (~several GB).
- **SRTM elevation** — 35 tiles merged into a 15120×22680 raster (~1.4 GB) plus
  an equal-size slope array.

Linear extrapolation to full Europe (~100k hexes) → ~360 GB. Min-spec ship
hardware is 16 GB. This blocks any eastward/bbox growth.

## Key enablers (verified)

1. **`get_elevation(tile_bbox)` already tiles SRTM.** It selects only the 1° SRTM
   tiles touching the bbox and merges them. Called per ~1° map-tile it loads
   1–4 SRTM tiles → a few MB raster. SRTM is essentially free to stream.
2. **`geopandas.read_file(gpkg, bbox=...)` with pyogrio (0.12.1, installed) spatial-
   filters at the OGR layer.** Measured on the 1.15 GB Benelux landuse gpkg: a
   full read is 2.24 M features in 10 s; a 1°-tile bbox read is **133 k features
   in 0.8 s**. So we keep the existing full-bbox cached gpkgs and read only each
   tile's slice — no re-fetch, no whole-layer materialization.

## Stage audit

| Stage | Mem cost | Scope | Depends on | Output |
|---|---|---|---|---|
| Hex grid build | low (coords only) | **GLOBAL** (built once) | spec.bbox | hex centers/coords |
| NE land / lakes / ports | low (coarse polys) | **GLOBAL** (small; prep once) | bbox | water/lake/port flags |
| OSM **landuse** | **HIGH** (2.24 M polys) | **TILE** (bbox read) | tile+margin | biome / anthrome / agriculture |
| OSM **roads** | med (640 k lines) | **TILE** (bbox read) | tile+margin | road level |
| OSM **rails** | med (162 k lines) | **TILE** (bbox read) | tile+margin | rail level |
| OSM **bridges** | low-med (241 k pts) | **TILE** (bbox read) | tile+margin | bridge flag |
| OSM settlements | low (13 k nodes) | **GLOBAL** (cheap) | bbox | centroid hex + sprawl seeds |
| OSM waterways | med raw → tiny filtered | **GLOBAL pre-pass** (AD-011 needs whole-bbox per-name length) | bbox | allowed rivers + river_edges |
| SRTM **elevation/slope** | **HIGH** (343 M cells) | **TILE** (`get_elevation(tile)`) | tile bbox | elevation / slope |
| Boundaries 1930 | low (5 polys) | **GLOBAL** | — | country_at_start |
| Resources 1930 | tiny (12 feats) | **GLOBAL** | — | resources |
| Pass 1 (per-hex) | the result dict | **TILE** (tile's hexes) | all above | per-hex record |
| Pass 2 coastal flag | low | **GLOBAL reconcile** (neighbor water spans tiles) | all hex water flags | is_coastal + beach upgrade |
| Pass 3 urban sprawl | low | **GLOBAL reconcile** (footprint crosses tiles) | hex biome/landuse/settlement | suburb/parent_city/distance/anthrome |

## Global-vs-tile split (the load-bearing decision)

**Tile-local (per-hex, depends only on the hex location + nearby features):**
water/lake detection, elevation+slope, landuse→biome/veg/moisture, road/rail,
river_edges (using the global filtered-waterway set), bridge, port, settlement
centroid, country_at_start, resources. A **0.2° margin** on each tile's data read
guarantees every feature reaching a hex (polygon containing the center; road/
river intersecting the ~0.08° hex) is present, so tile sampling is byte-identical
to the monolithic run.

**Global (cross-tile aggregation, cannot be tile-local):**
1. **Waterway significance filter (AD-011)** — needs total geodesic length per
   river *name* across the whole bbox to decide the ≥110 km cut. Two-pass:
   pre-pass computes allowed names + keeps the (bounded) filtered geometries in
   RAM; per-tile `river_edges` then intersects hexes against that small set.
2. **Coastal flag (pass 2)** — a hex is coastal iff a *neighbor* is water; the
   neighbor can be in an adjacent tile. Recomputed in merge from all hexes'
   water flags (+ the PLAINS→BEACH upgrade that depends on it).
3. **Urban sprawl (pass 3, AD-020)** — a city footprint (esp. the Ruhr) crosses
   any reasonable tile boundary. Run in merge over the lightweight per-hex
   {biome, landuse_type, settlement_type, is_water} from all tiles + the global
   grid-neighbor structure.

## Execution order (streaming)

```
1. Build global hex grid (spec.bbox).
2. GLOBAL pre-pass A — waterways: fetch named river/canal ways for the whole
   bbox, group by name, geodesic length, keep names > 110 km (AD-011).
   Retain the filtered geometries in RAM (bounded — majors only).
3. GLOBAL small loads — NE land/lakes/ports (prep land/lake unions),
   boundaries_1930, resources_1930, settlements → settlement_by_hex + seeds.
4. TILE LOOP (serial, ~1°×1° tiles; a hex belongs to the tile containing its
   center):
     if tile output cached -> skip (resume; T5)
     load landuse/roads/rails/bridges slices (bbox = tile + 0.2° margin) and
       elevation = get_elevation(tile bbox)
     pass-1 sample the tile's hexes (is_coastal=False placeholder, no sprawl)
     enforce per-tile RAM budget (< 4 GB; T6)
     write per-tile output (parquet/JSON), discard all tile data
5. GLOBAL reconcile (merge):
     load all tile records
     pass-2 coastal flag + beach upgrade (global neighbor scan)
     pass-3 urban sprawl (global BFS, AD-020)
     export final JSON (game_data_exporter)
```

## Identical-output guarantee

The streaming path calls the **same** per-hex pass-1 code and the **same**
`_assign_coastal` / `_assign_urban_sprawl` functions as the monolithic path; only
the *data feeding* pass-1 is tiled (and proven equivalent by the 0.2° margin).
Validation T7 hex-diffs the streaming Belgium (775) and Benelux (2,479) outputs
against the committed Sprint 3 monolithic outputs — must match field-for-field
(JSON metadata timestamps excepted).

## Memory budget

- **Per tile** < 4 GB: dominated by the densest tile's landuse slice
  (~130–200 k polys ≈ a few hundred MB) + roads slice + elevation (< 50 MB) +
  that tile's hex records. Tracked with tracemalloc/psutil; fail loudly (T6).
- **Global** < 6 GB: grid coords + filtered waterways + NE land + settlements +
  (in merge) all hex records (lightweight dicts). For ~50k Europe hexes the hex
  records dominate and stay well under budget.

## Anti-goals honored

No cross-tile parallelism (serial = predictable RAM). No schema change. No
eastward bbox in this sprint (architecture supports it). Monolithic path kept
for fast Belgium iteration; both share pass code.

## As-built (Sprint 4) — deviations + panel resolutions

The adversarial design panel (T1b, 3 reviewers + synthesis) returned
**GO-WITH-FIXES** and confirmed the core global-vs-tile split. Applied changes:

- **Elevation is NOT cleanly tile-local** (panel's highest risk — confirmed by
  the first streaming run: 121 Belgium hexes differed on elevation/slope). A
  per-tile `merge(bounds=…)` produces a subtly different pixel grid than the
  monolithic full-bbox raster, so `sample_at_point`'s `round()` picks different
  pixels. **Fix:** for bboxes small enough to build a full DEM (≤100 SRTM
  tiles), per-tile elevation is a **windowed read of the cached full DEM**
  (`ElevationProcessor.get_elevation_window`) — exact same pixels → byte-
  identical elevation+slope. For Europe-scale bboxes (no full DEM, no monolithic
  baseline to match) a per-tile merge fallback is used. Result: streaming
  Belgium is now hex-for-hex identical.
- **Synthetic-elevation fail-loud** (`get_elevation(allow_synthetic=False)` in
  streaming): a silent sin/cos substitution for one tile would be cached and
  shipped, invisibly breaking output. Fixed the misleading "max 20" message.
- **Deterministic landuse tie-break**: `_sample_landuse` now returns the
  smallest-area containing polygon (most-specific), with landuse_type as the
  final tie-break — order-independent so a bbox-sliced read matches the full
  read (changed 0 hexes on Belgium; pure insurance).
- **Land/lake detection is tile-local** (read NE land clipped to tile+margin,
  prepped per tile) — avoids holding a continent-scale prepared land union
  globally at Europe scale (panel must-fix #4). Output-identical (center
  containment).
- **OSM parts, never merged**: `OSMDownloader.ensure_parts` /
  `part_descriptors` + `_fetch_layer(merge=False)` cache each 2.2° sub-bbox part
  without materializing the full layer; the tile loop reads parts with a
  bbox(+margin) pyogrio filter. This kills the *fetch-time* merge spike too, not
  just the sampling spike.
- **Waterway filter streamed** (`geo/waterways_global.py`): two passes over the
  cached parts — pass 1 accumulates per-name geodesic length (floats only),
  pass 2 keeps geometries only for names ≥110 km. Never materializes all
  unfiltered waterways. The filtered set (bounded) is passed WHOLE to every
  tile's `_river_edges_for_hex`.
- **Merge assembles in grid order**: records are reassembled in `grid.cells`
  iteration order (NOT tile order) before coastal/sprawl, so their exact-
  distance tie-breaks (Ruhr seams) are deterministic.
- **Intermediate tile format: pickle** — the per-hex record holds a `Biome`
  enum and lists; pickle round-trips them exactly (the final output is still
  JSON via the unchanged exporter). Tile cache key includes a
  `STREAMING_VERSION` stamp so a sampler change invalidates stale tiles.
- **Tile size: 1° integer-degree** with **0.2° margin** (≫ one hex circumradius
  ~0.06° + a few SRTM pixels). Margin bounds land on 0.1° multiples, matching
  the bbox grid.

## T10 — province readiness (P1 forward-compat)

Province assignment slots in with **zero further refactor**, exactly mirroring
`country_at_start`: load `provinces_1930.geojson` once as a small GLOBAL
GeoDataFrame, pass it to `build_hex_terrain` (like `boundaries_gdf`), and do a
per-hex point-in-polygon lookup in pass-1 (tile-local, using a prepared-geometry
+ sindex helper like `assign_country`). It is a global-input + tile-local-lookup
stage — the category the architecture already supports (boundaries, resources).
No coastal/sprawl-style global reconcile is needed. `provinces_1930_metadata.json`
loads alongside as plain JSON.
