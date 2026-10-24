"""Interpenetration and contact on evaluated surfaces (requires bpy).

    from worldagent_measure.contact import penetration

    penetration([box, table, floor], frame_list(1, 120), eps_pen=1e-5)
    penetration([(box, table)], [1], eps_pen=1e-5)

Pairs are picked by AABB overlap (broad phase only, never a verdict), then
measured on the evaluated world-space triangles [paper Eq. 2;
non-penetration-contract.en.md 'Detection guidance']:

    dist(i, j) >= -eps_pen  for every pair on every checked frame

dist is the signed mesh clearance, negative when penetrating. Depth is measured
from points of one surface that lie inside the other: its vertices plus a grid
of samples on its triangles that the BVH reports as overlapping the other mesh,
clipped to the shared AABB. "Inside" is ray parity plus the nearest face's back side for a closed (watertight)
mesh, which also catches containment with no surface crossing; for an open mesh
(e.g. a ground plane) it is the back side of the nearest face, tested only on
points of crossing triangles. Depth is the distance to the nearest surface
point, so it is a lower bound off by at most `depth_sample_spacing_max_m`
(distance is 1-Lipschitz). Contact uses the same criterion on every frame
[paper 3.2.2 'Contact'].

eps_pen is required and only absorbs geometry-query noise; it is reported
separately and is not a penetration allowance [non-penetration-contract.en.md
'Hard rule']. Self-intersection is not checked here.

Long ranges reuse work for objects that do not change. Every frame, every
object's evaluated mesh is read and compared byte for byte with the previous
build: world matrix, local vertex coordinates and loop-triangle indices. A
surface is a pure function of exactly those, so an equal key gives the same
triangles, BVH, closedness and shells, and the surface (with its lazy caches)
is kept; any difference rebuilds it. This catches keyed transforms, modifiers,
shape keys, drivers and simulation caches alike, without trusting a caller's
notion of "static" or depsgraph update flags. A pair whose two surfaces are the
same objects as when it was last measured gets that measurement again, since
the narrow phase is deterministic in its inputs; it is counted on every frame
as if measured, so verdicts, the first failure and per-pair stats are those of
a full re-measure.
"""

from __future__ import annotations

import math
from array import array
from itertools import combinations

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from .geometry import WorldMesh, aabb_overlap, depsgraph, set_frame, world_mesh
from .verifier import Check

# Sampling and ray settings are measurement resolution, not thresholds.
# Grid divisions per edge of a clipped triangle (upper bound; fewer when the edge is short vs eps_pen).
MAX_DIVISIONS = 8
# Irrational-ish directions so parity rays rarely graze an edge or a face; majority of three.
_RAY_DIRECTIONS = (Vector((0.5773, 0.5774, 0.5775)).normalized(),
                   Vector((-0.7071, 0.3162, 0.6325)).normalized(),
                   Vector((0.2673, -0.8018, 0.5345)).normalized())
# BVH queries run in float32; step past a hit by this much of the mesh scale before casting again.
_RAY_STEP_REL = 1e-6
_MAX_RAY_HITS = 100000


def _resolve(obj):
    return bpy.data.objects[obj] if isinstance(obj, str) else obj


def _pairs(objects_or_pairs: list) -> list[tuple]:
    """All pairs of a flat object list, or the explicit (a, b) pairs given."""
    if not objects_or_pairs:
        raise ValueError("no objects or pairs")
    if all(isinstance(p, (tuple, list)) for p in objects_or_pairs):
        pairs = [(_resolve(a), _resolve(b)) for a, b in objects_or_pairs]
    elif any(isinstance(p, (tuple, list)) for p in objects_or_pairs):
        raise ValueError("pass either objects or (a, b) pairs, not both")
    else:
        objects = [_resolve(o) for o in objects_or_pairs]
        if len(objects) < 2:
            raise ValueError("need at least two objects")
        pairs = list(combinations(objects, 2))
    for a, b in pairs:
        if a.name == b.name:
            raise ValueError(f"pair of {a.name!r} with itself; self-intersection is not checked here")
    return pairs


def is_closed(mesh: WorldMesh) -> bool:
    """Watertight: every triangle edge is shared by exactly two triangles (unmerged seams count as open)."""
    if not mesh.triangles:
        return False
    edges = {}
    for tri in mesh.triangles:
        for k in range(3):
            edge = tuple(sorted((tri[k], tri[(k + 1) % 3])))
            edges[edge] = edges.get(edge, 0) + 1
    return all(count == 2 for count in edges.values())


