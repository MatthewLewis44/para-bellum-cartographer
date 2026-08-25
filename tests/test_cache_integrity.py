"""Cache-integrity gate for AD-030 rule 1 (audit finding D1).

History: `_fetch_layer` used to log a warning on a failed sub-bbox fetch and
carry on, writing the merged full-bbox cache from the SUCCESSFUL parts only.
The next run's freshness check short-circuited on that merged cache before
parts were consulted, so one transient Overpass 429 during a cold fetch
yielded a map silently missing a sub-bbox of a layer for the whole 30-day
TTL. AD-030 fixed it; nothing pinned the fix. This is that pin.

Checks:
  1. merge=True with a failed part: the merged full-bbox cache is NOT written
     (so the failed part is retried next run instead of being masked).
  2. merge=True with a failed part still RETURNS the successful parts for the
     current run — the fix must not silently empty the layer.
  3. merge=False (the streaming path, ensure_parts) RAISES on a failed part
     rather than leaving an incomplete part set for the tile sampler to
     sample a hole out of.
  4. The all-parts-succeed path is unaffected: the merged cache IS written.
  5. Successful parts from a partially-failed run are still cached
     individually, so a re-run resumes instead of refetching everything.

Usage: uv run python tests/test_cache_integrity.py
Plain asserts (no pytest dependency); exits non-zero on failure.
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wargame_cartographer.config.map_spec import BoundingBox      # noqa: E402
from wargame_cartographer.geo import osm_downloader as od         # noqa: E402
from wargame_cartographer.geo.osm_downloader import (             # noqa: E402
    OSMDownloader, _bbox_hash, _split_bbox,
)

# Wide enough to split into several sub-bboxes (MAX_QUERY_EDGE_DEG = 2.2).
BBOX = BoundingBox(min_lon=5.0, max_lon=12.0, min_lat=48.0, max_lat=52.0)
LAYER = "landuse"

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def _node(lon: float, lat: float, nid: int) -> dict:
    return {"type": "node", "id": nid, "lat": lat, "lon": lon,
            "tags": {"landuse": "forest"}}


def _parse(el: dict) -> dict | None:
    from shapely.geometry import Point
    return {"geometry": Point(el["lon"], el["lat"]).buffer(0.01),
            "landuse_type": "forest"}


def run_case(fail_index: int | None, merge: bool, cache_dir: Path):
    """Fetch LAYER over BBOX, failing sub-bbox #fail_index (None = none fail).

    Returns (result_or_None, raised_exception_or_None).
    """
    subs = _split_bbox(BBOX)
    calls = {"n": 0}

    def fake_query(query, timeout=60):
        i = calls["n"]
        calls["n"] += 1
        if fail_index is not None and i == fail_index:
            raise RuntimeError("simulated Overpass 429 (all retries exhausted)")
        sub = subs[i]
        lon = (sub.min_lon + sub.max_lon) / 2
        lat = (sub.min_lat + sub.max_lat) / 2
        return {"elements": [_node(lon, lat, 1000 + i)]}

    real = od._overpass_query_retry
    real_delay = od.SUBQUERY_DELAY_S
    od._overpass_query_retry = fake_query
    od.SUBQUERY_DELAY_S = 0.0
    try:
        dl = OSMDownloader(cache_dir=cache_dir)
        try:
            res = dl._fetch_layer(
                BBOX, LAYER, lambda b: "q", _parse,
                columns=["landuse_type"], merge=merge,
            )
            return res, None
        except Exception as e:
            return None, e
    finally:
        od._overpass_query_retry = real
        od.SUBQUERY_DELAY_S = real_delay


def main() -> int:
    subs = _split_bbox(BBOX)
    print(f"AD-030 rule 1 — no merged cache from a partial fetch")
    print(f"bbox splits into {len(subs)} sub-bboxes\n")
    assert len(subs) > 1, "test bbox must split for this gate to mean anything"

    merged_name = f"{LAYER}_{_bbox_hash(BBOX)}.gpkg"

    # --- 1/2/5: merge=True, one part fails --------------------------------
    tmp = Path(tempfile.mkdtemp(prefix="pb_cache_fail_"))
    try:
        res, exc = run_case(fail_index=1, merge=True, cache_dir=tmp)
        check("merge=True with a failed part does not raise", exc is None,
              f"{type(exc).__name__ if exc else 'no exception'}")
        check("merged full-bbox cache is NOT written after a failed part",
              not (tmp / merged_name).exists(),
              f"{merged_name} " + ("EXISTS" if (tmp / merged_name).exists()
                                   else "absent"))
        check("the current run still gets the successful parts",
              res is not None and len(res) == len(subs) - 1,
              f"{0 if res is None else len(res)} features from "
              f"{len(subs) - 1} good parts")
        parts = sorted(p.name for p in tmp.glob(f"{LAYER}_part_*.gpkg"))
        check("successful parts ARE cached individually (re-run resumes)",
              len(parts) == len(subs) - 1, f"{len(parts)} part gpkgs")
        failed_part = tmp / f"{LAYER}_part_{_bbox_hash(subs[1])}.gpkg"
        check("the failed part left no cache file of its own",
              not failed_part.exists(), failed_part.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 3: merge=False (streaming / ensure_parts) must fail loud ---------
    print()
    tmp = Path(tempfile.mkdtemp(prefix="pb_cache_stream_"))
    try:
        res, exc = run_case(fail_index=1, merge=False, cache_dir=tmp)
        check("merge=False RAISES on a failed part (streaming fail-loud)",
              isinstance(exc, RuntimeError),
              f"{type(exc).__name__ if exc else 'no exception raised'}")
        check("the raised error names the layer and AD-030",
              exc is not None and LAYER in str(exc) and "AD-030" in str(exc),
              (str(exc)[:70] + "...") if exc else "")
        check("merge=False writes no merged cache either",
              not (tmp / merged_name).exists(), merged_name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- 4: the happy path still caches -----------------------------------
    print()
    tmp = Path(tempfile.mkdtemp(prefix="pb_cache_ok_"))
    try:
        res, exc = run_case(fail_index=None, merge=True, cache_dir=tmp)
        check("all parts succeed: no exception", exc is None,
              f"{type(exc).__name__ if exc else 'no exception'}")
        check("all parts succeed: merged cache IS written",
              (tmp / merged_name).exists(), merged_name)
        check("all parts succeed: every sub-bbox contributed",
              res is not None and len(res) == len(subs),
              f"{0 if res is None else len(res)} features from {len(subs)} parts")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAIL — {len(failures)} check(s): {failures}")
        return 1
    print("PASS — AD-030 cache-integrity invariants hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
