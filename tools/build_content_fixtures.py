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
         "pb/rates.json", "pb/combat.json", "pb/economy.json",
         # Sprint 13: the research database and its generated models, written by the pipeline's converter
         # (tools/research_convert.py) from its SYNTHETIC case set, then hand-kept here as part of the control set.
         "pb/research.json", "pb/bureau.json", "pb/vendor.json", "pb/design_model.json",
         # Sprint 14: the state.
         "pb/claim.json", "pb/policy.json", "pb/population.json",
         # Sprint 15: the formation hierarchy and its rules.
         "pb/squad.json", "pb/company.json", "pb/category.json", "pb/bundle.json", "pb/aggregation.json")


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
R = "pb/research.json"
CL = "pb/claim.json"
PO = "pb/policy.json"
PP = "pb/population.json"
EC = "pb/economy.json"
SQ = "pb/squad.json"
CO = "pb/company.json"
CA = "pb/category.json"
BU = "pb/bundle.json"
AG = "pb/aggregation.json"
CB = "pb/combat.json"

MUTATIONS = [
    ("h108_invalid_json", "C108", M, "model.json is not JSON (truncated)", "RAW"),
    ("h101_missing_key", "C101", M, "models[0] has no year", _del([M, "models", 0, "year"])),
    ("h101b_active_bureau_without_home", "C101", "pb/bureau.json",
     "bureaus[0] (DE_OSTWERK, ACTIVE) has home null; the control set's inactive DE_OSTWERK_PLANT with a null "
     "home is accepted, so the rule is scoped to active bureaus", _set(["pb/bureau.json", "bureaus", 0, "home"], None)),
    ("h102_wrong_type", "C102", M, 'models[0].year is the string "1930"', _set([M, "models", 0, "year"], "1930")),
    ("h103_not_integer", "C103", M, "models[2].stats.armour_front_mm is 13.5",
     _set([M, "models", 2, "stats", "armour_front_mm"], 13.5)),
    ("h103b_integral_float", "C103", M, "models[2].stats.armour_front_mm is 13.0 (integral, but written as a float)",
     _set([M, "models", 2, "stats", "armour_front_mm"], 13.0)),
    ("h104_negative_cost", "C104", M, "models[0].cost.steel is -1", _set([M, "models", 0, "cost", "steel"], -1)),
    ("h105_unknown_key", "C105", M, 'models[0] carries an undeclared key "colour"',
     _set([M, "models", 0, "colour"], "green")),
    ("h106_bad_id_format", "C106", M, 'models[0].id is "Tiger" (no namespace, upper case)',
     _set([M, "models", 0, "id"], "Tiger")),
    ("h107_bad_format", "C107", M, 'model.json declares format "pb-content/model/2"',
     _set([M, "format"], "pb-content/model/2")),
    ("h109_unknown_content_file", "C109", "pb/models.json", "a misnamed pb/models.json beside the real model.json",
     lambda d: d.__setitem__("pb/models.json", copy.deepcopy(d[M]))),
    ("h110_namespace_without_content", "C110", "mymod/", "a mymod/ folder holding only a README.md",
     lambda d: {"mymod/README.md": "# My mod" + chr(10) + chr(10) + "Nothing here yet." + chr(10)}),
    ("h105b_extension_unknown_key", "C105", "mymod/class.json",
     "a class extension that also tries to add stats, which only the engine may define",
     lambda d: d.__setitem__("mymod/class.json", {"format": "pb-content/class/1", "classes": [
         {"extends": "pb:tank", "traits": ["pb:sloped_armour"], "stats": {"weight_kg": 0}}]})),
    ("h201_unknown_namespace", "C201", M, 'models[2] carries trait "mymod:extra_fuel"; no Content/mymod/ exists',
     _append([M, "models", 2, "traits"], "mymod:extra_fuel")),
    ("h202_id_outside_namespace", "C202", M, 'models[0].id is "mymod:infantry_kit" inside the pb folder',
     _set([M, "models", 0, "id"], "mymod:infantry_kit")),
    ("h203_duplicate_id", "C203", M, "models[0] is defined twice", "DUP_MODEL0"),
    ("h204_reserved_namespace", "C204", M, 'models[0].id is "player:infantry_kit"',
     _set([M, "models", 0, "id"], "player:infantry_kit")),
    ("h205_design_id_collision", "C205", V, 'variants[0].id is "pb:light_tank", which is also a model',
     _set([V, "variants", 0, "id"], "pb:light_tank")),
    ("h111_atom_value_whitespace", "C111", R,
     'projects[1] (pb:aaa_02) requires "pb:aaa_01" followed by U+00A0: a padded atom value, refused by its own '
     "code ahead of C106, and never trimmed into a match",
     _set([R, "projects", 1, "prerequisites", 0, 0, "value"], "pb:aaa_01" + chr(0xA0))),
    ("h304_exclusive_pair_asymmetric", "C304", R,
     "pb:ql_02 no longer names pb:ql_01 in exclusive_with, so pb:ql_01's pair is one-sided",
     _set([R, "projects", 5, "exclusive_with"], [])),
    ("h909_slice_row_unencoded", "C909", R,
     "pb:aaa_01, a prerequisite of a slice design and a start holding, has a null effect",
     _set([R, "projects", 0, "effect"], None)),
    ("h909b_slice_rooted_on_unlock", "C909", R,
     "design_model pb:design_0002 is renamed pb:kestrel_ii_model (its unlock is still the study pb:design_0002), "
     "and that study now also needs pb:sss_01, whose effect is null. The subject is rooted on a model's UNLOCK, "
     "so pb:sss_01 is in it; rooted on the model's id it would not be (AD-M37 section 4: ids are opaque)",
     _both(_set(["pb/design_model.json", "models", 1, "id"], "pb:kestrel_ii_model"),
           _set([R, "projects", 9, "prerequisites"],
                [[{"kind": "design", "value": "pb:design_0001"}], [{"kind": "technology", "value": "pb:sss_01"}]]))),
    ("h909c_slice_walks_variant_of", "C909", R,
     "the slice design pb:design_0002 is now a variant of pb:design_0004, whose study needs pb:sss_01 (null effect), "
     "and nothing else reaches pb:design_0004. The subject walks variant_of, so pb:sss_01 is in it; a walk of "
     "prerequisites alone would leave it out and load",
     _set([R, "projects", 9, "variant_of"], "pb:design_0004")),
    ("h909d_slice_takes_every_exclusive_doctrine", "C909", R,
     "pb:ql_03 is made exclusive with pb:bbb_90 (both ways), held by no nation at the start, outside every slice "
     "design's closure, and has a null effect. The subject takes EVERY doctrine with a non-empty exclusive_with, "
     "so pb:ql_03 is in it; a subject without that clause would leave it out and load",
     _both(_set([R, "projects", 7, "exclusive_with"], ["pb:bbb_90"]),
           _set([R, "projects", 6, "exclusive_with"], ["pb:ql_03"]),
           _set([R, "projects", 7, "effect"], None),
           _set([R, "start", 1, "doctrines"], ["pb:bbb_90", "pb:ql_01"]))),
    ("h301_dangling_reference", "C301", V, 'variants[1].model is "pb:light_tnak"',
     _set([V, "variants", 1, "model"], "pb:light_tnak")),
    ("h301b_extends_dangling", "C301", "mymod/class.json", 'a class extension of "pb:tnak", which is not a class',
     lambda d: d.__setitem__("mymod/class.json", {"format": "pb-content/class/1", "classes": [
         {"extends": "pb:tnak", "traits": ["pb:sloped_armour"]}]})),
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
    ("h501_illegal_trait", "C501", M, "the trucks carry pb:sloped_armour, not legal on pb:trucks",
     _set([M, "models", 4, "traits"], ["pb:sloped_armour"])),
    ("h502_trait_absent_stat", "C502", C, "pb:trucks lists pb:sloped_armour as legal, but has no armour_front_mm",
     _set([C, "classes", 3, "traits"], ["pb:sloped_armour"])),
    ("h502b_extension_absent_stat", "C502", "mymod/class.json",
     "a mod extends pb:trucks with sloped armour, but trucks have no armour_front_mm: blamed on the mod, not on pb",
     lambda d: d.__setitem__("mymod/class.json", {"format": "pb-content/class/1", "classes": [
         {"extends": "pb:trucks", "traits": ["pb:sloped_armour"]}]})),
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
                                  "power_supply_per_tier": 0, "staffing_per_tier": 1,
                                  "deposits": [], "output": {}})),
    ("h406_unknown_settlement_type", "C406", ST, 'an extra settlement row of type "village", not one the map produces',
     _append([ST, "settlements"], {"type": "village", "manpower": 0, "facility_slots": 0,
                                   "training_slots": 0, "tier": 0})),
    ("h801_linear_power", "C801", FA, "tier.step_per_mille is 0: a linear power stack. A PROJECT INVARIANT: the "
     "authored 1930 base then browns out eight nations, so our gate refuses. The C# content READER accepts it "
     "(it has no map); the C# START STATE reports it, which is a different layer (register csharp_elsewhere)",
     _set([FA, "tier", "step_per_mille"], 0)),
    ("h903_facility_missing_kind", "C903", FA, "no facility row for kind RailYard",
     lambda d: d[FA].__setitem__("facilities", [f for f in d[FA]["facilities"] if f["kind"] != "RailYard"])),
    ("h904_deposit_without_output", "C904", FA, "the Mine lists a coal deposit but its output has no coal",
     _set([FA, "facilities", 0, "deposits"], ["iron", "coal"])),
    ("h905_facility_rules_twice", "C905", "mymod/facility.json",
     "a second facility.json (mymod) carries the tier and repair rules",
     lambda d: d.__setitem__("mymod/facility.json", {"format": "pb-content/facility/1",
                                                     "tier": dict(d[FA]["tier"]),
                                                     "repair_per_mille": d[FA]["repair_per_mille"],
                                                     "facilities": []})),
    ("h906_settlement_missing_type", "C906", ST, "no settlement row for type none",
     lambda d: d[ST].__setitem__("settlements", [r for r in d[ST]["settlements"] if r["type"] != "none"])),
    ("h907_rules_file_twice", "C907", "mymod/rates.json", "a second rates.json, in mymod. Blamed on the mod, "
     "never on pb/: error attribution follows authorship (D59)",
     lambda d: d.__setitem__("mymod/rates.json", copy.deepcopy(d["pb/rates.json"]))),
    ("h907b_rules_file_two_mods", "C907", ["amod/combat.json", "zmod/combat.json"],
     "no shipped combat.json; two mods each define one. With no pb/ copy there is no right one to single "
     "out, so EVERY copy is named (D59)",
     lambda d: (d.__setitem__("amod/combat.json", d.pop("pb/combat.json")),
                d.__setitem__("zmod/combat.json", copy.deepcopy(d["amod/combat.json"])), None)[-1]),
    ("h908_rules_file_missing", "C908", "combat.json", "no namespace defines combat.json",
     lambda d: d.pop("pb/combat.json") and None),
    # Sprint 15: a formation's identity is a content id.
    ("h915_no_templates", "C915", "template.json", "no namespace defines a division template",
     _set([T, "templates"], [])),
    ("h107b_template_old_format", "C107", T, 'template.json still declares "pb-content/template/1"',
     _set([T, "format"], "pb-content/template/1")),
    ("h105c_template_keyed_on_unit_type", "C105", T,
     'templates[0] also carries unit_type, the key identity moved off: refused, never read as a second identity',
     _set([T, "templates", 0, "unit_type"], "Infantry")),
    ("h101g_template_without_id", "C101", T, "templates[0] has no id", _del([T, "templates", 0, "id"])),
    ("h106b_template_bad_id", "C106", T, 'templates[0].id is "Infantry", the old enum spelling',
     _set([T, "templates", 0, "id"], "Infantry")),
    ("h202b_template_outside_namespace", "C202", T, 'templates[0].id is "mymod:infantry_division" in the pb folder',
     _set([T, "templates", 0, "id"], "mymod:infantry_division")),
    ("h203d_template_twice", "C203", T, "templates[1] takes templates[0]'s id",
     _set([T, "templates", 1, "id"], "pb:infantry_division")),
    ("h204b_template_reserved_namespace", "C204", T, 'templates[0].id is "player:infantry_division": player templates live in the save',
     _set([T, "templates", 0, "id"], "player:infantry_division")),
    ("h101h_template_without_symbol", "C101", T, "templates[0] has no symbol", _del([T, "templates", 0, "symbol"])),
    ("h409_unknown_counter_symbol", "C409", T, 'templates[0].symbol is "cavalry", which the game cannot draw',
     _set([T, "templates", 0, "symbol"], "cavalry")),
    # Sprint 15: the levels. Four and no fifth: the h105 cases each try to put one level inside the wrong parent.
    ("h105d_platoon_in_an_element", "C105", CO, "an element also holds a platoon: the floor holds nothing",
     _set([CO, "companies", 0, "platoons", 0, "elements", 0, "platoon"], {"name": "x", "count": 1, "elements": []})),
    ("h105e_company_in_a_platoon", "C105", CO, "a platoon slot names a company: a battalion by another route",
     _set([CO, "companies", 0, "platoons", 0, "company"], "pb:tank_company")),
    ("h105f_division_holds_a_bundle", "C105", T, "a division slot also names a bundle: nothing in the hierarchy can hold one",
     _set([T, "templates", 0, "companies", 0, "bundle"], "pb:rifle_battalion")),
    ("h105g_bundle_contains_platoons", "C105", BU, "a bundle slot carries platoons: a bundle names companies, it does not contain them",
     _set([BU, "bundles", 0, "companies", 0, "platoons"], [])),
    ("h105h_company_stores_multiplier", "C105", CO, 'a company stores its own "count": the count belongs to the slot (AD-M45)',
     _set([CO, "companies", 0, "count"], 3)),
    ("h101i_slot_without_count", "C101", T, "a division slot has no count: 1 is the designer's offer, not a reader default",
     _del([T, "templates", 0, "companies", 0, "count"])),
    ("h101j_platoon_without_name", "C101", CO, "an inline platoon has no name", _del([CO, "companies", 0, "platoons", 0, "name"])),
    ("h101k_element_without_count", "C101", CO, "an element has no count",
     _del([CO, "companies", 0, "platoons", 0, "elements", 0, "count"])),
    ("h101l_combat_without_late", "C101", CB, "combat.json has no late_contribution_per_mille",
     _del([CB, "late_contribution_per_mille"])),
    ("h102c_element_both_kinds", "C102", CO, "an element names both a squad and a vehicle",
     _set([CO, "companies", 0, "platoons", 0, "elements", 0, "vehicle"], "pb:trucks")),
    ("h102d_late_over_a_thousand", "C102", CB, "late_contribution_per_mille is 1001", _set([CB, "late_contribution_per_mille"], 1001)),
    ("h102e_company_without_platoons", "C102", CO, "a company has an empty platoons list", _set([CO, "companies", 0, "platoons"], [])),
    ("h104e_slot_count_zero", "C104", T, "a division slot's count is 0", _set([T, "templates", 0, "companies", 0, "count"], 0)),
    ("h104f_terrain_negative", "C104", CO, "a terrain value is -1: 1000 is the baseline and nothing goes below 0",
     _set([CO, "companies", 0, "terrain", "marsh"], -1)),
    ("h104g_late_negative", "C104", CB, "late_contribution_per_mille is -1", _set([CB, "late_contribution_per_mille"], -1)),
    ("h203e_aggregation_row_twice", "C203", AG, "organisation has two rows",
     _append([AG, "rules"], {"stat": "organisation", "rule": "sum", "subset": "all"})),
    ("h301d_slot_names_no_company", "C301", T, 'a division slot names "pb:rifle_compny"',
     _set([T, "templates", 0, "companies", 0, "company"], "pb:rifle_compny")),
    ("h301e_element_names_no_squad", "C301", CO, 'an element names "pb:rifle_sqaud"',
     _set([CO, "companies", 0, "platoons", 0, "elements", 0, "squad"], "pb:rifle_sqaud")),
    ("h301f_vehicle_names_no_design", "C301", CO, 'a vehicle element names "pb:light_tnak"',
     _set([CO, "companies", 1, "platoons", 0, "elements", 0, "vehicle"], "pb:light_tnak")),
    ("h301g_company_names_no_category", "C301", CO, 'a company\'s category is "pb:infantri"', _set([CO, "companies", 0, "category"], "pb:infantri")),
    ("h301h_requires_no_category", "C301", CA, 'armour requires "pb:infantri"',
     _set([CA, "categories", 1, "requires", 0, "category"], "pb:infantri")),
    ("h301i_bundle_names_a_bundle", "C301", BU, "a bundle slot names a bundle: there is no level for that",
     _set([BU, "bundles", 0, "companies", 0, "company"], "pb:rifle_battalion")),
    ("h308b_vehicle_crew_absent", "C308", CO, "the medium tank, used at one site, authors no crew: not 0, no answer, so the walk has no total to give",
     _both(_set([CO, "companies", 1, "platoons", 1, "elements", 0, "vehicle"], "pb:medium_tank"),
           _del([M, "models", 3, "stats", "crew"]))),
    ("h308_vehicle_with_no_crew", "C308", CO, "a vehicle element names pb:infantry_kit, whose model authors no crew",
     _set([CO, "companies", 1, "platoons", 0, "elements", 0, "vehicle"], "pb:infantry_kit")),
    ("h309_category_requires_itself", "C309", CA, "armour requires armour",
     _set([CA, "categories", 1, "requires", 0, "category"], "pb:armour")),
    ("h410_rule_names_no_stat", "C410", AG, 'an extra row for "warp_factor", not a division stat',
     _append([AG, "rules"], {"stat": "warp_factor", "rule": "sum", "subset": "all"})),
    ("h410b_company_stat_unknown", "C410", CO, 'a company sets "warp_factor"', _set([CO, "companies", 0, "stats", "warp_factor"], 1)),
    ("h411_company_missing_stat", "C411", CO, "a company has no organisation", _del([CO, "companies", 0, "stats", "organisation"])),
    ("h412_unknown_rule", "C412", AG, 'organisation\'s rule is "median"', _set([AG, "rules", 0, "rule"], "median")),
    ("h413_unknown_column", "C413", T, 'a slot\'s column is "reserve"', _set([T, "templates", 0, "companies", 0, "column"], "reserve")),
    ("h414_unknown_biome", "C414", CO, 'a terrain table names "moon"', _set([CO, "companies", 0, "terrain", "moon"], 900)),
    ("h415_company_stores_men", "C415", CO, "a company sets men: a second figure that could disagree with the walk",
     _set([CO, "companies", 0, "stats", "men"], 100)),
    ("h415b_company_stores_terrain", "C415", CO, "a company sets terrain in stats, not in its table",
     _set([CO, "companies", 0, "stats", "terrain"], 1000)),
    ("h604_returns_rise", "C604", CA, "armour's returns go 1000, 1200: a curve that rewards stacking",
     _set([CA, "categories", 1, "returns_per_mille"], [1000, 1200])),
    ("h606_officers_exceed_men", "C606", SQ, "a squad of 5 has 6 officers", _set([SQ, "squads", 1, "officers"], 6)),
    ("h916_aggregation_missing_stat", "C916", AG, "no row for recce",
     lambda d: d[AG].__setitem__("rules", [r for r in d[AG]["rules"] if r["stat"] != "recce"])),
    ("h917_subset_misspelt", "C917", AG, 'hardness reads subset "lines", which selects nothing', _set([AG, "rules", 7, "subset"], "lines")),
    ("h917c_subset_is_hq", "C917", AG, "hardness reads hq: hidden companies are not a subset of their own; all includes them",
     _set([AG, "rules", 7, "subset"], "hq")),
    ("h917b_subset_no_template_uses", "C917", AG, "complexity reads support, and no template has a support company",
     _both(_set([AG, "rules", 11, "subset"], "support"),
           lambda d: [t.__setitem__("companies", [s for s in t["companies"] if s["column"] != "support"]) for t in d[T]["templates"]] and None)),
    ("h918_rule_reads_one_column", "C918", AG, "terrain's two-column rule is limited to line", _set([AG, "rules", 16, "subset"], "line")),
    ("h919_blend_without_weight", "C919", AG, "armour's blend has no weight", _del([AG, "rules", 5, "max_weight_per_mille"])),
    ("h919b_blend_weight_over", "C919", AG, "armour's blend weight is 1001", _set([AG, "rules", 5, "max_weight_per_mille"], 1001)),
    ("h919c_stray_weight", "C919", AG, "organisation's average carries a weight it would ignore", _set([AG, "rules", 0, "max_weight_per_mille"], 300)),
    ("h920_bundle_extends", "C920", "mymod/bundle.json", "a mod's bundle extends the sealed specimen: blamed on the mod",
     lambda d: d.__setitem__("mymod/bundle.json", {"format": "pb-content/bundle/1", "bundles": [
         {"id": "mymod:my_battalion", "extends": "pb:rifle_battalion", "companies": [{"company": "pb:rifle_company", "count": 2}]}]})),
    ("h203f_bundle_redefined", "C203", BU, "a second row takes the sealed specimen's id",
     _append([BU, "bundles"], {"id": "pb:rifle_battalion", "name": "Edited", "companies": [{"company": "pb:rifle_company", "count": 2}]})),
    ("h802c_template_unnamed", "C802", T, "templates[0] (pb:infantry_division) has no name: a PROJECT INVARIANT",
     _del([T, "templates", 0, "name"])),
    ("h902_template_class_no_design", "C902", T, "a template requires pb:helicopter, a class no design belongs to",
     _both(_append([C, "classes"], {"id": "pb:helicopter", "claim": "pb:claim_air", "name": "Helicopter",
                                    "stats": {"ceiling_m": 0}, "traits": []}),
           _append([T, "templates", 0, "equipment"], {"class": "pb:helicopter", "count": 1}))),
    # Sprint 14: the state. Staffing and a class's claim are required: a silent default is what kept
    # Facility.Staffing at 0 for three sprints.
    ("h101c_facility_without_staffing", "C101", FA, "the Mine has no staffing_per_tier",
     _del([FA, "facilities", 0, "staffing_per_tier"])),
    ("h101e_class_without_claim", "C101", C, "pb:tank names no claim", _del([C, "classes", 2, "claim"])),
    ("h104b_staffing_zero", "C104", FA, "the Mine's staffing_per_tier is 0: a facility that can never be unmanned",
     _set([FA, "facilities", 0, "staffing_per_tier"], 0)),
    ("h104c_province_without_people", "C104", PP, "provinces[0].start_population is 0: a missing census figure, not an empty province",
     _set([PP, "provinces", 0, "start_population"], 0)),
    ("h203b_province_twice", "C203", PP, "DEU_RHEINLAND has two rows",
     lambda d: d[PP]["provinces"].append(copy.deepcopy(d[PP]["provinces"][0]))),
    ("h203c_level_id_twice", "C203", PO, "pb:state_take's first level takes the id of a pb:draft level",
     _both(_set([PO, "policies", 2, "levels", 0, "id"], "pb:draft_0"),
           _set([PO, "policies", 2, "default_level"], "pb:draft_0"))),
    ("h301c_class_claim_dangling", "C301", C, 'pb:tank\'s claim is "pb:claim_ary", which is not a claim',
     _set([C, "classes", 2, "claim"], "pb:claim_ary")),
    ("h305_default_level_not_own", "C305", PO, "pb:draft's default_level is a level of pb:state_take",
     _set([PO, "policies", 0, "default_level"], "pb:state_take_0")),
    ("h305b_start_level_not_own", "C305", PO, "DEU starts pb:draft at a level of pb:state_take",
     _set([PO, "start", 0, "levels", "pb:draft"], "pb:state_take_0")),
    ("h306_class_on_system_claim", "C306", C, "pb:tank is built by the construction claim",
     _set([C, "classes", 2, "claim"], "pb:claim_construction")),
    ("h307_province_nation_without_row", "C307", PP, "FRA_MOSELLE belongs to POL, which has no nations row",
     _set([PP, "provinces", 1, "nation"], "POL")),
    ("h307b_start_nation_without_row", "C307", PO, "a policy start row for POL, which has no nations row",
     _set([PO, "start", 0, "nation"], "POL")),
    ("h403b_money_priced", "C403", EC, "economy prices money, which is not extracted",
     _set([EC, "prices_per_thousand", "money"], 1)),
    ("h407_unknown_policy_target", "C407", PO, 'pb:state_take has one level, which also writes "morale_per_mille", not an engine target',
     _both(_del([PO, "policies", 2, "levels", 1]),
           _append([PO, "policies", 2, "levels", 0, "effects"], {"target": "morale_per_mille", "value": 1}))),
    ("h408_unknown_claim_role", "C408", CL, 'pb:claim_navy has role "shipyard"',
     _set([CL, "claims", 3, "role"], "shipyard")),
    ("h603_share_over_a_thousand", "C603", PO, "pb:state_take's first level takes 1001 per mille",
     _set([PO, "policies", 2, "levels", 0, "effects", 0, "value"], 1001)),
    ("h603b_modifier_below_floor", "C603", PO, "pb:mobilisation's first level sets commerce to -1001",
     _set([PO, "policies", 1, "levels", 0, "effects", 1, "value"], -1001)),
    ("h804_province_not_in_map_data", "C804", PP, "provinces[0] is DEU_ATLANTIS: a PROJECT INVARIANT, loaded by the game",
     _set([PP, "provinces", 0, "province"], "DEU_ATLANTIS")),
    ("h805_design_domain_unclaimed", "C805", R, "no claim lists Naval, though a design study has that domain",
     _set([CL, "claims", 3, "domains"], [])),
    ("h806_domain_on_two_claims", "C806", CL, "Aircraft is on pb:claim_navy as well as pb:claim_air",
     _set([CL, "claims", 3, "domains"], ["Naval", "Aircraft"])),
    ("h806b_domain_on_system_claim", "C806", CL, "the civilian claim lists a domain",
     _set([CL, "claims", 0, "domains"], ["Tractors"])),
    ("h807_class_claim_disagrees_domain", "C807", C,
     "pb:tank is built by pb:claim_navy, but its generated models' studies are Armour, which maps to pb:claim_army",
     _set([C, "classes", 2, "claim"], "pb:claim_navy")),
    ("h910_level_writes_target_twice", "C910", PO, "pb:state_take has one level, which writes state_take_per_mille twice",
     _both(_del([PO, "policies", 2, "levels", 1]),
           _append([PO, "policies", 2, "levels", 0, "effects"], {"target": "state_take_per_mille", "value": 300}))),
    ("h911_levels_write_different_targets", "C911", PO, "pb:draft's second level no longer writes manpower_draft_per_mille",
     _del([PO, "policies", 0, "levels", 1, "effects", 1])),
    ("h912_share_two_writers", "C912", PO, "pb:mobilisation also writes state_take_per_mille at every level",
     lambda d: [lv["effects"].append({"target": "state_take_per_mille", "value": 100})
                for lv in d[PO]["policies"][1]["levels"]] and None),
    ("h912b_target_no_writer", "C912", PO, "no policy writes industry_term_per_mille",
     lambda d: [lv["effects"].pop(0) for lv in d[PO]["policies"][1]["levels"]] and None),
    ("h913_two_civilian_claims", "C913", CL, "pb:claim_navy has role civilian: two civilian claims",
     _both(_set([CL, "claims", 3, "role"], "civilian"), _del([CL, "claims", 3, "domains"]),
           _set([CL, "claims", 4, "domains"], ["Aircraft", "Naval"]))),
    ("h913b_no_construction_claim", "C913", CL, "no claim has role construction",
     _del([CL, "claims", 1])),
    ("h914_produced_pool_without_price", "C914", EC, "coal has a dispersed yield and no price",
     _del([EC, "prices_per_thousand", "coal"])),
    ("h403c_industry_cost_unknown_pool", "C403", EC, "one point of industry costs gold, not a resource pool",
     _set([EC, "industry_point", "cost", "gold"], 1)),
    ("h104d_industry_point_without_workers", "C104", EC, "one point of industry needs 0 workers: it could never be unmanned",
     _set([EC, "industry_point", "workers"], 0)),
    ("h101f_province_without_industry", "C101", PP, "provinces[0] has no start_industry",
     _del([PP, "provinces", 0, "start_industry"])),
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


