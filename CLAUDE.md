# Para Bellum Cartography Pipeline

Python pipeline producing hex JSON for **Para Bellum**, a WW2 grand strategy game
(Unity 6). Forked from the upstream "wargame-cartographer" map renderer — the
renderer (PNG/PDF/HTML) is kept for debug/QA; the **JSON output is the product**.

## Quick Start

```bash
uv run wargame-map generate configs/para_bellum_belgium_test.yaml   # run pipeline
uv run python inspect_output.py                                     # general inspection
uv run python check_settlements.py                                  # settlement validation
```

- **All Python execution uses `uv run`** — never plain `python`.
- **Windows console**: set `PYTHONIOENCODING=utf-8` before running, or rich's
  spinner glyphs crash on the legacy cp1252 console at the end of the run.

## Pipeline-to-Unity Contract

`output/<name>_hex_terrain.json` is a **versioned schema** consumed by the Unity 6
C# loader (separate repo). Current version: see `SCHEMA_VERSION` in
`output/game_data_exporter.py`. Bump it on ANY field add/remove/rename and
coordinate with the Unity loader. Full schema doc: `docs/hex-schema.md`.

## Hex Grid Convention

- **Flat-top** hexes (changed from pointy-top in Sprint 1.5); JSON
  `grid.offset = "odd_q"` (AD-012)
- Offset coords `(col, row)`, 1-based; axial `(q, r)` used internally only
- **`hex_size_km: 10` = FLAT-TO-FLAT distance** (edge-to-edge, AD-013).
  `HexGrid.hex_flat_to_flat_m = size·1000`; `hex_radius_m` (circumradius) =
  `flat_to_flat / √3 ≈ 5773.5 m`; area ≈ 86.6 km². (Pre-Sprint 3 this was
  misread as circumradius → ~3× too few hexes. Corrected in AD-013.)
  Belgium bbox → 775 hexes, Benelux+DE bbox → 2,479. Unit tests:
  `uv run python tests/test_hex_geometry.py`. The grid generator samples all
  four bbox edges for its projected extent (not just two corners) so wide
  bboxes are fully tiled — a 2-corner extent left an uncovered SE wedge that
  dropped Frankfurt once AD-013 shrank the padding.
- Hex ID = `CCCRR` zero-padded (`"00501"` = col 5, row 1)
- Grid layout: col spacing `1.5·r`, row spacing `√3·r` (r = circumradius),
  odd columns shifted down half a row — a proper tessellation, so
  *containing hex = nearest center* (`sampler._point_to_hex`, O(1) lookup)

## Architecture

```
src/wargame_cartographer/
├── pipeline.py            — orchestrator: spec → data → grid → sample → render → export
├── cli.py                 — Click CLI (generate, quick, ...)
├── config/map_spec.py     — Pydantic MapSpec + BoundingBox (YAML loader)
├── geo/
│   ├── downloader.py      — Natural Earth + ports (upstream; ports Overpass query
│   │                        currently failing with HTTP 406 — known issue)
│   ├── osm_downloader.py  — Para Bellum OSM layers: landuse/settlements/roads/
│   │                        rail/waterways/bridges. Cached as .gpkg keyed by
│   │                        bbox hash at ~/wargame-cartographer/cache/osm_pb/
│   │                        (30-day TTL). NOTE: cache key ignores query content —
│   │                        clear cache manually after changing a query.
│   ├── elevation.py       — SRTM download + hillshade + slope
│   └── projection.py      — UTM CRS auto-selection from bbox
├── hex/
│   ├── grid.py            — HexGrid (flat-top, axial coords, projected CRS)
│   ├── coords.py          — cube/offset conversion, neighbors, river edges
│   └── sampler.py         — ★ ALL per-hex tagging happens here (HexSampler)
├── terrain/
│   ├── types.py           — Biome enum (24), ElevationTier, Vegetation, Moisture
│   └── classifier.py      — BiomeClassifier (elevation+slope+landuse+settlement)
├── infrastructure/types.py — RoadLevel, RailLevel, SettlementType, Anthrome,
│                             FortificationLevel enums + population bands
├── rendering/             — upstream debug renderer (biomes mapped to 8 legacy
│                             terrain types via shim in pipeline.py — visual only)
└── output/
    ├── game_data_exporter.py — ★ the Unity JSON contract (SCHEMA_VERSION here)
    ├── html_exporter.py      — Folium debug viewer
    └── static_exporter.py    — PNG/PDF
```

