# Para Bellum — Architecture Decision Records (pipeline repo)

Decision records affecting the cartography pipeline and the pipeline→Unity
contract. AD-001 through AD-006 predate this file and live in the project
planning documents outside this repo; numbering continues from there.

---

## AD-007 — Schema v1.0.1: political fields renamed from year-suffixed to `_at_start`

**Date:** 2026-06-11 (Sprint 2)
**Status:** Accepted

Political fields renamed from year-suffixed (`_1939`) to `_at_start`,
supporting a **1930 game start**:

- `political.country_1939` → `political.country_at_start`
- `political.province` → `political.province_at_start`

`SCHEMA_VERSION` bumped `1.0.0` → `1.0.1`. Hardcoding a year in field names
couples the schema to one scenario date; `_at_start` is scenario-agnostic —
the actual date lives in `map_metadata.scenario_date` (`1930-01-01`).

**Impact:** Breaking for the Unity C# loader, which reads
`[JsonProperty("country_1939")]` — accepted; loader update is coordinated
separately. Full schema: `docs/hex-schema.md`.

---

## AD-008 — Sub-bbox splitting for Overpass queries

**Date:** 2026-06-11 (Sprint 2)
**Status:** Accepted

OSM layer queries whose bbox has any edge longer than **2.2°** are split
into an exact-tiling grid of sub-bbox queries (`MAX_QUERY_EDGE_DEG` in
`geo/osm_downloader.py`). Lock-ins:

- **2.2° max edge** — keeps each sub-query comfortably under the Belgium
  test bbox (3.9° × 2.0°) that Overpass handled in one shot, with margin
  for the densest-mapped regions (Ruhr, Randstad). The Sprint 2 target
  bbox (6.3° × 4.2°) splits 3×2 = 6.
- **3 s delay** between live sub-queries; **10/30/90 s backoff** retries
  on Overpass 429/502/503/504/timeout.
- **Per-sub-bbox caching** (`<layer>_part_<hash>.gpkg`, `.empty` marker
  for empty results) so partial failures resume; merged result cached
  under the full-bbox key.
- **Dedup by OSM `type/id`** at merge (ways spanning seams are returned
  whole by both sides). Sub-bboxes tile the full bbox exactly and
  Overpass matches elements with ≥1 node in the box, so the union is a
  superset of the single-query result — no seam loss.
- The **waterway significance filter runs at load time on merged data**
  so per-name length totals span seams (idempotent on filtered caches).

**Impact:** none on output schema; cache directory gains `_part_` files.

---

## AD-009 — Sprint 2 target bbox yields ~840 hexes, not ~2,500

**Date:** 2026-06-11 (Sprint 2)
**Status:** Superseded by AD-013 (Sprint 3)

*Original text retained for history.*

The Sprint 2 target bbox (2.5–8.8°E, 49.4–53.6°N ≈ 230,000 km²) produces
**840 hexes** at the locked 10 km hex standard (flat-top, center-to-vertex
10 km → ~260 km² per hex). The "~2,500 hexes" figure in sprint planning is
not reachable with this bbox at this hex size (it would require ~650,000
km², i.e. most of France + Germany). Hex size is a design constant; the
plan number was the error. Scale-spike implications: per-hex pipeline
stages have now been exercised at 3× Belgium, not 9× — the 100k-hex spike
remains a separate task.

**Superseded** because the underlying convention was reversed in AD-013:
"10 km hex" is now flat-to-flat (edge-to-edge), not circumradius. Under
the corrected convention the original ~2,500 figure was approximately
right.

---

## AD-010 — Coastal snap for `country_at_start` assignment

**Date:** 2026-06-11 (Sprint 2)
**Status:** Accepted

`assign_country()` snaps points not covered by any 1930 polygon to the
nearest country within **0.2°** (`COASTAL_SNAP_DEG`, ~2 hexes). Rationale:
the historical-basemaps coastline is coarse (BORDERPRECISION=3), leaving
inhabited coastal hexes (Den Helder, Wadden islands, East Frisia, Zeeland
banks) and post-1930 polders (Flevoland) with no country — 16 of 649 land
hexes in the Sprint 2 bbox, 5 of them settled. A hex center is a 10 km
quantized sample; coastline noise at that scale should not null out a
hex's nationality. Open sea beyond 0.2° still returns "".

**Known imprecision accepted:** Flevoland hexes get NLD although the land
itself was the Zuiderzee in 1930 (modern Natural Earth terrain + 1930
politics are mixed-era). Era-consistent coastline is a Sprint 3+ question.

---

## AD-011 — Waterway significance filter via name-aggregated geodesic length

**Date:** 2026-06-11 (Sprint 2)
**Status:** Accepted (formalizing earlier in-sprint decision)

Rivers are filtered to strategically significant waterways via per-name
geodesic length aggregation. Implementation in `get_waterways()` in
`geo/osm_downloader.py`:

- Fetch `way["waterway"="river"]["name"]` and `way["waterway"="canal"]["name"]`.
  Untagged waterways are dropped.
- Group ways by `name` (OSM fragments major rivers into hundreds of
  short ways; the 6,947 ways in Belgium span only 1,037 names).
- Compute total geodesic length per name using `pyproj.Geod`.
- Keep only names whose **total geodesic length > `MIN_WATERWAY_TOTAL_M = 110,000` (110 km)**.

**Two refinements over the obvious approaches:**

1. *Group by name, not by way.* Per-way length filtering would discard
   Meuse fragments because individual ways are short.
2. *Geodesic length, not Mercator (EPSG:3857).* Mercator inflates lengths
   ~1.57× at Belgian latitudes and ~2× at 60°N. A Mercator threshold
   would mean different things at different latitudes; geodesic is
   latitude-independent and will hold at Europe scale and beyond.

**Cross-language name fragmentation** (e.g. Escaut/Schelde as one river
with two names) is an accepted edge case. The Schelde alone clears the
threshold in the Sprint 2 bbox. Future fix path if needed: switch to
waterway relations rather than ways.

**Result:** 71 of 280 hexes (Belgium) and 264 of 840 hexes (Benelux+DE)
carry river edges. Zero isolated single-hex edges; single connected
network in both cases. Meuse, Sambre, Schelde, Rhine, Albert Canal all
verified at named confluences.

---

## AD-012 — Flat-top hex orientation

**Date:** 2026-06-11 (Sprint 2)
**Status:** Accepted (formalizing earlier in-sprint decision)

Hexes are oriented **flat-top**: flat edges on top and bottom (north and
south), pointy corners on east and west. Columns of hexes run straight
north-south; rows are jagged.

**Rationale:** Flat-top columns align naturally with longitude meridians,
making spatial queries by longitude trivial (a contiguous range of columns
= a band of constant longitude width). Pointy-top hexes have rows aligned
with parallels but their north-south structure is worse at high latitudes
where longitude convergence is most aggressive. Para Bellum may extend to
global maps in later editions; flat-top is the cleaner foundation.

**Coordinate convention:** offset coordinates `(col, row)` in JSON, with
`col` indexing the north-south columns (1-based, west→east) and `row`
indexing position within a column (1-based, south→north per Unity decode
of the v1.0.1 output). Cube coordinates `(q, r, s)` used internally for
neighbor and distance math only.

**JSON metadata:** `map_metadata.grid.offset` should read `"odd_q"` (or
similar; the existing v1.0.1 label `"odd_row_east"` is incorrect — flagged
in Sprint 2 reports — to be corrected with the next schema bump).

---

## AD-013 — Hex size is 10 km edge-to-edge (flat-to-flat), not circumradius

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted (supersedes AD-009)

"10 km hex" is the **flat-to-flat distance** between the two parallel
horizontal edges of a flat-top hex, NOT the circumradius (center to
corner). With flat-to-flat = 10 km:

- Circumradius (center-to-corner) ≈ 5.77 km (10/√3)
- Width (east-west, corner-to-corner) ≈ 11.55 km (2 × circumradius)
- Area ≈ 86.6 km² (√3/2 × 10²)

This corrects an implementation interpretation that used 10 km as
circumradius (area ≈ 260 km², ~3× larger per hex). Under the corrected
convention:

- Sprint 2 target bbox (~230,000 km²) yields **~2,650 hexes** (matching
  the original Sprint 2 planning estimate; the Sprint 2 output of 840
  hexes reflects the old convention).
- Belgium test bbox (~30,500 km²) yields **~880 hexes** (vs. the prior
  280-hex output).
- Full Europe (~10 million km² playable) yields ~115,000 hexes.

**Rationale for the correction:** Edge-to-edge as the canonical hex size
matches the original GDD's "6-mile-per-hex" intent (≈ 9.65 km), aligns
with how strategic wargames typically describe hex scale, and produces
the per-hex granularity needed for distinct strategic locations (one hex
per major settlement, factory district, or terrain feature) rather than
the over-coarse 260 km² hexes of the old convention.

**Action:** pipeline regenerates all hex outputs (Belgium and Benelux+DE
test bboxes). Validation gates recalibrate. Performance benchmarks
re-measured. Unity loader and rendering require no code change (Unity
hex size is a Unity-units constant, independent of real-world km).

---

## AD-014 — Multi-hex urban sprawl

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted (Sprint 3 implementation)

Major cities at 10 km hex resolution span multiple hexes. Berlin, Moscow,
Leningrad, London occupy 5-7 hexes each; Stalingrad, Paris, Hamburg,
Vienna, Munich 2-3 hexes each; the Ruhr region 10+ hexes as a continuous
industrial conurbation; Brussels, Amsterdam, Cologne 3-4 hexes each.

The pipeline implements multi-hex urban via:

1. **Fetch OSM city boundary polygons** (admin_level=8 or place=city
   area relations) for cities meeting a size threshold.
2. **For each hex inside a city boundary**:
   - Compute distance from hex center to city centroid.
   - Combine with OSM landuse polygons within the hex.
   - Assign `settlement.anthrome` per the table below.
3. **Centroid hex** retains the city's name and tier (city/metropolis).
   Ring hexes carry the parent city's name with anthrome differentiation;
   may use `settlement.type = "suburb"` (new value) or carry the parent
   tier with anthrome distinguishing them.
4. **`settlement.population_class`** scales by distance from centroid:
   centroid 5, inner ring 3-4, outer ring 2.

**Anthrome assignment table:**

| Distance from centroid | Dominant OSM landuse        | Anthrome    |
|------------------------|------------------------------|-------------|
| < ~3 km                | residential + commercial     | `metro`     |
| 3 - 15 km              | industrial                   | `industrial`|
| Anywhere in polygon    | residential (dense)          | `residential` |
| In polygon boundary    | (no specific landuse)        | `outskirts` |
| Outside polygon        | (any)                        | `none`      |

**Schema impact:** `settlement.anthrome` values extended to include
`metro`, `industrial`, `residential`, `outskirts`, `suburb`. Schema bumps
to v1.0.2 (additive only; no field renames).

**Rationale:** Single-hex cities collapse the Stalingrad/Berlin/Caen
urban-combat narrative arc to one battle. Multi-hex urban makes these
battles into the multi-hex campaigns they historically were. Pieces are
mostly already in place: anthrome field exists, urban biome exists, OSM
landuse polygons are already fetched.

---

## AD-015 — Battle map selection by hex anthrome

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted

Tactical battles select their tactical map from a pool keyed to the hex's
`terrain.biome` plus `settlement.anthrome`. Urban hexes specifically:

- `biome=urban + anthrome=metro` → city-center maps (Pavlov's House-type,
  major-street-network, government-quarter)
- `biome=urban + anthrome=industrial` → industrial-district maps (factory
  floors, rail yards, foundries)
- `biome=urban + anthrome=residential` → residential maps (suburban
  streets, row houses, mixed light commercial)
- `biome=urban + anthrome=outskirts` → urban-fringe maps (transition
  zones, peri-urban farmland, light industrial)

