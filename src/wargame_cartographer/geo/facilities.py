"""The 1930 starting industrial base (schema v1.0.7, AD-043).

Loads ``data/facilities/facilities_1930.geojson`` — hand-authored, repo-committed,
built from ``data/facilities/facilities_1930.csv`` by
``tools/build_facilities_1930.py`` — and assigns each facility to the hex that
contains it. The exporter writes the result as the per-hex ``facilities`` array.

Assignment runs at EXPORT time, not in the per-tile sampler: a facility is an
authored point keyed to one hex, needs nothing a tile holds, and keeping it out
of the sampling modules leaves the streaming tile cache valid when only the
authored layer changes.

Authored scenario data, like ``resources_1930.geojson`` (AD-036's model): never
detected from OSM/OHM.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

console = Console()

_REPO_ROOT = Path(__file__).resolve().parents[3]
FACILITIES_FILE = _REPO_ROOT / "data" / "facilities" / "facilities_1930.geojson"

# The kinds pass B authors, in export order. Each maps 1:1 onto the Unity sim's
# FacilityKind by name (mine → Mine, power_plant → PowerPlant, ...). Any other
# value is a schema change, not a data change.
FACILITY_KINDS = ("power_plant", "steel_mill", "mine", "rail_yard")
MAX_TIER = 3                      # the sim's MaxFacilityTier
MINE_DEPOSITS = ("iron",)         # the sim's RequiredDepositFor has one arm


def resolve_facilities_file() -> Path:
    """The layer path, with the same cwd fallback resources.py uses for a
    non-editable install."""
    if FACILITIES_FILE.exists():
        return FACILITIES_FILE
    fallback = Path.cwd() / "data" / "facilities" / "facilities_1930.geojson"
    return fallback if fallback.exists() else FACILITIES_FILE


def load_facilities_1930(path: Path | None = None) -> list[dict]:
    """Return the authored facility features (GeoJSON Point features).

    Empty (not an error) if the file is absent: every hex then exports
    ``facilities: []``, exactly what a pre-pass-B artifact means.
    """
    path = Path(path) if path is not None else resolve_facilities_file()
    if not path.exists():
        console.print(f"  [yellow]Facilities layer not found: {path} — no starting facilities[/yellow]")
        return []
    return json.loads(path.read_text(encoding="utf-8"))["features"]


def assign_facilities_to_hexes(grid, features: list[dict]) -> dict[tuple[int, int], list[dict]]:
    """(q, r) → the export entries of the facilities whose point lies in that hex.

    Facilities outside the grid (the bbox frames only part of a nation) are
    dropped and counted. Entries within a hex are ordered by kind, then name,
    so the export is deterministic.
    """
    from wargame_cartographer.hex.sampler import _point_to_hex

    out: dict[tuple[int, int], list[dict]] = {}
    outside = 0
    for f in features:
        p = f["properties"]
        lon, lat = f["geometry"]["coordinates"]
        x, y = grid._to_proj.transform(lon, lat)
        qr = _point_to_hex(grid, x, y)
        if qr is None:
            outside += 1
            continue
        out.setdefault(qr, []).append({
            "kind": p["kind"],
            "tier": int(p["tier"]),
            "name": p["name"],
            "deposit": p.get("deposit") or "",
        })
    for entries in out.values():
        entries.sort(key=lambda e: (FACILITY_KINDS.index(e["kind"]), e["name"]))
    placed = sum(len(v) for v in out.values())
    console.print(f"  Facilities: {placed} placed on {len(out)} hexes, {outside} outside the grid")
    return out
