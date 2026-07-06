# Para Bellum Hex JSON Schema — v1.0.5

The contract between the cartography pipeline (`output/game_data_exporter.py`)
and the Unity 6 C# loader. The loader checks `schema_version` on load and
warns on mismatch (it does not hard-reject — see `unity-hex-loader.md`; a
versioning policy with teeth is planned before save/load). **Bump
`SCHEMA_VERSION` on any field add/remove/rename** and record the change in
the changelog below and in `PARA_BELLUM_DECISIONS.md`.

## Top-Level Document

```json
{
  "schema_version": "1.0.1",
  "map_metadata": { ... },
  "hexes": [ { ... }, ... ]
}
```

| Field | Type | Notes |
|---|---|---|
| `schema_version` | string | Semver. Currently `"1.0.5"`. |
| `map_metadata` | object | See below. |
| `hexes` | array | One object per hex, sorted **numerically by `(coords.col, coords.row)`** (v1.0.5, AD-031 — the pre-1.0.5 "sorted by `id` string" ordering broke once packed id widths mixed). |

### `map_metadata`

| Field | Type | Notes |
|---|---|---|
| `name` | string | Spec name from YAML. |
| `title` | string | Display title. |
| `scenario_date` | string | ISO date of game start (`"1930-01-01"`). |
| `hex_size_km` | number | **Flat-to-flat** distance in km (edge-to-edge, = 2 × apothem). `10` = Para Bellum standard ⇒ circumradius ≈ 5.7735 km, area ≈ 86.6 km². Corrected in AD-013 (was misread as circumradius pre-Sprint 3). |
| `hex_size_miles` | number | Derived from `hex_size_km`, 2 decimals. |
| `generated_at` | string | ISO 8601 UTC timestamp. |
| `pipeline_version` | string | Pipeline build version. |
| `data_sources` | object | Provenance strings per layer (`terrain`, `elevation`, `boundaries`, `resources`). |
| `bounds` | object | `min_lon`, `min_lat`, `max_lon`, `max_lat` (WGS84). |
| `grid` | object | `orientation: "flat_top"`, `offset: "odd_q"` (flat-top odd-q offset, AD-012), `col_min/max`, `row_min/max`, `num_cols`, `num_rows`. **The `odd_q` layout is exact and guaranteed since v1.0.5 (AD-034):** rows run south → north, and **odd `col` columns are shifted +half a row north**. (Pre-1.0.5 artifacts did not guarantee which parity was shifted — it varied per bbox; Belgium/wceurope shipped odd-shifted, Benelux even-shifted. The grid now normalizes parity, which renumbered Benelux cols +1.) Neighbor deltas, keyed by `col % 2`: even → `(+1,0)(+1,−1)(0,−1)(−1,−1)(−1,0)(0,+1)`; odd → `(+1,+1)(+1,0)(0,−1)(−1,0)(−1,+1)(0,+1)`. |
| `hex_count` | int | Length of `hexes`. |
| `biome_distribution` | object | biome string → hex count. |

## Per-Hex Object

```json
{
  "id": "5_1",
  "coords": {"col": 5, "row": 1},
  "geo": {
    "center_lat": 51.2345, "center_lon": 4.8901,
    "elevation_m": 18.0, "slope_deg": 1.2
  },
  "terrain": {
    "biome": "plains",
    "elevation_tier": "flat",
    "vegetation": "light",
    "moisture": "temperate",
    "is_coastal": false,
    "river_edges": []
  },
  "rivers": {
    "has_river": false,
    "river_name": ""
  },
  "political": {
    "country_at_start": "BEL",
    "province_at_start": ""
  },
  "settlement": {
    "type": "city", "name": "Bruxelles - Brussel",
    "population_class": 3, "anthrome": "metro"
  },
  "infrastructure": {
    "road": "paved", "rail": "standard",
    "bridge": false, "port": false,
    "airfield": false, "fortification": "none"
  },
  "resources": {
    "oil": false, "coal": false, "steel": false,
    "agriculture": true, "industry_level": 0
  },
  "movement": {"base_cost": 1, "base_defense": 0},
  "flags": {"is_water": false, "is_impassable": false, "is_coastal": false}
}
```

### `id` and `coords`

| Field | Type | Notes |
|---|---|---|
| `id` | string | **v1.0.5 (AD-031): delimited `{col}_{row}`** (e.g. `"17_103"` = col 17, row 103). Unambiguous at any grid size — the pre-1.0.5 packed `CCCRR` format overflowed in shipped wceurope data (rows ≥ 100 → mixed 5/6-char ids). **Display/debug only** — consumers MUST key on `coords`, never parse `id` positionally. |
| `coords.col` | int | 1-based column. |
| `coords.row` | int | 1-based row (south → north). |

