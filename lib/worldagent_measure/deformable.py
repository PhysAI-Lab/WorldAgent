"""Cloth and elastic bodies: deformation, motion per step, self-intersection, recovery (requires bpy).

    from worldagent_measure import deformable

    deformable.deformation(flag, frame_list(1, 120), rest_frame=1, max_stretch_rel=0.1,
                           max_compression_rel=0.3, max_step_m=0.02, max_self_intersections=0)
    deformable.recovery(ball, rest_frame=1, final_frame=120, max_edge_deviation_rel=0.02)
    deformable.cloth_setup(flag, colliders=[pole, floor])

Category check for elastic bodies and cloth: deformation and displacement measured against
the local mesh and collision scale, including self-intersection and failure to recover
[paper 3.2.2 'Category: Elastic/Cloth'; material-playbooks.en.md 'Cloth' and 'Soft Body';
the cloth time step and collision distance are matched to thickness, local edge length, and
motion per step]. Measured on the evaluated mesh (cloth or soft-body cache applied):

- edge strain against the rest frame: |e| / |e_rest| - 1, largest stretch and compression;
- largest vertex motion between consecutive checked frames, also reported relative to the
  shortest rest edge and to the cloth collision distance when the object has one, since a
  step much larger than either lets the solver tunnel;
- triangle pairs that intersect without sharing a vertex (self-intersection);
- recovery: after the load is gone, edge lengths return to their rest lengths (rigid motion
  does not count, because edge lengths ignore it).

The topology must stay fixed; a changing vertex or edge count fails. All thresholds are
required arguments from the row's pass_predicate.

cloth_setup measures the cloth_resolution_contract instead of a motion: spatial resolution
is the evaluated mesh entering the Cloth modifier, so solver topology (modifiers before
Cloth) is measured apart from render topology (the whole stack at render settings)
[material-playbooks.en.md 'Cloth' > 'Mesh, spatial resolution, and modifier order'; typical
stack Armature/Hook -> SolverSubdivision -> Cloth -> Solidify -> RenderSubdivision]. It
fails only on structure, never on a resolution threshold: no Cloth modifier; Solidify
before Cloth; a Subdivision before Cloth that is adaptive or whose levels or visibility
differ between viewport and render (the topology entering Cloth would differ); a collider
without a Collision modifier, since rigid-body status alone does not collide with cloth
['Collision and anti-penetration'].
"""

from __future__ import annotations

import statistics
from typing import Sequence

import bpy

from .geometry import WorldMesh, depsgraph, set_frame
from .verifier import Check


def _evaluated(obj, graph) -> tuple[WorldMesh, list[tuple[int, int]]]:
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
    return WorldMesh(obj.name, vertices, triangles), edges


def _lengths(mesh: WorldMesh, edges) -> list[float]:
    return [(mesh.vertices[a] - mesh.vertices[b]).length for a, b in edges]


def self_intersections(mesh: WorldMesh) -> int:
    """Triangle pairs that intersect without sharing a vertex."""
    if not mesh.triangles:
        return 0
    tree = mesh.bvh()
    tris = mesh.triangles
    return sum(1 for i, j in tree.overlap(tree) if i < j and not set(tris[i]) & set(tris[j]))


def collision_distance(obj) -> float | None:
    """Cloth collision distance (distance_min) when the object has a cloth modifier."""
    for mod in obj.modifiers:
        if mod.type == "CLOTH":
            return mod.collision_settings.distance_min
    return None


