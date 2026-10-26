"""Scenes for worldagent_measure.coupling (run inside Blender by tests/test_measure_coupling.py).

Args after --: <mode> [<work_dir>]
  links:   link detection and graph_links on small unbaked scenes; a force field moving a rigid body
  bake:    a Mantaflow gas jet with a Fluid Flow field on a Bullet bar and a Cloth patch, baked node
           by node inside coupling.record_bake, saved to <work_dir>/case/scene.blend
  reopen:  the saved scene in a fresh process: bake_evidence from the records, from file times,
           without evidence, and after the driver cache changed
"""
import copy
import json
import math
import os
import shutil
import sys
from pathlib import Path

import bpy

from worldagent_measure import coupling

args = sys.argv[sys.argv.index("--") + 1:]
mode = args[0]
work = Path(args[1]) if len(args) > 1 else None
out = {}


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def cube(name, size, location):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def plane(name, size, location, rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_plane_add(size=size, location=location, rotation=rotation)
    obj = bpy.context.active_object
    obj.name = name
    with bpy.context.temp_override(object=obj, active_object=obj, selected_objects=[obj], selected_editable_objects=[obj]):
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.subdivide(number_cuts=6)
        bpy.ops.object.mode_set(mode="OBJECT")
    return obj


def rigid(obj, kind="ACTIVE", mass=1.0):
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.rigidbody.object_add()
    obj.rigid_body.type = kind
    obj.rigid_body.mass = mass
    return obj


def domain(name, kind, cache=None, resolution=24):
    d = cube(name, 4, (0, 0, 2))
    mod = d.modifiers.new("Fluid", "FLUID")
    mod.fluid_type = "DOMAIN"
    s = mod.domain_settings
    s.domain_type = kind
    s.resolution_max = resolution
    if cache:
        s.cache_directory = cache
    return d


def fluid_role(obj, role):
    mod = obj.modifiers.new("Fluid", "FLUID")
    mod.fluid_type = role
    return mod


def cloth(obj):
    mod = obj.modifiers.new("Cloth", "CLOTH")
    mod.settings.effector_weights.gravity = 0.0
    return mod


def manifest(contracts, nodes, edges):
    return {"object_contracts": [{"name": n, "blender_system": s} for n, s in contracts.items()],
            "topology": {"solver_graph": {"nodes": nodes, "edges": edges, "bake_order": [n["id"] for n in nodes]}}}


def edge(eid, a, b, channel, objects, **extra):
    return {"id": eid, "from": a, "to": b, "channel": channel, "objects": objects, "omitted_reactions": ["x"], **extra}


def summary(check):
    m = check.measurements
    return {"status": "pass" if check.passed else "fail", "first_failure": check.first_failure,
            "links": sorted([l["driver"], l["driven"], l["channel"]] for l in m["links"]),
            "undeclared": [u["problem"] for u in m["undeclared"]], "missing": [x["problem"] for x in m["missing"]],
            "wrong_carrier": [x["problem"] for x in m["wrong_carrier"]], "unchecked": m["unchecked"]}


def without_edges(m):
    m = copy.deepcopy(m)
    m["topology"]["solver_graph"]["edges"] = []
    return m


def evidence(check):
    m = check.measurements
    return {"status": "pass" if check.passed else "fail", "first_failure": check.first_failure,
            "edges": {e["edge"]: {"evidence": e["evidence"], "problem": e.get("problem"), "notes": e["notes"]}
                      for e in m["edges"]},
            "unverified_edges": m["unverified_edges"], "records": m["records"]}


if mode == "links":
    # Mantaflow effector: a Bullet box in a liquid domain.
    reset()
    water = domain("water", "LIQUID")
    box = rigid(cube("box", 0.5, (0, 0, 2)))
    fluid_role(box, "EFFECTOR")
    m = manifest({"water": "mantaflow_liquid", "box": "bullet_rigid_body"},
                 [{"id": "rigid", "objects": ["box"]}, {"id": "water", "objects": ["water"]}],
                 [edge("box_to_water", "rigid", "water", "effector", ["box"])])
    out["effector_declared"] = summary(coupling.graph_links(m))
    out["effector_undeclared"] = summary(coupling.graph_links(without_edges(m)))
    others = bpy.data.collections.new("others")
    bpy.context.scene.collection.children.link(others)
    water.modifiers["Fluid"].domain_settings.effector_group = others
    out["effector_outside_group"] = summary(coupling.graph_links(m))
    water.modifiers["Fluid"].domain_settings.effector_group = None
    box.modifiers.remove(box.modifiers["Fluid"])
    out["effector_missing"] = summary(coupling.graph_links(m))
    static = cube("static_wall", 0.5, (1, 0, 2))
    fluid_role(static, "EFFECTOR")
    out["static_effector_links"] = [l for l in coupling.scene_links() if l["driver"] == "static_wall"]
    out["node_map_rigid"] = coupling.node_map(m)["nodes"]["rigid"]

    # Mantaflow flow source on a Bullet body.
    reset()
    domain("smoke", "GAS")
    emitter = rigid(cube("burning_crate", 0.5, (0, 0, 1)))
    fluid_role(emitter, "FLOW")
    m = manifest({"smoke": "mantaflow_gas", "burning_crate": "bullet_rigid_body"},
                 [{"id": "rigid", "objects": ["burning_crate"]}, {"id": "smoke", "objects": ["smoke"]}],
                 [edge("crate_to_smoke", "rigid", "smoke", "flow_source", ["burning_crate"])])
    out["flow_source_declared"] = summary(coupling.graph_links(m))

    # Collision and hook: a Bullet box under a cloth patch.
    reset()
    patch = plane("patch", 1.0, (0, 0, 1))
    box = rigid(cube("box", 0.4, (0, 0, 0.5)))
    box.modifiers.new("Collision", "COLLISION")
    cloth(patch)
    m = manifest({"patch": "cloth", "box": "bullet_rigid_body"},
                 [{"id": "rigid", "objects": ["box"]}, {"id": "cloth", "objects": ["patch"]}],
                 [edge("box_to_patch", "rigid", "cloth", "collision", ["box"])])
    out["collision_declared"] = summary(coupling.graph_links(m))
    out["collision_undeclared"] = summary(coupling.graph_links(without_edges(m)))
    hook = patch.modifiers.new("Hook", "HOOK")
    hook.object = box
    m["topology"]["solver_graph"]["edges"].append(edge("box_hooks_patch", "rigid", "cloth", "hook", ["box"]))
    out["hook_below"] = summary(coupling.graph_links(m))
    with bpy.context.temp_override(object=patch):
        bpy.ops.object.modifier_move_to_index(modifier="Hook", index=0)
    out["hook_above"] = summary(coupling.graph_links(m))

    # Force fields: a wind field on a Bullet box and a cloth patch.
    reset()
    patch = plane("patch", 1.0, (2, 0, 1))
    cloth(patch)
    box = rigid(cube("box", 0.4, (0, 0, 1)))
    bpy.ops.object.effector_add(type="WIND", location=(-2, 0, 1))
    wind = bpy.context.active_object
    wind.name = "wind"
    m = manifest({"patch": "cloth", "box": "bullet_rigid_body"},
                 [{"id": "wind_input", "input": "force_field", "objects": ["wind"]},
                  {"id": "rigid", "objects": ["box"]}, {"id": "cloth", "objects": ["patch"]}],
                 [edge("wind_on_box", "wind_input", "rigid", "force_field", ["wind"]),
                  edge("wind_on_patch", "wind_input", "cloth", "force_field", ["wind"])])
    out["force_field_declared"] = summary(coupling.graph_links(m))
    bpy.context.scene.rigidbody_world.effector_weights.wind = 0.0
    out["force_field_weight_zero"] = summary(coupling.graph_links(m))

    # A FORCE field pushes a Bullet box without gravity.
    reset()
    box = rigid(cube("box", 0.4, (0, 0, 1)))
    world = bpy.context.scene.rigidbody_world
    world.effector_weights.gravity = 0.0
    world.point_cache.frame_start, world.point_cache.frame_end = 1, 20
    bpy.ops.object.effector_add(type="FORCE", location=(-2, 0, 1))
    bpy.context.active_object.field.strength = 50.0
    start = None
    for f in range(1, 21):
        bpy.context.scene.frame_set(f)
        x = box.evaluated_get(bpy.context.evaluated_depsgraph_get()).matrix_world.translation.x
        start = x if start is None else start
    out["force_field_moved_x_m"] = x - start

    # Keyed motor constraint between two Bullet bodies.
    reset()
    domain("water", "LIQUID")
    a = rigid(cube("raft", 0.5, (0, 0, 1)))
    b = rigid(cube("anchor", 0.5, (0, 0, 0)), kind="PASSIVE")
    bpy.ops.object.empty_add(location=(0, 0, 0.5))
    motor = bpy.context.active_object
    motor.name = "motor_x"
    bpy.ops.rigidbody.constraint_add(type="MOTOR")
    c = motor.rigid_body_constraint
    c.object1, c.object2 = a, b
    c.use_motor_lin = True
    for frame, value in ((1, 0.0), (20, 5.0)):
        c.motor_lin_max_impulse = value
        motor.keyframe_insert('rigid_body_constraint.motor_lin_max_impulse', frame=frame)
    m = manifest({"water": "mantaflow_liquid", "raft": "bullet_rigid_body"},
                 [{"id": "water", "objects": ["water"]}, {"id": "rigid", "objects": ["raft"]}], [])
    out["motor_undeclared"] = summary(coupling.graph_links(m))
    m["topology"]["solver_graph"]["edges"].append(edge(
        "water_pushes_raft", "water", "rigid", "scripted_transfer", ["water"], via=["motor_x"],
        method="src/couple.py: drag", assumptions=["Cd 1"], approximation="drag from the grid"))
    out["motor_scripted"] = summary(coupling.graph_links(m))
    out["motor_link"] = next(l for l in coupling.scene_links() if l["driver"] == "motor_x")

elif mode == "bake":
    reset()
    case = work / "case"
    case.mkdir(parents=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 30
    bpy.ops.wm.save_as_mainfile(filepath=str(case / "scene.blend"))
    gas = domain("gas_domain", "GAS", cache="//cache_gas", resolution=32)
    s = gas.modifiers["Fluid"].domain_settings
    s.cache_type = "ALL"
    s.cache_frame_start, s.cache_frame_end = 1, 30
    src = cube("jet", 0.5, (-1.4, 0, 1.0))
    fs = fluid_role(src, "FLOW").flow_settings
    fs.flow_type, fs.flow_behavior = "SMOKE", "INFLOW"
    fs.use_initial_velocity = True
    fs.velocity_coord = (6, 0, 0)
    # A bar with one end in the jet and one outside: any torque would turn it.
    bar = cube("bar", 1, (0.3, 0.5, 1.0))
    bar.scale = (0.2, 1.2, 0.2)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    rigid(bar)
    world = scene.rigidbody_world
    world.effector_weights.gravity = 0.0
    world.point_cache.frame_start, world.point_cache.frame_end = 1, 30
    patch = plane("patch", 0.5, (0.8, -0.6, 1.0), rotation=(0, math.pi / 2, 0))
    cmod = cloth(patch)
    cmod.point_cache.frame_start, cmod.point_cache.frame_end = 1, 30
    cmod.point_cache.use_disk_cache = True
    bpy.ops.object.effector_add(type="FORCE", location=(0, 0, 2))
    field_obj = bpy.context.active_object
    field_obj.name = "flow_field"
    field_obj.field.type = "FLUID_FLOW"
    field_obj.field.source_object = gas
    field_obj.field.flow = 1.0
    bpy.ops.wm.save_mainfile()

    m = manifest({"gas_domain": "mantaflow_gas", "bar": "bullet_rigid_body", "patch": "cloth"},
                 [{"id": "gas", "objects": ["gas_domain"]}, {"id": "rigid", "objects": ["bar"], "cache": "in .blend"},
                  {"id": "cloth", "objects": ["patch"], "cache": "blendcache_scene"}],
                 [edge("gas_pushes_bar", "gas", "rigid", "field_flow", ["gas_domain"], via=["flow_field"],
                       settings={"field.flow": 1.0}, approximation="grid velocity at the bar's centre"),
                  edge("gas_moves_patch", "gas", "cloth", "field_flow", ["gas_domain"], via=["flow_field"],
                       settings={"field.flow": 1.0}, approximation="grid velocity per vertex")])
    (case / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
    frames = list(range(1, 31))
    with coupling.record_bake(case, "c01", m, "gas", frames=frames):
        with bpy.context.temp_override(object=gas, active_object=gas):
            bpy.ops.fluid.bake_all()
    with coupling.record_bake(case, "c01", m, "rigid", frames=frames), \
            coupling.record_bake(case, "c01", m, "cloth", frames=frames):
        bpy.ops.ptcache.bake_all(bake=True)
    bpy.ops.wm.save_mainfile()

    poses, centres = [], []
    for f in (1, 30):
        scene.frame_set(f)
        graph = bpy.context.evaluated_depsgraph_get()
        mw = bar.evaluated_get(graph).matrix_world
        poses.append((list(mw.translation), list(mw.to_euler())))
        ev = patch.evaluated_get(graph)
        mesh = ev.to_mesh()
        centres.append(sum((ev.matrix_world @ v.co).x for v in mesh.vertices) / len(mesh.vertices))
        ev.to_mesh_clear()
    out["bar_moved_m"] = math.dist(poses[0][0], poses[1][0])
    out["bar_rotation_change_rad"] = max(abs(a - b) for a, b in zip(poses[0][1], poses[1][1]))
    out["patch_mean_x_moved_m"] = centres[1] - centres[0]
    out["records"] = json.loads((case / "reports" / "bakes" / "c01.json").read_text(encoding="utf-8"))
    out["field_flow_declared"] = summary(coupling.graph_links(m))
    out["field_flow_undeclared"] = summary(coupling.graph_links(without_edges(m)))
    wrong = copy.deepcopy(m)
    wrong["topology"]["solver_graph"]["edges"][0]["settings"] = {"field.flow": 2.0}
    out["field_flow_strength_differs"] = summary(coupling.graph_links(wrong))
    other = domain("other_domain", "GAS")
    field_obj.field.source_object = other
    out["field_flow_wrong_source"] = summary(coupling.graph_links(m))
    field_obj.field.source_object = gas
    bpy.data.objects.remove(other, do_unlink=True)
    out["evidence_same_process"] = evidence(coupling.bake_evidence(
        m, case, "c01", frames=frames, max_export_deviation_m=0.01, unverified="fail"))
    out["gas_digest"] = coupling.node_digest(coupling.node_map(m)["nodes"]["gas"], case, frames)
    bpy.ops.wm.save_mainfile()

elif mode == "reopen":
    case = work / "case"
    m = json.loads((case / "manifest.json").read_text(encoding="utf-8"))
    frames = list(range(1, 31))
    check = lambda unverified="fail": evidence(coupling.bake_evidence(  # noqa: E731
        m, case, "c01", frames=frames, max_export_deviation_m=0.01, unverified=unverified))
    gas_node = coupling.node_map(m)["nodes"]["gas"]
    out["gas_digest"] = coupling.node_digest(gas_node, case, frames)
    out["from_records"] = check()
    records = case / "reports" / "bakes" / "c01.json"
    kept = records.with_name("kept.json")
    shutil.move(records, kept)
    out["no_records_fail"] = check("fail")
    out["no_records_report"] = check("report")
    gas_files = sorted(p for p in (case / "cache_gas").rglob("*") if p.is_file())
    stamp = max(p.stat().st_mtime for p in (case / "blendcache_scene").rglob("*") if p.is_file()) + 100
    times = {p: p.stat().st_mtime for p in gas_files}
    for p in gas_files:
        os.utime(p, (stamp, stamp))
    out["gas_newer_than_cloth"] = check("report")
    for p, t in times.items():
        os.utime(p, (t, t))
    shutil.move(kept, records)
    data = next(p for p in gas_files if "data" in p.parts and p.stat().st_size > 16)
    raw = bytearray(data.read_bytes())
    raw[8] ^= 0xFF
    data.write_bytes(bytes(raw))
    out["gas_digest_after_change"] = coupling.node_digest(gas_node, case, frames)
    out["driver_changed"] = check()

print("WORLDAGENT_RESULT " + json.dumps(out, default=str))
