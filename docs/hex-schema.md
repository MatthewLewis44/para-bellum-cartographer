# Para Bellum Hex JSON Schema — v1.0.1

The contract between the cartography pipeline (`output/game_data_exporter.py`)
and the Unity 6 C# loader. The loader checks `schema_version` on load and
rejects incompatible versions. **Bump `SCHEMA_VERSION` on any field
add/remove/rename** and record the change in the changelog below and in
`PARA_BELLUM_DECISIONS.md`.

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
| `schema_version` | string | Semver. Currently `"1.0.1"`. |
| `map_metadata` | object | See below. |
| `hexes` | array | One object per hex, sorted by `id` ascending (enables binary search in Unity). |

### `map_metadata`

| Field | Type | Notes |
|---|---|---|
| `name` | string | Spec name from YAML. |
| `title` | string | Display title. |
| `scenario_date` | string | ISO date of game start (`"1930-01-01"`). |
| `hex_size_km` | number | Center-to-vertex radius in km (10 = Para Bellum standard). |
| `hex_size_miles` | number | Derived, 2 decimals. |
| `generated_at` | string | ISO 8601 UTC timestamp. |
| `pipeline_version` | string | Pipeline build version. |
| `data_sources` | object | Provenance strings per layer (`terrain`, `elevation`, `boundaries`, `resources`). |
| `bounds` | object | `min_lon`, `min_lat`, `max_lon`, `max_lat` (WGS84). |
| `grid` | object | `orientation: "flat_top"`, `offset: "odd_row_east"`, `col_min/max`, `row_min/max`, `num_cols`, `num_rows`. |
| `hex_count` | int | Length of `hexes`. |
| `biome_distribution` | object | biome string → hex count. |

## Per-Hex Object

```json
{
  "id": "00501",
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
| `id` | string | `CCCRR`: 3-digit zero-padded column + 2-digit zero-padded row, both 1-based. `"00501"` = col 5, row 1. |
| `coords.col` | int | 1-based column. |
| `coords.row` | int | 1-based row. |

Grid is **flat-top**, odd columns shifted; cube coordinates are internal to the
pipeline and never stored in JSON.

### `geo`

| Field | Type | Notes |
|---|---|---|
| `center_lat` | float | WGS84, 6 decimals. |
| `center_lon` | float | WGS84, 6 decimals. |
| `elevation_m` | float | SRTM sample at hex center, 1 decimal. ≥ 0 for land. |
| `slope_deg` | float | 90th-percentile slope within hex, 2 decimals. |

### `terrain`

| Field | Type | Values |
|---|---|---|
| `biome` | enum string | 24 values: `plains`, `steppe`, `forest`, `jungle`, `rainforest`, `desert`, `badlands`, `savanna`, `hill`, `mountain`, `highland_plateau`, `glacier`, `tundra`, `taiga`, `marsh`, `swamp`, `mangrove`, `beach`, `atoll`, `volcanic_island`, `water`, `coastal_water`, `lake`, `urban`. v1 region only assigns the non-`[post-v1]` subset (see `terrain/types.py`). |
| `elevation_tier` | enum string | `flat`, `hilly`, `mountainous`, `rugged`, `highland_plateau`. Slope-driven: <3° flat (>1500 m → highland_plateau), <10° hilly, <20° mountainous, ≥20° rugged. |
| `vegetation` | enum string | `bare`, `sparse`, `light`, `dense`. |
| `moisture` | enum string | `arid`, `dry`, `temperate`, `wet`, `flooded`. |
| `is_coastal` | bool | Land hex with ≥1 water-hex neighbor. |
| `river_edges` | int array | Edge indices 0–5 crossed by a river/canal. Edge 0 = NE, clockwise. Empty = no river. |

### `political`

| Field | Type | Notes |
|---|---|---|
| `country_at_start` | string | ISO3 country code (`"BEL"`, `"DEU"`, ...) as of game start (1930). Empty string = water / no country. |
| `province_at_start` | string | Province/region id as of game start. **Sprint 3 — currently always empty.** |

### `settlement`

| Field | Type | Values / Notes |
|---|---|---|
| `type` | enum string | `none`, `village` (<2k pop), `town` (2k–50k), `city` (50k–300k), `metropolis` (>300k). Type resolves from OSM population when known, from OSM place tag otherwise. At the 10 km hex scale only `town`+ (pop ≥ 20k) is tagged; villages stay `none`. |
| `name` | string | Settlement name (UTF-8, native spelling). Empty when `type` = `none`. |
| `population_class` | int | 0–5: none 0, village 1, town 2, city 3, metropolis 5 (4 reserved). |
| `anthrome` | enum string | `none`, `residential`, `industrial`, `metro`, `cropland`, `paddy`, `mining`, `mangrove`, `fortified`. Drives Unity tactical map pool selection. |

### `infrastructure`

| Field | Type | Values |
|---|---|---|
| `road` | enum string | `none`, `dirt`, `paved`, `highway`. |
| `rail` | enum string | `none`, `narrow`, `standard`, `double`. |
| `bridge` | bool | Bridge present on a river hex. |
| `port` | bool | Port facility in hex. |
| `airfield` | bool | Sprint 2 manual layer — currently always `false`. |
| `fortification` | enum string | `none`, `field`, `permanent`. Sprint 2 manual layer — currently always `none`. |

### `resources`

| Field | Type | Notes |
|---|---|---|
| `oil` / `coal` / `steel` | bool | Sprint 2 manual overlay — currently always `false`. |
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
