"""Gas (Mantaflow smoke and fire): setup before the bake, cache coverage, density continuity and loss
through the domain walls, clearance to the domain faces (requires bpy).

    from worldagent_measure import gas

    gas.gas_setup(domain, 1, 120, flows={"Torch": 40}, obstacles=[ceiling], cache_root=".work/cache",
                  expected_time_scale=1.0, min_source_voxels=4, max_flow_step_voxels=1.0,
                  min_flow_clearance_voxels=2)                      # before the bake
    gas.cache_coverage(domain, 1, 120)                              # first in every row that reads the cache
    gas.density(domain, frame_list(1, 120), max_drop_rel=0.2, max_jump_rel=0.5, max_boundary_fraction=0.05,
                guard_layers=2)
    gas.boundary_clearance(domain, frame_list(1, 120, 10), significant_density=0.01, guard_layers=2,
                           min_clearance_voxels=2, max_guard_fraction=None)

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

gas_setup (before the bake; never bakes or writes a cache) measures the Domain (world voxel
size per axis, resolution, borders, Noise, Adaptive Domain), time (fps, time_scale against the
spec's), the cache (type, range, folder) and, on every frame of the story range with every
Fluid Domain modifier switched off (so nothing is simulated), each Flow (type, emission,
active frames against the frame it may first emit, voxels across it, position in the Domain,
move per substep) and each Effector (on every frame, in the Domain's collection, rendered or a
listed proxy for a declared visible obstacle). cache_coverage checks the cache read is the one
baked: type, range, flags, one data (and noise) file per frame, grid length. boundary_clearance
measures, per frame, the box of cells above significant_density: its clearance to each Domain
face in voxels and metres, the gas in the guard layers of each face, centroid, occupied
volume and how fast each face of the box moves.

Border flags map to faces of the domain-local axes: left/right = x_min/x_max, front/back =
y_min/y_max, bottom/top = z_min/z_max. An Adaptive Domain moves its grid inside the Domain, so
positions next to the Domain walls cannot be read from it: boundary_clearance, and density
with a max_boundary_fraction, fail with verdict UNVERIFIED there (record the row as limited,
or use a fixed Domain). A REPLAY cache fails gas_setup and cache_coverage: verifiers read frames
out of order, and density / boundary_clearance would simulate a REPLAY domain while stepping
frames, so run cache_coverage first in the same row.

Units: cell_size_m is in world metres (Mantaflow's cell_size is in the domain's local units,
so it is multiplied by the domain's object scale; an unapplied scale is handled), and the
total uses that cell volume. guard_depth_m = guard_layers x the smallest cell size. Flow and
Effector surface_distance is in voxels (surface_emission_m, surface_thickness_m = that x the
smallest voxel).

Reads `density_grid` from the evaluated domain modifier, so the cache must be baked (or the
frames must be stepped in order for a replay cache). A frame with no grid at all raises
GasCacheMissing (density, boundary_clearance); an unbaked domain reads as an all-zero grid, so
set min_total to catch it. With noise upres the grid is the high-resolution one (noise_factor
cells per base voxel per axis); its cell size is derived from the grid length, and only its
outermost noise cell layer is empty, so guard layers are noise cells there. All thresholds,
and guard_layers, are required arguments; a threshold written as None is measured and
reported, not checked.
"""

from __future__ import annotations

import json
import math
import os
import sys
from contextlib import contextmanager
from typing import Iterable, Mapping, Sequence

import bpy
import numpy as np
from mathutils import Vector

from . import shot, systems
from .geometry import depsgraph, set_frame, world_mesh
from .verifier import CacheMissing, Check


class GasCacheMissing(CacheMissing):
    """The domain has no density on this frame: not baked, or the frame is outside the cache."""


FACES = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")  # domain-local axes of the grid
BORDERS = {"x_min": "left", "x_max": "right", "y_min": "front", "y_max": "back", "z_min": "bottom",
           "z_max": "top"}
_EXT = {"OPENVDB": "vdb", "UNI": "uni", "RAW": "raw"}
# Voxel counts come from float32 vertex coordinates; a comparison against a voxel threshold allows this
# much rounding so an exact count (say 1.0000001 against 1) does not fail. Numeric only.
_VOXEL_EPS = 1e-6
UNVERIFIED = {"problem": "adaptive Domain: voxel positions in the Domain are not readable", "verdict": "UNVERIFIED"}


def _domain_modifier(domain):
    """The original (not evaluated) Fluid DOMAIN modifier of a GAS domain."""
    for mod in domain.modifiers:
        if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            if mod.domain_settings.domain_type != "GAS":
                raise ValueError(f"{domain.name} is a {mod.domain_settings.domain_type} domain, not GAS")
            return mod
    raise ValueError(f"{domain.name} has no fluid domain modifier")


def _domain_settings(domain, graph):
    for mod in domain.evaluated_get(graph).modifiers:
        if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            if mod.domain_settings.domain_type != "GAS":
                raise ValueError(f"{domain.name} is a {mod.domain_settings.domain_type} domain, not GAS")
            return mod.domain_settings
    raise ValueError(f"{domain.name} has no fluid domain modifier")


def _read_grid(domain, graph) -> dict:
    """Density grid of the current frame as v[nz, ny, nx] (x fastest), with resolution and world cell size."""
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
    finite_mask = np.isfinite(grid)
    return {"settings": settings, "v": np.where(finite_mask, grid, 0.0).reshape(nz, ny, nx), "res": res,
            "factor": factor, "cell_m": cell, "non_finite": int(grid.size - finite_mask.sum()),
            "adaptive": bool(settings.use_adaptive_domain)}