Non-urban biomes use existing terrain-keyed pools. This is Unity logic
(per AD-GD-007 from project planning); pipeline contributes only the
tagging data.

---

## AD-016 — City capture is a multi-hex campaign minigame

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted

Major cities (per AD-014) are captured one hex at a time via tactical
battle (or auto-resolve). The city is "captured" when one side holds all
of its constituent hexes, or when the defender voluntarily retreats from
the city as a whole. Defenders may retreat from individual hexes
mid-campaign to consolidate forces in adjacent ones.

The Newsreel system (per project planning) groups consecutive engagements
within a single city into a named campaign arc (e.g., "Battle of
Stalingrad: Day 47, Mamayev Kurgan captured").

This is largely Unity gameplay logic; pipeline contributes the multi-hex
tagging that enables it (AD-014).

---

## AD-017 — Baseline + corrections layer architecture (planned)

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted, implementation deferred to Sprint 4-5

Map data uses two layers:

1. **Baseline layer**: produced by the pipeline. Geographic facts
   (terrain, biome, elevation, OSM data, political assignments). Fully
   regenerable. Hand-edits are not made here.
2. **Corrections layer**: a separate JSON file. Hand-edited via the
   Unity-side editor (Sprint 4-5). Contains only deltas — fields that
   override the baseline for specific hexes.

At Unity load time, final hex data = baseline + corrections (corrections
override baseline where present).

**Rationale:** Pipeline iterations don't destroy hand work. Corrections
are independently version-controllable and diffable. Modders can author
their own corrections layers without forking the baseline.

This pattern is used in Paradox games, OSM data versioning, and GIS
override systems generally.

---

## AD-018 — Boundary and historical data must be public-domain-licensable

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted

All shipped geographic and historical data must be sourced under licenses
compatible with commercial distribution. Specifically excluded:
**CC BY-NC** (non-commercial) and **CC BY-NC-SA** (non-commercial +
share-alike). Acceptable: public domain, CC0, CC BY (with attribution),
ODbL (with attribution and share-alike acceptance for derivatives).

**Action taken in Sprint 3:** the `historical-basemaps/world_1930`
dataset used in Sprint 2 (CC BY-NC-SA) is replaced with Natural Earth
public-domain country polygons as a stopgap for the Sprint 2 test bbox.
Accuracy loss is negligible for the current test region (1930 and modern
borders are essentially identical for Belgium, Netherlands, Luxembourg,
and Western Germany). When the bbox extends eastward, real 1930
boundaries will be hand-curated from public-domain sources (Wikipedia
historical maps, period atlases) or sourced from another commercially
compatible dataset.

`boundaries_1930.geojson` metadata reflects the source:
`"source": "Natural Earth (public domain)"`, `"version": "0.1-stopgap"`,
`"note": "Modern borders used as 1930 approximation. Valid for Belgium/
NL/Luxembourg/Western Germany; 1930 vs modern diverges significantly
only for Eastern Europe (to be hand-curated when bbox extends east)."`

---

## AD-019 —  Grid projection extent sampling

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted

Compute the projected bbox extent by sampling all four bbox edges (with sufficient sample density), not just SW/NE corners. Filter generated hexes by actual lon/lat membership within the bbox + one-hex margin. This guarantees no in-bbox region is uncovered, regardless of bbox width or local projection curvature. Caught when AD-013's finer hex padding exposed the SE-wedge gap at Frankfurt.


---

## AD-020 —   Multi-hex urban footprint algorithm

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted

Footprints are seeded from existing city/metropolis settlement nodes (NOT from OSM place=city relations or admin_level=8 polygons — both were probed and found unusable across the bbox). Footprint = hexes within a population-scaled radius (metropolis 14km / city 8-11km) that either contain urban OSM landuse OR are open developable terrain (plains/steppe) within radius of a major centroid — but never forest, water, or wetland. BFS-bounded growth, O(footprint). Amends AD-014's footprint sourcing spec.


---

## AD-021 —    Anthrome assignment order

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted

 Industrial OSM landuse wins regardless of distance from centroid (port and factory cores are industrial, not metro, even when geographically central). The metro anthrome applies only to dense residential/commercial cores within ~3km of centroid that lack dominant industrial landuse. This is load-bearing for AD-015 tactical map pool selection.


---

## AD-022 —    Hex picking is math-based

**Date:** 2026-06-12 (Sprint 3)
**Status:** Accepted

 Selection uses HexCoord.FromWorldPosition via cube-rounded inverse coordinate math. No MeshColliders, no physics raycasts. O(1) per pick, scales to 100k+ hexes, survives the Sprint 4 chunked-mesh refactor without code changes.


---

## AD-023 — Multi-tier administrative model: provinces with capital and sub-capital nodes

**Date:** 2026-06-14 (Sprint 4)
**Status:** Accepted

Hex-level data remains tactical (terrain, biome, movement, individual combat,
resources). Province-level data is aggregated. The aggregation is NOT a sum
over all hexes; it is the explicit sum over designated administrative nodes
(capital + sub-capitals per province).

**Three orthogonal axes for settled hexes:**

- `settlement.type` — physical size (village/town/city/metropolis/suburb). Existing.
- `settlement.anthrome` — urban character subtype (metro/industrial/residential/
  outskirts/cropland/etc.). Existing per AD-014.
- `settlement.admin_tier` — political/economic significance. New in v1.0.3.

**admin_tier enum values:**

- `capital` — province capital, exactly one per province
- `sub_capital` — designated regional city, 0-N per province (typically 1-5)
- `urban` — settled hex without administrative designation (most settled hexes)
- `rural` — unsettled hex within a province (default for non-water non-settled)
- `none` — water hex or no province assignment

**Population and economy semantics:**

- Only `capital` and `sub_capital` hexes carry meaningful population numbers
  that contribute to province totals
- `urban` hexes may carry `population_class` for tactical purposes (denser =
  harder fight) but their numbers do NOT aggregate to province-level totals
- `rural` and `none` hexes contribute zero to province economy/population

**Province capture semantics:**

- A province is "politically captured" when (a) its `capital` hex is held AND
  (b) a controlling-majority threshold of its hexes are held
- Holding capital without majority = raid/occupation, not political control
- Holding majority without capital = siege, awaiting capital fall
- Capturing a `sub_capital` cuts the province's effective economic output
  proportionally — each sub-capital's loss removes that center's economic
  contribution to the province pool
- `urban` hex capture is tactically meaningful (denies enemy, opens supply
  routes) but does not affect province economic state directly

**Sub-capital selection criteria (hand-curated per province from 1930 historical
sources):**

- Major industrial centers (Krupp/Essen, Škoda/Plzeň, Fiat/Torino)
- Major rail junctions (Leipzig, Lyon, Vienna)
- Strategic positions (ports, river crossings, fortified positions)
- Historical population centers above thresholds appropriate to the era

**Authoring:** Provinces stored as GeoJSON polygons (`data/boundaries/
provinces_1930.geojson`) for hex assignment via point-in-polygon (same code
path as country assignment per AD-018). Capital and sub-capital metadata in a
paired JSON file (`data/boundaries/provinces_1930_metadata.json`). All
hand-curated from public-domain 1930 historical sources per AD-018.

**Prussia handling for Sprint 5+:** 1930 Prussia is too large for single-province
treatment. Use historical Prussian provinces (Rhineland, Westphalia, Hesse-Nassau,
Hannover, Brandenburg, Pomerania, Silesia, East Prussia, West Prussia,
Schleswig-Holstein, Saxony Province) as our provinces. Each gets its own capital
and sub-capitals. Documented in DECISIONS.md when Sprint 5 implementation hits
this scope.

**Visualization (Sprint 5 work):** Each province rendered with a distinct hashed
color in Unity's Province view mode, HOI4-style. Capital hexes show a banner/icon.
Sub-capital hexes show a smaller marker. Province borders rendered as thin lines.

**Sprint 4 scope:** Schema bump to v1.0.3 with `admin_tier` field (this AD).
Implementation of province boundaries, sub-capital tagging, and Unity visualization
are P1 stretch work for this sprint; default home is Sprint 5.
---
## AD-024 — Tiled / streaming pipeline architecture

**Date:** 2026-06-14 (Sprint 4)
**Status:** Accepted

The pipeline processes any bbox at **< 4 GB peak RAM per tile and < 6 GB
global** by tiling the per-hex sampling and discarding intermediate state
between tiles, instead of holding all layers in memory at once (the monolithic
Benelux+DE run peaked at **30.4 GB**, extrapolating to ~360 GB for full Europe —
above the 16 GB min-spec). Entry point: `streaming.run_streaming_pipeline`.

Lock-ins:
- **~1° integer-degree tiles**, hex assigned to the tile containing its center;
  hex coordinates come from the single GLOBAL grid (no per-tile renumbering),
  so coastal/sprawl reconcile against consistent offsets.
- **0.2° query margin** per tile (≫ one hex circumradius + a few SRTM pixels),
  so every feature reaching an in-tile hex is present → tile sampling is
  byte-identical to monolithic.
- **Read parts, never merge**: tile-local layers (landuse/roads/rails/bridges)
  are read per tile from the cached 2.2° sub-bbox **part** gpkgs (AD-008) with a
  pyogrio `bbox` filter; the full layer is never materialized. `merge=False` on
  the OSM getters caches parts without the merge spike, killing the *fetch-time*
  wall too.
- **Serial tiles** (no cross-tile parallelism — keeps RAM predictable; Sprint 5+
  may parallelize). Per-tile pickle cache, `STREAMING_VERSION`-stamped for
  resume + invalidation. RAM enforced via `memory.working_set_mb` (fail loud).
- **Output is hex-for-hex identical** to monolithic, verified by
  `compare_hex_outputs.py` on Belgium (775) and Benelux+DE (2,479). The
  monolithic path is retained unchanged for fast iteration; both share the
  per-hex pass code, which is what guarantees identity.

## AD-025 — Global-vs-tile split and the identical-output invariants

**Date:** 2026-06-14 (Sprint 4)
**Status:** Accepted (informed by the T1b adversarial design panel)

Which sampling stages are tile-local vs global, and the subtleties that make
streaming byte-identical to monolithic:

- **GLOBAL** (computed once, reused by all tiles): the hex grid; boundaries
  (`country_at_start`, incl. the 0.2° coastal snap — AD-010); resources;
  settlement→hex assignment (most-significant-wins is only correct over the
  whole set); the AD-011 waterway significance filter (per-name geodesic length
  spans the whole bbox — streamed two-pass over parts, `geo/waterways_global`).
- **TILE-LOCAL** (per hex, given the 0.2° margin): water/lake (NE land clipped
  per tile), elevation+slope, landuse→biome, road/rail, river_edges (against the
  global filtered set passed WHOLE), bridge, port, resource lookup.
- **GLOBAL RECONCILE** (deferred to the merge): coastal flag (a hex is coastal
  iff a *neighbor* — possibly in another tile — is water) and multi-hex urban
  sprawl (AD-020 footprints cross tiles, e.g. the Ruhr). Run over the merged
  grid assembled in `grid.cells` order so exact-distance tie-breaks are
  deterministic.

Identical-output invariants the panel surfaced and we enforce:
1. **Elevation is NOT cleanly tile-local.** A per-tile `merge(bounds=…)` yields
   a different pixel grid than the full-bbox raster, so `sample_at_point`'s
   `round()` picks different pixels (121 Belgium hexes differed on the first
   run). Fix: per-tile elevation is a **windowed read of the cached full DEM**
   (`get_elevation_window`) for bboxes small enough to build one; Europe-scale
   bboxes use a per-tile merge (no monolithic baseline to match there).
2. **Deterministic landuse tie-break**: smallest-area containing polygon wins
   (then landuse_type), so a bbox-sliced read matches the full read regardless
   of feature order.
3. **Synthetic elevation is fail-loud** in streaming (`allow_synthetic=False`):
   a silent sin/cos substitution for one tile would be cached and shipped.

---

## AD-026 — Rivers are hex-center features, not hex-edge boundaries

**Date:** 2026-06-17 (Sprint 5)
**Status:** Accepted (supersedes river_edges gameplay role)

A river occupies the hexes its polyline passes through (node model), NOT the
edges between hexes (the prior edge model). Rationale: edge-based rivers
staircase illogically — a north-south river had to zigzag across top edge,
upper-right edge, bottom edge of successive hexes, which neither rendered
continuously nor represented the river sensibly.

**Gameplay:** crossing a river = attacking INTO a river hex. The opposed
crossing is folded into that single hex's battle (river-crossing tactical map
per AD-015). The attacker chooses where to force the crossing by which river-hex
they assault. Rivers are features armies assault into, not boundaries armies
form along.

