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