def _face_slices(v, k: int) -> dict:
    """Layers 1 .. k - 1 counted from each face (layer 0, the boundary layer, left out)."""
    nz, ny, nx = v.shape
    return {"x_min": v[:, :, 1:k], "x_max": v[:, :, max(nx - k, 0):nx - 1], "y_min": v[:, 1:k, :],
            "y_max": v[:, max(ny - k, 0):ny - 1, :], "z_min": v[1:k], "z_max": v[max(nz - k, 0):nz - 1]}


def grid_stats(domain, graph=None, *, guard_layers: int) -> dict:
    """Density statistics of the current frame.

    guard_layers  cell layers next to each domain face, counted from the second layer inward
                  (layer 0, Mantaflow's boundary layer, always reads zero), whose share of the total
                  is boundary_fraction; layers are cells of the grid read (noise cells with noise upres)
    adaptive is True on an Adaptive Domain: its layers then sit on the adaptive box, not the Domain walls.
    """
    if guard_layers < 1:
        raise ValueError("guard_layers must be >= 1")
    g = _read_grid(domain, graph or depsgraph())
    v, cell = g["v"], g["cell_m"]
    nz, ny, nx = v.shape
    volume = cell[0] * cell[1] * cell[2]
    total = float(v.sum())
    # depth of each cell = layers between it and the nearest face (0 on the outermost layer)
    depth = _depth(nz, ny, nx)
    guard = (depth >= 1) & (depth <= guard_layers)
    faces = _face_slices(v, guard_layers + 1)

    def share(x):
        return float(x) / total if total > 0 else 0.0

    return {"total": total * volume, "peak": float(v.max()), "non_finite_cells": g["non_finite"],
            "boundary_fraction": share(v[guard].sum()), "guard_layers": guard_layers,
            "guard_fraction_by_face": {f: share(faces[f].sum()) for f in FACES},
            "boundary_layer_fraction": share(v[depth == 0].sum()), "grid": [nx, ny, nz], "noise_factor": g["factor"],
            "cell_size_m": cell, "guard_depth_m": guard_layers * min(cell), "adaptive": g["adaptive"]}


def _depth(nz: int, ny: int, nx: int):
    z, y, x = (np.minimum(np.arange(n), np.arange(n)[::-1]) for n in (nz, ny, nx))
    return np.minimum(np.minimum(z[:, None, None], y[None, :, None]), x[None, None, :])


def density(domain, frames: Sequence[int], *, max_drop_rel: float, max_jump_rel: float,
            max_boundary_fraction: float | None, guard_layers: int, min_total: float | None = None,
            name: str = "gas_density") -> Check:
    """Finite density, bounded frame-to-frame change of the total, and little gas next to the domain walls.

    max_drop_rel           largest allowed (prev - now) / prev between consecutive checked frames
    max_jump_rel           largest allowed (now - prev) / prev
    max_boundary_fraction  required: largest allowed share of the total in the guard layers
                           (boundary_fraction); None: reported, not checked. On an Adaptive Domain a
                           number fails UNVERIFIED (the guard layers sit on the adaptive box, not the
                           Domain walls)
    guard_layers           required: guard depth in cells, counted from the second layer inward (grid_stats)
    min_total              optional: total below this on any frame fails (the gas vanished)
    Frames where the previous total is zero (before emission starts) skip the relative checks.
    Steps frames: on a REPLAY cache that simulates, so run cache_coverage in the same row first.
    """
    if not frames or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be non-empty and strictly increasing: {list(frames)}")
    if max_drop_rel < 0 or max_jump_rel < 0 or (max_boundary_fraction is not None
                                                 and not 0 <= max_boundary_fraction <= 1):
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
        if stats["adaptive"]:
            row["adaptive_grid"] = stats["grid"]
        if stats["non_finite_cells"]:
            fail({"frame": frame, "problem": "non-finite density", "cells": stats["non_finite_cells"]})
        if max_boundary_fraction is not None and stats["adaptive"]:
            fail({"frame": frame, **UNVERIFIED})
        elif max_boundary_fraction is not None and stats["boundary_fraction"] > max_boundary_fraction:
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
                    "adaptive": stats["adaptive"],
                    "largest_drop_rel": max((-c for c in changes), default=0.0),
                    "largest_jump_rel": max(changes, default=0.0),
                    "largest_boundary_fraction": max(r["boundary_fraction"] for r in rows),
                    "thresholds": {"max_drop_rel": max_drop_rel, "max_jump_rel": max_jump_rel,
                                   "max_boundary_fraction": max_boundary_fraction, "guard_layers": guard_layers,
                                   "min_total": min_total}}
    return Check(name, first_failure is None, measurements, first_failure)


# --- shared by gas_setup, cache_coverage and boundary_clearance ---------------------------------

def _scene_of(obj):
    return obj.users_scene[0] if obj.users_scene else bpy.context.scene


def _local_box(domain):
    """(lo, size) of the domain mesh in object space: the box Mantaflow fills with the grid."""
    co = np.array([v.co[:] for v in domain.data.vertices], dtype=np.float64)
    if not len(co):
        raise ValueError(f"{domain.name} has no vertices")
    lo = co.min(axis=0)
    return lo, co.max(axis=0) - lo


def _to_grid(points, domain, lo, size, res):
    """World points (N, 3) -> grid coordinates of the domain (0 .. res per axis, domain-local axes)."""
    inv = np.array(domain.matrix_world.inverted(), dtype=np.float64)
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    local = pts @ inv[:3, :3].T + inv[:3, 3]
    return (local - lo) / size * np.asarray(res, dtype=np.float64)


def _box_in_grid(g, res) -> tuple[float, dict]:
    """(fraction of the box of grid points g inside the domain, clearance in voxels to each face)."""
    a_lo, a_hi = g.min(axis=0), g.max(axis=0)
    fraction = 1.0
    for i in range(3):
        extent = a_hi[i] - a_lo[i]
        if extent <= 1e-12:
            fraction *= 1.0 if 0 <= a_lo[i] <= res[i] else 0.0
        else:
            fraction *= max(0.0, min(a_hi[i], res[i]) - max(a_lo[i], 0.0)) / extent
    clearance = {}
    for i, axis in enumerate("xyz"):
        clearance[f"{axis}_min"] = float(a_lo[i])
        clearance[f"{axis}_max"] = float(res[i] - a_hi[i])
    return fraction, clearance


