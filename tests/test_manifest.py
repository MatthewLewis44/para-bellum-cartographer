"""Gate for the cache snapshot manifest and its verifier (AD-040).

The manifest is only worth writing if the verifier can FAIL. These checks pin
that it does, and pin the two properties the design turns on:

  1. Round trip: a manifest written from recorded parts verifies clean.
  2. A part whose BYTES changed reports `changed` — and only that part.
  3. A deleted part reports `missing`.
  4. A part rewritten to identical bytes reports `match`. A refetch that
     produced the same data is not a change (content, not mtime).
  5. `--root` redirection verifies a manifest against a COPY of the cache, which
     is what makes it possible to test the verifier without touching the real
     one.
  6. The run-time hash memo is keyed on path+size+mtime, and `verify` does not
     consult it. A file whose bytes are replaced while its size and mtime are
     restored still reports `changed` — if the verifier trusted the memo it
     would report `match`, which is the exact failure the verifier exists to
     catch.
  7. A changed artifact reports `changed` even when every part still matches.

Usage: uv run python tests/test_manifest.py
Plain asserts (no pytest dependency); exits non-zero on failure.
"""

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wargame_cartographer import manifest as mf  # noqa: E402

failures: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  - {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def _fake_artifact(path: Path, marker: str = "a") -> None:
    doc = {
        "schema_version": "1.0.6",
        "map_metadata": {"generated_at": "2026-01-01T00:00:00Z", "hex_count": 2},
        "hexes": [{"id": "1_1", "note": marker}, {"id": "1_2", "note": marker}],
    }
    path.write_text(json.dumps(doc), "utf-8")


def _spec() -> SimpleNamespace:
    return SimpleNamespace(
        name="manifest_test",
        hex_size_km=10.0,
        river_scalerank_max=8,
        bbox=SimpleNamespace(min_lon=2.5, min_lat=49.5, max_lon=6.4, max_lat=51.5),
    )


def _status(rep: dict, needle: str) -> str:
    for p in rep["parts"]:
        if needle in p["path"]:
            return p["status"]
    return "<not-in-manifest>"


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="pb_manifest_"))
    try:
        cache = tmp / "cache"
        cache.mkdir()
        parts = {}
        for name, body in (("landuse_part_aaa.gpkg", b"LANDUSE-BYTES-1"),
                           ("roads_part_bbb.gpkg", b"ROADS-BYTES-1"),
                           ("railways_part_ccc.gpkg", b"RAILS-BYTES-1")):
            p = cache / name
            p.write_bytes(body)
            parts[name] = p

        # The recorder and the "osm" root both point at our temp cache, so the
        # real cache under ~/wargame-cartographer is never touched.
        real_roots = dict(mf._ROOTS)
        mf._ROOTS["osm"] = cache
        mf.HASH_CACHE_PATH = tmp / "content_hashes.json"
        mf._hash_cache = None

        artifact = tmp / "test_hex_terrain.json"
        _fake_artifact(artifact)

        mf.reset()
        for name, p in parts.items():
            mf.record_part(p, layer="osm:" + name.split("_")[0])
        man = mf.write_manifest(artifact, _spec(), Path("configs/para_bellum_belgium_test.yaml"),
                                run_path="monolithic")

        doc = json.loads(man.read_text("utf-8"))
        print("\n1. round trip")
        check("manifest written beside the artifact",
              man.name == "test_hex_terrain_manifest.json", man.name)
        check("all three parts recorded", len(doc["cache_parts"]) == 3,
              str(len(doc["cache_parts"])))
        check("parts carry sha256, bytes and fetch time",
              all(e["sha256"] and e["bytes"] and e["fetched_at"]
                  for e in doc["cache_parts"]))
        check("run records schema + streaming + config hash",
              bool(doc["run"]["schema_version"]) and "streaming_version" in doc["run"]
              and bool(doc["config"]["sha256"]))
        check("no absolute host paths in the manifest",
              str(Path.home()) not in man.read_text("utf-8"))
        check("hash_summary reports reuse",
              set(doc["hash_summary"]) >= {"hashed_this_run", "reused_from_hash_cache"})

        rep = mf.verify(man, artifact)
        check("verifier reports every part match and ok",
              rep["ok"] and rep["counts"] == {"match": 3, "changed": 0, "missing": 0},
              str(rep["counts"]))

        print("\n2. a part whose bytes changed")
        copy = tmp / "cache-copy"
        shutil.copytree(cache, copy)
        (copy / "roads_part_bbb.gpkg").write_bytes(b"ROADS-BYTES-2-TAMPERED")
        rep = mf.verify(man, artifact, {"osm": copy})
        check("exactly the altered part reports changed",
              rep["counts"] == {"match": 2, "changed": 1, "missing": 0}, str(rep["counts"]))
        check("the changed part is the one altered",
              _status(rep, "roads_part_bbb") == "changed")
        check("the others still report match",
              _status(rep, "landuse_part_aaa") == "match"
              and _status(rep, "railways_part_ccc") == "match")
        check("ok is False", rep["ok"] is False)

        print("\n3. a deleted part")
        copy2 = tmp / "cache-copy-2"
        shutil.copytree(cache, copy2)
        (copy2 / "railways_part_ccc.gpkg").unlink()
        rep = mf.verify(man, artifact, {"osm": copy2})
        check("the deleted part reports missing",
              _status(rep, "railways_part_ccc") == "missing")
        check("counts are 2 match / 1 missing",
              rep["counts"] == {"match": 2, "changed": 0, "missing": 1}, str(rep["counts"]))

        print("\n4. a rewrite to identical bytes is not a change")
        copy3 = tmp / "cache-copy-3"
        shutil.copytree(cache, copy3)
        target = copy3 / "landuse_part_aaa.gpkg"
        target.unlink()
        target.write_bytes(b"LANDUSE-BYTES-1")      # same content, new mtime
        os.utime(target, (0, 0))                     # and a wildly wrong one
        rep = mf.verify(man, artifact, {"osm": copy3})
        check("identical bytes, different mtime, still match", rep["ok"], str(rep["counts"]))

        print("\n5. the verifier does not trust the run-time hash memo")
        copy4 = tmp / "cache-copy-4"
        shutil.copytree(cache, copy4)
        victim = copy4 / "roads_part_bbb.gpkg"
        st = victim.stat()
        before, _ = mf.memoized_sha256(victim)       # this path is now memoized
        victim.write_bytes(b"ROADS-BYTES-X")         # same length as ROADS-BYTES-1
        os.utime(victim, ns=(st.st_atime_ns, st.st_mtime_ns))
        check("size and mtime restored",
              victim.stat().st_size == st.st_size
              and victim.stat().st_mtime_ns == st.st_mtime_ns)
        memo, reused = mf.memoized_sha256(victim)
        check("the memo answers with the stale hash of bytes that are gone",
              reused and memo == before and memo != mf.content_sha256(victim),
              "memo reuse is real, which is why verify must not use it")
        rep = mf.verify(man, artifact, {"osm": copy4})
        check("verify rehashes and reports changed anyway",
              _status(rep, "roads_part_bbb") == "changed")

        print("\n6. a changed artifact")
        _fake_artifact(artifact, marker="b")
        rep = mf.verify(man, artifact)
        check("artifact reports changed", rep["artifact"]["status"] == "changed",
              rep["artifact"]["status"])
        check("ok is False even though every part matches",
              rep["ok"] is False and rep["counts"]["match"] == 3)

        print("\n7. the CLI exits non-zero on a failure")
        rc_bad = mf.main(["verify", str(artifact)])
        _fake_artifact(artifact, marker="a")
        rc_good = mf.main(["verify", str(artifact)])
        check("exit 1 when something moved, 0 when nothing did",
              rc_bad == 1 and rc_good == 0, f"{rc_bad} / {rc_good}")

        mf._ROOTS.clear()
        mf._ROOTS.update(real_roots)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print()
    if failures:
        print(f"FAIL - {len(failures)} check(s): {failures}")
        return 1
    print("PASS - the manifest verifier detects change, loss and identity")
    return 0


if __name__ == "__main__":
    sys.exit(main())
