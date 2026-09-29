# `src/wargame_cartographer` — the pipeline

```
pipeline.py            — orchestrator: spec → data → grid → sample → render → export
cli.py                 — Click CLI (generate, quick, …)
streaming.py           — the tiled path (AD-024/025)
manifest.py            — cache snapshot manifest, its verifier (AD-040) and the
                         pre-run cache guard (AD-042)
memory.py              — working-set accounting, fail-loud over budget
config/map_spec.py     — Pydantic MapSpec + BoundingBox (YAML loader)
geo/
  downloader.py        — Natural Earth + ports (upstream; the ports Overpass query returns HTTP 406)
  osm_downloader.py    — Para Bellum OSM layers: landuse, settlements, roads, rail, waterways, bridges
  elevation.py         — SRTM download, hillshade, slope
  projection.py        — UTM CRS auto-selection from the bbox
  rivers_global.py     — AD-029 river selection: Natural Earth scalerank + OSM major canals
hex/
  grid.py              — HexGrid (flat-top, projected CRS) + OFFSET_NEIGHBOR_DELTAS, the single
                         adjacency convention (AD-034; coords.py was deleted in Sprint 6)
  sampler.py           — ★ ALL per-hex tagging happens here
terrain/               — Biome enum (24), ElevationTier, Vegetation, Moisture; BiomeClassifier
infrastructure/types.py— RoadLevel, RailLevel, SettlementType, Anthrome, Fortification + population bands
rendering/             — upstream debug renderer (biomes mapped to 8 legacy types, visual only)
output/
  game_data_exporter.py— ★ the Unity JSON contract; SCHEMA_VERSION lives here
  html_exporter.py     — Folium debug viewer
  static_exporter.py   — PNG/PDF
```

Validation scripts sit in the project root: `inspect_output.py`, `check_settlements.py`,
`check_rivers.py`, `check_urban_sprawl.py` and the rest.

## Sampling flow (`sampler.build_hex_terrain`)

1. Water detection (Natural Earth land polygons)
2. Elevation and slope (SRTM, 90th-percentile slope per hex)
3. Landuse (OSM polygons, point-in-polygon at the hex centre)
4. Settlement (precomputed settlement→hex assignment)
5. Biome classification, vegetation, moisture
6. Road and rail level (best class intersecting the WGS84 hex polygon)
7. Rivers (AD-026 node model): `has_river` + `river_name` from the AD-029 selected set;
   `river_edges` survives as a render-direction hint. Bridges, ports
8. Country and province at start (1930 boundaries, point-in-polygon)
9. Global reconcile passes: coastal flag, urban sprawl (AD-014), `admin_tier` (AD-023/027)

**Settlement assignment**: one pass, each node to its containing hex, most significant wins. Type
comes from population bands when population is known (OSM place tags are noisy), from the place tag
otherwise. At 10 km hexes, city and above always tag, towns only at ≥ 20k, villages never.

**Urban sprawl** (`_assign_urban_sprawl`, AD-014): contiguous BFS from each city or metropolis node
within a population-scaled radius (14 / 11 / 8 km), absorbing built-up hexes and open developable
land within 11 km, never forest, water or wetland. Nearest centroid wins an overlap. Ring hexes
become `suburb` with a `parent_city`; anthrome resolves **industrial landuse first at any distance**,
then metro under 3 km, then residential, then outskirts.

## Performance discipline

Target ~100,000 hexes. **Anything O(hexes × features) is a bug.** The settlement scan was rewritten
for exactly that reason: 280 × 5,243 distance calls became one O(settlements) pass.

## Provenance (AD-040)

Both paths call `manifest.write_manifest` after a successful export, writing
`<artifact-stem>_manifest.json` beside the artifact. What goes in it comes from `manifest.reset()`
at the start of a run plus `manifest.record_part(path, layer=..., role=...)` at each place a cached
input is consumed — `geo/osm_downloader.py` (`_fetch_layer`, `part_descriptors`),
`geo/elevation.py` and `geo/downloader.py`. **A new cached input needs a `record_part` call at the
point it is read**, or it silently leaves the receipt. `record_part` never raises.

Check one with `uv run python -m wargame_cartographer.manifest verify <artifact>`. The verifier
rehashes from disk and deliberately ignores the run-time hash memo; do not "optimise" that away.

**The guard (AD-042)** runs first, from `check_cache_before_run(spec)` at the top of both paths,
before anything is fetched or sampled. It compares the cache against the manifest committed at git
HEAD for that config and raises `CacheGuardError` on a part that changed, vanished, or is within a
day of the TTL in force for its root. Keep it before the first fetch; after it is worthless. The
CLI catches the exception and prints the message alone — the message is the guard, and a traceback
on top of it is how a guard gets deleted. `PARA_BELLUM_ALLOW_CACHE_REFETCH=1` downgrades a refusal
to a loud warning.

## Streaming

`streaming.run_streaming_pipeline(spec)` holds any bbox to under 4 GB per tile and 6 GB globally; the
monolithic Benelux run peaked at 30.4 GB. Output is **hex-for-hex identical** to the monolithic path
(gate: `compare_hex_outputs.py`), because both run the same pass code in `hex/sampler.py` — only the
data feeding pass 1 is tiled.

- **Global once**: grid, boundaries, resources, settlement→hex, river selection.
- **Per ~1° tile**: landuse/roads/rails/bridges from cached sub-bbox part gpkgs with a bbox filter
  plus a 0.2° margin (never merging a full layer); NE land and lakes clipped to tile plus margin;
  elevation as a **windowed read of the full DEM**, which is what makes the pixels identical.
- **Merge**: tiles in `grid.cells` order, then the global coastal and sprawl passes, then export.
- Tiles are cached and resumable, stamped with `STREAMING_VERSION`. RAM budgets fail loud.

The monolithic path stays for fast Belgium iteration. Why elevation is not cleanly tile-local is the
subtlety worth reading in `docs/streaming-pipeline-design.md`.

## Known quirks — check here before reporting a bug

- **Ports**: the upstream Overpass fetch returns HTTP 406, so `port` is false everywhere. Pre-existing.
  The port radius check had a metres-as-degrees bug, fixed in Sprint 6; ports use the corrected radius
  once the fetch is repaired.
- **Bridge ≈ river in western Europe**: at 10 km scale nearly every river hex carries `bridge: true`,
  so the field has almost no discriminating power there.
- **The Veluwe sand drifts (NL) classify as `desert`** via OSM `natural=sand`. Cosmetic.
- **Debug-renderer drift, accepted**: `grid.wargame_number` still labels PNG/NATO layers with packed
  `CCRR` ids, and `compute_hillshade` does not mask SRTM voids. Neither touches the JSON contract.
- **Deliberate deferral, do not re-flag**: `rivers.scalerank` and `rivers.waterway_type` are not
  exported; scalerank is retained per feature so they can be added additively with river-crossing
  gameplay (AD-029).
- **Resolved, do not re-litigate** (AD-034): the old `coords.offset_neighbors` / `grid.neighbors`
  disagreement was a grid-parity artifact. `hex/coords.py` is deleted and the neighbour gate prevents
  a recurrence.
