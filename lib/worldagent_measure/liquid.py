"""Liquid surface quality, volume, and liquid-in-solid checks (requires bpy).

    from worldagent_measure import geometry, liquid, verifier

    try:
        check = liquid.surface_quality(domain, frames, max_edge_spike_ratio=4.0, max_components=1,
                                       max_volume_jump_rel=0.05, max_component_jump=0)
    except liquid.FlipUnavailable as exc:
        verifier.write_blocked(args.out, str(exc))
    else:
        verifier.write_result(args.out, [check])

Paper category check for liquids: volume tracking that accounts for the specified
inflow/outflow, no liquid inside solids, and surface checks for spurious chunks,
spikes, discontinuous components, and frame-to-frame popping [worldagent-paper-notes.md
'Category / Liquid']. The surface is the solver's evaluated output: a FLIP Fluids
surface cache object, a baked Mantaflow liquid mesh, or any mesh object. If FLIP is
needed and the add-on is missing, the row is blocked, never measured on a Mantaflow
substitute [skill.md 'FLIP Fluids is not bundled'].

Only the reconstructed surface is measured, not solver particles or grids; a surface
vertex inside a solid stands in for liquid inside the solid. Leakage through
under-resolved gaps is not measured here.
"""

from __future__ import annotations

import math
import os
import statistics
import sys
from dataclasses import dataclass
from typing import Callable, Mapping

import bpy

from worldagent_runtime import value as setting
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .geometry import WorldMesh, aabb_overlap, depsgraph, set_frame, world_mesh
from .verifier import Check

FLIP_ADDON = "flip_fluids_addon"
# Face area at or below this counts as zero-area.
ZERO_AREA_EPS_M2 = 1e-8
# BVH queries run in float32; step past a hit by this much of the query scale before casting
# again (a fixed 1e-7 m step is below float32 resolution a few metres from the origin, so the
# ray kept re-hitting the same face). Same rule as contact.py.
_RAY_STEP_REL = 1e-6
_MAX_RAY_HITS = 100000


class FlipUnavailable(RuntimeError):
    """FLIP Fluids is needed but not installed/enabled; the case writes verifier.write_blocked(...)."""


class LiquidCacheMissing(RuntimeError):
    """The solver has no baked liquid surface for a frame, so there is nothing to measure."""


@dataclass
class LiquidSurface(WorldMesh):
    edges: list  # list[tuple[int, int]], mesh edges (not triangulation diagonals)


# --- surface loading ---------------------------------------------------------------


def _stored_flip_type(obj) -> str | None:
    """FLIP object_type kept in the .blend even when the add-on is not registered."""
    try:
        group = obj.bl_system_properties_get()  # Blender 5.x: bpy.props data lives here
    except AttributeError:
        group = obj  # older Blender stored it as a plain ID property
    try:
        data = group.get("flip_fluid") if group is not None else None
    except TypeError:
        data = None
    if data is None:
        return None
    value = data.get("object_type") if hasattr(data, "get") else None
    if isinstance(value, str):
        return value
    # A stored enum is an index whose mapping needs the add-on. Assumed: 0 (or unset)
    # is "no FLIP role"; any other index is treated as a possible domain, so a missing
    # add-on blocks instead of measuring the object's own mesh as liquid.
    return "UNKNOWN" if value else None


def flip_available() -> bool:
    return hasattr(bpy.types.Object, "flip_fluid")


def enable_flip(module: str = FLIP_ADDON) -> None:
    """Enable the FLIP Fluids add-on in this process, or raise FlipUnavailable.

    The runner starts Blender with --factory-startup, so add-ons must be enabled by the
    verifier [writing-verifiers.md '1. Script contract']. $WORLDAGENT_FLIP_PATH (the folder
    that contains flip_fluids_addon, set by the plugin's SessionStart hook or by the user)
    goes on sys.path first, so an add-on installed as a Blender extension or kept outside
    Blender's folders is found under the same module name.
    """
    if flip_available():
        return
    import addon_utils

    folder = setting("WORLDAGENT_FLIP_PATH")
    if folder and folder not in sys.path:
        sys.path.insert(0, folder)

    try:
        # default_set=True is required: FLIP's register() reads its own entry in
        # preferences.addons, which default_set=False never creates, so registration
        # fails silently (seen with FLIP Fluids 1.8.8 on Blender 5.1.1). The preference
        # change stays in this process; background runs do not save preferences.
        addon_utils.enable(module, default_set=True)
    except Exception as exc:  # noqa: BLE001 - any add-on import failure means unavailable
        raise FlipUnavailable(f"FLIP Fluids add-on {module!r} failed to enable: {exc}") from exc
    if not flip_available():
        raise FlipUnavailable(f"FLIP Fluids add-on {module!r} is not installed; FLIP requirements are blocked")


