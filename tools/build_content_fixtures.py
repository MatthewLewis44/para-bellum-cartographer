"""Build the hostile content set from the valid control set (Sprint 12.5, T3).

    uv run python tools/build_content_fixtures.py

Reads tests/fixtures/content/h000_valid/ (the hand-authored valid content set),
rewrites it in canonical JSON formatting, and writes one fixture directory per
seeded violation beside it. Each fixture is a COMPLETE copy of the valid set
with exactly ONE change, so a refusal can only come from that one rule, and
each reviews as a one-line diff against h000_valid.

Each fixture also gets an expected.json, the record both implementations are
tested against:

    {"code": "C301", "file": "pb/variant.json",
     "python": "refuse", "csharp": "refuse", "rule": "...", "seeded": "..."}

`python` and `csharp` say what each implementation must do. They differ in
exactly the two cases the contract declares (CONTENT_MODEL 10c and 2):
C701 vocabulary-drift is visible only to C#, and C801 base-browns-out is a
project invariant that Python refuses while C# loads and reports. Writing
those cases as data stops anyone from later "fixing" the disagreement.

The authoritative copy of this set ships beside the game's EditMode tests;
this repository keeps a hash-pinned copy (tests/test_content_checks.py).
"""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "content"
VALID = FIXTURES / "h000_valid"

sys.path.insert(0, str(ROOT / "tools"))
from content_check import REGISTER, RULES  # noqa: E402

SIDES = {c["code"]: {"python": c["python"], "csharp": c["csharp"]} for c in REGISTER}

FILES = ("vocabulary.json", "pb/class.json", "pb/model.json", "pb/variant.json",
         "pb/trait.json", "pb/template.json", "pb/facility.json", "pb/settlement.json",
         "pb/rates.json", "pb/combat.json", "pb/economy.json")


def dump(doc) -> str:
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def load_valid() -> dict[str, object]:
    return {f: json.loads((VALID / "Content" / f).read_text(encoding="utf-8")) for f in FILES}


# Each mutation: (dir, code, file, seeded description, mutate(docs) -> None or raw text).
# python/csharp default to "refuse"; the two declared divergences override them.
def _m(fn):
    return fn


def _set(path, value):
    def f(d):
        doc = d[path[0]]
        for p in path[1:-1]:
            doc = doc[p]
        doc[path[-1]] = value
    return f


def _del(path):
    def f(d):
        doc = d[path[0]]
        for p in path[1:-1]:
            doc = doc[p]
        del doc[path[-1]]
    return f


def _append(path, value):
    def f(d):
        doc = d[path[0]]
        for p in path[1:]:
            doc = doc[p]
        doc.append(value)
    return f


def _both(*fns):
    def f(d):
        for fn in fns:
            fn(d)
    return f


M = "pb/model.json"
FA = "pb/facility.json"
ST = "pb/settlement.json"
V = "pb/variant.json"
C = "pb/class.json"
T = "pb/template.json"