def deformation(obj, frames: Sequence[int], *, rest_frame: int, max_stretch_rel: float, max_compression_rel: float,
                max_step_m: float, max_self_intersections: int, name: str = "deformation") -> Check:
    """Strain, motion per step, and self-intersection on every checked frame, against the rest frame."""
    if not frames or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be non-empty and strictly increasing: {list(frames)}")
    if min(max_stretch_rel, max_compression_rel, max_step_m) < 0 or max_self_intersections < 0:
        raise ValueError("thresholds must be >= 0")
    set_frame(rest_frame)
    rest_mesh, edges = _evaluated(obj, depsgraph())
    if not edges:
        raise ValueError(f"{obj.name} has no edges")
    rest = _lengths(rest_mesh, edges)
    if min(rest) <= 0:
        raise ValueError(f"{obj.name} has zero-length rest edges")
    shortest_rest = min(rest)
    collide = collision_distance(obj)
    rows, first_failure, previous = [], None, None

    def fail(record):
        nonlocal first_failure
        if first_failure is None:
            first_failure = record

    for frame in frames:
        set_frame(frame)
        mesh, now_edges = _evaluated(obj, depsgraph())
        if len(mesh.vertices) != len(rest_mesh.vertices) or now_edges != edges:
            fail({"object": obj.name, "frame": frame, "problem": "topology changed"})
            rows.append({"frame": frame, "topology_changed": True})
            previous = None
            continue
        ratios = [now / r for now, r in zip(_lengths(mesh, edges), rest)]
        stretch, compression = max(ratios) - 1.0, 1.0 - min(ratios)
        step = max((p - q).length for p, q in zip(mesh.vertices, previous)) if previous else 0.0
        crossings = self_intersections(mesh)
        rows.append({"frame": frame, "max_stretch_rel": stretch, "max_compression_rel": compression,
                     "max_step_m": step, "self_intersections": crossings})
        if stretch > max_stretch_rel:
            fail({"object": obj.name, "frame": frame, "problem": "overstretched", "stretch_rel": stretch})
        if compression > max_compression_rel:
            fail({"object": obj.name, "frame": frame, "problem": "overcompressed", "compression_rel": compression})
        if step > max_step_m:
            fail({"object": obj.name, "frame": frame, "problem": "moved too far in one step", "step_m": step})
        if crossings > max_self_intersections:
            fail({"object": obj.name, "frame": frame, "problem": "self-intersection", "triangle_pairs": crossings})
        previous = mesh.vertices
    measured = [r for r in rows if not r.get("topology_changed")]
    largest_step = max((r["max_step_m"] for r in measured), default=0.0)
    measurements = {
        "object": obj.name, "rest_frame": rest_frame, "frames": rows,
        "largest_stretch_rel": max((r["max_stretch_rel"] for r in measured), default=0.0),
        "largest_compression_rel": max((r["max_compression_rel"] for r in measured), default=0.0),
        "largest_step_m": largest_step,
        "shortest_rest_edge_m": shortest_rest,
        "largest_step_over_shortest_edge": largest_step / shortest_rest,
        "collision_distance_m": collide,
        "largest_step_over_collision_distance": largest_step / collide if collide else None,
        "largest_self_intersections": max((r["self_intersections"] for r in measured), default=0),
        "thresholds": {"max_stretch_rel": max_stretch_rel, "max_compression_rel": max_compression_rel,
                       "max_step_m": max_step_m, "max_self_intersections": max_self_intersections},
    }
    return Check(name, first_failure is None, measurements, first_failure)


def recovery(obj, *, rest_frame: int, final_frame: int, max_edge_deviation_rel: float,
             name: str = "recovery") -> Check:
    """Edge lengths on final_frame are within max_edge_deviation_rel of the rest frame's."""
    if final_frame <= rest_frame or max_edge_deviation_rel < 0:
        raise ValueError("need rest_frame < final_frame and max_edge_deviation_rel >= 0")
    set_frame(rest_frame)
    rest_mesh, edges = _evaluated(obj, depsgraph())
    rest = _lengths(rest_mesh, edges)
    set_frame(final_frame)
    mesh, final_edges = _evaluated(obj, depsgraph())
    if final_edges != edges:
        return Check(name, False, {"object": obj.name, "topology_changed": True},
                     {"object": obj.name, "frame": final_frame, "problem": "topology changed"})
    deviations = [abs(now / r - 1.0) for now, r in zip(_lengths(mesh, edges), rest) if r > 0]
    worst = max(range(len(deviations)), key=deviations.__getitem__)
    measurements = {"object": obj.name, "rest_frame": rest_frame, "final_frame": final_frame,
                    "largest_edge_deviation_rel": deviations[worst],
                    "mean_edge_deviation_rel": sum(deviations) / len(deviations),
                    "max_edge_deviation_rel": max_edge_deviation_rel}
    if deviations[worst] > max_edge_deviation_rel:
        return Check(name, False, measurements, {"object": obj.name, "frame": final_frame, "edge": list(edges[worst]),
                                                 "problem": "did not recover its rest shape",
                                                 "deviation_rel": deviations[worst]})
    return Check(name, True, measurements)


# Modifiers that run a simulation; copying them for a topology count would start a new solve.
_SIMULATION_MODIFIERS = {"CLOTH", "COLLISION", "SOFT_BODY", "DYNAMIC_PAINT", "PARTICLE_SYSTEM", "FLUID"}


def _quantile(values: list[float], q: float) -> float:
    """Linear-interpolated quantile of a non-empty list."""
    ordered = sorted(values)
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _stack_copy_mesh(obj, keep):
    """Evaluate a temporary copy of obj that keeps only the modifiers keep(index, mod) accepts.

    A copy instead of toggling the original's modifiers: changing a modifier on the cloth
    object marks its unbaked cache outdated. The copy shares the mesh data and is removed
    afterwards, even on error. keep may also adjust the copy's modifier settings.
    """
    tmp = obj.copy()
    try:
        for index, mod in reversed(list(enumerate(tmp.modifiers))):
            if not keep(index, mod):
                tmp.modifiers.remove(mod)
        bpy.context.scene.collection.objects.link(tmp)
        bpy.context.view_layer.update()
        evaluated = tmp.evaluated_get(depsgraph())
        mesh = evaluated.to_mesh()
        try:
            mesh.calc_loop_triangles()
            matrix = evaluated.matrix_world
            vertices = [matrix @ v.co for v in mesh.vertices]
            edges = [tuple(e.vertices) for e in mesh.edges]
            triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
            faces = len(mesh.polygons)
        finally:
            evaluated.to_mesh_clear()
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.context.view_layer.update()
    return vertices, edges, triangles, faces


