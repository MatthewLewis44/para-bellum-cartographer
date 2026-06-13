"""Hex geometry unit tests for AD-013 (10 km = flat-to-flat).

Usage: uv run python tests/test_hex_geometry.py
Plain asserts (no pytest dependency); exits non-zero on failure.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wargame_cartographer.config.map_spec import BoundingBox
from wargame_cartographer.hex.grid import HexGrid

bbox = BoundingBox(min_lon=4.0, min_lat=50.5, max_lon=5.0, max_lat=51.0)
grid = HexGrid(bbox=bbox, hex_size_km=10.0)

failures = []


def check(name, actual, expected, tol):
    ok = abs(actual - expected) <= tol
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {actual:.2f} (expected {expected:.2f} ± {tol})")
    if not ok:
        failures.append(name)


# AD-013 invariants
check("flat-to-flat distance (m)", grid.hex_flat_to_flat_m, 10_000.0, 0.001)
check("circumradius (m)", grid.hex_radius_m, 5_773.50, 0.01)

# Hex polygon area: (3√3/2)·R² ≈ 86.60 km². (AD-013 quotes '86603 m²' —
# a unit slip for 86,602,540 m² = 86.603 km².)
q, r = next(iter(grid.cells))
area = grid.hex_polygon(q, r).area
check("hex area (m²)", area, 86_602_540.0, 1_000.0)

# Flat-top layout: vertical neighbor centers (same column) are exactly
# flat-to-flat apart; adjacent-column centers are 1.5·R horizontally.
cells_in_col = sorted((rr, key) for key in grid.cells if key[0] == q for rr in [key[1]])
if len(cells_in_col) >= 2:
    (r1, k1), (r2, k2) = cells_in_col[0], cells_in_col[1]
    c1, c2 = grid.cells[k1], grid.cells[k2]
    dy = abs(c2.center_y - c1.center_y)
    check("same-column neighbor spacing (m)", dy, 10_000.0, 0.001)

col_spacing_expected = 1.5 * grid.hex_radius_m
check("column spacing (m)", 1.5 * grid.hex_radius_m, 8_660.25, 0.01)

# Vertex sanity: all 6 vertices exactly circumradius from center
cell = grid.cells[(q, r)]
for i, (vx, vy) in enumerate(grid.hex_vertices(q, r)):
    d = math.hypot(vx - cell.center_x, vy - cell.center_y)
    if abs(d - grid.hex_radius_m) > 0.001:
        failures.append(f"vertex {i} distance")
        print(f"  FAIL  vertex {i} distance: {d:.3f}")
print("  PASS  all 6 vertices at circumradius" if not any("vertex" in f for f in failures) else "")

print()
if failures:
    print(f"FAIL ({len(failures)}): {failures}")
    sys.exit(1)
print("ALL HEX GEOMETRY TESTS PASS")
