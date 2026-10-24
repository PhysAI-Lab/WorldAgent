"""Validity of the meshes a grid liquid solver voxelizes as obstacles (requires bpy).

    from worldagent_measure.solver_mesh import grid_cell_size, solver_mesh_validity

    cell = grid_cell_size(bpy.data.objects["SIM_liquid_domain"])
    solver_mesh_validity(liquid_reachable_objects, cell_size_m=cell, min_cells=1)

Mantaflow and FLIP Fluids decide which grid cells are inside an obstacle from its
mesh, so an obstacle the liquid can reach must be a valid closed solid that spans
enough cells. FLIP solver meshes must be closed-manifold [flip-fluids.en.md §3 item 3,
'Validate manifold geometry']; the mesh audit covers boundary/non-manifold edges,
zero-area faces, normal consistency, and connected components
[simulation-workflow.en.md 'boundary/non-manifold edges ...']. Thickness is counted
in grid cells across the object's narrowest dimension (its smallest extent along its
own axes): no participating volume may be smaller than one cell, collision walls
must span multiple cells, primary openings and animated obstacles three to four
[flip-fluids.en.md §3 item 4, 'cells across the narrowest important dimension']. The case
picks min_cells from those rules. The smallest local thickness (inward rays from every
face) is also reported, but only as information: trim, bevels, and lips are legitimately
thinner than a cell and do not decide the verdict.

This is the complement of grouping static architecture in the contact check:
overlapping separate obstacles are harmless to a grid solver (it takes their union),
but a single self-intersecting or open obstacle mesh is not.

Groups: the case may declare obstacles the solver fills as one union, e.g. a carpet
lying on the floor or panels mounted on a wall. Thickness is then judged on the
group (its world-axis box), not on each thin member, and every member must measurably
touch another member (closest surface gap <= group_contact_tol_m), so a group cannot
hide a floating or detached piece. Topology is still checked per mesh, because the
solver voxelizes each mesh on its own.
"""

from __future__ import annotations

import math

from mathutils.bvhtree import BVHTree

from .geometry import depsgraph, set_frame, world_mesh
from .scale import extents
from .support import surface_gap
from .verifier import Check

# Polygon area at or below this counts as zero-area.
ZERO_AREA_EPS_M2 = 1e-8
# Start each thickness ray just inside the surface so it does not hit its own face; numeric only.
_RAY_INSET_REL = 1e-6


def grid_cell_size(domain) -> float:
    """cell_size ~= max(domain dimensions) / resolution [flip-fluids.en.md §3 item 4].

    Reads a Mantaflow domain (`resolution_max`) or a FLIP Fluids domain
    (`flip_fluid.domain.simulation.resolution`).
    """
    longest = max(domain.dimensions)
    for mod in getattr(domain, "modifiers", []):
        if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            return longest / mod.domain_settings.resolution_max
    flip = getattr(domain, "flip_fluid", None)
    if flip is not None and flip.object_type == "TYPE_DOMAIN":
        return longest / flip.domain.simulation.resolution
    raise ValueError(f"{domain.name} is not a Mantaflow or FLIP Fluids domain")


def _edge_use(triangles: list) -> dict:
    """Undirected edge -> list of directed occurrences (a, b) in triangle order."""
    use: dict = {}
    for a, b, c in triangles:
        for u, v in ((a, b), (b, c), (c, a)):
            use.setdefault((u, v) if u < v else (v, u), []).append((u, v))
    return use


def _components(n_vertices: int, triangles: list) -> int:
    parent = list(range(n_vertices))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b, c in triangles:
        parent[find(a)] = find(b)
        parent[find(b)] = find(c)
    return len({find(i) for t in triangles for i in t})


