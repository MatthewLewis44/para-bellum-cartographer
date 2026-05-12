"""Hex coordinate utilities for Para Bellum.

Two coordinate systems in use:
  - Offset (col, row): 1-based, used in JSON output and Unity. Wargame-standard.
    Pointy-top hexes, odd columns shifted north.
  - Cube (q, r, s): used internally for math. q+r+s=0 always.
    Neighbors, distance, and range are trivial in cube space.

The upstream grid.py uses its own axial system tied to projection space.
These utilities are independent and used by our new pipeline stages.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Iterator


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
    """Offset coordinate. 1-based col/row. Odd columns shifted north (up)."""
    col: int  # 1-based, west → east
    row: int  # 1-based, north → south

    def hex_id(self) -> str:
        """Wargame hex ID: zero-padded CCCRR (e.g. col=5, row=1 → '00501')."""
        return f"{self.col:03d}{self.row:02d}"


# ---------------------------------------------------------------------------
# Conversion: offset ↔ cube
# Pointy-top, odd-column-north (odd-q offset)
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

# The 6 cube directions (pointy-top)
CUBE_DIRECTIONS: list[CubeCoord] = [
    CubeCoord(+1, -1,  0),  # 0: NE
    CubeCoord(+1,  0, -1),  # 1: E
    CubeCoord( 0, +1, -1),  # 2: SE
    CubeCoord(-1, +1,  0),  # 3: SW
    CubeCoord(-1,  0, +1),  # 4: W
    CubeCoord( 0, -1, +1),  # 5: NW
]

DIRECTION_NAMES = ["NE", "E", "SE", "SW", "W", "NW"]


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


# ---------------------------------------------------------------------------
# Range: all hexes within N steps
# ---------------------------------------------------------------------------

def cube_range(center: CubeCoord, n: int) -> Iterator[CubeCoord]:
    """Yield all cube coords within n steps of center (including center)."""
    for q in range(-n, n + 1):
        for r in range(max(-n, -q - n), min(n, -q + n) + 1):
            s = -q - r
            yield CubeCoord(center.q + q, center.r + r, center.s + s)


def offset_range(col: int, row: int, n: int) -> Iterator[OffsetCoord]:
    """Yield all offset coords within n steps of (col, row)."""
    center = offset_to_cube(col, row)
    for cube in cube_range(center, n):
        yield cube_to_offset(cube)


# ---------------------------------------------------------------------------
# River edge indexing
# Edge 0 = NE side, clockwise: 0=NE, 1=E, 2=SE, 3=SW, 4=W, 5=NW
# Matches CUBE_DIRECTIONS order.
# ---------------------------------------------------------------------------

def edge_index_to_direction(edge: int) -> str:
    """Human-readable direction for a river edge index (0–5)."""
    return DIRECTION_NAMES[edge % 6]


def shared_edge(a: OffsetCoord, b: OffsetCoord) -> int | None:
    """Return the edge index (0-5) of hex a that borders hex b, or None."""
    ca = offset_to_cube(a.col, a.row)
    cb = offset_to_cube(b.col, b.row)
    diff = cb - ca
    try:
        return CUBE_DIRECTIONS.index(diff)
    except ValueError:
        return None  # Not neighbors


# ---------------------------------------------------------------------------
# Grid bounds helper
# ---------------------------------------------------------------------------

@dataclass
class GridBounds:
    """Describes the full extent of the hex grid."""
    col_min: int
    col_max: int
    row_min: int
    row_max: int

    @property
    def num_cols(self) -> int:
        return self.col_max - self.col_min + 1

    @property
    def num_rows(self) -> int:
        return self.row_max - self.row_min + 1

    @property
    def total_hexes(self) -> int:
        # Approximate — actual count depends on bbox clipping
        return self.num_cols * self.num_rows

    def contains(self, col: int, row: int) -> bool:
        return (self.col_min <= col <= self.col_max and
                self.row_min <= row <= self.row_max)