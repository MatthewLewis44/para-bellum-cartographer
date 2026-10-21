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
  6. The hash pin (content/SHA256SUMS) matches the schemas, the rule-code register
     and the fixtures.
  2a. THE REGISTER IS DATA. content/schemas/rule_codes.json is the only source of
     the codes: the checker reads it, every code it emits is in it, and every
     fixture's rule name and python/csharp behaviour match it.
  7. DRIFT AGAINST THE GAME. If the game repository is present (PB_UNITY_REPO,
     default C:/UnityProjects/ParaBellum), its Content/schemas/ and its vendored
     hostile set (Assets/Tests/EditMode/Fixtures/content, .meta ignored) must
     equal ours byte for byte (line endings normalised), and its real Content/
     must pass the checker. Both trees are printed with their SHA and whether the
     working tree differs from it (D24), so a red result during a handover is
     readable as a pending handover rather than a broken instrument. If the game repository is absent, or has not vendored the
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
PENDING: dict[str, str] = {}   # every code in the register now has a fixture


def tree_of(path: Path, *scope: str) -> str:
    """D24: a tool that produces evidence prints the tree it measured. Returns
    '<repo> @ <short sha>' plus whether the measured paths differ from that commit,
    so red during a handover reads as a pending handover, not a broken instrument."""
    def git(*a):
        r = subprocess.run(["git", "-C", str(path), *a], capture_output=True, text=True, encoding="utf-8")
        return r.stdout.strip() if r.returncode == 0 else ""
    sha = git("rev-parse", "--short", "HEAD")
    if not sha:
        return f"{path} (not a git checkout)"
    dirty = git("status", "--porcelain", "--", *scope) if scope else git("status", "--porcelain")
    n = len([ln for ln in dirty.splitlines() if ln.strip()])
    state = "clean" if not n else f"WORKING TREE differs from HEAD in {n} path(s)"
    return f"{path} @ {sha} ({state}{' under ' + ', '.join(scope) if scope else ''})"


def _tree_files(root: Path) -> dict[str, bytes]:
    """Every file under root, keyed by relative path, line endings normalised.
    Unity's .meta sidecars are ignored: the game adds one beside every asset."""
    return {p.relative_to(root).as_posix(): _lf(p.read_bytes())
            for p in root.rglob("*") if p.is_file() and p.suffix != ".meta"}


