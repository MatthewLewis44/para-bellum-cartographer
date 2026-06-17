"""Global reconcile passes for the streaming pipeline (Sprint 4 T3/T4).

The two cross-tile passes — coastal flag (pass 2) and multi-hex urban sprawl
(pass 3, AD-020) — are deferred out of per-tile sampling and run once over the
merged grid here. A city footprint (especially the Ruhr) crosses any reasonable
tile boundary, and a hex is coastal iff a *neighbor* (possibly in another tile)
is water; both are impossible to compute tile-locally.

This module is a thin orchestration layer over the sampler's pass functions so
the streaming merger and the monolithic pipeline run byte-identical logic.
"""

from __future__ import annotations

from wargame_cartographer.hex.sampler import _assign_coastal, _assign_urban_sprawl


def apply_global_passes(
    result, grid, settlement_by_hex, provinces=None, settlements_gdf=None
) -> None:
    """Run the global reconcile passes over the merged grid.

    coastal (pass 2) → urban sprawl (pass 3) → admin tiers (AD-023, Sprint 5).
    All three need cross-tile knowledge. ``result`` MUST be assembled in
    ``grid.cells`` iteration order before this call: coastal/sprawl resolve
    exact-distance overlap ties (e.g. between adjacent Ruhr cities) by
    first-seen / strict-less, so a tile-order assembly would flip
    parent_city/anthrome at seams. admin_tier runs last because it reads (and
    for capitals, may upgrade) settlement state. Mutates ``result`` in place.
    """
    _assign_coastal(result, grid)
    _assign_urban_sprawl(result, grid, settlement_by_hex)
    if provinces is not None:
        from wargame_cartographer.geo.provinces import assign_admin_tiers
        assign_admin_tiers(result, grid, provinces, settlements_gdf)
