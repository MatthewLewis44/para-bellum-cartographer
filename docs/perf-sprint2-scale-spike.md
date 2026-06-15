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
| Belgium | 775 | 15 | **528 MB** | **219 MB** | ~9.5 GB | warm ~2.5 min (cold ~7 min) |
| Benelux + W. Germany | 2,479 | TBD | TBD | TBD | 30.4 GB | TBD |
| W+C Europe (~5–15 E, 45–54 N) | ~30–50k | TBD | TBD | TBD | (infeasible) | TBD |

### Why the per-tile RAM is bbox-independent

The per-tile peak is set by the **densest tile** (Ruhr / Randstad), not the bbox
size — a bigger bbox adds *more* tiles, not *bigger* ones. The densest European
tiles are already inside the Benelux bbox, so Benelux's per-tile peak bounds the
per-tile peak for any larger European bbox. The **global** peak grows with hex
count (the merge holds all per-hex records), but records are small (~a few KB
each), so even 50k Europe hexes is hundreds of MB — well under the 6 GB budget.

### Runtime trade-off

Streaming is slower than warm monolithic (per-tile I/O: re-reading part slices,
windowed DEM reads, pickle round-trip) — acceptable per the sprint goal: RAM is
bounded, runtime is not the constraint. Tiles are cached/resumable, so a re-run
(or a resumed failure) only re-does incomplete tiles + the merge (Belgium re-ran
in **1.5 s** from cache).
