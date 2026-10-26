"""Blender-side cases for worldagent_measure.gas and .deformable (run by tests/test_measure_category.py).

Gas: a 16-cell Mantaflow smoke domain baked for 12 frames (the emitter keeps adding density); an upward
jet from higher up that reaches the open top border by frame 24; a domain with unapplied scale (2, 1, 1).
Deformable, exact: a 2 m cube whose shape key scales x by 1.5, keyed 0 -> 1 over frames 1-11
and back to 0 at frame 21 (x edges stretch by 0.5; each vertex moves 0.05 m per frame).
Deformable, solver: a 10 x 10 cloth grid dropped on a collision cube for 20 frames.
"""
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import deformable, gas, verifier  # noqa: E402
from worldagent_measure.geometry import frame_list  # noqa: E402


def reset(end):
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


def smoke_domain(cache_dir, end=12, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    domain = bpy.context.object
    domain.name = "smoke_domain"
    domain.scale = scale
    mod = domain.modifiers.new("Fluid", "FLUID")
    mod.fluid_type = "DOMAIN"
    s = mod.domain_settings
    s.domain_type = "GAS"
    s.resolution_max = 16
    s.cache_directory = cache_dir
    s.cache_type = "ALL"
    s.cache_frame_end = end
    return domain


def smoke_flow(z=0.4, up_velocity=None):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.2, location=(0, 0, z))
    flow = bpy.context.object.modifiers.new("Fluid", "FLUID")
    flow.fluid_type = "FLOW"
    flow.flow_settings.flow_type = "SMOKE"
    flow.flow_settings.flow_behavior = "INFLOW"
    if up_velocity is not None:
        flow.flow_settings.use_initial_velocity = True
        flow.flow_settings.velocity_coord = (0, 0, up_velocity)


def bake(domain):
    with bpy.context.temp_override(object=domain, active_object=domain):
        return list(bpy.ops.fluid.bake_all())


def raw_density_sum(domain):
    for mod in domain.evaluated_get(bpy.context.evaluated_depsgraph_get()).modifiers:
        if mod.type == "FLUID":
            return float(sum(mod.domain_settings.density_grid))


def gas_cases(out):
    reset(12)
    domain = smoke_domain(tempfile.mkdtemp())
    smoke_flow()
    out["gas_bake"] = bake(domain)
    frames = frame_list(1, 12)
    walls = {"max_boundary_fraction": 1.0, "guard_layers": 1}  # the source sits 0.2 m (1.6 voxels) above the floor
    out["gas_pass"] = as_dict(gas.density(domain, frames, max_drop_rel=0.5, max_jump_rel=1.0, **walls))
    out["gas_jump"] = as_dict(gas.density(domain, frames, max_drop_rel=0.5, max_jump_rel=0.01, **walls))
    out["gas_min_total"] = as_dict(gas.density(domain, frames, max_drop_rel=0.5, max_jump_rel=1.0, min_total=1e6,
                                               **walls))
    out["gas_no_guard"] = raises(lambda: gas.density(domain, frames, max_drop_rel=0.5, max_jump_rel=1.0,
                                                     max_boundary_fraction=0.05))
    out["gas_unbaked_total"] = gas.grid_stats(smoke_domain(tempfile.mkdtemp()), guard_layers=1)["total"]  # zero-filled
    bpy.ops.mesh.primitive_cube_add(size=1, location=(5, 0, 0))
    out["gas_not_domain"] = raises(lambda: gas.grid_stats(bpy.context.object, guard_layers=1))

    # an upward jet (3 m/s) from 0.6 m above the floor reaches the open top border by frame 24
    reset(24)
    domain = smoke_domain(tempfile.mkdtemp(), end=24)
    smoke_flow(z=0.8, up_velocity=3.0)
    out["jet_bake"] = bake(domain)
    jet = frame_list(1, 24, 6)
    out["jet_walls"] = as_dict(gas.density(domain, jet, max_drop_rel=1.0, max_jump_rel=100.0,
                                           max_boundary_fraction=0.05, guard_layers=1))
    out["jet_early"] = as_dict(gas.density(domain, frame_list(1, 6), max_drop_rel=1.0, max_jump_rel=100.0,
                                           max_boundary_fraction=0.05, guard_layers=1))

    # unapplied domain scale: a 2 m cube at scale (2, 1, 1) is 4 x 2 x 2 m, res [16, 8, 8], voxel 0.25 m
    reset(6)
    domain = smoke_domain(tempfile.mkdtemp(), end=6, scale=(2, 1, 1))
    smoke_flow()
    out["scaled_bake"] = bake(domain)
    bpy.context.scene.frame_set(6)
    stats = gas.grid_stats(domain, guard_layers=1)
    out["scaled"] = {"cell_size_m": stats["cell_size_m"], "grid": stats["grid"], "total": stats["total"],
                     "raw_sum": raw_density_sum(domain)}


def linear(obj_or_key):
    action = obj_or_key.animation_data.action
    curves = []
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    curves.extend(bag.fcurves)
    else:
        curves.extend(action.fcurves)
    for fc in curves:
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"


def shape_key_cases(out):
    reset(21)
    bpy.ops.mesh.primitive_cube_add(size=2)
    obj = bpy.context.object
    obj.name = "stretchy"
    obj.shape_key_add(name="Basis")
    key = obj.shape_key_add(name="wide")
    for point in key.data:
        point.co.x *= 1.5
    for frame, value in ((1, 0.0), (11, 1.0), (21, 0.0)):
        key.value = value
        key.keyframe_insert("value", frame=frame)
    linear(obj.data.shape_keys)
    frames = frame_list(1, 21)
    out["deform_pass"] = as_dict(deformable.deformation(obj, frames, rest_frame=1, max_stretch_rel=0.6,
                                                        max_compression_rel=0.1, max_step_m=0.06,
                                                        max_self_intersections=0))
    out["deform_stretch"] = as_dict(deformable.deformation(obj, frames, rest_frame=1, max_stretch_rel=0.125,
                                                           max_compression_rel=0.1, max_step_m=0.06,
                                                           max_self_intersections=0))
    out["deform_step"] = as_dict(deformable.deformation(obj, frames, rest_frame=1, max_stretch_rel=0.6,
                                                        max_compression_rel=0.1, max_step_m=0.01,
                                                        max_self_intersections=0))
    out["recovered"] = as_dict(deformable.recovery(obj, rest_frame=1, final_frame=21, max_edge_deviation_rel=1e-4))
    out["not_recovered"] = as_dict(deformable.recovery(obj, rest_frame=1, final_frame=11, max_edge_deviation_rel=0.1))


def cloth_cases(out):
    reset(20)
    bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, 0.5))
    bpy.context.object.modifiers.new("Collision", "COLLISION")
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=10, y_subdivisions=10, size=2, location=(0, 0, 1.5))
    sheet = bpy.context.object
    sheet.name = "sheet"
    sheet.modifiers.new("Cloth", "CLOTH")
    frames = frame_list(1, 20)
    for frame in frames:  # step the cloth cache in order before measuring
        bpy.context.scene.frame_set(frame)
    out["cloth"] = as_dict(deformable.deformation(sheet, frames, rest_frame=1, max_stretch_rel=1.0,
                                                  max_compression_rel=1.0, max_step_m=1.0,
                                                  max_self_intersections=1000))


def main():
    out = {}
    gas_cases(out)
    shape_key_cases(out)
    cloth_cases(out)
    emit(out)


main()