MUTATIONS = [
    ("h108_invalid_json", "C108", M, "model.json is not JSON (truncated)", "RAW"),
    ("h101_missing_key", "C101", M, "models[0] has no year", _del([M, "models", 0, "year"])),
    ("h102_wrong_type", "C102", M, 'models[0].year is the string "1930"', _set([M, "models", 0, "year"], "1930")),
    ("h103_not_integer", "C103", M, "models[2].stats.armour_front_mm is 13.5",
     _set([M, "models", 2, "stats", "armour_front_mm"], 13.5)),
    ("h104_negative_cost", "C104", M, "models[0].cost.steel is -1", _set([M, "models", 0, "cost", "steel"], -1)),
    ("h105_unknown_key", "C105", M, 'models[0] carries an undeclared key "colour"',
     _set([M, "models", 0, "colour"], "green")),
    ("h106_bad_id_format", "C106", M, 'models[0].id is "Tiger" (no namespace, upper case)',
     _set([M, "models", 0, "id"], "Tiger")),
    ("h107_bad_format", "C107", M, 'model.json declares format "pb-content/model/2"',
     _set([M, "format"], "pb-content/model/2")),
    ("h201_unknown_namespace", "C201", M, 'models[2] carries trait "mymod:extra_fuel"; no Content/mymod/ exists',
     _append([M, "models", 2, "traits"], "mymod:extra_fuel")),
    ("h202_id_outside_namespace", "C202", M, 'models[0].id is "mymod:infantry_kit" inside the pb folder',
     _set([M, "models", 0, "id"], "mymod:infantry_kit")),
    ("h203_duplicate_id", "C203", M, "models[0] is defined twice", "DUP_MODEL0"),
    ("h204_reserved_namespace", "C204", M, 'models[0].id is "player:infantry_kit"',
     _set([M, "models", 0, "id"], "player:infantry_kit")),
    ("h205_design_id_collision", "C205", V, 'variants[0].id is "pb:light_tank", which is also a model',
     _set([V, "variants", 0, "id"], "pb:light_tank")),
    ("h301_dangling_reference", "C301", V, 'variants[1].model is "pb:light_tnak"',
     _set([V, "variants", 1, "model"], "pb:light_tnak")),
    ("h302_cyclic_supersedes", "C302", M, "the light tank supersedes the medium tank, which supersedes it",
     _set([M, "models", 2, "supersedes"], "pb:medium_tank")),
    ("h302b_self_supersedes", "C302", M, "the medium tank supersedes itself",
     _set([M, "models", 3, "supersedes"], "pb:medium_tank")),
    ("h303_supersedes_across_class", "C303", M, "the medium tank supersedes the trucks",
     _set([M, "models", 3, "supersedes"], "pb:trucks")),
    ("h401_stat_not_on_class", "C401", M, "the trucks set armour_front_mm, which pb:trucks does not have",
     _set([M, "models", 4, "stats", "armour_front_mm"], 5)),
    ("h402_unknown_stat_key", "C402", M, "the light tank sets warp_factor, not an engine stat key",
     _set([M, "models", 2, "stats", "warp_factor"], 9)),
    ("h403_unknown_pool", "C403", M, "models[0] costs gold, not a resource pool",
     _set([M, "models", 0, "cost", "gold"], 1)),
    ("h404_unknown_unit_type", "C404", T, 'a template for unit type "Cavalry"',
     _append([T, "templates"], {"unit_type": "Cavalry", "cost": {"money": 1}, "training_days": 1,
                                "speed_km_per_day": 1,
                                "equipment": [{"class": "pb:infantry_kit", "count": 1}]})),
    ("h501_illegal_trait", "C501", M, "the trucks carry pb:sloped_armour, not legal on pb:trucks",
     _set([M, "models", 4, "traits"], ["pb:sloped_armour"])),
    ("h502_trait_absent_stat", "C502", C, "pb:trucks lists pb:sloped_armour as legal, but has no armour_front_mm",
     _set([C, "classes", 3, "traits"], ["pb:sloped_armour"])),
    ("h503_remove_absent_trait", "C503", V, "variants[0] removes pb:unreliable_transmission, which its model lacks",
     _set([V, "variants", 0, "traits_remove"], ["pb:unreliable_transmission"])),
    ("h601_variant_of_variant", "C601", V, "variants[1].model is a variant (pb:medium_tank_late)",
     _set([V, "variants", 1, "model"], "pb:medium_tank_late")),
    ("h602_delta_negative", "C602", V, "variants[0].cost.steel -50 on a model costing 7",
     _set([V, "variants", 0, "cost", "steel"], -50)),
    ("h602b_production_negative", "C602", V, "variants[1].production delta -5 on a model producing 2",
     _set([V, "variants", 1, "production"], {"units_per_factory_per_month": -5})),
    ("h802_shipped_unnamed", "C802", M, "models[0] (pb:infantry_kit) has no name: a PROJECT INVARIANT, "
     "refused by our gate, loaded and reported by the game",
     _del([M, "models", 0, "name"])),
    ("h405_unknown_facility_kind", "C405", FA, 'an extra facility row of kind "Shipyard", not an engine kind',
     _append([FA, "facilities"], {"kind": "Shipyard", "cost": {"money": 1}, "power_draw": 0,
                                  "power_supply_per_tier": 0, "deposits": [], "output": {}})),
    ("h406_unknown_settlement_type", "C406", ST, 'an extra settlement row of type "village", not one the map produces',
     _append([ST, "settlements"], {"type": "village", "income": 0, "manpower": 0, "facility_slots": 0,
                                   "training_slots": 0, "tier": 0})),
    ("h801_linear_power", "C801", FA, "tier.step_per_mille is 0: a linear power stack. A PROJECT INVARIANT: the "
     "authored 1930 base then browns out eight nations, so our gate refuses. The C# content READER accepts it "
     "(it has no map); the C# START STATE reports it, which is a different layer (register csharp_elsewhere)",
     _set([FA, "tier", "step_per_mille"], 0)),
    ("h903_facility_missing_kind", "C903", FA, "no facility row for kind RailYard",
     lambda d: d[FA].__setitem__("facilities", [f for f in d[FA]["facilities"] if f["kind"] != "RailYard"])),
    ("h904_deposit_without_output", "C904", FA, "the Mine lists a coal deposit but its output has no coal",
     _set([FA, "facilities", 0, "deposits"], ["iron", "coal"])),
    ("h905_facility_rules_twice", "C905", "pb/facility.json",
     "a second facility.json (mymod) carries the tier and repair rules",
     lambda d: d.__setitem__("mymod/facility.json", {"format": "pb-content/facility/1",
                                                     "tier": dict(d[FA]["tier"]),
                                                     "repair_per_mille": d[FA]["repair_per_mille"],
                                                     "facilities": []})),
    ("h906_settlement_missing_type", "C906", ST, "no settlement row for type none",
     lambda d: d[ST].__setitem__("settlements", [r for r in d[ST]["settlements"] if r["type"] != "none"])),
    ("h907_rules_file_twice", "C907", "pb/rates.json", "a second rates.json, in mymod. Blamed on pb/: both "
     "readers take namespaces in ordinal order and mymod sorts first (raised as a modder-facing question)",
     lambda d: d.__setitem__("mymod/rates.json", copy.deepcopy(d["pb/rates.json"]))),
    ("h908_rules_file_missing", "C908", "combat.json", "no namespace defines combat.json",
     lambda d: d.pop("pb/combat.json")),
    ("h901_template_missing_unit_type", "C901", T, "no template for Armored",
     _del([T, "templates", 2])),
    ("h902_template_class_no_design", "C902", T, "a template requires pb:helicopter, a class no design belongs to",
     _both(_append([C, "classes"], {"id": "pb:helicopter", "name": "Helicopter",
                                    "stats": {"ceiling_m": 0}, "traits": []}),
           _append([T, "templates", 0, "equipment"], {"class": "pb:helicopter", "count": 1}))),
]

