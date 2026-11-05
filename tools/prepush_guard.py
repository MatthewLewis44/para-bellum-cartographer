"""Refuse a push that would publish private material. This repository is public, and a push cannot be taken back.

Run by tools/hooks/pre-push, which git feeds one line per ref on stdin. Install once per clone:

    git config core.hooksPath tools/hooks

It checks EVERY COMMIT being pushed, not only the tip, because a file added and then deleted is still public.

  1. A newly added path whose name reads as planning or as private source (PRIVATE_NAME).
  2. A new top-level entry that origin/main does not have. Most private material arrives this way, whatever it is
     called. A real one is named for that push: PB_ALLOW_NEW_TOP_LEVEL=name git push
  3. A file whose bytes equal a private file in the game repository (PB_UNITY_REPO), whatever it was renamed to.

WHAT THIS DOES NOT DO. Read this before trusting it:
  - It only runs if it is installed, and git push --no-verify skips it. GitHub enforces nothing here.
  - Rule 1 knows names, not contents: a planning document with an innocent name inside docs/ passes rule 1.
  - Rule 3 sees exact copies only: an edited or pasted copy passes. If the game repository is absent, rule 3 does not
    run, and this says so.
  - Rule 1 applies to ADDED paths. Files already tracked here are not judged, including old ones that may not belong.
So it replaces neither a person looking at what a push holds, nor care about what gets written here.

Exit 0 lets the push proceed. Exit 1 refuses it and names every offending path.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys

ZERO = "0" * 40
EMPTY_BLOB = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
PRIVATE_NAME = re.compile(
    r"(^|[/_.-])(sprint|brief|close|prompt|roadmap|directive|proposal|plan|handover|open-issues|state-after)([/_.\d-]|$)"
    r"|contentsource|design_ledger|research_database|\.xlsx$|(^|/)candidate-|(^|/)standin-",
    re.IGNORECASE)
# The game's private source, its documents, and its generated catalogue.
PRIVATE_IN_GAME = re.compile(r"^(ContentSource/|docs/|Assets/StreamingAssets/Content/pb/(research|bureau|vendor|design_model)\.json$)")


def git(*args: str, cwd: str | None = None) -> str:
    r = subprocess.run(["git", "--no-optional-locks", *args], capture_output=True, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout.decode("utf-8", "replace")


def commits_for(local: str, remote: str) -> list[str]:
    if remote == ZERO:   # a new branch: everything origin has not already got
        return git("rev-list", local, "--not", "--remotes=origin").split()
    return git("rev-list", f"{remote}..{local}").split()


def changes(commit: str) -> list[tuple[str, str, str]]:
    """(status, blob, path) for every file a commit touches. -m so a merge shows what it brought in."""
    fields = git("diff-tree", "-r", "-m", "--root", "--no-commit-id", "--no-renames", "--raw", "-z", commit).split("\0")
    out = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        parts = meta.split()
        out.append((parts[4][0], parts[3], path))
    return out


def private_blobs() -> dict[str, str] | None:
    """Blob id -> the private path it is. None if the game repository is not here to compare with."""
    game = os.environ.get("PB_UNITY_REPO", "C:/UnityProjects/ParaBellum")
    if not os.path.isdir(os.path.join(game, ".git")):
        return None
    out = {}
    for line in git("ls-tree", "-r", "HEAD", cwd=game).splitlines():
        meta, path = line.split("\t", 1)
        blob = meta.split()[2]
        if PRIVATE_IN_GAME.match(path) and blob != EMPTY_BLOB:
            out[blob] = path
    return out


def main() -> int:
    refs = [line.split() for line in sys.stdin.read().splitlines() if line.strip()]
    pushing = [(local, remote) for _, local, _, remote in refs if local != ZERO]
    if not pushing:
        return 0
    allowed = set(git("ls-tree", "--name-only", "refs/remotes/origin/main").splitlines())
    allowed |= {n.strip() for n in os.environ.get("PB_ALLOW_NEW_TOP_LEVEL", "").split(",") if n.strip()}
    private = private_blobs()
    if private is None:
        print("prepush_guard: the game repository is not here, so the exact-copy rule DID NOT RUN", file=sys.stderr)
    problems: dict[str, str] = {}
    for local, remote in pushing:
        for commit in commits_for(local, remote):
            for status, blob, path in changes(commit):
                where = f"{path} (commit {commit[:7]})"
                if status == "A" and PRIVATE_NAME.search(path):
                    problems[where] = "its name reads as planning or private source"
                elif status == "A" and path.split("/")[0] not in allowed:
                    problems[where] = f'"{path.split("/")[0]}" is a new top level; if it is meant to be public, say so with PB_ALLOW_NEW_TOP_LEVEL'
                if private is not None and blob in private:
                    problems[where] = f"its bytes are the private file {private[blob]}"
    if not problems:
        return 0
    print(f"prepush_guard: PUSH REFUSED. This repository is public, and {len(problems)} path(s) must not reach it:", file=sys.stderr)
    for where, why in sorted(problems.items()):
        print(f"  {where}: {why}", file=sys.stderr)
    print("Rewrite the unpushed commits so these never appear: a later commit deleting them leaves them public.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, IndexError) as e:   # a guard that cannot look must not wave a push through
        print(f"prepush_guard: PUSH REFUSED, the check itself failed: {e}", file=sys.stderr)
        sys.exit(1)
