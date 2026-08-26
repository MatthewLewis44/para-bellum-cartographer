"""1930 provinces for the eight frame nations (Pass A, task 1).

Extends data/boundaries/provinces_1930.geojson + provinces_1930_metadata.json
with provinces for DNK, HUN, LTU, LVA, ROU, SOV, SWE and YUG — the eight
nations that framed the east-expansion map with a country_at_start but an
EMPTY province_at_start on every hex. ProvinceIndex.Build skips hexes with no
province, so those eight indexed nothing: no money, no manpower, and — because
resource yield is a per-PROVINCE walk over ProvinceInfo.ResourceHexes — none of
their 407 resource hexes produced either.

APPEND-ONLY. Every one of the 138 provinces already in the file is copied
through byte-for-byte; this tool never edits or reorders them.

-- Tier choice -------------------------------------------------------------

There is no single "1930 subdivision tier". Each nation's genuine top-level
1930 units sit at wildly different granularities (Prussian provinces average
~19,000 km²; Swiss cantons ~1,600). The shipped map is authored at the coarse
end — DEU 29 provinces, POL 16, CSK 4, AUT 9 — so a frame nation authored at
its own finest genuine tier would get ten times the capture granularity and,
under AD-M09 (building slots are per-province), ten times the industrial
capacity per unit area, for no authored reason.

The rule used here: province = the coarsest unit that is EITHER a genuine 1930
administrative unit OR a genuine grouping of them (never a meridian/parallel
cut-line — AD-035 retired those), chosen to land in the density band the
shipped map already uses.

HUNGARY IS THE DELIBERATE EXCEPTION. Matthew directed the genuine COUNTY tier
for HUN directly (2026-08-26), against a recommendation to group it into the
density band, accepting the density consequence. See AD-037 and
the density note in the pass report: HUN lands at roughly a third of the
DEU/POL hexes-per-province figure, and is the second-densest nation on the map
after CHE. This is recorded rather than silently normalised because pass B owns
the normalisation decision, and Hungary is now a second data point for it.

-- Geometry provenance ------------------------------------------------------

OpenHistoricalMap has NO 1930-valid admin relations for HUN, ROU, LTU, SWE,
YUG or the Soviet strip (probed 2026-08-26: `relation[boundary=administrative]
[admin_level=4|6]` over each country's bbox returns zero relations whose
[start_date, end_date) covers 1930-01-01). It DOES have the Danish amter and
the three Memelland Kreise. So:

  * DNK  — OHM amt relations (18), grouped into landsdele. Genuine 1930 lines.
  * LTU_KLAIPEDA — OHM Memel + Heydekrug + Pogegen Kreise. Genuine 1930 lines.
  * everything else — Natural Earth admin-1 unions, clipped to the 1930 country
    polygon. MODERN internal lines, 1930 external lines. This is the AD-027
    stopgap precedent that BEL/NLD/FRA/CHE/ITA already ship under, and the
    error it carries is an internal division line, never a national border:
    the country polygons in boundaries_1930.geojson are the 1930 ones and every
    province is clipped to them.

Per-province provenance is recorded in the feature's `notes` so a later
historical-review pass can tell the two apart.

-- Frame scoping ------------------------------------------------------------

SWE, YUG, LVA and SOV are authored for the southern/northern block the map
actually frames, not for their whole national territory (Sweden north of
Småland, Yugoslavia south of Croatia, Latvia west of Latgale and the Soviet
Union east of the Podolian strip are all outside the bbox and would be dead
weight). validate_full_bbox.py gates this: adding a country to the metadata
puts it in `covered_countries`, which hard-fails if <98% of its land hexes
carry a province. Widening the bbox therefore fails loudly instead of silently
producing province-less hexes.

Usage: uv run python tools/build_provinces_1930_frames.py
       (run AFTER tools/build_boundaries_1930_east.py)
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import requests
from pyproj import Geod
from shapely.geometry import Point, Polygon, box, mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = ROOT / "data" / "boundaries" / "boundaries_1930.geojson"
PROV_GEO = ROOT / "data" / "boundaries" / "provinces_1930.geojson"
PROV_META = ROOT / "data" / "boundaries" / "provinces_1930_metadata.json"
CACHE = Path.home() / "wargame-cartographer" / "cache" / "boundaries" / "ohm_1930"
NE_STATES = (Path.home() / "wargame-cartographer" / "cache" / "vector"
             / "ne_10m_states" / "ne_10m_admin_1_states_provinces.shp")
NE_LAND = (Path.home() / "wargame-cartographer" / "cache" / "vector"
           / "ne_10m_land" / "ne_10m_land.shp")
OVERPASS = "https://overpass-api.openhistoricalmap.org/api/interpreter"
SCENARIO_DATE = "1930-01-01"
GEOD = Geod(ellps="WGS84")

FRAME_NATIONS = ("DNK", "HUN", "LTU", "LVA", "ROU", "SOV", "SWE", "YUG")


def cap(name, lon, lat, match=(), note=""):
    d = {"city_name": name, "at": [lon, lat]}
    if match:
        d["match_names"] = list(match)
    if note:
        d["note"] = note
    return d


def sub(name, rationale, match=()):
    d = {"city_name": name, "rationale": rationale}
    if match:
        d["match_names"] = list(match)
    return d


# ---------------------------------------------------------------------------
# Denmark — OHM amt relations, grouped into landsdele.
# 18 amter are valid at 1930-01-01 in OHM. The four Sønderjylland amter
# (Haderslev / Aabenraa / Sønderborg / Tønder, created 1920 after the
# plebiscite) are NOT in OHM, so Sønderjylland is DERIVED as country-minus-the-
# eighteen — the same construction build_provinces_1930_east.py uses for
# Slovakia. Sønderjylland then joins Vejle Amt as DNK_SYDJYLLAND.
# ---------------------------------------------------------------------------
DNK_AMTER = {
    "Aalborg": 2855427, "Bornholms": 2855344, "Frederiksborg": 2855388,
    "Hjørring": 2855428, "Holbæk": 2856266, "Københavns": 2870219,
    "Maribo": 2855389, "Odense": 2855401, "Præstø": 2855390,
    "Randers": 2855426, "Ribe": 2855413, "Ringkøbing": 2855415,
    "Sorø": 2855394, "Svendborg": 2855402, "Thisted": 2855424,
    "Vejle": 2855412, "Viborg": 2855425, "Århus": 2855422,
}

DNK_GROUPS = {
    "DNK_NORDJYLLAND": (
        ["Hjørring", "Thisted", "Aalborg", "Viborg"], "Nordjylland",
        cap("Aalborg", 9.92, 57.05),
        [sub("Viborg", "stiftsstad and rail junction"),
         sub("Hjørring", "northern amt seat")]),
    "DNK_OSTJYLLAND": (
        ["Randers", "Århus"], "Østjylland",
        cap("Århus", 10.21, 56.16),
        [sub("Randers", "amt seat, Gudenå crossing"),
         sub("Horsens", "in-frame industrial town — carries the seat yield "
                        "south of the 56°N frame edge")]),
    "DNK_VESTJYLLAND": (
        ["Ringkøbing", "Ribe"], "Vestjylland",
        cap("Ribe", 8.77, 55.33),
        [sub("Esbjerg", "North Sea port, largest town of the group"),
         sub("Varde", "amt market town"),
         sub("Herning", "Jutland heath rail hub")]),
    "DNK_SYDJYLLAND": (
        ["Vejle"], "Sydjylland",   # + derived Sønderjylland, added below
        cap("Vejle", 9.53, 55.71),
        [sub("Kolding", "Lillebælt rail crossing"),
         sub("Haderslev", "principal town of the 1920 Sønderjylland amter")]),
    "DNK_FYN": (
        ["Odense", "Svendborg"], "Fyn",
        cap("Odense", 10.39, 55.40),
        [sub("Svendborg", "southern Funen port")]),
    "DNK_SJAELLAND": (
        ["Frederiksborg", "Københavns", "Holbæk", "Sorø", "Præstø"], "Sjælland",
        cap("København", 12.57, 55.68),
        [sub("Roskilde", "cathedral city, rail junction"),
         sub("Helsingør", "Øresund crossing"),
         sub("Slagelse", "west Zealand market town"),
         sub("Næstved", "south Zealand market town")]),
    "DNK_LOLLAND_FALSTER": (
        ["Maribo"], "Lolland-Falster",
        cap("Nykøbing Falster", 11.87, 54.77),
        [sub("Nakskov", "Lolland shipyard town")]),
    "DNK_BORNHOLM": (
        ["Bornholms"], "Bornholm",
        cap("Rønne", 14.70, 55.10),
        [sub("Nexø", "eastern harbour")]),
}

# ---------------------------------------------------------------------------
# Lithuania — OHM Memelland Kreise for Klaipėda; NE apskritys grouped into the
# ethnographic regions for the rest. Vilniaus apskritis is clipped away by the
# 1930 LTU country polygon (Wilno was Polish in 1930 — POL_WILENSKIE).
# ---------------------------------------------------------------------------
LTU_MEMEL_KREISE = {"Memel": 2693176, "Heydekrug": 2692563, "Pogegen": 2692564,
                    "Stadtkreis Memel": 2693177}

# ---------------------------------------------------------------------------
# NE admin-1 groupings. (adm0_a3, [NE `name` values]) — exact NE spellings,
# which are not always the modern orthography ("Gyor-Moson-Sopron", "Telšiai").
# A name that matches nothing is a hard failure, so typos abort the build.
# ---------------------------------------------------------------------------
NE_GROUPS: dict[str, dict] = {
    # ---- Hungary: the COUNTY tier (PM-directed, AD-037). -------------------
    # NE carries the 19 modern megyék + Budapest. The 1930 set was 25: the
    # modern counties are 1950 amalgamations and are NOT reversible from any
    # public-domain vector source (no OHM relations, no NE historical layer),
    # so each province is named for the modern megye and its `notes` record
    # the 1930 counties it absorbs. Budapest is merged into Pest rather than
    # authored as a city-province, per the Sprint 7 AD-035 addendum that merged
    # Berlin back into Brandenburg.
    "HUN_BACS_KISKUN": ("HUN", ["Bács-Kiskun"], "Bács-Kiskun",
        cap("Kecskemét", 19.69, 46.91),
        [sub("Kiskunhalas", "Kiskunság market town"),
         sub("Kiskunfélegyháza", "rail junction")],
        "1930: Bács-Bodrog + southern Pest-Pilis-Solt-Kiskun"),
    "HUN_BARANYA": ("HUN", ["Baranya"], "Baranya",
        cap("Pécs", 18.23, 46.08),
        [sub("Mohács", "Danube crossing")],
        "1930: Baranya (unchanged)"),
    "HUN_BEKES": ("HUN", ["Békés"], "Békés",
        cap("Békéscsaba", 21.09, 46.68),
        [sub("Gyula", "county seat until 1950"),
         sub("Orosháza", "Alföld market town")],
        "1930: Békés (unchanged)"),
    "HUN_BORSOD": ("HUN", ["Borsod-Abaúj-Zemplén"], "Borsod-Abaúj-Zemplén",
        cap("Miskolc", 20.78, 48.10),
        [sub("Ózd", "ironworks"),
         sub("Sátoraljaújhely", "Zemplén seat"),
         sub("Kazincbarcika", "Sajó valley coal")],
        "1930: Borsod-Gömör-Kishont + Abaúj-Torna + Zemplén"),
    "HUN_CSONGRAD": ("HUN", ["Csongrád"], "Csongrád",
        cap("Szeged", 20.15, 46.25),
        [sub("Hódmezővásárhely", "Alföld market town"),
         sub("Szentes", "Tisza crossing")],
        "1930: Csongrád (unchanged)"),
    "HUN_FEJER": ("HUN", ["Fejér"], "Fejér",
        cap("Székesfehérvár", 18.41, 47.19),
        [sub("Dunaújváros", "Danube crossing", ("Dunapentele",))],
        "1930: Fejér (unchanged)"),
    "HUN_GYOR_MOSON_SOPRON": ("HUN", ["Gyor-Moson-Sopron"], "Győr-Moson-Sopron",
        cap("Győr", 17.64, 47.69),
        [sub("Sopron", "1930 county seat of Sopron"),
         sub("Mosonmagyaróvár", "Moson seat")],
        "1930: Győr-Moson-Pozsony + Sopron"),
    "HUN_HAJDU_BIHAR": ("HUN", ["Hajdú-Bihar"], "Hajdú-Bihar",
        cap("Debrecen", 21.63, 47.53),
        [sub("Hajdúböszörmény", "hajdú town"),
         sub("Hajdúszoboszló", "rail junction")],
        "1930: Hajdú + the Hungarian remnant of Bihar"),
    "HUN_HEVES": ("HUN", ["Heves"], "Heves",
        cap("Eger", 20.38, 47.90),
        [sub("Gyöngyös", "Mátra market town")],
        "1930: Heves (unchanged)"),
    "HUN_JASZ_NAGYKUN_SZOLNOK": ("HUN", ["Jász-Nagykun-Szolnok"],
        "Jász-Nagykun-Szolnok",
        cap("Szolnok", 20.19, 47.18),
        [sub("Jászberény", "Jászság seat"),
         sub("Karcag", "Nagykunság seat")],
        "1930: Jász-Nagykun-Szolnok (unchanged)"),
    "HUN_KOMAROM_ESZTERGOM": ("HUN", ["Komárom-Esztergom"], "Komárom-Esztergom",
        cap("Esztergom", 18.74, 47.79),
        [sub("Tatabánya", "brown-coal basin"),
         sub("Tata", "market town")],
        "1930: Komárom-Esztergom (the Hungarian remnant; Komárom town itself "
        "was Czechoslovak)"),
    "HUN_NOGRAD": ("HUN", ["Nógrád"], "Nógrád",
        cap("Balassagyarmat", 19.30, 48.07),
        [sub("Salgótarján", "coal and steel — carries the seat yield if the "
                            "county seat does not tag")],
        "1930: Nógrád-Hont (Hungarian remnant)"),
    "HUN_PEST": ("HUN", ["Pest"], "Pest-Pilis-Solt",
        cap("Budapest", 19.04, 47.50),
        [sub("Vác", "Danube rail crossing"),
         sub("Cegléd", "Alföld rail junction"),
         sub("Gödöllő", "royal seat")],
        "1930: Pest-Pilis-Solt-Kiskun minus the Kiskunság (see "
        "HUN_BACS_KISKUN); Budapest merged in rather than authored as a "
        "city-province (AD-035 addendum, as Berlin into Brandenburg)"),
    "HUN_SOMOGY": ("HUN", ["Somogy"], "Somogy",
        cap("Kaposvár", 17.79, 46.36),
        [sub("Siófok", "Balaton rail head")],
        "1930: Somogy (unchanged)"),
    "HUN_SZABOLCS": ("HUN", ["Szabolcs-Szatmár-Bereg"], "Szabolcs-Szatmár",
        cap("Nyíregyháza", 21.72, 47.96),
        [sub("Mátészalka", "Szatmár rail junction")],
        "1930: Szabolcs-Ung + Szatmár-Ugocsa-Bereg (Hungarian remnants)"),
    "HUN_TOLNA": ("HUN", ["Tolna"], "Tolna",
        cap("Szekszárd", 18.70, 46.35),
        [sub("Dombóvár", "rail junction")],
        "1930: Tolna (unchanged)"),
    "HUN_VAS": ("HUN", ["Vas"], "Vas",
        cap("Szombathely", 16.62, 47.23),
        [sub("Sárvár", "Rába crossing")],
        "1930: Vas (Hungarian remnant; the west went to Burgenland)"),
    "HUN_VESZPREM": ("HUN", ["Veszprém"], "Veszprém",
        cap("Veszprém", 17.91, 47.09),
        [sub("Pápa", "market town"),
         sub("Ajka", "Bakony coal")],
        "1930: Veszprém (unchanged)"),
    "HUN_ZALA": ("HUN", ["Zala"], "Zala",
        cap("Zalaegerszeg", 16.84, 46.84),
        [sub("Nagykanizsa", "southern rail junction")],
        "1930: Zala (Hungarian remnant)"),

    # ---- Romania: historical provinces (the CSK "lands" analogue). ---------
    "ROU_TRANSILVANIA": ("ROU",
        ["Alba", "Bistrita-Nasaud", "Brasov", "Cluj", "Covasna", "Harghita",
         "Hunedoara", "Mures", "Salaj", "Sibiu"], "Transilvania",
        cap("Cluj", 23.60, 46.77, ("Cluj-Napoca",)),
        [sub("Târgu Mureș", "Székely Land seat"),
         sub("Sibiu", "Saxon seat"),
         sub("Brașov", "industry and Carpathian pass"),
         sub("Alba Iulia", "1918 union seat"),
         sub("Turda", "salt and chemicals")],
        "1930 historical province"),
    "ROU_BANAT": ("ROU", ["Timis", "Caras-Severin"], "Banat",
        cap("Timișoara", 21.23, 45.75),
        [sub("Reșița", "ironworks and rolling mills")],
        "1930 historical province"),
    "ROU_CRISANA": ("ROU", ["Arad", "Bihor"], "Crișana",
        cap("Oradea", 21.92, 47.06),
        [sub("Arad", "rail and machine works"),
         sub("Salonta", "border market town")],
        "1930 historical province"),
    "ROU_MARAMURES": ("ROU", ["Maramures", "Satu Mare"], "Maramureș",
        cap("Baia Mare", 23.58, 47.66),
        [sub("Satu Mare", "Someș rail junction"),
         sub("Sighetu Marmației", "Tisza crossing")],
        "1930 historical province"),
    "ROU_BUCOVINA": ("ROU", ["Suceava"], "Bucovina",
        cap("Cernăuți", 25.94, 48.29, ("Чернівці", "Chernivtsi")),
        [sub("Suceava", "southern Bucovina seat"),
         sub("Rădăuți", "market town"),
         sub("Câmpulung Moldovenesc", "Carpathian timber")],
        "1930 historical province; northern Bucovina supplied from UKR "
        "Chernivtsi and clipped to the 1930 ROU polygon"),
    "ROU_MOLDOVA": ("ROU",
        ["Bacau", "Botosani", "Galati", "Iasi", "Neamt", "Vaslui", "Vrancea"],
        "Moldova",
        cap("Iași", 27.60, 47.16),
        [sub("Botoșani", "in-frame northern seat — carries the seat yield "
                         "west of the 26.9°E frame edge"),
         sub("Piatra-Neamț", "Bistrița valley"),
         sub("Bacău", "oil-field rail head")],
        "1930 historical province"),
    "ROU_MUNTENIA": ("ROU",
        ["Arges", "Braila", "Buzau", "Calarasi", "Dâmbovita", "Giurgiu",
         "Ialomita", "Ilfov", "Prahova", "Teleorman"], "Muntenia",
        cap("București", 26.10, 44.44),
        [sub("Ploiești", "the refineries"),
         sub("Brăila", "Danube grain port")],
        "1930 historical province"),
    "ROU_OLTENIA": ("ROU", ["Dolj", "Gorj", "Mehedinti", "Olt", "Vâlcea"],
        "Oltenia",
        cap("Craiova", 23.80, 44.32),
        [sub("Turnu Severin", "Iron Gates")],
        "1930 historical province"),
    "ROU_DOBROGEA": ("ROU", ["Constanta", "Tulcea"], "Dobrogea",
        cap("Constanța", 28.65, 44.18),
        [sub("Tulcea", "Danube delta")],
        "1930 historical province"),
    "ROU_BASARABIA": ("MDA", None, "Basarabia",
        cap("Chișinău", 28.86, 47.01),
        [sub("Bălți", "northern seat"),
         sub("Tighina", "Dniester crossing")],
        "1930 historical province; supplied from the whole of modern MDA plus "
        "Ukrainian Odessa oblast (the Budjak, Romanian in 1930 and now "
        "Ukrainian) and clipped to the 1930 ROU polygon"),

    # ---- Lithuania: ethnographic regions + Memelland (built separately). ---
    "LTU_ZEMAITIJA": ("LTU", ["Telšiai", "Taurages", "Klaipedos", "Šiauliai"],
        "Žemaitija",
        cap("Telšiai", 22.25, 55.98),
        [sub("Šiauliai", "rail junction and leather works"),
         sub("Tauragė", "Prussian border town"),
         sub("Plungė", "market town")],
        "ethnographic region; Šiauliai apskritis grouped here rather than with "
        "Aukštaitija (it straddles the two) — flagged for historical review"),
    "LTU_AUKSTAITIJA": ("LTU", ["Kauno", "Panevezio", "Utenos", "Vilniaus"],
        "Aukštaitija",
        cap("Kaunas", 23.90, 54.90),
        [sub("Panevėžys", "northern rail junction"),
         sub("Utena", "eastern market town"),
         sub("Ukmergė", "Šventoji crossing")],
        "ethnographic region; contains Kaunas, the 1930 provisional capital. "
        "Modern Vilniaus apskritis is included as a source polygon and the "
        "1930 country clip removes the part that was Polish (Wilno) — without "
        "it the Lithuanian side of that border is left province-less"),
    "LTU_SUVALKIJA": ("LTU", ["Marijampoles"], "Suvalkija",
        cap("Marijampolė", 23.35, 54.56),
        [sub("Vilkaviškis", "border market town")],
        "ethnographic region"),
    "LTU_DZUKIJA": ("LTU", ["Alytaus"], "Dzūkija",
        cap("Alytus", 24.05, 54.40),
        [sub("Druskininkai", "Nemunas spa town")],
        "ethnographic region"),

    # ---- Latvia: frame-scoped to Latgale (the only in-frame corner). -------
    "LVA_LATGALE": ("LVA",
        ["Aglonas", "Baltinavas", "Balvu", "Ciblas", "Dagdas", "Daugavpils",
         "Ilukstes", "Karsavas", "Kraslavas", "Livanu", "Ludzas", "Preilu",
         "Rezeknes", "Riebinu", "Rugaju", "Varaklanu", "Varkavas",
         "Vilakas", "Vilanu", "Zilupes"], "Latgale",
        cap("Daugavpils", 26.53, 55.87),
        [sub("Rēzekne", "Latgale rail junction"),
         sub("Krāslava", "Daugava crossing")],
        "1930 historical region; Latvia is authored frame-scoped — Kurzeme, "
        "Zemgale and Vidzeme lie outside the bbox and are NOT authored"),

    # ---- Sweden: frame-scoped to the southern landsdelar. -----------------
    "SWE_SKANE": ("SWE", ["Skåne"], "Skåne",
        cap("Malmö", 13.00, 55.60),
        [sub("Lund", "university and rail junction"),
         sub("Helsingborg", "Øresund crossing"),
         sub("Kristianstad", "eastern län seat")],
        "1930 landskap (Malmöhus + Kristianstad län)"),
    "SWE_BLEKINGE": ("SWE", ["Blekinge"], "Blekinge",
        cap("Karlskrona", 15.59, 56.16),
        [sub("Karlshamn", "Baltic port")],
        "1930 landskap"),
    "SWE_HALLAND": ("SWE", ["Halland"], "Halland",
        cap("Halmstad", 12.86, 56.67),
        [sub("Varberg", "Kattegat port")],
        "1930 landskap"),
    "SWE_SMALAND": ("SWE", ["Kronoberg", "Kalmar", "Jönköping"], "Småland",
        cap("Jönköping", 14.16, 57.78),
        [sub("Växjö", "Kronoberg seat"),
         sub("Kalmar", "Baltic port")],
        "1930 landskap (Kronoberg + Kalmar + Jönköping län)"),

    # ---- Yugoslavia: 1929 banovinas, frame-scoped to the northwest. -------
    "YUG_DRAVSKA": ("SVN", None, "Dravska banovina",
        cap("Ljubljana", 14.51, 46.06),
        [sub("Maribor", "Drava industry — carries the seat yield south of the "
                        "46.3°N frame edge"),
         sub("Celje", "Savinja valley"),
         sub("Ptuj", "Drava crossing")],
        "1929 banovina; supplied from the whole of modern SVN and clipped to "
        "the 1930 YUG polygon, which removes Italian Primorska/Istria"),
    "YUG_SAVSKA": ("HRV",
        ["Zagrebacka", "Krapinsko-Zagorska", "Varaždinska",
         "Medimurska", "Koprivničko-Križevačka", "Bjelovarsko-bilogorska",
         "Sisacko-Moslavacka", "Karlovacka", "Viroviticko-Podravska",
         "Brodsko-Posavska", "Osjecko-Baranjska", "Vukovarsko-Srijemska"],
        "Savska banovina",
        cap("Zagreb", 15.98, 45.81),
        [sub("Varaždin", "in-frame Drava town — carries the seat yield"),
         sub("Karlovac", "Kupa crossing"),
         sub("Bjelovar", "Podravina market town")],
        "1929 banovina; the eastern Slavonian counties are included here "
        "although Dunavska banovina held part of Srijem — out of frame, "
        "flagged for historical review"),
    "YUG_PRIMORSKA": ("HRV",
        ["Primorsko-Goranska", "Licko-Senjska", "Zadarska", "Šibensko-Kninska",
         "Splitsko-Dalmatinska", "Dubrovacko-Neretvanska", "Istarska"],
        "Primorska banovina",
        cap("Split", 16.44, 43.51),
        [sub("Sušak", "Rijeka's Yugoslav half"),
         sub("Dubrovnik", "southern port")],
        "1929 banovina; Istria is clipped away by the 1930 YUG polygon "
        "(Italian). Entirely out of frame"),

    # ---- Soviet Union: the framed Podolian strip, one province. -----------
    # boundaries_1930.geojson carries SOV as a bbox-CLIPPED strip already
    # (build_boundaries_1930_east.py EAST_BBOX), so unioning four modern
    # oblasts and intersecting with it yields exactly the strip. At
    # 1930-01-01 the strip spanned the Kamianets-Podilskyi and Shepetivka
    # okruhas; okruhas were abolished in September 1930 and no public-domain
    # vector of them exists, so the strip is authored as ONE province rather
    # than split on a parallel (AD-035 retired cut-lines).
    "SOV_PODOLIA": ("UKR",
        ["Khmel'nyts'kyy", "Ternopil'", "Rivne", "Vinnytsya"], "Podolia",
        cap("Kam'ianets-Podilskyi", 26.59, 48.68, ("Кам'янець-Подільський",)),
        [sub("Slavuta", "northern rail town", ("Славута",)),
         sub("Shepetivka", "1930 okruha seat", ("Шепетівка",))],
        "framed strip only; spans the 1930 Kamianets-Podilskyi and Shepetivka "
        "okruhas, authored as one province (no 1930 okruha vector exists)"),
}


def overpass(query: str, cache_name: str) -> dict:
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"{cache_name}.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = requests.post(OVERPASS, data={"data": query}, timeout=600)
    r.raise_for_status()
    d = r.json()
    if not d.get("elements"):
        raise RuntimeError(f"OHM returned no elements for {cache_name}")
    f.write_text(json.dumps(d), encoding="utf-8")
    return d


def assemble(elements: list, label: str, fill_holes: bool = False):
    """Polygonise an `out geom` relation into a single (multi)polygon.

    Same construction as build_provinces_1930_east.assemble: OHM returns member
    way geometry INSIDE `relation.members[]`, not as separate way elements, and
    an unclosed merged outer line must fail loud rather than be silently
    dropped by polygonize() (AD-035 review finding 5 — a vanished exclave still
    passes a coarse coverage check).
    """
    from shapely.geometry import LineString, MultiLineString
    from shapely.ops import linemerge, polygonize

    outers, inners = [], []
    for e in elements:
        for m in e.get("members", []):
            if m.get("type") == "way" and "geometry" in m:
                c = [(p["lon"], p["lat"]) for p in m["geometry"]]
                if len(c) >= 2:
                    (inners if m.get("role") == "inner" else outers).append(
                        LineString(c))
    if not outers:
        raise RuntimeError(f"{label}: no outer ways")
    merged = linemerge(MultiLineString(outers))
    lines = list(merged.geoms) if merged.geom_type == "MultiLineString" else [merged]
    open_lines = [ln for ln in lines if not ln.is_ring]
    if open_lines:
        raise RuntimeError(
            f"{label}: {len(open_lines)} merged outer line(s) do not close")
    geom = unary_union(list(polygonize(merged)))
    if inners and not fill_holes:
        hp = list(polygonize(linemerge(MultiLineString(inners))))
        if hp:
            geom = geom.difference(unary_union(hp))
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        raise RuntimeError(f"{label}: empty geometry after assembly")
    return geom


def area_km2(geom) -> float:
    if geom.is_empty:
        return 0.0
    parts = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
    return sum(abs(GEOD.geometry_area_perimeter(p)[0]) for p in parts) / 1e6


def largest_part(geom):
    if geom.geom_type != "MultiPolygon":
        return geom
    return max(geom.geoms, key=lambda g: g.area)


def clean(geom):
    """buffer(0) repair, dropping anything that is not areal."""
    if geom.is_empty:
        return geom
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.geom_type not in ("Polygon", "MultiPolygon"):
        parts = [g for g in getattr(geom, "geoms", [])
                 if g.geom_type in ("Polygon", "MultiPolygon")]
        geom = unary_union(parts) if parts else Polygon()
    return geom


def main() -> int:
    failures: list[str] = []

    # --- existing layer (append-only) ---------------------------------------
    prov_geo = json.loads(PROV_GEO.read_text(encoding="utf-8"))
    prov_meta = json.loads(PROV_META.read_text(encoding="utf-8"))
    keep_feats = list(prov_geo["features"])
    keep_meta = list(prov_meta["provinces"])
    existing_ids = {f["properties"]["province_id"] for f in keep_feats}
    print(f"kept: {len(keep_feats)} existing provinces (untouched)")

    # --- 1930 country polygons ----------------------------------------------
    bnd = json.loads(BOUNDARIES.read_text(encoding="utf-8"))
    country_geom = {}
    for f in bnd["features"]:
        cc = f["properties"].get("country_code")
        if cc:
            country_geom[cc] = clean(shape(f["geometry"]))
    for cc in FRAME_NATIONS:
        if cc not in country_geom:
            failures.append(f"{cc}: no 1930 country polygon in {BOUNDARIES.name}")
    if failures:
        for f_ in failures:
            print(f"  FAIL  {f_}")
        return 1

    # --- Natural Earth admin-1 ----------------------------------------------
    if not NE_STATES.exists():
        print(f"  FAIL  Natural Earth admin-1 not cached at {NE_STATES}")
        return 1
    ne = gpd.read_file(NE_STATES)

    # Natural Earth carries a country's city-level units (Hungarian "Urban
    # county", Romanian/Croatian "City", Latvian "Republican City") as SEPARATE
    # admin-1 rows PUNCHED OUT of the surrounding county. Union the counties
    # alone and every county seat falls in a hole — Kecskemét, Miskolc, Debrecen
    # and thirteen more Hungarian capitals land outside their own province, and
    # assign_admin_tiers would then never designate them. Each such unit is
    # absorbed into the primary unit it shares the longest boundary with.
    NE_PRIMARY_TYPES = {
        "HUN": ("County",), "ROU": ("County",), "LTU": ("County",),
        "SWE": ("County",), "HRV": ("County",), "LVA": ("Municipality",),
        "UKR": ("Region",),
    }
    _resolved: dict[str, dict] = {}

    def ne_resolved(iso: str) -> dict:
        """{admin-1 name -> geometry}, with city-level units absorbed."""
        if iso in _resolved:
            return _resolved[iso]
        sel = ne[ne["adm0_a3"] == iso]
        primary_types = NE_PRIMARY_TYPES.get(iso)
        if primary_types is None:
            out = {"*": clean(unary_union(list(sel.geometry.values)))}
            _resolved[iso] = out
            return out
        prim = sel[sel["type_en"].isin(primary_types)]
        out = {}
        for nm in sorted(set(prim["name"].astype(str))):
            out[nm] = clean(unary_union(
                list(prim[prim["name"].astype(str) == nm].geometry.values)))
        absorbed = 0
        for _, row in sel[~sel["type_en"].isin(primary_types)].iterrows():
            g = clean(row.geometry)
            if g.is_empty:
                continue
            # Longest shared boundary wins; a detached unit falls back to the
            # nearest primary. Ties are impossible in practice and would be
            # broken by name order, which is deterministic.
            best, best_len = None, -1.0
            for nm, pg in out.items():
                shared = g.boundary.intersection(pg.boundary).length
                if shared > best_len:
                    best, best_len = nm, shared
            if best_len <= 0:
                best = min(out, key=lambda nm: out[nm].distance(g))
            out[best] = clean(unary_union([out[best], g]))
            absorbed += 1
        if absorbed:
            print(f"  NE {iso}: absorbed {absorbed} city-level unit(s) into "
                  f"their surrounding {primary_types[0].lower()}")
        _resolved[iso] = out
        return out

    def ne_union(iso: str, names: list[str] | None, label: str):
        res = ne_resolved(iso)
        if not res:
            failures.append(f"{label}: NE has no adm0_a3=={iso}")
            return None
        if names is None:
            return clean(unary_union(list(res.values())))
        missing = [n for n in names if n not in res]
        if missing:
            failures.append(f"{label}: NE {iso} has no admin-1 named "
                            f"{missing} (check spelling against the "
                            f"shapefile, not the modern orthography)")
            return None
        return clean(unary_union([res[n] for n in names]))

    geoms: dict[str, object] = {}
    prov_defs: dict[str, tuple] = {}   # pid -> (country, name, cap, subs, note, src)

    # --- Denmark: OHM amter --------------------------------------------------
    # Two things need care here and nowhere else on the map.
    #
    # (1) OHM admin relations are LAND-only, but the 1930 country polygons
    #     include territorial waters (DNK: 64,102 km² polygon over 42,683 km²
    #     of land). Deriving "country minus the amter" therefore yields one
    #     20,900 km² blob of Kattegat with Sønderjylland and Sorø attached as
    #     lobes — which, assigned to a province, would silently steal every
    #     land hex in it. Everything below is intersected with Natural Earth
    #     land first.
    # (2) Sorø Amt's outer ways do not close in OHM (17 open lines of 20), so
    #     it cannot be polygonised directly. It is recovered the same way
    #     Sønderjylland is: as leftover land, disambiguated by the OHM
    #     relation's own reported bounds. Any unclosed amt is handled this way
    #     — the code does not hard-code Sorø.
    if not NE_LAND.exists():
        print(f"  FAIL  Natural Earth land not cached at {NE_LAND}")
        return 1
    land = unary_union(list(gpd.read_file(
        NE_LAND, bbox=(7.5, 54.0, 15.8, 58.2)).geometry.values))
    dnk_land = clean(country_geom["DNK"].intersection(clean(land)))

    amt_geom, amt_unclosed = {}, {}
    for name, rel in DNK_AMTER.items():
        d = overpass(f"[out:json][timeout:300];relation({rel});out geom;",
                     f"frames_DNK_{rel}")
        relel = next(e for e in d["elements"] if e["type"] == "relation")
        tags = relel.get("tags", {})
        start, end = tags.get("start_date", ""), tags.get("end_date", "9999")
        if not start or not (start <= SCENARIO_DATE < end):
            failures.append(f"DNK amt {name}: relation {rel} validity "
                            f"[{start},{end}) misses {SCENARIO_DATE}")
            continue
        try:
            # fill_holes: Københavns Amt is a ring around Staden København,
            # which was administratively separate in 1930. The hole is merged
            # in rather than authored as a city-province — the AD-035 addendum
            # rule that put Berlin back into Brandenburg and Wien into NÖ.
            amt_geom[name] = clean(assemble(d["elements"], f"DNK/{name}",
                                            fill_holes=True))
        except RuntimeError as ex:
            b = relel.get("bounds")
            if not b:
                failures.append(f"DNK amt {name}: {ex} and no relation bounds "
                                f"to recover it from")
                continue
            amt_unclosed[name] = box(b["minlon"], b["minlat"],
                                     b["maxlon"], b["maxlat"])
            print(f"  DNK: {name} does not close in OHM ({ex}) — recovering "
                  f"from leftover land inside the relation bounds")
    print(f"  DNK: {len(amt_geom)} amter assembled, "
          f"{len(amt_unclosed)} recovered from leftover")

    if len(amt_geom) + len(amt_unclosed) == len(DNK_AMTER):
        # Sønderjylland (the four 1920 amter, absent from OHM) and any
        # unclosed amt both live in the land the assembled amter do not cover.
        SONDERJYLLAND_BOX = box(8.0, 54.5, 10.3, 55.6)
        leftover = clean(dnk_land.difference(unary_union(list(amt_geom.values()))))
        parts = (list(leftover.geoms) if leftover.geom_type == "MultiPolygon"
                 else [leftover])
        recovered: dict[str, list] = {n: [] for n in amt_unclosed}
        absorbed: dict[str, list] = {}
        sonder_parts, absorbed_km2 = [], 0.0
        for p in parts:
            rp = p.representative_point()
            hit = next((n for n, bx in amt_unclosed.items() if bx.contains(rp)), None)
            if hit and area_km2(p) >= 100.0:
                recovered[hit].append(p)
            elif SONDERJYLLAND_BOX.contains(rp) and area_km2(p) >= 100.0:
                sonder_parts.append(p)
            else:
                # Everything else is land the amt polygons do not reach: the
                # coastal fringe where OHM's amt outlines and NE's coastline
                # disagree, and the towns that were their own administrative
                # unit in 1930 — Staden København (57 km²) and Amager (31 km²)
                # sit OUTSIDE Københavns Amt, which is why filling the
                # relation's inner rings does not recover them. Absorb each
                # into the nearest amt so Danish land coverage is total.
                near = min(amt_geom, key=lambda n: amt_geom[n].distance(rp))
                absorbed.setdefault(near, []).append(p)
                absorbed_km2 += area_km2(p)
        for n, ps in recovered.items():
            if not ps:
                failures.append(f"DNK amt {n}: nothing recovered from leftover")
            else:
                amt_geom[n] = clean(unary_union(ps))
                print(f"  DNK: {n} recovered, {area_km2(amt_geom[n]):,.0f} km²")
        for n, ps in absorbed.items():
            amt_geom[n] = clean(unary_union([amt_geom[n]] + ps))
        sonder = clean(unary_union(sonder_parts)) if sonder_parts else Polygon()
        if sonder.is_empty or not sonder.contains(Point(9.49, 55.25)):
            failures.append("DNK_SYDJYLLAND: derived Sønderjylland does not "
                            "contain Haderslev (9.49, 55.25)")
        else:
            print(f"  DNK: Sønderjylland derived, {area_km2(sonder):,.0f} km² "
                  f"(the four 1920 amter are ~3,900 km²)")
        print(f"  DNK: {absorbed_km2:,.0f} km² of fringe and separately-"
              f"administered town land absorbed into the nearest amt")

        for pid, (amter, name, capital, subs) in DNK_GROUPS.items():
            parts = [amt_geom[a] for a in amter if a in amt_geom]
            if pid == "DNK_SYDJYLLAND" and not sonder.is_empty:
                parts.append(sonder)
            geoms[pid] = clean(unary_union(parts))
            note = ("OpenHistoricalMap amt relations (CC0), 1930-valid, "
                    "grouped into landsdele")
            if pid == "DNK_SYDJYLLAND":
                note += ("; Sønderjylland (the four 1920 amter, absent from "
                         "OHM) derived as land minus the assembled amter")
            if any(a in amt_unclosed for a in amter):
                note += ("; " + ", ".join(a for a in amter if a in amt_unclosed)
                         + " recovered from leftover land (unclosed OHM ways)")
            prov_defs[pid] = ("DNK", name, capital, subs, note, "ohm")

    # --- Lithuania: Memelland from OHM --------------------------------------
    memel_parts = []
    for name, rel in LTU_MEMEL_KREISE.items():
        d = overpass(f"[out:json][timeout:300];relation({rel});out geom;",
                     f"frames_LTU_{rel}")
        relel = next(e for e in d["elements"] if e["type"] == "relation")
        tags = relel.get("tags", {})
        start, end = tags.get("start_date", ""), tags.get("end_date", "9999")
        if not start or not (start <= SCENARIO_DATE < end):
            failures.append(f"LTU Memel {name}: relation {rel} validity "
                            f"[{start},{end}) misses {SCENARIO_DATE}")
            continue
        memel_parts.append(clean(assemble(d["elements"], f"LTU/{name}")))
    if memel_parts:
        geoms["LTU_KLAIPEDA"] = clean(unary_union(memel_parts))
        prov_defs["LTU_KLAIPEDA"] = (
            "LTU", "Klaipėdos kraštas",
            cap("Klaipėda", 21.14, 55.71, ("Memel",)),
            [sub("Šilutė", "Nemunas delta market town", ("Heydekrug",)),
             sub("Pagėgiai", "border rail crossing", ("Pogegen",))],
            "OpenHistoricalMap Kreis relations Memel / Heydekrug / Pogegen "
            "(CC0), 1930-valid — the autonomous Memel Territory", "ohm")
        print(f"  LTU: Memelland from {len(memel_parts)} OHM Kreise, "
              f"{area_km2(geoms['LTU_KLAIPEDA']):,.0f} km²")

    # --- everything else: NE unions -----------------------------------------
    ne_note = ("Natural Earth admin-1 union (public domain), MODERN internal "
               f"lines clipped to the 1930 country polygon — 1930 stopgap per "
               f"AD-027; extracted {date.today().isoformat()}")
    for pid, (iso, names, name, capital, subs, note) in NE_GROUPS.items():
        ccode = pid.split("_")[0]
        g = ne_union(iso, names, pid)
        if g is None:
            continue
        geoms[pid] = g
        prov_defs[pid] = (ccode, name, capital, subs, f"{ne_note}; {note}", "ne")

    # Žemaitija must not swallow the Memel Territory (modern Klaipėdos
    # apskritis contains Šilutė, which WAS Memelland, and Kretinga, which was
    # not) — subtract the OHM Kreise.
    if "LTU_ZEMAITIJA" in geoms and "LTU_KLAIPEDA" in geoms:
        geoms["LTU_ZEMAITIJA"] = clean(
            geoms["LTU_ZEMAITIJA"].difference(geoms["LTU_KLAIPEDA"]))

    # Bucovina takes northern Bucovina from UKR Chernivtsi; Basarabia then
    # yields to it where modern Chernivtsi oblast reaches into Bessarabia.
    if "ROU_BUCOVINA" in geoms:
        cher = ne_union("UKR", ["Chernivtsi"], "ROU_BUCOVINA/UKR")
        if cher is not None:
            geoms["ROU_BUCOVINA"] = clean(
                unary_union([geoms["ROU_BUCOVINA"], cher]))
    if "ROU_BASARABIA" in geoms:
        # The Budjak (southern Bessarabia, Romanian in 1930) is Ukrainian
        # today, so modern MDA stops short of the Danube and the Black Sea.
        budjak = ne_union("UKR", ["Odessa"], "ROU_BASARABIA/UKR")
        if budjak is not None:
            geoms["ROU_BASARABIA"] = clean(
                unary_union([geoms["ROU_BASARABIA"], budjak]))
    if "ROU_BASARABIA" in geoms and "ROU_BUCOVINA" in geoms:
        geoms["ROU_BASARABIA"] = clean(
            geoms["ROU_BASARABIA"].difference(geoms["ROU_BUCOVINA"]))

    if failures:
        print(f"\nABORT — {len(failures)} self-check failures:")
        for f_ in failures:
            print(f"  FAIL  {f_}")
        return 1

    # --- clip, self-check, emit ---------------------------------------------
    new_feats: list[dict] = []
    new_meta: list[dict] = []
    by_country: dict[str, list] = {}

    for pid in sorted(prov_defs):
        ccode, name, capital, subs, note, src = prov_defs[pid]
        if pid in existing_ids:
            failures.append(f"{pid}: already present in {PROV_GEO.name} — "
                            f"this tool is append-only")
            continue
        geom = clean(geoms[pid].intersection(country_geom[ccode]))
        if geom.is_empty:
            failures.append(f"{pid}: empty after clipping to {ccode}")
            continue
        # Capital anchor check. A strict contains() is the goal, but a
        # SEAPORT capital sits on a generalised coastline: Natural Earth 10m
        # and the OHM outlines both cut a few hundred metres inland of the
        # real quay, so København, Karlskrona and Split test as "outside"
        # their own province. COASTAL_TOL is the same order as the 0.2°
        # coastal snap assign_province() already applies to hex centres, and
        # every use of it is printed so it can never hide a real misplacement.
        COASTAL_TOL = 0.05   # degrees, ~3-5 km at these latitudes
        pt = Point(*capital["at"])
        if not geom.contains(pt):
            d = geom.distance(pt)
            if d <= COASTAL_TOL:
                print(f"  {pid}: capital {capital['city_name']} is {d:.3f}° "
                      f"outside the province outline — within the coastline "
                      f"tolerance, accepted")
            else:
                failures.append(f"{pid}: capital {capital['city_name']} "
                                f"{capital['at']} is {d:.3f}° outside the "
                                f"assembled province (tolerance "
                                f"{COASTAL_TOL}°)")
        if (not country_geom[ccode].contains(pt)
                and country_geom[ccode].distance(pt) > COASTAL_TOL):
            failures.append(f"{pid}: capital {capital['city_name']} "
                            f"{capital['at']} not inside 1930 {ccode}")
        by_country.setdefault(ccode, []).append((pid, geom))
        new_feats.append({
            "type": "Feature",
            "properties": {"province_id": pid, "name": name, "country": ccode,
                           "era": "1930" if src == "ohm" else "1930-stopgap",
                           "notes": note},
            "geometry": mapping(geom)})
        new_meta.append({
            "province_id": pid, "name": name, "country": ccode,
            "capital": {k: v for k, v in capital.items() if k != "at"},
            "sub_capitals": subs})

    # No two provinces of the same country may overlap: a hex would then get
    # whichever the sindex happened to return first.
    for ccode, items in sorted(by_country.items()):
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                inter = items[i][1].intersection(items[j][1])
                if not inter.is_empty and area_km2(inter) > 50.0:
                    failures.append(
                        f"{items[i][0]} and {items[j][0]} overlap by "
                        f"{area_km2(inter):,.0f} km²")

    # Coverage of the country polygon. Frame-scoped nations are authored for
    # the framed block only, so their floor is informational — validate_full_
    # bbox.py's 98%-of-land-hexes gate is the real check for those.
    # The denominator is country ∩ LAND, not the country polygon: the 1930
    # polygons carry territorial waters (Denmark's is 64,102 km² over 42,683 km²
    # of land), and measuring against the wet figure would read a complete
    # province set as 73% coverage.
    FRAME_SCOPED = {"LVA", "SWE", "YUG", "SOV"}
    all_land = clean(unary_union(list(gpd.read_file(
        NE_LAND, bbox=(5.0, 40.0, 31.0, 58.5)).geometry.values)))
    for ccode, items in sorted(by_country.items()):
        denom = area_km2(clean(country_geom[ccode].intersection(all_land)))
        cov = (area_km2(clean(unary_union([g for _, g in items])
                              .intersection(all_land))) / max(denom, 1.0))
        tag = " [frame-scoped]" if ccode in FRAME_SCOPED else ""
        print(f"  {ccode}: {len(items)} provinces, coverage "
              f"{cov * 100:.1f}% of 1930 {ccode} land{tag}")
        if ccode not in FRAME_SCOPED and cov < 0.95:
            failures.append(f"{ccode}: province union covers only "
                            f"{cov * 100:.1f}% of the country's land (< 95%)")

    if failures:
        print(f"\nABORT — {len(failures)} self-check failures:")
        for f_ in failures:
            print(f"  FAIL  {f_}")
        return 1

    # --- write ---------------------------------------------------------------
    out_geo = {"type": "FeatureCollection",
               "name": "para_bellum_provinces_1930",
               "crs": prov_geo.get("crs"),
               "features": keep_feats + new_feats}
    PROV_GEO.write_text(json.dumps(out_geo, ensure_ascii=False), encoding="utf-8")
    out_meta = {
        "version": "0.3-passA-frames",
        "era": "1930",
        "source_note": (
            prov_meta.get("source_note", "") +
            " | Pass A (AD-037): DNK/HUN/LTU/LVA/ROU/SOV/SWE/YUG added. DNK "
            "from OpenHistoricalMap amt relations grouped into landsdele; "
            "LTU_KLAIPEDA from the OHM Memelland Kreise; the rest from Natural "
            "Earth admin-1 unions clipped to the 1930 country polygons (modern "
            "internal lines, 1930 external lines — AD-027 stopgap). HUN is "
            "authored at the COUNTY tier by Matthew's direct direction "
            "(2026-08-26, AD-037); every other frame "
            "nation is grouped to the density band the shipped map uses. LVA, "
            "SWE, YUG and SOV are FRAME-SCOPED: only the block inside the "
            "east-expansion bbox is authored."),
        "provinces": keep_meta + new_meta,
    }
    PROV_META.write_text(json.dumps(out_meta, ensure_ascii=False, indent=1),
                         encoding="utf-8")

    n_subs = sum(len(p["sub_capitals"]) for p in new_meta)
    print(f"\nwrote {len(keep_feats) + len(new_feats)} provinces "
          f"({len(keep_feats)} kept + {len(new_feats)} new), "
          f"{len(new_meta)} new capitals, {n_subs} new sub-capitals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