# The two declared divergences (data, not prose).
DIVERGENT = [
    ("h701_vocabulary_drift", "C701", "vocabulary.json",
     "vocabulary.json lists a stat key (rotor_rpm) the engine's code does not have",
     _append(["vocabulary.json", "stat_keys"], "rotor_rpm"),
     {"python": "accept", "csharp": "refuse",
      "why": "Python cannot see the engine's enums; only the C# vocabulary test can catch drift (CONTENT_MODEL 2)."}),
]
# Controls that must be ACCEPTED, proving a rule is scoped correctly rather than
# refusing too much. (name, seeded, mutate)
def _layer_arithmetic(d):
    """a001: the two cases a single-trait set cannot discriminate (CONTENT_MODEL 3).
    The light tank (armour 13, reliability 900) carries two +150 armour traits and
    penalties summing to -1400 on reliability (-200 -800 -400)."""
    tr = d["pb/trait.json"]["traits"]
    tr += [{"id": "pb:plus_a", "name": "Plus A", "modifiers": [{"stat": "armour_front_mm", "per_mille": 150}]},
           {"id": "pb:plus_b", "name": "Plus B", "modifiers": [{"stat": "armour_front_mm", "per_mille": 150}]},
           {"id": "pb:minus_a", "name": "Minus A", "modifiers": [{"stat": "reliability_per_mille", "per_mille": -800}]},
           {"id": "pb:minus_b", "name": "Minus B", "modifiers": [{"stat": "reliability_per_mille", "per_mille": -400}]}]
    tank = next(c for c in d["pb/class.json"]["classes"] if c["id"] == "pb:tank")
    tank["traits"] += ["pb:plus_a", "pb:plus_b", "pb:minus_a", "pb:minus_b"]
    light = next(m for m in d["pb/model.json"]["models"] if m["id"] == "pb:light_tank")
    light["traits"] += ["pb:plus_a", "pb:plus_b", "pb:minus_a", "pb:minus_b"]