Validation scripts live in the project root: `inspect_output.py`,
`check_settlements.py`.

## Sampling Flow (sampler.build_hex_terrain)

1. Water detection (Natural Earth land polygons)
2. Elevation + slope (SRTM, 90th-percentile slope per hex)
3. Landuse (OSM polygons, point-in-polygon at hex center)
4. Settlement (precomputed settlement→hex assignment, see below)
5. Biome classification, vegetation, moisture
6. Road/rail level (best class intersecting WGS84 hex polygon)
7. Rivers (AD-026 node model): `has_river` + `river_name` (a selected river
   ∩ the hex polygon) from the **AD-029 selected set** — Natural Earth
   `scalerank` rivers (`river_scalerank_max`, default 8) + OSM major canals
   (`geo/rivers_global.py`); `river_edges` retained as a render-direction hint;
   bridges, ports
8. Country + province at start (1930 boundaries / provinces, point-in-polygon)
9. Global reconcile passes: coastal flag (water-hex adjacency), urban sprawl
   (AD-014), `admin_tier` capital/sub_capital (AD-023/027)

### Settlement assignment rules (Sprint 2)

One pass over settlement nodes (`_assign_settlements_to_hexes`), each assigned
to its containing hex, **most significant wins** per hex. Type resolved from
population bands when population is known (OSM place tags are noisy), from the
place tag otherwise: >300k metropolis, ≥50k city, ≥2k town. Significance floor
at 10 km hexes: city+ always tags, towns only at pop ≥ 20k, villages never tag
(they remain visible via landuse/anthrome). Belgium test: 114 tagged / 775 hexes
(post-AD-013).

### Multi-hex urban sprawl (Sprint 3, AD-014)

`_assign_urban_sprawl` (third sampler pass) grows a footprint around each
city/metropolis **node** (no OSM boundary relations — empirically `place=city`
relations are too sparse and `admin_level=8` fragments Brussels into 19
communes). Footprint = contiguous BFS through hexes within a population-scaled
radius (metropolis 14 km / large city 11 km / city 8 km) that are either
built-up (urban biome or residential/industrial landuse) **or** open
developable land (plains/steppe) within 11 km of the centroid — the latter
captures a major city's peri-urban fringe whose 10 km hex centers fall on green
belt, while forest/water/wetland are never absorbed. Nearest centroid wins on
overlap (so the Ruhr cities tile their gap). Ring hexes become `settlement.type
= suburb` with `parent_city`; anthrome per the AD-014 distance+landuse table,
resolved in order: **industrial landuse → `industrial` at any distance** (a
port/factory core like Antwerp/Duisburg is an industrial map per AD-015, not a
city-centre map), else <3 km → `metro`, else residential → `residential`, else
`outskirts`. Reuses settlements + landuse already sampled; O(seeds × footprint).
Validation: `uv run python check_urban_sprawl.py`.

## Performance Discipline

Target scale is **~100,000 hexes** (full Europe). Anything O(hexes × features)
is a bug — precompute feature→hex assignments or use spatial indexes. The
settlement scan was rewritten for exactly this reason (was 280×5,243 distance
calls; now one O(settlements) pass).

## Streaming / tiled pipeline (Sprint 4, AD-024/025)

