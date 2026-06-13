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

- **Flat-top** hexes (changed from pointy-top in Sprint 1.5)
- Offset coords `(col, row)`, 1-based; axial `(q, r)` used internally only
- `hex_size_km: 10` = center-to-vertex radius 10 km (`HexGrid.hex_radius_m`)
- Hex ID = `CCCRR` zero-padded (`"00501"` = col 5, row 1)
- Grid layout: col spacing `1.5·r`, row spacing `√3·r`, odd columns shifted
  down half a row — a proper tessellation, so *containing hex = nearest center*
  (used by `sampler._point_to_hex` for O(1) point→hex lookup)

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
7. River edges (waterway × hex edge index 0–5), bridges, ports
8. Pass 2: coastal flag from water-hex adjacency

### Settlement assignment rules (Sprint 2)

One pass over settlement nodes (`_assign_settlements_to_hexes`), each assigned
to its containing hex, **most significant wins** per hex. Type resolved from
population bands when population is known (OSM place tags are noisy), from the
place tag otherwise: >300k metropolis, ≥50k city, ≥2k town. Significance floor
at 10 km hexes: city+ always tags, towns only at pop ≥ 20k, villages never tag
(they remain visible via landuse/anthrome). Belgium test: 86 tagged / 280 hexes.

## Performance Discipline

Target scale is **~100,000 hexes** (full Europe). Anything O(hexes × features)
is a bug — precompute feature→hex assignments or use spatial indexes. The
settlement scan was rewritten for exactly this reason (was 280×5,243 distance
calls; now one O(settlements) pass).

## Configs

- `configs/para_bellum_belgium_test.yaml` — 280 hexes, fully cached, ~65 s.
  Use for fast iteration.
- `configs/para_bellum_benelux_germany_test.yaml` — Sprint 2 target region
  (Benelux + Western Germany, 2.5–8.8°E / 49.4–53.6°N), 840 hexes.
  First run fetches OSM via 6 sub-bbox queries (AD-008) + 35 SRTM tiles.
  Gate: `uv run python validate_full_bbox.py`.

## Architecture Decisions & Change Log

Decision records live in `PARA_BELLUM_DECISIONS.md` (AD-NNN). Sprint-level
changes tracked here:

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
- `hex/sampler.py` has a duplicated `_river_edges_for_hex` definition — the
  second (WGS84-based) shadows the first; first is dead code with unreachable
  tail. Works correctly; cleanup deferred.
- Stray top-level `wargame_cartographer/hex/coords.py` duplicate outside
  `src/` (untracked). Probably accidental; not imported.
- `game_data_exporter.py` metadata says `boundaries: "GADM 4.1"` — actual
  source is the 1930 historical-basemaps GeoJSON as of Sprint 2 Task C.