**v1 scope:** single boolean — a hex either has a river or it does not. No
major/minor/navigable class distinction yet. The AD-011 geodesic-length data is
retained so a class split can be added later without rework.

**Schema (v1.0.4, additive):**
- rivers.has_river (bool) — does this hex contain a river
- rivers.river_name (string) — which river (display + future Newsreel naming)
- river_edges retained ONLY as a directional hint for rendering (which neighbors
  to draw the spline toward); its gameplay role is superseded

**Rendering:** continuous spline through consecutive river-hex centers, drawn
toward neighboring river-hexes (using river_edges direction hints). Continuous
by construction (center-to-center always connects).

**As-built (Sprint 5):** computed in the per-hex pass (`sampler._river_for_hex`),
NOT a separate global stage. AD-025 already feeds the WHOLE AD-011-filtered
waterway set to the monolithic pass and to every streaming tile (that is what
makes `river_edges` seam-identical), so `has_river` (a filtered river intersects
the hex polygon) and `river_name` (the river with the longest in-hex run) are
seam-identical and automatically consistent with `river_edges` — no second grid
sweep. Belgium: 130 river-hexes (17.6 % of land), 0 isolated, Meuse / Scheldt /
Albert Canal / Sambre as continuous chains. Adversarial review confirmed
connectivity, determinism, and has_river↔river_edges consistency; its one
finding — the primary-river pick measured in-hex run length in raw WGS84 degrees,
under-weighting E-W vs N-S by cos(lat) — is fixed (lon scaled by cos(lat) before
the length comparison, so a trunk beats a clipping tributary regardless of
orientation).

---

## AD-027 — 1930 province authoring + tagging (implementation)

**Date:** 2026-06-17 (Sprint 5)
**Status:** Accepted (stopgap, pending historical review)

Implements the province layer specified by AD-023. Provinces are authored as
GeoJSON polygons + a paired capital/sub-capital metadata JSON, both generated
reproducibly by `tools/build_provinces_1930.py` from **Natural Earth admin-1**
(public domain, the AD-018 lineage). 38 provinces over the 5-country western
bbox (BEL 9, NLD 11, LUX 1, DEU 6, FRA 11).

**1930-vs-modern adjustments (NE admin-1 is a stopgap):**
- **Belgium** — modern NE splits Brabant into Flemish + Walloon Brabant +
  Brussels (post-1995). 1930 had ONE Brabant (incl. Brussels) → the three merge.
- **Netherlands** — NE includes Flevoland (reclaimed 1986; Zuiderzee *water* in
  1930). Folded into Overijssel so reclaimed-land hexes still resolve to a 1930
  province. 11 provinces.
- **Germany** — modern Bundesländer do NOT match 1930. Reconstructed the
  relevant 1930 states/Prussian provinces (AD-023): Rheinland, Westfalen,
  Hannover, Hesse-Nassau, Hesse-Darmstadt, and **Saar** (a League of Nations
  mandate in 1930 — a separate territory, not part of Germany proper). NRW is
  split into Rheinland (W) / Westfalen (E) by a meridian cut (7.3 °E); Hessen
  into Hesse-Nassau (N) / Hesse-Darmstadt (S) by a parallel cut (50.0 °N). The
  cuts are **approximate** — the historical borders zigzag, so the Ruhr edge
  (Bochum/Dortmund) is fuzzy — but every province capital resolves correctly.
- **France** — départements (préfecture = capital), 1930-stable.

**Capital / sub-capital tagging (`assign_admin_tiers`, a GLOBAL reconcile pass
like coastal/sprawl):** capital + sub-capital names are matched to OSM
settlement **nodes** (the raw set, not just floor-tagged hexes — so a provincial
capital below the 10 km tagging floor still resolves). Matching is on
**normalised, WHOLE-TOKEN** names (lowercase + accent-strip), scored
exact > token > shared-token, then by population. Raw substring containment was
explicitly dropped after it matched "**As**" (a Limburg town) to "H-**as**-selt"
and stole BEL_LIMBURG's capital. A matched capital hex is upgraded to read as a
settled place; `admin_tier` ∈ {capital, sub_capital, urban, rural, none}.

**Bbox edge effect:** provinces whose 1930 capital lies outside the run bbox
(Hannover, Kassel, Strasbourg, …) get no in-grid capital hex — reported as a
WARNING, not a failure. The region-wide gate is "30+ provinces, 30+ capitals"
(Benelux satisfies it); a single-country test bbox is treated as a subset.

**Validation:** `check_provinces.py` (one-capital-per-province, settled hexes are
capital/sub/urban, no rural hex carries a name, full land coverage, totals),
wired into `validate_full_bbox.py`.

**Provinces for CHE/AUT/ITA are NOT authored this sprint** (P0-C is country-level
coverage only); their hexes get a country but no province. STOPGAP posture per
AD-018 — review pending.

**Review follow-up (Sprint 5):** adversarial review surfaced and fixed:
- `_norm` now maps punctuation → space (not delete) and folds `ß`→`ss`, so
  hyphen/apostrophe names ("Charleville-Mézières", "'s-Gravenhage") keep their
  word boundaries for whole-token matching instead of gluing into one token.
- The Hague's OSM node is named **"Den Haag"**, not "'s-Gravenhage" — metadata
  capital corrected so NLD_ZUID_HOLLAND resolves.
- **Border-quantization limitation (accepted):** a province capital whose OSM
  node lands on a 10 km hex *whose center* falls across the border (e.g. Arlon —
  Belgian capital, but its hex center is in Luxembourg) is grouped under the
  hex's province. Keeping the hex-center rule is the consistent choice (a
  province's capital hex is always inside that province; never misattributed);
  the cost is an occasional border capital going untagged. Grid-dependent.
- **The "30+ provinces / 30+ capitals" target is a property of the AUTHORED
  layer (38/38/57), not of a clipped bbox** — a run only frames the capitals
  geographically inside it (the Benelux bbox omits 8 southern-French / Hannover /
  Saar capitals). The gates validate authored totals + per-run structural
  correctness (no province with >1 capital; every *framed* province has one),
  not a raw framed-capital count.

---

## AD-028 — Country boundary coverage extended to CHE / AUT / ITA

**Date:** 2026-06-17 (Sprint 5)
**Status:** Accepted (stopgap)

The W+C Europe run produced empty-`country_at_start` land hexes over
Switzerland, Austria and northern Italy (the AD-018 stopgap had only the 5
western countries). `tools/extend_boundaries_1930.py` **appends** CHE/AUT/ITA
from Natural Earth admin_0 (public domain), preserving the existing 5 countries'
geometry byte-for-byte (so the Benelux hex-identical gates are unaffected — only
previously-empty Alpine/N-Italian hexes change). 1930 validity: Swiss borders
stable; Austria independent (pre-Anschluss); Italy's modern polygon == 1930 for
the bbox's northern reach (South Tyrol Italian since 1919; the eastern
Trieste/Istria gains lost in 1947 are outside the bbox). Provinces for these
three are deferred to a later eastward-expansion sprint.

---

## AD-029 — River selection from Natural Earth scalerank (supersedes AD-011 for river selection)

**Date:** 2026-06-17 (Sprint 5 cleanup)
**Status:** Accepted

### Decision

River SELECTION — which rivers exist on the map — is sourced from Natural Earth's
`rivers_lake_centerlines` dataset, filtered by the dataset's `scalerank` field. A
configurable threshold `river_scalerank_max` selects rivers: only features with
`scalerank <= river_scalerank_max` become candidate rivers. This supersedes the
OSM-derived AD-011 geodesic-length-per-name significance heuristic as the river
SELECTION mechanism.

The river NODE MODEL (AD-026) is unchanged: a river still occupies the hexes its
polyline passes through; `rivers.has_river` and `rivers.river_name` are still
per-hex fields; rendering is still a continuous spline through river-hex centers.
Only the SOURCE of "which rivers count" changes — from OSM+AD-011 to Natural
Earth scalerank.

### Rationale

The AD-011 approach (sum geodesic length per river name across the bbox, keep
names totaling >110 km) was clever but had three failure modes that surfaced at
scale:

1. **Generic-name over-aggregation.** Common waterway names ("Mühlgraben",
   "Mühlbach", "Mühlenbach") appear hundreds of times across Germany. AD-011 sums
   all segments sharing a name, so dozens of unrelated 2 km mill-streams aggregate
   past the 110 km threshold and falsely qualify as significant — producing
   isolated, disconnected river-hexes (~0.5% of hexes at continental scale, all
   generic-named).

2. **Random middle-continent rivers.** OSM waterway tagging density varies wildly
   by region; AD-011's length heuristic admits minor rivers in well-mapped regions
   while the same significance class is dropped in sparsely-mapped regions.
   Inconsistent significance across the map.

3. **Cross-language fragmentation.** A river renamed at a language border
   (Escaut/Schelde, Maas/Meuse) splits into name-fragments that may each fall
   below threshold, dropping a genuinely major river.

Natural Earth `scalerank` is a CURATED significance ranking (cartographer
judgment, 1 = major like the Nile/Rhine/Amazon, 9 = minor tributary), not a
DERIVED one. It eliminates all three failure modes:

- No name-aggregation, so no generic-name false positives.
- Globally consistent significance (a rank-3 river is rank-3 everywhere).
- Major rivers carry their significance as data regardless of name fragmentation.

It is also already a project dependency (Natural Earth is used for coastlines and
boundaries), public-domain (AD-018 compliant), tunable (one threshold parameter),
and latitude-independent.

### Threshold

`river_scalerank_max` is a config parameter (in the bbox config YAML). Default
value to be set by PM art-direction after visual review of candidate thresholds
(a-priori expected range 6-7 for a 10 km strategic hex map). The parameter is
exposed so the threshold can be tuned per visual judgment without code changes.

**As-built default = 8 (empirically corrected).** The T1 probe found Natural
Earth ranks the **Meuse (Maas) and Scheldt (Schelde) at scalerank 8**, not 6-7 —
so ≤7 produces a Benelux map with the Rhine but *neither* the Meuse nor the
Scheldt (194 river-hexes, the two key rivers missing), while ≤8 includes them
(305 river-hexes). The continental giants (Danube, Rhône, Rhine, Elbe, Po, Oder)
sit at ≤6, so ≤8 is a strict superset that adds the regionally-major rank-8
rivers without re-admitting noise (no generic names exist in NE). Default is
therefore **8**; the ≤7 vs ≤8 comparison outputs are retained for the PM.

### River naming

`rivers.river_name` (v1.0.4 schema) populates from Natural Earth's `name` field,
which is cleaner and less language-fragmented than OSM's per-segment naming.

### AD-011 disposition

AD-011's geodesic-length-per-name logic is RETAINED in the codebase ONLY if OSM
is still needed for sub-hex river GEOMETRY refinement (e.g. precise edge-crossing
direction hints for `river_edges`). If Natural Earth centerline geometry is
sufficient for the AD-026 node computation (which hexes a river passes through),
the AD-011 path is dead code and may be removed. The engineer determines this
empirically: if Natural Earth centerlines alone produce correct connected
river-hex chains, AD-011 river logic is retired (documented as superseded). The
geodesic-length utility itself may be retained for other uses.

