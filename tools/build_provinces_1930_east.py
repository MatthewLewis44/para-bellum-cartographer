"""Rebuild the 1930 province layer for the eastward expansion (AD-035).

Extends data/boundaries/provinces_1930.geojson + provinces_1930_metadata.json:

  * KEEPS the authored BEL / NLD / FRA / LUX provinces untouched (AD-027).
  * REPLACES the German provinces with the REAL 1930 boundaries from
    OpenHistoricalMap admin_level=4 relations (CC0, AD-035) — retiring the
    AD-027 meridian/parallel cut-line approximations. Existing PM-curated
    capital metadata carries over where the province persists
    (Rheinland, Westfalen, Hannover, Hesse-Nassau, Hesse-Darmstadt).
  * ADDS the full eastern German set (Ostpreußen, Pommern, Brandenburg,
    Berlin from the Brandenburg hole, Nieder-/Oberschlesien, Grenzmark
    Posen-Westpreußen, ...), the 16 Polish voivodeships (1930-01-01
    versions), the Czechoslovak lands (Slovakia derived as country minus
    the other three), the 9 Austrian Bundesländer, Danzig, and the Saar
    (SAA_SAAR, geometry = the derived SAA country polygon).
  * Capitals + sub-capitals hand-curated per AD-023 (industrial / rail /
    strategic weight). `match_names` aliases carry the modern OSM node
    names for places renamed since 1930 (Königsberg → Калининград).

Every province polygon is clipped to its country polygon so mixed-source
border jitter can never put a hex's province in a different country than
its country_at_start.

Fail-loud self-checks (AD-030): relation dates cover 1930-01-01; every
capital coordinate falls inside its assembled province AND the right
country; per-country province-union coverage. Aborts without writing on
any failure.

Usage: uv run python tools/build_provinces_1930_east.py
       (run AFTER tools/build_boundaries_1930_east.py)
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import requests
from pyproj import Geod
from shapely.geometry import (
    LineString, MultiLineString, Point, mapping, shape,
)
from shapely.ops import linemerge, polygonize, unary_union

ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = ROOT / "data" / "boundaries" / "boundaries_1930.geojson"
PROV_GEO = ROOT / "data" / "boundaries" / "provinces_1930.geojson"
PROV_META = ROOT / "data" / "boundaries" / "provinces_1930_metadata.json"
CACHE = Path.home() / "wargame-cartographer" / "cache" / "boundaries" / "ohm_1930"
OVERPASS = "https://overpass-api.openhistoricalmap.org/api/interpreter"
SCENARIO_DATE = "1930-01-01"
GEOD = Geod(ellps="WGS84")

KEEP_COUNTRIES = ("BEL", "NLD", "FRA", "LUX")
# German provinces whose PM-curated Sprint 5 metadata carries over.
CARRY_META = {
    "DEU_RHEINLAND": 2690378,       # Rheinprovinz
    "DEU_WESTFALEN": 2690428,       # Westfalen
    "DEU_HANNOVER": 2691489,        # Hannover
    "DEU_HESSEN_NASSAU": 2690499,    # Provinz Hessen-Nassau
    "DEU_HESSEN_DARMSTADT": 2690433, # Hessen (People's State)
}


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


# province_id: (ohm_rel_id | None, display name, capital, [sub_capitals])
# rel None => geometry supplied specially (hole / derived / country polygon).
PROVINCES: dict[str, dict[str, tuple]] = {
    "DEU": {
        "DEU_OSTPREUSSEN": (2691479, "Ostpreußen",
            cap("Königsberg", 20.51, 54.71, ("Калининград", "Kaliningrad")),
            [sub("Allenstein", "rail hub of Masuria", ("Olsztyn",)),
             sub("Tilsit", "Memel-border industry", ("Советск", "Sovetsk")),
             sub("Insterburg", "rail junction", ("Черняховск", "Chernyakhovsk"))]),
        "DEU_POMMERN": (2694035, "Pommern",
            cap("Stettin", 14.55, 53.43, ("Szczecin",)),
            [sub("Stolp", "eastern rail centre", ("Słupsk",)),
             sub("Stralsund", "Baltic port")]),
        "DEU_BRANDENBURG": (2806722, "Brandenburg",
            cap("Potsdam", 13.06, 52.40, (),
                "provincial seat sat in Charlottenburg (Berlin); Potsdam designated for the game"),
            [sub("Frankfurt an der Oder", "Oder crossing + rail", ("Frankfurt (Oder)",)),
             sub("Cottbus", "Lusatian industry")]),
        "DEU_BERLIN": (None, "Berlin (Reichshauptstadt)",
            cap("Berlin", 13.40, 52.52),
            []),
        "DEU_NIEDERSCHLESIEN": (2694765, "Niederschlesien",
            cap("Breslau", 17.03, 51.11, ("Wrocław",)),
            [sub("Liegnitz", "rail + garrison", ("Legnica",)),
             sub("Waldenburg", "coal basin", ("Wałbrzych",)),
             sub("Görlitz", "Neisse rail junction")]),
        "DEU_OBERSCHLESIEN": (2693608, "Oberschlesien",
            cap("Oppeln", 17.93, 50.67, ("Opole",)),
            [sub("Gleiwitz", "border industry triangle", ("Gliwice",)),
             sub("Beuthen", "coal + zinc", ("Bytom",)),
             sub("Ratibor", "Oder industry", ("Racibórz",))]),
        "DEU_GRENZMARK": (2694718, "Grenzmark Posen-Westpreußen",
            cap("Schneidemühl", 16.74, 53.15, ("Piła",)),
            []),
        "DEU_SACHSEN_PROVINZ": (2748641, "Provinz Sachsen",
            cap("Magdeburg", 11.63, 52.13),
            [sub("Halle", "Leuna/Merseburg chemicals", ("Halle (Saale)",)),
             sub("Erfurt", "exclave admin + rail")]),
        "DEU_SCHLESWIG_HOLSTEIN": (2856514, "Schleswig-Holstein",
            cap("Kiel", 10.14, 54.32),
            [sub("Flensburg", "border port"),
             sub("Neumünster", "rail junction")]),
        "DEU_BAYERN": (2661374, "Bayern",
            cap("München", 11.58, 48.14),
            [sub("Nürnberg", "rail + industry"),
             sub("Augsburg", "MAN works"),
             sub("Ludwigshafen", "BASF (Pfalz exclave)", ("Ludwigshafen am Rhein",))]),
        "DEU_WUERTTEMBERG": (2661270, "Württemberg",
            cap("Stuttgart", 9.18, 48.78),
            [sub("Ulm", "fortress + rail"),
             sub("Friedrichshafen", "Zeppelin/Dornier works")]),
        "DEU_BADEN": (2661248, "Baden",
            cap("Karlsruhe", 8.40, 49.01),
            [sub("Mannheim", "Rhine port + industry"),
             sub("Freiburg", "upper-Rhine centre", ("Freiburg im Breisgau",))]),
        "DEU_THUERINGEN": (2745091, "Thüringen",
            cap("Weimar", 11.33, 50.98),
            [sub("Jena", "Zeiss optics"),
             sub("Gera", "textile industry")]),
        "DEU_SACHSEN_FREISTAAT": (2856631, "Freistaat Sachsen",
            cap("Dresden", 13.74, 51.05),
            [sub("Leipzig", "rail hub + trade fair"),
             sub("Chemnitz", "machine tools"),
             sub("Zwickau", "coal + vehicles")]),
        "DEU_MECKLENBURG_SCHWERIN": (2694820, "Mecklenburg-Schwerin",
            cap("Schwerin", 11.42, 53.63),
            [sub("Rostock", "Baltic port + aviation")]),
        "DEU_MECKLENBURG_STRELITZ": (2694119, "Mecklenburg-Strelitz",
            cap("Neustrelitz", 13.07, 53.36), []),
        "DEU_OLDENBURG": (2691493, "Oldenburg",
            cap("Oldenburg", 8.21, 53.14), []),
        "DEU_BRAUNSCHWEIG": (2691487, "Braunschweig",
            cap("Braunschweig", 10.52, 52.26), []),
        "DEU_ANHALT": (2856580, "Anhalt",
            cap("Dessau", 12.24, 51.83, (), "Junkers works in the capital"), []),
        "DEU_LIPPE": (2690429, "Lippe",
            cap("Detmold", 8.88, 51.94), []),
        "DEU_SCHAUMBURG_LIPPE": (2690418, "Schaumburg-Lippe",
            cap("Bückeburg", 9.05, 52.26), []),
        "DEU_HAMBURG": (2693616, "Hamburg",
            cap("Hamburg", 10.00, 53.55), []),
        "DEU_LUEBECK": (2691970, "Lübeck",
            cap("Lübeck", 10.69, 53.87), []),
        "DEU_BREMEN": (2691491, "Bremen",
            cap("Bremen", 8.80, 53.08),
            [sub("Bremerhaven", "North Sea port (exclave)")]),
        "DEU_HOHENZOLLERN": (2894236, "Hohenzollerische Lande",
            cap("Sigmaringen", 9.22, 48.09), []),
        # geometry replaced; metadata carried over from Sprint 5 (CARRY_META)
        "DEU_RHEINLAND": (2690378, "Rheinprovinz", None, None),
        "DEU_WESTFALEN": (2690428, "Westfalen", None, None),
        "DEU_HANNOVER": (2691489, "Hannover", None, None),
        "DEU_HESSEN_NASSAU": (2690499, "Provinz Hessen-Nassau", None, None),
        "DEU_HESSEN_DARMSTADT": (2690433, "Hessen (Darmstadt)", None, None),
    },
    "POL": {
        "POL_WARSZAWSKIE": (2741469, "Województwo warszawskie",
            cap("Warszawa", 21.01, 52.23),
            [sub("Płock", "Vistula crossing"),
             sub("Włocławek", "river industry")]),
        "POL_LODZKIE": (2741475, "Województwo łódzkie",
            cap("Łódź", 19.46, 51.77),
            [sub("Piotrków", "rail junction", ("Piotrków Trybunalski",))]),
        "POL_KIELECKIE": (2741470, "Województwo kieleckie",
            cap("Kielce", 20.63, 50.87),
            [sub("Częstochowa", "steel + rail"),
             sub("Radom", "arms works"),
             sub("Sosnowiec", "Dąbrowa coal basin")]),
        "POL_LUBELSKIE": (2741463, "Województwo lubelskie",
            cap("Lublin", 22.57, 51.25),
            [sub("Chełm", "rail junction")]),
        "POL_BIALOSTOCKIE": (2741468, "Województwo białostockie",
            cap("Białystok", 23.16, 53.13),
            [sub("Grodno", "Neman garrison town", ("Гродна", "Гродно", "Hrodna", "Grodno")),
             sub("Łomża", "Narew garrison")]),
        "POL_POMORSKIE": (2741477, "Województwo pomorskie",
            cap("Toruń", 18.60, 53.01),
            [sub("Gdynia", "the Corridor port"),
             sub("Grudziądz", "garrison + industry")]),
        "POL_POZNANSKIE": (2741476, "Województwo poznańskie",
            cap("Poznań", 16.93, 52.41),
            [sub("Gniezno", "rail + regional centre")]),
        "POL_SLASKIE": (2741471, "Województwo śląskie",
            cap("Katowice", 19.02, 50.26),
            [sub("Chorzów", "Królewska Huta steel"),
             sub("Bielsko", "textile industry", ("Bielsko-Biała",))]),
        "POL_KRAKOWSKIE": (2741461, "Województwo krakowskie",
            cap("Kraków", 19.94, 50.06),
            [sub("Tarnów", "chemicals (Mościce)")]),
        "POL_LWOWSKIE": (2929591, "Województwo lwowskie",
            cap("Lwów", 24.03, 49.84, ("Львів", "Lviv")),
            [sub("Przemyśl", "fortress + rail"),
             sub("Borysław", "oil basin", ("Борислав", "Boryslav")),
             sub("Rzeszów", "rail + industry")]),
        "POL_STANISLAWOWSKIE": (2741464, "Województwo stanisławowskie",
            cap("Stanisławów", 24.71, 48.92, ("Івано-Франківськ", "Ivano-Frankivsk")),
            [sub("Kołomyja", "rail junction", ("Коломия", "Kolomyia"))]),
        "POL_TARNOPOLSKIE": (2929590, "Województwo tarnopolskie",
            cap("Tarnopol", 25.60, 49.55, ("Тернопіль", "Ternopil")), []),
        "POL_WOLYNSKIE": (2698168, "Województwo wołyńskie",
            cap("Łuck", 25.32, 50.75, ("Луцьк", "Lutsk")),
            [sub("Równe", "rail junction", ("Рівне", "Rivne"))]),
        "POL_POLESKIE": (2698170, "Województwo poleskie",
            cap("Brześć nad Bugiem", 23.65, 52.10, ("Брэст", "Брест", "Brest")),
            [sub("Pińsk", "Pripyat flotilla base", ("Пінск", "Пинск", "Pinsk"))]),
        "POL_NOWOGRODZKIE": (2741466, "Województwo nowogródzkie",
            cap("Nowogródek", 25.83, 53.60, ("Навагрудак", "Novogrudok", "Navahrudak")),
            [sub("Baranowicze", "rail junction", ("Баранавічы", "Барановичи", "Baranavichy"))]),
        "POL_WILENSKIE": (2696109, "Województwo wileńskie",
            cap("Wilno", 25.28, 54.69, ("Vilnius",)), []),
    },
    "CSK": {
        "CSK_CESKA": (2856853, "Země Česká",
            cap("Praha", 14.42, 50.09),
            [sub("Plzeň", "Škoda works"),
             sub("Kladno", "steel + coal"),
             sub("Liberec", "Sudeten industry"),
             sub("Ústí nad Labem", "Elbe port + chemicals")]),
        "CSK_MORAVSKOSLEZSKA": (2857449, "Země Moravskoslezská",
            cap("Brno", 16.61, 49.19),
            [sub("Moravská Ostrava", "Vítkovice steel + coal", ("Ostrava",)),
             sub("Olomouc", "rail junction"),
             sub("Zlín", "Baťa industry")]),
        "CSK_SLOVENSKO": (None, "Slovenská krajina",
            cap("Bratislava", 17.11, 48.14),
            [sub("Košice", "eastern rail + industry"),
             sub("Žilina", "rail junction")]),
        "CSK_RUS": (2857483, "Podkarpatská Rus",
            cap("Užhorod", 22.30, 48.62, ("Ужгород", "Uzhhorod")),
            [sub("Mukačevo", "garrison + rail", ("Мукачево", "Mukachevo"))]),
    },
    "AUT": {
        "AUT_WIEN": (None, "Wien",
            cap("Wien", 16.37, 48.21), []),
        "AUT_NIEDEROESTERREICH": (2684077, "Niederösterreich",
            cap("Sankt Pölten", 15.62, 48.20, ("St. Pölten",),
                "governed from Vienna in 1930; St. Pölten designated for the game"),
            [sub("Wiener Neustadt", "industry + rail")]),
        "AUT_OBEROESTERREICH": (2928473, "Oberösterreich",
            cap("Linz", 14.29, 48.31),
            [sub("Steyr", "arms works")]),
        "AUT_SALZBURG": (2852310, "Salzburg",
            cap("Salzburg", 13.05, 47.81), []),
        "AUT_STEIERMARK": (2742908, "Steiermark",
            cap("Graz", 15.44, 47.07),
            [sub("Leoben", "Erzberg iron")]),
        "AUT_KAERNTEN": (2746370, "Kärnten",
            cap("Klagenfurt", 14.31, 46.62, ("Klagenfurt am Wörthersee",)),
            [sub("Villach", "rail junction")]),
        "AUT_TIROL": (2746440, "Tirol",
            cap("Innsbruck", 11.39, 47.27), []),
        "AUT_VORARLBERG": (2684073, "Vorarlberg",
            cap("Bregenz", 9.75, 47.51), []),
        "AUT_BURGENLAND": (2747803, "Burgenland",
            cap("Eisenstadt", 16.52, 47.85), []),
    },
    "SAA": {
        "SAA_SAAR": (None, "Saargebiet",
            cap("Saarbrücken", 7.00, 49.23),
            [sub("Neunkirchen", "steel works"),
             sub("Völklingen", "ironworks")]),
    },
    "DZG": {
        "DZG_DANZIG": (None, "Freie Stadt Danzig",
            cap("Danzig", 18.65, 54.35, ("Gdańsk",)), []),
    },
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


def assemble(elements: list, label: str, want_holes: bool = False):
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
    geom = unary_union(list(polygonize(linemerge(MultiLineString(outers)))))
    holes = None
    if inners:
        hp = list(polygonize(linemerge(MultiLineString(inners))))
        if hp:
            holes = unary_union(hp)
            geom = geom.difference(holes)
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        raise RuntimeError(f"{label}: empty geometry after assembly")
    return (geom, holes) if want_holes else geom


def area_km2(geom) -> float:
    return abs(GEOD.geometry_area_perimeter(geom)[0]) / 1e6


def main() -> int:
    boundaries = json.loads(BOUNDARIES.read_text(encoding="utf-8"))
    country_geom = {f["properties"]["country_code"]: shape(f["geometry"])
                    for f in boundaries["features"]}
    for need in PROVINCES:
        if need not in country_geom:
            raise RuntimeError(
                f"country {need} missing from boundaries_1930.geojson — run "
                f"tools/build_boundaries_1930_east.py first")

    prov_geo = json.loads(PROV_GEO.read_text(encoding="utf-8"))
    prov_meta = json.loads(PROV_META.read_text(encoding="utf-8"))
    keep_feats = [f for f in prov_geo["features"]
                  if f["properties"]["province_id"].split("_")[0] in KEEP_COUNTRIES]
    keep_meta = [p for p in prov_meta["provinces"]
                 if p["province_id"].split("_")[0] in KEEP_COUNTRIES]
    carry = {p["province_id"]: p for p in prov_meta["provinces"]
             if p["province_id"] in CARRY_META}
    missing_carry = [pid for pid in CARRY_META if pid not in carry]
    if missing_carry:
        raise RuntimeError(f"expected Sprint 5 metadata to carry over for "
                           f"{missing_carry} — not found")

    # Rheinland capital adjustment (Sprint 6, flagged for PM review): the real
    # 1930 Rheinprovinz/Hessen-Nassau line runs along the lower Lahn ~5 km
    # south of Koblenz, and the Koblenz hex CENTER falls ~500 m across it —
    # the AD-027 Arlon-class quantization, which would leave the Rhineland
    # (a core province) with no capital hex and break AD-023 capture
    # semantics. Game designation: Köln becomes the capital; Koblenz stays a
    # sub-capital (historical seat, noted).
    rh = dict(carry["DEU_RHEINLAND"])
    if rh.get("capital", {}).get("city_name") == "Koblenz":
        rh["capital"] = {"city_name": "Köln",
                         "note": ("1930 seat was Koblenz, whose hex center "
                                  "quantizes into Hessen-Nassau (AD-027 "
                                  "border-quantization); Köln designated")}
        subs = [s for s in rh.get("sub_capitals", [])
                if s.get("city_name") != "Köln"]
        rh["sub_capitals"] = ([{"city_name": "Koblenz",
                                "rationale": "historical provincial seat"}]
                              + subs)
        carry["DEU_RHEINLAND"] = rh
        print("  DEU_RHEINLAND: capital Koblenz -> Köln (hex-center "
              "quantization; Koblenz kept as sub-capital)")
    print(f"kept: {len(keep_feats)} western provinces; carrying over metadata "
          f"for {sorted(carry)}")

    ohm_note = ("OpenHistoricalMap admin_level=4 (CC0), extracted "
                f"{date.today().isoformat()} (AD-035)")
    new_feats: list[dict] = []
    new_meta: list[dict] = []
    failures: list[str] = []

    # --- pass 1: fetch + assemble all relation-backed provinces -------------
    geoms: dict[str, object] = {}
    brandenburg_holes = None
    noe_holes = None
    for ccode, provs in PROVINCES.items():
        for pid, (rel, name, *_rest) in provs.items():
            if rel is None:
                continue
            d = overpass(f"[out:json][timeout:300];relation({rel});out geom;",
                         f"prov_{pid}_{rel}")
            relel = next(e for e in d["elements"] if e["type"] == "relation")
            tags = relel.get("tags", {})
            start = tags.get("start_date", "")
            end = tags.get("end_date", "9999")
            if not (start <= SCENARIO_DATE < end):
                failures.append(f"{pid}: relation {rel} validity [{start},{end}) "
                                f"misses {SCENARIO_DATE}")
                continue
            want_holes = pid in ("DEU_BRANDENBURG", "AUT_NIEDEROESTERREICH")
            out = assemble(d["elements"], pid, want_holes=want_holes)
            if want_holes:
                geom, holes = out
                if pid == "DEU_BRANDENBURG":
                    brandenburg_holes = holes
                else:
                    noe_holes = holes
            else:
                geom = out
            geoms[pid] = geom

    # --- special geometries --------------------------------------------------
    # Berlin: the hole in Brandenburg that contains the Berlin point.
    berlin_pt = Point(13.40, 52.52)
    if brandenburg_holes is None:
        failures.append("DEU_BERLIN: Brandenburg relation has no holes")
    else:
        parts = (list(brandenburg_holes.geoms)
                 if brandenburg_holes.geom_type == "MultiPolygon"
                 else [brandenburg_holes])
        hit = [p for p in parts if p.contains(berlin_pt)]
        if not hit:
            failures.append("DEU_BERLIN: no Brandenburg hole contains Berlin")
        else:
            geoms["DEU_BERLIN"] = hit[0]

    # Wien: if the NÖ relation has a hole containing Vienna, that hole is the
    # Bundesland Wien; otherwise fall back to merging Wien into NÖ (AD-027
    # Brabant precedent) — flagged loudly either way.
    wien_pt = Point(16.37, 48.21)
    wien_from_hole = None
    if noe_holes is not None:
        parts = (list(noe_holes.geoms)
                 if noe_holes.geom_type == "MultiPolygon" else [noe_holes])
        hit = [p for p in parts if p.contains(wien_pt)]
        if hit:
            wien_from_hole = hit[0]
    if wien_from_hole is not None:
        geoms["AUT_WIEN"] = wien_from_hole
        print("  AUT_WIEN: authored from the Niederösterreich hole")
    else:
        del PROVINCES["AUT"]["AUT_WIEN"]
        # NÖ keeps Vienna inside; its capital becomes Wien.
        rel, name, _c, subs = PROVINCES["AUT"]["AUT_NIEDEROESTERREICH"]
        PROVINCES["AUT"]["AUT_NIEDEROESTERREICH"] = (
            rel, name, cap("Wien", 16.37, 48.21, (),
                           "NÖ merged with Wien (no OHM hole; AD-027 Brabant precedent)"),
            subs)
        print("  AUT_WIEN: NO hole found — merged into Niederösterreich")

    # Slovakia: CSK country minus the three other lands.
    sk = country_geom["CSK"]
    for other in ("CSK_CESKA", "CSK_MORAVSKOSLEZSKA", "CSK_RUS"):
        if other in geoms:
            sk = sk.difference(geoms[other])
    sk_parts = list(sk.geoms) if sk.geom_type == "MultiPolygon" else [sk]
    sk_main = max(sk_parts, key=lambda g: g.area)
    if not sk_main.contains(Point(17.11, 48.14)):
        failures.append("CSK_SLOVENSKO: derived polygon does not contain Bratislava")
    geoms["CSK_SLOVENSKO"] = sk_main

    geoms["SAA_SAAR"] = country_geom["SAA"]
    geoms["DZG_DANZIG"] = country_geom["DZG"]

    # --- pass 2: clip to country, self-check, emit ---------------------------
    for ccode, provs in PROVINCES.items():
        cg = country_geom[ccode]
        union_parts = []
        for pid, (rel, name, capital, subs) in provs.items():
            if pid not in geoms:
                continue
            geom = geoms[pid].intersection(cg)
            if geom.is_empty:
                failures.append(f"{pid}: empty after clipping to {ccode}")
                continue
            meta = carry.get(pid)
            if meta is None:
                meta = {"province_id": pid, "name": name, "country": ccode,
                        "capital": {k: v for k, v in capital.items() if k != "at"},
                        "sub_capitals": subs}
            else:
                meta = dict(meta)
                meta["country"] = ccode
            # capital-in-province check (skip carried-over: no coords stored)
            if capital is not None:
                pt = Point(*capital["at"])
                if not geom.contains(pt):
                    failures.append(f"{pid}: capital {capital['city_name']} "
                                    f"not inside the assembled province")
                if not cg.contains(pt):
                    failures.append(f"{pid}: capital {capital['city_name']} "
                                    f"not inside country {ccode}")
            union_parts.append(geom)
            new_feats.append({
                "type": "Feature",
                "properties": {"province_id": pid, "name": name,
                               "country": ccode, "era": "1930",
                               "notes": (f"{ohm_note}; rel {rel}" if rel
                                         else f"{ohm_note}; derived")},
                "geometry": mapping(geom)})
            new_meta.append(meta)

        cov = area_km2(unary_union(union_parts)) / area_km2(cg)
        print(f"  {ccode}: {len(union_parts)} provinces, "
              f"coverage {cov * 100:.1f}% of country polygon")
        # Country polygons include territorial waters; provinces of coastal
        # countries cover less of the polygon than of the land.
        floor = 0.90 if ccode in ("DEU", "DZG", "POL") else 0.93
        if cov < floor:
            failures.append(f"{ccode}: province union covers only "
                            f"{cov * 100:.1f}% (< {floor * 100:.0f}%)")

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
    PROV_GEO.write_text(json.dumps(out_geo, ensure_ascii=False),
                        encoding="utf-8")
    out_meta = {
        "version": "0.2-sprint6-east",
        "era": "1930",
        "source_note": (prov_meta.get("source_note", "") +
                        " | Sprint 6 (AD-035): DEU replaced with real 1930 "
                        "boundaries + POL/CSK/AUT/SAA/DZG added from "
                        "OpenHistoricalMap (CC0)."),
        "provinces": keep_meta + new_meta,
    }
    PROV_META.write_text(json.dumps(out_meta, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    n_caps = sum(1 for p in keep_meta + new_meta
                 if p.get("capital", {}).get("city_name"))
    n_subs = sum(len(p.get("sub_capitals", [])) for p in keep_meta + new_meta)
    print(f"\nwrote {len(keep_feats) + len(new_feats)} provinces "
          f"({len(keep_feats)} kept + {len(new_feats)} new), "
          f"{n_caps} capitals, {n_subs} sub-capitals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
