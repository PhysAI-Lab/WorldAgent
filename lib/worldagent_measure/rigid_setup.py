"""Rigid body setup mistakes that Bullet does not report (requires bpy).

    from worldagent_measure import rigid_setup

    rigid_setup.origin_at_center_of_mass(max_offset_m=0.001)
    rigid_setup.constraints_in_world()
    rigid_setup.bodies_in_world()
    rigid_setup.keyed_active_bodies(kinematic=["Pusher"])
    rigid_setup.cache_coverage(1, 216)
    rigid_setup.collision_shape_fit(max_protrusion_m=0.001, max_uncovered_m=0.001)

Bullet rotates and collides each rigid body about its object origin, which it uses as the
center of mass. An origin far from the geometry (for example after applying location, which
moves it to the world origin) makes a body swing about the wrong point and can explode the
first frame. origin_at_center_of_mass measures, per rigid body, the distance from the origin
to the center of mass of its evaluated mesh: the volume centroid for a closed mesh, else the
area centroid of its surface (reported as `method`).

Bullet evaluates only the constraint objects in the collection assigned to
scene.rigidbody_world.constraints. A constraint outside it, or with an object missing, is
ignored without any error. constraints_in_world lists every such constraint.

Bullet likewise simulates only the bodies in scene.rigidbody_world.collection; a body outside
it, or an ACTIVE body with `enabled` ("Dynamic") off, never moves. bodies_in_world lists them.

An ACTIVE body follows Bullet and ignores its own keyframes unless `kinematic` ("Animated") is
on, so keys on a non-kinematic ACTIVE body are silently dropped (or the body was meant to be
kinematic). keyed_active_bodies lists every such body with the animated data paths.

The rigid body world bakes only its point cache's frame range. cache_coverage checks that the
range covers the frames the story needs and that the cache is baked.

Bullet collides a body on its collision shape, not its visible mesh. A primitive shape (BOX,
SPHERE, CAPSULE, CYLINDER, CONE) is built from the mesh bounds along fixed local axes, so a
mesh that does not match it gets an invisible air wall (the shape sticks out) or parts that
pass through other bodies (the mesh sticks out). collision_shape_fit measures both per body.

Run them on the candidate before baking (cache_coverage after), or in a verifier at the first
frame. The offset threshold and the frame range are required arguments from the row.
"""
from __future__ import annotations

from typing import Iterable

import math
from dataclasses import dataclass

import bmesh
import bpy
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from .geometry import depsgraph, world_mesh
from .verifier import Check

# Below this |volume| (m^3) a mesh is treated as open or flat and its surface centroid is used.
MIN_VOLUME_M3 = 1e-12


def center_of_mass(obj, graph=None) -> tuple[Vector, str]:
    """World-space center of mass of the evaluated mesh at uniform density, and the method used."""
    mesh = world_mesh(obj, graph)
    if not mesh.triangles:
        raise ValueError(f"{obj.name} has no faces")
    volume, moment = 0.0, Vector()
    area, surface = 0.0, Vector()
    for i, j, k in mesh.triangles:
        a, b, c = mesh.vertices[i], mesh.vertices[j], mesh.vertices[k]
        v = a.dot(b.cross(c)) / 6.0
        volume += v
        moment += v * (a + b + c) / 4.0
        t = (b - a).cross(c - a).length / 2.0
        area += t
        surface += t * (a + b + c) / 3.0
    if abs(volume) > MIN_VOLUME_M3:
        return moment / volume, "volume"
    if area == 0.0:
        raise ValueError(f"{obj.name} has no area")
    return surface / area, "surface"


# Data paths that move an object; keys or drivers on them fight Bullet on a non-kinematic body.
TRANSFORM_PATHS = ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale",
                   "delta_location", "delta_rotation_euler", "delta_rotation_quaternion", "delta_scale")


