# Para Bellum Hex JSON Schema — v1.0.7

The contract between the cartography pipeline (`output/game_data_exporter.py`)
and the Unity 6 C# loader. **Bump `SCHEMA_VERSION` on any field
add/remove/rename** and record the change in the changelog below and in
`PARA_BELLUM_DECISIONS.md`.

> **⚠ A SCHEMA BUMP DOES NOT SHIP ON ITS OWN.** This paragraph used to say the
> loader "warns on mismatch (it does not hard-reject)" and that a versioning
> policy with teeth was planned. **That is stale — the policy landed and it has
> teeth.** `HexMap.ValidateSchemaVersion` pins
> `SupportedSchemaVersion = "1.0.5"` and **throws `InvalidDataException`** on
> any file whose version is NEWER, on a different major, or malformed. It does
> not warn and it does not degrade:
>
> *"schema_version 1.0.6 is NEWER than this loader supports (1.0.5). It may
> carry fields or semantics this loader would silently drop — update
> HexMap/HexData to the new schema (and the golden fixture) instead of loading
> blind. Refusing to load."*
>
> The failure is **asymmetric, and NEITHER DIRECTION IS "SAFE"** — one is loud
> and one is quiet, and the quiet one is worse:
>
> | | Behaviour |
> |---|---|
> | **NEW artifact → OLD loader** | **Refuses to load.** Throws immediately, and the message names the remedy. **The LOUD failure — this is the one you want.** |
> | **OLD artifact → NEW loader** | **Loads.** Does not crash. Every field the artifact lacks **takes its `HexData` default silently**, and the only trace is a `UnityEngine.Debug.Log` — the *lowest* severity available, not even `LogWarning`. **The QUIET failure.** |
>
> `HexData` uses `{ get; init; } = <default>` throughout, so a missing field is
> indistinguishable at runtime from a field the pipeline genuinely emitted at its
> default. **For the v1.0.6 bump the silently-defaulted field is
> `settlement.population`, and a defaulted `0` is byte-identical to a legitimate
> `0`** — which this schema explicitly allows and which occurs on roughly 5% of
> named settlement hexes. The map boots, looks right, and reports no population
> anywhere. Nothing in the console rises above `Debug`.
>
> **Consequences for anyone bumping the version here:**
>
> 1. **An artifact at a new schema version cannot be delivered by copying it into
>    `StreamingAssets`.** It is a migration in **one commit** on the Unity side —
>    raise `SupportedSchemaVersion`, extend `HexData`, update the golden fixture,
>    then copy. Owned by the seat that owns `Assets/Scripts/Map/*`, not by this
>    pipeline.
> 2. **If it must be staged, do the ARTIFACT FIRST.** A partial landing then
>    throws (new artifact, old loader) instead of defaulting in silence
>    (new loader, old artifact). Loader-first is the dangerous order.
> 3. **Verify by counting, not by looking.** A defaulted field cannot be seen. For
>    v1.0.6 the check is that **1,157 hexes carry `population > 0`**; zero
>    everywhere means the artifact never landed.
>
> **For the v1.0.7 bump the silently-defaulted field is `facilities`**, and a
> defaulted `[]` is indistinguishable from "this nation starts with no industry".
> An older artifact under a 1.0.7 loader boots with **no starting facilities
> anywhere and no error**. That outcome is correct for a genuinely pre-pass-B
> map and wrong for every other map, and nothing on screen tells the two apart.
> Count the facilities the start-state step created against the total in the
> v1.0.7 changelog entry below.

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
| `schema_version` | string | Semver. Currently `"1.0.7"`. |
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
| `data_sources` | object | Provenance strings per layer (`terrain`, `elevation`, `boundaries`, `provinces`, `resources`, and `facilities` since v1.0.7). Informational; never parse. |
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
  "facilities": [],
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
| `population` | int | **v1.0.6 (AD-038). ⚠ MODERN-DERIVED — NOT AUTHORED 1930 DATA.** Raw inhabitant count of the OSM settlement node that claimed this hex, and `0` on every hex no node claimed. **Population belongs to the node, not to the footprint:** a city's count sits on its own hex only, and the ring hexes of its sprawl carry `0` UNLESS a ring hex is itself a distinct named town with its own node (Lier inside Antwerpen's footprint, Herstal inside Liège's), in which case it carries its own count — those are real separate inhabitants, not a duplicate. Summing a province's hexes therefore counts every settlement exactly once. **`0` means "no node, or a node with no `population` tag", not "nobody lives here"** — roughly 5% of named settlement hexes are untagged, so a province sum UNDERCOUNTS and is a floor, never a census. The value is a **2020s OpenStreetMap `population` tag**, used as a stand-in for a 1930 figure. It is roughly serviceable for cities inside their 1930 states (Berlin, Budapest, Copenhagen) and **materially wrong** wherever the twentieth century moved the people: the German eastern territories, the Sudetenland, Memel, Danzig and the Bessarabian and Bukovinan strips all read their post-expulsion, post-resettlement populations. Treat every figure as provisional and on the historical-review list. It is still preferable to deriving population from the `population_class` ordinal, which throws away a real measurement to invent a fake one. |
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
| `industry_level` | int | **⚠ MODERN-DERIVED, same caveat as `settlement.population`.** Range is `{0, 1}` in practice, never higher: the sampler sets `1` when the dominant landuse polygon at the hex centre is **2020s OSM `landuse=industrial`**, else `0` (`hex/sampler.py`). It is not authored, not a 1930 measurement, and not a per-hex industry rating — it is "modern OSM calls this an industrial estate". The sim multiplies non-agricultural resource yield by `(1 + industry_level)`, so on the shipped eastern artifact it doubles the output of exactly 8 hexes (4 DEU, 2 CSK, 2 POL) out of the 72 that carry the flag — the other 64 hold no resource for it to multiply. On the historical-review list, and a candidate for retirement once Sprint 12 places real facilities. |