`streaming.run_streaming_pipeline(spec)` processes any bbox at **< 4 GB/tile,
< 6 GB global** (monolithic Benelux peaked 30.4 GB). Run it via
`uv run python run_streaming.py configs/<spec>.yaml`. Output is **hex-for-hex
identical** to the monolithic pipeline (verified by `compare_hex_outputs.py`) —
the per-hex pass-1 body and the global coastal/sprawl passes are the SAME code
(`hex/sampler.py`); only the *data feeding* pass-1 is tiled.

- **GLOBAL once**: grid, boundaries, resources, settlement→hex, AD-029 river
  selection (`geo/rivers_global.py`: Natural Earth `scalerank` rivers + OSM
  major canals, the canals streamed two-pass over parts).
- **PER ~1° TILE**: landuse/roads/rails/bridges read from the cached sub-bbox
  **part** gpkgs with a `bbox(+0.2° margin)` pyogrio filter (never merging the
  full layer — `OSMDownloader.ensure_parts`/`part_descriptors`/`merge=False`);
  NE land/lakes clipped to tile+margin; elevation a **windowed read of the full
  DEM** (`ElevationProcessor.get_elevation_window`) for identical pixels (or a
  per-tile merge for Europe-scale bboxes). Sample only the tile's hexes
  (`build_hex_terrain(hex_keys=…, precomputed=…, run_global_passes=False)`),
  pickle the tile, discard.
- **MERGE**: assemble tiles in `grid.cells` order, run coastal + sprawl
  globally (`geo/urban_global.apply_global_passes`), export.
- Tiles cached/resumable (`STREAMING_VERSION`-stamped pickles); per-tile and
  global RAM enforced (`memory.working_set_mb`, fail-loud over budget).
- Design + as-built: `docs/streaming-pipeline-design.md`. Why elevation isn't
  cleanly tile-local (and the windowed-DEM fix) is the key subtlety.
- **Monolithic path is unchanged** and kept for fast Belgium iteration; both
  share the pass code, which is what makes the streaming output identical.

## Configs

- `configs/para_bellum_belgium_test.yaml` — 775 hexes (post-AD-013),
  fully cached, ~75 s warm. Use for fast iteration.
- `configs/para_bellum_benelux_germany_test.yaml` — Sprint 2 target region
  (Benelux + Western Germany, 2.5–8.8°E / 49.4–53.6°N), 2,479 hexes.
  First run fetches OSM via 6 sub-bbox queries (AD-008) + 35 SRTM tiles
  (~30 min cold, ~3.3 min warm; monolithic peak RAM ~30 GB, streaming
  657 MB/tile). Gate: `uv run python validate_full_bbox.py`.
- `configs/para_bellum_wceurope_test.yaml` — Sprint 4 streaming scale test
  (W+C Europe, 5–15°E / 45–54°N), 8,607 hexes / 130 tiles. **Streaming only**
  (`uv run python run_streaming.py …`) — monolithic can't run it. Validated
  742 MB/tile, 492 MB global; ~4 h cold (Overpass-fetch-bound). NB: only the
  5 western countries have 1930 boundaries (CH/AT/IT → no country, AD-018).

## Architecture Decisions & Change Log

Decision records live in `PARA_BELLUM_DECISIONS.md` (AD-NNN). Sprint-level
changes tracked here:

### Sprint 5 (June 2026)

- **Schema v1.0.4 (PT-1, additive)**: new `rivers` block — `rivers.has_river`
  (bool) + `rivers.river_name` (string). `terrain.river_edges` retained as a
  rendering-direction hint only (AD-026).
- **River node migration (P0-A, AD-026)**: rivers are now the hexes a river
  *passes through* (`has_river` = a selected AD-029 river ∩ the hex polygon),
  not edges. `river_name` = the river with the longest in-hex run. Computed in
  the per-hex pass (`sampler._river_for_hex`) — the whole filtered set already
  feeds every tile (AD-025), so it's seam-identical and consistent with
  `river_edges`. Collapsed the old duplicate `_river_edges_for_hex`. Gate:
  `check_rivers.py` (connectivity, majors, share). Belgium 130 river-hexes
  (17.6 % land), 0 isolated.
