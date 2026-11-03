"""Mutation proofs for the population converter (Sprint 14).

    uv run python tools/mutate_population_convert.py

Breaks tools/population_convert.py one way at a time and names the EXACT checks of
tests/test_population_convert.py that must go red, so a mutant that turns a neighbour red as well, or misses
its own, fails the run. Restores the file and compares its sha256. Exits non-zero on any mismatch.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "population_convert.py"

MUTATIONS = (
    ("M1 X201: a map province may lack a census row", 'for pid in sorted(set(provinces) - seen):', "for pid in []:",
     {"a map province with no census row: refused with exactly X201"}),
    ("M2 X202: a census row may name a province not on the map",
     'bad.add("X202", pid, "is in the census table but not on this map: remove the row, or correct the id")\n', "pass\n",
     {"a census row for a province not on the map: refused with exactly X202"}),
    ("M3 X203: a province may have two rows", 'if pid in seen:', "if False:",
     {"a province with two census rows: refused with exactly X203"}),
    ("M4 X203: a nation may have two growth rows", 'if n in rated:', "if False:",
     {"a nation with two growth rows: refused with exactly X203"}),
    ("M5 X204: an empty figure accepted", 'if not fig:\n', "if False:\n",
     {"an empty census figure: refused with exactly X204"}),
    ("M6 X205: a census figure with a separator is read by stripping it",
     'elif not fig.isdigit() or int(fig) < 1:', 'elif not fig.replace(",", "").isdigit():',
     {"a census figure with a separator: refused with exactly X205"}),
    ("M7 X205: a growth rate may carry a decimal point", 'elif not v.isdigit():', 'elif not v.replace(".", "").isdigit():',
     {"a growth rate with a decimal point: refused with exactly X205"}),
    ("M8 X206: a nation may lack a growth row", 'for n in sorted({p["nation"] for p in provinces.values()} - rated):', "for n in []:",
     {"a nation with no growth row: refused with exactly X206"}),
    ("M9 X207: a province may lack a polygon",
     'bad.add("X207", pid, f"has no polygon in {POLYGONS.name}, so its on-map share cannot be measured: add one")\n', "pass\n",
     {"a province with no polygon: refused with exactly X207"}),
    ("M10 X208: a province may be left with nobody", 'if people < 1:', "if False:",
     {"a province wholly outside the frame: refused with exactly X208"}),
    ("M11 the frame ignored: a cut province keeps its whole census figure", 'people = figures[pid] * share // SHARE_SCALE', "people = figures[pid]",
     {"a province the frame cuts in half keeps very nearly half: within 0.1% of 500,000",
      "urban share is against the ON-MAP people: 100,000 of about 500,000 is about 200",
      "a province wholly outside the frame: refused with exactly X208"}),
    ("M12 urban counts every settlement type", 'if s.get("type") in URBAN_TYPES:', "if True:",
     {"urban share counts town-or-larger only: 200,000 of 800,000 is 250, the 'none' settlement ignored"}),
    ("M13 urban not held at 1000", 'urban = min(1000, provinces[pid]["urban"] * 1000 // people)', 'urban = provinces[pid]["urban"] * 1000 // people',
     {"urban share is held at 1000 when the modern tag exceeds the 1930 people"}),
    ("M14 a hex with no province makes a row", 'if not pr:', "if False:",
     {"a hex with no province makes no row and no refusal"}),
)


def reds() -> set[str]:
    r = subprocess.run([sys.executable, str(ROOT / "tests" / "test_population_convert.py")], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    if "Traceback" in r.stderr:
        return {"CRASH: " + r.stderr.strip().splitlines()[-1]}
    return {ln[8:].split("  [")[0] for ln in r.stdout.splitlines() if ln.startswith("  FAIL  ")}


def main() -> int:
    wrong = []
    base = reds()
    print(f"control, nothing mutated: {len(base)} red")
    if base:
        wrong.append("control")
    orig = TARGET.read_bytes()
    h0 = hashlib.sha256(orig).hexdigest()
    src = orig.decode("utf-8").replace("\r\n", "\n")
    try:
        for label, old, new, want in MUTATIONS:
            assert src.count(old) == 1, f"{label}: anchor found {src.count(old)} times"
            TARGET.write_bytes(src.replace(old, new).encode("utf-8"))
            got = reds()
            ok = got == want
            print(f"{'exact' if ok else 'WRONG'}  {label}" + ("" if ok else f": {sorted(got)}"))
            if not ok:
                wrong.append(label)
    finally:
        TARGET.write_bytes(orig)
    restored = hashlib.sha256(TARGET.read_bytes()).hexdigest() == h0
    print(f"{len(MUTATIONS)} mutations; wrong: {len(wrong)}; restored byte-identical: {restored}")
    return 0 if restored and not wrong else 1


if __name__ == "__main__":
    sys.exit(main())
