"""Gate for the content contract's second implementation (Sprint 12.5, T1-T4).

A validator nobody has watched fail is not known to work. This test runs the
Python content checker (tools/content_check.py) over the hostile content set and
asserts that every fixture is refused with EXACTLY its one stable code, in its
one file, and that the valid control set is accepted. The game's C# reader is
held to the same expected.json records, so the hostile set is itself the
cross-check: both implementations must agree on WHY each file is wrong.

  1. Every fixture under tests/fixtures/content/ has an expected.json, and the
     checker does what it says for Python: exactly one problem with the declared
     code in the declared file, or no problem for an `accept`.
  2. REGISTER COVERAGE. Every code in the rule register has a fixture, except
     those declared pending. Adding a rule without a fixture fails here.
  3. The committed fixtures are what the generator builds (no stale or
     hand-edited fixture): tools/build_content_fixtures.py is rebuilt into a
     temp directory and compared file by file.
  4. Each schema is itself valid JSON Schema 2020-12, and the control set passes
     the modder-facing command, `check-jsonschema`, file by file.
  5. The control set's derived values match the committed snapshot
     (h000_valid.resolved.txt), which the C# reader must also reproduce.
  6. The hash pin (content/SHA256SUMS) matches the schemas and fixtures.
  7. DRIFT AGAINST THE GAME. If the game repository is present (PB_UNITY_REPO,
     default C:/UnityProjects/ParaBellum), its Content/schemas/ must equal ours
     byte for byte (line endings normalised), and its real Content/ must pass
     the checker. If the game repository is absent, or has not vendored the
     schemas yet, this prints a LOUD SKIP rather than a silent pass.

Run: uv run python tests/test_content_checks.py
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import build_content_fixtures as bcf  # noqa: E402
import content_check as cc  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "content"
SCHEMAS = ROOT / "content" / "schemas"
PIN = ROOT / "content" / "SHA256SUMS"
SNAPSHOT = FIXTURES / "h000_valid.resolved.txt"
# Codes with a register entry but no fixture yet, and why. Anything else
# uncovered fails check 2.
PENDING = {"C801": "base-browns-out needs facilities.json, whose shape is not yet in the contract"}


def _lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def pinned_files() -> list[Path]:
    files = sorted(SCHEMAS.glob("*.schema.json"))
    files += sorted(p for p in FIXTURES.rglob("*") if p.is_file())
    return files


def pin_text() -> str:
    return "".join(f"{hashlib.sha256(_lf(p.read_bytes())).hexdigest()}  {p.relative_to(ROOT).as_posix()}\n"
                   for p in pinned_files())


def main() -> int:
    failures: list[str] = []

    def check(label, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  [{detail}]"))
        if not cond:
            failures.append(label)

    # 1. Every fixture does what its expected.json says, for Python.
    dirs = sorted(p for p in FIXTURES.iterdir() if p.is_dir())
    covered = set()
    for d in dirs:
        exp_path = d / "expected.json"
        if not exp_path.exists():
            check(f"{d.name} has an expected.json", False, "missing")
            continue
        exp = json.loads(exp_path.read_text(encoding="utf-8"))
        cs = cc.ContentSet(d / "Content")
        got = [(p.code, p.file) for p in cs.problems]
        if exp["code"]:
            covered.add(exp["code"])
        if exp["python"] == "accept":
            check(f"{d.name}: accepted", not got, f"{[str(p) for p in cs.problems][:3]}")
        else:
            check(f"{d.name}: refused with exactly PB-{exp['code']} in {exp['file']}",
                  got == [(exp["code"], exp["file"])], f"{[str(p) for p in cs.problems][:3]}")

    # 2. Register coverage.
    uncovered = sorted(set(cc.RULES) - covered - set(PENDING))
    check("every rule-register code has a fixture (or is declared pending)", not uncovered, f"{uncovered}")
    for code, why in PENDING.items():
        print(f"  NOTE  PB-{code} {cc.RULES[code]} has no fixture yet: {why}")

    # 3. Committed fixtures equal a fresh build.
    with tempfile.TemporaryDirectory() as t:
        built_root = Path(t)
        bcf.build(built_root)
        fresh = {p.relative_to(built_root).as_posix(): _lf(p.read_bytes())
                 for p in built_root.rglob("*") if p.is_file()}
        committed = {p.relative_to(FIXTURES).as_posix(): _lf(p.read_bytes())
                     for p in FIXTURES.rglob("*") if p.is_file() and p != SNAPSHOT}
        diff = sorted(k for k in fresh.keys() | committed.keys() if fresh.get(k) != committed.get(k))
        check("committed fixtures are exactly what the generator builds", not diff, f"{diff[:5]}")

    # 4. The schemas are valid 2020-12, and the modder-facing command accepts the control set.
    def cli(*args) -> bool:
        r = subprocess.run([sys.executable, "-m", "check_jsonschema", *args],
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode == 0
    check("every schema passes --check-metaschema", cli("--check-metaschema", *map(str, SCHEMAS.glob("*.json"))))
    content = FIXTURES / "h000_valid" / "Content"
    ok = cli("--schemafile", str(SCHEMAS / "vocabulary.schema.json"), str(content / "vocabulary.json"))
    for stem, _ in cc.TYPES:
        ok &= cli("--schemafile", str(SCHEMAS / f"{stem}.schema.json"), str(content / "pb" / f"{stem}.json"))
    check("check-jsonschema accepts the control set, file by file", ok)
    bad = FIXTURES / "h101_missing_key" / "Content" / "pb" / "model.json"
    check("check-jsonschema REFUSES the missing-key fixture",
          not cli("--schemafile", str(SCHEMAS / "model.schema.json"), str(bad)))

    # 5. Derived values match the committed snapshot.
    snap = cc.snapshot(cc.ContentSet(content).resolve())
    check("control set resolves to the committed snapshot (the values C# must reproduce)",
          SNAPSHOT.exists() and _lf(SNAPSHOT.read_bytes()).decode("utf-8") == snap,
          "regenerate with: uv run python tools/content_check.py "
          "tests/fixtures/content/h000_valid/Content --resolve")

    # 6. The hash pin.
    check("content/SHA256SUMS matches the schemas and fixtures",
          PIN.exists() and _lf(PIN.read_bytes()).decode("utf-8") == pin_text(),
          "re-pin with: uv run python tests/test_content_checks.py --repin")

    # 7. Drift against the game's copy.
    unity = Path(os.environ.get("PB_UNITY_REPO", "C:/UnityProjects/ParaBellum"))
    game_content = unity / "Assets" / "StreamingAssets" / "Content"
    game_schemas = game_content / "schemas"
    if not unity.exists():
        print(f"  SKIP  !!! game repository not found at {unity} (set PB_UNITY_REPO): "
              f"NO DRIFT CHECK RAN. This is expected for an outside clone of the public repo.")
    elif not game_schemas.exists():
        print(f"  SKIP  !!! {game_schemas} does not exist yet: the schemas are not vendored into "
              f"the game. NO DRIFT CHECK RAN.")
    else:
        mine = {p.name: _lf(p.read_bytes()) for p in SCHEMAS.glob("*.schema.json")}
        theirs = {p.name: _lf(p.read_bytes()) for p in game_schemas.glob("*.schema.json")}
        drift = sorted(k for k in mine.keys() | theirs.keys() if mine.get(k) != theirs.get(k))
        check("the game's Content/schemas/ equals the pinned copy", not drift, f"{drift}")
        gcs = cc.ContentSet(game_content)
        check("the game's real Content/ passes the checker", not gcs.problems,
              f"{[str(p) for p in gcs.problems][:3]}")

    print()
    if failures:
        print(f"FAIL - {len(failures)} check(s): {failures}")
        return 1
    print(f"PASS - {len(dirs)} fixtures, every refusal fires with its code")
    return 0


if __name__ == "__main__":
    if "--repin" in sys.argv:
        PIN.write_text(pin_text(), encoding="utf-8", newline="\n")
        print(f"pinned {len(pinned_files())} files to {PIN.relative_to(ROOT)}")
        sys.exit(0)
    sys.exit(main())
