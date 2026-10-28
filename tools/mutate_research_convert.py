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
     "            out.append({\"nation\": self.nations[x[\"nation\"]][0], \"treaty\": treaties[x[\"treaty\"]][\"id\"],",
     "            if lifted is None:\n                continue\n"
     "            out.append({\"nation\": self.nations[x[\"nation\"]][0], \"treaty\": treaties[x[\"treaty\"]][\"id\"],"),
    ("M15 a variant costs the full base",
     "                if p.get(\"variant_of\"):\n",
     "                if False:\n"),
    ("M16 an override labelled as the rule",
     'p.update(cost=o["cost"], cost_source="override", cost_reason=o["reason"])',
     'p.update(cost=o["cost"], cost_source="rule", cost_reason=None)'),
    ("M17 any currency string accepted",
     'if e["currency"] not in {"research", "doctrine"} | self.pools:',
     "if False:"),
    ("M18 an unreferenced flag passes without an inert declaration",
     "if fid not in used and f[\"id\"] not in inert:",
     "if False:"),
    ("M19 a declared treaty that nothing enforces passes",
     '            bad.add("X112", "overlay.treaties", t, "declared, but no restriction enforces it")',
     "            pass"),
    ("M20 the slice ignores whether the lead is active",
     'and lead_ok[p["lead"]] == (p["nation"], True)]',
     'and lead_ok[p["lead"]][0] == p["nation"]]'),
    ("M21 a parent outside the slice kept as supersedes",
     "                if sup not in ids:\n",
     "                if False:\n"),
    ("M22 a base design stamped 1930 instead of shipping year_declared base (D30)",
     '"year": p["year"], "year_declared": p["year_declared"],',
     '"year": p["year"] or 1930, "year_declared": None,'),
    ("M23 start designs read only base, dropping the dated-before-1930 clause",
     ' or (p["year"] is not None and p["year"] < START_YEAR))\n',
     ')\n'),
    ("M24 the no_target form refused",
     '    if set(v) == {"no_target"}:\n',
     '    if False:\n'),
    ("M25 a PAPER prototype counted as in service at the start",
     '\n                       and "PAPER" not in p["tags"]]',
     ']'),
    ("M26 a home's province not derived from the polygons",
     '"province": province_at(self.geo.get("provinces", []), h["lon"], h["lat"])}',
     '"province": None}'),
    ("M27 the province value covers every polygon, not only the SHIPPED provinces",
     '        comp = {pid: dict({"active_bureaus": 0}, **{k: 0 for k in kinds}) for pid in sorted(shipped)}',
     '        comp = {pid: dict({"active_bureaus": 0}, **{k: 0 for k in kinds})\n'
     '                for pid in sorted({q for q, _ in geo.get("provinces", [])})}'),
    ("M28 the AI weights' floor ignored (a category with no bureau weighs 0)",
     "weights[tag] = {c: max(floor, ",
     "weights[tag] = {c: max(0, "),
    ("M30 the AI weights' inputs not shipped (a moved weight could not be traced)",
     '            inputs[tag] = {"largest": largest, "bureaus": {c: counts[c] for c in live}}',
     '            inputs[tag] = {"largest": largest, "bureaus": {}}'),
    ("M29 an INACTIVE bureau's home counted in the province value",
     '            if not b["active"] or b["home"] is None:',
     '            if b["home"] is None:'),
    ("M31 a home's microdegrees truncated instead of rounded (0.500006 ships 500005)",
     '"lat_e6": round(h["lat"] * 1_000_000)',
     '"lat_e6": int(h["lat"] * 1_000_000)'),
    ("M32 a 7th decimal place silently rounded instead of refused",
     '              and all(round(h[k] * 1_000_000) / 1_000_000 == h[k] for k in ("lon", "lat")))',
     '              )'),
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
