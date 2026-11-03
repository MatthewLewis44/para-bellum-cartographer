"""The population converter: exact on synthetic input, and refusing one to one (Sprint 14).

    uv run python tests/test_population_convert.py

Each refuse case changes ONE thing in a valid synthetic set and must refuse with exactly its code. The
expected figures are worked by hand from the geometry below, not read back from the tool: a frame cutting a
province in half by longitude keeps very nearly half its area, so a census of 1,000,000 keeps very nearly
500,000, and the bound is stated rather than the tool's own output.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import population_convert as pc  # noqa: E402

REGISTER = json.loads((ROOT / "content" / "population" / "convert_codes.json").read_text(encoding="utf-8"))["codes"]


def hex_(province, nation, settlement=None):
    return {"political": {"province_at_start": province, "country_at_start": nation}, "settlement": settlement}


def square(lon0, lat0, lon1, lat1):
    return {"type": "Polygon", "coordinates": [[[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]]}


def valid():
    artifact = {"map_metadata": {"bounds": {"min_lon": 0.0, "min_lat": 40.0, "max_lon": 1.0, "max_lat": 42.0}},
                "hexes": [hex_("AAA_WHOLE", "AAA", {"type": "city", "population": 200000}),
                          hex_("AAA_WHOLE", "AAA", {"type": "none", "population": 999999}),
                          hex_("BBB_CUT", "BBB", {"type": "town", "population": 100000})]}
    census = [{"province": "AAA_WHOLE", "population": "800000", "note": ""},
              {"province": "BBB_CUT", "population": "1000000", "note": ""}]
    growth = [{"nation": "AAA", "growth_per_10k_per_year": "66"}, {"nation": "BBB", "growth_per_10k_per_year": "24"}]
    polygons = {"features": [
        {"properties": {"province_id": "AAA_WHOLE"}, "geometry": square(0.1, 40.1, 0.5, 40.5)},
        {"properties": {"province_id": "BBB_CUT"}, "geometry": square(0.5, 41.0, 1.5, 41.5)}]}
    return artifact, census, growth, polygons


def run(s):
    return pc.convert(s[0], s[1], s[2], s[3], 100)


def refuse_cases():
    def case(name, code, fn):
        s = copy.deepcopy(valid())
        fn(s)
        return name, code, s

    return [
        case("a map province with no census row", "X201", lambda s: s[1].pop(1)),
        case("a census row for a province not on the map", "X202",
             lambda s: s[1].append({"province": "CCC_GONE", "population": "1", "note": ""})),
        case("a province with two census rows", "X203", lambda s: s[1].append(dict(s[1][0]))),
        case("a nation with two growth rows", "X203", lambda s: s[2].append(dict(s[2][0]))),
        case("an empty census figure", "X204", lambda s: s[1][0].update(population="", note="not found")),
        case("a census figure with a separator", "X205", lambda s: s[1][0].update(population="800,000")),
        case("a growth rate with a decimal point", "X205", lambda s: s[2][0].update(growth_per_10k_per_year="6.6")),
        case("a nation with no growth row", "X206", lambda s: s[2].pop(1)),
        case("a province with no polygon", "X207", lambda s: s[3]["features"].pop(0)),
        case("a province wholly outside the frame", "X208",
             lambda s: s[3]["features"][1].update(geometry=square(5.0, 41.0, 6.0, 41.5))),
    ]


def main() -> int:
    failures = []

    def check(label, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"  [{detail}]"))
        if not ok:
            failures.append(label)

    doc, problems = run(valid())
    check("the valid set converts", doc is not None and not problems, f"{problems}")
    rows = {r["province"]: r for r in doc["provinces"]}
    check("a province wholly on the map keeps its whole census figure", rows["AAA_WHOLE"]["start_population"] == 800000,
          f"{rows['AAA_WHOLE']}")
    cut = rows["BBB_CUT"]["start_population"]
    check("a province the frame cuts in half keeps very nearly half: within 0.1% of 500,000", 499500 <= cut <= 500500, f"{cut}")
    check("urban share counts town-or-larger only: 200,000 of 800,000 is 250, the 'none' settlement ignored",
          rows["AAA_WHOLE"]["urban_per_mille"] == 250, f"{rows['AAA_WHOLE']}")
    check("urban share is against the ON-MAP people: 100,000 of about 500,000 is about 200",
          199 <= rows["BBB_CUT"]["urban_per_mille"] <= 201, f"{rows['BBB_CUT']}")
    s = copy.deepcopy(valid())
    s[0]["hexes"][0]["settlement"]["population"] = 5000000
    check("urban share is held at 1000 when the modern tag exceeds the 1930 people",
          {r["province"]: r for r in run(s)[0]["provinces"]}["AAA_WHOLE"]["urban_per_mille"] == 1000)
    s = copy.deepcopy(valid())
    s[0]["hexes"].append(hex_("", None))
    check("a hex with no province makes no row and no refusal", run(s)[0] == doc)
    check("the nations are the map's owners, sorted, each with its rate",
          doc["nations"] == [{"nation": "AAA", "growth_per_10k_per_year": 66}, {"nation": "BBB", "growth_per_10k_per_year": 24}],
          f"{doc['nations']}")
    check("the output passes the content schema", not list(pc_schema().iter_errors(doc)))

    cases = refuse_cases()
    for name, code, s in cases:
        try:
            d, got = run(s)
        except Exception as e:   # a broken rule that crashes on its own case fails that case, and only it
            d, got = None, [("CRASH", type(e).__name__, str(e))]
        check(f"{name}: refused with exactly {code}", d is None and [p[0] for p in got] == [code], f"{got}")
    covered = {c for _, c, _ in cases}
    check("every registered code has a case, and no case uses an unregistered code",
          covered == {c["code"] for c in REGISTER}, f"{sorted(covered ^ {c['code'] for c in REGISTER})}")

    # The committed source tables, as data: well-formed and consistent with what they claim.
    census = pc.read_tsv(pc.CENSUS)
    filled = [r for r in census if r["population"].strip()]
    check("every committed census row carries a source or says why it has no figure",
          all((r["source_url"].strip() and r["census_year"].strip()) if r["population"].strip() else r["note"].strip()
              for r in census))
    check("every committed growth row carries a source and a year",
          all(r["source_url"].strip() and r["year"].strip() for r in pc.read_tsv(pc.GROWTH)))
    total = sum(int(r["population"]) for r in filled if r["province"].startswith("DEU_"))
    # A THIRD SOURCE, not this table: Statistisches Jahrbuch 1934, Berufszaehlungen, 65,218 thousand.
    check("Germany's 29 province rows sum to the national census total within rounding (65,218 thousand)",
          abs(total - 65_218_000) < 1000, f"{total}")

    print(f"population_convert: {'FAILED ' + str(len(failures)) if failures else 'all passed'}; "
          f"{len(cases)} refuse cases over {len(covered)} codes")
    return 1 if failures else 0


def pc_schema():
    from jsonschema import Draft202012Validator
    return Draft202012Validator(json.loads((ROOT / "content" / "schemas" / "population.schema.json").read_text(encoding="utf-8")))


if __name__ == "__main__":
    sys.exit(main())
