"""Gate for the starting-industrial-base checks (AD-043).

A validator that has only ever passed has not been tested. Everything below
seeds ONE violation into a small synthetic artifact and asserts that exactly
the check meant to catch it fails — above all Matthew's two constraints:

  1. A mill whose province holds no plant          -> constraint 1 (province).
  2. A nation whose plants cannot cover its draw   -> constraint 1 (national).
  3. A steel deposit on an entry, or a hex still carrying resources.steel
                                                   -> constraint 2.
  4. More facilities than the seat's slot total    -> slots.
  5. A mine off its deposit                        -> mine.
  6. An entry the authored table does not hold, or one whose tier moved
                                                   -> stale/unknown.
  7. An authored facility inside the bbox that never landed
                                                   -> dropped.
  8. A facility on a hex of another nation than authored -> wrong nation.
  9. A tier of 4 and an unknown kind               -> shape, without crashing.
 10. Entries out of (kind, name) order             -> ordering.
 11. The clean baseline passes every check, and the power mirror is tier^2.
 12. The power model is a parameter (the stack/base coupling): the base that
     balances at tier^2 fails under a linear model (step 0) and under a heavier
     mill draw, and supply truncates like the sim's per-mille stack.

Also: the table-level checks refuse a steel deposit and a mill-without-plant
nation, and the resource-layer check refuses any `steel` feature.

Run: uv run python tests/test_facility_checks.py
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_facilities_1930 as bf  # noqa: E402


def _hex(col, row, country, prov, *, typ="none", name="", tier="rural", iron=False, steel=False):
    return {
        "id": f"{col}_{row}", "coords": {"col": col, "row": row},
        "political": {"country_at_start": country, "province_at_start": prov},
        "settlement": {"type": typ, "name": name, "admin_tier": tier},
        "resources": {"iron": iron, "steel": steel, "coal": False, "oil": False},
        "flags": {"is_water": False},
        "facilities": [],
    }


def _row(name, kind, tier, country, lon, lat, deposit=""):
    return {"name": name, "kind": kind, "tier": str(tier), "country": country,
            "lon": str(lon), "lat": str(lat), "deposit": deposit,
            "confidence": "site", "note": ""}


def _entry(r):
    return {"kind": r["kind"], "tier": int(r["tier"]), "name": r["name"], "deposit": r["deposit"]}


ROWS = [
    _row("Plant A", "power_plant", 2, "AAA", 1.0, 1.0),
    _row("Mill A", "steel_mill", 1, "AAA", 1.0, 1.0),
    _row("Mine A", "mine", 1, "AAA", 1.1, 1.0, "iron"),
]


def baseline():
    # Province P_A: a city seat (5 slots), owner AAA. One hex holds plant + mill,
    # one holds the mine on iron.
    a = _hex(1, 1, "AAA", "P_A", typ="city", name="Acity", tier="capital")
    m = _hex(1, 2, "AAA", "P_A", iron=True)
    a["facilities"] = [_entry(ROWS[0]), _entry(ROWS[1])]
    m["facilities"] = [_entry(ROWS[2])]
    b = _hex(2, 1, "BBB", "P_B", typ="town", name="Btown", tier="capital")
    return {"map_metadata": {"bounds": {"min_lon": 0, "min_lat": 0, "max_lon": 2, "max_lat": 2}},
            "hexes": [a, m, b]}


def failing(data, rows=ROWS):
    checks, _ = bf.check_artifact(data, rows)
    return {name for name, ok, _, _ in checks if not ok}


def main() -> int:
    failures: list[str] = []

    def check(label, cond):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}")
        if not cond:
            failures.append(label)

    def only(label, data, needle, rows=ROWS):
        f = failing(data, rows)
        check(f"{label} -> fails exactly '{needle}'", len(f) == 1 and needle in next(iter(f)))
        if len(f) != 1:
            print(f"        failing: {sorted(f)}")

    check("clean baseline passes every check", failing(baseline()) == set())
    check("power mirror is tier^2 (base Tier x Facility layer)",
          [bf.power_supply(t) for t in (1, 2, 3)] == [1, 4, 9])

    # The power model is a parameter (the coupling, PowerModel): the same base
    # passes under the sim's current stack and fails under a linear "fix".
    check("PowerModel default reproduces the sim's tier^2 exactly",
          [bf.CURRENT_POWER.supply(t) for t in (1, 2, 3)] == [1, 4, 9])
    lin = bf.PowerModel(tier_step_per_mille=0)
    check("a linear model (step 0) gives supply = tier", [lin.supply(t) for t in (1, 2, 3)] == [1, 2, 3])
    check("supply truncates like the sim's per-mille stack (tier 2, step 500 -> 3)",
          bf.PowerModel(tier_step_per_mille=500).supply(2) == 3)
    f = {n for n, ok, _, _ in bf.check_artifact(baseline(), ROWS, power=lin)[0] if not ok}
    check("the authored base that balances at tier^2 FAILS under a linear model",
          len(f) == 1 and "supply covers its draw" in next(iter(f)))
    heavy = bf.PowerModel(draw=(("power_plant", 0), ("steel_mill", 9), ("mine", 1), ("rail_yard", 1)))
    f = {n for n, ok, _, _ in bf.check_artifact(baseline(), ROWS, power=heavy)[0] if not ok}
    check("a heavier mill draw read from the model fails the supply check",
          any("supply covers its draw" in n for n in f))

    d = baseline(); d["hexes"][0]["facilities"] = [_entry(ROWS[1])]
    f = failing(d, ROWS[1:])
    check("mill with no plant in its province fails constraint 1 (province AND nation)",
          any("province holds a power plant" in n for n in f)
          and any("supply covers its draw" in n for n in f))

    weak = [_row("Plant A", "power_plant", 1, "AAA", 1.0, 1.0)] + ROWS[1:]
    d = baseline(); d["hexes"][0]["facilities"][0]["tier"] = 1
    only("plant too small for the nation's draw", d, "supply covers its draw", weak)

    d = baseline(); d["hexes"][2]["resources"]["steel"] = True
    only("a hex still carrying resources.steel", d, "constraint 2")

    d = baseline()
    d["hexes"][1]["facilities"][0]["deposit"] = "steel"
    f = failing(d)
    check("a steel deposit on an entry fails constraint 2",
          any("constraint 2" in n for n in f))

    d = baseline()
    big = [_row("Plant A", "power_plant", 3, "AAA", 1.0, 1.0)] + ROWS[1:]   # power not the issue
    extra = [_row(f"Yard {i}", "rail_yard", 1, "AAA", 1.0, 1.0) for i in range(3)]
    d["hexes"][0]["facilities"][0]["tier"] = 3
    d["hexes"][0]["facilities"] += [_entry(r) for r in extra]
    d["hexes"][0]["facilities"].sort(key=lambda e: (bf.KINDS.index(e["kind"]), e["name"]))
    only("six facilities in a five-slot province", d, "slot total", big + extra)

    d = baseline(); d["hexes"][1]["resources"]["iron"] = False
    only("a mine off its deposit", d, "sits on its deposit")

    d = baseline(); d["hexes"][0]["facilities"][0]["tier"] = 3
    f = failing(d)
    check("a tier that no longer matches the table fails the authored-table check",
          any("matches the authored table" in n for n in f))

    d = baseline(); d["hexes"][1]["facilities"] = []
    only("an authored facility inside the bbox that never landed", d, "placed exactly once")

    d = baseline(); d["hexes"][1]["political"]["country_at_start"] = "BBB"
    f = failing(d)
    check("a facility on another nation's hex fails the authored-nation check",
          any("authored nation" in n for n in f))

    d = baseline(); d["hexes"][0]["facilities"][0]["tier"] = 4
    f = failing(d)
    check("tier 4 fails the tier check", any("tier within" in n for n in f))
    d = baseline(); d["hexes"][0]["facilities"][0]["kind"] = "refinery"
    try:
        f = failing(d)
        check("an unknown kind fails the kind check (and does not crash)",
              any("four authored kinds" in n for n in f))
    except Exception as e:  # noqa: BLE001
        check(f"an unknown kind does not crash ({e!r})", False)
    d = baseline(); d["hexes"][0]["facilities"] = None
    try:
        f = failing(d)
        check("facilities: null fails the well-formed check (and does not crash)",
              any("well-formed" in n for n in f))
    except Exception as e:  # noqa: BLE001
        check(f"facilities: null does not crash ({e!r})", False)

    d = baseline(); d["hexes"][0]["facilities"].reverse()
    only("entries out of (kind, name) order", d, "well-formed and ordered")

    # Table-level (static) checks and the resource-layer check.
    errs = bf.check_rows([_row("X", "mine", 1, "AAA", 1, 1, "steel")])
    check("table refuses a steel deposit", any("steel deposit" in e for e in errs))
    errs = bf.check_rows([_row("M", "steel_mill", 1, "AAA", 1, 1)])
    check("table refuses a nation with a mill and no plant", any("constraint 1" in e for e in errs))
    with tempfile.TemporaryDirectory() as t:
        p = Path(t) / "r.geojson"
        p.write_text(json.dumps({"features": [{"properties": {"resource_type": "steel",
                                                               "name": "Works"}}]}))
        check("resource layer with a steel feature is refused",
              bool(bf.check_no_steel_resources(p)))
        p.write_text(json.dumps({"features": [{"properties": {"resource_type": "coal"}}]}))
        check("resource layer without steel passes", not bf.check_no_steel_resources(p))

    print()
    if failures:
        print(f"FAIL - {len(failures)} check(s): {failures}")
        return 1
    print("PASS - the facility checks refuse every seeded violation")
    return 0


if __name__ == "__main__":
    sys.exit(main())