def _intervals(frames: Iterable[int]) -> list[list[int]]:
    out = []
    for f in sorted(frames):
        if out and f == out[-1][1] + 1:
            out[-1][1] = f
        else:
            out.append([f, f])
    return out


def _saved_cache_path(domain, mod) -> tuple[str, bool]:
    """(cache_directory as saved in the .blend, rebased): the runner's rebase record when it ran in this process."""
    runtime = sys.modules.get("worldagent_runtime")
    entries = runtime.applied_rebases() if runtime is not None and hasattr(runtime, "applied_rebases") else []
    block = f"Object[{json.dumps(domain.name, ensure_ascii=False)}]"
    prop = f"modifiers[{json.dumps(mod.name, ensure_ascii=False)}].domain_settings.cache_directory"
    for entry in entries:
        if entry.get("datablock") == block and entry.get("property") == prop:
            return entry["from"], True
    return mod.domain_settings.cache_directory, False


def _missing_files(directory: str, sub: str, stem: str, fmt: str, frames: Iterable[int]) -> list[int]:
    """Frames of `frames` without a <directory>/<sub>/<stem>_####.<ext> file."""
    ext = _EXT.get(fmt, fmt.lower())
    return [f for f in frames if not os.path.isfile(os.path.join(directory, sub, f"{stem}_{f:04d}.{ext}"))]


def _writable(path: str) -> bool:
    """path, or its nearest existing parent folder (where Blender would create it), is writable."""
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return os.access(path, os.W_OK | os.X_OK)


def _cache_record(domain, mod, frame_start: int, frame_end: int) -> dict:
    s = mod.domain_settings
    saved, rebased = _saved_cache_path(domain, mod)
    resolved = os.path.normpath(bpy.path.abspath(s.cache_directory))
    frames = range(frame_start, frame_end + 1)
    missing_data = _missing_files(resolved, "data", "fluid_data", s.cache_data_format, frames)
    missing_noise = (_missing_files(resolved, "noise", "fluid_noise", s.cache_noise_format, frames)
                     if s.use_noise else [])
    return {"cache_type": s.cache_type, "cache_data_format": s.cache_data_format,
            "cache_noise_format": s.cache_noise_format, "cache_frame_start": s.cache_frame_start,
            "cache_frame_end": s.cache_frame_end, "cache_directory": s.cache_directory,
            "cache_directory_saved": saved, "relative": saved.startswith("//"), "rebased": rebased,
            "cache_directory_resolved": resolved, "exists": os.path.isdir(resolved), "writable": _writable(resolved),
            "has_cache_baked_data": bool(s.has_cache_baked_data),
            "has_cache_baked_noise": bool(s.has_cache_baked_noise),
            "data_files_in_range": len(frames) - len(missing_data),
            "noise_files_in_range": len(frames) - len(missing_noise) if s.use_noise else 0,
            "missing_data_frames": missing_data, "missing_noise_frames": missing_noise}


def _cache_problems(cache: dict, frame_start: int, frame_end: int) -> list[dict]:
    problems = []
    if cache["cache_type"] == "REPLAY":
        problems.append({"problem": "REPLAY cache: verifiers read frames out of order and stepping frames "
                                    "simulates it; use ALL or MODULAR"})
    if cache["cache_frame_start"] > frame_start or cache["cache_frame_end"] < frame_end:
        problems.append({"problem": f"cache frames {cache['cache_frame_start']}-{cache['cache_frame_end']} do not "
                                    f"cover {frame_start}-{frame_end}"})
    return problems


@contextmanager
def _domains_off(scene):
    """Every Fluid DOMAIN modifier off (viewport and render) so stepping frames simulates nothing.

    The frame is restored while the domains are still off, then the flags.
    """
    saved = []
    for obj in scene.objects:
        for mod in obj.modifiers:
            if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
                saved.append((mod, mod.show_viewport, mod.show_render))
                mod.show_viewport = mod.show_render = False
    frame = scene.frame_current
    try:
        yield
    finally:
        scene.frame_set(frame)
        for mod, viewport, render in saved:
            mod.show_viewport, mod.show_render = viewport, render


# --- gas_setup -----------------------------------------------------------------------------------

