"""The pre-push guard, against REAL pushes to a throwaway bare remote: a refused push must leave the remote without the commit.

    uv run python tests/test_prepush_guard.py

Each case is a fresh repository with one or more unpushed commits. Each rule has a case that goes red under its own mutant,
and the accepted cases go red if a rule refuses too much. Nothing here touches this repository or the game's.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "tools" / "prepush_guard.py"
SOURCE = GUARD.read_bytes().decode("utf-8")
ENV = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid", "PYTHONIOENCODING": "utf-8"}
ENV.pop("PB_ALLOW_NEW_TOP_LEVEL", None)
PRIVATE_TEXT = "A private planning note: not for a public repository.\n"


def run(*args, cwd, env=None, check=True):
    return subprocess.run(args, cwd=cwd, env=env or ENV, capture_output=True, text=True, encoding="utf-8", check=check)


def write(repo: Path, path: str, text: str):
    p = repo / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text.encode("utf-8"))


def commit(repo: Path, message: str):
    run("git", "add", "-A", cwd=repo)
    run("git", "commit", "-q", "-m", message, cwd=repo)


def push_case(root: Path, guard: Path, game: Path, build, *push_args, extra_env=None):
    """Returns (push accepted, the unpushed tip is on the remote, stderr)."""
    case = Path(tempfile.mkdtemp(dir=root))
    bare, repo = case / "remote.git", case / "repo"
    run("git", "init", "-q", "--bare", "-b", "main", str(bare), cwd=case)
    run("git", "init", "-q", "-b", "main", str(repo), cwd=case)
    run("git", "config", "core.autocrlf", "false", cwd=repo)
    write(repo, "README.md", "public\n")
    write(repo, "docs/guide.md", "public\n")
    commit(repo, "base")
    run("git", "remote", "add", "origin", str(bare), cwd=repo)
    run("git", "push", "-q", "origin", "main", cwd=repo)    # before the hook exists: the established public state
    hooks = case / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_bytes(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" -I "{guard.as_posix()}"\n'.encode("utf-8"))
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    run("git", "config", "core.hooksPath", hooks.as_posix(), cwd=repo)
    branch = build(repo)
    tip = run("git", "rev-parse", "HEAD", cwd=repo).stdout.strip()
    r = run("git", "push", "-q", "origin", branch, *push_args, cwd=repo, check=False,
            env={**ENV, "PB_UNITY_REPO": str(game), **(extra_env or {})})
    on_remote = run("git", "cat-file", "-t", tip, cwd=bare, check=False).returncode == 0
    return r.returncode == 0, on_remote, r.stderr


def main_branch(fn):
    def build(repo):
        fn(repo)
        return "main"
    return build


def new_branch(fn):
    def build(repo):
        run("git", "switch", "-q", "-c", "topic", cwd=repo)
        fn(repo)
        return "topic"
    return build


def adds(path, text="public\n"):
    def fn(repo):
        write(repo, path, text)
        commit(repo, "add")
    return fn


def added_then_deleted(repo):
    write(repo, "docs/CLOSE-sprint99.md", "x\n")
    commit(repo, "add")
    (repo / "docs/CLOSE-sprint99.md").unlink()
    commit(repo, "delete")


def renamed_private(repo):
    write(repo, "docs/innocent.md", PRIVATE_TEXT)
    commit(repo, "a copy under an innocent name")


def modifies_to_private(repo):
    write(repo, "docs/guide.md", PRIVATE_TEXT)
    commit(repo, "overwrite a public file with private bytes")


def delete_public(repo):
    (repo / "docs/guide.md").unlink()
    commit(repo, "delete")


# name, build, must the push be refused?, text the refusal must name, extra env
CASES = (
    ("a planning name in an allowed folder", main_branch(adds("docs/BRIEF-sprint99.md")), True, "docs/BRIEF-sprint99.md", None),
    ("private source by folder name", main_branch(adds("docs/ContentSource/x.json")), True, "docs/ContentSource/x.json", None),
    ("a workbook by extension", main_branch(adds("data/book.xlsx")), True, "data/book.xlsx", None),
    ("a new top level with an innocent name", main_branch(adds("notes/ideas.md")), True, "notes/ideas.md", None),
    ("an added-then-deleted file: it is still public", main_branch(added_then_deleted), True, "docs/CLOSE-sprint99.md", None),
    ("a private file's bytes under an innocent name", main_branch(renamed_private), True, "docs/innocent.md", None),
    ("a public file overwritten with private bytes", main_branch(modifies_to_private), True, "docs/guide.md", None),
    ("a NEW BRANCH carrying a planning name", new_branch(adds("docs/PROMPT-x.md")), True, "docs/PROMPT-x.md", None),
    ("the override names one top level, and another is still refused",
     main_branch(adds("notes/ideas.md")), True, "notes/ideas.md", {"PB_ALLOW_NEW_TOP_LEVEL": "other"}),
    ("CONTROL an ordinary file in an existing folder", main_branch(adds("docs/hex-notes.md")), False, "", None),
    ("CONTROL a word that only contains a planning word", main_branch(adds("docs/enclosed-planet-sources.md")), False, "", None),
    ("CONTROL an ordinary file on a new branch", new_branch(adds("docs/hex-notes.md")), False, "", None),
    ("CONTROL deleting a public file", main_branch(delete_public), False, "", None),
    ("CONTROL the override admits the top level it names", main_branch(adds("notes/ideas.md")), False, "", {"PB_ALLOW_NEW_TOP_LEVEL": "notes"}),
)

# One rule broken at a time, and exactly which cases must notice.
MUTANTS = (
    ("rule 1 off", 'if status == "A" and PRIVATE_NAME.search(path):', "if False:",
     {"a planning name in an allowed folder", "private source by folder name", "a NEW BRANCH carrying a planning name",
      "an added-then-deleted file: it is still public"}),
    ("rule 2 off", 'elif status == "A" and path.split("/")[0] not in allowed:', "elif False:",
     {"a new top level with an innocent name", "the override names one top level, and another is still refused"}),
    ("rule 3 off", "if private is not None and blob in private:", "if False:",
     {"a private file's bytes under an innocent name", "a public file overwritten with private bytes"}),
    ("only the tip commit is read", 'return git("rev-list", f"{remote}..{local}").split()', "return [local]",
     {"an added-then-deleted file: it is still public"}),
    ("a new branch reads nothing", 'return git("rev-list", local, "--not", "--remotes=origin").split()', "return []",
     {"a NEW BRANCH carrying a planning name"}),
    ("a warning instead of a refusal", 'print("Rewrite the unpushed commits so these never appear: a later commit deleting them leaves them public.", file=sys.stderr)\n    return 1',
     'return 0', "ALL_REFUSALS"),
    ("the name rule matches inside a word", r'r"(^|[/_.-])(sprint|brief|close|prompt|roadmap|directive|proposal|plan|handover|open-issues|state-after)([/_.\d-]|$)"',
     r'r"(sprint|brief|close|prompt|roadmap|directive|proposal|plan|handover|open-issues|state-after)"',
     {"CONTROL a word that only contains a planning word"}),
    ("the override admits everything", 'allowed |= {n.strip() for n in os.environ.get("PB_ALLOW_NEW_TOP_LEVEL", "").split(",") if n.strip()}',
     'allowed |= {"notes"} if os.environ.get("PB_ALLOW_NEW_TOP_LEVEL") else set()',
     {"the override names one top level, and another is still refused"}),
)


def make_game(root: Path) -> Path:
    game = root / "game"
    game.mkdir()
    run("git", "init", "-q", "-b", "main", str(game), cwd=root)
    run("git", "config", "core.autocrlf", "false", cwd=game)
    write(game, "docs/sprints/PLAN-x.md", PRIVATE_TEXT)
    commit(game, "private")
    return game


def run_all(root: Path, guard: Path, game: Path) -> dict[str, tuple[bool, bool, str]]:
    return {name: push_case(root, guard, game, build, extra_env=env) for name, build, _, _, env in CASES}


def wrong_cases(results) -> set[str]:
    bad = set()
    for name, _, refuse, names, _ in CASES:
        accepted, on_remote, err = results[name]
        ok = (not accepted and not on_remote and names in err) if refuse else (accepted and on_remote)
        if not ok:
            bad.add(name)
    return bad


def main() -> int:
    failures = []

    def check(label, cond, detail=""):
        print(f"  {'PASS' if cond else 'FAIL'}  {label}" + ("" if cond else f"  [{detail}]"))
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as t:
        root = Path(t)
        game = make_game(root)
        results = run_all(root, GUARD, game)
        for name, _, refuse, names, _ in CASES:
            accepted, on_remote, err = results[name]
            if refuse:
                check(f"{name}: refused, not on the remote, and names the path", not accepted and not on_remote and names in err, err)
            else:
                check(f"{name}: pushed", accepted and on_remote, err)
        accepted, on_remote, err = push_case(root, GUARD, root / "no-such-game", main_branch(adds("docs/hex-notes.md")))
        check("with no game repository the exact-copy rule SAYS it did not run", accepted and on_remote and "DID NOT RUN" in err, err)
        accepted, on_remote, err = push_case(root, GUARD, root / "no-such-game", main_branch(renamed_private))
        check("the stated limit is true: with no game repository, an exact copy under an innocent name passes", accepted and on_remote, err)

        refusals = {c[0] for c in CASES if c[2]}
        for label, old, new, want in MUTANTS:
            lf_source = SOURCE.replace("\r\n", "\n")
            assert lf_source.count(old) == 1, f"mutant anchor for {label!r} found {lf_source.count(old)} times"
            mutant = root / f"mutant{MUTANTS.index((label, old, new, want))}.py"
            mutant.write_bytes(lf_source.replace(old, new).encode("utf-8"))
            got = wrong_cases(run_all(root, mutant, game))
            expected = refusals if want == "ALL_REFUSALS" else want
            check(f"mutant: {label} is seen by exactly its cases", got == expected, f"{sorted(got)}")
    if failures:
        print(f"FAIL - {len(failures)}: {failures}")
        return 1
    print(f"PASS - {len(CASES)} cases against real pushes, {len(MUTANTS)} mutants")
    return 0


if __name__ == "__main__":
    sys.exit(main())