- **Provinces (P0-B, AD-023/AD-027)**: `data/boundaries/provinces_1930.geojson`
  + `provinces_1930_metadata.json` (38 provinces, capitals + sub-capitals),
  generated by `tools/build_provinces_1930.py` from NE admin-1 (public domain,
  1930 stopgap): Belgian Brabant merged, NL Flevoland folded, German Prussian
  provinces reconstructed (NRW/Hessen cut-lines), Saar separate. `geo/provinces.py`
  — `load_provinces`/`assign_province` (per-hex PIP, AD-010 snap) +
  `assign_admin_tiers` (global reconcile: capital/sub_capital matched to OSM
  nodes by normalised whole-token name). `political.province_at_start` and
  `settlement.admin_tier` now populated. Gate: `check_provinces.py` (in
  `validate_full_bbox.py`). **Stopgap pending Matthew's historical review.**
- **Boundary coverage (P0-C, AD-028)**: `boundaries_1930.geojson` extended with
  CHE/AUT/ITA (`tools/extend_boundaries_1930.py`, append-only — existing 5
  unchanged) so the Europe run no longer leaves Swiss/Austrian/N-Italian land
  hexes country-less. No provinces for those three this sprint.
- **River source swap (cleanup, AD-029)**: river SELECTION moved from the OSM
  AD-011 geodesic-length heuristic to **Natural Earth `scalerank`** rivers
  (`river_scalerank_max` config, default 8 — captures Meuse/Scheldt which NE
  ranks 8, plus Danube/Rhône at Europe scale) **+ OSM major canals** (NE has no
  canals, but the Albert Canal is required — `geo/rivers_global.py`; AD-011's
  geodesic-length utility retained for canals only). The AD-026 node model is
  unchanged. Eliminates the Mühlgraben generic-name false positives; rivers are
  fewer, cleaner, globally consistent. Belgium 130→87 river-hexes, 0 isolated.
  `STREAMING_VERSION` s5.1→s6.0. The old `geo/waterways_global.py` (AD-011
  streaming river filter) is removed as superseded.

### Sprint 3 (June 2026)

- **PT-1 boundary license fix** (AD-018): dropped CC BY-NC-SA
  historical-basemaps data; now loads repo-committed public-domain Natural
  Earth `data/boundaries/boundaries_1930.geojson`. Ship rule: all bundled
  geo/historical data must be public-domain or commercially licensable.
- **PT-2 hex size = 10 km flat-to-flat** (AD-013, supersedes AD-009): see
  Hex Grid Convention. ~3× more hexes (Belgium 280→775, Benelux 840→2,479).
  Includes a grid-coverage fix (sample all 4 bbox edges) that closed the SE
  wedge and restored Frankfurt. Gates recalibrated; unit tests added.
- **PT-3 metadata**: `grid.offset` `"odd_row_east"`→`"odd_q"`; scenario date
  1939→1930 in configs (output `scenario_date` was already 1930-01-01).
- **F-1 multi-hex urban sprawl** (AD-014): see Multi-hex urban sprawl above.
  **Schema v1.0.2** (additive): `settlement.parent_city`,
  `settlement.distance_from_centroid_km`; `type` gains `suburb`, `anthrome`
  gains `outskirts`. v1.0.1 consumers keep working. **Unity must update
  HexData.cs Settlement** for the two new optional fields.
- **F-2 strategic resources** (`data/resources/resources_1930.geojson`,
  hand-authored public-domain): coal/steel/iron points+polygons for Ruhr,
  Saar, Sambre-Meuse, Campine/Limburg, Liège, Lorraine. Sampler ingest →
  `resources.{coal,steel,iron,oil}` lands in the F-2 commit (`iron` new in
  v1.0.2). Pending Matthew historical review of the data file.

### Sprint 2 (June 2026)

- **Settlement matching rewritten** (`hex/sampler.py`): replaced per-hex
  nearest-node scan (let villages outcompete cities → 0 cities in output) with
  one-pass containing-hex assignment + importance priority + significance
  floors. Brussels/Antwerp/Gent/Liège/Namur now present; tagged hexes 243→86.