ACCEPTED = [
    ("a001_layer_arithmetic",
     "ONE truncating division per layer after summing: two +150 traits on armour 13 give 13 x 1300/1000 = 16 "
     "(per-contribution rounding would give 15). And the VISIBLE clamp: reliability penalties summing to -1400 "
     "hold the Base layer at 0 with a clamp:non_negative +400 source, so the value is 0, never negative",
     _layer_arithmetic),
    ("h802b_mod_unnamed_accepted", "a mod trait (mymod:extra_fuel) with no name: forgiven, because C802 holds "
     "only pb: content to a name",
     lambda d: d.__setitem__("mymod/trait.json", {"format": "pb-content/trait/1",
                                                  "traits": [{"id": "mymod:extra_fuel", "modifiers": []}]})),
]

# READY, NOT BUILT: the two-deposit coal mine (D42 option A, ruled by Matthew in D53).
# The engine cannot yet record which deposit a mine works, so shipping two deposits
# now would be a file claiming a capability the engine lacks (D42 refused that). When
# PB-SIM12 lands the instance deposit, the order parameter and the save field, move
# this into ACCEPTED, and the shipped Mine row gains the same one-line change.
READY_FOR_OPTION_A = [
    ("a002_coal_mine", "the Mine extracts iron OR coal: deposits [iron, coal], output {iron: 100, coal: 100}",
     lambda d: (_set([FA, "facilities", 0, "deposits"], ["iron", "coal"])(d),
                _set([FA, "facilities", 0, "output"], {"iron": 100, "coal": 100})(d))),
]


def write_set(target: Path, docs: dict, raw: dict[str, str] | None = None):
    if target.exists():
        shutil.rmtree(target)
    for f in sorted(docs):
        p = target / "Content" / f
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text((raw or {}).get(f, dump(docs[f])), encoding="utf-8", newline="\n")


def build(root: Path = FIXTURES) -> list[str]:
    """Write h000_valid (canonicalised) and every hostile fixture under root. The
    test rebuilds into a temp dir and compares, so a stale committed set fails."""
    valid = load_valid()
    # Canonical formatting, so every fixture diffs to its one change.
    write_set(root / VALID.name, valid)
    (root / VALID.name / "expected.json").write_text(
        dump({"code": None, "python": "accept", "csharp": "accept",
              "seeded": "nothing: the valid control set"}), encoding="utf-8", newline="\n")
    built = []
    for name, code, file, seeded, fn in MUTATIONS:
        docs = copy.deepcopy(valid)
        raw = None
        if fn == "RAW":
            raw = {file: dump(docs[file])[:-40]}
        elif fn == "DUP_MODEL0":
            docs[M]["models"].append(copy.deepcopy(docs[M]["models"][0]))
        else:
            fn(docs)
        write_set(root / name, docs, raw)
        exp = {"code": code, "rule": RULES[code], "file": file,
               **SIDES[code], "seeded": seeded}
        (root / name / "expected.json").write_text(dump(exp), encoding="utf-8", newline="\n")
        built.append(name)
    for name, code, file, seeded, fn, sides in DIVERGENT:
        docs = copy.deepcopy(valid)
        fn(docs)
        write_set(root / name, docs)
        exp = {"code": code, "rule": RULES[code], "file": file, "seeded": seeded, **sides,
               "declared_divergence": True}
        (root / name / "expected.json").write_text(dump(exp), encoding="utf-8", newline="\n")
        built.append(name)
    for name, seeded, fn in ACCEPTED:
        docs = copy.deepcopy(valid)
        fn(docs)
        write_set(root / name, docs)
        exp = {"code": None, "python": "accept", "csharp": "accept", "seeded": seeded}
        (root / name / "expected.json").write_text(dump(exp), encoding="utf-8", newline="\n")
        built.append(name)
    return built


def main() -> int:
    built = build()
    print(f"wrote h000_valid + {len(built)} hostile fixtures under {FIXTURES.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
