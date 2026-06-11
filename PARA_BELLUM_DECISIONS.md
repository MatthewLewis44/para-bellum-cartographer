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
**Status:** Accepted (reality, not a choice)

The Sprint 2 target bbox (2.5–8.8°E, 49.4–53.6°N ≈ 230,000 km²) produces
**840 hexes** at the locked 10 km hex standard (flat-top, center-to-vertex
10 km → ~260 km² per hex). The "~2,500 hexes" figure in sprint planning is
not reachable with this bbox at this hex size (it would require ~650,000
km², i.e. most of France + Germany). Hex size is a design constant; the
plan number was the error. Scale-spike implications: per-hex pipeline
stages have now been exercised at 3× Belgium, not 9× — the 100k-hex spike
remains a separate task.

---

## AD-010 — Coastal snap for 1930 country assignment

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
