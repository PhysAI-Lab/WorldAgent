"""Cloth and elastic bodies: deformation, motion per step, self-intersection, recovery (requires bpy).

    from worldagent_measure import deformable

    deformable.deformation(flag, frame_list(1, 120), rest_frame=1, max_stretch_rel=0.1,
                           max_compression_rel=0.3, max_step_m=0.02, max_self_intersections=0)
    deformable.recovery(ball, rest_frame=1, final_frame=120, max_edge_deviation_rel=0.02)
    deformable.cloth_setup(flag, colliders=[pole, floor])
    deformable.soft_body_setup(jelly, free_fall=True, frame_start=1, frame_end=48, baked=True,
                               supports=[plate], visible=[jelly_render])
    deformable.free_fall(jelly, frame_list(1, 8), max_acceleration_error_rel=0.02, max_initial_speed_m_s=0.05)
    deformable.transfer_deviation(jelly_render, jelly, frame_list(1, 48), max_distance_m=0.004)

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
['Collision and anti-penetration']. It also reports the Cloth pressure and internal-spring
settings, the simulation clock (time), whether the solver mesh is closed, and
volumetric_signature (closed and pressure or internal springs: Cloth simulating a volume).

soft_body_setup is the same kind of setup check for a Soft Body object. It fails only on
structure: no Soft Body modifier, or one off in the viewport; another solver on the object
(Cloth, a Fluid domain, simulated particles, a rigid body; Collision and Dynamic Paint are
roles and allowed); Goal pinning a body the spec says falls freely; a modifier before Soft Body
enabled differently in viewport and render (Soft Body needs original data, so Blender keeps it
above every non-deform modifier: only deform modifiers such as Armature, Hook or Displace can
precede it, and the simulation mesh is the base mesh they deform); an open simulation mesh
when visible meshes are driven from it; a visible mesh with no modifier reading the simulation object, or an unbound
Surface/Mesh Deform; a support without an enabled Collision modifier or outside the Soft Body
collision collection; a point cache that does not cover the requested frames, or (baked=True)
is not baked or is outdated. Everything else (springs, steps, clock, ball size, transfer
distances) is reported for the row to judge.

Simulation clock: in Blender 5.1.1 one frame of Soft Body or Cloth simulates
speed (or time_scale) x 0.0316228 s whatever the fps (measured; real time is
speed = 31.6228 x fps_base / fps). The setup checks report that as time.clock_ratio_5_1 and
never pass or fail on it; free_fall measures the clock independently of the constant.

Unbaked Soft Body, Cloth and rigid body caches only advance frame by frame, so free_fall and
transfer_deviation step every frame from the cache start when a cache is unbaked.
"""

from __future__ import annotations

import math
import statistics
from typing import Sequence

import bpy
from mathutils import Vector