def _animation(obj) -> tuple[list, set[str]]:
    """F-curves animating obj (its action and NLA strips) and the data paths of its drivers.

    Blender 5 layered actions hold F-curves in channelbags per slot; only the slot assigned
    to obj is read, so an action shared between objects does not leak the other's curves.
    """
    anim = obj.animation_data
    if anim is None:
        return [], set()
    curves = []
    sources = [(anim.action, getattr(anim, "action_slot", None))]
    sources += [(s.action, getattr(s, "action_slot", None)) for t in anim.nla_tracks for s in t.strips]
    for action, slot in sources:
        if action is None:
            continue
        if hasattr(action, "layers"):
            for layer in action.layers:
                for strip in layer.strips:
                    for bag in strip.channelbags:
                        if slot is None or bag.slot == slot:
                            curves.extend(bag.fcurves)
        else:
            curves.extend(action.fcurves)
    return curves, {d.data_path for d in anim.drivers}


def animated_paths(obj) -> set[str]:
    """Data paths of obj animated by its action, its NLA strips, or a driver."""
    curves, drivers = _animation(obj)
    return {fc.data_path for fc in curves} | drivers


def switched(obj, data_path: str) -> bool:
    """True when data_path changes value over time: keys with different values, or a driver.

    A single key (or equal keys) only pins the value, e.g. kinematic keyed on once at frame 1.
    """
    curves, drivers = _animation(obj)
    if data_path in drivers:
        return True
    values = {k.co[1] for fc in curves if fc.data_path == data_path for k in fc.keyframe_points}
    return len(values) > 1


def rigid_bodies(scene=None) -> list:
    scene = scene or bpy.context.scene
    return [o for o in scene.objects if o.type == "MESH" and o.rigid_body is not None]


def origin_at_center_of_mass(objects: Iterable | None = None, *, max_offset_m: float,
                             name: str = "origin_at_center_of_mass") -> Check:
    """Every rigid body's origin lies within max_offset_m of its mesh's center of mass.

    objects defaults to every mesh with a rigid body in the scene. PASSIVE bodies are included:
    their origin does not move them, but it is where Bullet places their collision shape.
    """
    if max_offset_m < 0:
        raise ValueError("max_offset_m must be >= 0")
    objects = list(objects) if objects is not None else rigid_bodies()
    graph = depsgraph()
    rows, failures = [], []
    for obj in objects:
        com, method = center_of_mass(obj, graph)
        origin = obj.evaluated_get(graph).matrix_world.translation
        offset = (com - origin).length
        rows.append({"object": obj.name, "offset_m": offset, "method": method})
        if offset > max_offset_m:
            failures.append({"object": obj.name, "offset_m": offset, "origin": list(origin),
                             "center_of_mass": list(com), "method": method})
    worst = max(rows, key=lambda r: r["offset_m"]) if rows else None
    measurements = {"bodies_checked": len(rows), "max_offset_m": max_offset_m,
                    "largest_offset": worst, "failing_count": len(failures), "failing": failures[:20]}
    if not rows:
        return Check(name, False, measurements, {"problem": "no rigid bodies to check"})
    return Check(name, not failures, measurements, failures[0] if failures else None)


def constraints_in_world(scene=None, *, name: str = "constraints_in_world") -> Check:
    """Every rigid body constraint is in the world's constraint collection and joins two objects."""
    scene = scene or bpy.context.scene
    world = scene.rigidbody_world
    collection = world.constraints if world else None
    members = set(collection.all_objects) if collection else set()
    found, ignored = [], []
    for obj in scene.objects:
        con = obj.rigid_body_constraint
        if con is None:
            continue
        entry = {"object": obj.name, "type": con.type,
                 "object1": con.object1.name if con.object1 else None,
                 "object2": con.object2.name if con.object2 else None, "enabled": con.enabled}
        found.append(entry)
        problems = []
        if world is None:
            problems.append("the scene has no rigid body world")
        elif obj not in members:
            problems.append("not in scene.rigidbody_world.constraints, so Bullet ignores it")
        if not (con.object1 and con.object2):
            problems.append("object1 or object2 is not set")
        if problems:
            ignored.append({**entry, "problems": problems})
    measurements = {"constraint_count": len(found),
                    "constraint_collection": collection.name if collection else None,
                    "ignored_count": len(ignored), "ignored": ignored[:20]}
    return Check(name, not ignored, measurements, ignored[0] if ignored else None)