- Settlement types now follow `SettlementType` population bands when OSM
  population is known; `town` nodes ≥50k upgrade to `city`, etc.
- (pre-session) Waterways restricted to river|canal; settlements query
  restricted to city|town|village with village pop≥500 filter (note: villages
  with *unknown* population pass that filter — superseded by sampler floors).
- **Schema v1.0.1** (AD-007): `country_1939` → `country_at_start`, `province`
  → `province_at_start` everywhere (sampler, exporter, debug geojson);
  `SCHEMA_VERSION` bumped. Breaking for Unity loader (coordinated). Schema
  documented in `docs/hex-schema.md`; decisions in `PARA_BELLUM_DECISIONS.md`.
- **1930 political boundaries** (`geo/boundaries.py`): loads the
  repo-committed `data/boundaries/boundaries_1930.geojson` — hand-authored
  from Natural Earth admin_0 (public domain, AD-018), modern borders as a
  1930 stopgap valid for this western bbox. (The Sprint 2 source,
  historical-basemaps world_1930, was CC BY-NC-SA — non-commercial — and
  was removed; never use NC-licensed data.) `assign_country()` does sindex
  + prepared-geometry point-in-polygon; coastal hexes outside all polygons
  snap to the nearest country within 0.2° (AD-010). Validation:
  `uv run python check_boundaries.py`.
- **Sprint 2 done gate**: `uv run python validate_sprint2.py` — 21 checks over
  schema, settlements, rivers, boundaries, biomes; exits non-zero on failure.
- **Stage logging**: `run_pipeline` returns `stage_log` (stage name, elapsed
  seconds, input/output counts per stage) and echoes `[stage ...]` lines via
  the status callback. Belgium test full run ≈ 65 s.
- **Sprint 2 target bbox shipped** (840 hexes, Benelux + W. Germany):
  OSM fetched via 6 sub-bbox queries with retry/backoff (AD-008) — cold run
  28.6 min (85% Overpass), warm-cache run 3.1 min. Layer sizes: landuse
  2.24M polygons, roads 640k, rail 162k, bridges 241k, settlements 13k.
  **Peak RAM 30.4 GB** — landuse GeoDataFrame + 343M-cell SRTM rasters all
  in memory; this is THE scale-spike blocker for 100k hexes (needs
  streaming/tiled sampling, not all-in-RAM). Gate: `validate_full_bbox.py`
  28/28 PASS. Coastal snap added to assign_country (AD-010).
- **River significance filter** (`get_waterways()`): fetches all named
  river+canal ways, then keeps only names whose per-name total *geodesic*
  length in the fetch area exceeds `MIN_WATERWAY_TOTAL_M` (110 km). OSM tags
  2 m brooks as `waterway=river`, and width tags are too sparse to use
  (measured brooks, unmeasured Meuse) — accumulated named length is the
  scalable significance proxy. Belgium test: 244 → 71 river hexes (25%),
  zero isolated hexes, one connected network (Meuse+Maas, Schelde, Sambre,
  Ourthe, Albertkanaal, Oise, Semois, Chiers). Validation:
  `uv run python check_rivers.py`. Caveats: rivers renamed across language
  borders fragment per-name totals (Escaut|Schelde count separately); very
  small bboxes can clip majors below the threshold.

### Known Issues / Quirks

- Upstream ports fetch (geo/downloader.py) fails with Overpass HTTP 406 →
  `port: false` everywhere. Pre-existing; not Sprint 2 scope.
- Stray top-level `wargame_cartographer/hex/coords.py` duplicate outside
  `src/` (untracked). Probably accidental; not imported.
  (The AD-011 generic-name over-aggregation caveat — Mühlgraben/Mühlbach
  isolated hexes — is **resolved by AD-029**: river selection is now Natural
  Earth `scalerank`, which has no such generic features.)
