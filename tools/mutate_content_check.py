"""Mutation proofs for the content contract's Sprint 14 rules: the state, one to one.

    uv run python tools/mutate_content_check.py

Each mutation below breaks one rule of tools/content_check.py or one constraint of a
schema, and names the EXACT fixtures that must go red. It is the fixture set's
specificity proof, not only its sensitivity: a mutant that turns a neighbour red as
well, or misses its own, fails the run. A broken rule that crashes the checker on its
own fixture counts as red for that fixture alone. Restores each file and compares its
sha256. Exits non-zero on any mismatch.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CC = ROOT / "tools" / "content_check.py"
SCHEMAS = ROOT / "content" / "schemas"
FIXTURES = ROOT / "tests" / "fixtures" / "content"

MUTATIONS = (
    # The rules in the checker.
    ("M1 C305: a default_level need not be the policy's own", CC,
     'if p["default_level"] not in ids:', "if False:", {"h305_default_level_not_own"}),
    ("M2 C305: a start level need not be the policy's own", CC,
     'elif lid not in {lv["id"] for lv in p["levels"]}:', "elif False:", {"h305b_start_level_not_own"}),
    ("M3 C306: a class may be built by a system claim", CC,
     'elif self.by_id["claim"][id_]["role"] != "production":', "elif False:", {"h306_class_on_system_claim"}),
    ("M4 C301: a class's claim need not exist", CC,
     'if id_ not in self.by_id["claim"]:', "if False:", {"h301c_class_claim_dangling"}),
    ("M5 C307: a province's nation needs no row", CC,
     'if p["nation"] not in nations:', "if False:", {"h307_province_nation_without_row"}),
    ("M6 C307: a start row's nation needs no row", CC,
     'if nations is not None and s["nation"] not in nations:', "if False:", {"h307b_start_nation_without_row"}),
    ("M7 C407: any policy target accepted", CC,
     "if t not in forms:", "if False:", {"h407_unknown_policy_target"}),
    ("M8 C603: a share may exceed 1000", CC,
     'elif forms[t] == "share" and not 0 <= v <= 1000:', "elif False:", {"h603_share_over_a_thousand"}),
    ("M9 C603: a modifier has no floor", CC,
     'elif forms[t] == "modifier" and v < -1000:', "elif False:", {"h603b_modifier_below_floor"}),
    ("M10 C408: any claim role accepted", CC,
     'if c["role"] not in roles:', "if False:", {"h408_unknown_claim_role"}),
    ("M11 C910: a level may write a target twice", CC,
     "if t in seen:", "if False:", {"h910_level_writes_target_twice"}),
    ("M12 C911: levels may write different targets", CC,
     "if any(s != shapes[0] for s in shapes):", "if False:", {"h911_levels_write_different_targets"}),
    ("M13 C912: a share may have two writers", CC,
     'if not who or (forms[t] == "share" and len(who) > 1):', "if not who:", {"h912_share_two_writers"}),
    ("M14 C912: a target may have no writer", CC,
     'if not who or (forms[t] == "share" and len(who) > 1):', 'if forms[t] == "share" and len(who) > 1:',
     {"h912b_target_no_writer"}),
    ("M15 C912: a MODIFIER limited to one writer too (the control a005 catches over-refusal)", CC,
     'if not who or (forms[t] == "share" and len(who) > 1):', "if not who or len(who) > 1:",
     {"a005_modifier_writers_add"}),
    ("M16 C913: two civilian claims accepted", CC,
     "if len(ids) != 1:", "if len(ids) < 1:", {"h913_two_civilian_claims"}),
    ("M17 C913: no construction claim accepted", CC,
     "if len(ids) != 1:", "if len(ids) > 1:", {"h913b_no_construction_claim"}),
    ("M18 C914: a produced pool needs no price", CC,
     'for pool in sorted((produced & pools) - set(economy["prices"])):', "for pool in []:",
     {"h914_produced_pool_without_price"}),
    ("M19 C403: money may be priced", CC,
     "if pool not in pools or pool in UNPRICED_POOLS:", "if pool not in pools:", {"h403b_money_priced"}),
    ("M20 C804: a province need not exist", CC,
     'if p["province"] not in known:', "if False:", {"h804_province_not_in_map_data"}),
    ("M21 C805: a design domain need not be claimed", CC,
     'if p["domain"] not in by_domain:', "if False:", {"h805_design_domain_unclaimed"}),
    ("M22 C806: a domain may sit on a system claim", CC,
     'if c["role"] != "production":\n                    self._add(rel, "C806"',
     'if False:\n                    self._add(rel, "C806"', {"h806b_domain_on_system_claim"}),
    ("M23 C806: a domain may sit on two claims", CC,
     "elif dom in by_domain:", "elif False:", {"h806_domain_on_two_claims"}),
    ("M24 C807: a class's claim may disagree with its designs' domain", CC,
     "if claim != want:", "if False:", {"h807_class_claim_disagrees_domain"}),
    ("M25 C203: a province may have two rows", CC,
     'if p["province"] in seen:', "if False:", {"h203b_province_twice"}),
    ("M26 C203: a level id may repeat", CC,
     'if lv["id"] in own:', "if False:", {"h203c_level_id_twice"}),
    # The constraints in the schemas.
    ("M27 C101: staffing_per_tier is optional", SCHEMAS / "facility.schema.json",
     '"power_supply_per_tier", "staffing_per_tier", "deposits"', '"power_supply_per_tier", "deposits"',
     {"h101c_facility_without_staffing"}),
    ("M29 C101: a class's claim is optional", SCHEMAS / "class.schema.json",
     '"required": ["id", "claim", "stats", "traits"]', '"required": ["id", "stats", "traits"]',
     {"h101e_class_without_claim"}),
    ("M30 C104: staffing may be 0, the state that hid a missing system for three sprints", SCHEMAS / "facility.schema.json",
     'can never be unmanned.",\n          "type": "integer", "minimum": 1', 'can never be unmanned.",\n          "type": "integer", "minimum": 0',
     {"h104b_staffing_zero"}),
    ("M31 C104: a province may hold nobody", SCHEMAS / "population.schema.json",
     'not that nobody lives there.",\n            "type": "integer", "minimum": 1',
     'not that nobody lives there.",\n            "type": "integer", "minimum": 0', {"h104c_province_without_people"}),
    ("M33 C403: an industry point may cost an unknown pool", CC,
     'if pool not in pools:\n                self._add(rel, "C403", f"$.industry_point.cost.{pool}"',
     'if False:\n                self._add(rel, "C403", f"$.industry_point.cost.{pool}"', {"h403c_industry_cost_unknown_pool"}),
    ("M34 C104: a point of industry may need nobody", SCHEMAS / "economy.schema.json",
     'impossible to unman.",\n          "type": "integer", "minimum": 1', 'impossible to unman.",\n          "type": "integer", "minimum": 0',
     {"h104d_industry_point_without_workers"}),
    ("M35 C101: start_industry is optional", SCHEMAS / "population.schema.json",
     '"start_population", "start_industry"]', '"start_population"]', {"h101f_province_without_industry"}),
)


def probe() -> int:
    """Print, as JSON, the fixtures whose result differs from their expected.json."""
    sys.path.insert(0, str(ROOT / "tools"))
    import content_check as cc
    red = []
    for d in sorted(p for p in FIXTURES.iterdir() if p.is_dir()):
        exp = json.loads((d / "expected.json").read_text(encoding="utf-8"))
        try:
            got = [(p.code, p.file) for p in cc.ContentSet(d / "Content").problems]
        except Exception:
            red.append(d.name)
            continue
        if exp["python"] == "accept":
            want = []
        else:
            files = exp["file"] if isinstance(exp["file"], list) else [exp["file"]]
            want = [(exp["code"], f) for f in files]
        if got != want:
            red.append(d.name)
    print(json.dumps(red))
    return 0


def run_probe() -> set[str]:
    r = subprocess.run([sys.executable, __file__, "--probe"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(r.stderr)
    return set(json.loads(r.stdout.splitlines()[-1]))


def main() -> int:
    if "--probe" in sys.argv:
        return probe()
    base = run_probe()
    print(f"control, nothing mutated: {len(base)} fixture(s) red{': ' + ', '.join(sorted(base)) if base else ''}")
    wrong = ["the unmutated control"] if base else []
    for label, target, old, new, want in MUTATIONS:
        orig = target.read_bytes()
        h0 = hashlib.sha256(orig).hexdigest()
        src = orig.decode("utf-8")
        assert src.count(old) == 1, f"{label}: anchor {old!r} found {src.count(old)} times"
        try:
            target.write_bytes(src.replace(old, new).encode("utf-8"))
            got = run_probe()
        finally:
            target.write_bytes(orig)
        assert hashlib.sha256(target.read_bytes()).hexdigest() == h0, f"{label}: not restored"
        ok = got == want
        print(f"{'exact' if ok else 'WRONG'}  {label}: {sorted(got)}")
        if not ok:
            wrong.append(label)
    print(f"{len(MUTATIONS)} mutations, {len(MUTATIONS) - len([w for w in wrong if w != 'the unmutated control'])} "
          f"exactly their own fixture{'; WRONG: ' + ', '.join(wrong) if wrong else ''}")
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
