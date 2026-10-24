"""Blender-side cases for worldagent_measure.liquid (run by tests/test_measure_liquid.py)."""
import json
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import liquid, verifier  # noqa: E402
from worldagent_measure.geometry import frame_list  # noqa: E402

# Thresholds below are test inputs, not recommended values; cases take theirs from pass_predicate.
SPIKE = 4.0


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def blob(name="water", radius=0.5, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=3, radius=radius, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def cube(name, size=1.0, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def join(target, other):
    bpy.ops.object.select_all(action="DESELECT")
    other.select_set(True)
    target.select_set(True)
    bpy.context.view_layer.objects.active = target
    bpy.ops.object.join()
    return target


def quality(obj, frames, **kw):
    args = dict(max_edge_spike_ratio=SPIKE, max_components=1, max_volume_jump_rel=0.1, max_component_jump=0)
    args.update(kw)
    return verifier.result([liquid.surface_quality(obj, frames, **args)])


def as_dict(check):
    return verifier.result([check])


def blocked(fn):
    """Run fn; turn FlipUnavailable into the blocked result a case script would write."""
    out = os.path.join(tempfile.mkdtemp(), "result.json")
    try:
        fn()
    except liquid.FlipUnavailable as exc:
        verifier.write_blocked(out, str(exc))
        with open(out, encoding="utf-8") as f:
            return json.load(f)
    return {"status": "not_blocked"}


def main():
    out = {}
    frames = frame_list(1, 3)

    reset()
    out["clean_pass"] = quality(blob(), frames)

    # One vertex pulled 1 m out: its edges are ~7x the median edge.
    reset()
    obj = blob()
    obj.data.vertices[0].co *= 3.0
    out["spike_fail"] = quality(obj, frames)

    # One vertex moved onto a neighbour: the two triangles sharing that edge collapse.
    reset()
    obj = blob()
    a, b = obj.data.edges[0].vertices
    obj.data.vertices[a].co = obj.data.vertices[b].co.copy()
    out["zero_area_fail"] = quality(obj, frames)

    # A detached chunk 2 m away: two components.
    reset()
    obj = join(blob(), blob("chunk", radius=0.1, location=(2, 0, 0)))
    out["chunk_fail"] = quality(obj, frames)

    # The chunk is masked on frame 1 and appears on frame 2: component count pops 1 -> 2.
    reset()
    obj = blob()
    obj.vertex_groups.new(name="main").add(list(range(len(obj.data.vertices))), 1.0, "REPLACE")
    obj = join(obj, blob("chunk", radius=0.1, location=(2, 0, 0)))
    mask = obj.modifiers.new("mask", "MASK")
    mask.vertex_group = "main"
    for frame, show in ((1, True), (2, False)):
        mask.show_viewport = show
        mask.keyframe_insert("show_viewport", frame=frame)
    out["component_pop_fail"] = quality(obj, [1, 2], max_components=2)

    # Squashed to half height between frames 1 and 2: volume pops by 50 %.
    reset()
    obj = blob()
    for frame, z in ((1, 1.0), (2, 0.5)):
        obj.scale = (1, 1, z)
        obj.keyframe_insert("scale", frame=frame)
    out["volume_pop_fail"] = quality(obj, [1, 2])

    # Unit cube, 1 m^3; scaled to 0.8 m^3 on frame 3.
    reset()
    obj = cube("water")
    for frame, z in ((1, 1.0), (2, 1.0), (3, 0.8)):
        obj.scale = (1, 1, z)
        obj.keyframe_insert("scale", frame=frame)
    out["volume_pass"] = as_dict(liquid.volume(obj, [1, 2], {1: 1.0, 2: 1.0}, rel_tol=1e-6))
    out["volume_drop_fail"] = as_dict(liquid.volume(obj, frames, lambda f: 1.0, rel_tol=0.05))

    # Open surface (one face deleted): volume undefined.
    reset()
    mesh = bpy.data.meshes.new("open_box")
    verts = [(x, y, z) for x in (-0.5, 0.5) for y in (-0.5, 0.5) for z in (-0.5, 0.5)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4)]  # top face (1, 3, 7, 5) left out
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new("water", mesh)
    bpy.context.collection.objects.link(obj)
    out["volume_open_fail"] = as_dict(liquid.volume(obj, [1], 1.0, rel_tol=0.05))

    # Solids: a cube touching the blob from outside, a cube overlapping it, an open ground plane under it.
    reset()
    water = blob()
    near = cube("box_near", location=(1.0, 0, 0))
    out["solid_touch_pass"] = as_dict(liquid.surface_inside_solids(water, [near], frames, eps_pen=1e-6))
    near.location = (0.9, 0, 0)
    out["solid_inside_fail"] = as_dict(liquid.surface_inside_solids(water, [near], frames, eps_pen=1e-6))
    # The same overlap 300 m from the origin, where BVH float32 resolution is ~3e-5 m: parity rays
    # must step past each hit, not re-hit the same face, so the same vertices count as inside.
    water.location, near.location = (300, 300, 0), (300.9, 300, 0)
    out["solid_inside_far"] = as_dict(liquid.surface_inside_solids(water, [near], frames, eps_pen=1e-6))
    water.location = (0, 0, 0)
    near.location = (5, 0, 0)
    bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, -0.3))
    ground = bpy.context.active_object
    ground.name = "ground"
    out["ground_inside_fail"] = as_dict(liquid.surface_inside_solids(water, [near, ground], frames, eps_pen=1e-6))

    # FLIP not installed (factory startup has no FLIP add-on).
    reset()
    obj = blob()
    out["flip_enable_blocked"] = blocked(liquid.enable_flip)
    out["flip_forced_blocked"] = blocked(lambda: liquid.surface_quality(obj, frames, SPIKE, 1, 0.1, 0, solver="flip"))
    # A FLIP domain saved by the add-on and reopened without it keeps its stored properties.

    class Settings(bpy.types.PropertyGroup):
        object_type: bpy.props.StringProperty()

    bpy.utils.register_class(Settings)
    bpy.types.Object.flip_fluid = bpy.props.PointerProperty(type=Settings)
    obj.flip_fluid.object_type = "TYPE_DOMAIN"
    del bpy.types.Object.flip_fluid
    bpy.utils.unregister_class(Settings)
    out["flip_stored_domain_detected"] = liquid.is_flip_domain(obj)
    out["flip_auto_blocked"] = blocked(lambda: liquid.volume(obj, frames, 1.0, rel_tol=0.05))

    out["mantaflow"] = mantaflow_case()
    emit(out)


