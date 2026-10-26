"""Scenes for the Soft Body helpers of worldagent_measure.deformable; prints one WORLDAGENT_RESULT line."""
import json
import math

import bpy

from worldagent_measure import verifier
from worldagent_measure.deformable import free_fall, soft_body_setup, transfer_deviation

REAL_TIME_SPEED_24 = math.sqrt(1000) / 24  # measured 5.1.1 clock: speed x 0.0316228 s per frame
scene = bpy.context.scene


def reset(fps=24, fps_base=1.0):
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    for tree in list(bpy.data.node_groups):
        bpy.data.node_groups.remove(tree)
    for col in list(bpy.data.collections):
        bpy.data.collections.remove(col)
    scene.render.fps, scene.render.fps_base = fps, fps_base
    scene.gravity = (0, 0, -9.81)
    scene.use_gravity = True
    scene.frame_set(1)


def cube(name, size=0.2, cuts=0, location=(0, 0, 1)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    if cuts:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.subdivide(number_cuts=cuts)
        bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def add_soft_body(obj, speed=REAL_TIME_SPEED_24, frame_end=12):
    mod = obj.modifiers.new("SoftBody", "SOFT_BODY")
    s = mod.settings
    s.use_goal = False
    s.friction = 0.0
    s.speed = speed
    s.pull, s.push, s.damping, s.bend = 0.6, 0.55, 0.4, 0.3
    s.step_min, s.step_max = 12, 250
    s.use_self_collision = True
    s.ball_size = 0.3
    mod.point_cache.frame_start, mod.point_cache.frame_end = 1, frame_end
    return mod


def jelly(name="jelly"):
    """56-vertex cube of side 0.2 at scale 2 (world edge 0.4 / 3); Subdivisions end up after Soft Body."""
    obj = cube(name, cuts=2)
    obj.scale = (2, 2, 2)
    solver = obj.modifiers.new("SolverSubdivision", "SUBSURF")
    solver.subdivision_type = "SIMPLE"
    solver.levels = solver.render_levels = 1
    add_soft_body(obj)
    render = obj.modifiers.new("RenderSubdivision", "SUBSURF")
    render.levels, render.render_levels = 1, 2
    bpy.context.view_layer.update()
    return obj


def plane(name, collision=True, thickness=0.001):
    bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    if collision:
        obj.modifiers.new("Collision", "COLLISION")
        obj.collision.thickness_outer = thickness
    else:
        bpy.ops.rigidbody.object_add(type="PASSIVE")
    return obj


def sphere(name, radius=0.08, location=(0, 0, 1)):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=radius, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def bind(obj, mod):
    op = bpy.ops.object.surfacedeform_bind if mod.type == "SURFACE_DEFORM" else bpy.ops.object.meshdeform_bind
    with bpy.context.temp_override(object=obj, active_object=obj):
        op(modifier=mod.name)


def bake(obj):
    cache = obj.modifiers["SoftBody"].point_cache
    with bpy.context.temp_override(point_cache=cache, object=obj, active_object=obj):
        bpy.ops.ptcache.bake(bake=True)


def r(check):
    return verifier.result([check])


def setup(obj, **kw):
    kw.setdefault("free_fall", True)
    kw.setdefault("frame_start", 1)
    kw.setdefault("frame_end", 12)
    kw.setdefault("baked", False)
    return r(soft_body_setup(obj, **kw))


def stack(obj):
    return [[m.name, m.show_viewport, m.show_render, getattr(m, "levels", None)] for m in obj.modifiers]


def error_name(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return type(exc).__name__
    return None


out = {}

# 1-3: good setup, original untouched, clock
reset()
body = jelly()
plate = plane("plate")
before, objects_before = stack(body), sorted(o.name for o in bpy.data.objects)
out["good"] = setup(body, supports=[plate])
out["stack_unchanged"] = before == stack(body)
out["objects_unchanged"] = objects_before == sorted(o.name for o in bpy.data.objects)
body.modifiers["SoftBody"].settings.speed = 1.0
out["speed_one"] = setup(body, supports=[plate])

# 2 / 13: bake, then the setup check leaves the cache baked and current
reset()
body = jelly()
plate = plane("plate")
out["unbaked_required"] = setup(body, supports=[plate], baked=True)
out["short_cache"] = setup(body, supports=[plate], frame_end=24)
bake(body)
out["baked"] = setup(body, supports=[plate], baked=True)
cache = body.modifiers["SoftBody"].point_cache
out["baked_after"] = {"is_baked": cache.is_baked, "is_outdated": cache.is_outdated}

# 4: no Soft Body
reset()
out["no_soft_body"] = setup(cube("plain"))

# 5: another solver
reset()
body = jelly()
body.modifiers.new("Cloth", "CLOTH")
out["with_cloth"] = setup(body)
reset()
body = jelly()
with bpy.context.temp_override(object=body, active_object=body, selected_objects=[body]):
    bpy.ops.rigidbody.object_add(type="ACTIVE")
out["with_rigid"] = setup(body)
reset()
body = jelly()
body.modifiers.new("Collision", "COLLISION")
out["with_collision"] = setup(body)

# 6: Goal rule (all free_fall=True); the plain 8-vertex cube, Goal settings per sub-case
goal_cases = {}


def goal_case(tag, fn):
    reset()
    obj = cube("g")
    mod = add_soft_body(obj)
    mod.settings.use_goal = True
    fn(obj, mod.settings)
    res = setup(obj)
    goal_cases[tag] = {"status": res["status"], "pinned": res["measurements"]["soft_body_setup"]["goal"]["pinned_vertices"],
                       "problem": (res.get("first_failure") or {}).get("problem")}


def new_defaults(o, s):
    o.modifiers.remove(o.modifiers["SoftBody"])
    o.modifiers.new("SoftBody", "SOFT_BODY")


def all_w0_min0(o, s):
    g = o.vertex_groups.new(name="g")
    g.add(list(range(8)), 0.0, "REPLACE")
    s.vertex_group_goal, s.goal_min = "g", 0.0


def none_assigned(o, s):
    o.vertex_groups.new(name="g")
    s.vertex_group_goal = "g"


def two_pinned(o, s):
    g = o.vertex_groups.new(name="g")
    g.add([0, 1], 1.0, "REPLACE")
    s.vertex_group_goal = "g"


def all_w1_max0(o, s):
    g = o.vertex_groups.new(name="g")
    g.add(list(range(8)), 1.0, "REPLACE")
    s.vertex_group_goal, s.goal_max = "g", 0.0


def one_w0_min05(o, s):
    g = o.vertex_groups.new(name="g")
    g.add([0], 0.0, "REPLACE")
    s.vertex_group_goal, s.goal_min = "g", 0.5


def default0_min05(o, s):
    s.goal_default, s.goal_min = 0.0, 0.5


goal_case("new_defaults", new_defaults)
goal_case("all_w0_min0", all_w0_min0)
goal_case("none_assigned", none_assigned)
goal_case("missing_group", lambda o, s: setattr(s, "vertex_group_goal", "nope"))
goal_case("two_pinned", two_pinned)
goal_case("default0", lambda o, s: setattr(s, "goal_default", 0.0))
goal_case("all_w1_max0", all_w1_max0)
goal_case("one_w0_min05", one_w0_min05)
goal_case("default0_min05", default0_min05)
out["goal_cases"] = goal_cases

# 7: anchored body may keep Goal
reset()
obj = cube("anchored")
mod = add_soft_body(obj)
mod.settings.use_goal = True
out["anchored"] = setup(obj, free_fall=False)

# 8: stack rules. Soft Body requires original data, so Blender keeps it above every non-deform
# modifier (modifiers.new puts it before the Subdivisions; moving one above it is refused); only
# deform modifiers can come before it.
stack_cases = {}
for tag in ("deform_ok", "visibility", "subsurf_above"):
    reset()
    body = jelly()
    if tag == "subsurf_above":
        try:
            body.modifiers.move(body.modifiers.find("SolverSubdivision"), 0)
            stack_cases["subsurf_move_refused"] = False
        except RuntimeError:
            stack_cases["subsurf_move_refused"] = True
    else:
        disp = body.modifiers.new("Displace", "DISPLACE")
        disp.strength = 0.0
        body.modifiers.move(body.modifiers.find("Displace"), 0)
        disp.show_render = tag != "visibility"
    bpy.context.view_layer.update()
    res = setup(body)
    stack_cases[tag] = {"status": res["status"], "first_failure": res.get("first_failure"),
                        "before": res["measurements"]["soft_body_setup"].get("before_soft_body")}
out["stack_cases"] = stack_cases

# 9: open simulation mesh
reset()
bpy.ops.mesh.primitive_grid_add(x_subdivisions=8, y_subdivisions=8, size=1, location=(0, 0, 1))
sheet = bpy.context.active_object
sheet.name = "sheet"
add_soft_body(sheet)
viewer = sheet.copy()
viewer.name = "sheet_view"
viewer.modifiers.clear()
scene.collection.objects.link(viewer)
sd = viewer.modifiers.new("SD", "SURFACE_DEFORM")
sd.target = sheet
bind(viewer, sd)
out["open_alone"] = setup(sheet)
out["open_with_visible"] = setup(sheet, visible=[viewer])

# 10: transfer detection
reset()
body = jelly()
bound = body.copy()
bound.name = "sd_bound"
bound.modifiers.clear()
scene.collection.objects.link(bound)
sd = bound.modifiers.new("SD", "SURFACE_DEFORM")
sd.target = body
bind(bound, sd)
out["sd_bound"] = setup(body, visible=[bound])
loose = sphere("sd_unbound")
loose.modifiers.new("SD", "SURFACE_DEFORM").target = body
out["sd_unbound"] = setup(body, visible=[loose])
alone = sphere("not_driven")
out["not_driven"] = setup(body, visible=[alone])
gn_obj = sphere("gn_visible")
inner = bpy.data.node_groups.new("Inner", "GeometryNodeTree")
info = inner.nodes.new("GeometryNodeObjectInfo")
info.inputs["Object"].default_value = body
outer = bpy.data.node_groups.new("Outer", "GeometryNodeTree")
outer.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
outer.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
group_node = outer.nodes.new("GeometryNodeGroup")
group_node.node_tree = inner
gn_input, gn_output = outer.nodes.new("NodeGroupInput"), outer.nodes.new("NodeGroupOutput")
outer.links.new(gn_input.outputs[0], gn_output.inputs[0])
gn_obj.modifiers.new("GN", "NODES").node_group = outer
out["gn_nested"] = setup(body, visible=[gn_obj])
outer2 = bpy.data.node_groups.new("ByInput", "GeometryNodeTree")
outer2.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
outer2.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
target = outer2.interface.new_socket("Target", in_out="INPUT", socket_type="NodeSocketObject")
gn_obj2 = sphere("gn_input")
outer2.links.new(outer2.nodes.new("NodeGroupInput").outputs[0], outer2.nodes.new("NodeGroupOutput").inputs[0])
gn2 = gn_obj2.modifiers.new("GN", "NODES")
gn2.node_group = outer2
gn2[target.identifier] = body
out["gn_input"] = setup(body, visible=[gn_obj2])
col = bpy.data.collections.new("SimCol")
col.objects.link(body)
outer3 = bpy.data.node_groups.new("ByCollection", "GeometryNodeTree")
outer3.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
collection_info = outer3.nodes.new("GeometryNodeCollectionInfo")
collection_info.inputs["Collection"].default_value = col
outer3.links.new(collection_info.outputs[0], outer3.nodes.new("NodeGroupOutput").inputs[0])
gn_obj3 = sphere("gn_collection")
gn_obj3.modifiers.new("GN", "NODES").node_group = outer3
out["gn_collection"] = setup(body, visible=[gn_obj3])

# 11: Mesh Deform cage at rest
reset()
cage = cube("cage")
add_soft_body(cage)
inside = sphere("caged")
md = inside.modifiers.new("MD", "MESH_DEFORM")
md.object = cage
md.precision = 2
bind(inside, md)
bpy.context.view_layer.update()
graph = bpy.context.evaluated_depsgraph_get()
ev = inside.evaluated_get(graph)
mesh = ev.to_mesh()
local = [ev.matrix_world @ v.co for v in mesh.vertices]
ev.to_mesh_clear()
out["cage_expected_max_m"] = max(0.1 - max(abs(p.x), abs(p.y), abs(p.z - 1)) for p in local)
out["cage"] = setup(cage, visible=[inside])
out["cage_deviation"] = r(transfer_deviation(inside, cage, [1, 2, 3, 4], max_distance_m=0.05))

# 12: supports
reset()
body = jelly()
rigid_only = plane("rigid_only", collision=False)
out["support_rigid_only"] = setup(body, supports=[rigid_only])
reset()
body = jelly()
plate = plane("plate")
plate.collision.use = False
out["support_disabled"] = setup(body, supports=[plate])
reset()
body = jelly()
plate = plane("plate")
other = plane("other", thickness=0.004)
group = bpy.data.collections.new("Colliders")
group.objects.link(other)
body.modifiers["SoftBody"].settings.collision_collection = group
out["support_outside"] = setup(body, supports=[plate, other])

# 14: keyed fall (exact)
keyed = {}
for fps, base, scale_g, slow in ((24, 1.0, 1.0, 1.0), (48, 2.0, 1.0, 1.0), (24, 1.0, 0.5, 1.0),
                                 (24, 1.0, 0.5, math.sqrt(2))):
    reset(fps, base)
    obj = cube("keyed")
    for f in range(1, 11):
        t = (f - 1) * base / fps
        obj.location.z = 5.0 - 0.5 * 9.81 * scale_g * t * t
        obj.keyframe_insert("location", index=2, frame=f)
    res = r(free_fall(obj, list(range(1, 11)), max_acceleration_error_rel=1e-3, max_initial_speed_m_s=1e-3,
                      slow_motion=slow))
    keyed[f"{fps}_{base}_{scale_g}_{round(slow, 3)}"] = res
out["keyed"] = keyed

# 15-16: Soft Body free fall, relational and the documented constant
sb_fall = {}
for tag, fps, base, speed in (("speed1_24", 24, 1.0, 1.0), ("speed05_24", 24, 1.0, 0.5), ("speed1_48", 48, 1.0, 1.0),
                              ("real_24", 24, 1.0, math.sqrt(1000) / 24), ("real_48", 48, 1.0, math.sqrt(1000) / 48),
                              ("real_60", 60, 1.0, math.sqrt(1000) / 60)):
    reset(fps, base)
    obj = cube("sb", cuts=2)
    add_soft_body(obj, speed=speed, frame_end=20)
    sb_fall[tag] = r(free_fall(obj, list(range(1, 9)), max_acceleration_error_rel=1.0, max_initial_speed_m_s=1.0))
out["sb_fall"] = sb_fall

# 17: rigid body
reset()
obj = cube("rigid")
with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj]):
    bpy.ops.rigidbody.object_add(type="ACTIVE")
