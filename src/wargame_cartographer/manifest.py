"""Cache snapshot manifest — what an exported artifact was actually built from.

The problem this exists for is in the `osm-cache` skill: a cached OSM part that
ages past the TTL is **refetched and overwritten in place**, with no archive.
The snapshot behind every artifact already shipped from that part is then gone,
and nothing on disk records that it ever existed. That has cost this project
provenance twice.

This module does not prevent the loss — it makes it **detectable**. After a
successful export the pipeline writes `<artifact-stem>_manifest.json` beside the
artifact, recording:

  * every cache part the run consumed — path, content sha256, size, fetch time;
  * the pipeline version and commit, the config file and its hash, the resolved
    bbox, `STREAMING_VERSION`, `SCHEMA_VERSION` and the exporter's source hash;
  * the artifact's own raw and content sha256, via `tools/artifact_hash.py`
    (the one hasher — this module does not write a second one).

and the manifest is **checkable**:

    uv run python -m wargame_cartographer.manifest verify \\
        output/para_bellum_belgium_test_hex_terrain.json

which reports every part as `match`, `changed` or `missing` and exits non-zero
if any part is not `match`. A manifest nobody can check is a receipt, not
evidence.

Two rules worth knowing before reading the code:

  * **Content, not mtime.** A refetch that produced identical bytes is not a
    change. Part identity is a sha256 of the bytes.
  * **The run may reuse a hash; the verifier never may.** Hashing ~1 GB of
    parts on every run would dominate a 75-second Belgium run, so
    `write_manifest` memoizes hashes in `~/wargame-cartographer/cache/
    content_hashes.json`, keyed by path + size + mtime, and records per part
    whether the hash was reused (`hash_reused`). `verify` deliberately ignores
    that cache and rehashes from disk: a mtime-keyed shortcut is exactly the
    assumption the verifier exists to test.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_VERSION = "1"

_HOME_CACHE = Path.home() / "wargame-cartographer" / "cache"
HASH_CACHE_PATH = _HOME_CACHE / "content_hashes.json"

# Named cache roots. Parts are stored in the manifest relative to one of these,
# so a manifest carries no absolute paths and `verify --root osm=<dir>` can be
# pointed at a copy of the cache. Anything outside them is stored under the
# pseudo-root "abs".
_ROOTS: dict[str, Path] = {
    "osm": _HOME_CACHE / "osm_pb",
    "elevation": _HOME_CACHE / "elevation",
    "vector": _HOME_CACHE / "vector",
    "osm_upstream": _HOME_CACHE / "osm",
    "boundaries": _HOME_CACHE / "boundaries",
}

_READ_CHUNK = 1 << 20


# ---------------------------------------------------------------------------
# The recorder — what the run consumed
# ---------------------------------------------------------------------------
#
# Process-global on purpose: a pipeline run is one process and the cache reads
# happen four modules deep (osm_downloader, elevation, downloader). Threading a
# recorder object through those signatures would be a larger change to the
# pipeline than the feature is worth.

_recorded: dict[str, dict] = {}


def reset() -> None:
    """Forget everything recorded so far. Called at the start of a run."""
    _recorded.clear()


def record_part(path, *, layer: str, role: str = "cache_hit") -> None:
    """Note that the run consumed ``path`` (a file, or a directory of them).

    ``role`` is ``cache_hit`` (served from cache) or ``fetched`` (written by
    this run). A path seen both ways in one run is recorded as ``fetched``.

    Never raises: a bookkeeping failure must not take down a pipeline run.
    """
    try:
        key = str(Path(path).resolve())
        prev = _recorded.get(key)
        if prev is None:
            _recorded[key] = {"layer": layer, "role": role, "order": len(_recorded)}
        elif role == "fetched":
            prev["role"] = "fetched"
    except Exception:  # pragma: no cover - defensive
        pass


def recorded_parts() -> list[tuple[Path, dict]]:
    """Recorded parts in first-seen order."""
    items = sorted(_recorded.items(), key=lambda kv: kv[1]["order"])
    return [(Path(k), dict(v)) for k, v in items]


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _portable(p: Path) -> str:
    """Absolute path as a POSIX string, with $HOME collapsed to ``~``.

    Keeps the username out of a manifest that lives in a public repository, and
    makes a manifest readable on another machine.
    """
    p = Path(p)
    try:
        return "~/" + p.resolve().relative_to(Path.home()).as_posix()
    except Exception:
        return p.as_posix()


def _split_root(p: Path) -> tuple[str, str]:
    """(root name, path relative to that root) for a consumed part."""
    p = p.resolve()
    for name, root in _ROOTS.items():
        try:
            return name, p.relative_to(root.resolve()).as_posix()
        except Exception:
            continue
    return "abs", _portable(p)


def _resolve(root: str, rel: str, overrides: dict[str, Path]) -> Path:
    if root == "abs":
        return Path(rel.replace("~", str(Path.home()), 1) if rel.startswith("~") else rel)
    base = overrides.get(root) or _ROOTS.get(root)
    if base is None:
        raise ValueError(f"manifest references unknown cache root {root!r}")
    return Path(base) / rel


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------

def _sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(_READ_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_dir(d: Path) -> str:
    """Hash a directory: each file's relative path and content, in sorted order.

    Natural Earth layers are cached as an unpacked shapefile directory — .shp
    alone would miss the .dbf attributes the pipeline reads.
    """
    h = hashlib.sha256()
    for f in sorted(x for x in d.rglob("*") if x.is_file()):
        h.update(f.relative_to(d).as_posix().encode("utf-8"))
        h.update(b"\0")
        h.update(_sha256_file(f).encode("ascii"))
        h.update(b"\0")
    return h.hexdigest()


def _stat(p: Path) -> tuple[int, int]:
    """(size in bytes, mtime in ns). For a directory: total size, newest mtime."""
    if p.is_dir():
        size = 0
        mtime = 0
        for f in p.rglob("*"):
            if f.is_file():
                st = f.stat()
                size += st.st_size
                mtime = max(mtime, st.st_mtime_ns)
        return size, mtime
    st = p.stat()
    return st.st_size, st.st_mtime_ns


def content_sha256(p: Path) -> str:
    """Content hash of a file or a directory. Always reads the bytes."""
    return _sha256_dir(p) if p.is_dir() else _sha256_file(p)


_hash_cache: dict[str, str] | None = None
_hash_cache_dirty = False


def _load_hash_cache() -> dict[str, str]:
    global _hash_cache
    if _hash_cache is None:
        try:
            _hash_cache = json.loads(HASH_CACHE_PATH.read_text("utf-8"))
            if not isinstance(_hash_cache, dict):
                _hash_cache = {}
        except Exception:
            _hash_cache = {}
    return _hash_cache


def _save_hash_cache() -> None:
    global _hash_cache_dirty
    if not _hash_cache_dirty or _hash_cache is None:
        return
    try:
        HASH_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = HASH_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(_hash_cache, indent=0, sort_keys=True), "utf-8")
        os.replace(tmp, HASH_CACHE_PATH)
        _hash_cache_dirty = False
    except Exception:  # pragma: no cover - a cold cache is not a failure
        pass


def memoized_sha256(p: Path) -> tuple[str, bool]:
    """(sha256, reused) — memoized by path + size + mtime.

    ONLY for building a manifest. `verify` must not use this: if a file's bytes
    were replaced without its size or mtime moving, the memo would answer with
    the hash of bytes that are no longer there, which is precisely the failure
    the verifier is supposed to catch.
    """
    global _hash_cache_dirty
    size, mtime_ns = _stat(p)
    key = f"{_portable(p)}|{size}|{mtime_ns}"
    cache = _load_hash_cache()
    hit = cache.get(key)
    if hit:
        return hit, True
    digest = content_sha256(p)
    cache[key] = digest
    _hash_cache_dirty = True
    return digest, False


def _sha256_text_lf(p: Path) -> str:
    """sha256 of a text file with CRLF/CR normalized to LF (see AD-039).

    A config's hash must depend on what it says, not on how git happened to
    check it out.
    """
    b = p.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    return hashlib.sha256(b).hexdigest()


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _artifact_hashes(path: Path) -> dict:
    """Delegate to tools/artifact_hash.py — the project's one artifact hasher."""
    import importlib.util

    tool = _repo_root() / "tools" / "artifact_hash.py"
    if not tool.exists():
        raise FileNotFoundError(f"tools/artifact_hash.py not found at {tool}")
    spec = importlib.util.spec_from_file_location("_pb_artifact_hash", tool)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.hashes(path)