def bodies_in_world(objects: Iterable | None = None, *, scene=None, name: str = "bodies_in_world") -> Check:
    """Every rigid body is in the world's collection, and every ACTIVE body has Dynamic on.

    objects defaults to every mesh with a rigid body in the scene; a given object without a
    rigid body is reported too. An ACTIVE body whose `enabled` is keyed to switch on later is
    listed under animated_dynamic, not as a failure.
    """
    scene = scene or bpy.context.scene
    world = scene.rigidbody_world
    collection = world.collection if world else None
    members = set(collection.all_objects) if collection else set()
    objects = list(objects) if objects is not None else rigid_bodies(scene)
    failing, animated = [], []
    for obj in objects:
        rb = obj.rigid_body
        problems = []
        if rb is None:
            problems.append("has no rigid body")
        else:
            if world is None:
                problems.append("the scene has no rigid body world")
            elif collection is None:
                problems.append("scene.rigidbody_world.collection is not set")
            elif obj not in members:
                problems.append("not in scene.rigidbody_world.collection, so Bullet does not simulate it")
            if rb.type == "ACTIVE" and not rb.enabled:
                if switched(obj, "rigid_body.enabled"):
                    animated.append(obj.name)
                else:
                    problems.append("ACTIVE with enabled (Dynamic) off, so it never moves")
        if problems:
            failing.append({"object": obj.name, "type": rb.type if rb else None, "problems": problems})
    measurements = {"bodies_checked": len(objects),
                    "world_collection": collection.name if collection else None,
                    "world_collection_count": len(members),
                    "animated_dynamic": animated[:20],
                    "failing_count": len(failing), "failing": failing[:20]}
    if not objects:
        return Check(name, False, measurements, {"problem": "no rigid bodies to check"})
    return Check(name, not failing, measurements, failing[0] if failing else None)


def keyed_active_bodies(objects: Iterable | None = None, *, kinematic: Iterable[str] = (), scene=None,
                        name: str = "keyed_active_bodies") -> Check:
    """No ACTIVE rigid body that Bullet drives has keys or drivers on its transform.

    A body is exempt when rigid_body.kinematic is on, when it is named in `kinematic` (the
    case's declared kinematic inputs), or when rigid_body.kinematic is keyed to change value
    (an animated release: keyed while kinematic, then handed to Bullet); the last are listed
    under animated_release. objects defaults to every mesh with a rigid body in the scene.
    """
    scene = scene or bpy.context.scene
    declared = set(kinematic)
    objects = list(objects) if objects is not None else rigid_bodies(scene)
    keyed, released, exempt = [], [], []
    checked = 0
    for obj in objects:
        rb = obj.rigid_body
        if rb is None or rb.type != "ACTIVE":
            continue
        checked += 1
        paths = animated_paths(obj)
        moving = sorted(p for p in paths if p in TRANSFORM_PATHS)
        if switched(obj, "rigid_body.kinematic"):
            released.append({"object": obj.name, "transform_paths": moving})
        elif rb.kinematic or obj.name in declared:
            if moving:
                exempt.append(obj.name)
        elif moving:
            keyed.append({"object": obj.name, "transform_paths": moving,
                          "problem": "ACTIVE, not kinematic, but its transform is animated"})
    measurements = {"active_bodies_checked": checked, "declared_kinematic": sorted(declared),
                    "keyed_kinematic": exempt[:20], "animated_release": released[:20],
                    "failing_count": len(keyed), "failing": keyed[:20]}
    return Check(name, not keyed, measurements, keyed[0] if keyed else None)


