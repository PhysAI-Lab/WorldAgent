"""Blender-side cases for gas.gas_setup, gas.cache_coverage and gas.boundary_clearance (run by
tests/test_measure_gas_setup.py).

Fixture: a 2 m cube GAS domain at (0, 0, 1), resolution_max 16 (voxel 0.125 m), cache ALL for
frames 1-12 in <tmp>/cache/c01 (cache_root <tmp>/cache); a UV sphere SMOKE INFLOW Flow "Flow" of
radius 0.2 at (0, 0, 0.4); a COLLISION Effector "lid" (1.6 x 1.6 x 0.1 m box at z 1.6).
"""
import hashlib
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy
from mathutils import Euler, Matrix, Vector

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import gas, verifier  # noqa: E402
from worldagent_measure.geometry import frame_list  # noqa: E402

TMP = tempfile.mkdtemp(prefix="gas_setup_")


def reset(end=12):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, end


def as_dict(check):
    return verifier.result([check])


def raises(fn):
    try:
        fn()
    except Exception as exc:  # noqa: BLE001 - the test records which error it was
        return type(exc).__name__
    return None


_count = [0]


def cache_dir():
    _count[0] += 1
    return os.path.join(TMP, f"case{_count[0]}", "cache", "c01")


def make_domain(cache, end=12, scale=(1, 1, 1), location=(0, 0, 1), rotation=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=2, location=location, rotation=rotation)
    domain = bpy.context.object
    domain.name = "Domain"
    domain.scale = scale
    mod = domain.modifiers.new("Fluid", "FLUID")
    mod.fluid_type = "DOMAIN"
    s = mod.domain_settings
    s.domain_type = "GAS"
    s.resolution_max = 16
    s.cache_directory = cache
    s.cache_type = "ALL"
    s.cache_frame_end = end
    return domain


def make_flow(name="Flow", location=(0, 0, 0.4), radius=0.2, up_velocity=None):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=location)
    obj = bpy.context.object
    obj.name = name
    mod = obj.modifiers.new("Fluid", "FLUID")
    mod.fluid_type = "FLOW"
    fs = mod.flow_settings
    fs.flow_type = "SMOKE"
    fs.flow_behavior = "INFLOW"
    if up_velocity is not None:
        fs.use_initial_velocity = True
        fs.velocity_coord = (0, 0, up_velocity)
    return obj