Grid is **flat-top**; **odd columns are shifted +half a row north**
(guaranteed since v1.0.5, AD-034 — see `map_metadata.grid` above for the
exact neighbor deltas). Cube coordinates are internal to the pipeline and
never stored in JSON.

### `geo`

| Field | Type | Notes |
|---|---|---|
| `center_lat` | float | WGS84, 6 decimals. |
| `center_lon` | float | WGS84, 6 decimals. |
| `elevation_m` | float | SRTM sample at hex center, 1 decimal. **Signed since v1.0.5 (AD-032)**: below-sea-level land exports its true negative elevation (Dutch polders to ≈ −8 m; the Hambach open-pit reads −83 m). Pre-1.0.5 clamped land to ≥ 0. Water hexes were always signed. |
| `slope_deg` | float | 90th-percentile slope within the hex, 2 decimals, measured at ~90 m terrain scale with metric per-axis spacing (AD-033 — pre-1.0.5 values were 3–4.5× too low; all values changed in the Sprint 6 regeneration). |

### `terrain`

| Field | Type | Values |
|---|---|---|
| `biome` | enum string | 24 values: `plains`, `steppe`, `forest`, `jungle`, `rainforest`, `desert`, `badlands`, `savanna`, `hill`, `mountain`, `highland_plateau`, `glacier`, `tundra`, `taiga`, `marsh`, `swamp`, `mangrove`, `beach`, `atoll`, `volcanic_island`, `water`, `coastal_water`, `lake`, `urban`. v1 region only assigns the non-`[post-v1]` subset (see `terrain/types.py`). |
| `elevation_tier` | enum string | `flat`, `hilly`, `mountainous`, `rugged`, `highland_plateau`. Slope-driven: <3° flat (>1500 m → highland_plateau), <10° hilly, <20° mountainous, ≥20° rugged. |
| `vegetation` | enum string | `bare`, `sparse`, `light`, `dense`. |
| `moisture` | enum string | `arid`, `dry`, `temperate`, `wet`, `flooded`. |
| `is_coastal` | bool | Land hex with ≥1 water-hex neighbor. |
| `river_edges` | int array | Edge indices 0–5 crossed by a river/canal. Edge `i` runs between hex vertex `i` and `i+1` (vertices at 60·`i`° from East), so the enumeration is **counterclockwise**: 0=NE, 1=N, 2=NW, 3=SW, 4=S, 5=SE. Empty = no river. **v1.0.4 (AD-026): rendering direction hint only** — which neighbours to draw the river spline toward. Its gameplay role is superseded by `rivers.has_river`. |

### `rivers`

**v1.0.4 (AD-026).** Rivers are modelled as hex-*center* features (the hex a
river polyline passes through), not hex-edge boundaries. Crossing a river means
attacking *into* a river hex; the opposed crossing is folded into that hex's
battle. River SELECTION is **Natural Earth `scalerank`** (AD-029): natural rivers
with `scalerank <= river_scalerank_max` (config, default 8) plus OSM major canals
(the Albert Canal etc. — Natural Earth carries no canals). Source change only;
the node model is unchanged.

| Field | Type | Notes |
|---|---|---|
| `has_river` | bool | `true` if a selected river/canal passes through this hex (its geometry intersects the hex polygon). Default `false`. River-hexes form continuous chains by construction (a polyline through consecutive hexes shares their edges). |
| `river_name` | string | Display name of the primary river in the hex — for natural rivers the Natural Earth `name` field; for canals the OSM name. When several cross a hex, the one with the longest run *inside* the hex wins (a trunk beats a clipping tributary). Empty `""` when `has_river` is `false`. |

No major/minor/navigable class distinction in v1 (single boolean per AD-026);
Natural Earth `scalerank` is retained per-feature so a river-class split can be
added later without rework (AD-029).

### `political`

| Field | Type | Notes |
|---|---|---|
| `country_at_start` | string | ISO3 country code (`"BEL"`, `"DEU"`, ...) as of game start (1930). Empty string = water / no country. |
| `province_at_start` | string | Province id as of game start (e.g. `"BEL_LIEGE"`, `"DEU_RHEINLAND"`). **Populated in Sprint 5** from the 1930 province layer (AD-023/AD-027). Empty for water, no-country, or land outside the authored 5-country coverage (CH/AT/IT have country but no province yet). |

### `settlement`