def mantaflow_case():
    """Tiny real Mantaflow liquid bake: resolution 24, 10 frames."""
    reset()
    cache = tempfile.mkdtemp(prefix="wa_manta_")
    bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
    domain = bpy.context.active_object
    domain.name = "manta_domain"
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.4, location=(0, 0, 1.3))
    flow = bpy.context.active_object
    settings = domain.modifiers.new("Fluid", "FLUID")
    settings.fluid_type = "DOMAIN"
    ds = settings.domain_settings
    ds.domain_type = "LIQUID"
    ds.resolution_max = 24
    ds.use_mesh = True
    ds.cache_type = "ALL"
    ds.cache_directory = cache
    ds.cache_frame_start, ds.cache_frame_end = 1, 10
    fm = flow.modifiers.new("Fluid", "FLUID")
    fm.fluid_type = "FLOW"
    fm.flow_settings.flow_type = "LIQUID"
    fm.flow_settings.flow_behavior = "GEOMETRY"
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 10
    result = {}
    try:
        liquid.surface_object(domain, 1)
        result["missing_cache"] = "not raised"
    except liquid.LiquidCacheMissing as exc:
        result["missing_cache"] = str(exc)
    started = time.time()
    with bpy.context.temp_override(object=domain, active_object=domain):
        bpy.ops.fluid.bake_all()
    result["bake_s"] = time.time() - started
    frames = frame_list(1, 10)
    # Generous test thresholds: the point is that a real baked liquid is loaded and measured.
    result["quality"] = as_dict(liquid.surface_quality(domain, frames, max_edge_spike_ratio=50.0, max_components=5,
                                                       max_volume_jump_rel=1.0, max_component_jump=5,
                                                       max_zero_area_faces=1000))
    result["volume"] = as_dict(liquid.volume(domain, frames, 4.0 / 3.0 * math.pi * 0.4 ** 3, rel_tol=1.0))
    ds.use_mesh = False
    try:
        liquid.surface_object(domain, 1)
        result["mesh_disabled"] = "not raised"
    except liquid.LiquidCacheMissing as exc:
        result["mesh_disabled"] = str(exc)
    shutil.rmtree(cache, ignore_errors=True)
    return result


main()