# The two-deposit coal mine (D42 option A, ruled by Matthew in D53) is BUILT now that the
# engine records which deposit each mine works (Unity 0074cd3) and M has vendored
# (3fc9e28). It is an ACCEPTED fixture (a002_coal_mine, below), not a change to
# h000_valid's Mine row: the shipped Mine row is the game's file to change (one line),
# and changing the control set would churn all 50 fixture directories for one row.
def _coal_mine(d):
    mine = next(f for f in d[FA]["facilities"] if f["kind"] == "Mine")
    mine["deposits"] = ["iron", "coal"]
    mine["output"] = {"iron": 100, "coal": 100}


def _class_extension(d):
    """a003: a MOD adds a trait to the SHIPPED class pb:tank and builds a tank that carries it.
    Without the extension this set is refused with C501; with it, it loads."""
    d["mymod/trait.json"] = {"format": "pb-content/trait/1", "traits": [
        {"id": "mymod:wet_stowage", "name": "Wet stowage",
         "modifiers": [{"stat": "reliability_per_mille", "per_mille": 50}]}]}
    d["mymod/class.json"] = {"format": "pb-content/class/1", "classes": [
        {"extends": "pb:tank", "traits": ["mymod:wet_stowage"]}]}
    d["mymod/model.json"] = {"format": "pb-content/model/1", "models": [
        {"id": "mymod:t34_85", "class": "pb:tank", "name": "T-34-85", "year": 1944, "unlock": None,
         "supersedes": None, "stats": {"armour_front_mm": 90, "main_gun_mm": 85, "speed_road_kph": 54, "crew": 5},
         "traits": ["mymod:wet_stowage", "pb:sloped_armour"], "cost": {"steel": 6, "oil": 1},
         "production": {"units_per_factory_per_month": 2}}]}