def make_effector(name="lid", location=(0, 0, 1.6), size=(1.6, 1.6, 0.1)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = size
    mod = obj.modifiers.new("Fluid", "FLUID")
    mod.fluid_type = "EFFECTOR"
    return obj


def setup(end=12, **domain_kw):
    reset(end)
    cache = cache_dir()
    domain = make_domain(cache, end=end, **domain_kw)
    flow = make_flow()
    lid = make_effector()
    bpy.context.view_layer.update()
    return domain, flow, lid, cache


def root_of(cache):
    return os.path.dirname(cache)


def call(domain, cache, **kw):
    args = dict(flows={"Flow": None}, obstacles=[bpy.data.objects["lid"]] if "lid" in bpy.data.objects else [],
                cache_root=root_of(cache), expected_time_scale=1.0, min_source_voxels=1.0,
                max_flow_step_voxels=None, min_flow_clearance_voxels=None)
    args.update(kw)
    frames = args.pop("frames", (1, 12))
    return gas.gas_setup(domain, *frames, **args)


def run(**kw):
    domain, flow, lid, cache = setup()
    return as_dict(call(domain, cache, **kw))


def flow_mod(obj):
    return obj.modifiers["Fluid"].flow_settings


def eff_mod(obj):
    return obj.modifiers["Fluid"].effector_settings


def key_bool(owner, prop, keys):
    for frame, value in keys:
        setattr(owner, prop, value)
        owner.id_data.keyframe_insert(data_path=owner.path_from_id(prop), frame=frame)


def bake(domain, op="bake_all"):
    with bpy.context.temp_override(object=domain, active_object=domain, scene=bpy.context.scene):
        return list(getattr(bpy.ops.fluid, op)())


def files(path):
    return sorted(os.path.relpath(os.path.join(r, f), path) for r, _, fs in os.walk(path) for f in fs) \
        if os.path.isdir(path) else []


def tree_hash(path):
    digest = hashlib.sha256()
    for rel in files(path):
        digest.update(rel.encode())
        digest.update(Path(path, rel).read_bytes())
    return digest.hexdigest()


def setup_cases(out):
    out["good"] = run()
    domain, flow, lid, cache = setup(scale=(2, 1, 1))
    out["scaled_domain"] = as_dict(call(domain, cache))

    domain, flow, lid, cache = setup()
    bpy.context.scene.render.fps = 48
    out["fps48"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    domain.modifiers["Fluid"].domain_settings.time_scale = 0.5
    out["time_scale_half"] = as_dict(call(domain, cache))
    out["time_scale_half_spec"] = as_dict(call(domain, cache, expected_time_scale=0.5))

    domain, flow, lid, cache = setup()
    flow.location.z = -2
    out["flow_outside"] = as_dict(call(domain, cache))
    flow.location.z = -0.1
    out["flow_partly_outside"] = as_dict(call(domain, cache))
    out["flow_partly_outside_clear"] = as_dict(call(domain, cache, min_flow_clearance_voxels=1))

    domain, flow, lid, cache = setup()
    domain.modifiers["Fluid"].domain_settings.fluid_group = bpy.data.collections.new("flows")
    out["flow_not_in_group"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    flow_mod(flow).flow_type = "LIQUID"
    out["liquid_flow"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    flow_mod(flow).surface_distance = 0
    flow_mod(flow).volume_density = 0
    out["no_emission"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    flow_mod(flow).density = 0
    out["zero_density"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    flow.modifiers["Fluid"].show_viewport = False
    out["flow_modifier_off"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    flow_mod(flow).use_inflow = False
    out["inflow_never"] = as_dict(call(domain, cache))

    domain, flow, lid, cache = setup()
    key_bool(flow_mod(flow), "use_inflow", [(1, True), (6, True), (8, True), (9, False)])
    out["pre_event"] = as_dict(call(domain, cache, flows={"Flow": 6}))
    domain, flow, lid, cache = setup()
    key_bool(flow_mod(flow), "use_inflow", [(1, False), (6, True)])
    out["keyed_ok"] = as_dict(call(domain, cache, flows={"Flow": 6}))
    domain, flow, lid, cache = setup()
    flow_mod(flow).flow_behavior = "GEOMETRY"
    out["geometry_late"] = as_dict(call(domain, cache, flows={"Flow": 6}))
    key_bool(flow_mod(flow), "use_inflow", [(1, False), (6, True)])
    out["geometry_keyed_on6"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    out["not_a_flow"] = as_dict(call(domain, cache, flows={"lid": 3}))
    out["no_flow_named"] = as_dict(call(domain, cache, flows={}))
    out["all_flows"] = as_dict(call(domain, cache, flows=None))

    # declared obstacle problems
    domain, flow, lid, cache = setup()
    lid.modifiers.remove(lid.modifiers["Fluid"])
    out["obstacle_missing_effector"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    lid.modifiers["Fluid"].show_viewport = False
    out["effector_modifier_off"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    eff_mod(lid).use_effector = False
    out["effector_disabled"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    eff_mod(lid).effector_type = "GUIDE"
    out["effector_guide"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    domain.modifiers["Fluid"].domain_settings.effector_group = bpy.data.collections.new("effectors")
    out["effector_outside_group"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    lid.location.z = 5
    out["obstacle_outside_domain"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    key_bool(eff_mod(lid), "use_effector", [(1, True), (7, False)])
    out["effector_off_midway"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    lid.hide_render = True
    out["obstacle_hidden"] = as_dict(call(domain, cache))

    domain, flow, lid, cache = setup()
    proxy = make_effector("proxy", location=(0, 0, 1.4))
    proxy.hide_render = True
    out["hidden_undeclared_effector"] = as_dict(call(domain, cache))
    out["proxy_ok"] = as_dict(call(domain, cache, proxies={"proxy": "lid"}))
    out["proxy_for_undeclared"] = as_dict(call(domain, cache, proxies={"proxy": "wall"}))
    domain, flow, lid, cache = setup()
    make_effector("wall", location=(0.9, 0, 1), size=(0.1, 1.6, 1.6))
    out["visible_undeclared_effector"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    eff_mod(lid).surface_distance = 2
    out["effector_thickened"] = as_dict(call(domain, cache))

    # cache
    domain, flow, lid, cache = setup()
    out["cache_outside_root"] = as_dict(call(domain, cache, cache_root=os.path.join(TMP, "elsewhere")))
    out["cache_root_none"] = as_dict(call(domain, cache, cache_root=None))
    domain.modifiers["Fluid"].domain_settings.cache_frame_end = 6
    out["cache_range_short"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    domain.modifiers["Fluid"].domain_settings.cache_type = "REPLAY"
    out["cache_replay"] = as_dict(call(domain, cache))
    domain, flow, lid, cache = setup()
    parent = os.path.dirname(cache)
    os.makedirs(parent, exist_ok=True)
    os.chmod(parent, 0o500)
    try:
        out["cache_not_writable"] = as_dict(call(domain, cache))
    finally:
        os.chmod(parent, 0o700)

    # motion
    domain, flow, lid, cache = setup()
    for frame, x in ((1, -0.75), (4, 0.75)):
        flow.location.x = x
        flow.keyframe_insert("location", index=0, frame=frame)
    for fc in flow.animation_data.action.layers[0].strips[0].channelbags[0].fcurves:
        for point in fc.keyframe_points:
            point.interpolation = "LINEAR"
    out["moving_flow"] = as_dict(call(domain, cache, max_flow_step_voxels=1))
    flow_mod(flow).subframes = 3
    out["moving_flow_subframes"] = as_dict(call(domain, cache, max_flow_step_voxels=1))

    # thresholds
    domain, flow, lid, cache = setup()
    out["source_4"] = as_dict(call(domain, cache, min_source_voxels=4))
    out["source_3"] = as_dict(call(domain, cache, min_source_voxels=3))
    out["feature_3"] = as_dict(call(domain, cache, features_m={"root_width": 0.3}, min_feature_voxels=3))
    out["feature_2"] = as_dict(call(domain, cache, features_m={"root_width": 0.3}, min_feature_voxels=2))
    out["feature_no_min"] = raises(lambda: call(domain, cache, features_m={"root_width": 0.3}))
    out["no_min_source"] = raises(lambda: gas.gas_setup(domain, 1, 12, cache_root=None, expected_time_scale=1.0,
                                                        max_flow_step_voxels=None, min_flow_clearance_voxels=None))
    out["not_gas"] = raises(lambda: call(lid, cache))

    # adaptive domain is reported only
    domain, flow, lid, cache = setup()
    domain.modifiers["Fluid"].domain_settings.use_adaptive_domain = True
    out["adaptive_reported"] = as_dict(call(domain, cache))


def untouched_cases(out):
    # a REPLAY domain saved and reopened, frame 5: gas_setup must not simulate or write a cache
    domain, flow, lid, cache = setup()
    domain.modifiers["Fluid"].domain_settings.cache_type = "REPLAY"
    path = os.path.join(TMP, "replay.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    shutil.rmtree(cache, ignore_errors=True)
    bpy.ops.wm.open_mainfile(filepath=path)
    bpy.context.scene.frame_set(5)
    shutil.rmtree(cache, ignore_errors=True)  # whatever opening the file wrote
    domain = bpy.data.objects["Domain"]
    before = files(cache)
    result = as_dict(call(domain, cache))
    bpy.context.view_layer.update()
    mod = domain.modifiers["Fluid"]
    out["replay_untouched"] = {"result": result, "files_before": before, "files_after": files(cache),
                               "show": [mod.show_viewport, mod.show_render],
                               "frame": bpy.context.scene.frame_current}

    domain, flow, lid, cache = setup()
    out["baked_bake"] = bake(domain)
    before = tree_hash(cache)
    result = as_dict(call(domain, cache))
    out["baked_untouched"] = {"result": result, "same_hash": before == tree_hash(cache),
                              "baked": domain.modifiers["Fluid"].domain_settings.has_cache_baked_data}


def rebase_cases(out):
    # the runner's record of a // path: <tmp>/.work/candidates/c01/scene.blend with //../../cache/c01
    import worldagent_runtime

    case = os.path.join(TMP, "rebase_case")
    candidate = os.path.join(case, ".work", "candidates", "c01")
    os.makedirs(candidate)
    domain, flow, lid, _ = setup()
    domain.modifiers["Fluid"].domain_settings.cache_directory = "//../../cache/c01"
    bpy.ops.wm.save_as_mainfile(filepath=os.path.join(candidate, "scene.blend"))
    copy = os.path.join(TMP, "runner_copy")
    os.makedirs(copy)
    shutil.copy(os.path.join(candidate, "scene.blend"), os.path.join(copy, "scene.blend"))
    bpy.ops.wm.open_mainfile(filepath=os.path.join(copy, "scene.blend"))
    worldagent_runtime.rebase_paths(candidate)
    domain = bpy.data.objects["Domain"]
    out["rebased"] = as_dict(gas.gas_setup(domain, 1, 12, flows={"Flow": None}, obstacles=["lid"],
                                           cache_root=os.path.join(case, ".work", "cache"), expected_time_scale=1.0,
                                           min_source_voxels=1.0, max_flow_step_voxels=None,
                                           min_flow_clearance_voxels=None))
    out["rebased_cache_dir"] = os.path.normpath(os.path.join(case, ".work", "cache", "c01"))


def coverage_cases(out):
    domain, flow, lid, cache = setup()
    out["coverage_unbaked"] = as_dict(gas.cache_coverage(domain, 1, 12))
    out["coverage_bake"] = bake(domain)
    out["coverage_baked"] = as_dict(gas.cache_coverage(domain, 1, 12))
    out["coverage_short"] = as_dict(gas.cache_coverage(domain, 1, 14))
    os.remove(os.path.join(cache, "data", "fluid_data_0007.vdb"))
    out["coverage_file_missing"] = as_dict(gas.cache_coverage(domain, 1, 12))
    domain.modifiers["Fluid"].domain_settings.resolution_max = 20
    bpy.context.view_layer.update()
    out["coverage_changed"] = as_dict(gas.cache_coverage(domain, 1, 12))

    domain, flow, lid, cache = setup(end=6)
    s = domain.modifiers["Fluid"].domain_settings
    s.use_noise = True
    s.noise_scale = 2
    out["noise_bake"] = bake(domain)
    out["coverage_noise"] = as_dict(gas.cache_coverage(domain, 1, 6))
    out["clearance_noise"] = as_dict(gas.boundary_clearance(domain, [4], significant_density=0.01, guard_layers=1,
                                                            min_clearance_voxels=None, max_guard_fraction=None))
    s.use_noise = False  # the base grid of the same bake, for the noise mapping
    bpy.context.view_layer.update()
    out["clearance_base"] = as_dict(gas.boundary_clearance(domain, [4], significant_density=0.01, guard_layers=1,
                                                           min_clearance_voxels=None, max_guard_fraction=None))

    domain, flow, lid, cache = setup()
    domain.modifiers["Fluid"].domain_settings.cache_type = "REPLAY"
    out["coverage_replay"] = as_dict(gas.cache_coverage(domain, 1, 12))


def clearance_cases(out):
    # an upward jet (3 m/s) from z 0.8 reaches the open top border by frame 24
    reset(24)
    domain = make_domain(cache_dir(), end=24)
    make_flow(location=(0, 0, 0.8), up_velocity=3.0)
    out["jet_bake"] = bake(domain)
    jet = [6, 12, 18, 24]
    kw = dict(significant_density=0.01, guard_layers=1)
    out["clearance_open_top"] = as_dict(gas.boundary_clearance(domain, jet, min_clearance_voxels=None,
                                                               max_guard_fraction=None, **kw))
    out["clearance_min"] = as_dict(gas.boundary_clearance(domain, jet, min_clearance_voxels=1,
                                                          max_guard_fraction=None, **kw))
    out["clearance_guard"] = as_dict(gas.boundary_clearance(domain, jet, min_clearance_voxels=None,
                                                            max_guard_fraction=0.05, **kw))
    out["clearance_bad_frames"] = raises(lambda: gas.boundary_clearance(domain, [6, 6], min_clearance_voxels=None,
                                                                        max_guard_fraction=None, **kw))

    # world mapping: domain at (3, -2, 1), rotated 30 deg about z, scale (2, 1, 1); Flow at local (0.4, 0.3, -0.2)
    reset(4)
    loc, rot, scale = Vector((3, -2, 1)), Euler((0, 0, math.radians(30))), Vector((2, 1, 1))
    domain = make_domain(cache_dir(), end=4, scale=tuple(scale), location=tuple(loc), rotation=tuple(rot))
    s = domain.modifiers["Fluid"].domain_settings
    s.alpha = s.beta = s.vorticity = 0.0
    centre = Matrix.LocRotScale(loc, rot, scale) @ Vector((0.4, 0.3, -0.2))
    make_flow(location=tuple(centre), radius=0.15)
    out["mapping_bake"] = bake(domain)
    out["clearance_mapping"] = as_dict(gas.boundary_clearance(domain, [2], min_clearance_voxels=None,
                                                              max_guard_fraction=None, **kw))
    out["mapping_flow_centre"] = list(centre)

    # only the front border closed
    reset(4)
    domain = make_domain(cache_dir(), end=4)
    domain.modifiers["Fluid"].domain_settings.use_collision_border_front = True
    make_flow()
    out["front_bake"] = bake(domain)
    out["clearance_front_closed"] = as_dict(gas.boundary_clearance(domain, [4], min_clearance_voxels=None,
                                                                   max_guard_fraction=None, **kw))

    # frames before a keyed-on Flow emits are empty
    reset(8)
    domain = make_domain(cache_dir(), end=8)
    flow = make_flow()
    key_bool(flow_mod(flow), "use_inflow", [(1, False), (5, True)])
    out["empty_bake"] = bake(domain)
    out["clearance_empty"] = as_dict(gas.boundary_clearance(domain, [2, 3, 8], min_clearance_voxels=None,
                                                            max_guard_fraction=None, **kw))

    # adaptive domain: clearance UNVERIFIED; density wall share None passes, a number fails UNVERIFIED
    reset(6)
    domain = make_domain(cache_dir(), end=6)
    domain.modifiers["Fluid"].domain_settings.use_adaptive_domain = True
    make_flow()
    out["adaptive_bake"] = bake(domain)
    frames = frame_list(1, 6)
    out["adaptive_clearance"] = as_dict(gas.boundary_clearance(domain, frames, min_clearance_voxels=None,
                                                               max_guard_fraction=None, **kw))
    dens = dict(max_drop_rel=0.5, max_jump_rel=100.0, guard_layers=1)
    out["adaptive_density_none"] = as_dict(gas.density(domain, frames, max_boundary_fraction=None, **dens))
    out["adaptive_density_wall"] = as_dict(gas.density(domain, frames, max_boundary_fraction=0.05, **dens))


def main():
    out = {}
    setup_cases(out)
    untouched_cases(out)
    rebase_cases(out)
    coverage_cases(out)
    clearance_cases(out)
    emit(out)


main()
