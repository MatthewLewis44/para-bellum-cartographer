"""1930 starting industrial base (Sprint 12 pass B, AD-043).

Builds data/facilities/facilities_1930.geojson from the hand-authored table
data/facilities/facilities_1930.csv, and holds the ONE implementation of the
checks that table must pass — both here (static, before writing) and against a
generated artifact (`check_artifact`, also run by validate_full_bbox.py).

    uv run python tools/build_facilities_1930.py                    # build
    uv run python tools/build_facilities_1930.py --check <artifact> # check only

The CSV is the authored source and is what historical review edits; the GeoJSON
is generated from it and never edited by hand. Columns:

    name, kind, tier, country, lon, lat, deposit, confidence, note

`confidence` is `site` (a named 1930 works or station placed within a few km)
or `approximate` (the facility kind and district are right, the identity or
siting needs review). Both are on the historical-review list.

-- The two constraints (not this tool's to relax) -------------------------------

1. A starting steelworks needs a starting power plant. Checked twice: every
   mill's PROVINCE holds a plant, and every NATION's authored supply covers its
   authored draw under the sim's current tables (the power pool is national).
2. No steel deposits are authored (AD-M03's transitional deposits retire; steel
   comes from mills). No row may name a steel deposit, and the resource layer
   may carry no `steel` feature.

The artifact checks additionally reject anything the sim itself would refuse to
build: a facility outside a province, on a hex owned by another nation than the
province, beyond the province's slot total, or a mine off its deposit.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "data" / "facilities" / "facilities_1930.csv"
OUT_PATH = ROOT / "data" / "facilities" / "facilities_1930.geojson"
RESOURCES_PATH = ROOT / "data" / "resources" / "resources_1930.geojson"

KINDS = ("power_plant", "steel_mill", "mine", "rail_yard")   # geo/facilities.py FACILITY_KINDS
MAX_TIER = 3
MINE_DEPOSITS = ("iron",)
CONFIDENCE = ("site", "approximate")

# --- Mirrors of the sim's [P] tables (Assets/Scripts/Sim/GameState.cs) ----------
# Unsigned placeholders on the Unity side, mirrored here in ONE place. If the sim
# moves them, move these. Supply: PowerOutputFor = base Tier x Facility layer
# (1 + (tier-1)) = tier^2 at v0. Draw: FacilityPowerDrawFor.
def power_supply(tier: int) -> int:
    return tier * tier


POWER_DRAW = {"power_plant": 0, "steel_mill": 2, "mine": 1, "rail_yard": 1}

# Slots: SlotsForSeatSettlement, read from the province's CAPTURE SEAT.
SEAT_SLOTS = {"metropolis": 7, "city": 5, "town": 3}
_SETTLEMENT_TIER = {"metropolis": 4, "city": 3, "town": 2, "suburb": 1}


# --- Static checks ---------------------------------------------------------------

def load_rows(path: Path = CSV_PATH) -> list[dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def check_rows(rows: list[dict]) -> list[str]:
    """Problems with the authored table itself. Empty list = clean."""
    errs: list[str] = []
    seen = set()
    for i, r in enumerate(rows, start=2):  # line 1 is the header
        where = f"line {i} ({r.get('name', '?')})"
        if r["kind"] not in KINDS:
            errs.append(f"{where}: kind {r['kind']!r} not in {KINDS}")
        if not r["tier"].isdigit() or not 1 <= int(r["tier"]) <= MAX_TIER:
            errs.append(f"{where}: tier {r['tier']!r} not 1..{MAX_TIER}")
        if len(r["country"]) != 3 or not r["country"].isupper():
            errs.append(f"{where}: country {r['country']!r} is not ISO3")
        if r["kind"] == "mine":
            if r["deposit"] not in MINE_DEPOSITS:
                errs.append(f"{where}: mine deposit {r['deposit']!r} not in {MINE_DEPOSITS}")
        elif r["deposit"]:
            errs.append(f"{where}: only a mine has a deposit")
        if "steel" in r["deposit"].lower():
            errs.append(f"{where}: authors a steel deposit (constraint 2)")
        if r["confidence"] not in CONFIDENCE:
            errs.append(f"{where}: confidence {r['confidence']!r} not in {CONFIDENCE}")
        try:
            lon, lat = float(r["lon"]), float(r["lat"])
            if not (-30 < lon < 60 and 30 < lat < 72):
                errs.append(f"{where}: lon/lat {lon},{lat} outside Europe")
        except ValueError:
            errs.append(f"{where}: lon/lat not numeric")
        key = (r["name"], r["kind"])
        if key in seen:
            errs.append(f"{where}: duplicate {key}")
        seen.add(key)
    # Constraint 1 at table scale: a nation authoring a mill authors a plant.
    kinds_by_nation = defaultdict(set)
    for r in rows:
        kinds_by_nation[r["country"]].add(r["kind"])
    for nation, kinds in sorted(kinds_by_nation.items()):
        if "steel_mill" in kinds and "power_plant" not in kinds:
            errs.append(f"{nation}: authors a steel mill and no power plant (constraint 1)")
    errs += check_no_steel_resources()
    return errs


def check_no_steel_resources(path: Path = RESOURCES_PATH) -> list[str]:
    """Constraint 2 on the resource layer: no `steel` feature at all."""
    if not path.exists():
        return []
    feats = json.loads(path.read_text(encoding="utf-8"))["features"]
    steel = [f["properties"].get("name", "?") for f in feats
             if str(f["properties"].get("resource_type", "")).lower() == "steel"]
    return [f"resources_1930 carries {len(steel)} steel deposit(s), e.g. {steel[:3]} "
            f"(constraint 2: steel comes from mills)"] if steel else []


def to_geojson(rows: list[dict]) -> dict:
    return {
        "type": "FeatureCollection",
        "metadata": {
            "description": ("Hand-authored 1930 starting industrial base for Para Bellum: "
                            "power plants, steel mills, iron mines and rail yards (AD-043)."),
            "generated_from": "data/facilities/facilities_1930.csv by tools/build_facilities_1930.py",
            "review": "Every row is on Matthew's historical-review list; see `confidence`.",
        },
        "features": [{
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
            "properties": {
                "name": r["name"], "kind": r["kind"], "tier": int(r["tier"]),
                "country": r["country"], "deposit": r["deposit"],
                "confidence": r["confidence"], "note": r["note"],
            },
        } for r in rows],
    }


# --- Artifact checks -------------------------------------------------------------

def _province_seats(hexes: list[dict]) -> dict[str, tuple[str, str]]:
    """province -> (baseline owner, seat settlement type), by the ProvinceIndex rule:
    seat = the capital hex, else the highest-tier named settlement (ties to lowest
    (col,row)), else the lowest hex; owner = the capital hex's country, else the
    majority country (ties alphabetical)."""
    by_prov: dict[str, list[dict]] = defaultdict(list)
    for h in hexes:
        p = h["political"]["province_at_start"]
        if p:
            by_prov[p].append(h)
    out = {}
    for p, hs in by_prov.items():
        hs.sort(key=lambda h: (h["coords"]["col"], h["coords"]["row"]))
        caps = [h for h in hs if h["settlement"]["admin_tier"] == "capital"]
        if caps:
            seat, owner = caps[0], caps[0]["political"]["country_at_start"]
        else:
            seat, best = hs[0], -1
            for h in hs:
                if h["settlement"]["name"]:
                    t = _SETTLEMENT_TIER.get(h["settlement"]["type"], 0)
                    if t > best:
                        seat, best = h, t
            c = Counter(h["political"]["country_at_start"] for h in hs
                        if h["political"]["country_at_start"])
            owner = sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if c else ""
        out[p] = (owner, seat["settlement"]["type"])
    return out


def check_artifact(data: dict, rows: list[dict] | None = None
                   ) -> tuple[list[tuple[str, bool, str, bool]], list[str]]:
    """(checks, report lines) for an artifact.

    Each check is (name, ok, detail, crop_sensitive). A crop-sensitive check can
    legitimately fail on a test frame that crops provinces seatless or cuts a
    plant off past the bbox edge (AD-M19); validate_full_bbox hard-fails those
    only on the shipped map. Every other check is hard on every artifact.

    The artifact is reconciled against the authored CSV (the source of truth),
    which also catches a stale GeoJSON, a facility silently dropped inside the
    bbox, and a coordinate that slipped across a border onto a consistent hex.
    """
    rows = load_rows() if rows is None else rows
    authored = {(r["name"], r["kind"]): r for r in rows}
    hexes = data["hexes"]
    seats = _province_seats(hexes)

    malformed, placed = [], []   # placed: (hex, entry) for well-formed entries only
    for h in hexes:
        fs = h.get("facilities")
        if not isinstance(fs, list):
            malformed.append((h["id"], "facilities is not a list"))
            continue
        for e in fs:
            ok = (isinstance(e, dict) and set(e) == {"kind", "tier", "name", "deposit"}
                  and isinstance(e["kind"], str) and isinstance(e["name"], str)
                  and isinstance(e["deposit"], str) and type(e["tier"]) is int)
            if ok:
                placed.append((h, e))
            else:
                malformed.append((h["id"], e))
        ordered = [e for e in fs if isinstance(e, dict) and e.get("kind") in KINDS]
        keys = [(KINDS.index(e["kind"]), e.get("name", "")) for e in ordered]
        if keys != sorted(keys):
            malformed.append((h["id"], "entries not ordered by (kind, name)"))

    def hid(h):
        return h["id"]

    def prov(h):
        return h["political"]["province_at_start"]

    def country(h):
        return h["political"]["country_at_start"]

    bad_kind = [(hid(h), e["kind"]) for h, e in placed if e["kind"] not in KINDS]
    placed = [(h, e) for h, e in placed if e["kind"] in KINDS]   # the rest cannot be costed
    bad_tier = [(hid(h), e["tier"]) for h, e in placed if not 1 <= e["tier"] <= MAX_TIER]
    bad_deposit = [(hid(h), e["name"], e["deposit"]) for h, e in placed
                   if (e["kind"] == "mine") != bool(e["deposit"])]
    bad_mine = [(hid(h), e["name"], e["deposit"]) for h, e in placed if e["kind"] == "mine"
                and not (e["deposit"] in MINE_DEPOSITS and h["resources"].get(e["deposit"]))]
    steel_dep = [(hid(h), e["name"]) for h, e in placed if "steel" in e["deposit"]]
    steel_hex = [hid(h) for h in hexes if h["resources"].get("steel")]

    # Reconciliation with the authored table.
    unknown = [(hid(h), e["name"]) for h, e in placed if (e["name"], e["kind"]) not in authored]
    stale = [(hid(h), e["name"]) for h, e in placed if (e["name"], e["kind"]) in authored
             and (int(authored[(e["name"], e["kind"])]["tier"]) != e["tier"]
                  or authored[(e["name"], e["kind"])]["deposit"] != e["deposit"])]
    wrong_nation = [(hid(h), e["name"], country(h), authored[(e["name"], e["kind"])]["country"])
                    for h, e in placed if (e["name"], e["kind"]) in authored
                    and country(h) != authored[(e["name"], e["kind"])]["country"]]
    b = data["map_metadata"]["bounds"]
    placed_keys = Counter((e["name"], e["kind"]) for _, e in placed)
    in_bbox = [k for k, r in authored.items()
               if b["min_lon"] <= float(r["lon"]) <= b["max_lon"]
               and b["min_lat"] <= float(r["lat"]) <= b["max_lat"]]
    dropped = sorted(k[0] for k in in_bbox if placed_keys[k] == 0)
    duplicated = sorted(k[0] for k, n in placed_keys.items() if n > 1)

    # Crop-sensitive: province, ownership, slots, power.
    bad_land = [(hid(h), e["name"]) for h, e in placed
                if h["flags"]["is_water"] or not country(h) or not prov(h)]
    bad_owner = [(hid(h), e["name"], country(h), seats[prov(h)][0])
                 for h, e in placed if prov(h) and country(h) != seats[prov(h)][0]]
    per_prov: dict[str, list[dict]] = defaultdict(list)
    for h, e in placed:
        if prov(h):
            per_prov[prov(h)].append(e)
    over_slots = [f"{p} {len(es)}>{SEAT_SLOTS.get(seats[p][1], 0)}"
                  for p, es in sorted(per_prov.items())
                  if len(es) > SEAT_SLOTS.get(seats[p][1], 0)]
    unpowered_mill_prov = sorted(p for p, es in per_prov.items()
                                 if any(e["kind"] == "steel_mill" for e in es)
                                 and not any(e["kind"] == "power_plant" for e in es))
    supply, draw = Counter(), Counter()
    count_by_nation: dict[str, Counter] = defaultdict(Counter)
    for h, e in placed:
        n = country(h)
        count_by_nation[n][e["kind"]] += 1
        if e["kind"] == "power_plant":
            supply[n] += power_supply(e["tier"])
        else:
            draw[n] += POWER_DRAW[e["kind"]]
    short = sorted(f"{n} {supply[n]}<{draw[n]}" for n in draw if supply[n] < draw[n])

    checks = [
        ("facilities: every entry well-formed and ordered (kind, name)",
         not malformed, f"{malformed[:4]}", False),
        ("facilities: kinds are the four authored kinds", not bad_kind, f"{bad_kind[:4]}", False),
        ("facilities: tier within 1..3", not bad_tier, f"{bad_tier[:4]}", False),
        ("facilities: deposit set on mines and only on mines", not bad_deposit,
         f"{bad_deposit[:4]}", False),
        ("facilities: every mine sits on its deposit", not bad_mine, f"{bad_mine[:4]}", False),
        ("facilities: every entry matches the authored table (not stale, not unknown)",
         not unknown and not stale, f"unknown {unknown[:3]} stale {stale[:3]}", False),
        ("facilities: every facility lands in its authored nation", not wrong_nation,
         f"{wrong_nation[:4]}", False),
        ("facilities: every authored facility inside the bbox is placed exactly once",
         not dropped and not duplicated, f"dropped {dropped[:4]} duplicated {duplicated[:4]}",
         False),
        ("facilities: no steel deposit authored, no hex carries resources.steel (constraint 2)",
         not steel_dep and not steel_hex, f"{len(steel_dep)} entries, {len(steel_hex)} hexes",
         False),
        ("facilities: every facility on a land hex with country + province",
         not bad_land, f"{bad_land[:4]}", True),
        ("facilities: hex country == province baseline owner", not bad_owner,
         f"{bad_owner[:4]}", True),
        ("facilities: no province holds more than its seat's slot total",
         not over_slots, f"{over_slots[:6]}", True),
        ("facilities: every steel mill's province holds a power plant (constraint 1)",
         not unpowered_mill_prov, f"{unpowered_mill_prov}", True),
        ("facilities: every nation's plant supply covers its draw (constraint 1)",
         not short, f"{short}", True),
    ]

    report = [f"Facilities in artifact: {len(placed)} on {len({hid(h) for h, _ in placed})} hexes; "
              f"{len(authored) - len(in_bbox)} authored facilities lie outside this bbox"]
    for n in sorted(count_by_nation):
        c = count_by_nation[n]
        report.append(f"  {n}: " + ", ".join(f"{k} {c[k]}" for k in KINDS if c[k])
                      + f"  | power {supply[n]} supply / {draw[n]} draw")
    full = [p for p, es in per_prov.items() if len(es) == SEAT_SLOTS.get(seats[p][1], 0)]
    report.append(f"  provinces left with NO free slot: {sorted(full) or 'none'}")
    return checks, report


def main(argv: list[str]) -> int:
    if len(argv) > 2 and argv[1] == "--check":
        data = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        checks, report = check_artifact(data)
        failed = [c for c in checks if not c[1]]
        for name, ok, detail, crop in checks:
            print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("  (crop-sensitive)" if crop else "")
                  + ("" if ok else f"  [{detail}]"))
        print("\n".join(report))
        return 1 if failed else 0

    rows = load_rows()
    errs = check_rows(rows)
    if errs:
        print("REFUSING to write — the authored table fails:")
        for e in errs:
            print(f"  {e}")
        return 1
    OUT_PATH.write_text(json.dumps(to_geojson(rows), ensure_ascii=False, indent=1) + "\n",
                        encoding="utf-8")
    c = Counter((r["country"], r["kind"]) for r in rows)
    print(f"Wrote {OUT_PATH.relative_to(ROOT)}: {len(rows)} facilities")
    for n in sorted({r["country"] for r in rows}):
        print(f"  {n}: " + ", ".join(f"{k} {c[(n, k)]}" for k in KINDS if c[(n, k)]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