### river_edges disposition

`terrain.river_edges` (the per-hex edge-crossing array) remains a rendering
direction hint per AD-026. It can be computed from Natural Earth centerline
geometry the same way it was from OSM (which edges of the hex polygon the
selected river polyline crosses). If Natural Earth geometry is too coarse for
reliable edge-crossing computation at 10 km hex resolution, `river_edges` may be
derived from hex-to-hex adjacency among river-hexes instead (connect toward
neighboring river-hexes) — the Sprint 5 Unity river renderer already drives
connections by neighbor status rather than raw river_edges, so this is safe.

### Validation

`check_rivers.py` updated to validate the Natural Earth source: connected chains,
zero (or near-zero, documented-tolerance) isolated hexes, major rivers present
(Meuse, Rhine, Scheldt, Danube, Rhône, Albert Canal verified at named points),
sensible river-hex count. The Mühlgraben/Mühlbach isolated-hex problem from
AD-011 must be GONE.

### Future: river class

Scalerank also enables the deferred major/minor visual distinction: a future
schema addition could expose `rivers.scalerank` (or a derived class) per hex so
Unity renders rank-1 rivers (Rhine) visually heavier than rank-6 rivers. Out of
scope for this cleanup; noted as the natural next step when river-class gameplay
(crossing difficulty scaled by river size) is implemented.

---
## AD-030 — Cache integrity and fail-loud rules

**Date:** 2026-06-18 (pre-Sprint-6 cleanup)
**Status:** Accepted

An audit found the fail-loud principle (AD-025, enforced for streaming
elevation) was violated in the caching and fallback paths: a transient fetch
failure or an edited data file could silently ship wrong output. This AD records
the policy now enforced.

**Rules:**

1. **No merged cache from a partial fetch.** `osm_downloader._fetch_layer`
   counts failed sub-bbox parts. If any failed, the merged full-bbox cache is
   NOT written (so the failed part is retried next run instead of being masked
   by a fresh merged cache). The streaming path (`merge=False`) RAISES on a
   failed part rather than caching an incomplete part set; `_read_layer_slice`
   RAISES on an unreadable overlapping part instead of silently skipping it.

   **Amended in Pass A (2026-08-26): the monolithic path (`merge=True`) now
   RAISES too.** As originally written it declined to *cache* the partial merge
   but still returned it, so the run continued and exported a map with a
   sub-bbox of a layer missing — the same defect one step later, silently wrong
   terrain that simply was not cached. The only thing separating the two
   behaviours was which caller happened to be asking. Both paths fail loud; a
   partial fetch never becomes map content. Successful parts stay cached
   individually, so a re-run retries only what failed. Pinned by
   `tests/test_cache_integrity.py` (12 checks).

2. **Tile caches are keyed on input-data content AND sampling-code content.**
   The streaming tile directory includes an md5 of the boundaries / provinces
   / resources GeoJSON (`_input_data_hash`) so editing any of those three (all
   pending historical review) invalidates the tile cache — country / province
   / resource assignment lives inside cached tiles. It ALSO includes an md5 of
   the sampling-critical source modules (`_sampling_code_hash`:
   `hex/sampler.py`, `hex/grid.py`, `terrain/classifier.py`+`types.py`,
   `geo/boundaries.py`+`provinces.py`+`elevation.py`+`rivers_global.py`,
   `streaming.py`) so any change to the code that produces per-hex tile content
   invalidates stale tiles automatically. **Added Sprint 7** after a
   `STREAMING_VERSION` NAME was reused across a sampling-code change (port
   retirement) and silently reused port-containing tiles — the manual version
   string is a fragile proxy for "the code changed"; the content hash is not.
   `STREAMING_VERSION` remains a human marker + manual invalidation lever but
   is no longer load-bearing for cache correctness. (`river_scalerank_max` and
   `STREAMING_VERSION` still key the cache too.)

3. **No silent fallback for P0 map content.** River selection failure in the
   monolithic pipeline propagates (no "keeping OSM waterways" lie — those are
   empty since Sprint 5). Synthetic (sin/cos) elevation is now `allow_synthetic
   = False` by default in BOTH pipelines; SRTM failure fails loud. Offline dev
   opts in explicitly via `PARA_BELLUM_ALLOW_SYNTHETIC_ELEVATION=1`.

4. **Merge and export assert completeness.** The streaming merge asserts
   `len(result) == grid.hex_count` (a missing/empty tile pickle aborts). The
   exporter counts grid cells absent from `hex_terrain` and RAISES if any (they
   would otherwise export as default plains hexes).

**Anti-goal for the pass that introduced this AD:** these are failure-path and
cache-key changes only. Regenerated output is hex-for-hex identical to the
pre-AD-030 shipped output (verified with `compare_hex_outputs.py`); the tile
cache key change forces a resample but the resampled data is unchanged.

---

## AD-031 — Hex id format: delimited `{col}_{row}` (schema v1.0.5)

**Date:** 2026-07-02 (Sprint 6, P0-B)
**Status:** Accepted

The documented packed `CCCRR` id (3-digit col + 2-digit row) had already
overflowed in shipped data: wceurope v1.0.4 has rows ≥ 100, so its ids mix 5
and 6 characters, are positionally ambiguous, and their lexicographic sort no
longer matches (col,row) order (row 100 sorts before row 11). Nothing broke
only because Unity keys on `coords` — no consumer parses ids yet, which is
exactly why the format is fixed NOW (audit finding D2).

**New format: `{col}_{row}`** (e.g. `"17_103"`). Unambiguous at any scale, no
future overflow, trivially human-readable. Ids remain display/debug only;
`coords` stays the key. The exporter's ordering becomes a numeric sort by
(col,row) — "sorted by id" is retired. The debug GeoJSON exporter follows the
same format. Rejected alternatives: wider fixed packing (`CCCCRRR` — still
overflows someday, still positionally fragile) and dropping `id` entirely
(kept: PM/debug communication uses it; the Unity inspector displays it).

Unity impact: accept v1.0.5; update `HexCoord.ToHexId()` display format.

---

## AD-032 — Signed elevation: below-sea-level land exports its true elevation (schema v1.0.5)

**Date:** 2026-07-02 (Sprint 6, P0-B)
**Status:** Accepted

The sampler clamped land elevation to ≥ 0 (`max(0.0, elev)`), discarding data
SRTM already gives us. Dutch polders are the obvious case (Zuidplas/Haarlemmer
polders to ≈ −8 m). Rationale for the change: (a) data fidelity we already
have and were throwing away; (b) era-plausible inundation gameplay (Walcheren,
the Hollandic Water Line) stays unscoped, but the data now survives so that
feature needs no pipeline rework when it lands.

Ocean hexes were never clamped (shipped v1.0.4 already carries negative
North Sea samples) — but the old clamp keyed on ocean-water only, so **lake**
hexes were clamped too. As-built change set (regeneration diff, only
`geo.elevation_m` moved): **5 hexes in Belgium** (−1..−2 m coastal polders);
**183 in Benelux** = 163 land (Dutch polder belt −1..−8 m, incl.
Hoofddorp/Delft/Amstelveen/Urk urban hexes; one −83 m outlier — the
**Hambach open-pit lignite mine**, DEU, real modern terrain accepted under
the AD-010 mixed-era posture, flagged for Matthew's review with the resource
layer) + 20 IJsselmeer lake-bed hexes (−1..−5 m, genuinely below sea level).

**Downstream check (polder report):** every elevation consumer uses
upper-bound thresholds only (`_elevation_tier` > 1500; classifier glacier
> 2800 / mountain ≥ 800 / plateau ≥ 1500 / tundra ≥ 1800; steppe rule needs
lon > 25°E; moisture "wet" needs > 600 m) — polder hexes classify **identically**
to their clamped-0 selves (verified by field diff: only `geo.elevation_m`
changed on those hexes). SRTM void sentinels (−32768) are guarded to 0.0 at
the sample site and gated (`validate_full_bbox.py` land band [−120, 4900] m).

---

## AD-033 — Corrected slope computation: metric per-axis gradient at 90 m terrain scale; hill threshold restored to 8°

**Date:** 2026-07-02 (Sprint 6, P0-A fix 2)
**Status:** Accepted (thresholds subject to PM visual review of the regenerated maps)

### The defect was larger than briefed

The Sprint 6 brief described `np.gradient(elevation, 90.0)` as underestimating
E-W slopes ~1.6× (isotropic 90 m assumed, ~57 m E-W actual at 51°N). The
empirical probe found the DEM is **1-arcsec** (AWS skadi tiles: ~30.9 m N-S,
~19.7 m E-W at 51°N), not the 3-arcsec/90 m the pipeline assumed everywhere —
so slopes were underestimated **~3× N-S and ~4.5× E-W**.

### Decision (three parts)

1. **Metric per-axis gradient.** N-S pixel pitch from the raster transform
   (`|e|·111320 m`); E-W pitch additionally scaled by **cos(lat) per row**, so
   the correction tracks latitude across tiles and across Europe.
   (`ElevationProcessor.compute_slope`, now taking the transform.)
2. **90 m terrain analysis scale via a wide central-difference stencil**
   (k pixels per side, k = 90 m / N-S pitch → 3 for 1-arcsec, 1 for genuine
   SRTM3). Rationale: per-pixel slope at 1-arcsec measures micro-relief
   (embankments, road cuts) that the p90-per-hex statistic then max-biases —
   probe: 79 % of Belgian land ≥ the hill threshold. 90 m is the scale the
   classifier was designed for (every doc said "SRTM3/90 m"). The stencil —
   not block aggregation — is **window-invariant**, preserving the AD-024/025
   streaming/monolithic hex identity (block alignment would differ between a
   windowed tile read and the full raster; verified IDENTICAL post-fix).
   SRTM voids (−32768) are masked to NaN before the gradient (no void-edge
   spikes; nan-aware percentile; raster-edge stencil band NaN too).
3. **`SLOPE_HILL` restored 4° → 8°.** The in-code comment recorded that 8° was
   lowered to 4° "for rolling hills" — a compensation for the broken math
   (4° × the ~2–3× hex-level underread ≈ the intended 8–12°). With correct
   slopes, geographic ground truth (probe): flat Flanders reads 1.5–2.6°,
   Condroz 7.4°, Ardennes plateau ~9°, steep Ardennes valleys 11–16°. At 8°
   the PM-approved Belgian hill set is reproduced as a **strict subset**
   (66/66 kept) plus the Condroz/Ardennes-plateau/Thiérache terrain the broken
   math missed. Keeping 4° would have classified half of flat Brabant as hills.

### Output impact (the fix bundle delta, 2026-07-02)

Belgium: slope_deg on 765/775 hexes; 84 plains→hill, 4 hill→mountain
(steepest Ardennes valleys); hill 66→146 (19.8 % of land).
Benelux: slope_deg on 2,182/2,479; 167 plains→hill, 41 hill→mountain
(Eifel/Sauerland/Rhine gorge); 7 city-fringe hexes correctly left urban
footprints (their fringe eligibility was an artifact of under-read slope).
`elevation_tier` redistributes (~60 % of land reads `hilly` ≥3°); tier is
descriptive metadata and its 3/10/20 bands were NOT recalibrated this sprint.

**PM sanity-check items:** 4 Belgian + 41 Benelux `mountain` hexes (≥20° p90
at 90 m scale — real gorge/low-mountain terrain, but "mountain" as a *biome*
is a game-feel call); the `hilly` tier share. Both tunable by threshold only.

### Validation

Gates recalibrated (parameterized `validate_full_bbox.py`): land slope p90
bands, biome-share tripwires, elevation plausibility (land ∈ [−120, 4900] m,
no void sentinel, slope ∈ [0, 60]°). `check_rivers` / `check_provinces` /
`compare_hex_outputs` all green.

