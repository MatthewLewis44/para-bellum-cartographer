"""Parameterized map-output validation gate (Sprint 6).

Supersedes the Benelux-hardwired Sprint 2 version: expectations are keyed by
the spec ``name`` in EXPECTATIONS below, so every bbox artifact — Belgium,
Benelux, wceurope, the eastern expansion — gets first-class validation from
the same structural gates.

Usage:
    uv run python validate_full_bbox.py [configs/<spec>.yaml] [output.json]

Defaults to the Benelux config (back-compat with the Sprint 2 CLI). The
output path defaults to ``output/<name>_hex_terrain.json``.

Gate design:
- STRUCTURAL gates (schema/id format, river connectivity via the package's
  OFFSET_NEIGHBOR_DELTAS — the single adjacency convention, no parity
  guessing — province/sprawl invariants, elevation plausibility) run
  identically for every config.
- BANDED gates (hex count, country mix, biome shares, river %) are
  per-config tripwires: measured value at the last deliberate output change
  (Sprint 6 P0-A fix bundle, AD-033) ± a regression margin. Recalibrate
  bands ONLY alongside an AD that deliberately changes terrain output.

Exit code 0 = all checks pass, 1 = at least one failure.
"""

import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from wargame_cartographer.config.map_spec import MapSpec           # noqa: E402
from wargame_cartographer.hex.grid import OFFSET_NEIGHBOR_DELTAS   # noqa: E402

EXPECTED_SCHEMA = "1.0.5"