def _geometry_key(obj, graph) -> tuple:
    """Everything a _Surface is built from, as bytes, so equality is exact (no hashing, no tolerance)."""
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        coords = array("f", bytes(4 * 3 * len(mesh.vertices)))
        mesh.vertices.foreach_get("co", coords)
        triangles = array("i", bytes(4 * 3 * len(mesh.loop_triangles)))
        mesh.loop_triangles.foreach_get("vertices", triangles)
        matrix = array("d", [x for row in evaluated.matrix_world for x in row])
    finally:
        evaluated.to_mesh_clear()
    return matrix.tobytes(), coords.tobytes(), triangles.tobytes()


class _Surface:
    """Evaluated mesh of one object, with its BVH and closedness; kept while its _geometry_key is unchanged."""

    def __init__(self, obj, graph):
        self.mesh = world_mesh(obj, graph)
        self.name = self.mesh.name
        self.bbox = self.mesh.bbox
        self.bvh = self.mesh.bvh()
        self.closed = is_closed(self.mesh)
        lo, hi = self.bbox
        scale = (hi - lo).length + max(abs(c) for c in (*lo, *hi))
        self.ray_step = _RAY_STEP_REL * max(scale, 1.0)
        self._self_intersections = None
        self._component_bvhs = None

    @property
    def self_intersections(self) -> int:
        """Triangle pairs of this mesh that intersect without sharing a vertex (0 for a valid solid).

        Inside/outside is not well defined for a self-intersecting mesh, so depths measured
        against it are reported as ambiguous instead of being trusted.
        """
        if self._self_intersections is None:
            tris = self.mesh.triangles
            self._self_intersections = sum(
                1 for i, j in self.bvh.overlap(self.bvh) if i < j and not set(tris[i]) & set(tris[j]))
        return self._self_intersections

    def _components(self) -> list:
        """One BVH per connected shell. Authored assets often join overlapping closed shells
        into one object; parity over the whole mesh is even inside their overlap."""
        if self._component_bvhs is None:
            parent = list(range(len(self.mesh.vertices)))

            def find(i: int) -> int:
                while parent[i] != i:
                    parent[i] = parent[parent[i]]
                    i = parent[i]
                return i

            for a, b, c in self.mesh.triangles:
                parent[find(a)] = find(b)
                parent[find(b)] = find(c)
            shells = {}
            for tri in self.mesh.triangles:
                shells.setdefault(find(tri[0]), []).append(tri)
            if len(shells) == 1:
                self._component_bvhs = [self.bvh]
            else:
                coords = [v[:] for v in self.mesh.vertices]
                self._component_bvhs = [BVHTree.FromPolygons(coords, tris, all_triangles=True, epsilon=0.0)
                                        for tris in shells.values()]
        return self._component_bvhs

    def _parity(self, bvh, point: Vector) -> bool:
        """Ray parity against one shell, majority of three directions."""
        votes = 0
        for direction in _RAY_DIRECTIONS:
            hits, origin, last = 0, point, None
            for _ in range(_MAX_RAY_HITS):
                location, _, index, _ = bvh.ray_cast(origin, direction)
                if location is None:
                    break
                if index != last:
                    hits += 1
                last = index
                origin = location + direction * self.ray_step
            votes += hits % 2
        return votes >= 2

    def contains(self, point: Vector) -> bool:
        """Inside any closed shell of the mesh; meaningful only for a closed mesh."""
        return any(self._parity(bvh, point) for bvh in self._components())


def _clip_to_box(polygon: list, lo: Vector, hi: Vector) -> list:
    """Sutherland-Hodgman clip of a convex polygon to an axis-aligned box."""
    for axis in range(3):
        for bound, keep_below in ((lo[axis], False), (hi[axis], True)):
            if not polygon:
                return []
            inside = (lambda p: p[axis] <= bound) if keep_below else (lambda p: p[axis] >= bound)
            out = []
            for k, cur in enumerate(polygon):
                prev = polygon[k - 1]
                if inside(cur) != inside(prev):
                    t = (bound - prev[axis]) / (cur[axis] - prev[axis])
                    out.append(prev.lerp(cur, t))
                if inside(cur):
                    out.append(cur)
            polygon = out
    return polygon


def _samples(mesh: WorldMesh, triangles: set, lo: Vector, hi: Vector, eps_pen: float) -> tuple[list, float]:
    """Grid points on the given triangles clipped to the box, and the largest grid spacing used."""
    points, spacing = [], 0.0
    for t in triangles:
        polygon = _clip_to_box([mesh.vertices[i].copy() for i in mesh.triangles[t]], lo, hi)
        for k in range(1, len(polygon) - 1):
            a, b, c = polygon[0], polygon[k], polygon[k + 1]
            longest = max((b - a).length, (c - b).length, (a - c).length)
            n = MAX_DIVISIONS if eps_pen <= 0 else max(1, min(MAX_DIVISIONS, math.ceil(longest / eps_pen)))
            spacing = max(spacing, longest / n)
            for i in range(n + 1):
                for j in range(n + 1 - i):
                    points.append(a + (b - a) * (i / n) + (c - a) * (j / n))
    return points, spacing


