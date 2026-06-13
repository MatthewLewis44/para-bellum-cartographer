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