def cache_coverage(frame_start: int, frame_end: int, *, scene=None, name: str = "cache_coverage") -> Check:
    """The rigid body world's point cache spans [frame_start, frame_end] and is baked."""
    if frame_end < frame_start:
        raise ValueError("frame_end must be >= frame_start")
    scene = scene or bpy.context.scene
    world = scene.rigidbody_world
    wanted = {"frame_start": frame_start, "frame_end": frame_end}
    if world is None:
        return Check(name, False, {"requested": wanted, "has_world": False},
                     {"problem": "the scene has no rigid body world"})
    cache = world.point_cache
    measurements = {"requested": wanted, "has_world": True,
                    "cache": {"frame_start": cache.frame_start, "frame_end": cache.frame_end},
                    "is_baked": cache.is_baked, "info": cache.info, "world_enabled": world.enabled}
    problems = []
    if cache.frame_start > frame_start or cache.frame_end < frame_end:
        problems.append(f"cache frames {cache.frame_start}-{cache.frame_end} do not cover "
                        f"{frame_start}-{frame_end}")
    if not cache.is_baked:
        problems.append("the cache is not baked")
    if not world.enabled:
        problems.append("the rigid body world is disabled")
    failure = {"problems": problems, **measurements["cache"], "is_baked": cache.is_baked} if problems else None
    return Check(name, not problems, measurements, failure)


PRIMITIVE_SHAPES = ("BOX", "SPHERE", "CAPSULE", "CYLINDER", "CONE")
# Margin Blender gives BOX and CYLINDER when use_margin is off (measured, see collision_primitive).
DEFAULT_MARGIN_M = 0.04
# Surface directions sampled per primitive for the protrusion, plus its corner and axis directions.
SURFACE_SAMPLES = 1500


def _box_sdf(p: Vector, b: Vector) -> float:
    q = Vector((abs(p.x) - b.x, abs(p.y) - b.y, abs(p.z) - b.z))
    outside = Vector((max(q.x, 0.0), max(q.y, 0.0), max(q.z, 0.0))).length
    return outside + min(max(q.x, q.y, q.z), 0.0)


def _cylinder_sdf(p: Vector, radius: float, half_height: float) -> float:
    dx, dz = math.hypot(p.x, p.y) - radius, abs(p.z) - half_height
    return min(max(dx, dz), 0.0) + math.hypot(max(dx, 0.0), max(dz, 0.0))


def _cone_sdf(p: Vector, radius: float, half_height: float) -> float:
    """Exact distance to a solid cone along Z: base of `radius` at -half_height, apex at +half_height."""
    qx, qy = math.hypot(p.x, p.y), p.z
    ax, ay = qx - min(qx, radius if qy < 0.0 else 0.0), abs(qy) - half_height
    k2x, k2y = -radius, 2.0 * half_height
    t = min(max(((0.0 - qx) * k2x + (half_height - qy) * k2y) / (k2x * k2x + k2y * k2y), 0.0), 1.0)
    bx, by = qx + k2x * t, qy - half_height + k2y * t
    sign = -1.0 if bx < 0.0 and ay < 0.0 else 1.0
    return sign * math.sqrt(min(ax * ax + ay * ay, bx * bx + by * by))


@dataclass
class Primitive:
    """A rigid body's primitive collision shape as Bullet builds it.

    frame   shape space to world: the object's origin and rotation, without scale
    size    dimensions as built, in metres (half extents, radius, half_height)
    margin  rounds edges inside the size (BOX, CYLINDER), lies outside it (CONE), or none
    """

    shape: str
    frame: Matrix
    size: dict
    margin_m: float
    margin_mode: str

    def local_distance(self, p: Vector) -> float:
        """Signed distance from p (shape space) to the shape surface; negative inside."""
        m, z = self.margin_m, self.size
        if self.shape == "BOX":
            return _box_sdf(p, Vector(z["half_extents"]) - Vector((m, m, m))) - m
        if self.shape == "SPHERE":
            return p.length - z["radius"]
        if self.shape == "CYLINDER":
            return _cylinder_sdf(p, z["radius"] - m, z["half_height"] - m) - m
        if self.shape == "CAPSULE":
            segment = max(z["half_height"] - z["radius"], 0.0)
            return (p - Vector((0.0, 0.0, min(max(p.z, -segment), segment)))).length - z["radius"]
        return _cone_sdf(p, z["radius"], z["half_height"]) - m

    def distance(self, world_point: Vector) -> float:
        return self.local_distance(self.frame.inverted_safe() @ world_point)

    def surface_points(self, count: int = SURFACE_SAMPLES) -> list:
        """World-space points on the shape surface, along evenly spread and feature directions."""
        reach = 2.0 * (max(abs(v) for v in self._extent()) + self.margin_m) + 1e-6
        directions = [Vector((x, y, zz)) for x in (-1, 0, 1) for y in (-1, 0, 1) for zz in (-1, 0, 1)
                      if (x, y, zz) != (0, 0, 0)]
        e = self._extent()
        directions += [Vector((x * e.x, y * e.y, zz * e.z)) for x in (-1, 1) for y in (-1, 1) for zz in (-1, 1)]
        golden = math.pi * (3.0 - math.sqrt(5.0))
        for i in range(count):
            zc = 1.0 - 2.0 * (i + 0.5) / count
            r = math.sqrt(1.0 - zc * zc)
            directions.append(Vector((r * math.cos(golden * i), r * math.sin(golden * i), zc)))
        points = []
        for d in directions:
            d = d.normalized()
            lo, hi = 0.0, reach  # the shape is convex and contains its centre
            for _ in range(40):
                mid = 0.5 * (lo + hi)
                lo, hi = (mid, hi) if self.local_distance(d * mid) < 0.0 else (lo, mid)
            points.append(self.frame @ (d * hi))
        return points

    def _extent(self) -> Vector:
        z = self.size
        if self.shape == "BOX":
            return Vector(z["half_extents"])
        if self.shape == "SPHERE":
            return Vector((z["radius"],) * 3)
        return Vector((z["radius"], z["radius"], z["half_height"]))