def _zero_area_polygons(obj, graph, eps_m2: float) -> int:
    """Polygons (not triangulation pieces) with world-space area <= eps.

    Triangulating an n-gon with collinear vertices yields zero-area triangles that are not defects.
    """
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        m = evaluated.matrix_world
        area: dict = {}
        for t in mesh.loop_triangles:
            a, b, c = (m @ mesh.vertices[i].co for i in t.vertices)
            area[t.polygon_index] = area.get(t.polygon_index, 0.0) + (b - a).cross(c - a).length / 2
        return sum(1 for i in range(len(mesh.polygons)) if area.get(i, 0.0) <= eps_m2)
    finally:
        evaluated.to_mesh_clear()


def _signed_volume(vertices: list, triangles: list) -> float:
    return sum(vertices[a].dot(vertices[b].cross(vertices[c])) for a, b, c in triangles) / 6.0


def _min_thickness(vertices: list, triangles: list, bvh: BVHTree, scale: float) -> tuple:
    """Smallest inward ray length from a face centroid to the opposite surface: (thickness, point).

    Assumes outward normals; faces whose inward ray hits nothing are counted separately.
    """
    best, where, misses = math.inf, None, 0
    inset = _RAY_INSET_REL * max(scale, 1.0)
    for a, b, c in triangles:
        pa, pb, pc = vertices[a], vertices[b], vertices[c]
        normal = (pb - pa).cross(pc - pa)
        if normal.length == 0:
            continue
        inward = -normal.normalized()
        centroid = (pa + pb + pc) / 3
        hit, _, _, dist = bvh.ray_cast(centroid + inward * inset, inward)
        if hit is None:
            misses += 1
            continue
        if dist + inset < best:
            best, where = dist + inset, list(centroid)
    return best, where, misses