# ---------------------------------------------------------------------------
# Per-config expectations. Bands = measured at Sprint 6 fix bundle ± margin.
# ---------------------------------------------------------------------------
EXPECTATIONS = {
    "para_bellum_belgium_test": dict(
        hex_count=(700, 850),
        countries={"BEL": (310, 400), "NLD": (50, 90), "FRA": (200, 280),
                   "LUX": (20, 40), "DEU": (40, 70)},
        water_min=25,
        cities=[
            ("Brussels", 50.85, 4.35, ("Bruxelles", "Brussel"), "BEL"),
            ("Antwerp", 51.22, 4.40, ("Antwerpen",), "BEL"),
            ("Gent", 51.05, 3.72, ("Gent",), "BEL"),
            ("Liège", 50.63, 5.57, ("Liège",), "BEL"),
            ("Charleroi", 50.41, 4.44, ("Charleroi",), "BEL"),
        ],
        river_points=[
            ("Meuse at Liège", 50.64, 5.57),
            ("Meuse at Namur", 50.46, 4.87),
            ("Scheldt at Antwerp", 51.22, 4.40),
            ("Albertkanaal/Hasselt", 50.93, 5.34),
        ],
        river_hex_band=(70, 120),
        cities_min=25,
        sprawl=[("Brussels", 3, ("Brux",)), ("Antwerp", 2, ("Antwerp",))],
        ruhr_min=None,
        resources=dict(coal_min=5, steel_min=2, iron_min=0),
        resource_points=[("Liège steel", 50.61, 5.54, ("steel",))],
        provinces_min=12,
        province_points=[
            ("Liège -> BEL_LIEGE", 50.63, 5.57, "BEL_LIEGE"),
            ("Gent -> BEL_EAST_FLANDERS", 51.05, 3.72, "BEL_EAST_FLANDERS"),
        ],
        land_slope_p90_band=(8, 16),
        biome_bands={"hill": ("pct", 14, 26), "mountain": ("count", 0, 8),
                     "urban": ("pct", 10, 21), "forest": ("pct", 4, 12)},
    ),
    "para_bellum_benelux_germany_test": dict(
        hex_count=(2350, 2650),
        countries={"BEL": (300, 410), "NLD": (340, 460), "LUX": (20, 42),
                   "DEU": (820, 990), "FRA": (230, 330),
                   # AD-035: the Saar Basin's northern edge frames into this
                   # bbox (min_lat 49.4 cuts the territory at ~half height).
                   "SAA": (3, 25)},
        water_min=400,
        country_points=[
            ("Merzig (Saar Territory)", 49.44, 6.63, "SAA"),
            ("Sankt Wendel (Saar Territory)", 49.47, 7.17, "SAA"),
            ("Trier (Rhineland, not Saar)", 49.76, 6.64, "DEU"),
        ],
        cities=[
            ("Brussels", 50.85, 4.35, ("Bruxelles", "Brussel"), "BEL"),
            ("Antwerp", 51.22, 4.40, ("Antwerpen",), "BEL"),
            ("Amsterdam", 52.37, 4.90, ("Amsterdam",), "NLD"),
            ("Rotterdam", 51.92, 4.48, ("Rotterdam",), "NLD"),
            ("Luxembourg City", 49.61, 6.13, ("Luxembourg",), "LUX"),
            ("Cologne", 50.94, 6.96, ("Köln", "Cologne"), "DEU"),
            ("Düsseldorf", 51.23, 6.78, ("Düsseldorf",), "DEU"),
            ("Frankfurt", 50.11, 8.68, ("Frankfurt",), "DEU"),
        ],
        river_points=[
            ("Meuse at Liège", 50.64, 5.57),
            ("Meuse at Venlo", 51.37, 6.17),
            ("Rhine at Cologne", 50.94, 6.96),
            ("Rhine at Bonn", 50.73, 7.10),
            ("Scheldt at Antwerp", 51.22, 4.40),
            ("Albertkanaal/Hasselt", 50.93, 5.34),
        ],
        river_hex_band=(250, 380),
        cities_min=25,
        sprawl=[("Brussels", 3, ("Brux",)), ("Antwerp", 2, ("Antwerp",)),
                ("Amsterdam", 3, ("Amsterdam",)), ("Cologne", 3, ("Köln",))],
        ruhr_min=10,
        resources=dict(coal_min=10, steel_min=3, iron_min=1),
        resource_points=[("Essen coal+steel", 51.45, 7.01, ("coal", "steel")),
                         ("Liège steel", 50.61, 5.54, ("steel",))],
        provinces_min=30,
        province_points=[
            ("Köln -> DEU_RHEINLAND", 50.94, 6.96, "DEU_RHEINLAND"),
            ("Münster -> DEU_WESTFALEN", 51.96, 7.63, "DEU_WESTFALEN"),
            ("Maastricht -> NLD_LIMBURG", 50.85, 5.69, "NLD_LIMBURG"),
            ("Liège -> BEL_LIEGE", 50.63, 5.57, "BEL_LIEGE"),
        ],
        land_slope_p90_band=(10, 19),
        biome_bands={"hill": ("pct", 15, 27), "mountain": ("count", 10, 60),
                     "urban": ("pct", 15, 28), "forest": ("pct", 4, 12)},
    ),
    "para_bellum_east_expansion": dict(
        # Bands calibrated to the first Sprint 6 artifact (18,719 hexes,
        # 2026-07-04); initial geometry-derived guesses corrected for DNK
        # (bbox cuts Denmark at 56°N) and the Alpine/Carpathian slope share.
        # The structural gates + spot checks are the real 1930 assertions.
        hex_count=(16500, 20500),
        countries={"DEU": (4600, 6000), "POL": (3900, 5000),
                   "CSK": (1350, 1850), "AUT": (800, 1150),
                   "HUN": (600, 1200), "CHE": (350, 600),
                   "DZG": (10, 40), "SAA": (10, 40),
                   "LTU": (400, 750), "DNK": (200, 400),
                   "ROU": (800, 1900), "FRA": (300, 900),
                   "NLD": (100, 380), "SWE": (50, 320)},
        water_min=800,
        country_points=[
            ("Katowice (Polish E. Upper Silesia)", 50.26, 19.02, "POL"),
            ("Beuthen (German Upper Silesia)", 50.35, 18.92, "DEU"),
            ("Danzig Free City", 54.35, 18.65, "DZG"),
            ("Gdynia (Polish Corridor port)", 54.52, 18.53, "POL"),
            ("Toruń (Corridor)", 53.01, 18.60, "POL"),
            ("Schneidemühl (Grenzmark, German)", 53.15, 16.74, "DEU"),
            ("Königsberg (East Prussia)", 54.71, 20.51, "DEU"),
            ("Memel (Lithuanian since 1923)", 55.71, 21.13, "LTU"),
            ("Wilno (Polish since 1922)", 54.69, 25.28, "POL"),
            ("Lwów (Polish Galicia)", 49.84, 24.03, "POL"),
            ("Cheb/Eger (Sudetenland, CSK)", 50.08, 12.37, "CSK"),
            ("Užhorod (Ruthenia, CSK)", 48.62, 22.30, "CSK"),
            ("Wien (pre-Anschluss Austria)", 48.21, 16.37, "AUT"),
            ("Sopron (Hungarian, 1921 plebiscite)", 47.68, 16.59, "HUN"),
            ("Saarbrücken (League territory)", 49.23, 7.00, "SAA"),
            # (Sønderborg's nearest hex center is open water in the
            #  Flensburg Fjord — Haderslev is the inland plebiscite check.)
            ("Haderslev (Danish, 1920 plebiscite)", 55.25, 9.49, "DNK"),
            ("Cernăuți (Romanian Bukovina)", 48.29, 25.94, "ROU"),
        ],
        cities=[
            ("Berlin", 52.52, 13.40, ("Berlin",), "DEU"),
            ("Warszawa", 52.23, 21.01, ("Warszawa",), "POL"),
            ("Praha", 50.09, 14.42, ("Praha",), "CSK"),
            ("Wien", 48.21, 16.37, ("Wien",), "AUT"),
            ("München", 48.14, 11.58, ("München",), "DEU"),
            ("Breslau", 51.11, 17.03, ("Wrocław",), "DEU"),
            ("Königsberg", 54.71, 20.51, ("Калининград", "Kaliningrad"), "DEU"),
            ("Kraków", 50.06, 19.94, ("Kraków",), "POL"),
            ("Budapest", 47.50, 19.04, ("Budapest",), "HUN"),
            ("Danzig", 54.35, 18.65, ("Gdańsk",), "DZG"),
        ],
        river_points=[
            ("Vistula at Warsaw", 52.25, 21.03),
            ("Oder at Breslau", 51.11, 17.03),
            ("Danube at Vienna", 48.22, 16.40),
            ("Elbe at Dresden", 51.05, 13.74),
            ("Rhine at Cologne", 50.94, 6.96),
        ],
        river_hex_band=(1400, 2800),
        cities_min=120,
        sprawl=[("Berlin", 4, ("Berlin",)), ("Warszawa", 3, ("Warszaw",)),
                ("Wien", 3, ("Wien",)), ("Praha", 2, ("Praha",))],
        ruhr_min=10,
        resources=dict(coal_min=10, steel_min=3, iron_min=1),
        resource_points=[("Essen coal+steel", 51.45, 7.01, ("coal", "steel"))],
        provinces_min=70,
        province_partial_countries=("FRA",),
        province_points=[
            ("Breslau -> DEU_NIEDERSCHLESIEN", 51.11, 17.03, "DEU_NIEDERSCHLESIEN"),
            ("Königsberg -> DEU_OSTPREUSSEN", 54.71, 20.51, "DEU_OSTPREUSSEN"),
            ("Katowice -> POL_SLASKIE", 50.26, 19.02, "POL_SLASKIE"),
            ("Praha -> CSK_CESKA", 50.09, 14.42, "CSK_CESKA"),
            ("Bratislava -> CSK_SLOVENSKO", 48.14, 17.11, "CSK_SLOVENSKO"),
            ("Wien -> AUT_NIEDEROESTERREICH", 48.21, 16.37, "AUT_NIEDEROESTERREICH"),
            ("Danzig -> DZG_DANZIG", 54.35, 18.65, "DZG_DANZIG"),
            ("Saarbrücken -> SAA_SAAR", 49.23, 7.00, "SAA_SAAR"),
            # Berlin merged into Brandenburg (Sprint 7 AD-035 addendum)
            ("Berlin -> DEU_BRANDENBURG", 52.52, 13.40, "DEU_BRANDENBURG"),
        ],
        land_slope_p90_band=(18, 30),   # full Alps + Tatra/Carpathians framed
        biome_bands={"hill": ("pct", 10, 30),
                     "mountain": ("count", 1500, 2700),
                     "urban": ("pct", 5, 20), "forest": ("pct", 8, 35)},
    ),
    "para_bellum_wceurope_test": dict(
        # Bands measured from the Sprint 6 regeneration (fixes + AD-035
        # boundaries): 8,607 hexes / 8,238 land.
        hex_count=(8500, 8700),
        countries={"DEU": (3700, 4400), "FRA": (850, 1100),
                   "ITA": (850, 1060), "AUT": (570, 710),
                   "CHE": (420, 520), "CSK": (380, 480),
                   "NLD": (270, 350), "BEL": (130, 190),
                   "YUG": (120, 200), "LUX": (24, 40), "SAA": (15, 32)},
        water_min=300,
        country_points=[
            ("Saarbrücken (League territory)", 49.23, 7.00, "SAA"),
            ("Stettin (German Pomerania)", 53.43, 14.55, "DEU"),
            ("Cheb/Eger (Sudetenland, CSK)", 50.08, 12.37, "CSK"),
            ("Ljubljana (Yugoslav Slovenia)", 46.05, 14.51, "YUG"),
            ("Zürich", 47.37, 8.54, "CHE"),
            ("Milano", 45.46, 9.19, "ITA"),
        ],
        cities=[
            ("Berlin", 52.52, 13.40, ("Berlin",), "DEU"),
            ("München", 48.14, 11.58, ("München",), "DEU"),
            ("Köln", 50.94, 6.96, ("Köln", "Cologne"), "DEU"),
            ("Praha", 50.09, 14.42, ("Praha",), "CSK"),
            ("Zürich", 47.37, 8.54, ("Zürich",), "CHE"),
            ("Milano", 45.46, 9.19, ("Milano",), "ITA"),
        ],
        river_points=[
            ("Rhine at Cologne", 50.94, 6.96),
            ("Danube at Linz", 48.31, 14.29),
            ("Elbe at Dresden", 51.05, 13.74),
            ("Po at Cremona", 45.13, 10.02),
            ("Rhône at Geneva", 46.20, 6.14),
        ],
        river_hex_band=(950, 1350),
        cities_min=200,
        sprawl=[("Berlin", 4, ("Berlin",)), ("München", 3, ("München",)),
                ("Milano", 3, ("Milano",)),
                # Köln reads 2 here (per-tile-merge DEM slopes differ from the
                # Benelux windowed-DEM artifact at the footprint fringe).
                ("Köln", 2, ("Köln",))],
        ruhr_min=10,
        resources=dict(coal_min=10, steel_min=3, iron_min=1),
        resource_points=[("Essen coal+steel", 51.45, 7.01, ("coal", "steel"))],
        provinces_min=50,
        province_partial_countries=("FRA",),
        province_points=[
            ("Köln -> DEU_RHEINLAND", 50.94, 6.96, "DEU_RHEINLAND"),
            ("München -> DEU_BAYERN", 48.14, 11.58, "DEU_BAYERN"),
            # Berlin merged into Brandenburg (Sprint 7 AD-035 addendum)
            ("Berlin -> DEU_BRANDENBURG", 52.52, 13.40, "DEU_BRANDENBURG"),
            ("Dresden -> DEU_SACHSEN_FREISTAAT", 51.05, 13.74, "DEU_SACHSEN_FREISTAAT"),
            ("Praha -> CSK_CESKA", 50.09, 14.42, "CSK_CESKA"),
            ("Innsbruck -> AUT_TIROL", 47.27, 11.39, "AUT_TIROL"),
            ("Zürich -> CHE_ZUERICH", 47.37, 8.54, "CHE_ZUERICH"),
            ("Milano -> ITA_LOMBARDIA", 45.46, 9.19, "ITA_LOMBARDIA"),
        ],
        land_slope_p90_band=(30, 45),   # the Alps are in-frame
        biome_bands={"hill": ("pct", 18, 32), "mountain": ("count", 1400, 2300),
                     "urban": ("pct", 7, 15), "forest": ("pct", 5, 13)},
    ),
}

