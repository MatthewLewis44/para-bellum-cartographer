# Pipeline change log

Moved out of `CLAUDE.md` on 2026-09-29: this is history, and history does not need to be in
context on every turn. The decision records themselves are in `PARA_BELLUM_DECISIONS.md`.

## Architecture Decisions & Change Log

Decision records live in `PARA_BELLUM_DECISIONS.md` (AD-NNN). Sprint-level
changes tracked here:

### Sprint 11 (September 2026)

- **Cache snapshot manifest (AD-040).** Every export now writes
  `<artifact-stem>_manifest.json` beside the artifact: every cache part the run
  consumed with its content sha256, size and fetch time, plus the pipeline
  version and commit, the config and its hash, the resolved bbox,
  `SCHEMA_VERSION`, `STREAMING_VERSION`, the sampling-code and input-data
  hashes, and the artifact's own hashes. Verify one with
  `uv run python -m wargame_cartographer.manifest verify <artifact>` — every
  part reads `match`, `changed` or `missing`, exit non-zero if any moved.
  Manifests are committed (`!output/*_manifest.json`); artifacts are not.
  Gate: `tests/test_manifest.py`. It is provenance, **not** recovery — see the
  "does NOT protect against" list in AD-040 before relying on it.
  - The hook lives in `run_streaming_pipeline`, so the tile-cache key moved
    `dd2b5bdc → decd1dee` once. Re-tile with
    `PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS=120` pinned.
  - Found by the manifest on its first run: the **Natural Earth cache
    re-downloads on every run** once its layer directory ages past the TTL,
    because the freshness check stats the directory and overwriting the files
    inside does not move its mtime. 30 downloads on a 15-tile Belgium streaming
    run. Not fixed here (fetch-path change); recorded in AD-040.

#### Also Sprint 11, after review

- **Natural Earth freshness (AD-041).** An NE layer unpacks into a directory and
  a directory's mtime does not move when the files inside are overwritten, so
  `_is_fresh` on it reported the age of the FIRST extraction forever and the
  layer re-downloaded on every run. `ne_10m_rivers` is the AD-029 river
  selection source, so this was map content silently re-fetched every run.
  Freshness now comes from an `ne_10m_<layer>.fetched` stamp written beside the
  directory after a COMPLETED extraction; existing caches adopt the newest file
  inside and are stamped backdated, so landing it downloads nothing. Found by
  the AD-040 manifest. Gate: `tests/test_ne_freshness.py`.
- **The pre-run cache guard (AD-042).** A run now compares the cache against the
  manifest committed at git HEAD for that config, BEFORE fetching or sampling,
  and refuses to start when a part changed, vanished, or is within a day of the
  TTL in force for its root. Chosen over raising the pin, deliberately: a wider
  pin still ends in a run that destroys a snapshot and reports it afterwards.
  Override with `PARA_BELLUM_ALLOW_CACHE_REFETCH=1` when the refetch is meant.
  A config with no committed manifest is unguarded and says so — no baseline is
  invented. Gate: `tests/test_cache_guard.py`. Tile-cache key moved again, to
  `d68b36c2`; read AD-042's "cannot catch" list before trusting a clean start.

### Pass A (August 2026)

- **Provinces for the eight frame nations (AD-037).** DNK/HUN/LTU/LVA/ROU/SOV/
  SWE/YUG carried a `country_at_start` but an EMPTY `province_at_start` on all
  3,105 of their land hexes, so `ProvinceIndex.Build` skipped them and they
  produced nothing — no money, no manpower, and (because resource yield is a
  per-PROVINCE walk, not a per-hex sweep) none of their 407 resource hexes
  either. **138 → 189 provinces.** Builder:
  `tools/build_provinces_1930_frames.py`, **append-only**, runs FOURTH in the
  chain (west → east → backfill → frames).
  - **Tier rule:** a province is the coarsest unit that is a genuine 1930 unit
    or a genuine grouping of them, chosen to land in the density band the
    shipped map uses. Never a cut-line. **HUNGARY IS A DELIBERATE EXCEPTION —
    the genuine county tier, 19 provinces, directed by Matthew directly on
    2026-08-26 against a recommendation to group it into the density band.
    Density recorded as AUTHORED. Pass B must NOT normalise it, and must hold
    it apart from CHE, whose density is drift rather than a decision.**
  - **Provenance:** OHM has no 1930-valid relations for HUN/ROU/LTU/SWE/YUG/SOV
    (probed). DNK comes from real OHM amt relations grouped into landsdele and
    LTU_KLAIPEDA from the OHM Memelland Kreise (`era: 1930`); everything else is
    a Natural Earth admin-1 union clipped to the 1930 country polygon
    (`era: 1930-stopgap`, modern internal lines / 1930 external lines, the
    AD-027 precedent). Recorded per feature in `notes`.
  - **LVA/SWE/YUG/SOV are frame-scoped** — only the block inside the bbox is
    authored. `validate_full_bbox.py` hard-fails if <98% of a metadata-listed
    country's land hexes carry a province, so widening the bbox fails loudly.
  - Watch out for two source traps if you extend this: Natural Earth punches
    city-level units (Hungarian "Urban county", Croatian/Romanian "City")
    OUT of their county as separate rows — union the counties alone and
    sixteen Hungarian county capitals land outside their own province. And OHM
    admin relations are land-only while the 1930 country polygons include
    territorial waters, so "country minus the parts" must be intersected with
    NE land first or it yields a blob of sea.
