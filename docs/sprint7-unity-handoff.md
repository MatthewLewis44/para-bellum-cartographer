# Sprint 7 — Pipeline → Unity handoff

**Schema: v1.0.5 (unchanged).** No field add/remove/rename. Two existing
`infrastructure` fields flip from always-`false` to populated; the province
layer is complete and some province IDs changed. **Re-import all four
artifacts** (Belgium, Benelux, wceurope, east).

## Province-ID changes (save-file relevant — read this)

Province set: **92 → 138** provinces.

- **REMOVED: `DEU_BERLIN`** — Berlin's province merged into
  `DEU_BRANDENBURG` (Matthew's no-city-provinces ruling, AD-035 addendum).
  Berlin is now Brandenburg's *provincial* capital (still the national
  capital); holding Berlin = holding Brandenburg. **Any save fixture or test
  referencing `DEU_BERLIN` must switch to `DEU_BRANDENBURG`.** Hexes formerly
  `DEU_BERLIN` now read `DEU_BRANDENBURG`; `settlement.admin_tier = capital`
  moves to the Berlin hex within Brandenburg.
- **ADDED: 47 provinces** (previously country-only land):
  - **CHE ×25** — the Swiss cantons (`CHE_ZUERICH`, `CHE_BERN`, `CHE_TICINO`, …).
  - **ITA ×6** — northern compartimenti (`ITA_PIEMONTE`, `ITA_LOMBARDIA`,
    `ITA_VENETO`, `ITA_VENEZIA_TRIDENTINA`, `ITA_VENEZIA_GIULIA`, `ITA_EMILIA`).
  - **FRA ×16** — eastern départements framed by wceurope/east
    (`FRA_RHONE`, `FRA_ISERE`, `FRA_HAUT_RHIN`, …).
- **Wien** stays merged into `AUT_NIEDEROESTERREICH` (ratified, same ruling).
- Existing IDs (BEL/NLD/LUX/DEU/POL/CSK/AUT/SAA/DZG + the 11 Sprint-5 FRA)
  are **unchanged**.

Countries still without authored provinces (country-only, by design):
HUN, LTU, LVA, DNK, SWE, ROU, YUG, SOV.

## Infrastructure fields stay inert (AD-036 — unchanged for you)

`infrastructure.port`, `infrastructure.airfield`, and
`infrastructure.fortification` remain schema fields but are **NOT populated by
the pipeline** — starting infrastructure is authored construction-system
scenario data, filled when that system exists (like `resources`). They are
`false` / `false` / `"none"` on every hex; that is intentional, not a bug.
`settlement.anthrome = "fortified"` is a separate, descriptive tactical-map
signal (AD-015) and does NOT imply a fortification.

No action for Unity here — nothing changed from your side vs the Sprint 6
artifacts for these three fields.

## Artifact status

All four regenerated once, gates green, streaming ≡ monolithic where both run.
Delimited `{col}_{row}` IDs, signed elevation, `grid.col_min/row_min` (cols
don't start at 1) all as in Sprint 6. **Schema v1.0.5 UNCHANGED.**
`STREAMING_VERSION` s6.3 → s7.0. The ONLY hex-data change vs the Sprint 6
artifacts is the province layer (`province_at_start` + follow-on
`admin_tier`); terrain / country / rivers / resources / movement are
byte-identical.

## Canonical content hashes — CONFIRM YOUR INTEGRATED COPIES MATCH

Verify each integrated copy against the hash below before baking save
fixtures. Two hashes per file (recompute with `uv run python
tools/artifact_hash.py <file>`):

- **`content_sha256`** — the canonical hash: JSON with `map_metadata.generated_at`
  removed, keys sorted, compact UTF-8. Stable across re-exports of identical
  content. **This is the one to match** (Unity: parse → delete
  `map_metadata.generated_at` → serialize sorted-keys/no-whitespace → SHA256).
- **`raw_sha256`** — sha256 of the exact bytes I handed over (changes on any
  re-export because `generated_at` moves). Use it for a byte-exact check of
  *these* files.

| artifact | hex_count | bytes | content_sha256 | raw_sha256 |
|---|---|---|---|---|
| belgium_test | 775 | 1,167,685 | `ce0453fb0086559e016e64313afee666b3b72aceca2b3ce83fc258e842c40626` | `b0004cf7618783aaf9f6190a4a20fce11fc6ecfc473295ac278d863959a7df4f` |
| benelux_germany_test | 2,479 | 3,732,327 | `3de72caa9f516a398bed8fdb954c5eba545867b9133c466da3566db2cdf75672` | `efc90ca9435f11d605c439193c884120dad695143bae9e2f372733d68357d86e` |
| wceurope_test | 8,607 | 13,008,565 | `a14cd835afbf085663e8398a4536bce8debf01a1294c28796272ea1ca6707159` | `d958a3787f28f66401ae2593bd7f394d11a09a51c8fa4a2f37d7f15713a9762f` |
| east_expansion | 18,719 | 28,201,009 | `a76eaa0ba16ae9f65840143d3fdab9ac5a53cd8f2bf39fed7713335f0527c466` | `d8395633bb375550baf4e68a4fe799dfd2c69708226a7b8ea488eff19a4d923f` |

If a `content_sha256` mismatches, the integrated copy is stale or from a
different build — re-sync from the pipeline `output/` before proceeding. (These
are the specific files behind commit `53fa213`; `raw_sha256` reflects one
export of each. Ask Pipeline to re-emit if you need a fresh matching pair.)

_Details: `PARA_BELLUM_DECISIONS.md` AD-035 addendum + AD-036._
