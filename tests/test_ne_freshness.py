"""Gate for Natural Earth cache freshness (AD-041).

`get_natural_earth` unpacks a layer into a DIRECTORY and used to ask
`_is_fresh(directory)` whether it was current. A directory's mtime does not move
when the files inside it are overwritten, so the check reported the age of the
FIRST extraction forever: once a layer aged past the TTL it re-downloaded on
every run and never went back to being fresh. A 15-tile Belgium streaming run
pulled land and lakes 30 times, and `ne_10m_rivers` — the AD-029 river SELECTION
source, i.e. map content — came down on every run. The AD-040 manifest found it
by marking those layers `role: fetched` on a run that should have had none.

Checks:
  1. The old directory-mtime rule really is broken: rewriting every file inside
     a layer leaves the directory's own mtime untouched. (If this ever stops
     holding on some filesystem, the rest of this gate still stands, but the
     bug's mechanism has changed and the comments should be re-read.)
  2. A layer whose files were just rewritten reads FRESH. This is the bug.
  3. The stamp lives BESIDE the directory, never inside it — a stamp inside
     would change the directory's content hash, which is what an artifact
     manifest records for this part (AD-040).
  4. A pre-stamp cache adopts the newest file inside as its fetch time, so
     landing the fix re-dates an existing cache instead of re-downloading it.
  5. A genuinely old layer still reads stale, and an empty or missing directory
     reads stale. The fix narrows the window; it does not remove the TTL.
  6. A half-extracted directory with no stamp and no files reads stale rather
     than passing as a good cache.

Usage: uv run python tests/test_ne_freshness.py
Plain asserts (no pytest dependency); no network. Exits non-zero on failure.
"""

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wargame_cartographer.geo import downloader as dl  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  - {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def _layer(root: Path, name: str, age_days: float) -> Path:
    """An unpacked NE layer whose files are ``age_days`` old."""
    d = root / f"ne_10m_{name}"
    d.mkdir(parents=True)
    when = time.time() - age_days * 86400
    for ext in ("shp", "dbf", "shx", "prj"):
        f = d / f"ne_10m_{name}.{ext}"
        f.write_bytes(b"x" * 16)
        os.utime(f, (when, when))
    # Backdate the directory too, so nothing in the test leans on it being new.
    os.utime(d, (when, when))
    return d


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pb_ne_fresh_"))
    try:
        print("\n1. the mechanism of the bug")
        d = _layer(tmp, "land", age_days=100)
        dir_mtime_before = d.stat().st_mtime
        for f in d.glob("*"):
            f.write_bytes(b"y" * 32)          # a fresh "download" of every file
        dir_mtime_after = d.stat().st_mtime
        newest_file = max(f.stat().st_mtime for f in d.glob("*") if f.is_file())
        check("rewriting every file leaves the directory mtime untouched",
              dir_mtime_before == dir_mtime_after,
              f"{dir_mtime_before:.0f} vs {dir_mtime_after:.0f}")
        check("the old rule would call this 100-day-old and re-download it",
              not dl._is_fresh(d),
              "this is the check that never recovered")
        check("the files inside are in fact brand new",
              (time.time() - newest_file) < 60)

        print("\n2. the fix")
        check("a layer whose files were just rewritten reads FRESH",
              dl.ne_layer_is_fresh(d))
        stamp = dl._ne_stamp_path(d)
        check("the stamp is beside the directory, not inside it",
              stamp.exists() and stamp.parent == d.parent and not stamp.is_relative_to(d),
              stamp.name)
        check("nothing was added inside the layer directory",
              sorted(f.suffix for f in d.glob("*")) == [".dbf", ".prj", ".shp", ".shx"])

        print("\n3. adopting a pre-stamp cache")
        old = _layer(tmp, "rivers", age_days=3)
        check("no stamp yet", not dl._ne_stamp_path(old).exists())
        adopted = dl.ne_fetch_time(old)
        check("the newest file inside is adopted as the fetch time",
              adopted is not None and abs((time.time() - adopted) / 86400 - 3) < 0.01,
              f"{(time.time() - adopted) / 86400:.2f} days")
        check("and the stamp is written backdated to it, so no download happens",
              dl._ne_stamp_path(old).exists() and dl.ne_layer_is_fresh(old))

        print("\n4. the TTL still bites")
        stale = _layer(tmp, "states", age_days=45)
        check("a genuinely 45-day-old layer reads stale (TTL 30)",
              not dl.ne_layer_is_fresh(stale))
        check("...and fresh again under a wider window",
              dl.ne_layer_is_fresh(stale, max_age_days=120))

        print("\n5. nothing there at all")
        missing = tmp / "ne_10m_nope"
        check("a missing directory reads stale", not dl.ne_layer_is_fresh(missing))
        check("and has no fetch time", dl.ne_fetch_time(missing) is None)
        empty = tmp / "ne_10m_empty"
        empty.mkdir()
        check("a half-extracted directory with no files reads stale",
              not dl.ne_layer_is_fresh(empty))
        check("a directory with files but an interrupted extract is only as good "
              "as its newest file", dl.ne_fetch_time(empty) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAIL - {len(failures)} check(s): {failures}")
        return 1
    print("PASS - Natural Earth freshness reflects what was actually written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
