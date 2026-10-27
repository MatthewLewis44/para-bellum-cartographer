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
CASES = json.loads((ROOT / "tests" / "fixtures" / "research_validate" / "cases.json").read_text(encoding="utf-8"))


def base() -> tuple[dict, list, list]:
    def read(name):
        return json.loads((BASE / name).read_text(encoding="utf-8"))
    return read("research.json"), read("bureau.json")["bureaus"], read("vendor.json")["vendors"]


def apply(patch: list) -> rv.Validator:
    research, bureaus, vendors = copy.deepcopy(base())
    for op in patch:
        if "project" in op:
            rows, key, sel = research["projects"], "id", op["project"]
        elif "bureau" in op:
            rows, key, sel = bureaus, "id", op["bureau"]
        elif "start" in op:
            rows, key, sel = research["start"], "nation", op["start"]
        else:
            raise AssertionError(f"unknown patch {op}")
        hits = [r for r in rows if r[key] == sel]
        assert len(hits) == 1, f"patch {op}: {len(hits)} rows match"
        hits[0].update(op["set"])
    return rv.Validator(research, bureaus, vendors)


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
        got = apply(case["patch"]).run()
        check(f"accept/{case['id']}", not got, "; ".join(f"{c} {w}: {t!r} {d}" for c, w, t, d in got))
    for case in CASES["refuse"]:
        got = apply(case["patch"]).run()
        codes = {c for c, *_ in got}
        check(f"refuse/{case['id']}", codes == {case["code"]},
              f"expected exactly {case['code']}, got {sorted(codes)}: "
              + "; ".join(f"{c} {w}: {t!r} {d}" for c, w, t, d in got))
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