---

## AD-034 — Grid parity normalization: one adjacency convention for all artifacts

**Date:** 2026-07-02 (Sprint 6, P0-A fix 1)
**Status:** Accepted

### The known neighbor defect was a parity defect

The pre-Sprint-6 note said `coords.offset_neighbors` disagreed with
`grid.HexGrid.neighbors` "on every cell". The Sprint 6 probe sharpened this:
it disagreed on 100 % of cells on **Belgium and wceurope** but on **0 % on
Benelux**. Mechanism: the grid's internal `q_min` (an artifact of the
projected bbox extent) has arbitrary parity per bbox, and exported
`col = q − q_min + 1` — so *which JSON columns are the half-row-shifted ones*
differed per artifact (Belgium/wceurope shipped odd-cols-shifted-north;
Benelux even-cols-shifted). `coords.py`'s odd-q math was internally coherent
but matched only even-`q_min` grids; the old gate script even brute-forced
both parities per file. Any fixed consumer decode (Unity reads `odd_q`) was
necessarily wrong for half the artifacts. `is_coastal` was therefore computed
against wrong neighbours on Belgium/wceurope (9 Belgian hexes flipped on
regeneration; Benelux was accidentally correct — 0 flips).

### Decision

1. **`HexGrid._build_grid` forces `q_min` odd** (pure renumbering; verified
   identical cell sets on all three shipped bboxes — Benelux cols shift +1).
   Invariant forever: **odd JSON `col` ⇔ column shifted +half row north**.
   Odd parity chosen because it matches the artifacts Unity demonstrably
   consumes correctly (wceurope in StreamingAssets, Belgium).
2. **Exactly one neighbor implementation**: `grid.HexGrid.neighbors`, backed
   by the module-level **`OFFSET_NEIGHBOR_DELTAS`** table (keyed by column
   parity, valid in both internal (q,r) and exported (col,row) space since
   the parities are now equal). `hex/coords.py` is **deleted** — its
   `offset_neighbors`/`CUBE_DIRECTIONS` were the divergent second
   implementation, and its remaining conversions/distances had zero callers
   and the same parity trap. Validators import `OFFSET_NEIGHBOR_DELTAS`
   instead of re-deriving deltas.
3. **Permanent gate**: `tests/test_neighbor_consistency.py` asserts the
   parity invariant on multiple bboxes, verifies neighbors against raw
   geometry (exact hex-spacing distance), asserts the table matches
   `grid.neighbors` in both parity views, and — if a `hex.coords` module
   ever reappears — asserts any neighbor-named callable in it agrees with
   `grid.neighbors` on every cell.

**Unity note (v1.0.5 coordination):** Belgium and wceurope coords are
unchanged; **Benelux cols renumber +1** at its next export. The schema doc now
states the normalized convention precisely so `HexCoord.cs` can assert it
instead of assuming it.

---

## AD-035 — 1930 eastern boundaries and provinces from OpenHistoricalMap (CC0)

**Date:** 2026-07-03 (Sprint 6, P0-C)
**Status:** Accepted (pending Matthew's ratification before push — this AD is
the "propose before building" record; the probe evidence is below)

### Problem

Modern Natural Earth polygons are invalid for 1930 east of Germany (AD-018
anticipated this): the 1922–1937 German–Polish border (Upper Silesia
partition, Polish Corridor, East Prussia exclave), the Free City of Danzig,
1930 Poland's eastern extent (Riga line), Memel under Lithuania. The AD-018
ship rule excludes the obvious historical GIS sources: historical-basemaps
(CC BY-NC-SA, removed in Sprint 3), CShapes 2.0 (CC BY-NC-SA), MPIDR/HGIS
collections (non-commercial), Euratlas/GeaCron (proprietary). Hand-digitizing
~3,000 km of 1930 border polylines from PD scans was the fallback plan.

### Decision: extract from OpenHistoricalMap via its Overpass API

**License (verified 2026-07-03, openhistoricalmap.org/copyright):**
"OpenHistoricalMap data is dedicated to the public domain under a Creative
Commons **CC0** dedication." CC0 is explicitly AD-018-acceptable. We attribute
in `data_sources` anyway as good practice.

**Coverage + quality (probed 2026-07-03):** OHM boundary relations are
date-versioned (`start_date`/`end_date`); querying entities valid on the
scenario date **1930-01-01** returns a complete, mutually consistent set for
the expanded bbox: Deutsches Reich (rel 2696515, 1922-06-20→1935-03-01 — the
post-Silesia-partition, pre-Saar-return snapshot), Polska (2692205, incl.
Kresy/Riga line), Freie Stadt Danzig (2691478), Československá republika
(2692233), Österreich (2858751), Magyar Királyság, Lietuva (incl. Memel),
Latvija, Danmark, Sverige, Soviet Union, România, Jugoslavija. Assembly test:
Danzig (749 vertices) and Provinz Oberschlesien (4,464 vertices) polygonize
into valid polygons and pass all town-allegiance spot checks including the
fine 1922 partition line (Gleiwitz/Beuthen German; Katowice/Königshütte/
Rybnik/Tarnowskie Góry Polish; Gdynia outside Danzig). This beats any
achievable hand-digitization.

**Provinces too:** OHM admin_level=4 carries the actual 1930 units — all
Prussian provinces (Ostpreußen, Pommern, Nieder-/Oberschlesien, Brandenburg,
**Grenzmark Posen-Westpreußen**, Rheinprovinz, Westfalen, …), the German
Länder, the 16 Polish voivodeships (in their 1930-01-01 versions — several
relations are date-versioned within 1930), the Czechoslovak lands (Země
Česká, Země Moravskoslezská, Podkarpatská Rus; Slovakia derived as country
minus the other lands if no relation), and the 9 Austrian Bundesländer.

### Scope of the boundary layer rebuild

1. **Countries** (`boundaries_1930.geojson`): keep the existing NE-derived
   geometry for BEL/NLD/LUX/FRA/CHE/ITA (1930 == modern at sub-hex accuracy;
   preserves Benelux gates). Replace **DEU** with the OHM 1922–1935 Reich
   polygon; add POL, **DZG** (Danzig), CSK, AUT, HUN, LTU, LVA, DNK, SWE,
   ROU, YUG, SOV (SOV bbox-clipped — the in-bbox strip east of the Riga
   line; the full USSR relation is impractically large).
2. **Saar consequence (flagged deviation):** the OHM Reich correctly
   EXCLUDES the Saar Basin territory (League-administered in 1930; returns
   to Germany 1935 — a natural scripted-event hook). It becomes country
   **`SAA`** (geometry = modern DEU minus OHM Reich, clipped to the Saar
   area), consistent with the province layer that already treats Saar as
   separate (AD-027). This CHANGES shipped Benelux/Belgium output (~15–30
   hexes DEU→SAA); gates recalibrated; called out in the close report.
3. **German provinces replaced with the real 1930 boundaries** (OHM) —
   retiring the AD-027 meridian/parallel cut-line approximations for
   Rheinland/Westfalen and the Hessens. Changes some German hexes'
   `province_at_start` in shipped Benelux output (the Ruhr-edge fuzziness
   AD-027 accepted is now resolved correctly); delta-reported.
4. **New provinces + capitals/sub-capitals** (AD-023/027 model) for POL,
   CSK, AUT, and eastern Prussia, hand-curated metadata (capitals from 1930
   administrative fact; sub-capitals by industrial/rail/strategic weight —
   e.g. Gdynia, Sosnowiec, Drohobycz oil, Ostrava/Vítkovice, Plzeň/Škoda,
   Leoben/Erzberg, Steyr). Framed-but-unauthored countries (HUN, LTU, LVA,
   DNK, SWE, ROU, YUG, SOV) get country-only coverage, like AD-028 CHE/AUT/
   ITA — provinces deferred to their own expansion sprint.
5. **Reproducibility:** relation IDs pinned in the builder
   (`tools/build_boundaries_1930_east.py`); extraction date + OHM provenance
   recorded in the geojson metadata and `data_sources`; raw Overpass
   responses cached under the wargame cache dir. Builder self-checks the
   town-allegiance table before writing; the same table gates the eastern
   artifact in `validate_full_bbox.py`.

### Rejected alternatives

- **Modern admin-line proxies** (voivodeship/powiat edges as the 1930
  border): 10–40 km errors on the Corridor and Silesian stretches — worse
  than the ±1 hex (10 km) target, and unverifiable without exactly the
  reference data OHM already provides.
- **Hand-digitizing from PD atlases:** slower, error-prone, and strictly
  dominated by OHM's existing digitization (which passes the same
  town-allegiance verification we would have used).
- **CShapes 2.0 / historical-basemaps / MPIDR:** license-excluded (AD-018).

## AD-036 — Starting infrastructure (port / airfield / fortification) is AUTHORED scenario data, not pipeline detection

**Date:** 2026-07-06 (Sprint 7, P0-B)
**Status:** Accepted — Matthew's ruling, now project policy

`infrastructure.port`, `infrastructure.airfield`, and
`infrastructure.fortification` remain schema fields but are **NOT populated by
the pipeline**. Starting infrastructure is **construction-system scenario
data**: airfields, port facilities, and fortifications are things the player
builds and upgrades, and where the 1930 scenario begins with a level-1 or
level-2 structure will be **AUTHORED** (like `data/resources/resources_1930.geojson`,
F-2) when the construction system exists — not sniffed from modern OSM/OHM.

### Why the reversal

A mid-sprint attempt to *detect* these from OSM/OHM (ports via
`harbour=yes` / `industrial=port`; airfields via OHM `aeroway=aerodrome`) was
the wrong model. Modern OSM harbours and aerodromes are present-day
facilities, not the 1930 starting network, and detection cannot distinguish a
"level-1 field the scenario starts with" from a "modern container terminal."
The test any future pipeline work must pass: **a system that consumes the data
exists or is in the current sprint.** Empty inert schema fields are NOT
defects — do not "fix" them by re-adding detection. (This AD exists precisely
because a session did exactly that; the note is now in `hex-schema.md`,
`CLAUDE.md`, and the code itself.)

### As-built

The OSM/OHM port-detection path is RETIRED as dead code:
`DataDownloader.get_ports` + `_overpass_to_gdf` deleted; the `vector` and
`streaming` port fetches removed; the sampler no longer sniffs
`vector_data.ports`. `infrastructure.port` = `False`, `airfield` = `False`,
`fortification` = `"none"` for every hex, awaiting the authored layer.
`bridge` / `road` / `rail` are terrain facts and remain sampled.

### Fortified-anthrome independence

The 28 eastern hexes with `settlement.anthrome = "fortified"` and
`infrastructure.fortification = "none"` are correct as-is — the fields are
independent by design. `anthrome=fortified` is descriptive LAND CHARACTER
(from OSM `landuse=military`; drives tactical-map-pool selection per AD-015);
`infrastructure.fortification` is future construction state. One line in the
schema doc; no field-linking, no gate.

### Guard + sanctioned replacement path

`validate_full_bbox.py` carries an INERTNESS GUARD: while no authored
infrastructure layer exists it fails if anything re-populates
port/airfield/fortification (the regression this AD exists to prevent — a
session already did it once). **The guard must not trip AD-036's own
replacement mechanism.** Sanctioned path: the authored layer lands at
**`data/infrastructure/infrastructure_1930.geojson`** (the `resources_1930`
pattern). When that file exists, the inertness guard retires automatically and
emits a reminder to add POSITIVE validation for the new layer; until then it
enforces inertness. Fold the file into the streaming tile-cache input hash
(like boundaries/provinces/resources) when it lands, since it will feed
per-hex tile content.

---

### Addendum to AD-035 (Sprint 7, 2026-07-06) — no city-provinces; province layer complete