def _git_describe() -> dict:
    try:
        root = _repo_root()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, timeout=10,
        )
        if head.returncode != 0:
            return {"commit": None, "dirty": None}
        dirty = subprocess.run(
            ["git", "status", "--porcelain"], cwd=root, capture_output=True,
            text=True, timeout=20,
        )
        return {
            "commit": head.stdout.strip() or None,
            "dirty": bool(dirty.stdout.strip()) if dirty.returncode == 0 else None,
        }
    except Exception:
        return {"commit": None, "dirty": None}


def _pipeline_version() -> str:
    try:
        from importlib.metadata import version
        return version("wargame-cartographer")
    except Exception:
        return "unknown"


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def manifest_path_for(artifact_path: Path) -> Path:
    """`<artifact-stem>_manifest.json`, beside the artifact."""
    artifact_path = Path(artifact_path)
    return artifact_path.with_name(f"{artifact_path.stem}_manifest.json")


def _facilities_hashes() -> dict:
    """Content hashes of the authored facility layer and the module that
    assigns it (AD-043). None for a layer that is absent, which exports []."""
    from wargame_cartographer.geo import facilities as _fac
    layer = _fac.resolve_facilities_file()
    return {
        "facilities_layer_sha256": _sha256_text_lf(layer) if layer.exists() else None,
        "facilities_source_sha256": _sha256_text_lf(Path(_fac.__file__)),
    }


