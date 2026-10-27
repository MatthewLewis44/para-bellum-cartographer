"""Mutation proofs for the research workbook converter (Sprint 13).

    uv run python tools/mutate_research_convert.py

A test that cannot fail proves nothing. Each mutation below breaks
tools/research_convert.py in one deliberate way, runs
tests/test_research_convert.py, and prints which checks went red. The file is
restored afterwards and its sha256 compared with the original. Exits non-zero
if any mutation leaves the gate green or the restore does not match.

M1 is R10 at the converter: the category derived from the id prefix instead of
read from the category column. It must fail on the folded row, whose prefix and
category disagree by design (AD-M37 section 4).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "research_convert.py"

# (label, exact text in research_convert.py, its replacement), or
# (label, ((text, replacement), ...), None) for one defect spanning several sites.
MUTATIONS = (
    ("M1 R10: category derived from the id prefix",
     '"category": cat(r["Cat."], f"{where} Cat."),',
     '"category": qualify(r["ID"].split("-")[0]),'),
    ("M2 atom kind derived from the id prefix",
     '"technology" if v in self.tech_ids else "doctrine"',
     '"technology" if v[:3] in {t[:3] for t in self.tech_ids} else "doctrine"'),
    ("M3 the category fold ignored",
     "return qualify(fold.get(code, code))",
     "return qualify(code)"),
    ("M4 an undeclared year coerced instead of refused",
     '    bad.add("X103", where, value, f"expected an integer year or {BASE!r}")\n'
     '    return {"year": None, "year_declared": None}',
     '    return {"year": 1930, "year_declared": None}'),
    ("M5 a boolean accepted as a year",
     "if isinstance(value, int) and not isinstance(value, bool):",
     "if isinstance(value, int):"),
    ("M6 the ledger rewritten instead of appended to",
     'self.entries[section].append({"id": new, "name": name})',
     'self.entries[section].insert(0, {"id": new, "name": name})'),
    ("M7 a name minted without --mint",
     "if got is None and mint:",
     "if got is None:"),
    ("M8 an active bureau's missing capacity defaulted",
     "if active and not (isinstance(capacity, int)",
     "if False and not (isinstance(capacity, int)"),
    ("M9 interior whitespace normalised in a shipped name",
     '"kind": "design", "name": r["Design"],',
     '"kind": "design", "name": " ".join(r["Design"].split()),'),
    ("M10 the lead taken from the last bid",
     'lead = acq[0][0]["value"] if acq and acq[0][0]["kind"] == "bureau" else None',
     'lead = acq[0][-1]["value"] if acq and acq[0][-1]["kind"] == "bureau" else None'),
    ("M11 prose shipped (design notes)",
     '"tags": code_list(r["Tags"]',
     '"notes": r.get("Notes"), "tags": code_list(r["Tags"]'),
    ("M13 the design role vocabulary left open",
     'if r["Type / role"] not in declared:',
     "if False:"),
    ("M14 a restriction with no covert route dropped as if nothing restricted",
     "            out.append({\"nation\": self.nations[x[\"nation\"]][0], \"scope\": scope,",
     "            if lifted is None:\n                continue\n"
     "            out.append({\"nation\": self.nations[x[\"nation\"]][0], \"scope\": scope,"),
    ("M12 a refused run still writes its files",
     "    if bad.items:\n        print(f\"REFUSED",
     "    if bad.items and not files:\n        print(f\"REFUSED"),
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
            edits = old if new is None else ((old, new),)
            for anchor, replacement in edits:
                assert src.count(anchor) == 1, f"{label}: anchor {anchor!r} found {src.count(anchor)} times"
                src = src.replace(anchor, replacement)
            TARGET.write_bytes(src.encode("utf-8"))
            r = subprocess.run([sys.executable, str(ROOT / "tests" / "test_research_convert.py")],
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
