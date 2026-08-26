"""The cropped-province seat rule (AD-037), applied to every nation on the map.

A province yields money and manpower through its SEATS — the capital-tier hex
plus any sub-capital hexes (`GameState.ProvinceSeatYield`). A province with
neither yields nothing. That is the correct model for uninhabited terrain
(AD-U10), but it is the WRONG answer for a province the map frame cut in half:
ITA_VENEZIA_TRIDENTINA holds Bolzano and 104 hexes, and produced nothing at all
purely because Trento — its declared 1930 capital — lies south of the bbox and
so was never there to be designated.

The rule: **where a province's declared 1930 capital lies outside the frame, the
highest-weight settlement INSIDE the frame is designated a sub-capital and
carries the seat yield.** The declared capital is never changed — it stays as
the historical record, and it will designate itself the moment the frame widens
far enough to include it. This is deliberately a sub-capital and not a
substitute capital: `ProvinceInfo.CapitalHex` drives `BaselineOwner` and the
AD-023 "province control = holding its prime settlement" rule, and a frame
accident should not silently move a province's prime settlement.

Where a cropped province has NO named settlement in frame, nothing is added and
it keeps yielding zero — which is then correct rather than accidental, because
there is genuinely nobody there.

Reads the artifact (which province each settlement actually landed in, after
the hex sampler's point-in-polygon and coastal snap) and edits ONLY
`sub_capitals` in provinces_1930_metadata.json. Idempotent: a province that
already has a seat is skipped, and a name already listed is never duplicated.

The province metadata is deliberately NOT part of `streaming._input_data_hash`
— it drives only `admin_tier`, which is recomputed in the uncached merge pass
every run — so re-running the pipeline after this tool reuses every cached tile.

Usage: uv run python tools/add_cropped_province_seats.py [artifact.json]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROV_META = ROOT / "data" / "boundaries" / "provinces_1930_metadata.json"
DEFAULT_ARTIFACT = ROOT / "output" / "para_bellum_east_expansion_hex_terrain.json"

# Settlement size tier — mirrors ProvinceIndex.SettlementTier, which is what
# the sim uses for the capture-seat fallback and for income.
TIER = {"metropolis": 4, "city": 3, "town": 2, "suburb": 1}


def main() -> int:
    art_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_ARTIFACT
    if not art_path.exists():
        print(f"FAIL  artifact not found: {art_path}")
        return 1

    art = json.loads(art_path.read_text(encoding="utf-8"))
    meta = json.loads(PROV_META.read_text(encoding="utf-8"))
    by_id = {p["province_id"]: p for p in meta["provinces"]}

    # What actually landed in each framed province.
    state: dict[str, dict] = {}
    for h in art["hexes"]:
        pid = h["political"]["province_at_start"]
        if not pid:
            continue
        st = state.setdefault(pid, {"hex": 0, "cap": 0, "sub": 0, "named": []})
        st["hex"] += 1
        s = h["settlement"]
        if s["admin_tier"] == "capital":
            st["cap"] += 1
        elif s["admin_tier"] == "sub_capital":
            st["sub"] += 1
        if s["name"]:
            # Tie-break on (col, row) so the choice is deterministic and does
            # not depend on hex iteration order.
            st["named"].append((TIER.get(s["type"], 0), -h["coords"]["col"],
                                -h["coords"]["row"], s["type"], s["name"]))

    promoted, empty, healthy = [], [], 0
    for pid, st in sorted(state.items()):
        if st["cap"] or st["sub"]:
            healthy += 1
            continue
        entry = by_id.get(pid)
        if entry is None:
            print(f"  WARN  {pid} is in the artifact but not in the metadata")
            continue
        declared = entry.get("capital", {}).get("city_name", "?")
        if not st["named"]:
            empty.append((pid, st["hex"], declared))
            continue
        _, _, _, stype, name = max(st["named"])
        existing = {s.get("city_name") for s in entry.get("sub_capitals", [])}
        if name in existing:
            # Two very different situations produce this, and they must not be
            # confused. USUALLY it just means this tool already ran and the
            # artifact has not been regenerated yet — the normal workflow, and
            # the reason this tool is idempotent. It is only a defect if the
            # artifact IS current, in which case the metadata names a seat that
            # assign_admin_tiers could not match to an OSM node.
            print(f"  SKIP  {pid}: {name} is already listed as a sub-capital "
                  f"but is not designated in this artifact. Expected if the "
                  f"artifact predates the last run of this tool; a "
                  f"name-matching failure if it does not. Nothing written.")
            continue
        entry.setdefault("sub_capitals", []).append({
            "city_name": name,
            "rationale": (
                f"cropped-frame seat (AD-037): the declared 1930 capital "
                f"{declared} lies outside the map frame, so this is the "
                f"highest-weight settlement inside it and carries the seat "
                f"yield. The capital entry is unchanged and designates itself "
                f"if the frame ever widens to include {declared}."),
        })
        promoted.append((pid, st["hex"], declared, stype, name))

    print(f"{len(state)} framed provinces: {healthy} already seated, "
          f"{len(promoted)} promoted, {len(empty)} left unseated (correctly)\n")

    print("=== PROMOTED — a cropped province that now carries its own seat ===")
    for pid, hx, declared, stype, name in promoted:
        print(f"  {pid:<26} {hx:>4} hexes   {declared} (off-frame) -> "
              f"{name} [{stype}]")
    if not promoted:
        print("  (none)")

    print("\n=== LEFT UNSEATED — no named settlement in frame, so zero money")
    print("    and manpower is the correct answer, not an accident ===")
    for pid, hx, declared in empty:
        print(f"  {pid:<26} {hx:>4} hexes   capital {declared} is off-frame")
    if not empty:
        print("  (none)")

    if not promoted:
        print("\nnothing to write")
        return 0

    meta["source_note"] = (meta.get("source_note", "") +
                           " | Cropped-province seat rule (AD-037) applied to "
                           "every nation: provinces whose declared 1930 capital "
                           "falls outside the map frame carry an in-frame "
                           "sub-capital so they are not silently zero-yield.")
    PROV_META.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"\nwrote {PROV_META.name}: {len(promoted)} sub-capital(s) added")
    return 0


if __name__ == "__main__":
    sys.exit(main())
