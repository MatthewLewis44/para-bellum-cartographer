"""Canonical content hashes for handed-off hex-map artifacts (Sprint 7).

Publishes two hashes per artifact so Unity can confirm its integrated copy
matches the pipeline's handoff copy (closing the window where a cache purge
and a handoff overlap):

  * raw-file sha256  — sha256 of the exact file bytes. Trivial for Unity to
    verify: `sha256sum file` or `SHA256.HashData(File.ReadAllBytes(path))`.
    Changes if the file is re-exported (new `generated_at`) or re-encoded.
  * content sha256   — sha256 of a CANONICAL serialization with the volatile
    `map_metadata.generated_at` removed and keys sorted (compact UTF-8,
    separators ",",":"). Stable across re-exports of identical content.
    Unity reproduces it by: parse JSON -> delete map_metadata.generated_at ->
    serialize with sorted keys + no whitespace (UTF-8, non-ASCII preserved) ->
    sha256.

Usage:
  uv run python tools/artifact_hash.py output/<name>_hex_terrain.json [...]
  uv run python tools/artifact_hash.py            # hashes the 4 shipped artifacts
"""

import hashlib
import json
import sys
from pathlib import Path

DEFAULT = [
    "output/para_bellum_belgium_test_hex_terrain.json",
    "output/para_bellum_benelux_germany_test_hex_terrain.json",
    "output/para_bellum_wceurope_test_hex_terrain.json",
    "output/para_bellum_east_expansion_hex_terrain.json",
]


def hashes(path: Path) -> dict:
    raw = path.read_bytes()
    raw_sha = hashlib.sha256(raw).hexdigest()

    doc = json.loads(raw)
    md = doc.get("map_metadata", {})
    md.pop("generated_at", None)   # volatile — excluded from the content hash
    canon = json.dumps(doc, sort_keys=True, ensure_ascii=False,
                       separators=(",", ":")).encode("utf-8")
    content_sha = hashlib.sha256(canon).hexdigest()

    return {
        "file": path.name,
        "bytes": len(raw),
        "hex_count": doc.get("map_metadata", {}).get("hex_count", len(doc.get("hexes", []))),
        "schema_version": doc.get("schema_version"),
        "raw_sha256": raw_sha,
        "content_sha256": content_sha,
    }


def main() -> int:
    paths = sys.argv[1:] or DEFAULT
    for p in paths:
        path = Path(p)
        if not path.exists():
            print(f"MISSING  {p}")
            continue
        h = hashes(path)
        print(f"{h['file']}")
        print(f"  schema {h['schema_version']}  hex_count {h['hex_count']}  bytes {h['bytes']:,}")
        print(f"  raw_sha256     {h['raw_sha256']}")
        print(f"  content_sha256 {h['content_sha256']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