def _lf(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def contract_files() -> list[Path]:
    """The files that are vendored into the game: every schema plus the rule-code register."""
    return sorted(SCHEMAS.glob("*.schema.json")) + [cc.RULE_CODES_FILE]


def pinned_files() -> list[Path]:
    files = contract_files()
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
            files = exp["file"] if isinstance(exp["file"], list) else [exp["file"]]
            check(f"{d.name}: refused with exactly PB-{exp['code']} in {', '.join(files)}",
                  got == [(exp["code"], f) for f in files], f"{[str(p) for p in cs.problems][:3]}")

    # 2a. The register (rule_codes.json) is sound, and is the checker's only source.
    codes = [c["code"] for c in cc.REGISTER]
    check("rule_codes.json: codes are unique and well-formed (C + 3 digits)",
          len(codes) == len(set(codes)) and all(len(c) == 4 and c[0] == "C" and c[1:].isdigit() for c in codes))
    check("rule_codes.json: every entry declares layer, python and csharp behaviour",
          all({"rule", "layer", "python", "csharp", "summary"} <= set(c) for c in cc.REGISTER))
    modes = {"refuse", "report", "accept", "n/a"}
    check("rule_codes.json: python/csharp are reader modes, and csharp_elsewhere names a layer, a mode and a status",
          all(c["python"] in modes and c["csharp"] in modes for c in cc.REGISTER)
          and all({"layer", "does", "status"} <= set(c["csharp_elsewhere"]) and c["csharp_elsewhere"]["does"] in modes
                  for c in cc.REGISTER if "csharp_elsewhere" in c))
    c801 = next(c for c in cc.REGISTER if c["code"] == "C801")
    check("C801: the C# READER accepts, the C# START STATE reports, and D38's line is carried",
          c801["csharp"] == "accept" and c801.get("csharp_elsewhere", {}).get("layer") == "start_state"
          and "C#'s synthetic test proves the mechanism; the pipeline gate proves the shipped base." in c801["summary"])
    check("the checker's RULES are exactly the register (no copy in code)",
          cc.RULES == {c["code"]: c["rule"] for c in cc.REGISTER})
    reg = {c["code"]: c for c in cc.REGISTER}
    bad_meta = []
    for d in dirs:
        exp = json.loads((d / "expected.json").read_text(encoding="utf-8"))
        if exp["code"] and (exp.get("rule") != reg[exp["code"]]["rule"]
                            or (not exp.get("declared_divergence")
                                and (exp["python"], exp["csharp"]) != (reg[exp["code"]]["python"],
                                                                       reg[exp["code"]]["csharp"]))):
            bad_meta.append(d.name)
    check("every fixture's rule name and python/csharp behaviour match the register", not bad_meta,
          f"{bad_meta}")
    emitted = {pr.code for d in dirs for pr in cc.ContentSet(d / "Content").problems}
    check("every code the checker emitted is in the register", emitted <= set(codes),
          f"{sorted(emitted - set(codes))}")

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
                     for p in FIXTURES.rglob("*") if p.is_file()
                     and not p.name.endswith((".resolved.txt", ".applied.txt"))}
        diff = sorted(k for k in fresh.keys() | committed.keys() if fresh.get(k) != committed.get(k))
        check("committed fixtures are exactly what the generator builds", not diff, f"{diff[:5]}")

    # 4. The schemas are valid 2020-12, and the modder-facing command accepts the control set.
    def cli(*args) -> bool:
        r = subprocess.run([sys.executable, "-m", "check_jsonschema", *args],
                           capture_output=True, text=True, encoding="utf-8")
        return r.returncode == 0
    check("every schema passes --check-metaschema", cli("--check-metaschema", *map(str, SCHEMAS.glob("*.schema.json"))))
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

    # 5b. Applied values (CONTENT_MODEL section 3's arithmetic): snapshots for the
    #     control set and the discriminating a001 fixture, the arithmetic's own
    #     invariants, and proof that a001 actually tells the right rule from the wrong one.
    for name in ("h000_valid", "a001_layer_arithmetic"):
        path = FIXTURES / f"{name}.applied.txt"
        got = cc.applied_snapshot(cc.ContentSet(FIXTURES / name / "Content").resolve())
        check(f"{name} applied values match the committed snapshot (C# must reproduce)",
              path.exists() and _lf(path.read_bytes()).decode("utf-8") == got,
              f"regenerate with: uv run python tools/content_check.py tests/fixtures/content/{name}/Content "
              f"--applied")
    a001 = cc.ContentSet(FIXTURES / "a001_layer_arithmetic" / "Content").resolve()["pb:light_tank"]["stats"]
    arm, rel = a001["armour_front_mm"], a001["reliability_per_mille"]
    check("traits SUM within a layer: armour 13 with +150 +150 resolves to 16", arm["value"] == 16, f"{arm['value']}")
    wrong = arm["base"] + sum(cc.mul_div_nonneg(arm["base"], d) for _, d in arm["modifiers"])
    check("a001 DISCRIMINATES: per-contribution rounding would give a different value",
          wrong != arm["value"], f"wrong-method {wrong} == right {arm['value']}")
    base_layer = rel["layers"][0]
    check("a layer summing below zero is held at 0 by a VISIBLE clamp source, and the value is 0",
          rel["value"] == 0 and base_layer["per_mille"] == 0
          and (cc.CLAMP_SOURCE, 400) in base_layer["sources"], f"{base_layer}")
    every = [L for d in a001.values() for L in d["layers"]]
    check("every layer equals 1000 + the sum of its sources, clamp included",
          all(L["per_mille"] == 1000 + sum(v for _, v in L["sources"]) for L in every))
    check("all five layers are present on every stat, in the signed order",
          all([L["layer"] for L in d["layers"]] == list(cc.LAYERS) for d in a001.values()))
    try:
        cc.mul_div_nonneg(-7, 500)
        refused = False
    except ValueError:
        refused = True
    check("a signed operand is refused rather than rounded (section 3 point 6: C# / and Python // "
          "agree only on non-negatives)", refused and cc.mul_div_nonneg(7, 500) == 3)

    # 5c. The power model now comes FROM facility.json. The former hand-mirror
    #     (CURRENT_POWER) is kept as a cross-check: it must equal the file, so a change
    #     to the file that nobody carried here fails rather than drifting.
    import build_facilities_1930 as bf
    ctl_fac = json.loads((FIXTURES / "h000_valid" / "Content" / "pb" / "facility.json").read_text(encoding="utf-8"))
    check("the power model read from the control set's facility.json equals the cross-check mirror",
          bf.PowerModel.from_facility_json(ctl_fac) == bf.CURRENT_POWER)
    check("the authored 1930 base balances under it (C801 holds for the shipped figures)",
          all(sv >= dv for sv, dv in bf.nation_power(bf.load_rows(), bf.CURRENT_POWER).values()))
    lin = bf.PowerModel.from_facility_json({**ctl_fac, "tier": {**ctl_fac["tier"], "step_per_mille": 0}})
    short = sorted(n for n, (sv, dv) in bf.nation_power(bf.load_rows(), lin).items() if sv < dv)
    check("a linear stack browns out the eight nations the coupling predicts",
          short == ["AUT", "BEL", "CSK", "DEU", "HUN", "LUX", "POL", "SAA"], f"{short}")

    # 6. The hash pin.
    check("content/SHA256SUMS matches the schemas and fixtures",
          PIN.exists() and _lf(PIN.read_bytes()).decode("utf-8") == pin_text(),
          "re-pin with: uv run python tests/test_content_checks.py --repin")

    # 7. Drift against the game's copy. Both trees are printed (D24).
    unity = Path(os.environ.get("PB_UNITY_REPO", "C:/UnityProjects/ParaBellum"))
    game_content = unity / "Assets" / "StreamingAssets" / "Content"
    game_schemas = game_content / "schemas"
    game_fixtures = unity / "Assets" / "Tests" / "EditMode" / "Fixtures" / "content"
    print(f"  TREE  measured (this repo): {tree_of(ROOT, 'content', 'tests/fixtures/content')}")
    if not unity.exists():
        print(f"  SKIP  !!! game repository not found at {unity} (set PB_UNITY_REPO): "
              f"NO DRIFT CHECK RAN. This is expected for an outside clone of the public repo.")
    elif not game_schemas.exists():
        print(f"  SKIP  !!! {game_schemas} does not exist yet: the schemas are not vendored into "
              f"the game. NO DRIFT CHECK RAN.")
    else:
        mine = {p.name: _lf(p.read_bytes()) for p in contract_files()}
        theirs = {p.name: _lf(p.read_bytes()) for p in game_schemas.iterdir() if p.is_file()
                  and (p.name.endswith(".schema.json") or p.name == cc.RULE_CODES_FILE.name)}
        print(f"  TREE  compared (the game): "
              f"{tree_of(unity, 'Assets/StreamingAssets/Content', 'Assets/Tests/EditMode/Fixtures/content')}")
        drift = sorted(k for k in mine.keys() | theirs.keys() if mine.get(k) != theirs.get(k))
        check("the game's Content/schemas/ (schemas + rule_codes.json) equals the pinned copy",
              not drift, f"{drift}")
        if game_fixtures.exists():
            mine_f, theirs_f = _tree_files(FIXTURES), _tree_files(game_fixtures)
            fdrift = sorted(k for k in mine_f.keys() | theirs_f.keys() if mine_f.get(k) != theirs_f.get(k))
            check("the game's vendored hostile set (EditMode Fixtures/content) equals ours",
                  not fdrift, f"{len(fdrift)} file(s) differ, e.g. {fdrift[:3]}")
        else:
            print(f"  SKIP  !!! {game_fixtures} does not exist: the hostile set is not vendored. "
                  f"NO FIXTURE DRIFT CHECK RAN.")
        game_fac = game_content / "pb" / "facility.json"
        if game_fac.exists():
            gm = bf.PowerModel.from_facility_json(json.loads(game_fac.read_text(encoding="utf-8")))
            check("the game's SHIPPED facility.json gives the same power model as the cross-check mirror",
                  gm == bf.CURRENT_POWER, f"{gm}")
        else:
            print(f"  SKIP  !!! {game_fac} does not exist yet: the shipped power model is not "
                  f"checked against the mirror. NO POWER CROSS-CHECK RAN.")
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