# Elevation plausibility (generic, AD-032/fix-2 gate): band covers the Dutch
# polders (-7 m) and the Hambach open-pit outlier (-83 m, real modern
# terrain) through Mont Blanc (4809 m); anything outside is a data defect.
LAND_ELEV_BAND = (-120.0, 4900.0)
SLOPE_RANGE = (0.0, 60.0)
SRTM_VOID = -32768.0

# ---------------------------------------------------------------------------

def main() -> int:
    config = sys.argv[1] if len(sys.argv) > 1 else \
        "configs/para_bellum_benelux_germany_test.yaml"
    spec = MapSpec.from_yaml(config)
    exp = EXPECTATIONS.get(spec.name)
    if exp is None:
        print(f"FAIL: no EXPECTATIONS entry for spec '{spec.name}' — add one "
              f"(bands measured from a reviewed run) before gating this bbox.")
        return 1
    output = sys.argv[2] if len(sys.argv) > 2 else \
        f"output/{spec.name}_hex_terrain.json"

    with open(output, encoding="utf-8") as f:
        data = json.load(f)
    hexes = data["hexes"]
    by_coords = {(h["coords"]["col"], h["coords"]["row"]): h for h in hexes}
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = ""):
        checks.append((name, ok, detail))

    def closest_hex(lat: float, lon: float) -> dict:
        return min(hexes, key=lambda h: math.hypot(
            h["geo"]["center_lat"] - lat,
            (h["geo"]["center_lon"] - lon) * math.cos(math.radians(lat))))

    # --- Schema + id format -------------------------------------------------
    ver = data["schema_version"]
    check(f"schema_version is {EXPECTED_SCHEMA}", ver == EXPECTED_SCHEMA,
          f"got {ver}")
    ver_t = tuple(int(x) for x in ver.split("."))

    def expected_id(col: int, row: int) -> str:
        if ver_t >= (1, 0, 5):
            return f"{col}_{row}"          # AD-031 delimited format
        return f"{col:03d}{row:02d}"       # legacy CCCRR (min-width)

    bad_ids = [h["id"] for h in hexes
               if h["id"] != expected_id(h["coords"]["col"], h["coords"]["row"])]
    check("hex ids match documented format for schema version", not bad_ids,
          f"{len(bad_ids)} bad ids, e.g. {bad_ids[:4]}")
    if ver_t >= (1, 0, 5):
        cr = [(h["coords"]["col"], h["coords"]["row"]) for h in hexes]
        check("hexes sorted numerically by (col,row) (AD-031)",
              cr == sorted(cr), "")

    # --- Hex count + duplicates ---------------------------------------------
    lo, hi = exp["hex_count"]
    check(f"hex count in [{lo},{hi}]", lo <= len(hexes) <= hi, f"{len(hexes)}")
    check("no duplicate coords", len(by_coords) == len(hexes),
          f"{len(hexes) - len(by_coords)} duplicates")

    # --- Country distribution ------------------------------------------------
    counts = Counter(h["political"]["country_at_start"] for h in hexes)
    for code, (clo, chi) in exp["countries"].items():
        n = counts.get(code, 0)
        check(f"{code} hexes in [{clo},{chi}]", clo <= n <= chi, f"{code}={n}")

    land = [h for h in hexes if not h["flags"]["is_water"]]
    water_n = len(hexes) - len(land)
    check(f"water hexes >= {exp['water_min']}", water_n >= exp["water_min"],
          f"{water_n} water")
    land_empty = sum(1 for h in land if not h["political"]["country_at_start"])
    check("land hexes without country <= 1% of land",
          land_empty <= len(land) * 0.01, f"{land_empty} empty land hexes")

    # --- Elevation / slope plausibility (Sprint 6 fix-2 gate) ----------------
    elo, ehi = LAND_ELEV_BAND
    bad_elev = [(h["id"], h["geo"]["elevation_m"]) for h in land
                if not (elo <= h["geo"]["elevation_m"] <= ehi)]
    check(f"land elevation in [{elo:.0f},{ehi:.0f}] m", not bad_elev,
          f"{len(bad_elev)}: {bad_elev[:4]}")
    sentinels = [h["id"] for h in hexes
                 if h["geo"]["elevation_m"] <= SRTM_VOID + 1]
    check("no SRTM void sentinel shipped", not sentinels, f"{sentinels[:4]}")
    slo, shi = SLOPE_RANGE
    bad_slope = [(h["id"], h["geo"]["slope_deg"]) for h in hexes
                 if not (slo <= h["geo"]["slope_deg"] <= shi)]
    check(f"slope_deg in [{slo:.0f},{shi:.0f}]", not bad_slope,
          f"{len(bad_slope)}: {bad_slope[:4]}")
    land_slopes = sorted(h["geo"]["slope_deg"] for h in land)
    p90 = land_slopes[int(0.9 * (len(land_slopes) - 1))] if land_slopes else 0.0
    plo, phi = exp["land_slope_p90_band"]
    check(f"land slope p90 in [{plo},{phi}] deg (AD-033 calibration)",
          plo <= p90 <= phi, f"p90={p90:.2f}")

    # --- Biome share tripwires ------------------------------------------------
    bio = Counter(h["terrain"]["biome"] for h in hexes)
    for biome, (kind, blo, bhi) in exp["biome_bands"].items():
        n = bio.get(biome, 0)
        if kind == "pct":
            v = 100.0 * n / max(1, len(land))
            check(f"{biome} {blo}-{bhi}% of land", blo <= v <= bhi,
                  f"{n} hexes = {v:.1f}%")
        else:
            check(f"{biome} count in [{blo},{bhi}]", blo <= n <= bhi, f"{n}")

    # --- Starting infrastructure is INERT (AD-036) ----------------------------
    # port / airfield / fortification are authored construction-system data,
    # NOT pipeline-detected. This guard fails if a future change re-populates
    # them from OSM/OHM (the exact regression AD-036 exists to prevent) — it is
    # NOT a detection gate. `anthrome=fortified` (descriptive land character,
    # AD-015) is deliberately independent and NOT checked against fortification.
    non_inert = [h["id"] for h in hexes
                 if h["infrastructure"]["port"]
                 or h["infrastructure"]["airfield"]
                 or h["infrastructure"]["fortification"] != "none"]
    check("port/airfield/fortification inert per AD-036 (authored, not detected)",
          not non_inert, f"{len(non_inert)} populated, e.g. {non_inert[:4]}")

    # --- Country spot checks (1930 border placement, AD-035) --------------------
    for label, lat, lon, want in exp.get("country_points", []):
        h = closest_hex(lat, lon)
        check(f"{label} -> {want}",
              h["political"]["country_at_start"] == want,
              f"hex {h['id']} -> {h['political']['country_at_start'] or '(none)'}")

    # --- Major city spot checks ------------------------------------------------
    # The city's tagged hex is not always the closest-center hex (a metropolis
    # node near a hex edge tags the CONTAINING hex, and its suburb ring
    # surrounds it) — scan the 3 nearest hexes for the named city.
    def nearest_hexes(lat, lon, n=3):
        return sorted(hexes, key=lambda h: math.hypot(
            h["geo"]["center_lat"] - lat,
            (h["geo"]["center_lon"] - lon) * math.cos(math.radians(lat))))[:n]

    for label, lat, lon, name_keys, country in exp["cities"]:
        cands = nearest_hexes(lat, lon)
        hit = next((h for h in cands
                    if any(k in h["settlement"]["name"] for k in name_keys)
                    and h["settlement"]["type"] in ("city", "metropolis")
                    and h["political"]["country_at_start"] == country), None)
        h0 = cands[0]
        check(f"{label}: tagged city/metropolis, {country}", hit is not None,
              f"nearest hex {h0['id']}: name={h0['settlement']['name']!r} "
              f"type={h0['settlement']['type']} "
              f"country={h0['political']['country_at_start']}")

    # --- Rivers: node model (AD-026/029) ---------------------------------------
    river = {(h["coords"]["col"], h["coords"]["row"])
             for h in hexes if h["rivers"]["has_river"]}
    rlo, rhi = exp["river_hex_band"]
    check(f"has_river hexes in [{rlo},{rhi}]", rlo <= len(river) <= rhi,
          f"{len(river)}")
    mismatch = [h["id"] for h in hexes
                if bool(h["rivers"]["has_river"]) != bool(h["rivers"]["river_name"])]
    check("river_name set iff has_river", not mismatch, f"{len(mismatch)}")

    # Connectivity via the package's single adjacency convention — no parity
    # guessing (grid parity is normalized since Sprint 6 fix 1).
    def nbrs(cr):
        col, row = cr
        return [(col + dc, row + dr) for dc, dr in OFFSET_NEIGHBOR_DELTAS[col % 2]]

    comp_of: dict[tuple[int, int], int] = {}
    comps: list[set] = []
    for cell in sorted(river):
        if cell in comp_of:
            continue
        stack, comp = [cell], set()
        while stack:
            cur = stack.pop()
            if cur in comp_of:
                continue
            comp_of[cur] = len(comps)
            comp.add(cur)
            stack.extend(n for n in nbrs(cur) if n in river and n not in comp_of)
        comps.append(comp)
    isolated = [next(iter(c)) for c in comps if len(c) == 1]
    interior_isolated = [c for c in isolated
                         if all(n in by_coords for n in nbrs(c))]
    check("no INTERIOR isolated single-hex rivers", not interior_isolated,
          f"interior isolated: {interior_isolated[:6]} "
          f"(boundary isolated: {len(isolated) - len(interior_isolated)})")

    for label, lat, lon in exp["river_points"]:
        h = closest_hex(lat, lon)
        cr = (h["coords"]["col"], h["coords"]["row"])
        on = cr in river
        chain = len(comps[comp_of[cr]]) if on else 0
        check(f"{label}: river hex in chain of >= 3", on and chain >= 3,
              f"hex {h['id']} chain={chain}")

    # --- Settlements ------------------------------------------------------------
    settled = sum(1 for h in hexes if h["settlement"]["type"] != "none")
    check("settlement hexes <= 40%", settled <= 0.40 * len(hexes),
          f"{settled} = {100 * settled / len(hexes):.1f}%")
    cities_n = sum(1 for h in hexes
                   if h["settlement"]["type"] in ("city", "metropolis"))
    check(f">= {exp['cities_min']} cities/metropolises",
          cities_n >= exp["cities_min"], f"{cities_n}")

    bridges = sum(1 for h in hexes if h["infrastructure"]["bridge"])
    check("bridge hexes >= 50% of river hexes",
          bridges >= 0.5 * len(river), f"{bridges} vs {len(river)} river")

    # --- Multi-hex urban sprawl (AD-014) ------------------------------------------
    by_city: dict[str, list] = defaultdict(list)
    for h in hexes:
        pc = h["settlement"].get("parent_city", "")
        if pc:
            by_city[pc].append(h)
    for label, need, subs in exp["sprawl"]:
        n = sum(len(v) for pc, v in by_city.items()
                if any(s.lower() in pc.lower() for s in subs))
        check(f"{label} footprint >= {need} hexes", n >= need, f"{n}")
    if exp["ruhr_min"]:
        ruhr = [h for h in hexes if h["settlement"].get("parent_city")
                and 6.5 <= h["geo"]["center_lon"] <= 7.65
                and 51.25 <= h["geo"]["center_lat"] <= 51.62]
        check(f"Ruhr >= {exp['ruhr_min']} urban hexes",
              len(ruhr) >= exp["ruhr_min"], f"{len(ruhr)}")
    fp = [h for v in by_city.values() for h in v]
    bad_anthrome = [h["id"] for h in fp if h["settlement"]["anthrome"] not in
                    ("metro", "industrial", "residential", "outskirts")]
    check("footprint hexes carry urban anthromes", not bad_anthrome,
          f"{len(fp)} footprint, {len(bad_anthrome)} bad")
    orphans = [h["id"] for h in hexes if h["settlement"]["type"] == "suburb"
               and not h["settlement"].get("parent_city")]
    check("every suburb has a parent_city", not orphans, f"{len(orphans)}")

    # --- Strategic resources (F-2; western coverage only) --------------------------
    if exp["resources"]:
        res = {r: sum(1 for h in hexes if h["resources"].get(r))
               for r in ("coal", "steel", "iron", "oil")}
        for r in ("coal", "steel", "iron"):
            need = exp["resources"][f"{r}_min"]
            check(f"{r} hexes >= {need}", res[r] >= need, f"{res[r]}")
        for label, lat, lon, kinds in exp["resource_points"]:
            h = closest_hex(lat, lon)
            ok = all(h["resources"].get(k) for k in kinds)
            check(label, ok,
                  f"hex {h['id']} " + " ".join(f"{k}={h['resources'].get(k)}" for k in kinds))

    # --- Provinces + admin tiers (AD-023/027) ---------------------------------------
    provs = sorted({h["political"]["province_at_start"] for h in hexes
                    if h["political"]["province_at_start"]})
    cap_by_prov = Counter()
    sub_total = 0
    for h in hexes:
        t = h["settlement"]["admin_tier"]
        if t == "capital":
            cap_by_prov[h["political"]["province_at_start"]] += 1
        elif t == "sub_capital":
            sub_total += 1
    check(f"{exp['provinces_min']}+ provinces framed",
          len(provs) >= exp["provinces_min"], f"{len(provs)}")
    multi = [p for p in provs if cap_by_prov[p] > 1]
    check("no province with >1 capital hex", not multi, f"{multi[:4]}")
    framed = [p for p in provs if cap_by_prov[p]]
    check("every framed province has exactly one capital",
          all(cap_by_prov[p] == 1 for p in framed), f"{len(framed)} framed")
    # Countries with an authored province layer — derived from the metadata,
    # not hardcoded (Sprint 6: DEU/POL/CSK/AUT/SAA/DZG joined; HUN etc. are
    # country-only per AD-035, CHE/ITA still pending per AD-028).
    covered_countries = set()
    mdp = Path("data/boundaries/provinces_1930_metadata.json")
    if mdp.exists():
        md_prov = json.load(open(mdp, encoding="utf-8"))
        covered_countries = {p.get("country", p["province_id"].split("_")[0])
                             for p in md_prov["provinces"]}
    # Countries whose authored provinces only cover PART of this bbox are
    # excluded from the hard gate but reported (e.g. FRA outside the Sprint 5
    # Benelux-frame départements — backfill pending).
    partial = set(exp.get("province_partial_countries", ()))
    covered_countries -= partial
    prov_land = [h for h in land
                 if h["political"]["country_at_start"] in covered_countries]
    no_prov = [h for h in prov_land if not h["political"]["province_at_start"]]
    check("province coverage of covered-country land >= 98%",
          len(no_prov) <= 0.02 * max(1, len(prov_land)),
          f"{len(no_prov)} of {len(prov_land)} uncovered "
          f"({len(covered_countries)} covered countries)")
    for c in sorted(partial):
        n_un = sum(1 for h in land if h["political"]["country_at_start"] == c
                   and not h["political"]["province_at_start"])
        check(f"[info] {c} partially authored — uncovered hexes reported",
              True, f"{n_un} uncovered (backfill pending)")

    # Country/province consistency (Sprint 6 review fix): a hex's province
    # must belong to its country — the old unrestricted 0.2° snap violated
    # this along borders with province-less neighbours.
    prov_country = {}
    if mdp.exists():
        prov_country = {p["province_id"]: p.get("country",
                                                p["province_id"].split("_")[0])
                        for p in md_prov["provinces"]}
    inconsistent = [h["id"] for h in hexes
                    if h["political"]["province_at_start"]
                    and h["political"]["country_at_start"]
                    and prov_country.get(h["political"]["province_at_start"])
                    not in (None, h["political"]["country_at_start"])]
    check("province country == country_at_start on every hex",
          not inconsistent, f"{len(inconsistent)}: {inconsistent[:6]}")
    orphan_prov = [h["id"] for h in hexes
                   if h["political"]["province_at_start"]
                   and not h["political"]["country_at_start"]]
    check("no province on a country-less hex", not orphan_prov,
          f"{len(orphan_prov)}: {orphan_prov[:4]}")
    bad_settled = [h["id"] for h in hexes
                   if h["political"]["province_at_start"]
                   and h["settlement"]["type"] != "none"
                   and h["settlement"]["admin_tier"] not in
                   ("capital", "sub_capital", "urban")]
    check("settled in-province hexes are capital/sub/urban", not bad_settled,
          f"{len(bad_settled)}")
    rural_named = [h["id"] for h in hexes
                   if h["settlement"]["admin_tier"] == "rural"
                   and h["settlement"]["name"]]
    check("no rural hex carries a settlement name", not rural_named,
          f"{len(rural_named)}")
    for label, lat, lon, want in exp["province_points"]:
        h = closest_hex(lat, lon)
        check(label, h["political"]["province_at_start"] == want,
              f"hex {h['id']} -> {h['political']['province_at_start'] or '(none)'}")

    # Authored-layer totals (global facts, not per-bbox)
    mdp = Path("data/boundaries/provinces_1930_metadata.json")
    if mdp.exists():
        md = json.load(open(mdp, encoding="utf-8"))
        caps = sum(1 for p in md["provinces"]
                   if p.get("capital", {}).get("city_name"))
        subs = sum(len(p.get("sub_capitals", [])) for p in md["provinces"])
        # Sprint 6 (AD-035): 92 provinces / 92 capitals / 124 sub-capitals.
        check("authored layer has 80+ capitals", caps >= 80, f"{caps}")
        check("authored layer sub-capitals sane (100-200)",
              100 <= subs <= 200, f"{subs}")

    # --- Summary -----------------------------------------------------------------
    print(f"Validation — {spec.name}  ({output})")
    print(f"{len(hexes)} hexes, schema {ver}")
    print(f"countries: "
          f"{ {k or '(none)': v for k, v in counts.most_common()} }\n")
    failed = 0
    for name, ok, detail in checks:
        if not ok:
            failed += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}"
              + (f"  [{detail}]" if detail else ""))
    print("\n" + "=" * 50)
    if failed:
        print(f"OVERALL: FAIL ({failed}/{len(checks)} checks failed)")
        return 1
    print(f"OVERALL: PASS ({len(checks)}/{len(checks)} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
