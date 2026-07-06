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

_Details: `PARA_BELLUM_DECISIONS.md` AD-035 addendum + AD-036._