- **Cropped-province seat rule (AD-037), applied to every nation.** A province
  whose declared 1930 capital falls OUTSIDE the bbox gets the highest-weight
  in-frame settlement as a **sub-capital** (never as a substitute capital — the
  declared capital stays, and designates itself if the frame widens). Otherwise
  a province like ITA_VENEZIA_TRIDENTINA holds Bolzano and 104 hexes and yields
  nothing purely because Trento is south of the frame edge.
  `tools/add_cropped_province_seats.py`, idempotent, reads the artifact.
  Province metadata is deliberately NOT in `_input_data_hash` (it drives only
  `admin_tier`, recomputed in the uncached merge pass), so re-running after it
  reuses every cached tile.
- **Schema v1.0.6 — `settlement.population` (AD-038).** The pipeline always read
  a real population integer off each OSM node (it decides `settlement.type` and
  the sprawl radius) and then discarded it at export. Now emitted **per hex**,
  because the export format has no province-level record; the sim sums a
  province's hexes. Population attaches to the settlement NODE: a city's count
  sits on its own hex and its sprawl ring carries 0, EXCEPT where a ring hex is
  a distinct named town with its own node (Lier, Herstal, Waterloo), which keeps
  its own count. Every settlement is counted exactly once; `0` means "no node or
  no population tag", so a province sum is a floor, not a census. **⚠ MODERN-DERIVED: 2020s OSM tags standing in for 1930
  figures — materially wrong for the eastern territories, the Sudetenland,
  Memel, Danzig and Bessarabia.** Labelled as such in `docs/hex-schema.md`.
  `resources.industry_level` has identical provenance (it is just "2020s OSM
  calls this hex an industrial estate", range {0,1}) and had NO documentation
  at all — now labelled too, and flagged for retirement once Sprint 12 places
  real facilities.
- **AD-030 amendment: `merge=True` now fails loud too.** The monolithic fetch
  path declined to CACHE a partial merge but still returned it, so the run
  exported a map with a hole in a layer — silently wrong terrain that simply
  was not cached. Both paths raise now. `tests/test_cache_integrity.py` is 12
  checks.
- **AD-039: the tile-cache key is normalized for line endings.** It hashed raw
  file bytes, so with `core.autocrlf=true` a plain `git checkout` of an
  UNCHANGED file flipped the key and invalidated all 242 cached tiles, and two
  clones of the same commit computed different keys. Content-based now.
- **The OSM cache TTL is a runtime lever, not a code edit.**

  ```
  PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS=120 uv run python run_streaming.py configs/...
  ```

  **Set this for ANY controlled regeneration.** The 30-day default is right for
  "give me a current map" and wrong for "regenerate and diff", because an
  expired part triggers a live refetch that mixes months of OSM churn into a run
  whose only intended change was something else. This has bitten twice: Pre-
  Sprint 9.0 worked around it by editing the constant, and **Pass A lost the
  settlements snapshot the shipped eastern artifact was built from** before the
  refetch could be stopped — the artifact is no longer byte-reproducible.
  Measured consequence: 115 of 18,719 hexes (0.61%) differ in settlement
  tagging, with landuse/roads/rail/bridges unaffected because those parts were
  still cached. **There is no way back once a part is refetched; we do not
  archive the cache.**
- **Province-density audit** (`tools/province_density_audit.py`, measurement
  only). Reports hexes/province per nation over WHOLE in-frame provinces, which
  is the only comparable figure — a nation the bbox sliced understates badly.
  **The result is DENOMINATOR-DEPENDENT and the tool reports both**, because
  they disagree and a claim built on one must say which:
  **A frame-referenced** (in-frame hexes / provinces the frame references —
  what the GAME sees, i.e. building slots and capture units per unit area) gives
  a CONTINUUM, 13.0–406.0, no break wider than 1.51x.
  **B whole-province** (hexes in fully-framed provinces / count of those — the
  AUTHORING TIER, undistorted by slicing) gives a BIMODAL split, an empty 2.25x
  band between HUN 54.2 and AUT 121.9.
  Both are true of the same data; under A the frame clipping smears the tiers
  together. **What survives both: a ~31x spread, CHE at the dense extreme,
  CSK at the coarse one.** The "two authoring tiers" reading is a claim about
  AUTHORING, not about live economy — do not carry it into a yield argument
  without naming the denominator. Medians are useless here (adding the eight
  frame nations moved B's median 154 -> 54 with no existing nation changing).
  Measurement only; normalisation is pass B's call, and HUN's position is
  authored (see above), not drift.

### Sprint 7 (July 2026)

- **Province layer complete (AD-035 addendum):** no city-provinces — Berlin
  merged into `DEU_BRANDENBURG` (its provincial capital; Potsdam → sub),
  Wien-into-Niederösterreich ratified. `DEU_BERLIN` **removed**. Backfill
  (`tools/build_provinces_1930_backfill.py`, runs third: west → east →
  backfill) added 25 CHE cantons, 6 ITA compartimenti, 16 eastern-FRA
  départements (NE admin-1 stopgap, `era:1930-stopgap`). **92 → 138
  provinces / 138 capitals / 138 sub-capitals.** Countries still country-only:
  HUN/LTU/LVA/DNK/SWE/ROU/YUG/SOV.
- **Infrastructure deferral (AD-036) — policy:** `infrastructure.port` /
  `airfield` / `fortification` are **authored construction-system scenario
  data, NOT pipeline-detected.** They stay inert (`false`/`false`/`"none"`) —
  those empty values are intentional, not defects. A mid-sprint attempt to
  detect ports/airfields from OSM/OHM was retired as the wrong model (modern
  facilities ≠ the 1930 starting network). The OSM port-detection path
  (`get_ports`, `_overpass_to_gdf`, the vector/streaming fetch, the sampler
  sniff) is **deleted**. Rule: pipeline work needs a consuming system that
  exists or is in the current sprint. `anthrome="fortified"` is descriptive
  land character (AD-015), independent of the (inert) fortification field.
- **Gates:** an AD-036 inertness guard (port/airfield/fortification all inert)
  added; Berlin→Brandenburg + CHE/ITA province spot-checks. Schema v1.0.5
  UNCHANGED. `STREAMING_VERSION` s6.3 → s7.0 (province layer + port retirement;
  province content-hash invalidates tiles). Unity handoff:
  `docs/sprint7-unity-handoff.md` (DEU_BERLIN removed + 47 province ids added).

### Sprint 6 (July 2026)

- **P0-A fix bundle (one regeneration):** (1) neighbor-math reconciliation +
  grid parity normalization (AD-034) — coords.py deleted, single
  `grid.neighbors`/`OFFSET_NEIGHBOR_DELTAS` implementation, permanent gate
  test; (2) slope computation corrected (AD-033) — the DEM is 1-arcsec, not
  the assumed 90 m, so slopes were ~3–4.5× underread; now metric per-axis
  with cos(lat), SRTM voids masked, wide-stencil 90 m terrain scale,
  `SLOPE_HILL` restored 4°→8°. Belgium hill 66→146 hexes (Condroz/Ardennes),
  Benelux +167 hill/+41 mountain (Eifel/Rhine gorge); (3) bridge/port radius
  cos(lat)-corrected (+1 bridge Benelux; latent port unit bug fixed).
- **Schema v1.0.5** (AD-031/032): hex id → delimited `{col}_{row}`; hexes
  sorted numerically by (col,row); signed `elevation_m` (polders to −8 m,
  IJsselmeer bed, Hambach pit −83 m; classifications unchanged).
- **1930 eastern boundaries + provinces from OpenHistoricalMap, CC0
  (AD-035):** boundaries_1930.geojson rebuilt — OHM Deutsches Reich
  1922–1935 (full eastern extent), POL (Riga line), DZG, CSK, AUT-kept, HUN,
  LTU, LVA, DNK, SWE, ROU, YUG, SOV-strip, **SAA** (Saar as separate League
  territory — Benelux hexes flipped DEU→SAA). Provinces: DEU set replaced
  with real 1930 lines (meridian-cut approximations retired), + 16 Polish
  voivodeships, 4 CSK lands, 8 AUT Bundesländer (Wien merged into NÖ — no
  OHM hole), DEU_BERLIN (from the Brandenburg hole), SAA_SAAR, DZG_DANZIG.
  92 provinces / 92 capitals / 124 sub-capitals; `match_names` aliases for
  renamed/Cyrillic places (Königsberg→Калининград). Builders:
  `tools/build_boundaries_1930_east.py`, `tools/build_provinces_1930_east.py`
  (fail-loud town-allegiance + area/coverage self-checks).
- **Validation gates parameterized:** `validate_full_bbox.py` takes a config;
  per-config expectation tables (Belgium/Benelux/wceurope/east) + shared
  structural gates; elevation-plausibility gate; river connectivity via
  `OFFSET_NEIGHBOR_DELTAS` (parity guessing removed).
- **New config:** `configs/para_bellum_east_expansion.yaml` (5.8–26.9°E,
  46.3–56°N, ~19k hexes, streaming only).
- `STREAMING_VERSION` s6.0→s6.2. Unity coordination: v1.0.5 + Benelux col
  renumbering + new country codes (SAA/DZG/POL/CSK/HUN/LTU/LVA/DNK/SWE/ROU/
  YUG/SOV) must be accepted before this data ships.

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

