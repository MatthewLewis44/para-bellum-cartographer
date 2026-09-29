---
name: run-pipeline
description: Run the cartography pipeline and validate its output. Use when generating a hex map, running a config, choosing between the monolithic and streaming paths, or checking a regenerated artifact.
---

# Run the pipeline

## Every command goes through `uv`

```bash
uv run wargame-map generate configs/para_bellum_belgium_test.yaml     # monolithic
uv run python run_streaming.py configs/para_bellum_east_expansion.yaml # streaming
```

Never plain `python`. On Windows set `PYTHONIOENCODING=utf-8` first, or the run crashes at the end
on rich's spinner glyphs against the legacy cp1252 console.

## Pick the path

Belgium (775 hexes) is the iteration config and runs warm in about 75 seconds. Benelux + Germany
runs either way. Anything larger is **streaming only** — the monolithic path cannot hold it in RAM.
`configs/CLAUDE.md` has the table with real timings.

**Before a large run, read the `osm-cache` skill.** A run that lets a cache part expire destroys the
snapshot behind everything already shipped from it, silently.

## Validate what came out

```bash
uv run python validate_full_bbox.py configs/<spec>.yaml   # the gate
uv run python inspect_output.py                           # general inspection
uv run python check_settlements.py                        # and check_rivers / check_urban_sprawl / …
uv run python compare_hex_outputs.py <a> <b>              # streaming vs monolithic, or before vs after
```

A regeneration is not "unchanged" because it looks the same. **Diff it field by field, keyed on
coords, and state the churn as a number** — how many hexes changed, which fields, and whether any
type flipped. A change you cannot account for is a finding, not noise.

## When a run looks broken

- **Slow on a cold bbox** is usually Overpass, not the pipeline. Check what is cached first.
- **A field is empty**: check `src/wargame_cartographer/CLAUDE.md` under known quirks before
  reporting a bug. Ports, airfields and fortifications are **inert by ruling** (AD-036) and are
  authored scenario data, not pipeline output. Do not "fix" them with OSM detection.
- **Columns renumbered**: `col`/`row` start at the bbox-dependent minimum. A bbox change renumbers
  the grid, and anything that cached an id must be regenerated rather than patched.