scene.rigidbody_world.point_cache.frame_end = 20
out["rigid_fall"] = r(free_fall(obj, list(range(1, 11)), max_acceleration_error_rel=0.01, max_initial_speed_m_s=0.05))

# 18: unbaked body, frames requested from the middle
stepping = {}
for tag, frames in (("full", list(range(1, 11))), ("late", list(range(5, 11)))):
    reset()
    obj = cube("sb", cuts=2)
    add_soft_body(obj, frame_end=20)
    scene.frame_set(1)
    stepping[tag] = r(free_fall(obj, frames, max_acceleration_error_rel=1.0, max_initial_speed_m_s=10.0))
out["stepping"] = stepping

# 19: transfer deviation against a static closed target
reset()
target = cube("target")
copy = target.copy()
copy.name = "follower"
scene.collection.objects.link(copy)
for f, dz in ((1, 0.0), (2, 0.0), (3, 0.003)):
    copy.location.z = 1.0 + dz
    copy.keyframe_insert("location", index=2, frame=f)
out["offset_pass"] = r(transfer_deviation(copy, target, [1, 2, 3], max_distance_m=0.005))
out["offset_fail"] = r(transfer_deviation(copy, target, [1, 2, 3], max_distance_m=0.002))

# 20: argument errors
reset()
body = jelly()
target = cube("t2", location=(1, 0, 1))
empty = bpy.data.objects.new("empty", None)
scene.collection.objects.link(empty)
errors = {
    "ff_few_frames": error_name(lambda: free_fall(body, [1, 2, 3], max_acceleration_error_rel=0.1,
                                                  max_initial_speed_m_s=0.1)),
    "ff_gap": error_name(lambda: free_fall(body, [1, 2, 3, 5], max_acceleration_error_rel=0.1,
                                           max_initial_speed_m_s=0.1)),
    "ff_negative": error_name(lambda: free_fall(body, [1, 2, 3, 4], max_acceleration_error_rel=-0.1,
                                                max_initial_speed_m_s=0.1)),
    "ff_slow_motion_zero": error_name(lambda: free_fall(body, [1, 2, 3, 4], max_acceleration_error_rel=0.1,
                                                        max_initial_speed_m_s=0.1, slow_motion=0)),
    "ff_no_threshold": error_name(lambda: free_fall(body, [1, 2, 3, 4])),
    "td_non_increasing": error_name(lambda: transfer_deviation(target, body, [2, 1], max_distance_m=0.1)),
    "td_negative": error_name(lambda: transfer_deviation(target, body, [1], max_distance_m=-1)),
    "td_same": error_name(lambda: transfer_deviation(body, body, [1], max_distance_m=0.1)),
    "setup_non_mesh": error_name(lambda: soft_body_setup(empty, free_fall=True, frame_start=1, frame_end=2,
                                                         baked=False)),
    "setup_bad_range": error_name(lambda: soft_body_setup(body, free_fall=True, frame_start=5, frame_end=2,
                                                          baked=False)),
}
scene.use_gravity = False
errors["ff_gravity_off"] = error_name(lambda: free_fall(body, [1, 2, 3, 4], max_acceleration_error_rel=0.1,
                                                        max_initial_speed_m_s=0.1))
out["errors"] = errors

print("WORLDAGENT_RESULT " + json.dumps(out, default=str))