### `facilities`

**v1.0.7 (AD-043).** The authored **1930 starting industrial base**: the
facilities that exist on this hex at the first tick. This is an **array** and
is `[]` on almost every hex. It is hand-authored scenario data (from
`data/facilities/facilities_1930.csv`) and is never detected from OSM.

```json
"facilities": [
  {"kind": "power_plant", "tier": 3, "name": "Goldenberg-Werk (Knapsack)", "deposit": ""},
  {"kind": "steel_mill",  "tier": 3, "name": "Krupp Gussstahlfabrik (Essen)", "deposit": ""},
  {"kind": "mine",        "tier": 3, "name": "Erzberg (Eisenerz)", "deposit": "iron"}
]
```

| Field | Type | Notes |
|---|---|---|
| `kind` | enum string | `power_plant`, `steel_mill`, `mine`, `rail_yard`. Each maps **by name** onto the sim's `FacilityKind` (`PowerPlant`, `SteelMill`, `Mine`, `RailYard`). **No other value is emitted.** `civilian_factory`, `military_factory` and `refinery` are not authored, so the starting base has no factories. A new kind would be a schema change, so a loader **should throw on an unknown kind** rather than skip it. |
| `tier` | int | `1`–`3`: the facility's `Tier` at the first tick. It records relative scale, so Krupp Essen is 3 and a single-site works is 1. The ceiling is the sim's `MaxFacilityTier`, and the pipeline validator fails on anything above 3. |
| `name` | string | The historical works or station, in its 1930 local spelling. **Display only.** It is not an id, it is not unique across the map, and nothing should key on it. |
| `deposit` | string | On a `mine`: the pool it extracts. Always `"iron"` in this version, because the sim's `RequiredDepositFor` has one arm (Sprint 11 close report §7.3). On every other kind it is `""`. **A mine always sits on a hex whose `resources` flag for that deposit is `true`.** `"steel"` is never emitted. |

**Where it sits, and why this is per-hex.** A sim `Facility` is keyed on its
`Hex`. Its province and its owner are both *derived* from that hex, never
stored. A per-hex array is therefore the sim's own shape. A per-province list
would have made the start-state step invent a hex for each facility.
`ProvinceIndex.Build` does not need to change: the start-state step walks
`hexes` once, independently of it.

**Instantiation contract (what the start-state step can rely on).** Walk
`hexes` in export order, which is numeric `(col, row)`, and each hex's array
in order. That order is fixed: by kind as listed above, then by name. Create
one `Facility` per entry, with `Hex` = this hex's `coords`, `Kind` and `Tier`
from the entry, and `DamagePerMille = 0`. `PowerDraw` and `Staffing` come from
the sim's own per-kind tables, because the artifact carries neither. Because
the walk order is deterministic, the assigned `Facility.Id`s are deterministic
too.