def write_manifest(
    artifact_path,
    spec,
    spec_path,
    *,
    run_path: str,
    extra: dict | None = None,
) -> Path:
    """Write the manifest for a just-exported artifact. Call AFTER export only.

    ``run_path`` is "monolithic" or "streaming". Raises if it cannot write: a
    silently missing receipt is the failure class this whole module exists to
    end, and the artifact is already safely on disk by this point.
    """
    artifact_path = Path(artifact_path).resolve()
    spec_path = Path(spec_path).resolve()
    t0 = time.perf_counter()

    parts: list[dict] = []
    hashed = reused = 0
    total_bytes = 0
    for p, meta in recorded_parts():
        if not p.exists():
            # Recorded then deleted mid-run. Record it as consumed-and-gone
            # rather than dropping it: a hole in the receipt is worse than a
            # part that says it could not be hashed.
            parts.append({
                "root": _split_root(p)[0], "path": _split_root(p)[1],
                "layer": meta["layer"], "role": meta["role"],
                "bytes": None, "sha256": None, "fetched_at": None,
                "hash_reused": False, "note": "vanished during the run",
            })
            continue
        root, rel = _split_root(p)
        size, mtime_ns = _stat(p)
        digest, was_reused = memoized_sha256(p)
        hashed += 0 if was_reused else 1
        reused += 1 if was_reused else 0
        total_bytes += size
        parts.append({
            "root": root,
            "path": rel,
            "layer": meta["layer"],
            "role": meta["role"],
            "kind": "dir" if p.is_dir() else "file",
            "bytes": size,
            "sha256": digest,
            "hash_algo": "sha256-dir" if p.is_dir() else "sha256",
            "fetched_at": _iso(mtime_ns / 1e9),
            "hash_reused": was_reused,
        })
    _save_hash_cache()

    art = _artifact_hashes(artifact_path)

    from wargame_cartographer.output import game_data_exporter as _exp
    exporter_src = Path(_exp.__file__)

    run: dict = {
        "path": run_path,
        "pipeline_version": _pipeline_version(),
        "schema_version": _exp.SCHEMA_VERSION,
        "exporter_source_sha256": _sha256_text_lf(exporter_src),
        # AD-043: the authored facility layer is read at export, outside the
        # tile-cache hashes, so the receipt names it explicitly.
        **_facilities_hashes(),
        "python": sys.version.split()[0],
        "osm_cache_max_age_days": os.environ.get(
            "PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS", "") or None,
        **_git_describe(),
    }
    try:
        from wargame_cartographer import streaming as _st
        run["streaming_version"] = _st.STREAMING_VERSION
        run["sampling_code_hash"] = _st._sampling_code_hash()
        run["input_data_hash"] = _st._input_data_hash()
    except Exception as e:  # pragma: no cover - keep the receipt, flag the gap
        run["streaming_version"] = None
        run["note"] = f"streaming metadata unavailable: {e}"

    bbox = spec.bbox
    doc = {
        "manifest_version": MANIFEST_VERSION,
        "generated_at": _iso(time.time()),
        "artifact": {
            "file": artifact_path.name,
            "bytes": art["bytes"],
            "hex_count": art["hex_count"],
            "schema_version": art["schema_version"],
            "raw_sha256": art["raw_sha256"],
            "content_sha256": art["content_sha256"],
        },
        "run": run,
        "config": {
            "path": _portable(spec_path),
            "sha256": _sha256_text_lf(spec_path),
            "hash_algo": "sha256-lf",
            "name": spec.name,
            "resolved_bbox": {
                "min_lon": bbox.min_lon, "min_lat": bbox.min_lat,
                "max_lon": bbox.max_lon, "max_lat": bbox.max_lat,
            },
            "hex_size_km": spec.hex_size_km,
            "river_scalerank_max": getattr(spec, "river_scalerank_max", None),
        },
        "cache_roots": {k: _portable(v) for k, v in _ROOTS.items()},
        "cache_parts": parts,
        "hash_summary": {
            "parts": len(parts),
            "bytes": total_bytes,
            "hashed_this_run": hashed,
            "reused_from_hash_cache": reused,
            "seconds": round(time.perf_counter() - t0, 2),
        },
    }
    if extra:
        doc.update(extra)

    out = manifest_path_for(artifact_path)
    out.write_text(json.dumps(doc, indent=2, sort_keys=False) + "\n", "utf-8")
    return out