| Field | Type | Values / Notes |
|---|---|---|
| `type` | enum string | `none`, `village` (<2k pop), `town` (2k–50k), `city` (50k–300k), `metropolis` (>300k), `suburb` (v1.0.2 — ring hex of a multi-hex city, AD-014). Type resolves from OSM population when known, from OSM place tag otherwise. At the 10 km hex scale only `town`+ (pop ≥ 20k) is tagged; villages stay `none`. |
| `name` | string | Settlement name (UTF-8, native spelling). Empty when `type` = `none`. For a `suburb` hex, its own name if it had one, else empty (the city is in `parent_city`). |
| `population_class` | int | 0–5: none 0, village 1, town 2, city 3, metropolis 5 (4 reserved). Suburb ring hexes: 3 (inner, <6 km) or 2 (outer). |
| `anthrome` | enum string | `none`, `residential`, `industrial`, `metro`, `outskirts` (v1.0.2), `cropland`, `paddy`, `mining`, `mangrove`, `fortified`. Drives Unity tactical map pool selection. Within a city footprint (AD-014): `metro` <3 km from centroid, else `industrial`/`residential` by dominant landuse, else `outskirts`. |
| `parent_city` | string | **v1.0.2 (AD-014).** Name of the city this hex belongs to, for hexes inside a multi-hex urban footprint (centroid + suburb ring). Empty `""` otherwise. |
| `distance_from_centroid_km` | float \| null | **v1.0.2 (AD-014).** Distance from this hex's center to the parent city's centroid hex (0.0 at the centroid). `null` for hexes not in any city footprint. |
| `admin_tier` | enum string | **v1.0.3 field; `capital`/`sub_capital` assigned in Sprint 5 (AD-023/AD-027).** `capital` (province capital, ≤1 per province), `sub_capital` (designated regional centre), `urban` (other settled in-province hex), `rural` (unsettled in-province land), `none` (water / no-country / outside province coverage). Capital + sub-capital hexes are matched from the province metadata to OSM settlement nodes; where no province layer is loaded, falls back to the population-derived default (settled→`urban`, land→`rural`). |

### `infrastructure`

| Field | Type | Values |
|---|---|---|
| `road` | enum string | `none`, `dirt`, `paved`, `highway`. |
| `rail` | enum string | `none`, `narrow`, `standard`, `double`. |
| `bridge` | bool | Bridge present on a river hex. |
| `port` | bool | **AD-036: authored, NOT pipeline-detected.** Starting infrastructure (port facilities) is construction-system scenario data, filled from an authored layer when that system exists — like `resources`. The pipeline emits `false` for every hex; this empty value is intentional, not a bug. Do not "fix" it with OSM detection. |
| `airfield` | bool | **AD-036: authored, NOT pipeline-detected** (same as `port`). Always `false` from the pipeline; filled from authored scenario data later. |
| `fortification` | enum string | `none`, `field`, `permanent`. **AD-036: authored, NOT pipeline-detected.** Always `none` from the pipeline. Independent of `settlement.anthrome = "fortified"`, which is descriptive military-land *character* for tactical-map selection (AD-015), not a strategic-works flag. |

### `resources`

| Field | Type | Notes |
|---|---|---|
| `coal` / `steel` / `iron` / `oil` | bool | From the hand-authored `data/resources/resources_1930.geojson` layer (F-2): basins (polygons) tag hexes by center-in-polygon, works (points) tag the containing hex. `iron` **new in v1.0.2**. `oil` currently has no in-bbox 1930 source (always `false` here). |
| `agriculture` | bool | True when hex landuse is farmland. |
| `industry_level` | int | 0–N. Currently 1 when OSM industrial landuse present, else 0. |

### `movement`

| Field | Type | Notes |
|---|---|---|
| `base_cost` | int | Base movement points from biome (99 = impassable). Unity applies the full modifier stack at runtime. |
| `base_defense` | int | Base defense modifier from biome. |

### `flags`

| Field | Type | Notes |
|---|---|---|
| `is_water` | bool | Biome ∈ {water, coastal_water, lake}. |
| `is_impassable` | bool | Biome ∈ {water, coastal_water, lake, glacier}. |
| `is_coastal` | bool | Duplicate of `terrain.is_coastal` for fast Unity filtering. |

## Changelog

### v1.0.5 (2026-07-02, Sprint 6)

- **`id` format (AD-031, breaking for anything that parsed ids):** packed
  `CCCRR` → delimited **`{col}_{row}`** (`"17_103"`). The packed format had
  already overflowed in shipped wceurope v1.0.4 data (rows ≥ 100 → ambiguous
  mixed-width ids; string sort no longer matched (col,row)). `id` is
  display/debug only; Unity keys on `coords` and is unaffected functionally.