**What the pipeline validator guarantees on the shipped map**
(`para_bellum_east_expansion`). The checks are in
`tools/build_facilities_1930.py --check`, `validate_full_bbox.py` runs them, and
`tests/test_facility_checks.py` proves each one fails on a seeded violation.
Checks 1–5 depend on which provinces the frame includes. A **test frame**
(Belgium, Benelux) crops provinces down to having no seat (AD-M19) and can cut a
plant off at the bbox edge, so there those checks only report. **A test-frame
artifact is not a valid start state.** Every other check fails the build on
every artifact. That covers entry shape and `(kind, name)` order, tier, the
deposit rules, and steel. It also reconciles every placed entry with the
authored table, which catches a stale layer and a facility landing in a
different nation from the one authored, and it requires that every authored
facility inside the bbox is placed exactly once.

1. The hex is land, and `country_at_start` and `province_at_start` are both
   non-empty.
2. The hex's `country_at_start` equals the province's **baseline owner**,
   computed by the `ProvinceIndex` rule. A facility therefore belongs at the
   first tick to the nation that owns its province.
3. The number of facilities in a province is at most that province's **slot
   total**, read from the settlement type of its seat (capture seat per
   `ProvinceIndex`: metropolis 7, city 5, town 3, otherwise 0). No facility
   sits in a zero-slot province, so the start-state step never has to
   over-fill a province or bypass `CanBuildFacility`'s slot rule.
4. **Every `steel_mill`'s province also holds a `power_plant`.**
5. **Every nation's authored power supply covers its authored draw** under
   the sim's current tables: a plant supplies `tier × tier`, which is base
   `Tier` times the Facility layer `1 + (tier − 1)`; a mill draws 2, and a mine
   or rail yard draws 1. At the first tick no nation is in brownout. These are
   the sim's unsigned `[P]` values mirrored in one place in the validator. If
   the sim changes them, the mirror must change too.
6. A `mine`'s hex carries `resources.<deposit> = true`.
7. No entry and no resource feature authors a steel deposit (AD-M03's
   transitional deposits retire; steel comes from mills).

**Not on this field:** ports, airfields and fortifications. Those remain the
inert `infrastructure` booleans of AD-036, because nothing in the sim consumes
them yet. The pass B "infrastructure network" means rail yards here, plus the
`road` and `rail` levels that were already sampled.

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

### v1.0.7 (2026-10-01, Sprint 12 pass B)

- **`facilities` (additive, AD-043).** A new per-hex array of authored
  starting facilities `{kind, tier, name, deposit}`. See the field section
  above. The exporter also adds `map_metadata.data_sources.facilities`.
  **Nothing else changed:** a field diff of Belgium regenerated at 1.0.7
  against 1.0.6 differs only in `facilities` (all 775 hexes, `[]` before the
  data landed) and in that metadata string.
- **An older artifact under a 1.0.7 loader** loads, and every hex defaults
  `facilities` to `[]`. The game then starts with **no industry anywhere**,
  silently. See the banner at the top.
- **A 1.0.7 artifact under an older loader** is refused, which is the loud
  failure. The loader bump, `HexData`, the golden fixture and the artifact copy
  land in **one** Unity commit.

### v1.0.6 (2026-08-26, Pass A)

- **`settlement.population` (additive, AD-038).** New int field carrying the raw
  inhabitant count of the settlement node on the hex. The pipeline has always
  read this integer — it is what decides `settlement.type` and the urban-sprawl
  radius (`geo/osm_downloader.py`, `hex/sampler.py`) — and then discarded it at
  export. Nothing else changed; every other field is bit-for-bit what v1.0.5
  produced.

  **Shape:** per-hex, because the export format has no province-level record to
  put it on. The document is `{schema_version, map_metadata, hexes}` and there
  is no `provinces` array, so a province-level population would have required a
  new top-level section and a second place for the loader to keep province
  state. The sim's contract (`ProvinceInfo.Population`) is satisfied by summing
  the hexes of a province inside `ProvinceIndex.Build`, which already walks
  every hex exactly once and already derives `ResourceHexes` the same way.
  Population attaches to the settlement NODE, so each settlement is counted
  exactly once and a sprawl footprint never multiplies its parent city — but a
  ring hex that is a distinct named town keeps its own count. The sum is a
  FLOOR: hexes whose node carries no `population` tag contribute 0.

  **`population` is modern-derived and must not be mistaken for authored 1930
  data** — see the field's row above for the full warning and the regions where
  it is materially wrong. `resources.industry_level`, which had the same
  provenance and no documentation at all, is now labelled too.

- **v1.0.5 consumers keep working.** The field is additive; a loader that does
  not know it ignores it. Unity writes `ProvinceInfo.Population` in Sprint 11.

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