def is_flip_domain(obj) -> bool:
    settings = getattr(obj, "flip_fluid", None)
    if settings is not None:
        return settings.object_type == "TYPE_DOMAIN"
    return _stored_flip_type(obj) in ("TYPE_DOMAIN", "UNKNOWN")


def _mantaflow_domain(obj):
    for mod in getattr(obj, "modifiers", []):
        if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            return mod.domain_settings
    return None


def _mantaflow_mesh_file(settings, frame: int) -> str:
    ext = {"BOBJECT": ".bobj.gz", "OBJECT": ".obj"}.get(settings.cache_mesh_format, "")
    return os.path.join(bpy.path.abspath(settings.cache_directory), "mesh", f"fluid_mesh_{frame:04d}{ext}")


def _liquid_surface(obj, graph) -> LiquidSurface:
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        matrix = evaluated.matrix_world
        vertices = [matrix @ v.co for v in mesh.vertices]
        triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
        edges = [tuple(e.vertices) for e in mesh.edges]
    finally:
        evaluated.to_mesh_clear()
    return LiquidSurface(obj.name, vertices, triangles, edges)


def surface_object(source, frame: int, solver: str = "auto") -> LiquidSurface:
    """Evaluated world-space liquid surface of `source` at `frame`.

    solver  "flip"  FLIP Fluids domain: mesh_cache.surface.load_frame + get_cache_object
            "mesh"  Mantaflow liquid domain (baked mesh) or any mesh object, evaluated
            "auto"  "flip" if `source` is (or is stored as) a FLIP domain, else "mesh"

    Raises FlipUnavailable when FLIP is needed but missing, LiquidCacheMissing when a
    Mantaflow liquid has no baked mesh for the frame (the evaluated domain would then be
    the domain box, not liquid).
    """
    if solver not in ("auto", "flip", "mesh"):
        raise ValueError(f"unknown solver {solver!r}")
    if solver == "auto":
        solver = "flip" if is_flip_domain(source) else "mesh"
    set_frame(frame)
    if solver == "flip":
        if not flip_available():
            raise FlipUnavailable(
                f"{source.name} needs FLIP Fluids, which is not enabled; FLIP requirements are blocked "
                f"(never substitute Mantaflow) [skill.md]")
        if not is_flip_domain(source):
            raise ValueError(f"{source.name} is not a FLIP Fluids domain")
        cache = source.flip_fluid.domain.mesh_cache.surface
        cache.load_frame(frame, force_load=True)
        bpy.context.view_layer.update()
        obj = cache.get_cache_object()
        if obj is None or obj.type != "MESH":
            raise LiquidCacheMissing(f"{source.name} has no FLIP surface cache object at frame {frame}")
        return _liquid_surface(obj, depsgraph())
    settings = _mantaflow_domain(source)
    if settings is not None:
        if settings.domain_type != "LIQUID":
            raise ValueError(f"{source.name} is a Mantaflow {settings.domain_type} domain, not liquid")
        if not settings.use_mesh:
            raise LiquidCacheMissing(f"{source.name}: Mantaflow liquid mesh is disabled (use_mesh=False)")
        path = _mantaflow_mesh_file(settings, frame)
        if not os.path.isfile(path):
            raise LiquidCacheMissing(f"{source.name}: no baked liquid mesh for frame {frame} ({path})")
    if source.type != "MESH":
        raise ValueError(f"{source.name} is a {source.type}, not a mesh")
    return _liquid_surface(source, depsgraph())