**Matthew's ruling: no city-provinces** (HOI4-style state model). Berlin's
province polygon merges back into **Brandenburg**; Berlin becomes
Brandenburg's *provincial* capital (it remains the Reich capital), Potsdam
demoted to sub-capital — holding Berlin = holding Brandenburg under the
AD-023 capture semantics. The Sprint 6 Wien-into-Niederösterreich merge
(then a fallback for a missing OHM hole) is **ratified** under the same
principle. `DEU_BERLIN` is REMOVED from the layer — flagged to Unity in the
Sprint 7 handoff (province ids are about to be referenced by save files).

**Province layer completed** (`tools/build_provinces_1930_backfill.py`,
runs third in the chain west → east → backfill): 25 Swiss cantons (Jura
merged into Bern — 1979 split undone for 1930), 6 Italian compartimenti
(Valle d'Aosta into Piemonte — 1948 split undone; Trentino-Alto Adige →
Venezia Tridentina; Friuli-Venezia Giulia split at 13.35°E into Veneto /
Venezia Giulia per the AD-027 cut-line pattern — approximate, flagged), and
16 eastern-French départements (préfecture = capital). All NE admin-1
derived, `era: "1930-stopgap"`, pending Matthew's historical review like
the Sprint 5 western set. NE data quirks handled: Italian admin-1 rows are
provinces (grouped via their `region` column); "Haute-Rhin" misspelling;
French-spelled "Lucerne". Layer totals: **138 provinces / 138 capitals /
138 sub-capitals**; countries with authored provinces: BEL NLD LUX FRA DEU
POL CSK AUT SAA DZG CHE ITA (HUN/LTU/LVA/DNK/SWE/ROU/YUG/SOV remain
country-only per the AD-035 frame rule).

---

## AD-037 — Provinces for the eight frame nations, and the province TIER rule

**Date:** 2026-08-26 (Pass A)
**Status:** Accepted

### The defect

Eight nations framed the east-expansion map with a `country_at_start` but an
**empty `province_at_start` on every hex**: DNK, HUN, LTU, LVA, ROU, SOV, SWE,
YUG (3,105 land hexes). AD-035 authored them as country-only frames on the
reasoning that a frame nation needs no internal detail.

That reasoning stopped holding once the economy shipped. `ProvinceIndex.Build`
skips any hex with an empty province, so those eight indexed **nothing** — and
the consequence is larger than "no manpower":

* Money and manpower walk a province's capital + sub-capital seats. No province,
  no seats, no income.
* **Resource yield is also a per-province walk** (`ProvinceInfo.ResourceHexes`),
  not a per-hex sweep of the map. So DNK's 147 resource hexes and ROU's 156 —
  407 across the eight — produced nothing either.
* All seven pools seed from monthly income, so each of the eight bootstrapped at
  zero across the board, and any of them holding a unit fired an unpaid-upkeep
  announcement every month forever. (The S9-1 clamp stops the spiral; it does
  not stop the event-log spam.)

An earlier read of this defect scoped it as "`admin_tier` is `none` on those
nations, populate it". That is a symptom, not the cause: `admin_tier` is set by
the province reconcile pass and is read *after* `ProvinceIndex` has already
skipped the hex. Populating it fixes nothing.

### The tier rule

There is no single "1930 subdivision tier" to author to. National top-level
units of 1930 differ by an order of magnitude in size (Prussian provinces
~19,000 km²; Swiss cantons ~1,600), and the shipped map sits at the coarse end
(DEU 29 provinces, POL 16, CSK 4 lands, AUT 9 Bundesländer). Because building
slots are per-province and capture flips per-province, a nation authored at a
finer tier silently receives more industrial capacity per unit area and finer
capture granularity than its neighbours, for no authored reason.

**Rule: a province is the coarsest unit that is either a genuine 1930
administrative unit or a genuine grouping of them, chosen to land in the density
band the shipped map already uses.** Never a meridian or parallel cut-line —
AD-035 retired those.

Applied: ROU as its historical provinces (the CSK "lands" analogue), LTU as
ethnographic regions plus the Memel Territory (a genuinely distinct 1930
entity), DNK as landsdele grouped from real amt polygons, YUG as 1929
banovinas, SWE as landskap, LVA and SOV as single framed regions.

**HUNGARY IS A DELIBERATE EXCEPTION.**

*Authority:* **Matthew, directly, 2026-08-26**, in the Pass A working session.
Not relayed and not a seat decision. The pass plan put both options in front of
him — the four-macro-region grouping (Dunántúl / Duna-Tisza köze / Tiszántúl /
Északi-hegyvidék), which lands Hungary squarely in the map's density band and
was the author's recommendation, against the genuine county tier, which does not
— together with the density consequence stated in those terms. His instruction,
verbatim: *"change the hungary tier, note what you did and reasoning in
documentation."* This record is the "note what you did and reasoning" half of
that instruction.

Hungary is therefore authored at the genuine **county** tier rather than grouped,
accepting that it lands well below the DEU/POL hexes-per-province figure and is
among the densest nations on the map.

**Hungary's density is AUTHORED, not drift. Pass B must not flatten it.** The
density audit will show HUN as one of the densest nations on the map, sitting
near CHE and far from DEU/POL — and the two are NOT the same finding. CHE's
density is an accident of authoring Switzerland at its own canton tier without
reference to the rest of the map; Hungary's is a deliberate PM decision to keep
the real 1930 county tier with the consequences understood. A normalisation pass
that treats "outlier" as "defect" and regroups both would be reversing a ruling.
Whatever pass B does about CHE, HUN stays at 19 unless the PM says otherwise.

Hungary's 1930 set was 25 counties; the authored set is **19**. The modern
megyék are 1950 amalgamations (Borsod-Abaúj-Zemplén = Borsod-Gömör-Kishont +
Abaúj-Torna + Zemplén, and so on) and are **not reversible from any
public-domain vector source** — OHM has no 1930-valid Hungarian relations and
Natural Earth carries only the modern set. Each province therefore records the
1930 counties it absorbs in its `notes`. Budapest is merged into Pest rather
than authored as a city-province, per the AD-035 addendum that put Berlin back
into Brandenburg.

### Geometry provenance

OpenHistoricalMap was probed for `admin_level` 4 and 6 relations valid at
1930-01-01 over each of the eight. It has **none** for HUN, ROU, LTU, SWE, YUG
or the Soviet strip. It does have the 18 Danish amter and the Memelland Kreise.
So:

* **DNK** — OHM amt relations (CC0), grouped into landsdele. Genuine 1930 lines.
  The four Sønderjylland amter (created 1920, absent from OHM) are derived as
  land-minus-the-assembled-amter, the construction AD-035 used for Slovakia.
* **LTU_KLAIPEDA** — OHM Memel + Heydekrug + Pogegen Kreise. Genuine 1930 lines.
* **Everything else** — Natural Earth admin-1 unions clipped to the 1930 country
  polygon: MODERN internal lines, 1930 external lines, `era: "1930-stopgap"`.
  This is the AD-027 precedent BEL/NLD/FRA/CHE/ITA already ship under. The error
  it carries is an internal division line, never a national border.

Provenance is recorded per feature in `notes` so historical review can separate
the two classes.

### Three source hazards worth recording

1. **Natural Earth punches city-level units out of their county.** Hungarian
   "Urban county", Romanian/Croatian "City" and Latvian "Republican City" are
   separate admin-1 rows, and the county polygon has a hole where they sit.
   Unioning counties alone put sixteen Hungarian county capitals — Kecskemét,
   Miskolc, Debrecen and Győr among them — **outside their own province**, where
   `assign_admin_tiers` would never have designated them and the province would
   have shipped seatless. Each such unit is absorbed into the primary unit it
   shares the longest boundary with.
2. **OHM admin relations are land-only; the 1930 country polygons include
   territorial waters** (DNK: 64,102 km² of polygon over 42,683 km² of land).
   Deriving "country minus the amter" without intersecting land first yields one
   20,900 km² blob of Kattegat with Sønderjylland attached as a lobe. Every
   derivation is intersected with Natural Earth land first, and the coverage
   self-check measures against country ∩ land, not the wet polygon.
3. **Sorø Amt's outer ways do not close in OHM** (17 open lines of 20), and
   Staden København and Amager were administratively separate from Københavns
   Amt in 1930 and so lie outside it. Both are recovered from leftover land —
   the unclosed amt by its own OHM-reported bounds, the town land by absorption
   into the nearest amt. The recovery is generic, not hard-coded to Sorø.

A fourth, smaller one: three seaport capitals (København, Karlskrona, Split)
test as lying outside their own province because Natural Earth and OHM both cut
the coastline a few hundred metres inland of the real quay. The capital-anchor
self-check accepts an offset up to 0.05° — the same order as the coastal snap
`assign_province` already applies to hex centres — and prints every use, so the
tolerance cannot hide a genuine misplacement. Observed offsets: 0.000°, 0.002°,
0.001°.

### Frame scoping

LVA, SWE, YUG and SOV are authored for the block the bbox actually frames, not
their whole national territory. `validate_full_bbox.py` gates this: adding a
country to the province metadata puts it in `covered_countries`, which
hard-fails if under 98% of its land hexes carry a province. Widening the bbox
therefore fails loudly rather than silently producing province-less hexes.

Builder: `tools/build_provinces_1930_frames.py`, **append-only** — the 138
existing provinces are copied through untouched. Runs fourth in the chain
(west → east → backfill → frames).

---

## AD-038 — `settlement.population` exported per hex (schema v1.0.6), and modern-derived provenance labelled

**Date:** 2026-08-26 (Pass A)
**Status:** Accepted

### What changed

`geo/osm_downloader.py` has always parsed a real population integer off each OSM
settlement node, and `hex/sampler.py` has always used it — it decides
`settlement.type` outright when known (OSM `place` tags are noisy) and scales the
urban-sprawl radius. `output/game_data_exporter.py` then discarded it. Schema
**v1.0.6** exports it as `settlement.population` (int).

### Shape: per hex, not per province

The export document is `{schema_version, map_metadata, hexes}`. **There is no
province-level record**, so a province-level population would have meant a new
top-level section and a second place for the loader to hold province state. The
sim's contract (`ProvinceInfo.Population`) is satisfied by summing a province's
hexes inside `ProvinceIndex.Build`, which already walks every hex exactly once
and already derives `ResourceHexes` by the same means.

**Population attaches to the settlement NODE, not to the footprint.** A city's
count sits on the city's own hex; the ring hexes of its urban sprawl carry `0`,
so a province sum is never multiplied by the sprawl radius. The exception is a
ring hex that is itself a distinct named town with its own node — Lier inside
Antwerpen's footprint, Herstal inside Liège's, Waterloo inside Brussels' — which
keeps its own count, because those are separate inhabitants and zeroing them
would delete about 400,000 people from the Belgium frame alone. Every settlement
is therefore counted exactly once. (The gate that first shipped with this asserted
the cruder rule "suburb hexes carry 0" and failed on those fifteen towns; the
gate was wrong, not the data, and now asserts the real invariant: population > 0
implies the hex has a settlement name, and no ring hex repeats its parent's
count.)

A capital designated by the province reconcile pass below the hex tagging floor
takes the population of the node it matched.

**`0` does not mean "uninhabited".** It means no node claimed the hex, or the
node carries no `population` tag — about 5% of named settlement hexes in the
Belgium frame (Aalst, Mechelen, Hasselt among them). A province sum is a FLOOR,
not a census, and should be treated as one.

### The provenance warning is the load-bearing part

The value is a **2020s OpenStreetMap `population` tag** standing in for a 1930
figure. It is roughly serviceable for cities that stayed inside their 1930 states
and **materially wrong** wherever the twentieth century moved the people: the
German eastern territories, the Sudetenland, Memel, Danzig, and the Bessarabian
and Bukovinan strips all read post-expulsion, post-resettlement populations.

