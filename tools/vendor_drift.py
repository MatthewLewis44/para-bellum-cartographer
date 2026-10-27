"""Compare a game commit's vendored content contract against a pipeline commit, EXACTLY.

    uv run python tools/vendor_drift.py <game-rev> <pipeline-rev> [--game-repo PATH]

Reads both sides as COMMITTED BLOBS (git ls-tree + git cat-file), never through git
archive or a working tree: both of those apply line-ending conversion, so their
bytes are not what the repositories hold. Compares, with no normalisation:

  pipeline content/schemas/            <->  game Assets/StreamingAssets/Content/schemas/
  pipeline tests/fixtures/content/     <->  game Assets/Tests/EditMode/Fixtures/content/

NOT compared, by design: the game's ContentSource/ (the research workbook, its
overlay and the design-id ledger). That folder is private SOURCE that the
converter reads, not content vendored from here, and no pipeline file mirrors it
(Sprint 13, D19). Comparing it would fail forever; leaking it would publish the
catalogue.

Unity's .meta sidecars are ignored. Prints both revisions it measured (D24), the file
counts on each side (so a comparison of nothing cannot pass, D41), and every
differing path. Exit code 0 if identical, 1 if not, 2 on a usage or git error.

Use it for a handover across branches: a game tip that vendors a pipeline BRANCH
(not this checkout's HEAD) is compared against that branch's commit, by name.
"""

from __future__ import annotations

import os
import subprocess
import sys

DEFAULT_GAME = os.environ.get("PB_UNITY_REPO", "C:/UnityProjects/ParaBellum")
PIPE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAIRS = (("content/schemas", "Assets/StreamingAssets/Content/schemas"),
         ("tests/fixtures/content", "Assets/Tests/EditMode/Fixtures/content"))
assert not any(game.startswith("ContentSource") for _, game in PAIRS), "ContentSource is private source, never a pair"


def _git(repo: str, *args: str) -> bytes:
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} in {repo}: {r.stderr.decode(errors='replace').strip()}")
    return r.stdout


def blobs(repo: str, rev: str, prefix: str) -> dict[str, bytes]:
    files = {}
    for line in _git(repo, "ls-tree", "-r", rev, "--", prefix).decode("utf-8").splitlines():
        meta, path = line.split("\t", 1)
        _, kind, sha = meta.split()
        if kind == "blob" and not path.endswith(".meta"):
            files[path[len(prefix):].lstrip("/")] = _git(repo, "cat-file", "blob", sha)
    return files


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    game_repo = DEFAULT_GAME
    if "--game-repo" in argv:
        game_repo = argv[argv.index("--game-repo") + 1]
        args = [a for a in args if a != game_repo]
    if len(args) != 2:
        print(__doc__)
        return 2
    game_rev, pipe_rev = args
    try:
        g_sha = _git(game_repo, "rev-parse", "--short", game_rev).decode().strip()
        p_sha = _git(PIPE, "rev-parse", "--short", pipe_rev).decode().strip()
        print(f"MEASURED  game {game_repo} @ {g_sha} ({game_rev})")
        print(f"MEASURED  pipeline {PIPE} @ {p_sha} ({pipe_rev})")
        print("METHOD    exact committed blobs (ls-tree + cat-file), no line-ending normalisation")
        failed = False
        for mine_p, game_p in PAIRS:
            mine, game = blobs(PIPE, pipe_rev, mine_p), blobs(game_repo, game_rev, game_p)
            diff = sorted(k for k in mine.keys() | game.keys() if mine.get(k) != game.get(k))
            if not mine or not game:
                print(f"FAIL      {mine_p}: an EMPTY side ({len(mine)} pipeline, {len(game)} game) "
                      f"cannot prove agreement")
                failed = True
                continue
            only_mine = sorted(set(mine) - set(game))
            only_game = sorted(set(game) - set(mine))
            status = "IDENTICAL" if not diff else f"{len(diff)} DIFFER"
            print(f"{'PASS' if not diff else 'FAIL':<9} {mine_p}: {len(mine)} pipeline / {len(game)} game "
                  f"files: {status}")
            for k in diff[:20]:
                why = "only in pipeline" if k in only_mine else "only in game" if k in only_game else "bytes differ"
                print(f"            {k}  ({why})")
            if len(diff) > 20:
                print(f"            ... and {len(diff) - 20} more")
            failed |= bool(diff)
        return 1 if failed else 0
    except RuntimeError as e:
        print(f"ERROR     {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