# ---------------------------------------------------------------------------
# The pre-run guard (AD-042)
# ---------------------------------------------------------------------------
#
# The manifest is written at the END of a run, so on its own it reports a loss
# after the loss. This refuses to START a run that is about to cause one, or
# that is already standing on a cache that moved.

GUARD_OVERRIDE_ENV = "PARA_BELLUM_ALLOW_CACHE_REFETCH"

#: A part this close to its TTL is treated as expiring, because a run that
#: starts now can still cross the line before it reads that part. A day is
#: generous for every config in this repo (the longest cold run is ~4 h) and
#: gives an operator a full day of warning rather than a race.
EXPIRY_MARGIN_DAYS = 1.0


class CacheGuardError(RuntimeError):
    """Raised instead of starting a run that would destroy or has lost a part."""


def _ttl_days_for_root(root: str) -> float | None:
    """The freshness window in force for a cache root, or None if it has none.

    Resolved per call, so `PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS` applies to the
    run being guarded rather than to import time.
    """
    if root == "osm":
        from wargame_cartographer.geo.osm_downloader import _cache_max_age_days
        return float(_cache_max_age_days())
    if root == "vector":
        from wargame_cartographer.config.defaults import CACHE_MAX_AGE_DAYS
        return float(CACHE_MAX_AGE_DAYS)
    # elevation: get_elevation keeps a cached DEM until someone deletes it.
    return None


def evaluate_cache(
    doc: dict,
    *,
    root_overrides: dict[str, Path] | None = None,
    ttl_days: dict[str, float | None] | None = None,
    now: float | None = None,
) -> list[dict]:
    """Problems with the cache, judged against a blessed manifest ``doc``.

    Pure apart from reading the cache: takes the baseline, returns a list of
    {kind, layer, root, path, ...} where kind is ``changed``, ``missing`` or
    ``expiring``. An empty list means the run is safe to start.
    """
    overrides = {k: Path(v) for k, v in (root_overrides or {}).items()}
    now = time.time() if now is None else now
    problems: list[dict] = []

    for entry in doc.get("cache_parts", []):
        root, rel = entry.get("root"), entry.get("path")
        base = {"layer": entry.get("layer"), "root": root, "path": rel}
        try:
            p = _resolve(root, rel, overrides)
        except Exception as e:
            problems.append({**base, "kind": "missing", "detail": str(e)})
            continue
        if not p.exists():
            problems.append({**base, "kind": "missing"})
            continue

        actual = content_sha256(p)
        if actual != entry.get("sha256"):
            problems.append({**base, "kind": "changed",
                             "blessed_sha256": entry.get("sha256"),
                             "actual_sha256": actual})
            continue  # already moved; its age is beside the point

        if ttl_days is not None and root in ttl_days:
            ttl = ttl_days[root]
        else:
            ttl = _ttl_days_for_root(root)
        if ttl is None:
            continue
        age_days = (now - _stat(p)[1] / 1e9) / 86400
        if age_days >= ttl - EXPIRY_MARGIN_DAYS:
            problems.append({**base, "kind": "expiring",
                             "age_days": round(age_days, 1), "ttl_days": ttl})

    return problems