It is still the better of the two available options — the alternative was
deriving population from the `population_class` ordinal, which discards a real
measurement to invent a fake one — but the failure that costs us is someone
mistaking it for authored 1930 data in eight months. It is therefore labelled
**MODERN-DERIVED — NOT AUTHORED 1930 DATA** in `docs/hex-schema.md`, in the
exporter, in the sampler, and here, and it joins the historical-review list.

### `resources.industry_level` carries the same defect and is now labelled

Investigating the above surfaced that `industry_level` has identical provenance
and had **no documentation at all**. It is not authored and not a 1930
measurement: `hex/sampler.py` sets it to `1` when the dominant landuse polygon
at the hex centre is 2020s OSM `landuse=industrial`, else `0`. Its range is
`{0, 1}`, never higher, despite the schema and the Unity loader admitting `0–N`.

The sim multiplies non-agricultural resource yield by `(1 + industry_level)`. On
the shipped eastern artifact 72 hexes carry the flag but only **8** also carry a
resource for it to multiply (4 DEU, 2 CSK, 2 POL), so the entire live effect is
eight doubled hexes. It is documented as modern-derived, added to the
historical-review list, and flagged as a retirement candidate once real
facilities are placed — a dispersed baseline that scales with research does not
want an accidental per-hex multiplier from a modern industrial-estate polygon.

---

## AD-039 — The sampling-code cache key is normalized for line endings

**Date:** 2026-08-26 (Pass A)
**Status:** Accepted

AD-030 rule 2 keys the streaming tile cache on an md5 of the sampling-critical
source modules, so any edit to code that produces per-hex content invalidates
stale tiles. It hashed the files' **raw bytes**.

On a checkout with `core.autocrlf=true` (the default on Windows), the bytes on
disk depend on how a file arrived rather than on what it contains. Restoring a
single *unmodified* file from git during this pass rewrote `geo/provinces.py`
from LF to CRLF and changed the whole key from `c5bc4980` to `4ee3935b` —
silently invalidating all 242 cached tiles for a file whose content had not
changed. Two clones of the *same commit* on different platforms compute
different cache keys and cannot share a tile cache.

This never produced wrong output; the failure mode is a needless ~40-minute
re-tile and a cache key that is not reproducible across machines. `\r\n` and
`\r` are now normalized to `\n` before hashing, which makes the key depend on
content alone. The key changes once when this lands (folded into the Pass A
re-tile, which was happening anyway) and is stable and platform-independent
afterwards.

The same normalization is applied to `_input_data_hash`, which hashes the
boundaries / provinces / resources GeoJSON and has the same exposure.

---

## AD-040 — Cache snapshot manifest: every artifact records, and can prove, what built it

**Date:** 2026-09-29 (Sprint 11)
**Status:** Accepted

The OSM cache is a one-way door. A part that ages past the TTL is refetched and
**overwritten in place**; there is no archive. AD-030 made a partial fetch fail
loud and the TTL a runtime lever (`PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS`), but
neither records what an artifact was built from, so the provenance of everything
already shipped from a part dies with that part. That has now cost this project
a snapshot twice — most recently 50 settlement parts and 11 of 50 waterways
parts behind the shipped eastern artifact, which permanently removed byte
identity as a definition of done and forced churn to be measured instead.

**The decision: a receipt that can be checked.** After a successful export, both
paths write `<artifact-stem>_manifest.json` beside the artifact recording every
cache part the run consumed — content sha256, size, fetch time, and whether the
part was served from cache or fetched by this run — plus the pipeline version
and commit, the config file and its hash, the resolved bbox, `SCHEMA_VERSION`,
`STREAMING_VERSION`, the sampling-code and input-data hashes, the exporter's own
source hash, and the artifact's raw and content sha256 (from
`tools/artifact_hash.py` — there is still exactly one artifact hasher).

And a verb, because a manifest nobody can check is a receipt, not evidence:

```bash
uv run python -m wargame_cartographer.manifest verify \
    output/para_bellum_belgium_test_hex_terrain.json
```

reports every part as `match`, `changed` or `missing` and exits non-zero if any
is not `match`. `--root osm=<dir>` points it at a copy of the cache.

### What the design turns on

- **Content, not modification time.** A refetch that produced identical bytes is
  not a change; a rewrite that preserved size and mtime is.
- **The run may reuse a hash; the verifier never may.** Hashing ~760 MB of parts
  on every run would be a third of a 75-second Belgium run, so `write_manifest`
  memoizes hashes in `~/wargame-cartographer/cache/content_hashes.json` keyed by
  path + size + mtime, and records `hash_reused` per part and in
  `hash_summary`. `verify` ignores that memo and rehashes from disk: a
  mtime-keyed shortcut is precisely the assumption the verifier exists to test.
  Measured: 13 parts / 759 MB, 0.54 s to write warm, 2.4 s to verify cold.
- **After a successful export only.** A failed run leaves no receipt. If the
  manifest itself cannot be written the run raises — the artifact is already on
  disk by then, and a silently missing receipt is the failure class this exists
  to end.
- **No archiving.** Copying tens of GB per run is a different decision with a
  different cost and it is not signed. The manifest makes the loss *detectable*,
  which is what was missing.
- **One module, one entry point.** `src/wargame_cartographer/manifest.py`. The
  fetch sites call one function, `record_part`, which never raises: bookkeeping
  must not take down a run.
- Manifests are **un-ignored in `.gitignore`** (`output/*_manifest.json`) while
  artifacts stay ignored. Commit the receipt, never the data.

### What it protects against

A cache part that was refetched, truncated, corrupted or deleted after an
artifact shipped — detected part by part, against the artifact's own hash, with
the fetch time and the TTL pin that were in force recorded alongside.

### What it does NOT protect against

- **It does not recover anything.** Once a part is overwritten the previous
  bytes are gone. This converts a silent loss into a detected one; it is not a
  backup.
- **It does not stop a refetch.** The manifest is written at the *end* of a run.
  A run that expires a part still destroys it, and the manifest only shows it
  afterwards. The guard that fails loud *before* sampling — compare the cache
  against a blessed manifest and refuse to start — is the natural next step and
  is deliberately not in this change.
- **Natural Earth and SRTM are recorded but not governed.** Their caches have
  their own freshness rules and are not covered by
  `PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS`. In particular the NE freshness check
  stats the unpacked shapefile *directory*, whose mtime does not move when the
  files inside are overwritten, so once a layer ages past its TTL **every run
  re-downloads it, on every tile** (15 tiles → 30 downloads on a Belgium
  streaming run). The manifest makes that visible — it is how it was found —
  and marks those parts `role: fetched`. Fixing the check is a fetch-path change
  and is not in this decision.