from . import systems
from .geometry import WorldMesh, depsgraph, set_frame, world_mesh
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
            users = {}
            for polygon in mesh.polygons:
                for key in polygon.edge_keys:
                    users[key] = users.get(key, 0) + 1
            edge_faces = [users.get(tuple(sorted(e)), 0) for e in edges]
        finally:
            evaluated.to_mesh_clear()
    finally:
        bpy.data.objects.remove(tmp, do_unlink=True)
        bpy.context.view_layer.update()
    return vertices, edges, triangles, faces, edge_faces


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

    measurements["cloth_settings"].update(
        use_pressure=settings.use_pressure, uniform_pressure_force=settings.uniform_pressure_force,
        pressure_factor=settings.pressure_factor, target_volume=settings.target_volume,
        use_internal_springs=settings.use_internal_springs,
        internal_spring_max_length=settings.internal_spring_max_length,
        internal_tension_stiffness=settings.internal_tension_stiffness,
        gravity_weight=settings.effector_weights.gravity, time_scale=settings.time_scale)
    measurements["time"] = _clock(settings.time_scale)

    vertices, edges, triangles, faces, edge_faces = _stack_copy_mesh(obj, lambda i, m: i < cloth_index)
    stats = _mesh_stats(vertices, edges, triangles, faces, edge_faces)
    area = sum(((vertices[b] - vertices[a]).cross(vertices[c] - vertices[a])).length / 2 for a, b, c in triangles)
    lo = [min(v[i] for v in vertices) for i in range(3)] if vertices else [0.0] * 3
    hi = [max(v[i] for v in vertices) for i in range(3)] if vertices else [0.0] * 3
    measurements["solver"] = {
        "vertices": len(vertices), "edges": len(edges), "faces": faces,
        "area_m2": area, "extent_m": [h - l for l, h in zip(lo, hi)],
        **{k: stats[k] for k in ("edge_median_m", "edge_p95_m", "edge_min_m", "edge_max_m")},
        "vertices_per_m2": len(vertices) / area if area > 0 else None,
        **{k: stats[k] for k in ("boundary_edges", "non_manifold_edges", "closed", "signed_volume_m3")},
    }
    measurements["volumetric_signature"] = bool(
        stats["closed"] and (settings.use_pressure or settings.use_internal_springs))

    def render_settings(index, mod):
        if mod.type in _SIMULATION_MODIFIERS or not mod.show_render:
            return False
        mod.show_viewport = True
        if mod.type in {"SUBSURF", "MULTIRES"}:
            mod.levels = mod.render_levels
        return True

    r_vertices, r_edges, _, r_faces, _ = _stack_copy_mesh(obj, render_settings)
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


# Soft Body and Cloth in Blender 5.1.1 simulate speed (time_scale) x this many seconds per frame,
# whatever the fps: free fall of a 0.2 m cube gave a / g = 1.0000012, 0.9999985, 0.9999991 at
# 24, 48 and 60 fps with speed = 31.6228 x fps_base / fps. Reported, never a pass rule.
_MEASURED_SECONDS_PER_SPEED_5_1 = 1 / math.sqrt(1000)


def _clock(speed: float, scene=None) -> dict:
    """Simulated seconds per frame against scene seconds per frame, from the measured 5.1.1 constant."""
    scene = scene or bpy.context.scene
    fps, fps_base = scene.render.fps, scene.render.fps_base
    scene_step = fps_base / fps
    simulated = speed * _MEASURED_SECONDS_PER_SPEED_5_1
    return {"speed": speed, "scene_fps": fps, "scene_fps_base": fps_base, "scene_seconds_per_frame": scene_step,
            "simulated_seconds_per_frame_5_1": simulated, "clock_ratio_5_1": simulated / scene_step,
            "real_time_speed_5_1": scene_step / _MEASURED_SECONDS_PER_SPEED_5_1,
            "clock_basis": "measured in Blender 5.1.1: one frame simulates speed x 0.0316228 s whatever the fps; "
                           "1 is real time, below 1 slow motion; verify with free_fall",
            "blender_version": bpy.app.version_string}