def _modifier_record(mod) -> dict:
    record = {"name": mod.name, "type": mod.type, "show_viewport": mod.show_viewport, "show_render": mod.show_render}
    if mod.type in {"SUBSURF", "MULTIRES"}:
        record.update(levels=mod.levels, render_levels=mod.render_levels,
                      adaptive=bool(getattr(mod, "use_adaptive_subdivision", False)))
    return record


def cloth_setup(obj, *, colliders: Sequence = (), name: str = "cloth_setup") -> Check:
    """Solver mesh entering Cloth, render topology, cloth settings, modifier order, collider modifiers.

    Measured at the current frame. Edge lengths and area are world scale; the solver mesh is
    the evaluated stack before the Cloth modifier (viewport settings), the render topology is
    the whole stack at render visibility and levels with simulation modifiers left out (they
    do not change topology). Adaptive subdivision is counted at its render level.
    """
    if obj.type != "MESH":
        raise ValueError(f"{obj.name} is not a mesh")
    mods = list(obj.modifiers)
    stack = [_modifier_record(m) for m in mods]
    cloth_index = next((i for i, m in enumerate(mods) if m.type == "CLOTH"), None)
    collider_rows = {}
    for col in colliders:
        has_collision = any(m.type == "COLLISION" for m in col.modifiers)
        collider_rows[col.name] = {
            "collision_modifier": has_collision,
            "rigid_body": col.rigid_body is not None,
            "thickness_outer_m": col.collision.thickness_outer if has_collision and col.collision else None,
        }
    measurements = {"object": obj.name, "frame": bpy.context.scene.frame_current, "modifier_stack": stack,
                    "cloth_modifier": mods[cloth_index].name if cloth_index is not None else None,
                    "colliders": collider_rows}
    if cloth_index is None:
        return Check(name, False, measurements, {"object": obj.name, "problem": "no Cloth modifier"})
    cloth = mods[cloth_index]
    settings, collision = cloth.settings, cloth.collision_settings
    measurements["cloth_settings"] = {
        "quality": settings.quality, "collision_quality": collision.collision_quality,
        "use_collision": collision.use_collision, "use_self_collision": collision.use_self_collision,
        "distance_min_m": collision.distance_min, "self_distance_min_m": collision.self_distance_min,
        "mass_kg": settings.mass, "pin_group": settings.vertex_group_mass or None,
    }
    measurements["before_cloth"] = [m.name for m in mods[:cloth_index]]
    measurements["after_cloth"] = [m.name for m in mods[cloth_index + 1:]]

    vertices, edges, triangles, faces = _stack_copy_mesh(obj, lambda i, m: i < cloth_index)
    lengths = [(vertices[a] - vertices[b]).length for a, b in edges]
    area = sum(((vertices[b] - vertices[a]).cross(vertices[c] - vertices[a])).length / 2 for a, b, c in triangles)
    lo = [min(v[i] for v in vertices) for i in range(3)] if vertices else [0.0] * 3
    hi = [max(v[i] for v in vertices) for i in range(3)] if vertices else [0.0] * 3
    measurements["solver"] = {
        "vertices": len(vertices), "edges": len(edges), "faces": faces,
        "area_m2": area, "extent_m": [h - l for l, h in zip(lo, hi)],
        "edge_median_m": statistics.median(lengths) if lengths else None,
        "edge_p95_m": _quantile(lengths, 0.95) if lengths else None,
        "edge_min_m": min(lengths, default=None), "edge_max_m": max(lengths, default=None),
        "vertices_per_m2": len(vertices) / area if area > 0 else None,
    }

    def render_settings(index, mod):
        if mod.type in _SIMULATION_MODIFIERS or not mod.show_render:
            return False
        mod.show_viewport = True
        if mod.type in {"SUBSURF", "MULTIRES"}:
            mod.levels = mod.render_levels
        return True

    r_vertices, r_edges, _, r_faces = _stack_copy_mesh(obj, render_settings)
    measurements["render"] = {"vertices": len(r_vertices), "edges": len(r_edges), "faces": r_faces}

    problems = []
    for mod in mods[:cloth_index]:
        if mod.type == "SOLIDIFY":
            problems.append({"modifier": mod.name, "problem": "Solidify before Cloth"})
        if mod.show_viewport != mod.show_render:
            problems.append({"modifier": mod.name, "problem": "enabled differently in viewport and render before Cloth"})
        if mod.type in {"SUBSURF", "MULTIRES"}:
            if getattr(mod, "use_adaptive_subdivision", False):
                problems.append({"modifier": mod.name, "problem": "adaptive subdivision before Cloth"})
            if mod.levels != mod.render_levels:
                problems.append({"modifier": mod.name, "problem": "subdivision levels differ between viewport and render "
                                 "before Cloth", "levels": mod.levels, "render_levels": mod.render_levels})
    for col_name, row in collider_rows.items():
        if not row["collision_modifier"]:
            problems.append({"collider": col_name, "problem": "collider has no Collision modifier",
                             "rigid_body": row["rigid_body"]})
    measurements["problems"] = problems
    if problems:
        return Check(name, False, measurements, {"object": obj.name, **problems[0]})
    return Check(name, True, measurements)
