"""Gate for the pre-run cache guard (AD-042).

AD-040 made a lost cache part detectable, but only after the run that lost it:
the manifest is written at the end. This guard refuses to START a run that is
about to overwrite a part in place, or that is already standing on a cache that
moved. A guard that has only ever permitted has not been tested, so most of
what is below is about it REFUSING.

Checks, against `evaluate_cache` (the pure core: baseline in, problems out) and
`check_cache_before_run` (the wrapper that raises, formats and honours the
override):

  1. Clean cache, nothing near its TTL: no problems, and the run is permitted.
  2. A part whose BYTES moved: refused, and named.
  3. A deleted part: refused, and named.
  4. A part past the TTL in force: refused. This is the Sprint 10 disaster —
     the run that would silently refetch and overwrite.
  5. A part inside the expiry MARGIN but not yet past the TTL: still refused. A
     run takes hours; "not expired at the moment you typed the command" is not
     the question.
  6. The TTL is read PER RUN, so widening the window (the
     PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS pin) turns a refusal into a pass
     without touching a byte.
  7. A root with no TTL (the SRTM DEM, kept until deleted) never trips the
     expiry check however old it is.
  8. A part that has already CHANGED is reported as changed, not as expiring —
     one problem per part, and the one that matters.
  9. The override permits, and says loudly what it permitted.
 10. The refusal text names the part, the fault, and the override. A refusal
     nobody can act on gets the guard deleted.
 11. No blessed manifest: permitted, with a reason, and no baseline invented.

Usage: uv run python tests/test_cache_guard.py
Plain asserts (no pytest dependency); no network, no real cache. Exits non-zero
on failure.
"""

import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wargame_cartographer import manifest as mf  # noqa: E402