def collision_primitive(obj, graph=None, mesh=None) -> Primitive:
    """The primitive collision shape Blender builds for obj's rigid body.

    Measured in Blender 5.1.1 by resting a sphere-shaped probe, held on a slider constraint,
    against a PASSIVE body from each axis (gravity along that axis), with margins 0 to 0.04,
    off-centre meshes, an Array modifier, and object scales (2, 1, 1) and (1, 2, 3):
      - the size comes from the evaluated mesh's local bounds (modifiers included), and the
        shape is centred on the object origin, not on the bounds centre;
      - BOX: half extents = local half bounds * object scale;
      - SPHERE: radius = largest local half bound * scale x;
      - CYLINDER: along local Z, radius = max(half x, half y) * scale x, half height = half z * scale z;
      - CAPSULE: along local Z, local radius r = max(half x, half y); radius = r * scale y,
        half segment = max(half z - r, 0) * scale z, so half height = half segment + radius;
      - CONE: along local Z, apex at +Z, base radius = max(half x, half y) * (scale x + scale y) / 2,
        half height = half z * scale z; it stays circular under scale (1, 2, 3): probes along x
        and y agree, and match an unscaled mesh giving the same cone;
      - margin: BOX and CYLINDER keep their outer size and round edges and corners with radius
        collision_margin (0.04 when use_margin is off); a margin above a half extent gives a
        shape that no longer matches its bounds. CONE adds collision_margin outside, whatever
        use_margin says. SPHERE and CAPSULE ignore it.
    Over 359 probe rests the model matched Bullet within 0.1 mm for BOX, SPHERE, CYLINDER and
    CAPSULE and within 0.6 mm for CONE. A probe dropped along Z onto a cone flank 9.5 degrees
    from vertical rested 2.7 mm lower than the model, the same on the unscaled cone: Bullet's
    ~0.45 mm resting penetration along the contact normal, magnified by the slope. Along the
    flank normal the same cone matched within 0.1 mm.
    """
    rb = obj.rigid_body
    if rb is None or rb.collision_shape not in PRIMITIVE_SHAPES:
        raise ValueError(f"{obj.name} has no primitive collision shape")
    mesh = mesh or world_mesh(obj, graph)
    if not mesh.vertices:
        raise ValueError(f"{obj.name} has no vertices")
    matrix = obj.evaluated_get(graph or depsgraph()).matrix_world
    to_local = matrix.inverted_safe()
    local = [to_local @ v for v in mesh.vertices]
    h = [(max(v[i] for v in local) - min(v[i] for v in local)) / 2.0 for i in range(3)]
    location, rotation, scale = matrix.decompose()
    sx, sy, sz = (abs(c) for c in scale)
    frame = Matrix.Translation(location) @ rotation.to_matrix().to_4x4()
    shape = rb.collision_shape
    margin = rb.collision_margin if rb.use_margin else DEFAULT_MARGIN_M
    if shape == "BOX":
        return Primitive(shape, frame, {"half_extents": [h[0] * sx, h[1] * sy, h[2] * sz]}, margin, "rounds edges inside")
    if shape == "SPHERE":
        return Primitive(shape, frame, {"radius": max(h) * sx}, 0.0, "none")
    if shape == "CYLINDER":
        return Primitive(shape, frame, {"radius": max(h[0], h[1]) * sx, "half_height": h[2] * sz},
                         margin, "rounds edges inside")
    if shape == "CAPSULE":
        radius = max(h[0], h[1])
        segment = max(h[2] - radius, 0.0) * sz
        return Primitive(shape, frame, {"radius": radius * sy, "half_height": segment + radius * sy}, 0.0, "none")
    return Primitive(shape, frame, {"radius": max(h[0], h[1]) * (sx + sy) / 2.0, "half_height": h[2] * sz},
                     rb.collision_margin, "added outside")

