"""Gate for the research prerequisite grammar's reference parser (Sprint 13).

A parser that accepts everything is indistinguishable from no parser. This test
runs tools/research_grammar.py over the shared case set
(tests/fixtures/research_grammar/cases.json) and asserts:

  1. Every `refuse` case is refused with EXACTLY its one code: a G1xx by
     parse(), a G2xx by resolve() after a clean parse. Never a different code,
     never two.
  2. Every `accept` case parses to exactly the declared clauses, resolves with
     no problem, and renders back to its declared canonical spelling.
  3. REGISTER COVERAGE. Every code in content/research/grammar_codes.json has at
     least one refuse case, and every case's code is in the register. Adding a
     rule without a case fails here.
  4. Case ids are unique, and every case declares exactly one of accept/refuse.

The game's C# reader is held to the same cases.json, so the case set is itself
the cross-check between the two implementations.

Run: uv run python tests/test_research_grammar.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import research_grammar as rg  # noqa: E402

CASES = ROOT / "tests" / "fixtures" / "research_grammar" / "cases.json"


def load_registry(doc: dict) -> rg.Registry:
    return rg.Registry(
        ids=frozenset(doc["ids"]), designs=frozenset(doc["designs"]),
        flags=frozenset(doc["flags"]), fielded=frozenset(doc["fielded"]),
        focus=frozenset(doc["focus"]), xp=frozenset((b, t) for b, t in doc["xp"]),
        bureaus={n: frozenset(v) for n, v in doc["bureaus"].items()},
        vendors=frozenset(doc["vendors"]), nations=frozenset(doc["nations"]))


def outcome(case: dict, registry: rg.Registry) -> tuple[list[str], rg.Expression | None]:
    """The codes a case produces, in order, and its parse if it parsed."""
    try:
        expr = rg.parse(case["cell"], case["context"])
    except rg.GrammarError as e:
        return [e.code], None
    return [p.code for p in rg.resolve(expr, case["context"], registry, case.get("nation"))], expr


def as_data(expr: rg.Expression) -> list:
    return [[[a.kind, a.value] for a in clause] for clause in expr]


def main() -> int:
    doc = json.loads(CASES.read_text(encoding="utf-8"))
    registry = load_registry(doc["registry"])
    cases = doc["cases"]
    failures: list[str] = []

    def check(label, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  [{detail}]"))
        if not cond:
            failures.append(label)

    print(f"cases: {CASES.relative_to(ROOT).as_posix()}")
    ids = Counter(c["id"] for c in cases)
    check("4. case ids are unique", all(n == 1 for n in ids.values()),
          f"{[i for i, n in ids.items() if n > 1]}")
    check("4. every case declares exactly one of accept/refuse",
          all(("accept" in c) != ("refuse" in c) for c in cases),
          f"{[c['id'] for c in cases if ('accept' in c) == ('refuse' in c)]}")

    refusing = [c for c in cases if "refuse" in c]
    accepting = [c for c in cases if "accept" in c]
    for c in refusing:
        codes, _ = outcome(c, registry)
        check(f"1. {c['id']}: refused with exactly {c['refuse']}", codes == [c["refuse"]], f"got {codes}")
    for c in accepting:
        codes, expr = outcome(c, registry)
        ok = not codes and expr is not None and as_data(expr) == c["accept"]
        if ok and "render" in c:
            ok = rg.render(expr) == c["render"]
        check(f"2. {c['id']}: accepted as declared", ok,
              f"codes {codes}, parse {as_data(expr) if expr is not None else None}, "
              f"render {rg.render(expr) if expr is not None else None!r}")

    register = {r["code"] for r in rg.REGISTER}
    covered = {c["refuse"] for c in refusing}
    check(f"3. every register code has a refuse case ({len(covered & register)} of {len(register)})",
          register <= covered, f"uncovered {sorted(register - covered)}")
    check("3. every refuse case's code is in the register", covered <= register,
          f"unregistered {sorted(covered - register)}")

    print()
    if failures:
        print(f"FAIL - {len(failures)} check(s): {failures}")
        return 1
    print(f"PASS - {len(refusing)} refuse cases cover all {len(register)} codes, each refused with "
          f"exactly its code; {len(accepting)} accept cases parse, resolve and render as declared")
    return 0


if __name__ == "__main__":
    sys.exit(main())
