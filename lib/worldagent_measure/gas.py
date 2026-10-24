"""Gas (Mantaflow smoke and fire): density continuity and loss through the domain walls (requires bpy).

    from worldagent_measure import gas

    gas.density(domain, frame_list(1, 120), max_drop_rel=0.2, max_jump_rel=0.5, max_boundary_fraction=0.05)

Category check for gas: density continuity and no unintended loss or escape [paper 3.2.2
'Category: Gas'; mantaflow-gas.en.md; material-playbooks.en.md 'Mantaflow Gas']. Per frame,
from the baked density grid of the evaluated domain: total density (sum x cell volume), peak,
non-finite cells, and the fraction of the total that sits in the outermost cell layer (gas
pressing on or leaving through the domain walls). Between consecutive checked frames the
relative change of the total is bounded both ways, so a sudden loss (solver loss, cache
holes) and a sudden jump (popping, re-emission) fail. Telling solver loss from camera
cropping needs both this check and the shot coverage of the plume (`shot.coverage`).

Reads `density_grid` from the evaluated domain modifier, so the cache must be baked (or the
frames must be stepped in order for a replay cache). A frame with no grid at all raises
GasCacheMissing; an unbaked domain reads as an all-zero grid, so set min_total to catch it. With noise upres the grid is the high-resolution one; its cell size is
derived from the grid length. All thresholds are required arguments.
"""

from __future__ import annotations

import math
from typing import Sequence

from .geometry import depsgraph, set_frame
from .verifier import Check


class GasCacheMissing(RuntimeError):
    """The domain has no density on this frame: not baked, or the frame is outside the cache."""


def _domain_settings(domain, graph):
    for mod in domain.evaluated_get(graph).modifiers:
        if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            if mod.domain_settings.domain_type != "GAS":
                raise ValueError(f"{domain.name} is a {mod.domain_settings.domain_type} domain, not GAS")
            return mod.domain_settings
    raise ValueError(f"{domain.name} has no fluid domain modifier")


def grid_stats(domain, graph=None) -> dict:
    """Density statistics of the current frame."""
    graph = graph or depsgraph()
    settings = _domain_settings(domain, graph)
    grid = list(settings.density_grid)
    res = [int(r) for r in settings.domain_resolution]
    if not grid or 0 in res:
        raise GasCacheMissing(f"{domain.name}: no density on frame {domain.users_scene[0].frame_current}")
    cells = res[0] * res[1] * res[2]
    factor = round((len(grid) / cells) ** (1 / 3)) if cells else 1
    if factor < 1 or res[0] * res[1] * res[2] * factor ** 3 != len(grid):
        raise ValueError(f"{domain.name}: density grid of {len(grid)} cells does not match resolution {res}")
    nx, ny, nz = (r * factor for r in res)
    cell = [c / factor for c in settings.cell_size]
    volume = cell[0] * cell[1] * cell[2]
    non_finite = sum(1 for v in grid if not math.isfinite(v))
    finite = [v if math.isfinite(v) else 0.0 for v in grid]
    total = sum(finite)
    boundary = 0.0
    for z in range(nz):
        for y in range(ny):
            row = (z * ny + y) * nx
            if z in (0, nz - 1) or y in (0, ny - 1):
                boundary += sum(finite[row:row + nx])
            else:
                boundary += finite[row] + finite[row + nx - 1]
    return {"total": total * volume, "peak": max(finite), "non_finite_cells": non_finite,
            "boundary_fraction": boundary / total if total > 0 else 0.0, "grid": [nx, ny, nz],
            "cell_size_m": cell}


def density(domain, frames: Sequence[int], *, max_drop_rel: float, max_jump_rel: float,
            max_boundary_fraction: float, min_total: float | None = None, name: str = "gas_density") -> Check:
    """Finite density, bounded frame-to-frame change of the total, and little gas on the domain walls.

    max_drop_rel           largest allowed (prev - now) / prev between consecutive checked frames
    max_jump_rel           largest allowed (now - prev) / prev
    max_boundary_fraction  largest allowed share of the total in the outermost cell layer
    min_total              optional: total below this on any frame fails (the gas vanished)
    Frames where the previous total is zero (before emission starts) skip the relative checks.
    """
    if not frames or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be non-empty and strictly increasing: {list(frames)}")
    if max_drop_rel < 0 or max_jump_rel < 0 or not 0 <= max_boundary_fraction <= 1:
        raise ValueError("thresholds out of range")
    rows, first_failure, previous = [], None, None

    def fail(record):
        nonlocal first_failure
        if first_failure is None:
            first_failure = record

    for frame in frames:
        set_frame(frame)
        stats = grid_stats(domain)
        row = {"frame": frame, "total": stats["total"], "peak": stats["peak"],
               "boundary_fraction": stats["boundary_fraction"], "non_finite_cells": stats["non_finite_cells"],
               "change_rel": None}
        if stats["non_finite_cells"]:
            fail({"frame": frame, "problem": "non-finite density", "cells": stats["non_finite_cells"]})
        if stats["boundary_fraction"] > max_boundary_fraction:
            fail({"frame": frame, "problem": "gas on the domain walls", "boundary_fraction": stats["boundary_fraction"]})
        if min_total is not None and stats["total"] < min_total:
            fail({"frame": frame, "problem": "total density below minimum", "total": stats["total"]})
        if previous is not None and previous > 0:
            change = (stats["total"] - previous) / previous
            row["change_rel"] = change
            if -change > max_drop_rel:
                fail({"frame": frame, "problem": "sudden density loss", "change_rel": change})
            elif change > max_jump_rel:
                fail({"frame": frame, "problem": "sudden density jump", "change_rel": change})
        previous = stats["total"]
        rows.append(row)
    changes = [r["change_rel"] for r in rows if r["change_rel"] is not None]
    measurements = {"frames": rows, "grid": stats["grid"], "cell_size_m": stats["cell_size_m"],
                    "largest_drop_rel": max((-c for c in changes), default=0.0),
                    "largest_jump_rel": max(changes, default=0.0),
                    "largest_boundary_fraction": max(r["boundary_fraction"] for r in rows),
                    "thresholds": {"max_drop_rel": max_drop_rel, "max_jump_rel": max_jump_rel,
                                   "max_boundary_fraction": max_boundary_fraction, "min_total": min_total}}
    return Check(name, first_failure is None, measurements, first_failure)