# Modifier types that only move vertices; Blender's DEFORM mesh source keeps the leading ones.
DEFORM_MODIFIERS = {"ARMATURE", "CAST", "CURVE", "DISPLACE", "HOOK", "LAPLACIANDEFORM", "LATTICE", "MESH_DEFORM",
                    "SHRINKWRAP", "SIMPLE_DEFORM", "SMOOTH", "CORRECTIVE_SMOOTH", "LAPLACIANSMOOTH",
                    "SURFACE_DEFORM", "WARP", "WAVE", "MESH_CACHE", "VOLUME_DISPLACE"}
# Hull face samples for the protrusion: spacing as a fraction of the hull's bounding diagonal.
HULL_SAMPLE_SPACING = 1.0 / 80.0


def _convex_hull(points: list) -> tuple[list, list, list]:
    """Hull vertices, triangles and outward planes (normal, offset) of a point set."""
    bm = bmesh.new()
    try:
        for p in points:
            bm.verts.new(p)
        out = bmesh.ops.convex_hull(bm, input=bm.verts[:])
        bmesh.ops.delete(bm, geom=list({*out["geom_interior"], *out["geom_unused"]}), context="VERTS")
        bmesh.ops.triangulate(bm, faces=bm.faces[:])
        bm.verts.index_update()
        verts = [v.co.copy() for v in bm.verts]
        triangles = [tuple(v.index for v in f.verts) for f in bm.faces]
    finally:
        bm.free()
    centre = sum(verts, Vector()) / len(verts)
    planes, oriented = [], []
    for tri in triangles:
        a, b, c = (verts[i] for i in tri)
        n = (b - a).cross(c - a)
        if n.length == 0.0:
            continue
        n.normalize()
        if n.dot(a - centre) < 0.0:
            n, tri = -n, (tri[0], tri[2], tri[1])
        planes.append((n, n.dot(a)))
        oriented.append(tri)
    return verts, oriented, planes


@dataclass
class Hull:
    """A CONVEX_HULL collision shape as Bullet builds it: a core hull grown by margin_m.

    The core is the hull of the source vertices, or that hull shrunk by the margin when Blender
    embeds it (use_margin off), so the outer faces stay on the hull and only edges round off.
    """

    shape: str
    size: dict
    margin_m: float
    margin_mode: str
    core_vertices: list
    core_triangles: list
    core_planes: list

    def __post_init__(self):
        self._bvh = BVHTree.FromPolygons([v[:] for v in self.core_vertices], self.core_triangles,
                                         all_triangles=True, epsilon=0.0)

    def distance(self, p: Vector) -> float:
        """Signed distance from a world point to the shape surface; negative inside."""
        inside = max(n.dot(p) - d for n, d in self.core_planes)
        core = inside if inside <= 0.0 else self._bvh.find_nearest(p)[3]
        return core - self.margin_m

    def surface_points(self) -> list:
        """Points on the outer faces: dense samples of every core triangle pushed out by the margin."""
        lo = Vector([min(v[i] for v in self.core_vertices) for i in range(3)])
        hi = Vector([max(v[i] for v in self.core_vertices) for i in range(3)])
        spacing = max((hi - lo).length * HULL_SAMPLE_SPACING, 1e-5)
        points = []
        for (i, j, k), (n, _) in zip(self.core_triangles, self.core_planes):
            a, b, c = self.core_vertices[i], self.core_vertices[j], self.core_vertices[k]
            steps = max(1, math.ceil(max((b - a).length, (c - b).length, (a - c).length) / spacing))
            offset = n * self.margin_m
            for u in range(steps + 1):
                for v in range(steps + 1 - u):
                    w = steps - u - v
                    points.append((a * u + b * v + c * w) / steps + offset)
            points.append((a + b + c) / 3.0 + offset)
        return points


