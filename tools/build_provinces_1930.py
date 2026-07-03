"""Generate data/boundaries/provinces_1930.geojson + provinces_1930_metadata.json.

⚠️  PARTIALLY SUPERSEDED (Sprint 6, AD-035): the German + Saar provinces this
    tool authors were REPLACED by real 1930 boundaries from OpenHistoricalMap —
    see tools/build_provinces_1930_east.py, which must be re-run AFTER this
    tool if this tool is ever re-run (it rebuilds DEU/POL/CSK/AUT/SAA/DZG on
    top of this tool's BEL/NLD/FRA/LUX output). Running this tool alone will
    clobber the AD-035 layer.

Reproducible province authoring for the 5-country western-front bbox (AD-023,
AD-027). Geometry source: Natural Earth admin-1 ("states", public domain) — a
1930 stopgap where 1930 boundaries ~= modern, with explicit manual adjustments
for the divergences:

  * Belgium: modern NE splits Brabant into Flemish Brabant + Walloon Brabant +
    Brussels (post-1995). 1930 had ONE Brabant province (incl. Brussels) → merge.
  * Netherlands: NE includes Flevoland (reclaimed 1986; Zuiderzee water in 1930).
    Folded into Overijssel (the Noordoostpolder's historical association) as a
    stopgap so reclaimed-land hexes still get a 1930 province. Documented.
  * Germany: modern Bundesländer do NOT match 1930. Reconstruct the relevant
    1930 states/Prussian provinces (AD-023): Rheinland, Westfalen, Hannover,
    Hesse-Nassau, Hesse-Darmstadt, Saar (League mandate 1930 — separate). NRW is
    split into Rheinland (W) / Westfalen (E) by a meridian cut; Hessen into
    Hesse-Nassau (N) / Hesse-Darmstadt (S) by a parallel cut. Cuts are
    APPROXIMATE (the historical borders zigzag) — capitals resolve correctly;
    the Ruhr edge is the main fuzziness. Flagged for Matthew's historical review.
  * France: départements (préfecture = capital), 1930-stable (Alsace-Lorraine
    returned 1919). Only those intersecting the build bbox are emitted.

Everything here is public-domain-derived per AD-018. This is a STOPGAP pending
historical review — same posture as boundaries_1930.geojson and
resources_1930.geojson.

Usage:  uv run python tools/build_provinces_1930.py
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
from shapely.geometry import box
from shapely.ops import unary_union

from wargame_cartographer.geo.downloader import DataDownloader
from wargame_cartographer.config.map_spec import BoundingBox

# Build region: covers Benelux + W. Germany generously (the validation bbox is
# 2.5-8.8E / 49.4-53.6N). Province geoms are clipped to this + a margin.
BUILD_BBOX = BoundingBox(min_lon=2.0, min_lat=49.0, max_lon=10.5, max_lat=54.2)
CLIP = box(BUILD_BBOX.min_lon - 0.5, BUILD_BBOX.min_lat - 0.5,
           BUILD_BBOX.max_lon + 0.5, BUILD_BBOX.max_lat + 0.5)

OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "boundaries"
GEOJSON = OUT_DIR / "provinces_1930.geojson"
METADATA = OUT_DIR / "provinces_1930_metadata.json"

# German Prussian/state cut lines (approximate; see module docstring).
NRW_CUT_LON = 7.3       # NRW: west -> Rheinland, east -> Westfalen
HESSEN_CUT_LAT = 50.0   # Hessen: north -> Hesse-Nassau, south -> Hesse-Darmstadt

# --- Belgium: NE name -> (province_id, display). Brabant pieces merge. ---
BEL_MAP = {
    "Antwerp":         ("BEL_ANTWERP", "Antwerpen"),
    "East Flanders":   ("BEL_EAST_FLANDERS", "Oost-Vlaanderen"),
    "West Flanders":   ("BEL_WEST_FLANDERS", "West-Vlaanderen"),
    "Hainaut":         ("BEL_HAINAUT", "Hainaut"),
    "Liege":           ("BEL_LIEGE", "Liège"),
    "Limburg":         ("BEL_LIMBURG", "Limburg"),
    "Luxembourg":      ("BEL_LUXEMBOURG", "Luxembourg (BE)"),
    "Namur":           ("BEL_NAMUR", "Namur"),
    "Flemish Brabant": ("BEL_BRABANT", "Brabant"),
    "Walloon Brabant": ("BEL_BRABANT", "Brabant"),
    "Brussels":        ("BEL_BRABANT", "Brabant"),
}

# --- Netherlands: 11 provinces; Flevoland folded into Overijssel. ---
NLD_MAP = {
    "Groningen":     ("NLD_GRONINGEN", "Groningen"),
    "Friesland":     ("NLD_FRIESLAND", "Friesland"),
    "Drenthe":       ("NLD_DRENTHE", "Drenthe"),
    "Overijssel":    ("NLD_OVERIJSSEL", "Overijssel"),
    "Flevoland":     ("NLD_OVERIJSSEL", "Overijssel"),   # reclaimed post-1930
    "Gelderland":    ("NLD_GELDERLAND", "Gelderland"),
    "Utrecht":       ("NLD_UTRECHT", "Utrecht"),
    "Noord-Holland": ("NLD_NOORD_HOLLAND", "Noord-Holland"),
    "Zuid-Holland":  ("NLD_ZUID_HOLLAND", "Zuid-Holland"),
    "Zeeland":       ("NLD_ZEELAND", "Zeeland"),
    "Noord-Brabant": ("NLD_NOORD_BRABANT", "Noord-Brabant"),
    "Limburg":       ("NLD_LIMBURG", "Limburg (NL)"),
}

# --- Luxembourg: the 3 NE districts merge into the single 1930 state. ---
LUX_MAP = {
    "Diekirch":     ("LUX_LUXEMBOURG", "Luxembourg"),
    "Grevenmacher": ("LUX_LUXEMBOURG", "Luxembourg"),
    "Luxembourg":   ("LUX_LUXEMBOURG", "Luxembourg"),
}

# --- France: département -> (province_id, display, prefecture=capital). ---
FRA_MAP = {
    "Nord":               ("FRA_NORD", "Nord", "Lille"),
    "Pas-de-Calais":      ("FRA_PAS_DE_CALAIS", "Pas-de-Calais", "Arras"),
    "Ardennes":           ("FRA_ARDENNES", "Ardennes", "Charleville-Mézières"),
    "Aisne":              ("FRA_AISNE", "Aisne", "Laon"),
    "Somme":              ("FRA_SOMME", "Somme", "Amiens"),
    "Oise":               ("FRA_OISE", "Oise", "Beauvais"),
    "Marne":              ("FRA_MARNE", "Marne", "Châlons-en-Champagne"),
    "Meuse":              ("FRA_MEUSE", "Meuse", "Bar-le-Duc"),
    "Moselle":            ("FRA_MOSELLE", "Moselle", "Metz"),
    "Meurthe-et-Moselle": ("FRA_MEURTHE_MOSELLE", "Meurthe-et-Moselle", "Nancy"),
    "Bas-Rhin":           ("FRA_BAS_RHIN", "Bas-Rhin", "Strasbourg"),
    "Haut-Rhin":          ("FRA_HAUT_RHIN", "Haut-Rhin", "Colmar"),
}

# --- Hand-curated capital + sub-capitals (AD-023). City names use OSM-likely
#     native spellings; the tagger normalises (lowercase/accent-strip) and falls
#     back to token containment, so language variants still match. ---
META: dict[str, dict] = {
    # Belgium (1-2 sub-capitals)
    "BEL_ANTWERP": {"capital": "Antwerpen", "subs": [("Mechelen", "rail+industry"), ("Turnhout", "Kempen centre")]},
    "BEL_BRABANT": {"capital": "Bruxelles - Brussel", "subs": [("Leuven", "university"), ("Nivelles", "Walloon Brabant seat")]},
    "BEL_EAST_FLANDERS": {"capital": "Gent", "subs": [("Aalst", "Dender industry"), ("Sint-Niklaas", "Waasland")]},
    "BEL_WEST_FLANDERS": {"capital": "Brugge", "subs": [("Kortrijk", "Leie industry"), ("Oostende", "North Sea port")]},
    "BEL_HAINAUT": {"capital": "Mons", "subs": [("Charleroi", "coal+steel"), ("Tournai", "Scheldt crossing")]},
    "BEL_LIEGE": {"capital": "Liège", "subs": [("Verviers", "textiles"), ("Seraing", "Cockerill steel")]},
    "BEL_LIMBURG": {"capital": "Hasselt", "subs": [("Genk", "Campine coal")]},
    "BEL_LUXEMBOURG": {"capital": "Arlon", "subs": [("Bastogne", "Ardennes road hub")]},
    "BEL_NAMUR": {"capital": "Namur", "subs": [("Dinant", "Meuse crossing")]},
    # Netherlands
    "NLD_GRONINGEN": {"capital": "Groningen", "subs": [("Delfzijl", "Eems port")]},
    "NLD_FRIESLAND": {"capital": "Leeuwarden", "subs": [("Sneek", "waterways")]},
    "NLD_DRENTHE": {"capital": "Assen", "subs": [("Emmen", "peat/Veenkoloniën")]},
    "NLD_OVERIJSSEL": {"capital": "Zwolle", "subs": [("Enschede", "Twente textiles"), ("Deventer", "IJssel trade")]},
    "NLD_GELDERLAND": {"capital": "Arnhem", "subs": [("Nijmegen", "Waal crossing"), ("Apeldoorn", "Veluwe")]},
    "NLD_UTRECHT": {"capital": "Utrecht", "subs": [("Amersfoort", "rail junction")]},
    "NLD_NOORD_HOLLAND": {"capital": "Haarlem", "subs": [("Amsterdam", "largest city, port"), ("Zaandam", "Zaan industry"), ("Hilversum", "Gooi")]},
    "NLD_ZUID_HOLLAND": {"capital": "Den Haag", "subs": [("Rotterdam", "world port"), ("Leiden", "university"), ("Dordrecht", "river junction")]},  # OSM names The Hague "Den Haag"
    "NLD_ZEELAND": {"capital": "Middelburg", "subs": [("Vlissingen", "Scheldt port")]},
    "NLD_NOORD_BRABANT": {"capital": "'s-Hertogenbosch", "subs": [("Eindhoven", "Philips industry"), ("Tilburg", "textiles"), ("Breda", "garrison")]},
    "NLD_LIMBURG": {"capital": "Maastricht", "subs": [("Heerlen", "coal"), ("Venlo", "Maas/rail border")]},
    # Luxembourg
    "LUX_LUXEMBOURG": {"capital": "Luxembourg", "subs": [("Esch-sur-Alzette", "Minett steel"), ("Diekirch", "Oesling")]},
    # Germany (Prussian provinces / 1930 states; 3-5 sub-capitals)
    "DEU_RHEINLAND": {"capital": "Koblenz", "subs": [("Köln", "largest, Rhine port"), ("Aachen", "western industry"), ("Düsseldorf", "Rhine, government"), ("Trier", "Mosel"), ("Bonn", "Rhine, university")]},
    "DEU_WESTFALEN": {"capital": "Münster", "subs": [("Dortmund", "Ruhr coal/steel"), ("Bielefeld", "Ravensberg textiles"), ("Bochum", "Ruhr mining"), ("Hagen", "south Ruhr")]},
    "DEU_HANNOVER": {"capital": "Hannover", "subs": [("Osnabrück", "western seat"), ("Oldenburg", "NW"), ("Bremen", "Weser port")]},
    "DEU_HESSEN_NASSAU": {"capital": "Wiesbaden", "subs": [("Frankfurt am Main", "finance/rail"), ("Kassel", "northern seat"), ("Fulda", "east")]},
    "DEU_HESSEN_DARMSTADT": {"capital": "Darmstadt", "subs": [("Mainz", "Rheinhessen"), ("Gießen", "Oberhessen"), ("Offenbach", "industry")]},
    "DEU_SAAR": {"capital": "Saarbrücken", "subs": [("Saarlouis", "Saar steel"), ("Neunkirchen", "ironworks")]},
}

COUNTRY_OF = {  # province_id prefix -> ISO3
    "BEL": "BEL", "NLD": "NLD", "LUX": "LUX", "FRA": "FRA", "DEU": "DEU",
}


def country_of(pid: str) -> str:
    return COUNTRY_OF[pid.split("_", 1)[0]]


def german_pieces(name: str, geom):
    """Yield (province_id, display, geom) for a modern German Bundesland."""
    minx, miny, maxx, maxy = geom.bounds
    if name == "Nordrhein-Westfalen":
        west = geom.intersection(box(minx - 1, miny - 1, NRW_CUT_LON, maxy + 1))
        east = geom.intersection(box(NRW_CUT_LON, miny - 1, maxx + 1, maxy + 1))
        yield "DEU_RHEINLAND", "Rheinland", west
        yield "DEU_WESTFALEN", "Westfalen", east
    elif name == "Rheinland-Pfalz":
        yield "DEU_RHEINLAND", "Rheinland", geom
    elif name == "Saarland":
        yield "DEU_SAAR", "Saar", geom
    elif name in ("Niedersachsen", "Bremen"):
        yield "DEU_HANNOVER", "Hannover", geom
    elif name == "Hessen":
        north = geom.intersection(box(minx - 1, HESSEN_CUT_LAT, maxx + 1, maxy + 1))
        south = geom.intersection(box(minx - 1, miny - 1, maxx + 1, HESSEN_CUT_LAT))
        yield "DEU_HESSEN_NASSAU", "Hesse-Nassau", north
        yield "DEU_HESSEN_DARMSTADT", "Hesse-Darmstadt", south
    # else: out-of-scope eastern/southern states (Bayern, BW, Saxony, …) — no
    # 1930 province this sprint; their hexes get country DEU but no province.


def main():
    dl = DataDownloader()
    g = dl.get_natural_earth("states", BUILD_BBOX)
    g = g[g["adm0_a3"].isin(["BEL", "NLD", "LUX", "FRA", "DEU"])].copy()

    # province_id -> list of geometries (to dissolve)
    geoms: dict[str, list] = {}
    disp: dict[str, str] = {}

    def add(pid, display, geom):
        geom = geom.intersection(CLIP)
        if geom.is_empty:
            return
        geoms.setdefault(pid, []).append(geom)
        disp.setdefault(pid, display)

    for _, row in g.iterrows():
        cc = row["adm0_a3"]
        name = row.get("name")
        geom = row.geometry
        if geom is None or geom.is_empty:
            continue
        if cc == "BEL" and name in BEL_MAP:
            pid, d = BEL_MAP[name]; add(pid, d, geom)
        elif cc == "NLD" and name in NLD_MAP:
            pid, d = NLD_MAP[name]; add(pid, d, geom)
        elif cc == "LUX" and name in LUX_MAP:
            pid, d = LUX_MAP[name]; add(pid, d, geom)
        elif cc == "FRA" and name in FRA_MAP:
            pid, d, _cap = FRA_MAP[name]; add(pid, d, geom)
        elif cc == "DEU":
            for pid, d, gp in german_pieces(name, geom):
                add(pid, d, gp)

    # Dissolve + build feature records.
    records = []
    for pid, glist in sorted(geoms.items()):
        merged = unary_union(glist)
        records.append({
            "province_id": pid,
            "name": disp[pid],
            "country": country_of(pid),
            "era": "1930-stopgap",
            "notes": "NE admin-1 derived; 1930 stopgap (AD-027) — review pending",
            "geometry": merged,
        })

    gdf = gpd.GeoDataFrame(records, crs="EPSG:4326")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gdf.to_file(GEOJSON, driver="GeoJSON")

    # --- Build metadata json (capital + sub_capitals) consistent with geojson ---
    emitted = set(gdf["province_id"])
    provinces_meta = []
    missing = []
    for pid in sorted(emitted):
        cc = country_of(pid)
        if pid in META:
            cap = META[pid]["capital"]
            subs = [{"city_name": n, "rationale": why} for n, why in META[pid]["subs"]]
        elif cc == "FRA":
            # capital = préfecture from FRA_MAP (match by province_id)
            cap = next((c for (_p, _d, c) in FRA_MAP.values()
                        if _p == pid), "")
            subs = []
        else:
            cap = ""
            subs = []
            missing.append(pid)
        provinces_meta.append({
            "province_id": pid,
            "name": disp[pid],
            "country": cc,
            "capital": {"city_name": cap},
            "sub_capitals": subs,
        })

    metadata = {
        "version": "0.1",
        "era": "1930",
        "source_note": "Hand-curated capitals/sub-capitals over NE-admin-1-derived "
                       "1930-stopgap province polygons (AD-023/AD-027, public domain). "
                       "Review pending.",
        "provinces": provinces_meta,
    }
    with open(METADATA, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(records)} provinces -> {GEOJSON}")
    print(f"Wrote metadata for {len(provinces_meta)} provinces -> {METADATA}")
    by_cc: dict[str, int] = {}
    for pid in emitted:
        by_cc[country_of(pid)] = by_cc.get(country_of(pid), 0) + 1
    print("Per country:", dict(sorted(by_cc.items())))
    if missing:
        print(f"WARNING: {len(missing)} provinces have no capital metadata: {missing}")
    caps = sum(1 for p in provinces_meta if p["capital"]["city_name"])
    subs_total = sum(len(p["sub_capitals"]) for p in provinces_meta)
    print(f"Capitals set: {caps}/{len(provinces_meta)}; sub-capitals total: {subs_total}")


if __name__ == "__main__":
    main()
