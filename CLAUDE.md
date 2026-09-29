# Para Bellum Cartography Pipeline

Python pipeline producing hex JSON for **Para Bellum**, a WW2 grand strategy game (Unity 6). Forked
from the upstream "wargame-cartographer" map renderer — the renderer (PNG/PDF/HTML) is kept for
debug and QA; **the JSON output is the product**.

**This repository is public.** Technical documentation belongs here. Planning, commercial and
process material does not, and never has.

Keep this file short: area-specific conventions live in the `CLAUDE.md` of the directory they
govern, which loads only when Claude works there.

| Working on | Read |
|---|---|
| The sampler, the passes, streaming, module layout | `src/wargame_cartographer/CLAUDE.md` |
| Which config to run and what each costs | `configs/CLAUDE.md` |
| Decision records (AD-NNN) | `PARA_BELLUM_DECISIONS.md` |
| The schema the Unity loader consumes | `docs/hex-schema.md` |
| Sprint-by-sprint history | `docs/pipeline-change-log.md` |
| Streaming design and as-built | `docs/streaming-pipeline-design.md` |

## Quick start

```bash
uv run wargame-map generate configs/para_bellum_belgium_test.yaml   # run the pipeline
uv run python inspect_output.py                                     # general inspection
uv run python check_settlements.py                                  # settlement validation
# check what an artifact was built from, part by part (AD-040)
uv run python -m wargame_cartographer.manifest verify output/para_bellum_belgium_test_hex_terrain.json
```

- **All Python execution uses `uv run`** — never plain `python`.
- **Windows console**: set `PYTHONIOENCODING=utf-8` first, or rich's spinner glyphs crash on the
  legacy cp1252 console at the end of a run.

## Provenance

Every export writes `output/<name>_hex_terrain_manifest.json` beside the artifact: the content
hash of every cache part the run consumed, the config and its hash, the resolved bbox, the schema
and streaming versions, and the artifact's own hashes (AD-040). **Manifests are committed;
artifacts are not.** Verify one with the `manifest verify` command above — it reports every part as
`match`, `changed` or `missing` and exits non-zero if anything moved.

It detects loss; it does not prevent or recover it. Read the "does NOT protect against" list in
AD-040 before treating a clean verify as a guarantee, and read the `osm-cache` skill before any run
that could expire a part.

## The contract with Unity

`output/<name>_hex_terrain.json` is a **versioned schema** consumed by the Unity 6 C# loader in a
separate repository. The version constant is `SCHEMA_VERSION` in `output/game_data_exporter.py`.

**Bump it on any field add, remove or rename, and coordinate with the Unity loader in the same
change.** The loader's guard is directional: an artifact **newer** than the loader is rejected
loudly, while an **older** one loads with silent defaults. That asymmetry is why a schema change
ships as one coordinated pair, artifact first when the artifact is already staged. The `schema-bump`
skill has the procedure.

Changes are **additive only**. The schema's history has no removals or renames after the fact.

## Hex grid convention

- **Flat-top** hexes, JSON `grid.offset = "odd_q"` (AD-012). Since Sprint 6 (AD-034) the grid
  normalises `q_min` to odd, so an **odd JSON `col` is shifted half a row north** on every artifact.
- **One neighbour implementation exists**: `grid.HexGrid.neighbors`, backed by
  `OFFSET_NEIGHBOR_DELTAS`. Import it; never re-derive the deltas. Gate:
  `uv run python tests/test_neighbor_consistency.py`.
- Offset coords `(col, row)`, 1-based; axial `(q, r)` internal only.
- **`hex_size_km: 10` is the flat-to-flat distance** (AD-013), so the circumradius is
  `10/√3 ≈ 5.7735 km` and a hex covers ≈ 86.6 km². Belgium bbox → 775 hexes, Benelux + DE → 2,479.
  Gate: `uv run python tests/test_hex_geometry.py`.
- Hex id is the delimited `"{col}_{row}"` form (v1.0.5, AD-031). **Display and debug only —
  consumers key on `coords`.** Export order is numeric by `(col, row)`.
- **`col`/`row` start at their bbox-dependent minimum**, not at 1. Unity keys off
  `grid.col_min` / `grid.row_min` and must never assume 1.

## Scale and discipline

The target is **~100,000 hexes** (full Europe). Anything O(hexes × features) is a bug: precompute a
feature-to-hex assignment or use a spatial index. Bboxes past Benelux scale run through the
**streaming** path, not the monolithic one.
