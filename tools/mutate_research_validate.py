"""Mutation proofs for the research validator, R1-R9 (Sprint 13).

    uv run python tools/mutate_research_validate.py

A rule that cannot fail proves nothing. Each mutation below breaks
tools/research_validate.py in one deliberate way, at least one per rule, and
several break a READING rather than a whole rule (an OR clause read as AND),
because the reading is the specification (D17). Runs
tests/test_research_validate.py, prints which checks went red, restores the file
and compares its sha256. Exits non-zero if any mutation survives.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "research_validate.py"

MUTATIONS = (
    ("M1 R1: any project accepted whatever its kind",
     '"technology": lambda: kind_of.get(v) == "technology",\n                    "doctrine": lambda: kind_of.get(v) == "doctrine",',
     '"technology": lambda: v in kind_of,\n                    "doctrine": lambda: v in kind_of,'),
    ("M2 R1: variant_of not checked",
     'if parent is not None and kind_of.get(parent) != "design":',
     "if False:"),
    ("M3 R2: token-safety not checked",
     "            if not safe:\n",
     "            if False:\n"),
    ("M4 R2: bureau names unique globally, not per nation (reading)",
     'per_nation = collections.Counter((b["nation"], b["name"]) for b in self.bureaus)',
     'per_nation = collections.Counter(("all", b["name"]) for b in self.bureaus)'),
    ("M5 R3: cycles never reported",
     "                    if len(comp) > 1 or v in edges[v]:",
     "                    if False:"),
    ("M6 R4: an OR clause read as AND (reading)",
     "                if min(times) > mine:",
     "                if max(times) > mine:"),
    ("M7 R5: a Supplier may lead a design",
     'if b is not None and (b["nation"] != p["nation"] or b["role"] != "Lead"):',
     'if b is not None and b["nation"] != p["nation"]:'),
    ("M8 R6: only one direction checked",
     "            if vendor != (\"COND\" in p[\"tags\"]):",
     "            if vendor and \"COND\" not in p[\"tags\"]:"),
    ("M9 R7: symmetry not checked",
     'elif x in docs and pid not in docs[x]["exclusive_with"]:',
     "elif False:"),
    ("M10 R8: every id alternative must be held (OR read as AND, reading)",
     "                        if not set(ids) & held:",
     "                        if not set(ids) <= held:"),
    ("M11 R8: a non-id clause checked too (reading)",
     'ids = [a["value"] for a in clause if a["kind"] in ("technology", "doctrine")]\n                    if ids:',
     'ids = [a["value"] for a in clause if isinstance(a["value"], str)]\n                    if ids:'),
    ("M13 R12: an inactive lead accepted in the slice",
     'if not (lead["active"] and lead["nation"] == p["nation"] and lead["role"] == "Lead"):',
     'if not (lead["nation"] == p["nation"] and lead["role"] == "Lead"):'),
    ("M14 R12: an import counted as reachable in alpha (reading)",
     'a["kind"] not in ("seized", "captured")',
     'a["kind"] not in ("seized", "captured", "import", "licence")'),
    ("M15 R12: a model year that differs from its design accepted",
     "            if (m[\"year\"], m[\"year_declared\"]) != (p[\"year\"], p[\"year_declared\"]):",
     "            if False:"),
    ("M16 R10: the category DERIVED FROM THE ID PREFIX (the defect R10 exists to catch)",
     'c = cats.get(p["category"])',
     'c = cats.get("pb:" + p["authored_id"].split("-")[0].lower())'),
    ("M17 R10: a slice where no id disagrees passes (vacuity unchecked)",
     "        if techs and not disagree:",
     "        if False:"),
    ("M18 R11: an extra (prose) field on a project accepted",
     "            if extra:\n",
     "            if False:\n"),
    ("M19 R11: a prose string accepted as an effect",
     '            if isinstance(e, dict) and set(e) == {"no_target"}:   # declared',
     '            if isinstance(e, str):\n                continue\n'
     '            if isinstance(e, dict) and set(e) == {"no_target"}:   # declared'),
    ("M20 R11: gate_only and modifiers allowed to disagree",
     '            if e["gate_only"] == bool(e["modifiers"]):',
     "            if False:"),
    ("M21 R11: a zero modifier accepted",
     ' and m["per_mille"] != 0):',
     "):"),
    ("M22 R11: a no_target with no reason accepted",
     'and all(isinstance(n[k], str) and n[k].strip() for k in ("needs", "reason"))):',
     'and all(isinstance(n[k], str) for k in ("needs", "reason"))):'),
    ("M23 R11: a null effect in the subject accepted",
     '            if e is None:\n                self.fail("V111"',
     '            if False:\n                self.fail("V111"'),
    ("M24 R11: the subject derived from the effects (the tautology D37 forbids)",
     "        return subject & set(self.projects)",
     "        return {x for x in subject & set(self.projects) if self.projects[x][\"effect\"] is not None}"),
    ("M25 R11: target validity skipped",
     "                why = self.target_invalid(m)",
     "                why = None"),
    ("M26 R11: a stat accepted on any class (surface not checked)",
     '            if m["stat"] not in g["classes"][m["scope"]]:',
     "            if False:"),
    ("M27 R11: the subject rooted on a model's id, not its unlock (AD-M37 section 4)",
     ('        seen, stack = set(), [m["unlock"] for m in self.models\n'
      '                              if m["unlock"] in self.projects and self.projects[m["unlock"]]["kind"] == "design"]'),
     '        seen, stack = set(), [m["id"] for m in self.models if m["id"] in self.projects]'),
    ("M28 R11: the subject does not walk variant_of",
     ('            if p.get("variant_of"):\n'
      '                stack.append(p["variant_of"])\n'),
     ''),
    ("M29 R11: the subject omits the exclusive doctrines",
     '        subject |= {pid for pid, p in self.projects.items() if p["kind"] == "doctrine" and p["exclusive_with"]}\n',
     ''),
    ("M12 R9: lineage across nations accepted",
     'if parent["nation"] != p["nation"]:',
     "if False:"),
)


def main() -> int:
    orig = TARGET.read_bytes()
    h0 = hashlib.sha256(orig).hexdigest()
    print(f"target: {TARGET.relative_to(ROOT).as_posix()} sha256 {h0[:16]}")
    survivors = []
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        for label, old, new in MUTATIONS:
            src = orig.decode("utf-8")
            assert src.count(old) == 1, f"{label}: anchor {old!r} found {src.count(old)} times"
            TARGET.write_bytes(src.replace(old, new).encode("utf-8"))
            r = subprocess.run([sys.executable, str(ROOT / "tests" / "test_research_validate.py")],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
            red = [ln[8:].split(": ")[0] for ln in r.stdout.splitlines() if ln.startswith("  FAIL  ")]
            print(f"{label}: exit {r.returncode}, {len(red)} check(s) red")
            for name in red:
                print(f"      {name}")
            if r.returncode == 0:
                survivors.append(label)
    finally:
        TARGET.write_bytes(orig)
    restored = hashlib.sha256(TARGET.read_bytes()).hexdigest() == h0
    print(f"{len(MUTATIONS)} mutations, {len(MUTATIONS) - len(survivors)} killed"
          f"{'; SURVIVED: ' + ', '.join(survivors) if survivors else ''}; "
          f"restored byte-identical: {restored}")
    return 0 if restored and not survivors else 1


if __name__ == "__main__":
    sys.exit(main())