def blessed_manifest(manifest_path) -> tuple[dict | None, str]:
    """The COMMITTED manifest for an artifact, read from git HEAD.

    Deliberately not the working-tree copy: every run overwrites that, so using
    it would make each run its own baseline and a part that moved would be
    flagged once and then quietly become the new normal. A baseline has to be
    something a person blessed. Returns (None, why) when there is none.
    """
    manifest_path = Path(manifest_path)
    root = _repo_root()
    try:
        rel = manifest_path.resolve().relative_to(root).as_posix()
    except ValueError:
        return None, f"{manifest_path} is outside the repository"
    try:
        r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=root,
                           capture_output=True, timeout=30)
    except Exception as e:
        return None, f"git unavailable ({e})"
    if r.returncode != 0:
        return None, f"no manifest committed at {rel}"
    try:
        return json.loads(r.stdout.decode("utf-8")), f"HEAD:{rel}"
    except Exception as e:
        return None, f"the manifest committed at {rel} is unreadable: {e}"


def artifact_path_for_spec(spec) -> Path:
    """Where a run of ``spec`` writes its artifact (mirrors both export paths)."""
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in spec.name)[:40].lower()
    return Path(spec.output_dir) / f"{safe}_hex_terrain.json"


def _guard_message(problems: list[dict], source: str, doc: dict,
                   overridden: bool = False) -> str:
    lines = [
        "",
        f"OVERRIDDEN ({GUARD_OVERRIDE_ENV}=1) — proceeding although the cache this"
        if overridden else
        "Refusing to start: the cache this run depends on has moved, or is about",
        "run depends on has moved, or is about to be overwritten by this run."
        if overridden else
        "to be overwritten by this run.",
        "",
        f"  baseline: {source}",
        f"            written {doc.get('generated_at')} for "
        f"{doc.get('artifact', {}).get('file')}",
        "",
    ]
    for p in problems:
        if p["kind"] == "changed":
            lines += [
                f"  CHANGED   {p['layer']}  {p['path']}",
                f"            blessed {p['blessed_sha256']}",
                f"            on disk {p['actual_sha256']}",
                "            The snapshot that produced the shipped artifact is not what",
                "            is on disk now. Do not regenerate against it silently:",
                "            measure the churn (compare_hex_outputs.py) and report the",
                "            number, or commit a new manifest if the change was intended.",
            ]
        elif p["kind"] == "missing":
            lines += [
                f"  MISSING   {p['layer']}  {p['path']}",
                "            This run would refetch it from scratch. Whatever produced",
                "            the blessed artifact is gone; the new data is not the same",
                "            data, and no diff after the fact can separate them.",
            ]
        else:
            lines += [
                f"  EXPIRING  {p['layer']}  {p['path']}",
                f"            {p['age_days']} days old against a {p['ttl_days']:.0f}-day TTL"
                f" (margin {EXPIRY_MARGIN_DAYS:.0f} d).",
                "            This run WILL refetch it and overwrite it IN PLACE. There is",
                "            no archive and no way back.",
            ]
            if p["root"] == "osm":
                lines.append("            Widen the window: "
                             "PARA_BELLUM_OSM_CACHE_MAX_AGE_DAYS=120")
        lines.append("")
    if overridden:
        lines += [
            "The manifest this run writes will record what it actually used.",
            "",
        ]
    else:
        lines += [
            "If the refetch is intentional, say so and re-run:",
            "",
            f"    {GUARD_OVERRIDE_ENV}=1 <your command>",
            "",
            "The run then proceeds and its manifest records what it actually used.",
            "",
        ]
    return "\n".join(lines)


