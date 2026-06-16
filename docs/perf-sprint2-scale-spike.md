# Pipeline Scale / Memory — Perf Notes

## Sprint 2 scale spike (baseline, monolithic)

Monolithic pipeline holds all layers in memory during sampling. Measured peak
working set:

| bbox | hexes | peak RAM | dominant offenders |
|---|---|---|---|
| Belgium (2.5–6.4 E, 49.5–51.5 N) | 775* | ~9.5 GB | landuse 0.8 M polys, SRTM raster |
| Benelux + W. Germany (2.5–8.8 E, 49.4–53.6 N) | 2,479* | **30.4 GB** | landuse 2.24 M polys, SRTM 343 M-cell raster |

\* post-AD-013 hex counts. Linear extrapolation to full Europe (~115k hexes,
~10 M km²) → **~360 GB**. Min-spec ship hardware is 16 GB → hard wall.

## Sprint 4 streaming refactor (tiled, AD-024/025)

`streaming.run_streaming_pipeline` tiles the per-hex work (~1° tiles, 0.2°
margin) and discards intermediate state between tiles; only small global tables
+ the densest single tile's slice are resident at once. Output is **hex-for-hex
identical** to monolithic (verified, modulo the additive `admin_tier` v1.0.3
field).

| bbox | hexes | tiles | peak RAM / tile | peak RAM global | monolithic peak | runtime |
|---|---|---|---|---|---|---|
| Belgium | 775 | 15 | **528 MB** | **219 MB** | ~9.5 GB | warm ~2.5 min |
| Benelux + W. Germany | 2,479 | 35 | **657 MB** | **451 MB** | **30.4 GB** | 6.7 min (warm cache) |
| W+C Europe (5–15 E, 45–54 N) | 8,607 | 130 | **742 MB** | **492 MB** | (infeasible) | 249 min (cold OSM fetch) |

All three runs are **well under the 4 GB/tile + 6 GB global budgets**. Belgium
and Benelux are hex-for-hex identical to monolithic; W+C Europe has no monolithic
baseline (it cannot run monolithic) and was validated by sanity check (8,607
hexes, coherent Alpine terrain, correct major cities — Frankfurt/Stuttgart/
Munich metropolis-DEU, Strasbourg city-FRA). Note: ~2,758 Europe hexes have no
country (Switzerland/Austria/N-Italy are outside the 5-country boundary stopgap,
AD-018) — a data limit, not a streaming bug.

### Why the per-tile RAM is bbox-independent

The per-tile peak is set by the **densest tile**, not the bbox size — a bigger
bbox adds *more* tiles, not *bigger* ones. Measured: Belgium 528 MB → Benelux
657 MB → **Europe 742 MB** (3.5× the area, only +13 % per-tile RAM; the slight
rise is denser Alpine landuse tiles, still far under 4 GB). The **global** peak
grows slowly with hex count (the merge holds all per-hex records): 219 MB (775
hexes) → 451 MB (2,479) → **492 MB (8,607)** — well under 6 GB.

### The real continental constraint is fetch time, not RAM

Europe's 249-min runtime is **almost entirely the cold OSM fetch** (125 sub-bbox
Overpass queries, 68 rate-limit retries handled by backoff, 0 failures) plus
first-time SRTM downloads — the process sat at **25 MB** throughout the global
fetch (the streamed waterway filter kept 134 major rivers / 10,636 ways without
materializing the unfiltered set). The memory wall is gone; Overpass *throughput*
is the next wall (Sprint 5+ cross-tile parallelism). Everything is checkpointed
(per-part gpkg cache + `STREAMING_VERSION` tile pickles), so a re-run finishes
from cache in minutes.

### Runtime trade-off

Streaming is slower than warm monolithic (per-tile I/O: re-reading part slices,
windowed DEM reads, pickle round-trip) — acceptable per the sprint goal: RAM is
bounded, runtime is not the constraint. Tiles are cached/resumable, so a re-run
(or a resumed failure) only re-does incomplete tiles + the merge (Belgium re-ran
in **1.5 s** from cache).
