"""Gas (Mantaflow smoke and fire): density continuity and loss through the domain walls (requires bpy).

    from worldagent_measure import gas

    gas.density(domain, frame_list(1, 120), max_drop_rel=0.2, max_jump_rel=0.5, max_boundary_fraction=0.05,
                guard_layers=2)

Category check for gas: density continuity and no unintended loss or escape [paper 3.2.2
'Category: Gas'; mantaflow-gas.en.md; material-playbooks.en.md 'Mantaflow Gas']. Per frame,
from the baked density grid of the evaluated domain: total density (sum x cell volume), peak,
non-finite cells, and the fraction of the total that sits in the guard layers next to the six
domain faces (gas pressing on or leaving through the domain walls). Mantaflow keeps the
outermost cell layer as its boundary layer and it always reads zero, also where gas leaves
through an open border, so the guard layers start at the second layer: guard_layers = 1 is
the second layer from each face, 2 the second and third, and so on, in cells of the grid
read. The outermost layer's share is still reported (boundary_layer_fraction, expected 0)
and so is each face's own share (guard_fraction_by_face; faces overlap at edges and
corners, so these do not add up to boundary_fraction). Between consecutive checked frames the
relative change of the total is bounded both ways, so a sudden loss (solver loss, cache
holes) and a sudden jump (popping, re-emission) fail. Telling solver loss from camera
cropping needs both this check and the shot coverage of the plume (`shot.coverage`).

Units: cell_size_m is in world metres (Mantaflow's cell_size is in the domain's local units,
so it is multiplied by the domain's object scale; an unapplied scale is handled), and the
total uses that cell volume. guard_depth_m = guard_layers x the smallest cell size.

Reads `density_grid` from the evaluated domain modifier, so the cache must be baked (or the
frames must be stepped in order for a replay cache). A frame with no grid at all raises
GasCacheMissing; an unbaked domain reads as an all-zero grid, so set min_total to catch it.
With noise upres the grid is the high-resolution one (noise_factor cells per base voxel per
axis); its cell size is derived from the grid length, and only its outermost noise cell
layer is empty, so guard layers are noise cells there. All thresholds, and guard_layers,
are required arguments.
"""

from __future__ import annotations

from typing import Sequence

import numpy as np

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


FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")  # domain-local axes of the grid


def grid_stats(domain, graph=None, *, guard_layers: int) -> dict:
    """Density statistics of the current frame.

    guard_layers  cell layers next to each domain face, counted from the second layer inward
                  (layer 0, Mantaflow's boundary layer, always reads zero), whose share of the total
                  is boundary_fraction; layers are cells of the grid read (noise cells with noise upres)
    """
    if guard_layers < 1:
        raise ValueError("guard_layers must be >= 1")
    graph = graph or depsgraph()
    settings = _domain_settings(domain, graph)
    grid = np.array(settings.density_grid, dtype=np.float64)
    res = [int(r) for r in settings.domain_resolution]
    if not grid.size or 0 in res:
        raise GasCacheMissing(f"{domain.name}: no density on frame {domain.users_scene[0].frame_current}")
    cells = res[0] * res[1] * res[2]
    factor = round((grid.size / cells) ** (1 / 3)) if cells else 1
    if factor < 1 or cells * factor ** 3 != grid.size:
        raise ValueError(f"{domain.name}: density grid of {grid.size} cells does not match resolution {res}")
    nx, ny, nz = (r * factor for r in res)
    # settings.cell_size is in the domain's local units; the object scale makes it world metres
    scale = [abs(v) for v in domain.evaluated_get(graph).matrix_world.to_scale()]
    cell = [c * k / factor for c, k in zip(settings.cell_size, scale)]
    volume = cell[0] * cell[1] * cell[2]
    finite_mask = np.isfinite(grid)
    non_finite = int(grid.size - finite_mask.sum())
    v = np.where(finite_mask, grid, 0.0).reshape(nz, ny, nx)
    total = float(v.sum())
    # depth of each cell = layers between it and the nearest face (0 on the outermost layer)
    depth = _depth(nz, ny, nx)
    guard = (depth >= 1) & (depth <= guard_layers)
    k = guard_layers + 1
    faces = {"x_min": v[:, :, 1:k], "x_max": v[:, :, max(nx - k, 0):nx - 1], "y_min": v[:, 1:k, :],
             "y_max": v[:, max(ny - k, 0):ny - 1, :], "z_min": v[1:k], "z_max": v[max(nz - k, 0):nz - 1]}

    def share(x):
        return float(x) / total if total > 0 else 0.0

    return {"total": total * volume, "peak": float(v.max()), "non_finite_cells": non_finite,
            "boundary_fraction": share(v[guard].sum()), "guard_layers": guard_layers,
            "guard_fraction_by_face": {f: share(faces[f].sum()) for f in FACES},
            "boundary_layer_fraction": share(v[depth == 0].sum()), "grid": [nx, ny, nz], "noise_factor": factor,
            "cell_size_m": cell, "guard_depth_m": guard_layers * min(cell)}


def _depth(nz: int, ny: int, nx: int):
    z, y, x = (np.minimum(np.arange(n), np.arange(n)[::-1]) for n in (nz, ny, nx))
    return np.minimum(np.minimum(z[:, None, None], y[None, :, None]), x[None, None, :])


def density(domain, frames: Sequence[int], *, max_drop_rel: float, max_jump_rel: float,
            max_boundary_fraction: float, guard_layers: int, min_total: float | None = None,
            name: str = "gas_density") -> Check:
    """Finite density, bounded frame-to-frame change of the total, and little gas next to the domain walls.

    max_drop_rel           largest allowed (prev - now) / prev between consecutive checked frames
    max_jump_rel           largest allowed (now - prev) / prev
    max_boundary_fraction  largest allowed share of the total in the guard layers (boundary_fraction)
    guard_layers           required: guard depth in cells, counted from the second layer inward (grid_stats)
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
        stats = grid_stats(domain, guard_layers=guard_layers)
        row = {"frame": frame, "total": stats["total"], "peak": stats["peak"],
               "boundary_fraction": stats["boundary_fraction"],
               "guard_fraction_by_face": stats["guard_fraction_by_face"],
               "boundary_layer_fraction": stats["boundary_layer_fraction"],
               "non_finite_cells": stats["non_finite_cells"], "change_rel": None}
        if stats["non_finite_cells"]:
            fail({"frame": frame, "problem": "non-finite density", "cells": stats["non_finite_cells"]})
        if stats["boundary_fraction"] > max_boundary_fraction:
            face = max(FACES, key=lambda f: stats["guard_fraction_by_face"][f])
            fail({"frame": frame, "problem": "gas on the domain walls", "boundary_fraction": stats["boundary_fraction"],
                  "face": face})
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
    measurements = {"frames": rows, "grid": stats["grid"], "noise_factor": stats["noise_factor"],
                    "cell_size_m": stats["cell_size_m"], "guard_depth_m": stats["guard_depth_m"],
                    "largest_drop_rel": max((-c for c in changes), default=0.0),
                    "largest_jump_rel": max(changes, default=0.0),
                    "largest_boundary_fraction": max(r["boundary_fraction"] for r in rows),
                    "thresholds": {"max_drop_rel": max_drop_rel, "max_jump_rel": max_jump_rel,
                                   "max_boundary_fraction": max_boundary_fraction, "guard_layers": guard_layers,
                                   "min_total": min_total}}
    return Check(name, first_failure is None, measurements, first_failure)