failures: list[str] = []
DAY = 86400.0


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  - {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def _kinds(problems: list[dict]) -> dict[str, str]:
    """{part path: kind} for easy assertions."""
    return {p["path"]: p["kind"] for p in problems}


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pb_guard_"))
    os.environ.pop(mf.GUARD_OVERRIDE_ENV, None)
    try:
        osm = tmp / "osm"
        osm.mkdir()
        dem_dir = tmp / "elevation"
        dem_dir.mkdir()

        parts = {
            "landuse_x.gpkg": (osm, b"LANDUSE-1", 10.0),
            "roads_x.gpkg": (osm, b"ROADS-1", 10.0),
            "bridges_x.gpkg": (osm, b"BRIDGES-1", 10.0),
        }
        now = time.time()
        for name, (root, body, age) in parts.items():
            p = root / name
            p.write_bytes(body)
            os.utime(p, (now - age * DAY, now - age * DAY))
        dem = dem_dir / "dem_x.tif"
        dem.write_bytes(b"DEM-1")
        os.utime(dem, (now - 900 * DAY, now - 900 * DAY))   # ancient on purpose

        def entry(root, name, path):
            return {"root": root, "path": name, "layer": f"{root}:{name.split('_')[0]}",
                    "sha256": mf.content_sha256(path), "bytes": path.stat().st_size}

        doc = {
            "generated_at": "2026-09-29T00:00:00Z",
            "artifact": {"file": "test_hex_terrain.json"},
            "cache_parts": [entry("osm", n, osm / n) for n in parts]
                           + [entry("elevation", "dem_x.tif", dem)],
        }
        roots = {"osm": osm, "elevation": dem_dir}
        TTL = {"osm": 30.0, "elevation": None}

        print("\n1. a clean cache")
        problems = mf.evaluate_cache(doc, root_overrides=roots, ttl_days=TTL)
        check("no problems", problems == [], str(_kinds(problems)))

        print("\n2. a part whose bytes moved")
        copy = tmp / "osm-changed"
        shutil.copytree(osm, copy)
        (copy / "bridges_x.gpkg").write_bytes(b"BRIDGES-2")
        problems = mf.evaluate_cache(
            doc, root_overrides={**roots, "osm": copy}, ttl_days=TTL)
        check("refused, and only that part", _kinds(problems) == {"bridges_x.gpkg": "changed"},
              str(_kinds(problems)))

        print("\n3. a deleted part")
        copy2 = tmp / "osm-missing"
        shutil.copytree(osm, copy2)
        (copy2 / "roads_x.gpkg").unlink()
        problems = mf.evaluate_cache(
            doc, root_overrides={**roots, "osm": copy2}, ttl_days=TTL)
        check("refused, and only that part", _kinds(problems) == {"roads_x.gpkg": "missing"},
              str(_kinds(problems)))

        print("\n4. a part past the TTL in force — the Sprint 10 disaster")
        problems = mf.evaluate_cache(doc, root_overrides=roots,
                                     ttl_days={"osm": 5.0, "elevation": None})
        check("every 10-day-old OSM part refused against a 5-day TTL",
              sorted(_kinds(problems).values()) == ["expiring"] * 3, str(_kinds(problems)))
        check("the DEM is not among them", "dem_x.tif" not in _kinds(problems))

        print("\n5. inside the margin but not yet expired")
        # 10 days old, TTL 10.5: not expired, but within EXPIRY_MARGIN_DAYS of it.
        problems = mf.evaluate_cache(doc, root_overrides=roots,
                                     ttl_days={"osm": 10.5, "elevation": None})
        check(f"refused inside the {mf.EXPIRY_MARGIN_DAYS:.0f}-day margin",
              sorted(_kinds(problems).values()) == ["expiring"] * 3, str(_kinds(problems)))
        problems = mf.evaluate_cache(doc, root_overrides=roots,
                                     ttl_days={"osm": 11.5, "elevation": None})
        check("permitted once the margin clears", problems == [], str(_kinds(problems)))

        print("\n6. widening the window is what the pin does")
        narrow = mf.evaluate_cache(doc, root_overrides=roots,
                                   ttl_days={"osm": 5.0, "elevation": None})
        wide = mf.evaluate_cache(doc, root_overrides=roots,
                                 ttl_days={"osm": 120.0, "elevation": None})
        check("refused at 5 days, permitted at 120, no byte touched",
              len(narrow) == 3 and wide == [])

        print("\n7. a root with no TTL")
        check("a 900-day-old DEM never expires",
              mf.evaluate_cache(doc, root_overrides=roots,
                                ttl_days={"osm": 120.0, "elevation": None}) == [])

        print("\n8. changed beats expiring")
        problems = mf.evaluate_cache(doc, root_overrides={**roots, "osm": copy},
                                     ttl_days={"osm": 5.0, "elevation": None})
        check("the moved part reports changed, not expiring",
              _kinds(problems)["bridges_x.gpkg"] == "changed", str(_kinds(problems)))
        check("the untouched parts still report expiring",
              _kinds(problems)["landuse_x.gpkg"] == "expiring")

        print("\n9. the refusal text")
        msg = mf._guard_message(
            mf.evaluate_cache(doc, root_overrides={**roots, "osm": copy},
                              ttl_days={"osm": 5.0, "elevation": None}),
            "HEAD:output/test_hex_terrain_manifest.json", doc)
        check("names the part", "bridges_x.gpkg" in msg)
        check("names the fault", "CHANGED" in msg and "EXPIRING" in msg)
        check("gives the override", mf.GUARD_OVERRIDE_ENV + "=1" in msg)
        check("gives the TTL pin for an OSM part",
              "PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS" in msg)
        check("says the overwrite is unrecoverable", "no way back" in msg)

        print("\n10. the wrapper: raise, override, and no baseline invented")
        spec = SimpleNamespace(name="guard_test", output_dir=tmp)
        artifact = mf.artifact_path_for_spec(spec)
        check("artifact path mirrors the export paths",
              artifact.name == "guard_test_hex_terrain.json", artifact.name)

        # No committed manifest for this made-up config: permitted, with a reason.
        said: list[str] = []
        res = mf.check_cache_before_run(spec, status=said.append)
        check("no blessed manifest: permitted", res["problems"] == [] and not res["checked"])
        check("...and it says so rather than inventing a baseline",
              any("no blessed manifest" in s for s in said), said[0] if said else "")

        # Now force the raising path with a baseline we control.
        real_blessed = mf.blessed_manifest
        mf.blessed_manifest = lambda _p: (doc, "TEST-BASELINE")
        real_eval = mf.evaluate_cache
        mf.evaluate_cache = lambda d, **kw: real_eval(
            d, root_overrides={**roots, "osm": copy},
            ttl_days={"osm": 5.0, "elevation": None})
        try:
            raised = None
            try:
                mf.check_cache_before_run(spec, status=lambda _m: None)
            except mf.CacheGuardError as e:
                raised = e
            check("a moved/expiring cache RAISES CacheGuardError", raised is not None)
            check("and the exception carries the actionable text",
                  raised is not None and mf.GUARD_OVERRIDE_ENV in str(raised))

            os.environ[mf.GUARD_OVERRIDE_ENV] = "1"
            said = []
            res = mf.check_cache_before_run(spec, status=said.append)
            check("the override permits", res.get("overridden") is True)
            check("...and reports what it permitted",
                  any("OVERRIDDEN" in s for s in said)
                  and any("3 problem(s)" in s for s in said),
                  " | ".join(s.splitlines()[0] for s in said if s))
            os.environ.pop(mf.GUARD_OVERRIDE_ENV, None)

            os.environ[mf.GUARD_OVERRIDE_ENV] = "yes please"
            raised = None
            try:
                mf.check_cache_before_run(spec, status=lambda _m: None)
            except mf.CacheGuardError as e:
                raised = e
            check("only the exact value '1' overrides", raised is not None)
        finally:
            os.environ.pop(mf.GUARD_OVERRIDE_ENV, None)
            mf.blessed_manifest = real_blessed
            mf.evaluate_cache = real_eval

        print("\n11. the blessed baseline comes from git, not the working tree")
        src = Path(mf.__file__).read_text("utf-8")
        check("blessed_manifest reads HEAD:", 'f"HEAD:{rel}"' in src)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAIL - {len(failures)} check(s): {failures}")
        return 1
    print("PASS - the guard refuses a run that would destroy or has lost a part")
    return 0


if __name__ == "__main__":
    sys.exit(main())