def _hull_source(obj, graph) -> list:
    """World-space vertices Blender hands Bullet for obj's hull, following rigid_body.mesh_source."""
    rb = obj.rigid_body
    matrix = obj.evaluated_get(graph).matrix_world
    if rb.mesh_source == "FINAL":
        return world_mesh(obj, graph).vertices
    active = [m for m in obj.modifiers if m.show_viewport]
    if rb.mesh_source == "DEFORM":
        if all(m.type in DEFORM_MODIFIERS for m in active):
            return world_mesh(obj, graph).vertices
        if active[0].type in DEFORM_MODIFIERS or obj.data.shape_keys:
            raise ValueError("mesh_source DEFORM with deform modifiers or shape keys before a constructive "
                             "modifier is not modelled; set mesh_source to BASE or FINAL")
    return [matrix @ v.co for v in obj.data.vertices]


def collision_hull(obj, graph=None) -> Hull:
    """The CONVEX_HULL collision shape Blender builds for obj's rigid body.

    Measured in Blender 5.1.1 with the probe method of collision_primitive, on boxes (centred,
    off-centre, scale (1, 2, 3)), a Subsurf box with each mesh_source, and a torus:
      - the hull is of the mesh_source vertices: BASE and DEFORM ignore a Subsurf modifier
        (the cage), FINAL includes it; it is placed by the object's full transform, not
        recentred, and scaled per axis;
      - use_margin on: collision_margin is added outside the hull (not scaled);
      - use_margin off: Blender sets collision_margin to 0.04 and embeds it: the hull is shrunk
        by 0.04 and the margin added back, so faces stay put and edges round with radius 0.04.
        When 0.04 is not below the smallest distance from the hull centre to a face (a 2 cm box,
        a flat plane) the hull is used exactly, with no margin, though collision_margin reads 0.04;
      - a torus hull fills the hole flush with the tube (probe rests at the tube top, + margin).
    """
    graph = graph or depsgraph()
    rb = obj.rigid_body
    if rb is None or rb.collision_shape != "CONVEX_HULL":
        raise ValueError(f"{obj.name} has no CONVEX_HULL collision shape")
    points = _hull_source(obj, graph)
    if len(points) < 4:
        raise ValueError(f"{obj.name} has fewer than 4 vertices")
    verts, triangles, planes = _convex_hull(points)
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    size = {"hull_vertices": len(verts), "bounds_half_extents": [(h - l) / 2.0 for l, h in zip(lo, hi)],
            "mesh_source": rb.mesh_source}
    if rb.use_margin:
        return Hull("CONVEX_HULL", size, rb.collision_margin, "added outside", verts, triangles, planes)
    centre = sum(verts, Vector()) / len(verts)
    if not planes or DEFAULT_MARGIN_M >= min(d - n.dot(centre) for n, d in planes):
        return Hull("CONVEX_HULL", size, 0.0, "none (too thin to embed)", verts, triangles, planes)
    # Shrink: move every face plane in by the margin and intersect the planes around each vertex.
    m = DEFAULT_MARGIN_M
    shrunk = []
    for v in verts:
        around = [(n, d) for n, d in planes if abs(n.dot(v) - d) < 1e-6]
        a = np.array([n[:] for n, _ in around])
        b = np.array([d - m for _, d in around])
        shrunk.append(Vector(np.linalg.lstsq(a, b, rcond=None)[0]))
    core, core_triangles, core_planes = _convex_hull(shrunk)
    return Hull("CONVEX_HULL", size, m, "rounds edges inside", core, core_triangles, core_planes)


