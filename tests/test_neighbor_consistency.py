"""Neighbor-implementation gate (Sprint 6 P0-A fix 1).

Guards the invariant that exactly ONE hex-adjacency implementation exists
(``HexGrid.neighbors`` + the shared ``OFFSET_NEIGHBOR_DELTAS`` table) and that
it matches the grid's actual geometry. History: ``hex/coords.py`` carried a
second, parity-inconsistent ``offset_neighbors`` that silently computed
``is_coastal`` against the wrong neighbours on odd-q_min bboxes (Belgium,
wceurope). It was deleted; this gate keeps a replacement from diverging.

Checks:
  1. Grid parity normalization: ``_col_offset`` is ODD for every bbox, so
     exported col parity == internal q parity (odd JSON col == shifted north).
  2. Geometry: every neighbor pair is exactly one hex-spacing apart; interior
     cells have exactly 6 neighbors.
  3. OFFSET_NEIGHBOR_DELTAS reproduces HexGrid.neighbors exactly, keyed by
     either q parity or exported col parity (they must be equal).
  4. If a ``wargame_cartographer.hex.coords`` module reappears, any callable
     with "neighbor" in its name must agree with grid.neighbors on every cell.

Usage: uv run python tests/test_neighbor_consistency.py
Plain asserts (no pytest dependency); exits non-zero on failure.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.hex.grid import HexGrid, OFFSET_NEIGHBOR_DELTAS

failures: list[str] = []


def check(name: str, ok: bool, detail: str = ""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        failures.append(name)


# Bboxes chosen so the UN-normalized q_min parity differs (Belgium-like was
# odd, Benelux-like was even) — the normalization must make both odd.
BBOXES = {
    "belgium-like": BoundingBox(min_lon=2.5, min_lat=49.4, max_lon=6.4, max_lat=51.6),
    "benelux-like": BoundingBox(min_lon=2.5, min_lat=49.4, max_lon=8.8, max_lat=53.6),
    "small": BoundingBox(min_lon=4.0, min_lat=50.5, max_lon=5.0, max_lat=51.0),
}

for name, bbox in BBOXES.items():
    grid = HexGrid(bbox=bbox, hex_size_km=10.0)

    # --- 1. Parity normalization ---
    check(f"[{name}] _col_offset is odd (parity-normalized)",
          grid._col_offset % 2 == 1, f"_col_offset={grid._col_offset}")

    # --- 2. Geometric adjacency: neighbor centers exactly one spacing away ---
    # Flat-top: same-column neighbors are flat_to_flat apart; diagonal
    # neighbors are also exactly flat_to_flat apart (regular tessellation).
    spacing = grid.hex_flat_to_flat_m
    bad_dist = 0
    n_pairs = 0
    interior_bad = 0
    n_interior = 0
    for (q, r) in grid.cells:
        nbrs = grid.neighbors(q, r)
        for (nq, nr) in nbrs:
            d = grid.distance(q, r, nq, nr)
            n_pairs += 1
            if abs(d - spacing) > 0.01:
                bad_dist += 1
        # interior cell: all 6 candidate deltas land in the grid
        if len(nbrs) == 6:
            n_interior += 1
        elif len(nbrs) > 6:
            interior_bad += 1
    check(f"[{name}] all neighbor pairs exactly {spacing:.0f} m apart",
          bad_dist == 0, f"{bad_dist}/{n_pairs} wrong-distance pairs")
    check(f"[{name}] interior cells have exactly 6 neighbors, none more",
          interior_bad == 0 and n_interior > 0,
          f"{n_interior} interior cells")

    # --- 3. OFFSET_NEIGHBOR_DELTAS == grid.neighbors, via BOTH parities ---
    mismatch_q = 0
    mismatch_col = 0
    for (q, r) in grid.cells:
        good = set(grid.neighbors(q, r))
        via_q = {(q + dq, r + dr) for dq, dr in OFFSET_NEIGHBOR_DELTAS[q % 2]
                 if (q + dq, r + dr) in grid.cells}
        col = q - grid._col_offset + 1
        via_col = {(q + dq, r + dr) for dq, dr in OFFSET_NEIGHBOR_DELTAS[col % 2]
                   if (q + dq, r + dr) in grid.cells}
        if via_q != good:
            mismatch_q += 1
        if via_col != good:
            mismatch_col += 1
    check(f"[{name}] OFFSET_NEIGHBOR_DELTAS[q%2] == grid.neighbors",
          mismatch_q == 0, f"{mismatch_q}/{grid.hex_count} mismatches")
    check(f"[{name}] OFFSET_NEIGHBOR_DELTAS[col%2] == grid.neighbors "
          f"(exported-parity view)",
          mismatch_col == 0, f"{mismatch_col}/{grid.hex_count} mismatches")

# --- 4. No second neighbor implementation may silently diverge ---
try:
    from wargame_cartographer.hex import coords as _coords  # noqa: F401
except ImportError:
    _coords = None
    check("hex.coords module absent (single implementation)", True)

if _coords is not None:
    grid = HexGrid(bbox=BBOXES["small"], hex_size_km=10.0)
    helpers = [n for n in dir(_coords)
               if "neighbor" in n.lower() and callable(getattr(_coords, n))]
    if not helpers:
        check("hex.coords exists but defines no neighbor helpers", True)
    for helper_name in helpers:
        fn = getattr(_coords, helper_name)
        n_bad = 0
        for (q, r) in grid.cells:
            col = q - grid._col_offset + 1
            row = r - grid._row_offset + 1
            good = set(grid.neighbors(q, r))
            try:
                raw = fn(col, row)
                got = set()
                for item in raw:
                    c, w = (item.col, item.row) if hasattr(item, "col") else item
                    key = (c + grid._col_offset - 1, w + grid._row_offset - 1)
                    if key in grid.cells:
                        got.add(key)
            except Exception as exc:  # a helper that errors also fails the gate
                n_bad = -1
                detail = f"raised {exc!r}"
                break
            if got != good:
                n_bad += 1
        check(f"coords.{helper_name} matches grid.neighbors on every cell",
              n_bad == 0,
              detail if n_bad == -1 else f"{n_bad}/{grid.hex_count} mismatches")

print()
if failures:
    print(f"FAIL: {len(failures)} check(s) failed")
    sys.exit(1)
print("OK: neighbor-consistency gate passed")