def solver_mesh_validity(objects: list, cell_size_m: float | None = None, min_cells: float | None = None, *,
                         groups: dict | None = None, group_contact_tol_m: float | None = None,
                         frame: int | None = None, zero_area_eps_m2: float = ZERO_AREA_EPS_M2,
                         name: str = "solver_mesh_validity") -> Check:
    """Each object must be a closed, consistently oriented, non-self-intersecting solid whose
    narrowest dimension spans at least min_cells grid cells.

    objects: obstacle meshes the liquid can reach (the case chooses them).
    cell_size_m, min_cells: give both to check thickness (see grid_cell_size); omit both to
    check topology only. min_cells has no default: pick it from flip-fluids.en.md §3 item 4.
    groups: {group name: [object names]} the solver fills as one union (see module doc);
    needs the thickness check and group_contact_tol_m (required with groups).
    """
    if not objects:
        raise ValueError("no objects")
    if (cell_size_m is None) != (min_cells is None):
        raise ValueError("give cell_size_m and min_cells together, or neither")
    if cell_size_m is not None and (cell_size_m <= 0 or min_cells <= 0):
        raise ValueError("cell_size_m and min_cells must be positive")
    groups = groups or {}
    names = {o.name for o in objects}
    group_of = {}
    for group, members in groups.items():
        if len(members) < 2:
            raise ValueError(f"group {group!r} needs at least two members")
        for member in members:
            if member not in names:
                raise ValueError(f"group {group!r} member {member!r} is not among the checked objects")
            if member in group_of:
                raise ValueError(f"{member!r} is in groups {group_of[member]!r} and {group!r}")
            group_of[member] = group
    if groups and (cell_size_m is None or group_contact_tol_m is None or group_contact_tol_m < 0):
        raise ValueError("groups need cell_size_m, min_cells, and a non-negative group_contact_tol_m")
    if frame is not None:
        set_frame(frame)
    graph = depsgraph()

    meshes = {}
    per_object = {}
    first_failure = None

    def fail(obj_name: str, metric: str, value, **extra) -> None:
        nonlocal first_failure
        if first_failure is None:
            first_failure = {"object": obj_name, "metric": metric, "value": value, **extra}

    for obj in objects:
        mesh = world_mesh(obj, graph)
        meshes[obj.name] = mesh
        tris = mesh.triangles
        if not tris:
            per_object[obj.name] = {"triangles": 0}
            fail(obj.name, "empty_mesh", 0)
            continue
        verts = mesh.vertices
        use = _edge_use(tris)
        open_edges = sum(1 for occ in use.values() if len(occ) == 1)
        non_manifold = sum(1 for occ in use.values() if len(occ) > 2)
        # In a consistently oriented closed surface each shared edge runs once each way.
        flipped = sum(1 for occ in use.values() if len(occ) == 2 and occ[0] == occ[1])
        zero_area = _zero_area_polygons(obj, graph, zero_area_eps_m2)
        bvh = mesh.bvh()
        self_hits = sum(1 for i, j in bvh.overlap(bvh) if i < j and not set(tris[i]) & set(tris[j]))
        volume = _signed_volume(verts, tris)
        rec = {
            "triangles": len(tris),
            "open_edges": open_edges,
            "non_manifold_edges": non_manifold,
            "inconsistent_orientation_edges": flipped,
            "zero_area_polygons": zero_area,
            "self_intersecting_triangle_pairs": self_hits,
            "components": _components(len(verts), tris),
            "signed_volume_m3": volume,
        }
        for metric in ("open_edges", "non_manifold_edges", "inconsistent_orientation_edges",
                       "zero_area_polygons", "self_intersecting_triangle_pairs"):
            if rec[metric]:
                fail(obj.name, metric, rec[metric])
        if open_edges == 0 and non_manifold == 0 and volume <= 0:
            fail(obj.name, "inverted_normals", volume, reason="closed mesh with non-positive signed volume")
        if cell_size_m is not None:
            narrowest = min(extents(obj, graph))
            cells = narrowest / cell_size_m
            lo, hi = mesh.bbox
            local, where, misses = _min_thickness(verts, tris, bvh, (hi - lo).length)
            rec.update(narrowest_dimension_m=narrowest, narrowest_dimension_cells=cells,
                       min_local_thickness_m=local if math.isfinite(local) else None,
                       min_local_thickness_point=where, inward_ray_misses=misses)
            if obj.name in group_of:
                rec["thickness_judged_by_group"] = group_of[obj.name]
            elif cells < min_cells:
                fail(obj.name, "narrowest_dimension_cells", cells, min_cells=min_cells, narrowest_m=narrowest)
        per_object[obj.name] = rec

    group_records = {}
    for group, members in groups.items():
        points = [v for m in members for v in meshes[m].vertices]
        if not points:
            fail(group, "empty_group", 0)
            continue
        narrowest = min(max(v[i] for v in points) - min(v[i] for v in points) for i in range(3))
        cells = narrowest / cell_size_m
        gaps = {}
        for m in members:
            others = [o for o in members if o != m and meshes[o].triangles]
            gaps[m] = min((surface_gap(meshes[m], meshes[o]) for o in others), default=math.inf) \
                if meshes[m].triangles else math.inf
        group_records[group] = {"members": list(members), "narrowest_dimension_m": narrowest,
                                "narrowest_dimension_cells": cells, "member_gap_m": gaps}
        for m, gap in gaps.items():
            if gap > group_contact_tol_m:
                fail(m, "group_member_gap", gap, group=group, group_contact_tol_m=group_contact_tol_m)
        if cells < min_cells:
            fail(group, "group_narrowest_dimension_cells", cells, min_cells=min_cells, narrowest_m=narrowest)

    topology_keys = ("open_edges", "non_manifold_edges", "inconsistent_orientation_edges", "zero_area_polygons",
                     "self_intersecting_triangle_pairs")
    invalid = sorted(n for n, r in per_object.items() if any(r.get(k) for k in topology_keys))
    measurements = {
        "objects_checked": len(per_object),
        "zero_area_eps_m2": zero_area_eps_m2,
        "cell_size_m": cell_size_m,
        "min_cells": min_cells,
        "topology_invalid_objects": invalid,
        "thin_objects": sorted(n for n, r in per_object.items()
                               if min_cells is not None and n not in group_of
                               and r.get("narrowest_dimension_cells", 0) < min_cells),
        "group_contact_tol_m": group_contact_tol_m,
        "groups": group_records,
        "objects": per_object,
    }
    return Check(name, first_failure is None, measurements, first_failure)
