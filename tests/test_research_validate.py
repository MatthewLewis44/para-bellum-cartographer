"""Gate for tools/research_validate.py, the R1-R9 port.

    uv run python tests/test_research_validate.py

The base is the converter's golden output (tests/fixtures/research_convert/expected/,
SYNTHETIC), which must pass every rule. tests/fixtures/research_validate/cases.json
patches copies of it:

  1. every accept case passes all nine rules (each pins a reading);
  2. every refuse case fails with EXACTLY its one code;
  3. every V code in validate_codes.json has a refuse case, every case's code is
     registered, and every code records a definition and a reading.

Exit status 0 only if every check holds; each failure is listed by name.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import research_validate as rv  # noqa: E402

BASE = ROOT / "tests" / "fixtures" / "research_convert" / "expected"
# The game content R11's target validity reads: the shared control set, whose research half IS this golden.
GAME = rv.load_game(ROOT / "tests" / "fixtures" / "content" / "h000_valid" / "Content")
CASES = json.loads((ROOT / "tests" / "fixtures" / "research_validate" / "cases.json").read_text(encoding="utf-8"))


def base() -> tuple[dict, list, list, list]:
    def read(name):
        return json.loads((BASE / name).read_text(encoding="utf-8"))
    return (read("research.json"), read("bureau.json")["bureaus"], read("vendor.json")["vendors"],
            read("design_model.json")["models"])


def apply(patch: list) -> rv.Validator:
    research, bureaus, vendors, models = copy.deepcopy(base())
    for op in patch:
        if "project" in op:
            rows, key, sel = research["projects"], "id", op["project"]
        elif "bureau" in op:
            rows, key, sel = bureaus, "id", op["bureau"]
        elif "model" in op:
            rows, key, sel = models, "id", op["model"]
        elif "start" in op:
            rows, key, sel = research["start"], "nation", op["start"]
        else:
            raise AssertionError(f"unknown patch {op}")
        hits = [r for r in rows if r[key] == sel]
        assert len(hits) == 1, f"patch {op}: {len(hits)} rows match"
        hits[0].update(op["set"])
    return rv.Validator(research, bureaus, vendors, models, GAME)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    fails: list[str] = []
    passed = 0

    def check(name: str, ok: bool, detail: str = "") -> None:
        nonlocal passed
        if ok:
            passed += 1
        else:
            fails.append(f"{name}: {detail}")

    for case in CASES["accept"]:
        v = apply(case["patch"])
        got = v.run()
        check(f"accept/{case['id']}", not got, "; ".join(f"{c} {w}: {t!r} {d}" for c, w, t, d in got))
        # D32: every verdict carries its subject size, so a vacuous pass is visible
        check(f"accept/{case['id']} every rule reports its subject size", set(v.size) == set(rv.CODES),
              f"missing {sorted(set(rv.CODES) - set(v.size))}")
    for case in CASES["refuse"]:
        got = apply(case["patch"]).run()
        codes = {c for c, *_ in got}
        check(f"refuse/{case['id']}", codes == {case["code"]},
              f"expected exactly {case['code']}, got {sorted(codes)}: "
              + "; ".join(f"{c} {w}: {t!r} {d}" for c, w, t, d in got))
    # The R11 subject is ROOTED on a model's unlock, not its id (AD-M37 section 4). No case can isolate this:
    # R12 refuses any generated model whose id differs from its unlock (V112), so the subject is read directly.
    # A model renamed away from its study, whose study needs pb:sss_01, must pull pb:sss_01 into the subject;
    # rooted on the id it would not. The vendored hostile fixture h909b pins the same root at load time.
    v = apply([{"model": "pb:design_0002", "set": {"id": "pb:kestrel_ii_model"}},
               {"project": "pb:design_0002", "set": {"prerequisites": [[{"kind": "design", "value": "pb:design_0001"}],
                                                                       [{"kind": "technology", "value": "pb:sss_01"}]]}}])
    sub = v.slice_subject()
    check("R11 subject is rooted on a model's UNLOCK, not its id (a renamed model's study still seeds it)",
          "pb:sss_01" in sub, f"{sorted(sub)}")
    # The other two clauses, read directly for the same reason (the edits would also trip R9 or R8). The vendored
    # fixtures h909c and h909d pin the same clauses at load time.
    sub = apply([{"project": "pb:design_0002", "set": {"variant_of": "pb:design_0004"}}]).slice_subject()
    check("R11 subject walks variant_of (a slice design's lineage parent's prerequisites are in it)",
          "pb:sss_01" in sub, f"{sorted(sub)}")
    sub = apply([{"project": "pb:ql_03", "set": {"exclusive_with": ["pb:bbb_90"]}},
                 {"project": "pb:bbb_90", "set": {"exclusive_with": ["pb:ql_03"]}},
                 {"start": "STH", "set": {"doctrines": ["pb:bbb_90", "pb:ql_01"]}}]).slice_subject()
    check("R11 subject takes every doctrine with a non-empty exclusive_with (held nowhere, needed by nothing)",
          "pb:ql_03" in sub, f"{sorted(sub)}")
    ids = [c["id"] for c in CASES["accept"] + CASES["refuse"]]
    check("cases/ids unique", len(ids) == len(set(ids)))
    covered = {c["code"] for c in CASES["refuse"]}
    for code, c in sorted(rv.CODES.items()):
        check(f"cases/{code} has a refuse case", code in covered)
        check(f"cases/{code} records its definition and reading", bool(c.get("definition")) and bool(c.get("reading")))
    for code in sorted(covered - set(rv.CODES)):
        check(f"cases/{code} is registered", False)

    print(f"research_validate: {passed} checks passed, {len(fails)} failed; {len(CASES['accept'])} accept and "
          f"{len(CASES['refuse'])} refuse cases over {len(covered)} of {len(rv.CODES)} codes")
    for f in fails:
        print(f"  FAIL  {f}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