ACCEPTED = [
    ("a007_mod_lists_own_bundle", "a mod writes a NEW bundle naming shipped companies: the control for h920. "
     "Sealed means no edit of a bundle, not that bundles are closed",
     lambda d: d.__setitem__("mymod/bundle.json", {"format": "pb-content/bundle/1", "bundles": [
         {"id": "mymod:my_battalion", "name": "My battalion", "companies": [{"company": "pb:rifle_company", "count": 2}]}]})),
    ("a008_subset_some_division_lacks", "complexity reads support; pb:motorized_division has no support company and is "
     "legal. The control for h917b: a subset must select something in the set, not in every division",
     _set([AG, "rules", 11, "subset"], "support")),
    ("a010_crew_zero_is_an_answer", "a vehicle whose model authors crew 0 is accepted: the control for h308b. 0 means it "
     "carries none, and the walk counts its passengers as nobody",
     _both(_set([CO, "companies", 1, "platoons", 1, "elements", 0, "vehicle"], "pb:trucks"),
           _set([M, "models", 4, "stats", "crew"], 0))),
    ("a009_vehicle_is_a_variant", "a vehicle element names a variant: a design is a model or a variant",
     _set([CO, "companies", 1, "platoons", 0, "elements", 0, "vehicle"], "pb:medium_tank_late")),
    ("a006_mod_adds_template", "a mod adds a division of its own beside the shipped three. Identity is a content id, "
     "so nothing is keyed on a closed engine list any more",
     lambda d: d.__setitem__("mymod/template.json", {"format": "pb-content/template/2", "templates": [
         {"id": "mymod:cavalry_division", "name": "Cavalry division", "symbol": "infantry",
          "companies": [{"company": "pb:rifle_company", "count": 1, "column": "line"}], "cost": {"money": 1}, "training_days": 1,
          "speed_km_per_day": 1, "equipment": [{"class": "pb:infantry_kit", "count": 1}]}]})),
    ("a002_coal_mine", "the Mine extracts iron OR coal: deposits [iron, coal], output {iron: 100, coal: 100}. "
     "Each deposit names what it produces, so C904 holds", _coal_mine),
    ("a003_class_extension", "a mod makes its own trait legal on the shipped pb:tank with { extends, traits } "
     "and builds a tank carrying it; refused with C501 without the extension", _class_extension),
    ("a001_layer_arithmetic",
     "ONE truncating division per layer after summing: two +150 traits on armour 13 give 13 x 1300/1000 = 16 "
     "(per-contribution rounding would give 15). And the VISIBLE clamp: reliability penalties summing to -1400 "
     "hold the Base layer at 0 with a clamp:non_negative +400 source, so the value is 0, never negative",
     _layer_arithmetic),
    ("h802b_mod_unnamed_accepted", "a mod trait (mymod:extra_fuel) with no name: forgiven, because C802 holds "
     "only pb: content to a name",
     lambda d: d.__setitem__("mymod/trait.json", {"format": "pb-content/trait/1",
                                                  "traits": [{"id": "mymod:extra_fuel", "modifiers": []}]})),
    ("a004_claim_with_no_producer", "pb:claim_navy and pb:claim_air are declared with no class pointing at them, and "
     "a mod adds a claim of its own: a claim set is data, and a claim nobody builds for is legal, not a gap",
     lambda d: d.__setitem__("mymod/claim.json", {"format": "pb-content/claim/1", "claims": [
         {"id": "mymod:claim_space", "role": "production"}]})),
    ("a005_modifier_writers_add", "a mod's own policy also writes industry_term_per_mille: a modifier may have many "
     "writers, unlike a share",
     lambda d: d.__setitem__("mymod/policy.json", {"format": "pb-content/policy/1", "start": [], "policies": [
         {"id": "mymod:war_bonds", "default_level": "mymod:war_bonds_0", "levels": [
             {"id": "mymod:war_bonds_0", "effects": [{"target": "industry_term_per_mille", "value": 50}]}]}]})),
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
            out = fn(docs)
            if isinstance(out, dict):      # a mutation may add non-JSON files (raw text)
                raw = out
                docs.update({k: None for k in out})
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