- **`hexes` ordering:** sorted numerically by `(coords.col, coords.row)`
  (was: by `id` string).
- **`geo.elevation_m` signed (AD-032):** below-sea-level land no longer
  clamps to 0. Impact: 5 Belgium / 163 Benelux land hexes go negative
  (Dutch/Belgian polders −1..−8 m; Hambach open-pit −83 m). Biome/tier
  classification of these hexes is unchanged (all thresholds are upper
  bounds; verified in the Sprint 6 polder report).
- **Grid parity guarantee (AD-034, metadata semantics):** `grid.offset =
  "odd_q"` is now exact — odd cols shifted north, all artifacts. **Benelux
  cols renumbered +1** (Belgium/wceurope unchanged). Unity's `HexCoord`
  decode can assert the convention instead of assuming it.
- Ships together with the AD-033 slope correction (values-only change to
  `slope_deg`, `elevation_tier`, and slope-driven biomes — see AD-033 delta).
- **Unity migration:** accept `schema_version` 1.0.5; do not parse `id`
  positionally (`ToHexId` display format should switch to `{col}_{row}`);
  re-import the regenerated artifacts.

### v1.0.4 (2026-06-17, Sprint 5) — additive only

- **`rivers`** block added with **`rivers.has_river`** (bool, default `false`)
  and **`rivers.river_name`** (string, default `""`) — the hex-center river node
  model (AD-026). Populated from the selected-river set in the sampler's per-hex
  pass — the same whole-bbox set already feeds `river_edges`, so the new fields
  are seam-identical under the streaming pipeline and automatically consistent
  with `river_edges`. (The selection source later moved from the OSM AD-011
  filter to Natural Earth `scalerank` per AD-029; the schema is unchanged.)
- **`terrain.river_edges`** is **retained** but redocumented as a *rendering
  direction hint only* — its gameplay role is superseded by `rivers.has_river`
  (AD-026). No value or position change; v1.0.3 consumers keep working.
- Purely additive — a v1.0.3 consumer that ignores the `rivers` block still
  loads. **Unity should add a `Rivers` block (has_river, river_name) to
  HexData.cs** and migrate river gameplay from edge-based to hex-based.

### v1.0.3 (2026-06-14, Sprint 4) — additive only

- **`settlement.admin_tier`** (enum string) added: `capital` / `sub_capital` /
  `urban` / `rural` / `none`. `capital`/`sub_capital` reserved for a future
  political layer; pipeline currently defaults water/no-country → `none`,
  settled → `urban`, unsettled land → `rural`. Derived at export time from
  existing fields, so it is purely additive — v1.0.2 consumers ignore it.
- No other field changes. (Sprint 4 is otherwise a non-schema streaming
  refactor; output is hex-equivalent to v1.0.2 modulo this field.)

### v1.0.2 (2026-06-13, Sprint 3) — additive only

- **`settlement.parent_city`** (string) and **`settlement.distance_from_centroid_km`**
  (float|null) added for multi-hex urban sprawl (AD-014).
- **`settlement.type`** gains `suburb`; **`settlement.anthrome`** gains
  `outskirts`. Existing values unchanged.
- **`resources.iron`** (bool) added; resources now populated from the
  hand-authored 1930 layer (F-2). Existing resource booleans unchanged.
- Multi-hex urban footprints: each city/metropolis grows a contiguous,
  population-scaled, urban-landuse-gated footprint; ring hexes become
  `suburb` carrying `parent_city`.
- Non-schema (metadata) corrections shipped alongside: `grid.offset` →
  `"odd_q"` (was `"odd_row_east"`), `scenario_date` confirmed `"1930-01-01"`,
  and `hex_size_km` is documented as flat-to-flat (AD-013).
- No renames or removals — v1.0.1 consumers keep working (new fields ignored).

### v1.0.1 (2026-06-11, Sprint 2)

- **Renamed** `political.country_1939` → `political.country_at_start` and
  `political.province` → `political.province_at_start`. Year-suffixed names
  presumed a 1939 start; the game starts in **1930**. See AD-007.
- `country_at_start` is now populated from 1930 historical boundaries
  (aourednik/historical-basemaps `world_1930.geojson`), ISO3 codes.
- **Breaking for Unity**: the C# loader's `[JsonProperty("country_1939")]`
  must be updated to `country_at_start` (and `province` →
  `province_at_start`).

### v1.0.0 (Sprint 1)

- Initial Para Bellum schema: biome, elevation tier, vegetation, moisture,
  settlement, infrastructure, resources, movement, flags.
