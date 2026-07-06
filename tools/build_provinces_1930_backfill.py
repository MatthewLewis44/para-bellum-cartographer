"""Backfill CHE / ITA / eastern-FRA provinces (Sprint 7 P0-A, AD-027 lineage).

Runs THIRD in the province chain:
    build_provinces_1930.py  ->  build_provinces_1930_east.py  ->  THIS TOOL

APPEND-ONLY on top of the east tool's output: adds provinces for the two
countries that were country-only (CHE, ITA — OHM 1930 admin coverage is
partial there, so this uses the Natural Earth admin-1 stopgap route like the
Sprint 5 western set) and the French départements framed by the wceurope /
east bboxes that Sprint 5 didn't author. Existing provinces are never
touched; already-present ids are skipped (idempotent).

1930 adjustments on the NE-modern units (era: "1930-stopgap", review pending):
  * CHE — canton Jura (split from Bern in 1979) merges back into Bern.
    25 cantons.
  * ITA — Valle d'Aosta (separated 1948) merges into Piemonte;
    Trentino-Alto Adige is renamed Venezia Tridentina (the 1930
    compartimento); Friuli-Venezia Giulia is SPLIT at 13.35°E (the pre-1918
    Austro-Italian border approximation): west joins Veneto (1930 Friuli/
    Udine belonged to Veneto), east becomes Venezia Giulia. Cut-line is
    approximate per the AD-027 precedent.
  * FRA — départements are 1930-stable; préfecture = capital.

Every polygon is clipped to its country polygon (mixed-source jitter guard,
same rule as the east tool). Fail-loud: an NE unit intersecting the frame
with no mapping entry aborts the build.

Usage: uv run python tools/build_provinces_1930_backfill.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from shapely.geometry import box, mapping, shape
from shapely.ops import unary_union

ROOT = Path(__file__).resolve().parents[1]
BOUNDARIES = ROOT / "data" / "boundaries" / "boundaries_1930.geojson"
PROV_GEO = ROOT / "data" / "boundaries" / "provinces_1930.geojson"
PROV_META = ROOT / "data" / "boundaries" / "provinces_1930_metadata.json"

# Union of the frames that need coverage (wceurope 5-15E/45-54N + east
# 5.8-26.9E/46.3-56N). NE units intersecting this and belonging to
# CHE/ITA/FRA must resolve to a province.
FRAMES = [box(5.0, 45.0, 15.0, 54.0), box(5.8, 46.3, 26.9, 56.0)]
FRAME = unary_union(FRAMES)

# --- Switzerland: canton name -> (province_id, display, capital, subs) ------
# Jura is handled by merge (below); Basel city/land are separate in 1930 too.
CHE_MAP = {
    "Zürich": ("CHE_ZUERICH", "Zürich", "Zürich",
               [("Winterthur", "machine industry")]),
    "Bern": ("CHE_BERN", "Bern", "Bern",
             [("Biel/Bienne", "watch industry + rail")]),
    "Luzern": ("CHE_LUZERN", "Luzern", "Luzern", []),
    "Lucerne": ("CHE_LUZERN", "Luzern", "Luzern", []),   # NE French spelling
    "Uri": ("CHE_URI", "Uri", "Altdorf", []),
    "Schwyz": ("CHE_SCHWYZ", "Schwyz", "Schwyz", []),
    "Obwalden": ("CHE_OBWALDEN", "Obwalden", "Sarnen", []),
    "Nidwalden": ("CHE_NIDWALDEN", "Nidwalden", "Stans", []),
    "Glarus": ("CHE_GLARUS", "Glarus", "Glarus", []),
    "Zug": ("CHE_ZUG", "Zug", "Zug", []),
    "Fribourg": ("CHE_FRIBOURG", "Fribourg", "Fribourg", []),
    "Solothurn": ("CHE_SOLOTHURN", "Solothurn", "Solothurn", []),
    "Basel-Stadt": ("CHE_BASEL_STADT", "Basel-Stadt", "Basel", []),
    "Basel-Landschaft": ("CHE_BASEL_LAND", "Basel-Landschaft", "Liestal", []),
    "Schaffhausen": ("CHE_SCHAFFHAUSEN", "Schaffhausen", "Schaffhausen", []),
    "Appenzell Ausserrhoden": ("CHE_APPENZELL_AR", "Appenzell Ausserrhoden",
                               "Herisau", []),
    "Appenzell Innerrhoden": ("CHE_APPENZELL_IR", "Appenzell Innerrhoden",
                              "Appenzell", []),
    "Sankt Gallen": ("CHE_ST_GALLEN", "St. Gallen", "Sankt Gallen", []),
    "Graubünden": ("CHE_GRAUBUENDEN", "Graubünden", "Chur", []),
    "Aargau": ("CHE_AARGAU", "Aargau", "Aarau", []),
    "Thurgau": ("CHE_THURGAU", "Thurgau", "Frauenfeld", []),
    "Ticino": ("CHE_TICINO", "Ticino", "Bellinzona",
               [("Lugano", "southern centre")]),
    "Vaud": ("CHE_VAUD", "Vaud", "Lausanne", []),
    "Valais": ("CHE_VALAIS", "Valais", "Sion", []),
    "Neuchâtel": ("CHE_NEUCHATEL", "Neuchâtel", "Neuchâtel",
                  [("La Chaux-de-Fonds", "watch industry")]),
    "Genève": ("CHE_GENEVE", "Genève", "Genève", []),
    # 1930 merge: Jura was part of Bern until 1979.
    "Jura": ("CHE_BERN", "Bern", None, None),
}

# --- Italy: region -> (province_id, display, capital, subs) -----------------
ITA_MAP = {
    "Piemonte": ("ITA_PIEMONTE", "Piemonte", "Torino", []),
    # 1930 merge: Aosta was part of Piemonte until 1948.
    "Valle d'Aosta": ("ITA_PIEMONTE", "Piemonte", None, None),
    "Lombardia": ("ITA_LOMBARDIA", "Lombardia", "Milano",
                  [("Brescia", "arms industry"), ("Bergamo", "industry")]),
    "Trentino-Alto Adige": ("ITA_VENEZIA_TRIDENTINA", "Venezia Tridentina",
                            "Trento", [("Bolzano", "Alto Adige centre")]),
    # Split at 13.35E in code: west -> Veneto, east -> Venezia Giulia.
    "Friuli-Venezia Giulia": ("__FVG_SPLIT__", "", None, None),
    "Veneto": ("ITA_VENETO", "Veneto", "Venezia",
               [("Verona", "rail junction"), ("Udine", "Friuli centre")]),
    "Emilia-Romagna": ("ITA_EMILIA", "Emilia", "Bologna", []),
    "Liguria": ("ITA_LIGURIA", "Liguria", "Genova",
                [("La Spezia", "naval base")]),
    "Toscana": ("ITA_TOSCANA", "Toscana", "Firenze", []),
}
FVG_CUT_LON = 13.35
ITA_VENEZIA_GIULIA = ("ITA_VENEZIA_GIULIA", "Venezia Giulia", "Trieste",
                      [("Gorizia", "Isonzo centre")])

# --- France: département -> (province_id, display, préfecture, subs) --------
# Only consulted for départements NOT already authored (Sprint 5 has 11).
FRA_MAP = {
    "Haut-Rhin": ("FRA_HAUT_RHIN", "Haut-Rhin", "Colmar",
                  [("Mulhouse", "textile + potash industry")]),
    "Vosges": ("FRA_VOSGES", "Vosges", "Épinal", []),
    "Aube": ("FRA_AUBE", "Aube", "Troyes", []),
    "Haute-Marne": ("FRA_HAUTE_MARNE", "Haute-Marne", "Chaumont", []),
    "Haute-Saône": ("FRA_HAUTE_SAONE", "Haute-Saône", "Vesoul", []),
    "Territoire de Belfort": ("FRA_BELFORT", "Territoire de Belfort",
                              "Belfort", []),
    "Doubs": ("FRA_DOUBS", "Doubs", "Besançon",
              [("Montbéliard", "Peugeot works")]),
    "Jura": ("FRA_JURA", "Jura", "Lons-le-Saunier", []),
    "Côte-d'Or": ("FRA_COTE_D_OR", "Côte-d'Or", "Dijon", []),
    "Saône-et-Loire": ("FRA_SAONE_ET_LOIRE", "Saône-et-Loire", "Mâcon",
                       [("Le Creusot", "Schneider arms works")]),
    "Ain": ("FRA_AIN", "Ain", "Bourg-en-Bresse", []),
    "Rhône": ("FRA_RHONE", "Rhône", "Lyon", []),
    "Loire": ("FRA_LOIRE", "Loire", "Saint-Étienne",
              [("Roanne", "arsenal")]),
    "Isère": ("FRA_ISERE", "Isère", "Grenoble", []),
    "Savoie": ("FRA_SAVOIE", "Savoie", "Chambéry", []),
    "Haute-Savoie": ("FRA_HAUTE_SAVOIE", "Haute-Savoie", "Annecy", []),
    "Drôme": ("FRA_DROME", "Drôme", "Valence", []),
    "Ardèche": ("FRA_ARDECHE", "Ardèche", "Privas", []),
    "Yonne": ("FRA_YONNE", "Yonne", "Auxerre", []),
    "Nièvre": ("FRA_NIEVRE", "Nièvre", "Nevers", []),
    "Allier": ("FRA_ALLIER", "Allier", "Moulins", []),
    "Puy-de-Dôme": ("FRA_PUY_DE_DOME", "Puy-de-Dôme", "Clermont-Ferrand",
                    [("Clermont-Ferrand", "Michelin works")]),
    "Seine-et-Marne": ("FRA_SEINE_ET_MARNE", "Seine-et-Marne", "Melun", []),
    "Hautes-Alpes": ("FRA_HAUTES_ALPES", "Hautes-Alpes", "Gap", []),
    # NE data quirks: "Haute-Rhin" is NE's misspelling of Haut-Rhin.
    "Haute-Rhin": ("FRA_HAUT_RHIN", "Haut-Rhin", "Colmar",
                   [("Mulhouse", "textile + potash industry")]),
    # Sprint 5 already authored these 11 — explicit entries with their
    # EXISTING ids so the pid-in-existing skip fires (no slug heuristics).
    "Nord": ("FRA_NORD", "Nord", "Lille", []),
    "Pas-de-Calais": ("FRA_PAS_DE_CALAIS", "Pas-de-Calais", "Arras", []),
    "Ardennes": ("FRA_ARDENNES", "Ardennes", "Charleville-Mézières", []),
    "Aisne": ("FRA_AISNE", "Aisne", "Laon", []),
    "Somme": ("FRA_SOMME", "Somme", "Amiens", []),
    "Oise": ("FRA_OISE", "Oise", "Beauvais", []),
    "Marne": ("FRA_MARNE", "Marne", "Châlons-en-Champagne", []),
    "Meuse": ("FRA_MEUSE", "Meuse", "Bar-le-Duc", []),
    "Meurthe-et-Moselle": ("FRA_MEURTHE_MOSELLE", "Meurthe-et-Moselle",
                           "Nancy", []),
    "Moselle": ("FRA_MOSELLE", "Moselle", "Metz", []),
    "Bas-Rhin": ("FRA_BAS_RHIN", "Bas-Rhin", "Strasbourg", []),
}


def main() -> int:
    sys.path.insert(0, str(ROOT / "src"))
    from wargame_cartographer.geo.downloader import DataDownloader
    from wargame_cartographer.config.map_spec import BoundingBox

    boundaries = json.loads(BOUNDARIES.read_text(encoding="utf-8"))
    country_geom = {f["properties"]["country_code"]: shape(f["geometry"])
                    for f in boundaries["features"]}

    prov_geo = json.loads(PROV_GEO.read_text(encoding="utf-8"))
    prov_meta = json.loads(PROV_META.read_text(encoding="utf-8"))
    existing_ids = {f["properties"]["province_id"] for f in prov_geo["features"]}
    print(f"existing provinces: {len(existing_ids)}")

    dl = DataDownloader()
    ne = dl.get_natural_earth(
        "states", BoundingBox(min_lon=4.5, min_lat=44.0, max_lon=27.0,
                              max_lat=56.5))
    ne = ne[ne["adm0_a3"].isin(["CHE", "ITA", "FRA"])].copy()

    # province_id -> {display, capital, subs, geoms: []}
    build: dict[str, dict] = {}
    failures: list[str] = []

    def add(pid, display, capital, subs, geom):
        e = build.setdefault(pid, {"display": display, "capital": capital,
                                   "subs": subs, "geoms": []})
        if capital is not None and e["capital"] is None:
            e["capital"], e["subs"] = capital, subs
        e["geoms"].append(geom)

    for _, row in ne.iterrows():
        cc, name, geom = row["adm0_a3"], row.get("name"), row.geometry
        if geom is None or geom.is_empty or not geom.intersects(FRAME):
            continue
        if cc == "CHE":
            m = CHE_MAP.get(name)
        elif cc == "ITA":
            # NE admin-1 for Italy is PROVINCES (Vercelli, Pavia, ...); the
            # parent compartimento comes from the `region` column.
            m = ITA_MAP.get(str(row.get("region")))
        else:
            m = FRA_MAP.get(name)
        if m is None:
            failures.append(f"{cc}: NE unit {name!r} intersects the frame "
                            f"but has no mapping entry")
            continue
        pid, display, capital, subs = m
        if pid == "__FVG_SPLIT__":
            west = geom.intersection(box(-30, 30, FVG_CUT_LON, 60))
            east = geom.intersection(box(FVG_CUT_LON, 30, 40, 60))
            if not west.is_empty:
                vp, vd, vc, vs = ITA_MAP["Veneto"]
                add(vp, vd, vc, vs, west)
            if not east.is_empty:
                gp, gd, gc, gs = ITA_VENEZIA_GIULIA
                add(gp, gd, gc, gs, east)
            continue
        add(pid, display, capital, subs, geom)

    if failures:
        print("ABORT — unmapped NE units:")
        for f_ in failures:
            print("  FAIL ", f_)
        return 1

    new_feats, new_meta = [], []
    stamp = (f"NE admin-1 derived (Sprint 7 backfill, AD-027 lineage), "
             f"{date.today().isoformat()}; 1930 stopgap — review pending")
    skipped = []
    for pid in sorted(build):
        if pid in existing_ids:
            skipped.append(pid)
            continue
        e = build[pid]
        cc = pid.split("_")[0]
        geom = unary_union(e["geoms"]).intersection(country_geom[cc])
        if geom.is_empty:
            print(f"  note: {pid} empty after country clip — dropped")
            continue
        if e["capital"] is None:
            print(f"ABORT: {pid} assembled without a capital entry")
            return 1
        new_feats.append({
            "type": "Feature",
            "properties": {"province_id": pid, "name": e["display"],
                           "country": cc, "era": "1930-stopgap",
                           "notes": stamp},
            "geometry": mapping(geom)})
        new_meta.append({
            "province_id": pid, "name": e["display"], "country": cc,
            "capital": {"city_name": e["capital"]},
            "sub_capitals": [{"city_name": n, "rationale": r}
                             for n, r in e["subs"]]})
    if skipped:
        print(f"skipped already-authored: {sorted(skipped)}")

    prov_geo["features"].extend(new_feats)
    prov_meta["provinces"].extend(new_meta)
    prov_meta["version"] = "0.3-sprint7-backfill"
    prov_meta["source_note"] = (prov_meta.get("source_note", "") +
                                " | Sprint 7: CHE/ITA/eastern-FRA backfill "
                                "(NE admin-1 stopgap).")
    PROV_GEO.write_text(json.dumps(prov_geo, ensure_ascii=False),
                        encoding="utf-8")
    PROV_META.write_text(json.dumps(prov_meta, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    n_caps = sum(1 for p in prov_meta["provinces"]
                 if p.get("capital", {}).get("city_name"))
    print(f"\nadded {len(new_feats)} provinces "
          f"({sum(1 for p in new_meta if p['country']=='CHE')} CHE, "
          f"{sum(1 for p in new_meta if p['country']=='ITA')} ITA, "
          f"{sum(1 for p in new_meta if p['country']=='FRA')} FRA); "
          f"total {len(prov_geo['features'])} provinces / {n_caps} capitals")
    return 0


def _norm_id(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s.upper() if c.isalnum())


if __name__ == "__main__":
    sys.exit(main())