def _mesh_stats(vertices, edges, triangles, faces: int, edge_faces) -> dict:
    """Counts, open/non-manifold edges, connected components, closedness, signed volume, edge lengths."""
    lengths = [(vertices[a] - vertices[b]).length for a, b in edges]
    parent = list(range(len(vertices)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in edges:
        parent[find(a)] = find(b)
    boundary = sum(1 for n in edge_faces if n == 1)
    non_manifold = sum(1 for n in edge_faces if n > 2)
    volume = sum(vertices[a].dot(vertices[b].cross(vertices[c])) for a, b, c in triangles) / 6
    return {"vertices": len(vertices), "edges": len(edges), "faces": faces,
            "boundary_edges": boundary, "non_manifold_edges": non_manifold,
            "components": len({find(i) for i in range(len(vertices))}),
            "closed": faces > 0 and boundary == 0 and non_manifold == 0, "signed_volume_m3": volume,
            "edge_min_m": min(lengths, default=None),
            "edge_median_m": statistics.median(lengths) if lengths else None,
            "edge_p95_m": _quantile(lengths, 0.95) if lengths else None,
            "edge_max_m": max(lengths, default=None)}


def _unbaked_cache_starts(objects) -> list[int]:
    starts = []
    for obj in objects:
        for mod in obj.modifiers:
            if mod.type in {"SOFT_BODY", "CLOTH"} and mod.show_viewport and not mod.point_cache.is_baked:
                starts.append(mod.point_cache.frame_start)
        world = bpy.context.scene.rigidbody_world
        if obj.rigid_body is not None and world is not None and world.point_cache is not None \
                and not world.point_cache.is_baked:
            starts.append(world.point_cache.frame_start)
    return starts


def _step_frames(objects, frames: Sequence[int]):
    """Set each frame in turn and yield it; with an unbaked cache, step every frame from its start.

    An unbaked Soft Body, Cloth or rigid body cache only advances one frame at a time; a jump
    leaves the body at rest.
    """
    starts = _unbaked_cache_starts(objects)
    wanted = set(frames)
    if starts:
        for frame in range(min(min(starts), frames[0]), frames[-1] + 1):
            set_frame(frame)
            if frame in wanted:
                yield frame
    else:
        for frame in frames:
            set_frame(frame)
            yield frame


def _tree_references(tree, sim, seen) -> bool:
    """A node tree (followed into group nodes) has an Object socket set to sim or a Collection holding it."""
    if tree is None or tree.name in seen:
        return False
    seen.add(tree.name)
    for node in tree.nodes:
        for socket in node.inputs:
            value = getattr(socket, "default_value", None)
            if socket.type == "OBJECT" and value == sim:
                return True
            if socket.type == "COLLECTION" and value is not None and sim.name in value.all_objects:
                return True
        if node.type == "GROUP" and _tree_references(node.node_tree, sim, seen):
            return True
    return False


def _driving_modifiers(visible, sim) -> list[dict]:
    """Modifiers on visible that read sim: any Object pointer property equal to sim, or a Geometry
    Nodes modifier whose inputs or node tree reference sim (Object socket or Collection)."""
    found = []
    for mod in visible.modifiers:
        via = None
        for prop in mod.bl_rna.properties:
            if prop.type == "POINTER" and getattr(prop.fixed_type, "identifier", None) == "Object" \
                    and getattr(mod, prop.identifier) == sim:
                via = prop.identifier
                break
        if via is None and mod.type == "NODES" and mod.node_group is not None:
            for item in mod.node_group.interface.items_tree:
                if getattr(item, "in_out", None) != "INPUT" or item.identifier not in mod.keys():
                    continue
                value = mod[item.identifier]
                if item.socket_type == "NodeSocketObject" and value == sim:
                    via = f"input {item.name}"
                elif item.socket_type == "NodeSocketCollection" and value is not None \
                        and sim.name in value.all_objects:
                    via = f"input {item.name}"
                if via:
                    break
            if via is None and _tree_references(mod.node_group, sim, set()):
                via = "node tree"
        if via is not None:
            record = {"name": mod.name, "type": mod.type, "via": via, "enabled": bool(mod.show_viewport)}
            if hasattr(mod, "is_bound"):
                record["is_bound"] = bool(mod.is_bound)
            found.append(record)
    return found


_GOAL_RULE = ("measured in Blender 5.1.1: use_goal off -> 0; vertex_group_goal naming a group with at least one "
              "vertex assigned -> assigned vertices goal_min + w x (goal_max - goal_min), unassigned 0; otherwise "
              "(no name, missing group, empty group) goal_default clamped to [goal_min, goal_max] for every vertex")


def _effective_goal(obj, settings) -> tuple[list[float], dict]:
    """Goal strength per vertex of the original mesh, by the measured 5.1.1 rule (_GOAL_RULE)."""
    n = len(obj.data.vertices)
    group = obj.vertex_groups.get(settings.vertex_group_goal) if settings.vertex_group_goal else None
    weights = {}
    if group is not None:
        for vertex in obj.data.vertices:
            for g in vertex.groups:
                if g.group == group.index:
                    weights[vertex.index] = g.weight
    record = {"group_exists": group is not None, "group_assigned_vertices": len(weights)}
    lo, hi = settings.goal_min, settings.goal_max
    if not settings.use_goal:
        goal = [0.0] * n
    elif weights:
        goal = [lo + weights[i] * (hi - lo) if i in weights else 0.0 for i in range(n)]
    else:
        goal = [min(max(settings.goal_default, lo), hi)] * n
    return goal, record


def _subdivision_problems(mods, solver: str) -> list[dict]:
    problems = []
    for mod in mods:
        if mod.show_viewport != mod.show_render:
            problems.append({"modifier": mod.name, "problem": f"enabled differently in viewport and render before {solver}"})
        if mod.type in {"SUBSURF", "MULTIRES"}:
            if getattr(mod, "use_adaptive_subdivision", False):
                problems.append({"modifier": mod.name, "problem": f"adaptive subdivision before {solver}"})
            if mod.levels != mod.render_levels:
                problems.append({"modifier": mod.name, "problem": "subdivision levels differ between viewport and "
                                 f"render before {solver}", "levels": mod.levels, "render_levels": mod.render_levels})
    return problems


def _distances(points, surface) -> list[float]:
    return [surface.bvh.find_nearest(p)[3] for p in points]


def _transfer_record(points, surface) -> dict:
    """Distance of points to surface (max, p95, median) and the fraction inside it when it is closed."""
    d = _distances(points, surface) if points else []
    return {"max_m": max(d, default=None), "p95_m": _quantile(d, 0.95) if d else None,
            "median_m": statistics.median(d) if d else None,
            "inside_fraction": sum(1 for p in points if surface.contains(p)) / len(points)
            if surface.closed and points else None}


def soft_body_setup(obj, *, free_fall: bool, frame_start: int, frame_end: int, baked: bool,
                    supports: Sequence = (), visible: Sequence = (), name: str = "soft_body_setup") -> Check:
    """Soft Body solver setup on the current frame (call it on the rest frame); structural failures only.

    free_fall     the spec says the body is not anchored, so Goal must pin no vertex
    frame_start, frame_end   frames the story needs from this body; the point cache must cover them
    baked         True in a verifier (cache must be baked and current), False in a pre-bake check
    supports      objects the body can touch; each needs an enabled Collision modifier
    visible       render meshes driven by this simulation object (Surface/Mesh Deform, Geometry
                  Nodes, ...); when given, the simulation mesh must be closed

    Measurements: physics, modifier_stack, soft_body_modifier, before_soft_body, after_soft_body,
    goal, springs, mass_and_media, steps, time (clock), collision, simulation_mesh,
    render_topology, transfer (per visible), supports, solver_distance_m (largest support
    thickness_outer: a Soft Body rests about 0.9 x that above a Collision surface), cache, problems.
    """
    if obj.type != "MESH":
        raise ValueError(f"{obj.name} is not a mesh")
    if frame_end < frame_start:
        raise ValueError(f"frame_end {frame_end} < frame_start {frame_start}")
    if any(v == obj for v in visible) or any(s == obj for s in supports):
        raise ValueError(f"{obj.name} cannot be its own support or visible mesh")
    scene = bpy.context.scene
    mods = list(obj.modifiers)
    sb_index = next((i for i, m in enumerate(mods) if m.type == "SOFT_BODY"), None)
    others = [s for s in systems.physics_systems(obj, include_disabled=True) if s not in ("soft_body", "none")]
    if obj.rigid_body is not None and "bullet_rigid_body" not in others:
        others.append("bullet_rigid_body")
    measurements = {
        "object": obj.name, "frame": scene.frame_current,
        "physics": {"systems": systems.physics_systems(obj, include_disabled=True),
                    "disabled_systems": systems.disabled_systems(obj), "roles": systems.physics_roles(obj),
                    "modifiers": [{"name": m.name, "type": m.type} for m in mods if m.type in _SIMULATION_MODIFIERS],
                    "rigid_body": obj.rigid_body.type if obj.rigid_body is not None else None,
                    "other_solvers": others},
        "modifier_stack": [_modifier_record(m) for m in mods],
        "soft_body_modifier": mods[sb_index].name if sb_index is not None else None,
    }
    if sb_index is None:
        measurements["problems"] = [{"problem": "no Soft Body modifier"}]
        return Check(name, False, measurements, {"object": obj.name, "problem": "no Soft Body modifier"})
    mod = mods[sb_index]
    s = mod.settings
    measurements["before_soft_body"] = [m.name for m in mods[:sb_index]]
    measurements["after_soft_body"] = [m.name for m in mods[sb_index + 1:]]
    goal, goal_record = _effective_goal(obj, s)
    pinned = sum(1 for g in goal if g > 0)
    measurements["goal"] = {"use_goal": s.use_goal, "vertex_group_goal": s.vertex_group_goal or None, **goal_record,
                            "goal_default": s.goal_default, "goal_min": s.goal_min, "goal_max": s.goal_max,
                            "goal_spring": s.goal_spring, "goal_friction": s.goal_friction,
                            "pinned_vertices": pinned, "vertices": len(goal), "goal_rule": _GOAL_RULE}
    measurements["springs"] = {k: getattr(s, k) for k in (
        "use_edges", "pull", "push", "damping", "plastic", "bend", "use_stiff_quads", "shear", "spring_length",
        "vertex_group_spring", "aero", "aerodynamics_type")}
    measurements["mass_and_media"] = {
        "mass": s.mass, "vertex_group_mass": s.vertex_group_mass or None, "friction": s.friction,
        "gravity_weight": s.effector_weights.gravity, "effector_weight_all": s.effector_weights.all,
        "scene_use_gravity": scene.use_gravity, "scene_gravity": list(scene.gravity)}
    measurements["steps"] = {k: getattr(s, k) for k in (
        "use_auto_step", "step_min", "step_max", "error_threshold", "choke", "fuzzy", "use_estimate_matrix")}
    measurements["time"] = _clock(s.speed, scene)

    vertices, edges, triangles, faces, edge_faces = _stack_copy_mesh(obj, lambda i, m: i < sb_index)
    stats = _mesh_stats(vertices, edges, triangles, faces, edge_faces)
    stats["self_intersections"] = self_intersections(WorldMesh(obj.name, vertices, triangles))
    measurements["simulation_mesh"] = stats

    def render_settings(index, m):
        if m.type in _SIMULATION_MODIFIERS or not m.show_render:
            return False
        m.show_viewport = True
        if m.type in {"SUBSURF", "MULTIRES"}:
            m.levels = m.render_levels
        return True

    r_vertices, r_edges, _, r_faces, _ = _stack_copy_mesh(obj, render_settings)
    measurements["render_topology"] = {"vertices": len(r_vertices), "edges": len(r_edges), "faces": r_faces}

    collection = s.collision_collection
    edge_min = stats["edge_min_m"]
    support_rows = {}
    for support in supports:
        col_mod = next((m for m in support.modifiers if m.type == "COLLISION"), None)
        c = support.collision if col_mod is not None else None
        support_rows[support.name] = {
            "collision_modifier": col_mod is not None,
            "collision_enabled": bool(col_mod.show_viewport and c.use) if col_mod is not None else None,
            "rigid_body": support.rigid_body is not None,
            "thickness_outer_m": c.thickness_outer if c else None, "thickness_inner_m": c.thickness_inner if c else None,
            "damping": c.damping if c else None, "friction_factor": c.friction_factor if c else None,
            "in_collision_collection": collection is None or support.name in collection.all_objects,
        }
    thickness = [r["thickness_outer_m"] for r in support_rows.values() if r["thickness_outer_m"] is not None]
    measurements["supports"] = support_rows
    measurements["solver_distance_m"] = max(thickness, default=None)
    measurements["collision"] = {
        "use_self_collision": s.use_self_collision, "collision_type": s.collision_type, "ball_size": s.ball_size,
        "ball_stiff": s.ball_stiff, "ball_damp": s.ball_damp, "use_edge_collision": s.use_edge_collision,
        "use_face_collision": s.use_face_collision, "collision_collection": collection.name if collection else None,
        "edge_min_over_ball_size": edge_min / s.ball_size if edge_min and s.collision_type == "MANUAL"
        and s.ball_size > 0 else None,
        "edge_min_over_support_thickness": edge_min / max(thickness) if edge_min and thickness and max(thickness) > 0
        else None,
    }

    graph = depsgraph()
    from .contact import _Surface  # same package; closed-surface inside test
    surface = _Surface(obj, graph)
    transfer = {}
    for vis in visible:
        transfer[vis.name] = {"driving_modifiers": _driving_modifiers(vis, obj),
                              **_transfer_record(world_mesh(vis, graph).vertices, surface)}
    measurements["transfer"] = transfer

    cache = mod.point_cache
    measurements["cache"] = {"frame_start": cache.frame_start, "frame_end": cache.frame_end,
                             "is_baked": cache.is_baked, "is_outdated": cache.is_outdated, "info": cache.info,
                             "requested": [frame_start, frame_end]}

    problems = []
    if not mod.show_viewport:
        problems.append({"modifier": mod.name, "problem": "Soft Body modifier disabled"})
    if others:
        problems.append({"problem": "another solver on the simulation object", "systems": others})
    if free_fall and pinned:
        problems.append({"problem": "Goal pins a free-falling body", "pinned_vertices": pinned})
    problems += _subdivision_problems(mods[:sb_index], "Soft Body")
    if visible and not stats["closed"]:
        problems.append({"problem": "simulation mesh not closed", "boundary_edges": stats["boundary_edges"],
                         "non_manifold_edges": stats["non_manifold_edges"]})
    for vis_name, row in transfer.items():
        if not row["driving_modifiers"]:
            problems.append({"visible": vis_name, "problem": "visible object not driven by the simulation object"})
        for d in row["driving_modifiers"]:
            if d.get("is_bound") is False:
                problems.append({"visible": vis_name, "modifier": d["name"], "problem": "transfer modifier not bound"})
    for sup_name, row in support_rows.items():
        if not row["collision_modifier"]:
            problems.append({"support": sup_name, "problem": "support has no Collision modifier",
                             "rigid_body": row["rigid_body"]})
        elif not row["collision_enabled"]:
            problems.append({"support": sup_name, "problem": "support Collision disabled"})
        if not row["in_collision_collection"]:
            problems.append({"support": sup_name, "problem": "support outside the Soft Body collision collection",
                             "collision_collection": collection.name})
    if cache.frame_start > frame_start or cache.frame_end < frame_end:
        problems.append({"problem": f"cache frames {cache.frame_start}-{cache.frame_end} do not cover "
                                    f"{frame_start}-{frame_end}"})
    if baked and not cache.is_baked:
        problems.append({"problem": "the cache is not baked"})
    elif baked and cache.is_outdated:
        problems.append({"problem": "the cache is outdated"})
    measurements["problems"] = problems
    if problems:
        return Check(name, False, measurements, {"object": obj.name, **problems[0]})
    return Check(name, True, measurements)


def _solve3(a, b):
    """Gaussian elimination with partial pivoting for a 3 x 3 system."""
    m = [row[:] + [v] for row, v in zip(a, b)]
    for i in range(3):
        p = max(range(i, 3), key=lambda r: abs(m[r][i]))
        m[i], m[p] = m[p], m[i]
        for r in range(3):
            if r != i:
                f = m[r][i] / m[i][i]
                m[r] = [x - f * y for x, y in zip(m[r], m[i])]
    return [m[i][3] / m[i][i] for i in range(3)]


def _solver_cache(obj):
    """(clock setting name, value, point cache) of obj's Soft Body or Cloth, or rigid body world cache."""
    for mod in obj.modifiers:
        if mod.type == "SOFT_BODY":
            return "speed", mod.settings.speed, mod.point_cache
        if mod.type == "CLOTH":
            return "time_scale", mod.settings.time_scale, mod.point_cache
    world = bpy.context.scene.rigidbody_world
    if obj.rigid_body is not None and world is not None:
        return None, None, world.point_cache
    return None, None, None


def free_fall(obj, frames: Sequence[int], *, max_acceleration_error_rel: float, max_initial_speed_m_s: float,
              slow_motion: float = 1.0, name: str = "free_fall") -> Check:
    """Measured fall of obj's evaluated vertex mean against scene gravity.

    Least-squares fit along up = -gravity of h(t) = h0 + v0 t - a t^2 / 2, t in scene seconds
    from frames[0] (anchored at the first fitted frame). Expected a = |g| / slow_motion^2
    (slow_motion: scene seconds per simulated second stated in the spec, 1 = real time). Passes
    when |a / a_expected - 1| <= max_acceleration_error_rel and |v0| <= max_initial_speed_m_s.
    Frames must be consecutive and end before first contact. v0 is the upward speed at frames[0]
    (negative = moving down); Soft Body integration leaves an offset of about 0.05 x g x frame time
    downward, so set max_initial_speed_m_s above it.
    """
    frames = list(frames)
    if len(frames) < 4 or any(b != a + 1 for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be at least 4 consecutive frames: {frames}")
    if max_acceleration_error_rel < 0 or max_initial_speed_m_s < 0:
        raise ValueError("thresholds must be >= 0")
    if slow_motion <= 0:
        raise ValueError(f"slow_motion must be > 0: {slow_motion}")
    if obj.type != "MESH":
        raise ValueError(f"{obj.name} is not a mesh")
    scene = bpy.context.scene
    gravity = Vector(scene.gravity)
    if not scene.use_gravity or gravity.length == 0:
        raise ValueError("scene gravity is off or zero")
    g = gravity.length
    up = -gravity.normalized()
    fps, fps_base = scene.render.fps, scene.render.fps_base
    rows, means = [], []
    for frame in _step_frames([obj], frames):
        vertices = world_mesh(obj).vertices
        if not vertices:
            raise ValueError(f"{obj.name} has no vertices on frame {frame}")
        mean = sum(vertices, Vector()) / len(vertices)
        means.append(mean)
        rows.append({"frame": frame, "mean_m": mean.dot(up), "lowest_m": min(v.dot(up) for v in vertices)})
    ts = [(f - frames[0]) * fps_base / fps for f in frames]
    hs = [r["mean_m"] for r in rows]
    basis = [[1.0, t, -t * t / 2] for t in ts]
    ata = [[sum(row[i] * row[j] for row in basis) for j in range(3)] for i in range(3)]
    atb = [sum(row[i] * h for row, h in zip(basis, hs)) for i in range(3)]
    h0, v0, a = _solve3(ata, atb)
    residual = math.sqrt(sum((h0 + v0 * t - a * t * t / 2 - h) ** 2 for t, h in zip(ts, hs)) / len(ts))
    expected = g / slow_motion ** 2
    lateral = [(m - means[0]) - up * (m - means[0]).dot(up) for m in means]
    clock_ratio = math.sqrt(a / g) if a > 0 else None
    setting, value, cache = _solver_cache(obj)
    measurements = {
        "object": obj.name, "frames": rows, "a_m_s2": a, "g_m_s2": g, "slow_motion": slow_motion,
        "a_expected_m_s2": expected, "a_over_expected": a / expected, "v0_m_s": v0, "rms_residual_m": residual,
        "lateral_drift_m": max(v.length for v in lateral), "measured_clock_ratio": clock_ratio,
        "fps": fps, "fps_base": fps_base, "cache_baked": cache.is_baked if cache is not None else None,
        "thresholds": {"max_acceleration_error_rel": max_acceleration_error_rel,
                       "max_initial_speed_m_s": max_initial_speed_m_s},
    }
    if setting is not None:
        measurements[setting] = value
        measurements[f"{setting}_for_real_time"] = value / clock_ratio if clock_ratio else None
    error = abs(a / expected - 1)
    if error > max_acceleration_error_rel:
        return Check(name, False, measurements, {"object": obj.name, "problem": "fall acceleration off scene gravity",
                                                 "a_over_expected": a / expected, "error_rel": error})
    if abs(v0) > max_initial_speed_m_s:
        return Check(name, False, measurements, {"object": obj.name, "problem": "initial speed", "v0_m_s": v0})
    return Check(name, True, measurements)


def transfer_deviation(visible, simulation, frames: Sequence[int], *, max_distance_m: float,
                       name: str = "transfer_deviation") -> Check:
    """Distance from every evaluated visible vertex to the evaluated simulation surface, per frame.

    A bound Surface Deform can still sit far from its target, so binding is no evidence; this
    measures the result. Fails on the first frame where max_m > max_distance_m ("visible mesh
    left the simulation surface") or the visible vertex count changes ("topology changed").
    Reported per frame: max_m, p95_m, median_m, simulation_to_visible_max_m (reverse direction,
    a loose cage's stand-off), inside_fraction (simulation mesh closed only; a vertex lying on the
    surface counts either way), max_change_m
    (largest per-vertex change of the distance since frames[0]; a Mesh Deform cage has a rest gap).
    """
    frames = list(frames)
    if not frames or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be non-empty and strictly increasing: {frames}")
    if max_distance_m < 0:
        raise ValueError("max_distance_m must be >= 0")
    if visible == simulation:
        raise ValueError(f"{visible.name} is both the visible and the simulation object")
    from .contact import _Surface  # same package; closed-surface inside test
    rows, first_failure, reference, count = [], None, None, None
    for frame in _step_frames([visible, simulation], frames):
        graph = depsgraph()
        surface = _Surface(simulation, graph)
        vis = world_mesh(visible, graph)
        if count is None:
            count = len(vis.vertices)
        if len(vis.vertices) != count:
            rows.append({"frame": frame, "topology_changed": True, "vertices": len(vis.vertices)})
            if first_failure is None:
                first_failure = {"visible": visible.name, "frame": frame, "problem": "topology changed",
                                 "vertices": len(vis.vertices), "expected_vertices": count}
            continue
        d = _distances(vis.vertices, surface)
        if reference is None:
            reference = d
        record = {"frame": frame, **_transfer_record(vis.vertices, surface),
                  "simulation_to_visible_max_m": max((vis.bvh().find_nearest(p)[3] for p in surface.mesh.vertices),
                                                     default=None) if vis.triangles else None,
                  "max_change_m": max((abs(x - y) for x, y in zip(d, reference)), default=0.0)}
        rows.append(record)
        if first_failure is None and record["max_m"] is not None and record["max_m"] > max_distance_m:
            worst = max(range(len(d)), key=d.__getitem__)
            first_failure = {"visible": visible.name, "frame": frame, "problem": "visible mesh left the simulation "
                             "surface", "vertex": worst, "value": record["max_m"]}
    measured = [r for r in rows if not r.get("topology_changed")]
    measurements = {"visible": visible.name, "simulation": simulation.name, "frames": rows,
                    "frames_checked": len(rows),
                    "largest_max_m": max((r["max_m"] for r in measured if r["max_m"] is not None), default=None),
                    "largest_change_m": max((r["max_change_m"] for r in measured), default=None),
                    "max_distance_m": max_distance_m}
    return Check(name, first_failure is None, measurements, first_failure)