def _measure_into(x: _Surface, y: _Surface, x_tris: set, lo: Vector, hi: Vector, eps_pen: float) -> dict:
    """Signed clearance of points of x's surface against y; depth > 0 means inside y."""
    if y.closed:
        # Every vertex of x in the shared box: catches containment without any surface crossing.
        vertices = [v for v in x.mesh.vertices if all(lo[k] <= v[k] <= hi[k] for k in range(3))]
    else:
        # An open surface has no inside; only points of triangles that cross it can be "through" it.
        used = {i for t in x_tris for i in x.mesh.triangles[t]}
        vertices = [x.mesh.vertices[i] for i in used if all(lo[k] <= x.mesh.vertices[i][k] <= hi[k] for k in range(3))]
    samples, spacing = _samples(x.mesh, x_tris, lo, hi, eps_pen)
    best = {"clearance": None, "depth": 0.0, "point": None, "count": 0, "spacing": spacing}
    for p in vertices + samples:
        location, normal, _, distance = y.bvh.find_nearest(p)
        if location is None:
            continue
        best["count"] += 1
        # At coincident contact the sign is noise, but |signed| <= eps_pen so the verdict does not depend on it.
        behind = (p - location).dot(normal) < 0
        if y.closed:
            # Parity alone misreads self-intersecting or internally faced "closed" meshes, which
            # authored assets often are (an outside point can get an odd hit count). Require
            # the nearest face to agree, so such a mesh cannot invent depth.
            inside = behind and y.contains(p)
        else:
            inside = behind
        signed = -distance if inside else distance
        if best["clearance"] is None or signed < best["clearance"]:
            best["clearance"] = signed
        if inside and distance > best["depth"]:
            best.update(depth=distance, point=list(p))
    return best


def _measure_pair(a: _Surface, b: _Surface, eps_pen: float) -> dict:
    """Narrow phase for one AABB candidate pair on the current frame."""
    lo = Vector([max(a.bbox[0][k], b.bbox[0][k]) - eps_pen for k in range(3)])
    hi = Vector([min(a.bbox[1][k], b.bbox[1][k]) + eps_pen for k in range(3)])
    overlap = a.bvh.overlap(b.bvh)
    a_tris = {i for i, _ in overlap}
    b_tris = {j for _, j in overlap}
    a_in_b = _measure_into(a, b, a_tris, lo, hi, eps_pen)
    b_in_a = _measure_into(b, a, b_tris, lo, hi, eps_pen)
    deeper, inside, of = (a_in_b, b, a) if a_in_b["depth"] >= b_in_a["depth"] else (b_in_a, a, b)
    clearances = [m["clearance"] for m in (a_in_b, b_in_a) if m["clearance"] is not None]
    return {
        "depth": deeper["depth"],
        "clearance": min(clearances) if clearances else None,
        "point": deeper["point"],
        "point_of": of.name,
        "inside": inside.name,
        "kind": "surface_intersection" if overlap else "containment",
        # Meshes an inside test found depth in, if they self-intersect. Only looked up when there
        # is depth, so a valid result does not pay for the self-overlap query.
        "ambiguous": {y.name: y.self_intersections for x_in_y, y in ((a_in_b, b), (b_in_a, a))
                      if x_in_y["depth"] > eps_pen and y.self_intersections},
        "triangle_overlaps": len(overlap),
        "points": a_in_b["count"] + b_in_a["count"],
        "spacing": max(a_in_b["spacing"], b_in_a["spacing"]),
    }