# --- mesh measurements -------------------------------------------------------------


def _triangle_area(a: Vector, b: Vector, c: Vector) -> float:
    return (b - a).cross(c - a).length / 2.0


def _edge_use(triangles: list) -> dict:
    use: dict = {}
    for t in triangles:
        for a, b in ((t[0], t[1]), (t[1], t[2]), (t[2], t[0])):
            key = (a, b) if a < b else (b, a)
            use[key] = use.get(key, 0) + 1
    return use


def open_edge_count(triangles: list) -> int:
    """Edges not shared by exactly two triangles (nonmanifold or boundary); 0 means closed."""
    return sum(1 for n in _edge_use(triangles).values() if n != 2)


def signed_volume(mesh: WorldMesh) -> float:
    """Sum of signed tetrahedra against the origin; exact for a closed, consistently oriented mesh."""
    total = 0.0
    v = mesh.vertices
    for i, j, k in mesh.triangles:
        total += v[i].dot(v[j].cross(v[k]))
    return total / 6.0


def components(mesh: WorldMesh) -> list[int]:
    """Triangle counts of the connected components (shared vertex index), largest first."""
    parent = list(range(len(mesh.vertices)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, c in mesh.triangles:
        ra = find(a)
        for other in (b, c):
            ro = find(other)
            if ro != ra:
                parent[ro] = ra
    counts: dict = {}
    for t in mesh.triangles:
        root = find(t[0])
        counts[root] = counts.get(root, 0) + 1
    return sorted(counts.values(), reverse=True)


def edge_spike_ratio(surface: LiquidSurface) -> float | None:
    """Longest / median mesh edge length; None without edges."""
    lengths = [(surface.vertices[a] - surface.vertices[b]).length for a, b in surface.edges]
    if not lengths:
        return None
    median = statistics.median(lengths)
    return max(lengths) / median if median > 0 else math.inf


def _frame_stats(surface: LiquidSurface, zero_area_eps_m2: float) -> dict:
    v = surface.vertices
    zero = sum(1 for i, j, k in surface.triangles if _triangle_area(v[i], v[j], v[k]) <= zero_area_eps_m2)
    comps = components(surface)
    open_edges = open_edge_count(surface.triangles)
    return {
        "vertices": len(v),
        "triangles": len(surface.triangles),
        "zero_area_faces": zero,
        "edge_spike_ratio": edge_spike_ratio(surface),
        "components": len(comps),
        "component_triangles": comps[:10],
        "open_edges": open_edges,
        "closed": open_edges == 0 and bool(surface.triangles),
        "signed_volume_m3": signed_volume(surface),
    }


def _fail(source, frame: int, metric: str, value, limit) -> dict:
    return {"object": source.name, "frame": frame, "metric": metric, "value": value, "limit": limit}


# --- checks --------------------------------------------------------------------------


def surface_quality(source, frames: list[int], max_edge_spike_ratio: float, max_components: int,
                    max_volume_jump_rel: float, max_component_jump: int, max_zero_area_faces: int = 0,
                    zero_area_eps_m2: float = ZERO_AREA_EPS_M2, name: str = "liquid_surface",
                    solver: str = "auto") -> Check:
    """Per-frame surface quality plus frame-to-frame popping.

    Per frame: non-empty surface, zero-area faces <= max_zero_area_faces,
    longest/median edge <= max_edge_spike_ratio, connected components <= max_components (detached chunks and
    blobs) [flip-fluids.en.md 5.1]. Between consecutive listed frames: |dV|/V_prev <=
    max_volume_jump_rel (only when both frames are closed) and |d components| <=
    max_component_jump [paper 'frame-to-frame popping']. Popping is measured across the
    frames given, so pass every frame of the range to measure per-frame pops.
    """
    rows = []
    limitations = []
    first = None
    prev = None
    for frame in frames:
        stats = _frame_stats(surface_object(source, frame, solver), zero_area_eps_m2)
        row = {"frame": frame, **stats}
        fails = []
        if stats["triangles"] == 0:
            fails.append(_fail(source, frame, "empty_surface", 0, ">0 triangles"))
        else:
            if stats["zero_area_faces"] > max_zero_area_faces:
                fails.append(_fail(source, frame, "zero_area_faces", stats["zero_area_faces"], max_zero_area_faces))
            ratio = stats["edge_spike_ratio"]
            if ratio is None or ratio > max_edge_spike_ratio:
                fails.append(_fail(source, frame, "edge_spike_ratio", ratio, max_edge_spike_ratio))
            if stats["components"] > max_components:
                fails.append(_fail(source, frame, "components", stats["components"], max_components))
        if prev is not None:
            jump = abs(stats["components"] - prev["components"])
            row["component_jump"] = jump
            if jump > max_component_jump:
                fails.append(_fail(source, frame, "component_jump", jump, max_component_jump)
                             | {"previous_frame": prev["frame"]})
            if prev["closed"] and stats["closed"] and prev["signed_volume_m3"] > 0:
                rel = abs(stats["signed_volume_m3"] - prev["signed_volume_m3"]) / prev["signed_volume_m3"]
                row["volume_jump_rel"] = rel
                if rel > max_volume_jump_rel:
                    fails.append(_fail(source, frame, "volume_jump_rel", rel, max_volume_jump_rel)
                                 | {"previous_frame": prev["frame"]})
            else:
                row["volume_jump_rel"] = None
                limitations.append(f"frame {prev['frame']}->{frame}: volume jump not measured (open or empty surface)")
        if fails and first is None:
            first = fails[0]
        row["failed_metrics"] = [f["metric"] for f in fails]
        rows.append(row)
        prev = {"frame": frame, **stats}
    measurements = {
        "object": source.name,
        "frames_checked": len(rows),
        "max_edge_spike_ratio_limit": max_edge_spike_ratio,
        "max_components_limit": max_components,
        "max_zero_area_faces_limit": max_zero_area_faces,
        "zero_area_eps_m2": zero_area_eps_m2,
        "max_volume_jump_rel_limit": max_volume_jump_rel,
        "max_component_jump_limit": max_component_jump,
        "frames": rows,
        "limitations": limitations,
    }
    return Check(name, first is None, measurements, first)


def volume(source, frames: list[int], expected_volume_m3: Mapping[int, float] | Callable[[int], float] | float,
           rel_tol: float, name: str = "liquid_volume", solver: str = "auto") -> Check:
    """Closed-surface volume per frame against the expected volume.

    The caller computes the expected volume from the initial volume and the specified
    inflow/outflow [paper 'volume tracking'; flip-fluids.en.md 'volume budget'].
    Passes when |V - V_expected| / V_expected <= rel_tol on every frame. A surface that
    is not closed has no defined enclosed volume; that frame fails as `not_closed`
    and the open edge count is reported, rather than passing on a partial number.
    """
    if callable(expected_volume_m3):
        expected_of = expected_volume_m3
    elif isinstance(expected_volume_m3, Mapping):
        expected_of = expected_volume_m3.__getitem__
    else:
        expected_of = lambda _frame: float(expected_volume_m3)  # noqa: E731
    rows = []
    first = None
    for frame in frames:
        surface = surface_object(source, frame, solver)
        expected = float(expected_of(frame))
        if not expected > 0:
            raise ValueError(f"expected volume at frame {frame} must be > 0, got {expected}")
        open_edges = open_edge_count(surface.triangles)
        closed = open_edges == 0 and bool(surface.triangles)
        measured = signed_volume(surface)
        rel = abs(measured - expected) / expected
        rows.append({"frame": frame, "signed_volume_m3": measured, "expected_volume_m3": expected,
                     "rel_error": rel, "closed": closed, "open_edges": open_edges})
        if first is None:
            if not closed:
                first = _fail(source, frame, "not_closed", open_edges, "0 open edges")
            elif rel > rel_tol:
                first = _fail(source, frame, "volume_rel_error", rel, rel_tol) | {
                    "volume_m3": measured, "expected_volume_m3": expected}
    measurements = {
        "object": source.name,
        "frames_checked": len(rows),
        "rel_tol": rel_tol,
        "max_rel_error": max((r["rel_error"] for r in rows), default=None),
        "frames": rows,
        "method": "signed tetrahedron sum over the evaluated surface triangles",
    }
    return Check(name, first is None, measurements, first)


def _inside_closed(bvh: BVHTree, point: Vector, max_dist: float) -> bool:
    """Ray parity, majority of three skewed directions (one ray can graze an edge)."""
    step = _RAY_STEP_REL * max(max_dist + max(abs(c) for c in point), 1.0)
    votes = 0
    for direction in (Vector((1.0, 0.0123, 0.0071)), Vector((-0.0093, 1.0, 0.0157)), Vector((0.0111, -0.0067, -1.0))):
        direction.normalize()
        origin = point.copy()
        hits, last = 0, None
        for _ in range(_MAX_RAY_HITS):
            loc, _n, index, _d = bvh.ray_cast(origin, direction, max_dist)
            if loc is None:
                break
            if index != last:
                hits += 1
            last = index
            origin = loc + direction * step
        votes += hits % 2
    return votes >= 2


def surface_inside_solids(liquid, solids: list, frames: list[int], eps_pen: float,
                          name: str = "liquid_in_solids", solver: str = "auto") -> Check:
    """Liquid surface vertices inside solid meshes, per frame.

    Signed gap = (p - nearest) . face normal on the solid's evaluated world mesh. A vertex fails when the
    gap < -eps_pen; for a closed solid it must also be inside by ray parity, which rejects
    wrong signs near edges and concave corners. eps_pen only absorbs geometry-query noise [paper Eq. 2].
    Solids are re-read every frame, so moving obstacles are measured where they are.
    """
    if eps_pen < 0:
        raise ValueError("eps_pen must be >= 0")
    rows = []
    first = None
    solid_closed = {}
    for frame in frames:
        surface = surface_object(liquid, frame, solver)
        graph = depsgraph()
        row = {"frame": frame, "liquid_vertices": len(surface.vertices), "bad_vertices": 0, "min_gap_m": None,
               "pairs_tested": 0}
        if surface.vertices:
            liquid_box = surface.bbox
            for solid in solids:
                mesh = world_mesh(solid, graph)
                if not mesh.triangles:
                    continue
                box = mesh.bbox
                closed = open_edge_count(mesh.triangles) == 0
                solid_closed[solid.name] = closed
                # A closed solid's interior lies in its AABB; an open one (terrain) has an
                # "inside" below it, so every vertex is tested.
                if closed and not aabb_overlap(liquid_box, box, eps_pen):
                    continue
                row["pairs_tested"] += 1
                bvh = mesh.bvh()
                diag = (box[1] - box[0]).length + (liquid_box[1] - liquid_box[0]).length + 1.0
                lo, hi = box[0] - Vector((eps_pen,) * 3), box[1] + Vector((eps_pen,) * 3)
                for index, p in enumerate(surface.vertices):
                    if closed and not all(lo[i] <= p[i] <= hi[i] for i in range(3)):
                        continue
                    loc, normal, _face, dist = bvh.find_nearest(p)
                    if loc is None:
                        continue
                    gap = (p - loc).dot(normal)
                    if row["min_gap_m"] is None or gap < row["min_gap_m"]:
                        row["min_gap_m"] = gap
                    if gap < -eps_pen and (not closed or _inside_closed(bvh, p, diag)):
                        row["bad_vertices"] += 1
                        if first is None:
                            first = {"object": liquid.name, "other_object": solid.name, "frame": frame,
                                     "vertex": index, "position": p[:], "value": gap, "depth_m": dist,
                                     "limit": -eps_pen}
        rows.append(row)
    measurements = {
        "object": liquid.name,
        "solids": [s.name for s in solids],
        "solid_closed": solid_closed,
        "eps_pen_m": eps_pen,
        "frames_checked": len(rows),
        "bad_vertices_total": sum(r["bad_vertices"] for r in rows),
        "min_gap_m": min((r["min_gap_m"] for r in rows if r["min_gap_m"] is not None), default=None),
        "frames": rows,
        "limitations": ["surface vertices only, not solver particles; an open solid uses the nearest-face "
                        "normal sign alone, which can misjudge vertices beyond its border"],
    }
    return Check(name, first is None, measurements, first)