def check_cache_before_run(spec, *, status=None) -> dict:
    """Guard a run before it fetches or samples anything.

    Raises `CacheGuardError` when a part the blessed manifest records has moved,
    is gone, or would expire during this run. A config with no committed
    manifest has nothing to check against: that is said out loud and the run
    proceeds. `PARA_BELLUM_ALLOW_CACHE_REFETCH=1` downgrades a refusal to a
    warning.

    ``status`` redirects the guard's own lines (tests use it). It defaults to
    print rather than to the caller's status callback on purpose: in the
    monolithic CLI that callback is a progress-spinner label, which overwrites
    itself, and a provenance statement nobody can read is not one.
    """
    def say(msg: str) -> None:
        (status or print)(msg)

    manifest_path = manifest_path_for(artifact_path_for_spec(spec))
    doc, source = blessed_manifest(manifest_path)
    if doc is None:
        say(f"[cache guard] no blessed manifest to check against ({source}) — "
            f"proceeding. This run will write the first one.")
        return {"checked": False, "reason": source, "problems": []}

    problems = evaluate_cache(doc)
    n = len(doc.get("cache_parts", []))
    if not problems:
        say(f"[cache guard] {n} parts match {source} and none expire during "
            f"this run — proceeding.")
        return {"checked": True, "source": source, "problems": []}

    if os.environ.get(GUARD_OVERRIDE_ENV) == "1":
        say(_guard_message(problems, source, doc, overridden=True).rstrip())
        say(f"[cache guard] proceeding anyway on {len(problems)} problem(s).")
        return {"checked": True, "source": source, "problems": problems,
                "overridden": True}
    raise CacheGuardError(_guard_message(problems, source, doc))


# ---------------------------------------------------------------------------
# Verifying
# ---------------------------------------------------------------------------

