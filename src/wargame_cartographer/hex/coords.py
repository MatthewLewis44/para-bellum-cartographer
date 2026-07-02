"""Hex coordinate utilities for Para Bellum.

Two coordinate systems in use:
  - Offset (col, row): 1-based, used in JSON output and Unity. Wargame-standard.
    FLAT-TOP hexes, odd-q offset (AD-012). col runs west → east, row runs
    south → north.
  - Cube (q, r, s): used internally for math. q+r+s=0 always.

The upstream grid.py uses its own axial system tied to projection space and is
the AUTHORITATIVE source for grid geometry.

⚠️  KNOWN DEFECT (surfaced pre-Sprint-6; NOT fixed in this doc pass because a fix
    changes terrain output, which is out of scope): ``offset_neighbors`` here
    does NOT agree with the correct ``grid.HexGrid.neighbors`` — its neighbour
    set differs on every cell tested (its ``CUBE_DIRECTIONS`` are a pointy-top
    vector set paired with a flat-top odd-q offset conversion, which is
    internally inconsistent). ``sampler`` uses ``offset_neighbors`` ONLY for the
    ``is_coastal`` flag, so that flag is computed against the wrong neighbours.
    Reconcile with ``grid.neighbors`` (the verified flat-top implementation) and
    Unity ``HexCoord.cs`` before Sprint 7 unit movement builds on these
    directions. The per-index compass labels have been removed rather than left
    wrong.
"""

from __future__ import annotations
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CubeCoord:
    """Cube coordinate. Invariant: q + r + s == 0."""
    q: int
    r: int
    s: int

    def __post_init__(self):
        assert self.q + self.r + self.s == 0, (
            f"Invalid cube coord: {self.q}+{self.r}+{self.s} != 0"
        )

    def __add__(self, other: "CubeCoord") -> "CubeCoord":
        return CubeCoord(self.q + other.q, self.r + other.r, self.s + other.s)

    def __sub__(self, other: "CubeCoord") -> "CubeCoord":
        return CubeCoord(self.q - other.q, self.r - other.r, self.s - other.s)


@dataclass(frozen=True)
class OffsetCoord:
    """Offset coordinate. 1-based col/row, flat-top odd-q (AD-012)."""
    col: int  # 1-based, west → east
    row: int  # 1-based, south → north

    def hex_id(self) -> str:
        """Wargame hex ID: zero-padded CCCRR (e.g. col=5, row=1 → '00501')."""
        return f"{self.col:03d}{self.row:02d}"


# ---------------------------------------------------------------------------
# Conversion: offset ↔ cube — flat-top odd-q (AD-012)
# Reference: https://www.redblobgames.com/grids/hexes/
# ---------------------------------------------------------------------------

def offset_to_cube(col: int, row: int) -> CubeCoord:
    """Convert 1-based offset (col, row) to cube coordinates."""
    # Shift to 0-based for the math
    c = col - 1
    r = row - 1
    q = c
    cube_r = r - (c - (c & 1)) // 2  # odd-col offset
    s = -q - cube_r
    return CubeCoord(q, cube_r, s)


def cube_to_offset(cube: CubeCoord) -> OffsetCoord:
    """Convert cube coordinates to 1-based offset (col, row)."""
    col = cube.q + 1  # back to 1-based
    row = cube.r + (cube.q - (cube.q & 1)) // 2 + 1
    return OffsetCoord(col, row)


# ---------------------------------------------------------------------------
# Neighbors
# ---------------------------------------------------------------------------

# The 6 cube neighbour steps (indices 0-5). ⚠️ Per-direction COMPASS LABELS
# REMOVED — the old NE/E/SE/SW/W/NW labels were a pointy-top set and do not match
# the projected flat-top layout (see the module ⚠️ note). The VECTORS are
# UNCHANGED (they determine offset_neighbors' output). Reconcile against
# grid.neighbors / Unity HexCoord.cs before assigning semantic directions.
CUBE_DIRECTIONS: list[CubeCoord] = [
    CubeCoord(+1, -1,  0),  # index 0
    CubeCoord(+1,  0, -1),  # index 1
    CubeCoord( 0, +1, -1),  # index 2
    CubeCoord(-1, +1,  0),  # index 3
    CubeCoord(-1,  0, +1),  # index 4
    CubeCoord( 0, -1, +1),  # index 5
]


def cube_neighbors(cube: CubeCoord) -> list[CubeCoord]:
    """Return all 6 cube neighbors (may be off-grid — caller filters)."""
    return [cube + d for d in CUBE_DIRECTIONS]


def offset_neighbors(col: int, row: int) -> list[OffsetCoord]:
    """Return all 6 offset neighbors of a hex (may be off-grid)."""
    cube = offset_to_cube(col, row)
    return [cube_to_offset(n) for n in cube_neighbors(cube)]


# ---------------------------------------------------------------------------
# Distance
# ---------------------------------------------------------------------------

def cube_distance(a: CubeCoord, b: CubeCoord) -> int:
    """Hex distance in steps between two cube coords."""
    return max(abs(a.q - b.q), abs(a.r - b.r), abs(a.s - b.s))


def offset_distance(col1: int, row1: int, col2: int, row2: int) -> int:
    """Hex distance in steps between two offset coords."""
    return cube_distance(
        offset_to_cube(col1, row1),
        offset_to_cube(col2, row2),
    )