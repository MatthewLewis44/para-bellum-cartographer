# `configs/` — the map specifications

Each YAML here is a bbox plus its settings. Pick by what you are doing, because the cost difference
between them is hours.

| Config | Hexes | Path | Cost |
|---|---|---|---|
| `para_bellum_belgium_test.yaml` | 775 | monolithic | ~75 s warm, fully cached. **Use this for iteration** |
| `para_bellum_benelux_germany_test.yaml` | 2,479 | either | ~30 min cold, ~3.3 min warm. Monolithic peak RAM ~30 GB, streaming 657 MB/tile. Gate: `validate_full_bbox.py` |
| `para_bellum_wceurope_test.yaml` | 8,607 / 130 tiles | **streaming only** | ~4 h cold (Overpass-bound), ~13 min warm re-tile |
| `para_bellum_east_expansion.yaml` | ~19k / ~205 tiles | **streaming only** | The shipped eastern artifact: Germany in full 1930 extent, Poland, Czechoslovakia, Austria (AD-035) |

Streaming-only means the monolithic path cannot run it at all:

```bash
uv run python run_streaming.py configs/para_bellum_east_expansion.yaml
```

## Before you run a big one

- **Warm the cache deliberately.** A cold run on a large bbox is bound by Overpass fetches, not by
  the pipeline. Check what is already cached before assuming a run is broken because it is slow.
- **Validate after**: `uv run python validate_full_bbox.py configs/<spec>.yaml`.
- **Compare paths when it matters**: `compare_hex_outputs.py` proves a streaming run matches the
  monolithic one hex for hex. That gate is what lets the streaming path be trusted.
- A bbox change renumbers columns and rows, because `col`/`row` start at their bbox-dependent
  minimum. Anything downstream that cached an id must be regenerated, not patched.