def verify(
    manifest_path,
    artifact_path=None,
    root_overrides: dict[str, Path] | None = None,
) -> dict:
    """Check a manifest against the cache (and the artifact) as they are now.

    Returns a report dict. Every part is rehashed from disk — the run-time hash
    memo is deliberately not consulted here.
    """
    manifest_path = Path(manifest_path).resolve()
    doc = json.loads(manifest_path.read_text("utf-8"))
    overrides = {k: Path(v) for k, v in (root_overrides or {}).items()}

    part_reports: list[dict] = []
    counts = {"match": 0, "changed": 0, "missing": 0}
    for entry in doc.get("cache_parts", []):
        rep = {
            "layer": entry.get("layer"),
            "root": entry.get("root"),
            "path": entry.get("path"),
            "expected_sha256": entry.get("sha256"),
        }
        try:
            p = _resolve(entry["root"], entry["path"], overrides)
        except Exception as e:
            rep.update(status="missing", detail=str(e))
            part_reports.append(rep)
            counts["missing"] += 1
            continue
        rep["resolved"] = _portable(p)
        if not p.exists():
            rep.update(status="missing", actual_sha256=None)
            counts["missing"] += 1
        else:
            actual = content_sha256(p)
            rep["actual_sha256"] = actual
            if entry.get("sha256") == actual:
                rep["status"] = "match"
                counts["match"] += 1
            else:
                rep["status"] = "changed"
                rep["bytes_now"] = _stat(p)[0]
                rep["bytes_then"] = entry.get("bytes")
                counts["changed"] += 1
        part_reports.append(rep)

    # The artifact itself.
    art_expect = doc.get("artifact", {})
    if artifact_path is None:
        artifact_path = manifest_path.with_name(art_expect.get("file", ""))
    artifact_path = Path(artifact_path)
    if not artifact_path.exists():
        art_report = {"file": str(artifact_path), "status": "missing"}
    else:
        got = _artifact_hashes(artifact_path)
        same_raw = got["raw_sha256"] == art_expect.get("raw_sha256")
        same_content = got["content_sha256"] == art_expect.get("content_sha256")
        art_report = {
            "file": artifact_path.name,
            "status": "match" if same_raw and same_content
                      else "content-match" if same_content else "changed",
            "raw_sha256": got["raw_sha256"],
            "content_sha256": got["content_sha256"],
            "expected_raw_sha256": art_expect.get("raw_sha256"),
            "expected_content_sha256": art_expect.get("content_sha256"),
        }

    ok = counts["changed"] == 0 and counts["missing"] == 0 \
        and art_report["status"] in ("match", "content-match")
    return {
        "manifest": str(manifest_path),
        "generated_at": doc.get("generated_at"),
        "run": doc.get("run", {}),
        "artifact": art_report,
        "parts": part_reports,
        "counts": counts,
        "ok": ok,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_report(rep: dict, verbose: bool) -> None:
    run = rep.get("run", {})
    print(f"manifest : {rep['manifest']}")
    print(f"written  : {rep.get('generated_at')}  "
          f"path={run.get('path')}  schema={run.get('schema_version')}  "
          f"streaming={run.get('streaming_version')}")
    a = rep["artifact"]
    print(f"artifact : {a.get('file')}  {a['status'].upper()}")
    if a["status"] == "changed":
        print(f"           expected content {a.get('expected_content_sha256')}")
        print(f"           actual   content {a.get('content_sha256')}")
    print()
    for p in rep["parts"]:
        status = p["status"]
        if status == "match" and not verbose:
            print(f"  match    {p['layer']:<22} {p['path']}")
        elif status == "match":
            print(f"  match    {p['layer']:<22} {p['path']}  {p['actual_sha256'][:16]}")
        elif status == "changed":
            print(f"  CHANGED  {p['layer']:<22} {p['path']}")
            print(f"           expected {p['expected_sha256']}")
            print(f"           actual   {p['actual_sha256']}")
            print(f"           bytes {p.get('bytes_then')} -> {p.get('bytes_now')}")
        else:
            print(f"  MISSING  {p['layer']:<22} {p['path']}")
            if p.get("detail"):
                print(f"           {p['detail']}")
    c = rep["counts"]
    print()
    print(f"{c['match']} match, {c['changed']} changed, {c['missing']} missing "
          f"of {sum(c.values())} parts")
    if rep["ok"]:
        print("OK — the cache still holds what produced this artifact.")
    elif c["changed"] or c["missing"]:
        print("FAIL — this artifact's inputs are no longer what the manifest "
              "records. The snapshot behind it is gone or altered.")
    else:
        print("FAIL — every input still matches, but this artifact is not the "
              "one the manifest was written for.")


def main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        prog="python -m wargame_cartographer.manifest",
        description="Check an exported artifact against its cache snapshot manifest.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", help="report each recorded cache part as "
                                      "match / changed / missing")
    v.add_argument("target", help="the artifact JSON, or the manifest itself")
    v.add_argument("--manifest", default=None,
                   help="manifest path (default: <artifact-stem>_manifest.json beside it)")
    v.add_argument("--root", action="append", default=[], metavar="NAME=PATH",
                   help="redirect a cache root, e.g. --root osm=/tmp/cache-copy "
                        "(repeatable)")
    v.add_argument("--json", action="store_true", help="emit the report as JSON")
    v.add_argument("--verbose", action="store_true", help="show hashes for matches")

    c = sub.add_parser("check", help="ask the pre-run guard whether a config is "
                                     "safe to run, without running it")
    c.add_argument("config", help="the map spec YAML")
    args = ap.parse_args(argv)

    if args.cmd == "check":
        from wargame_cartographer.config.map_spec import MapSpec
        spec = MapSpec.from_yaml(args.config)
        try:
            check_cache_before_run(spec)
        except CacheGuardError as e:
            print(str(e), file=sys.stderr)
            return 1
        return 0

    overrides: dict[str, Path] = {}
    for spec in args.root:
        if "=" not in spec:
            ap.error(f"--root expects NAME=PATH, got {spec!r}")
        name, _, path = spec.partition("=")
        overrides[name] = Path(path)

    target = Path(args.target)
    if args.manifest:
        man, art = Path(args.manifest), target
    elif target.name.endswith("_manifest.json"):
        man, art = target, None
    else:
        man, art = manifest_path_for(target), target
    if not man.exists():
        print(f"no manifest at {man}", file=sys.stderr)
        return 2

    rep = verify(man, art, overrides)
    if args.json:
        print(json.dumps(rep, indent=2))
    else:
        _print_report(rep, args.verbose)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
