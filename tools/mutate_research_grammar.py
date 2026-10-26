"""Mutation proofs for the research grammar parser (Sprint 13).

    uv run python tools/mutate_research_grammar.py

A test that cannot fail proves nothing. Each mutation below breaks
tools/research_grammar.py in one deliberate way, runs
tests/test_research_grammar.py, and prints which checks went red. The file is
restored afterwards and its sha256 compared with the original. Exits non-zero
if any mutation leaves the gate green or the restore does not match.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "tools" / "research_grammar.py"

# (label, exact text in research_grammar.py, its replacement), or
# (label, ((text, replacement), ...), None) for one defect spanning several sites.
MUTATIONS = (
    ("M1 naive split: parentheses ignored",
     '        if ch == "(":\n            depth += 1\n        elif ch == ")":\n            depth -= 1\n'
     '        elif ch == sep and depth == 0:',
     '        if ch == sep:'),
    ("M2 empty cell read as unsatisfiable",
     '    if cell is None or cell == "":\n        return ()',
     '    if cell is None or cell == "":\n        return ((),)'),
    ("M3 G104 empty-alternative check removed",
     '            if alt == "":\n                raise GrammarError("G104"',
     '            if False:\n                raise GrammarError("G104"'),
    ("M4 parser accepts everything",
     '    if context not in CONTEXT_PREFIXES:\n        raise ValueError(f"unknown context {context!r}")',
     '    return ()'),
    ("M5 bare bureau name resolved against every nation",
     '                if v not in registry.bureaus.get(nation, frozenset()):',
     '                if not registry.any_bureau(v):'),
    ("M6 pipe binds looser than comma (split order swapped)",
     'for raw_clause in _split_top(cell, ",")',
     'for raw_clause in _split_top(cell, "|")'),
    # One defect, four sites: whitespace read as ASCII space only.
    ("M7 whitespace is ASCII-only (U+00A0, U+3000 not trimmed or detected)", (
        ('    if cell.strip() == "":', '    if cell.strip(" ") == "":'),
        ('            alt = raw_alt.strip()', '            alt = raw_alt.strip(" ")'),
        ('        if arg[0].isspace():', '        if arg[0] == " ":'),
        ('_ID_FORBIDDEN = re.compile(r"[\\s():]")', '_ID_FORBIDDEN = re.compile(r"[ ():]")'),
    ), None),
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
            edits = old if new is None else ((old, new),)   # a mutation may edit several sites
            for anchor, replacement in edits:
                assert src.count(anchor) == 1, f"{label}: anchor {anchor!r} found {src.count(anchor)} times"
                src = src.replace(anchor, replacement)
            TARGET.write_bytes(src.encode("utf-8"))
            r = subprocess.run([sys.executable, str(ROOT / "tests" / "test_research_grammar.py")],
                               cwd=ROOT, capture_output=True, text=True, encoding="utf-8", env=env)
            red = [ln[8:].split("  [")[0] for ln in r.stdout.splitlines() if ln.startswith("  FAIL  ")]
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