def _is_number(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def gas_setup(domain, frame_start: int, frame_end: int, *,
              flows: Mapping[str, int | None] | None = None,
              obstacles: Iterable = (),
              proxies: Mapping[str, str] | None = None,
              cache_root: str | None,
              expected_time_scale: float,
              min_source_voxels: float,
              max_flow_step_voxels: float | None,
              min_flow_clearance_voxels: float | None,
              features_m: Mapping[str, float] | None = None,
              min_feature_voxels: float | None = None,
              name: str = "gas_setup") -> Check:
    """Domain, time, cache, Flows and Effectors of a gas domain before the bake (never bakes or writes a cache).

    frame_start, frame_end     story range the gas must cover (the beats' frame interval)
    flows                      Flow name -> first frame it may emit (the cause beat), None for no limit;
                               None (the argument): every FLUID FLOW object in the scene, unlimited
    obstacles                  visible objects (or names) the gas must hit: each an enabled COLLISION
                               Effector on every sampled frame (itself, or a listed proxy standing for it)
    proxies                    hidden collision proxy name -> declared obstacle it stands for
    cache_root                 folder the cache should be inside (normally <case>/.work/cache); outside is
                               reported (cache.inside_cache_root False), not failed; None: not checked
    expected_time_scale        the spec's time scale (1.0 unless it declares slow motion)
    min_source_voxels          voxels across the thinnest axis of every emitting mesh Flow, at least
    max_flow_step_voxels       a Flow's largest move per frame / (subframes + 1), at most; None: reported
    min_flow_clearance_voxels  voxels between an emitting Flow's box and every Domain face, at least;
                               None: reported
    features_m                 named story lengths (metres) to express in voxels of the smallest axis;
                               min_feature_voxels (required with it) is the least voxel count for each

    Frames [frame_start, frame_end] (and the cache's first frame, the only one a GEOMETRY Flow emits
    on) are set once each with every Fluid Domain modifier off; the frame and the flags are restored.
    Structural problems always fail; the first is first_failure, all are in measurements["problems"].
    Raises ValueError when domain has no GAS Fluid Domain modifier (the verifier named the wrong object).
    """
    mod = _domain_modifier(domain)
    if frame_end < frame_start:
        raise ValueError("frame_end must be >= frame_start")
    if not _is_number(expected_time_scale) or expected_time_scale <= 0:
        raise ValueError("expected_time_scale must be a positive number")
    if not _is_number(min_source_voxels) or min_source_voxels < 0:
        raise ValueError("min_source_voxels must be a number >= 0")
    for label, value in (("max_flow_step_voxels", max_flow_step_voxels),
                         ("min_flow_clearance_voxels", min_flow_clearance_voxels),
                         ("min_feature_voxels", min_feature_voxels)):
        if value is not None and not _is_number(value):
            raise ValueError(f"{label} must be a number or None")
    if features_m and min_feature_voxels is None:
        raise ValueError("min_feature_voxels is required with features_m")
    s = mod.domain_settings
    scene = _scene_of(domain)
    proxies = dict(proxies or {})
    declared = [o if isinstance(o, str) else o.name for o in obstacles]
    problems: list[dict] = []
    thresholds = {"expected_time_scale": expected_time_scale, "min_source_voxels": min_source_voxels,
                  "max_flow_step_voxels": max_flow_step_voxels,
                  "min_flow_clearance_voxels": min_flow_clearance_voxels, "min_feature_voxels": min_feature_voxels,
                  "cache_root": cache_root}

    # Domain: the evaluated resolution at the current frame (no frame change), the formula as fallback
    lo, size = _local_box(domain)
    scale = [abs(k) for k in domain.matrix_world.to_scale()]
    dims = [float(size[i] * scale[i]) for i in range(3)]
    formula = [max(1, round(s.resolution_max * d / max(dims))) for d in dims]
    evaluated = [int(r) for r in _domain_settings(domain, depsgraph()).domain_resolution]
    # an Adaptive Domain's evaluated resolution is its adaptive box; the base grid is the formula's
    res = evaluated if 0 not in evaluated and not s.use_adaptive_domain else formula
    voxel = [dims[i] / res[i] for i in range(3)]
    smallest = min(voxel)
    noise_scale = int(s.noise_scale) if s.use_noise else 1
    borders = {f: {"flag": BORDERS[f], "closed": bool(getattr(s, f"use_collision_border_{BORDERS[f]}"))}
               for f in FACES}
    domain_m = {"name": domain.name, "domain_type": s.domain_type, "world_dimensions_m": dims,
                "object_scale": list(domain.scale), "rotation_euler": list(domain.rotation_euler),
                "unapplied_scale": any(abs(k - 1) > 1e-6 for k in domain.scale), "resolution_max": s.resolution_max,
                "domain_resolution": evaluated, "domain_resolution_formula": formula,
                "domain_resolution_used": "evaluated" if res is evaluated else "formula", "voxel_m": voxel,
                "voxel_estimate_m": max(dims) / s.resolution_max, "use_noise": bool(s.use_noise),
                "noise_scale": int(s.noise_scale),
                "noise_voxel_m": [v / noise_scale for v in voxel] if s.use_noise else None,
                "use_adaptive_domain": bool(s.use_adaptive_domain), "adapt_margin": s.adapt_margin,
                "adapt_threshold": s.adapt_threshold, "additional_res": getattr(s, "additional_res", None),
                "borders": borders, "alpha": s.alpha, "beta": s.beta, "vorticity": s.vorticity,
                "use_dissolve_smoke": bool(s.use_dissolve_smoke), "dissolve_speed": s.dissolve_speed,
                "fluid_group": s.fluid_group.name if s.fluid_group else None,
                "effector_group": s.effector_group.name if s.effector_group else None,
                "effector_weights": {"gravity": s.effector_weights.gravity, "all": s.effector_weights.all}}
    if s.use_adaptive_domain:
        domain_m["note"] = "Adaptive Domain: boundary_clearance and the density wall check will be UNVERIFIED"

    fps, fps_base = scene.render.fps, scene.render.fps_base
    time_m = {"fps": fps, "fps_base": fps_base, "time_scale": s.time_scale, "expected_time_scale": expected_time_scale,
              "use_adaptive_timesteps": bool(s.use_adaptive_timesteps), "cfl_condition": s.cfl_condition,
              "timesteps_min": s.timesteps_min, "timesteps_max": s.timesteps_max,
              "story_seconds": (frame_end - frame_start + 1) * fps_base / fps,
              "simulated_seconds_per_frame": s.time_scale * fps_base / fps}
    if abs(s.time_scale - expected_time_scale) > 1e-6:
        problems.append({"object": domain.name, "problem": f"time_scale {round(s.time_scale, 6)} differs from the "
                                                           f"spec's {float(expected_time_scale)}"})

    cache = _cache_record(domain, mod, frame_start, frame_end)
    resolved = cache["cache_directory_resolved"]
    if cache_root is None:
        cache["inside_cache_root"] = None
    else:
        root, real = os.path.realpath(bpy.path.abspath(cache_root)), os.path.realpath(resolved)
        cache["inside_cache_root"] = real != root and os.path.commonpath([real, root]) == root
    problems += [{"object": domain.name, **p} for p in _cache_problems(cache, frame_start, frame_end)]
    if not cache["writable"] and not cache["has_cache_baked_data"]:
        problems.append({"object": domain.name, "problem": "cache folder not writable and nothing baked",
                         "cache_directory": resolved})

    # frames to sample: the story range, plus the cache's first frame (the only one a GEOMETRY Flow emits on)
    sample = list(range(frame_start, frame_end + 1))
    if s.cache_frame_start < frame_start:
        sample.insert(0, s.cache_frame_start)
    not_rendered, _ = shot.render_visibility()

    per_flow: dict[str, dict] = {}
    per_effector: dict[str, dict] = {}
    with _domains_off(scene):
        for frame in sample:
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            listing = systems.effectors(domain, system="mantaflow_gas", scene=scene)
            graph = depsgraph()
            for e in listing["flows"]:
                obj = bpy.data.objects[e["object"]]
                rec = per_flow.setdefault(e["object"], {"frames": [], "grid_points": []})
                points = [v[:] for v in world_mesh(obj, graph).vertices]
                rec["frames"].append({"frame": frame, "enabled": e["enabled"], "active": e["active"],
                                      "in_group": e["in_group"]})
                rec["grid_points"].append(_to_grid(points, domain, lo, size, res) if points else np.zeros((0, 3)))
            for e in listing["effectors"]:
                obj = bpy.data.objects[e["object"]]
                rec = per_effector.setdefault(e["object"], {"frames": [], "inside": []})
                corners = [(obj.matrix_world @ Vector(c))[:] for c in obj.evaluated_get(graph).bound_box]
                rec["frames"].append({"frame": frame, "enabled": e["enabled"], "active": e["active"],
                                      "in_group": e["in_group"], "settings": e["settings"]})
                rec["inside"].append(_box_in_grid(_to_grid(corners, domain, lo, size, res), res)[0])

    # Flows
    found_flows = list(per_flow)
    wanted = dict(flows) if flows is not None else {n: None for n in found_flows}
    flow_rows, emitting, flow_failed = [], [], False
    for flow_name, allowed_from in wanted.items():
        obj = bpy.data.objects.get(flow_name)
        if obj is None or flow_name not in per_flow:
            problems.append({"object": flow_name, "problem": "Flow object missing" if obj is None
                             else f"{flow_name} has no Fluid FLOW modifier"})
            flow_failed = True
            continue
        fmod = next(m for m in obj.modifiers if m.type == "FLUID" and m.fluid_type == "FLOW")
        row, flow_problems = _flow_record(obj, fmod, per_flow[flow_name], allowed_from, s, res, voxel, smallest)
        flow_rows.append(row)
        problems += flow_problems
        flow_failed = flow_failed or bool(flow_problems)
        if row["emits"]:
            emitting.append(row)
    if not emitting and not flow_failed:
        problems.append({"object": domain.name, "problem": "no emitting Flow"})
    for row in emitting:
        if row["thinnest_voxels"] is not None and row["thinnest_voxels"] < min_source_voxels - _VOXEL_EPS:
            problems.append({"object": row["name"], "problem": "Flow thinner than min_source_voxels",
                             "thinnest_voxels": row["thinnest_voxels"], "min_source_voxels": min_source_voxels})
        if max_flow_step_voxels is not None and row["step_per_substep_voxels"] > max_flow_step_voxels + _VOXEL_EPS:
            problems.append({"object": row["name"], "problem": "Flow moves more than max_flow_step_voxels per "
                             "substep", "frame": row["max_step_frame"],
                             "step_per_substep_voxels": row["step_per_substep_voxels"]})
        if min_flow_clearance_voxels is not None and row["clearance_voxels"]:
            face = min(FACES, key=lambda f: row["clearance_voxels"][f])
            if row["clearance_voxels"][face] < min_flow_clearance_voxels - _VOXEL_EPS:
                problems.append({"object": row["name"], "problem": "Flow closer to a Domain face than "
                                 "min_flow_clearance_voxels", "face": face,
                                 "clearance_voxels": row["clearance_voxels"][face]})

    # Effectors, declared obstacles, proxies
    effector_rows = {}
    for eff_name, rec in per_effector.items():
        first, st = rec["frames"][0], rec["frames"][0]["settings"]
        effector_rows[eff_name] = {
            "name": eff_name, "modifier_enabled": first["enabled"], "effector_type": st["effector_type"],
            "use_effector_frames_off": _intervals(f["frame"] for f in rec["frames"] if not f["active"]),
            "surface_distance_voxels": st["surface_distance"], "surface_thickness_m": st["surface_distance"] * smallest,
            "thickened": st["surface_distance"] > 0, "use_plane_init": st["use_plane_init"],
            "subframes": st["subframes"], "in_effector_group": first["in_group"], "inside_fraction": max(rec["inside"]),
            "declared": eff_name in declared, "proxy_for": proxies.get(eff_name),
            "rendered": eff_name not in not_rendered, "not_rendered_reason": not_rendered.get(eff_name),
            "acts": any(f["enabled"] and f["active"] and f["in_group"] for f in rec["frames"])}
    obstacle_rows = {}
    for ob_name in declared:
        via = [p for p, target in proxies.items() if target == ob_name and p in effector_rows]
        checked = effector_rows.get(ob_name) or (effector_rows[via[0]] if via else None)
        reasons = []
        if bpy.data.objects.get(ob_name) is None:
            reasons.append("declared obstacle missing")
        elif checked is None:
            reasons.append("declared obstacle has no Fluid Effector modifier")
        else:
            if not checked["modifier_enabled"]:
                reasons.append("Effector modifier is off in the viewport")
            if checked["use_effector_frames_off"]:
                reasons.append(f"use_effector off on frames {checked['use_effector_frames_off']}")
            if checked["effector_type"] != "COLLISION":
                reasons.append(f"Effector is {checked['effector_type']}, not COLLISION")
            if not checked["in_effector_group"]:
                reasons.append("Effector outside the Domain's Effector collection")
            if checked["inside_fraction"] == 0:
                reasons.append("declared obstacle is outside the Domain")
        if ob_name in not_rendered:
            reasons.append("declared obstacle is not rendered (a hidden obstacle is a proxy: list it in proxies "
                           "and declare the visible object)")
        obstacle_rows[ob_name] = {"effector": checked is not None and not reasons,
                                  "effector_via": checked["name"] if checked else None, "problems": reasons}
        problems += [{"object": ob_name, "problem": reason} for reason in reasons]
    for proxy, target in proxies.items():
        if target not in declared:
            problems.append({"object": proxy, "problem": f"proxy stands for {target}, which is not a declared "
                                                         "obstacle"})
        elif proxy not in effector_rows:
            problems.append({"object": proxy, "problem": "listed proxy has no Fluid Effector modifier"})
    undeclared = []
    for eff_name, row in effector_rows.items():
        if row["declared"] or row["proxy_for"] is not None or not row["acts"]:
            continue
        if row["rendered"]:
            undeclared.append(eff_name)
        else:
            problems.append({"object": eff_name, "problem": "hidden Effector is neither a declared obstacle nor a "
                             "listed proxy (an air wall)", "not_rendered_reason": row["not_rendered_reason"]})

    features = {}
    for feature, length in (features_m or {}).items():
        features[feature] = length / smallest
        if features[feature] < min_feature_voxels - _VOXEL_EPS:
            problems.append({"feature": feature, "problem": "feature narrower than min_feature_voxels",
                             "voxels": features[feature], "length_m": length})

    measurements = {"domain": domain_m, "time": time_m, "cache": cache, "sampled_frames": [sample[0], sample[-1]],
                    "flows": flow_rows, "undeclared_flows": [n for n in found_flows if n not in wanted],
                    "effectors": list(effector_rows.values()), "obstacles": obstacle_rows,
                    "undeclared_effectors": undeclared, "features_voxels": features, "thresholds": thresholds,
                    "problems": problems}
    return Check(name, not problems, measurements, problems[0] if problems else None)


def _flow_record(obj, fmod, rec, allowed_from, s, res, voxel, smallest) -> tuple[dict, list[dict]]:
    """One Flow's measurements over the sampled frames, and its structural problems."""
    fs = fmod.flow_settings
    frames, points = rec["frames"], rec["grid_points"]
    behavior, source = fs.flow_behavior, fs.flow_source
    on = [f["frame"] for f in frames if f["active"]]
    if behavior == "GEOMETRY":  # emits only on the cache's first frame
        active = [s.cache_frame_start] if s.cache_frame_start in on else []
    elif behavior == "INFLOW":
        active = on
    else:  # OUTFLOW removes gas
        active = []
    # geometry against the grid on the frames it emits, or on every sampled frame when it never does
    use = [i for i, f in enumerate(frames) if f["frame"] in active and len(points[i])] or \
        [i for i in range(len(frames)) if len(points[i])]
    boxes = [_box_in_grid(points[i], res) for i in use]
    thinnest = [float((points[i].max(axis=0) - points[i].min(axis=0)).min()) for i in use]
    inside_any = max((_box_in_grid(g, res)[0] for g in points if len(g)), default=0.0)
    extents = None
    if use:
        g0 = points[use[0]]
        extents = [float((g0.max(axis=0) - g0.min(axis=0))[i] * voxel[i]) for i in range(3)]
    # largest move of a vertex (or of the centre when the vertex count changes) between consecutive frames
    max_step, max_step_frame = 0.0, None
    for i in range(1, len(frames)):
        a, b = points[i - 1], points[i]
        if frames[i]["frame"] != frames[i - 1]["frame"] + 1 or not len(a) or not len(b):
            continue
        step = float(np.linalg.norm(b - a, axis=1).max()) if a.shape == b.shape else \
            float(np.linalg.norm(b.mean(axis=0) - a.mean(axis=0)))
        if step > max_step:
            max_step, max_step_frame = step, frames[i]["frame"]
    mesh_source = source == "MESH"
    row = {"name": obj.name, "modifier_enabled": frames[0]["enabled"], "flow_type": fs.flow_type,
           "flow_behavior": behavior, "flow_source": source,
           "particle_system": fs.particle_system.name if fs.particle_system else None,
           "surface_distance_voxels": fs.surface_distance, "surface_emission_m": fs.surface_distance * smallest,
           "volume_density": fs.volume_density, "use_plane_init": bool(fs.use_plane_init), "subframes": fs.subframes,
           "density": fs.density, "temperature": fs.temperature, "fuel_amount": fs.fuel_amount,
           "use_initial_velocity": bool(fs.use_initial_velocity), "velocity_factor": fs.velocity_factor,
           "velocity_normal": fs.velocity_normal, "velocity_coord": list(fs.velocity_coord), "extents_m": extents,
           "thinnest_voxels": min(thinnest) if thinnest and mesh_source else None,
           "in_fluid_group": frames[0]["in_group"], "inside_fraction": inside_any,
           "clearance_voxels": {f: min(c[f] for _, c in boxes) for f in FACES} if boxes else {},
           "active_intervals": _intervals(active), "first_active": active[0] if active else None,
           "allowed_from": allowed_from, "max_step_m": max_step * smallest, "max_step_voxels": max_step,
           "max_step_frame": max_step_frame, "step_per_substep_voxels": max_step / (fs.subframes + 1),
           "hide_render": bool(obj.hide_render), "emits": False}
    problems = []

    def bad(problem, **extra):
        problems.append({"object": obj.name, "problem": problem, **extra})

    if not row["modifier_enabled"]:
        bad("Flow modifier is off in the viewport (the bake ignores it)")
    if fs.flow_type == "LIQUID":
        bad("LIQUID Flow in a gas Domain")
    if not row["in_fluid_group"]:
        bad("Flow outside the Domain's Flow collection")
    if inside_any == 0:
        bad("Flow outside the Domain", inside_fraction=0.0)
    if behavior != "OUTFLOW":
        if mesh_source and fs.surface_distance == 0 and fs.volume_density == 0:
            bad("Flow emits nothing: surface_distance and volume_density are 0")
        if source == "PARTICLES" and fs.particle_system is None:
            bad("particle-source Flow without a particle system")
        if (fs.flow_type == "SMOKE" and fs.density == 0) or (fs.flow_type == "FIRE" and fs.fuel_amount == 0) or \
                (fs.flow_type == "BOTH" and fs.density == 0 and fs.fuel_amount == 0):
            bad(f"{fs.flow_type} Flow emits nothing: density {fs.density:g}, fuel_amount {fs.fuel_amount:g}")
        if not active:
            bad("Flow never emits: use_inflow off on every sampled frame" if behavior == "INFLOW" else
                f"Flow never emits: a GEOMETRY Flow emits only on the cache's first frame "
                f"({s.cache_frame_start}) and use_inflow is off there")
        elif allowed_from is not None and active[0] < allowed_from:
            bad("Flow active before its allowed frame", first_active=active[0], allowed_from=allowed_from)
        row["emits"] = bool(active) and not problems
    return row, problems


# --- cache_coverage -------------------------------------------------------------------------------

def cache_coverage(domain, frame_start: int, frame_end: int, *, name: str = "gas_cache_coverage") -> Check:
    """The gas cache read is the one baked: type, range, baked flags, one file per frame, grid length.

    Fails on a REPLAY cache, a cache range not covering [frame_start, frame_end], Data (and Noise
    when use_noise) not baked, a data or noise file missing for a frame in range, or an empty grid or
    one of the wrong length on the first, middle or last frame (read only when Data is baked and the
    cache is not REPLAY; the frame is restored). Zero density is not a failure (gas.density min_total).
    """
    if frame_end < frame_start:
        raise ValueError("frame_end must be >= frame_start")
    mod = _domain_modifier(domain)
    s = mod.domain_settings
    cache = _cache_record(domain, mod, frame_start, frame_end)
    problems = _cache_problems(cache, frame_start, frame_end)
    if not cache["has_cache_baked_data"]:
        problems.append({"problem": "Data not baked"})
    if s.use_noise and not cache["has_cache_baked_noise"]:
        problems.append({"problem": "Noise not baked"})
    if cache["missing_data_frames"]:
        problems.append({"problem": "data files missing", "frames": cache["missing_data_frames"]})
    if cache["missing_noise_frames"]:
        problems.append({"problem": "noise files missing", "frames": cache["missing_noise_frames"]})
    grids = []
    if cache["cache_type"] != "REPLAY" and cache["has_cache_baked_data"]:
        scene = _scene_of(domain)
        current = scene.frame_current
        factor = int(s.noise_scale) if s.use_noise and cache["has_cache_baked_noise"] else 1
        try:
            for frame in dict.fromkeys((frame_start, (frame_start + frame_end) // 2, frame_end)):
                set_frame(frame)
                ev = _domain_settings(domain, depsgraph())
                grid = np.array(ev.density_grid, dtype=np.float64)
                res = [int(r) for r in ev.domain_resolution]
                expected = res[0] * res[1] * res[2] * factor ** 3
                cell = [c * abs(k) / factor for c, k in zip(ev.cell_size, domain.matrix_world.to_scale())]
                grids.append({"frame": frame, "length": int(grid.size), "expected_length": expected,
                              "resolution": res, "peak": float(np.nanmax(grid)) if grid.size else None,
                              "total": float(np.nansum(grid)) * cell[0] * cell[1] * cell[2] if grid.size else None})
                if not grid.size:
                    problems.append({"frame": frame, "problem": "empty density grid"})
                elif grid.size != expected or 0 in res:
                    problems.append({"frame": frame, "problem": "density grid of the wrong length",
                                     "length": int(grid.size), "expected_length": expected})
        finally:
            set_frame(current)
    measurements = {"requested": {"frame_start": frame_start, "frame_end": frame_end}, "cache": cache,
                    "use_noise": bool(s.use_noise), "noise_scale": int(s.noise_scale), "grids": grids,
                    "problems": problems}
    return Check(name, not problems, measurements, problems[0] if problems else None)


# --- boundary_clearance ---------------------------------------------------------------------------

def boundary_clearance(domain, frames: Sequence[int], *, significant_density: float, guard_layers: int,
                       min_clearance_voxels: float | None, max_guard_fraction: float | None,
                       name: str = "gas_boundary_clearance") -> Check:
    """Clearance of the gas to the six Domain faces, and the gas in each face's guard layers, per frame.

    significant_density   cells with density above it are the gas (their box, centroid, volume)
    guard_layers          guard depth in cells from the second layer inward, as in grid_stats
    min_clearance_voxels  interior layers between the gas box and a face, at least (layer 1
                          occupied -> 0); None: reported
    max_guard_fraction    a face's share of the total in its guard layers, at most; None: reported
    Per frame and face: clearance_voxels, clearance_m, border flag and closed, guard_sum (density x
    cell volume), guard_max, guard_significant_cells, guard_fraction, and front_speed_m_s: how fast
    that face of the gas box moves outward (m/s along the domain axis) since the previous checked
    non-empty frame. A frame with no significant cell is `empty` and not failed (gas existence is
    density min_total). With noise the cells are noise cells. On an Adaptive Domain the result fails
    with verdict UNVERIFIED (totals and grid size per frame are still listed).
    Steps frames: on a REPLAY cache that simulates, so run cache_coverage in the same row first.
    Raises GasCacheMissing when a frame has no grid.
    """
    if not frames or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be non-empty and strictly increasing: {list(frames)}")
    if guard_layers < 1:
        raise ValueError("guard_layers must be >= 1")
    if significant_density < 0 or (min_clearance_voxels is not None and min_clearance_voxels < 0) or \
            (max_guard_fraction is not None and not 0 <= max_guard_fraction <= 1):
        raise ValueError("thresholds out of range")
    s = _domain_modifier(domain).domain_settings
    scene = _scene_of(domain)
    fps = scene.render.fps / scene.render.fps_base
    thresholds = {"significant_density": significant_density, "guard_layers": guard_layers,
                  "min_clearance_voxels": min_clearance_voxels, "max_guard_fraction": max_guard_fraction}
    borders = {f: {"border": BORDERS[f], "closed": bool(getattr(s, f"use_collision_border_{BORDERS[f]}"))}
               for f in FACES}
    lo, size = _local_box(domain)
    rows, first_failure = [], None
    previous = None  # (frame, outward position of each face of the gas box, metres along the domain axis)

    for frame in frames:
        set_frame(frame)
        g = _read_grid(domain, depsgraph())
        v, cell = g["v"], g["cell_m"]
        volume = cell[0] * cell[1] * cell[2]
        nz, ny, nx = v.shape
        total = float(v.sum())
        row = {"frame": frame, "total": total * volume, "peak": float(v.max()), "grid": [nx, ny, nz]}
        rows.append(row)
        if g["adaptive"]:
            continue
        sig = v > significant_density
        count = int(sig.sum())
        row.update(empty=count == 0, significant_cells=count, significant_share=count / v.size,
                   occupied_volume_m3=count * volume)
        if not count:
            continue
        z, y, x = np.nonzero(sig)
        idx_lo, idx_hi, n = [int(x.min()), int(y.min()), int(z.min())], [int(x.max()), int(y.max()), int(z.max())], \
            [nx, ny, nz]
        matrix = np.array(domain.matrix_world, dtype=np.float64)

        def world(index):  # voxel centre -> world
            local = lo + (np.asarray(index, dtype=np.float64) + 0.5) * size / n
            return matrix[:3, :3] @ local + matrix[:3, 3]

        corners = np.array([world((cx, cy, cz)) for cx in (idx_lo[0], idx_hi[0]) for cy in (idx_lo[1], idx_hi[1])
                            for cz in (idx_lo[2], idx_hi[2])])
        weight = np.where(sig, v, 0.0)
        wsum = float(weight.sum())
        zi, yi, xi = np.indices(v.shape)
        centroid_index = [float((xi * weight).sum() / wsum), float((yi * weight).sum() / wsum),
                          float((zi * weight).sum() / wsum)]
        slices, sig_slices = _face_slices(v, guard_layers + 1), _face_slices(sig, guard_layers + 1)
        faces, positions = {}, {}
        for axis in range(3):
            for side, layer in (("min", idx_lo[axis]), ("max", idx_hi[axis])):
                face = f"{'xyz'[axis]}_{side}"
                clearance = layer - 1 if side == "min" else n[axis] - 2 - layer
                part = slices[face]
                positions[face] = (-(layer + 0.5) if side == "min" else layer + 0.5) * cell[axis]
                faces[face] = {**borders[face], "clearance_voxels": clearance, "clearance_m": clearance * cell[axis],
                               "guard_sum": float(part.sum()) * volume,
                               "guard_max": float(part.max()) if part.size else 0.0,
                               "guard_significant_cells": int(sig_slices[face].sum()),
                               "guard_fraction": float(part.sum()) / total if total > 0 else 0.0}
        speed = None
        if previous is not None:
            speed = {f: (positions[f] - previous[1][f]) * fps / (frame - previous[0]) for f in FACES}
        previous = (frame, positions)
        row.update(aabb_index=[idx_lo, idx_hi], aabb_world_m=[corners.min(axis=0).tolist(),
                                                               corners.max(axis=0).tolist()],
                   centroid_index=centroid_index, centroid_world_m=world(centroid_index).tolist(),
                   faces=faces, front_speed_m_s=speed)
        if min_clearance_voxels is not None and first_failure is None:
            face = min(FACES, key=lambda f: faces[f]["clearance_voxels"])
            if faces[face]["clearance_voxels"] < min_clearance_voxels:
                first_failure = {"frame": frame, "problem": "gas closer to a Domain face than min_clearance_voxels",
                                 "face": face, "clearance_voxels": faces[face]["clearance_voxels"]}
        if max_guard_fraction is not None and first_failure is None:
            face = max(FACES, key=lambda f: faces[f]["guard_fraction"])
            if faces[face]["guard_fraction"] > max_guard_fraction:
                first_failure = {"frame": frame, "problem": "gas in the guard layers of a Domain face",
                                 "face": face, "guard_fraction": faces[face]["guard_fraction"]}

    adaptive = bool(s.use_adaptive_domain) or g["adaptive"]
    measurements = {"frames": rows, "adaptive": adaptive, "noise_factor": g["factor"],
                    "cells": "noise" if g["factor"] > 1 else "base", "cell_size_m": g["cell_m"], "borders": borders,
                    "thresholds": thresholds}
    if adaptive:
        return Check(name, False, measurements, dict(UNVERIFIED))
    clear = [r["faces"] for r in rows if not r["empty"]]
    measurements["min_clearance_voxels"] = {f: min(c[f]["clearance_voxels"] for c in clear) for f in FACES} \
        if clear else None
    measurements["largest_guard_fraction"] = {f: max(c[f]["guard_fraction"] for c in clear) for f in FACES} \
        if clear else None
    return Check(name, first_failure is None, measurements, first_failure)