- **It does not describe the Overpass query.** The cache key is the bbox hash
  only (AD-008 and the `osm-cache` skill's second trap). Two parts with the same
  hash may have been fetched under different query text, and the manifest
  cannot tell them apart.
- **It is not a signature.** Anyone who can rewrite a cache part can rewrite the
  manifest beside it. It answers "did this move?", not "who moved it?".
- **The repo-committed inputs** (boundaries / provinces / resources GeoJSON) are
  covered only by the existing `input_data_hash` digest, not listed part by
  part. They are in git, which is the stronger guarantee.

### Cost paid once

The hook in `run_streaming_pipeline` edits `streaming.py`, which is in
`_SAMPLING_CODE_MODULES`, so the tile-cache key moves `dd2b5bdc → decd1dee` and
every cached tile is invalidated once. That is the accepted trade in AD-030 rule
2 (correctness over cache-hit rate). **The re-tile must be run with
`PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS=120` pinned**, and note that the oldest
parts in the cache are at 110 days — the 120-day pin has about ten days of
headroom left before it stops protecting anything.

### Verification

Belgium (775 hexes, monolithic, TTL pinned at 120, no part refetched): 13 parts
/ 759 MB recorded, manifest written in 0.54 s, verifier reports **13 match, 0
changed, 0 missing**, exit 0. The same config through the streaming path records
**15 parts** (the sub-bbox part gpkgs rather than the merged full-bbox caches,
plus the tile count and tile-cache dir) and verifies clean — and its artifact
`content_sha256` is **identical** to the monolithic run's, which re-confirms the
AD-025 streaming-equals-monolithic invariant from a direction that had not been
used before. One byte flipped in a **copy** of one part, with the file's size
unchanged: that part reports `changed`, the other twelve report `match`, exit 1.
Gate: `tests/test_manifest.py`.

---

## AD-041 — Natural Earth freshness is read from a stamp, not from the folder's mtime

**Date:** 2026-09-29 (Sprint 11)
**Status:** Accepted

A Natural Earth layer unpacks into a **directory**, and a directory's mtime does
not move when the files inside it are overwritten. `get_natural_earth` asked
`_is_fresh(cache_path)` about the directory, so the check reported the age of the
**first** extraction forever. Once a layer aged past the 30-day TTL it
re-downloaded on every run and never returned to fresh. Measured before the fix:
`ne_10m_land`'s directory mtime was 103.8 days old while every file inside it had
been rewritten that morning; a 15-tile Belgium streaming run pulled `land` and
`lakes` **30 times**.

It was not only wasted bandwidth. `ne_10m_rivers` is the AD-029 river
**selection** source, so map content was being silently re-fetched on every run
with nothing comparing before against after. It happened to be byte-benign on the
runs measured here — the artifact's `content_sha256` was identical across a
re-download — but that is a statement nobody could have made before AD-040, and
it was the AD-040 manifest that surfaced this at all, by marking those layers
`role: fetched` on a run that should have had no fetches.

Freshness now comes from an explicit stamp, `ne_10m_<layer>.fetched`, written
after a **completed** extraction.

**A stamp rather than stat-ing a file inside**, because a stamp means "a complete
extraction finished at time T". Stat-ing the `.shp` cannot tell a finished
extraction from an interrupted one, so a half-unpacked directory would keep
passing as a good cache; it also depends on per-layer file naming, which varies
(`ne_10m_land.shp` against `ne_10m_rivers_lake_centerlines.shp`).

**Beside the directory rather than inside it**, because the layer directory's
bytes are a manifest part (AD-040) and a stamp inside would change that part's
content hash every time the layer refreshed — `manifest verify` would report
`changed` for a file the pipeline itself had just written.

**Landing it downloads nothing.** A cache with no stamp adopts the newest file
inside as its fetch time and is stamped backdated to that. Verified on the real
cache: all six layers the pipeline calls adopted an age of 0.1 days and read
fresh. `ne_10m_states` adopted 104.0 days and reads stale, which is correct — it
is genuinely that old and nothing in the pipeline calls it.

**Not fixed here:** the NE cache keeps its own TTL constant and is **not**
governed by `PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS`. Unlike an Overpass part, an NE
layer is re-downloadable from a stable published URL, so its refresh is not a
one-way door — but it is still map content changing under a run, which is why
AD-042's guard checks NE parts against their own TTL alongside the OSM ones.

Evidence: zero "Downloading Natural Earth" lines on the next Belgium run against
five on the run before; all 13 manifest parts reading `role: cache_hit`; manifest
written in 0.19 s with every hash reused; `manifest verify` 13 match / 0 changed
/ 0 missing. Gate: `tests/test_ne_freshness.py` (15 checks, no network), which
pins the mechanism, the fix, the stamp's location, the backdating migration, and
that the TTL still bites a genuinely old layer.

---

## AD-042 — The cache guard: a run that would destroy a snapshot refuses to start

**Date:** 2026-09-29 (Sprint 11)
**Status:** Accepted

AD-040 made a lost cache part **detectable**, but only after the run that lost
it: the manifest is written at the end. AD-040's own "does NOT protect against"
list named the gap and left it deliberately open. This closes it, and it was
chosen over simply raising the TTL pin, because a wider pin still ends in a run
that destroys a snapshot and reports it afterwards.

**Before any fetching or sampling**, both paths call `check_cache_before_run`,
which compares the cache the run is about to depend on against the **blessed**
manifest and raises `CacheGuardError` when a part:

- **changed** — its bytes differ from the blessed hash;
- **missing** — it is not there at all;
- **expiring** — it is within `EXPIRY_MARGIN_DAYS` (1 day) of the TTL **in force
  for its cache root**, so this run would refetch it and overwrite it in place.

The TTL is resolved per root and per run: `osm` reads
`PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS` (so the pin is what turns a refusal into a
pass), `vector` reads the Natural Earth constant, and `elevation` has none — a
cached DEM is kept until someone deletes it, so an ancient DEM is not a problem.

### Blessed means committed

The baseline is read from **git HEAD**, not from the working tree. Every run
overwrites the working-tree manifest, so using that would make each run its own
baseline: a part that moved would be flagged once and then quietly become the new
normal, which is the failure this is supposed to prevent. A baseline has to be
something a person blessed.

**A config with no committed manifest has nothing to check against.** The guard
says so in one line and the run proceeds; it does not derive a baseline from the
bbox. Inventing one would create a second source of truth about part naming
beside the downloader's, and a baseline nobody blessed is not evidence of
anything.

### The refusal is the feature

The message names the part, says what is wrong, says that an overwrite is
unrecoverable, gives the TTL pin for an OSM part, and gives the override:

    PARA_BELLUM_ALLOW_CACHE_REFETCH=1 <your command>

Only the exact value `1` overrides, and an overridden run says loudly what it
proceeded on. The override exists because **a guard with no findable escape gets
deleted rather than heeded** — and a deletion leaves nothing, while an override
leaves a manifest recording what the run actually used.

For the same reason the CLI catches `CacheGuardError` and prints the message on
its own. The first version let it propagate, and forty lines of click traceback
buried the one paragraph the operator needed. The library still raises.

### What the guard cannot catch

- **A config that has never shipped a manifest** — the first run of anything is
  unguarded, by design.
- **A part the blessed manifest does not list.** The guard checks the blessed
  part list, so a run whose bbox, config or code path pulls in a part the blessed
  artifact never used will fetch it unguarded.
- **A streaming threshold-comparison run** (`scalerank_override`), whose artifact
  name is suffixed `_sr<N>` and therefore matches no committed manifest. It
  proceeds unguarded, correctly — it is not a shipped artifact.
- **Anything outside the recorded roots**, including the repo-committed
  boundaries / provinces / resources GeoJSON (git covers those) and the Overpass
  query text, which the bbox-hash cache key ignores entirely (AD-008).
- **A part that expires mid-run beyond the margin.** The margin is one day; a run
  longer than that against a part within a day of its TTL is not covered.
- **Deliberate sabotage.** Anyone who can rewrite a part can commit a new
  manifest. This answers "did this move since someone blessed it?", not "who
  moved it?".
- It still **recovers nothing**. Every caveat in AD-040 stands; this only moves
  the moment of discovery from after the loss to before it.

### Cost

The hook edits `streaming.py`, which is in `_SAMPLING_CODE_MODULES`, so the tile
cache key moves again, to `d68b36c2`. No re-tile had happened since AD-040 moved
it, so the marginal cost is zero — but the next streaming run re-tiles and **must
be made with `PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS=120` pinned**. The guard will
now refuse it if it is not.

### Verification

With the pin, Belgium passes the guard and reports it in one line: *13 parts
match `HEAD:output/para_bellum_belgium_test_hex_terrain_manifest.json` and none
expire during this run*; the run completes in 61.6 s and `manifest verify` reads
13 match / 0 changed / 0 missing.

**Without** the pin — the 30-day TTL against parts 85 to 110 days old, which is
exactly the Sprint 10 disaster — both `wargame-map generate` and
`run_streaming.py` refuse, exit 1, and name all **seven** OSM parts they would
have overwritten. Zero cache parts were touched by either attempt (509 gpkg
parts, none younger than a day, before and after). Gate:
`tests/test_cache_guard.py`, 25 checks, no network and no real cache.

---

## AD-043 — The 1930 starting industrial base is a per-hex `facilities` array (schema v1.0.7)

**Date:** 2026-10-01 (Sprint 12, pass B)
**Status:** Accepted

The starting industrial base (power plants, steel mills, mines and rail yards)
is authored data, carried in the artifact and instantiated by the sim before the
first tick. This entry fixes its shape.

### Shape

There is a new top-level per-hex key `facilities`: an array of
`{kind, tier, name, deposit}`, which is `[]` on almost every hex.
`docs/hex-schema.md` is the field-level authority.

**Per hex, not per province.** The sim's `Facility` is keyed on `Hex`. Its
province and its owner are derived from that hex and are never stored, and the
slot rule (`SlotsOf`) counts a province's facilities by deriving each one's
province from its hex. A per-province record would force the start-state step to
choose a hex for each facility, which means re-deriving the placement this layer
exists to author. It would also add a second province-level structure next to
`ProvinceIndex`, so the sim would pay twice. Per hex, the start-state step is a
single walk of `hexes` and `ProvinceIndex` is untouched. This is the same
argument that put `settlement.population` on the hex (AD-038).

**Why an array.** The sim allows more than one facility on a hex (slots are a
province rule, not a hex rule), and the Ruhr needs it: Oberhausen and Hamborn
fall within one 10 km hex of each other.

**`kind` maps by name onto `FacilityKind`.** Only the four authored kinds are
emitted. A loader should reject an unknown kind, because a fifth kind is a
schema change.

**`tier` is the starting `Tier`, from 1 to 3**, recording relative scale. The
sim's `MaxFacilityTier` is the ceiling.

**`deposit` exists only for mines, and is `"iron"` only.** The sim can express
nothing but an iron mine (`RequiredDepositFor` has one arm; whether coal mines
should exist is Sprint 11 open item §7.3 and is Matthew's call). Coal reaches the
economy through the dispersed basin term as before. The field exists so that a
mine record can never be read as the wrong deposit, and so that coal mines are a
data change rather than a schema change if they are ever allowed.

### Assignment happens at export, not in the sampler

`geo/facilities.py` maps each authored point to its containing hex inside
`export_game_data`, which both the monolithic and streaming paths call. A
facility is one point on one hex and needs nothing a tile holds. Keeping it out
of `_SAMPLING_CODE_MODULES` and out of `_input_data_hash` means editing the
authored layer, which historical review will do, re-exports the artifact without
invalidating a single cached tile and without touching the OSM cache. It is the
cheapest regeneration this pipeline can do. (The steel retirement this pass
needs is the exception. It edits `resources_1930.geojson`, which IS in
`_input_data_hash`, so that one edit re-tiles every tile.)

### "The infrastructure network"

This means **rail yards**, a `FacilityKind` the sim already has, on top of the
`road` and `rail` levels that were already sampled. Ports, airfields and
fortifications stay inert under AD-036, because that AD's test (a system
consuming the data exists or is in the current sprint) still fails for them:
nothing in `Assets/Scripts/Sim` reads them. The authored layer is therefore
**not** at AD-036's reserved `data/infrastructure/infrastructure_1930.geojson`.
A file there would retire that AD's inertness guard for data that does not exist.

### The validator enforces what the sim would refuse

The authored base must be one the sim could have built. The builder refuses
to write the layer if the TABLE fails a static check (shape, steel, a nation
with a mill and no plant). `tools/build_facilities_1930.py --check <artifact>`,
which `validate_full_bbox.py` runs, fails an ARTIFACT on any of the following.
`tests/test_facility_checks.py` seeds each violation and asserts it is caught.

* a facility on a hex that is water, has no country, or has no province;
* a facility whose hex country differs from its province's baseline owner, which
  would give the facility to a different nation from the one that owns its slot;
* more facilities in a province than its seat's slot total (7/5/3/0);
* a mine whose hex does not carry its deposit;
* **a steel mill whose province holds no power plant** (constraint 1);
* **a nation whose authored power supply falls short of its authored draw**
  under the sim's current `[P]` tables (constraint 1, nation scale, because the
  sim's power pool is national);
* **any authored steel deposit** (constraint 2).

The power figures are mirrored from `GameState.cs` in one named place, and they
are unsigned placeholders. If the sim moves them, the mirror moves with them.

The artifact is also reconciled against the authored CSV. That catches a stale
GeoJSON, a facility silently dropped inside the bbox, and a coordinate that
slipped across a border onto a hex that is internally consistent but belongs
to the wrong nation.

**Province, owner, slots and both power checks hard-fail only on the shipped
map.** A test frame crops provinces until they have no seat (AD-M19) and can
cut a plant off at the bbox edge. The same authored layer then puts a
Lorraine mill in a 0-slot province in the Belgium frame, and loses Fenne in the
Benelux frame. Test frames report these checks instead of failing on them, and
a test-frame artifact is not a start state. Every other check fails on every
artifact.

**Provenance.** The layer is read outside both tile-cache hashes, so the
AD-040 manifest records `facilities_layer_sha256` and
`facilities_source_sha256` in its `run` block. Without those, nothing would
record which authored base an artifact carries.

### Landing order

Unity's staged east artifact is still **1.0.5**, under a 1.0.6 loader, so the
Pass A artifact was never staged. Nothing is staged at the current version, so
"artifact first" does not apply. The 1.0.7 artifact is produced here, and the
loader bump, `HexData`, the golden fixture and the artifact copy land on the
Unity side as **one** commit.

## AD-044 — AD-M03's transitional steel deposits retire into authored mills

**Date:** 2026-10-01 (Sprint 12, pass B)
**Status:** Accepted. Matthew approved the edit directly (2026-10-01), on
PB-MANAGER's ruling that it should happen.

`data/resources/resources_1930.geojson` carried **18** `resource_type: "steel"`
Point features: DEU 6, POL 4, CSK 4, BEL 2, AUT 2. These were steelworks,
authored as deposits because the sim had no mill. AD-M03 named them
transitional, to retire once mills existed. Sprint 11 built the mill, and pass B
authors one at each of the 18 sites under the same name at the same coordinates
(AD-043). All 18 are therefore **removed** from the resource layer, and the other
30 features are byte-identical.

**Why remove, not pin.** Keeping them would make every one of those sites yield
steel twice, once through the dispersed deposit term and once through the mill on
the same hex. The worst place for that is the Ruhr, where a player's steel
picture is formed. Pinning the legacy set would have left the fix to the sim,
which would have to ignore steel in the dispersed term: a code workaround for data
that is wrong.

**Consequences.**

* `resources.steel` is `false` on every hex. The field stays (additive-only), and
  the facility gate fails any artifact on which it is true. The sim's dispersed
  steel arm becomes **unreachable**, not wrong. Deleting it is PB-SIM's call.
* **Nobody in the east frame loses steel.** This was measured before the run,
  from the real export assignment. Each of the 14 east hexes that carried steel
  receives a mill on the same hex. HUN, FRA, CHE, YUG, LUX and SAA gain a steel
  source they did not have. BEL's two works lie west of the east frame edge,
  so BEL had no in-frame steel to lose.
* **National steel supply rises materially at the start** (DEU: 5 steel hexes
  become 11 mills). This is **placeholder-driven and not tuned.** The figure
  comes from the sim's `[P]` yield tables and is that seat's to compute, not
  this repository's. Sprint 16 is where it gets judged.
* **AUT's "distinct resource types" floor drops from 3 to 2** in
  `validate_full_bbox.py` and `check_resources.py`. Its third type was steel. The
  steel floors and steel spot points are gone from both, because steel is no
  longer a deposit type.
* `resources_1930.geojson` is in the tile-cache input hash, so this edit re-tiles
  every streaming tile once. That cost was accepted.

**Constraint 2 of pass B** ("no steel deposits are authored") is enforced by
`tools/build_facilities_1930.py`, which refuses to build while the resource layer
carries any steel feature, and on the artifact by the facility gate.