def penetration(objects_or_pairs: list, frames: list[int], eps_pen: float, *, name: str = "interpenetration") -> Check:
    """Fail when any pair's signed clearance is below -eps_pen on any checked frame [paper Eq. 2].

    objects_or_pairs: objects (every pair is checked) or explicit (a, b) pairs; names are accepted.
    frames: frames to scan in order (see geometry.frame_list); every frame is scanned, and the
    first failing frame (its deepest pair) is reported. eps_pen: required, geometry-query noise only.
    """
    if not math.isfinite(eps_pen) or eps_pen < 0:
        raise ValueError(f"eps_pen must be finite and >= 0, got {eps_pen}")
    if not frames:
        raise ValueError("no frames")
    pairs = _pairs(objects_or_pairs)
    objects = list({o.name: o for pair in pairs for o in pair}.values())
    start_frame = bpy.context.scene.frame_current

    candidates = triangle_overlaps = points = 0
    surfaces_built = surfaces_reused = pairs_measured = pairs_reused = 0
    surfaces, keys = {}, {}
    # Pair key -> (surface a, surface b, result) of its last measurement.
    measured = {}
    changed = set()
    spacing = 0.0
    min_clearance = None
    max_depth = None
    open_meshes = set()
    self_intersecting = {}
    per_pair = {}
    penetrating_frames = []
    first_failure = None
    try:
        for frame in frames:
            set_frame(frame)
            graph = depsgraph()
            for o in objects:
                key = _geometry_key(o, graph)
                if keys.get(o.name) == key:
                    surfaces_reused += 1
                    continue
                if o.name in keys:
                    changed.add(o.name)
                surfaces[o.name], keys[o.name] = _Surface(o, graph), key
                surfaces_built += 1
            open_meshes.update(n for n, s in surfaces.items() if not s.closed)
            frame_worst = None
            for obj_a, obj_b in pairs:
                a, b = surfaces[obj_a.name], surfaces[obj_b.name]
                if not aabb_overlap(a.bbox, b.bbox, margin=eps_pen):
                    continue
                candidates += 1
                key = f"{a.name}|{b.name}"
                last = measured.get(key)
                if last is not None and last[0] is a and last[1] is b:
                    m = last[2]
                    pairs_reused += 1
                else:
                    m = _measure_pair(a, b, eps_pen)
                    measured[key] = (a, b, m)
                    pairs_measured += 1
                triangle_overlaps += m["triangle_overlaps"]
                points += m["points"]
                spacing = max(spacing, m["spacing"])
                stats = per_pair.setdefault(key, {"pair": [a.name, b.name], "candidate_frames": 0,
                                                  "penetrating_frames": 0, "max_depth_m": 0.0,
                                                  "min_signed_clearance_m": None, "ambiguous_solid": False})
                stats["candidate_frames"] += 1
                stats["max_depth_m"] = max(stats["max_depth_m"], m["depth"])
                max_depth = m["depth"] if max_depth is None else max(max_depth, m["depth"])
                if m["clearance"] is not None:
                    if stats["min_signed_clearance_m"] is None or m["clearance"] < stats["min_signed_clearance_m"]:
                        stats["min_signed_clearance_m"] = m["clearance"]
                    if min_clearance is None or m["clearance"] < min_clearance:
                        min_clearance = m["clearance"]
                if m["depth"] > eps_pen:
                    stats["penetrating_frames"] += 1
                    if m["ambiguous"]:
                        stats["ambiguous_solid"] = True
                        self_intersecting.update(m["ambiguous"])
                    if frame_worst is None or m["depth"] > frame_worst[2]["depth"]:
                        frame_worst = (a.name, b.name, m)
            if frame_worst is not None:
                penetrating_frames.append(frame)
                if first_failure is None:
                    a_name, b_name, m = frame_worst
                    first_failure = {"frame": frame, "pair": [a_name, b_name], "object": m["point_of"],
                                     "other_object": m["inside"], "value": m["depth"],
                                     "signed_clearance_m": -m["depth"], "kind": m["kind"],
                                     "point": m["point"], "eps_pen_m": eps_pen,
                                     # Non-empty: a mesh of this pair self-intersects, so the depth is
                                     # ambiguous; still a failure (unverifiable is not a pass), fix the asset.
                                     "self_intersecting": m["ambiguous"]}
    finally:
        set_frame(start_frame)

    measurements = {
        "eps_pen_m": eps_pen,
        "frames_checked": len(frames),
        "frame_range": [frames[0], frames[-1]],
        "pairs_checked": len(pairs),
        "broad_phase_candidates": candidates,
        "triangle_overlap_pairs": triangle_overlaps,
        "depth_points_tested": points,
        "depth_sample_spacing_max_m": spacing,
        "penetrating_pair_count": sum(1 for s in per_pair.values() if s["penetrating_frames"]),
        "penetrating_frame_count": len(penetrating_frames),
        "first_penetrating_frame": penetrating_frames[0] if penetrating_frames else None,
        # None: no pair reached the narrow phase on any frame, so nothing was measured.
        "min_signed_clearance_m": min_clearance,
        "max_penetration_depth_m": max_depth,
        "open_meshes": sorted(open_meshes),
        "self_intersecting_meshes": self_intersecting,
        "candidate_pairs": list(per_pair.values()),
        # Work actually done: a surface or pair measurement is reused when its inputs are
        # byte-identical to the last build (see module docstring); results do not depend on it.
        "surfaces_built": surfaces_built,
        "surfaces_reused": surfaces_reused,
        "pairs_measured": pairs_measured,
        "pairs_reused": pairs_reused,
        # Objects whose evaluated geometry or transform changed after the first checked frame.
        "changed_objects": sorted(changed),
    }
    return Check(name, first_failure is None, measurements, first_failure)