def collision_shape_fit(objects: Iterable | None = None, *, max_protrusion_m: float, max_uncovered_m: float,
                        scene=None, name: str = "collision_shape_fit") -> Check:
    """Every primitive or convex hull collision shape matches its visible mesh within the given distances.

    protrusion: how far the shape surface lies outside the mesh (an invisible air wall), the
    largest distance to the mesh over surface samples outside it. uncovered: how far the mesh
    lies outside the shape (parts that pass through other bodies), over the mesh vertices.
    The inside test uses the nearest face normal, so it assumes a closed, outward-facing mesh.
    A CONVEX_HULL fills every hole and hollow (a ring's hole, a cup's inside, a hook's bend):
    its protrusion is how far that filled space reaches from the mesh. objects defaults to every
    mesh with a rigid body; MESH and COMPOUND bodies, and hulls whose source mesh is not
    modelled, are listed under not_checked. Measure at the first frame, where Bullet builds them.
    """
    if max_protrusion_m < 0 or max_uncovered_m < 0:
        raise ValueError("max_protrusion_m and max_uncovered_m must be >= 0")
    scene = scene or bpy.context.scene
    objects = list(objects) if objects is not None else rigid_bodies(scene)
    graph = depsgraph()
    rows, failing, not_checked = [], [], []
    for obj in objects:
        rb = obj.rigid_body
        shape = rb.collision_shape if rb else None
        if shape not in PRIMITIVE_SHAPES and shape != "CONVEX_HULL":
            not_checked.append({"object": obj.name, "shape": shape})
            continue
        mesh = world_mesh(obj, graph)
        if shape == "CONVEX_HULL":
            try:
                prim = collision_hull(obj, graph)
            except ValueError as e:
                not_checked.append({"object": obj.name, "shape": shape, "reason": str(e)})
                continue
        else:
            prim = collision_primitive(obj, graph, mesh)
        row = {"object": obj.name, "shape": prim.shape, "size_m": prim.size, "margin_m": prim.margin_m,
               "margin_mode": prim.margin_mode}
        problems = []
        if isinstance(prim, Primitive) and prim.margin_mode == "rounds edges inside" \
                and prim.margin_m > min(prim._extent()):
            problems.append(f"collision margin {prim.margin_m} exceeds a half extent, so Bullet's shape "
                            "does not match its bounds")
        else:
            bvh = mesh.bvh()
            protrusion, protrusion_at = 0.0, None
            for p in prim.surface_points():
                hit, normal, _, gap = bvh.find_nearest(p)
                if hit is not None and (p - hit).dot(normal) > 0.0 and gap > protrusion:
                    protrusion, protrusion_at = gap, list(p)
            uncovered, uncovered_at = 0.0, None
            for v in mesh.vertices:
                d = prim.distance(v)
                if d > uncovered:
                    uncovered, uncovered_at = d, list(v)
            row.update(protrusion_m=protrusion, protrusion_at=protrusion_at,
                       uncovered_m=uncovered, uncovered_at=uncovered_at)
            if protrusion > max_protrusion_m:
                problems.append("collision shape sticks out of the visible mesh")
            if uncovered > max_uncovered_m:
                problems.append("visible mesh sticks out of the collision shape")
        rows.append(row)
        if problems:
            failing.append({**row, "problems": problems})
    measured = [r for r in rows if "protrusion_m" in r]
    measurements = {"bodies_checked": len(rows), "max_protrusion_m": max_protrusion_m,
                    "max_uncovered_m": max_uncovered_m,
                    "largest_protrusion": max(measured, key=lambda r: r["protrusion_m"], default=None),
                    "largest_uncovered": max(measured, key=lambda r: r["uncovered_m"], default=None),
                    "bodies": rows[:20], "not_checked": not_checked[:20], "not_checked_count": len(not_checked),
                    "failing_count": len(failing), "failing": failing[:20]}
    if not objects:
        return Check(name, False, measurements, {"problem": "no rigid bodies to check"})
    return Check(name, not failing, measurements, failing[0] if failing else None)
