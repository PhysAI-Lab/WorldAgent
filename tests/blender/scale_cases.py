"""Blender-side cases for worldagent_measure.scale (run by tests/test_measure_scale.py)."""
import sys
from pathlib import Path

import math

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import verifier  # noqa: E402
from worldagent_measure.scale import dimensions, pixel_footprint, size_ratios  # noqa: E402


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def box(name, size, location=(0, 0, 0), scale=(1, 1, 1), apply=True):
    """Box with the given world dimensions; apply=False keeps the scale on the object."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    if apply:
        obj.scale = size
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    else:
        obj.scale = [s * k for s, k in zip(size, scale)]
    bpy.context.view_layer.update()
    return obj


def camera(location=(0, -1.6, 0), ortho_scale=None):
    """Camera looking along +y: image x is world +x, image y is world +z."""
    data = bpy.data.cameras.new("cam")
    if ortho_scale:
        data.type = "ORTHO"
        data.ortho_scale = ortho_scale
    cam = bpy.data.objects.new("cam", data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = location
    cam.rotation_euler = (math.radians(90), 0, 0)
    bpy.context.scene.camera = cam
    bpy.context.view_layer.update()
    return cam


def projected_pixels(cam, point, step, axis):
    """Pixels between point and point + step along world axis (0 = x, 2 = z), by Blender's projection."""
    scene = bpy.context.scene
    a = world_to_camera_view(scene, cam, Vector(point))
    b = world_to_camera_view(scene, cam, Vector(point) + step * Vector([float(i == axis) for i in range(3)]))
    size = scene.render.resolution_x if axis == 0 else scene.render.resolution_y
    return abs(b[0 if axis == 0 else 1] - a[0 if axis == 0 else 1]) * size * scene.render.resolution_percentage / 100


def footprint_cases():
    out = {}
    reset()
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1920, 1080, 100
    cam = camera()  # 50 mm lens, 36 mm sensor, 1.6 m from the origin
    fp = pixel_footprint(cam, (0, 0, 0))
    out["persp"] = {"m": fp, "projected_px": projected_pixels(cam, (0, 0, 0), fp, 0)}
    off = (0.5, 0, 0.3)  # off axis, same depth
    out["persp_off_axis"] = {"m": pixel_footprint(cam, off), "projected_px": projected_pixels(cam, off, fp, 0)}
    out["persp_far"] = pixel_footprint(cam, (0, 1.6, 0))
    out["persp_4k"] = pixel_footprint(cam, (0, 0, 0), (3840, 2160))
    out["persp_none_camera"] = pixel_footprint(None, (0, 0, 0))
    scene.render.resolution_percentage = 50
    out["persp_half_percentage"] = pixel_footprint(cam, (0, 0, 0))
    scene.render.resolution_percentage = 100
    try:
        pixel_footprint(cam, (0, -3, 0))
        out["behind"] = None
    except ValueError as exc:
        out["behind"] = str(exc)

    scene.render.resolution_x, scene.render.resolution_y = 1080, 1920  # portrait: AUTO fits the height
    fp = pixel_footprint(cam, (0, 0, 0))
    out["portrait"] = {"m": fp, "projected_px": projected_pixels(cam, (0, 0, 0), fp, 2)}

    reset()
    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 1920, 1080, 100
    cam = camera(ortho_scale=4.0)
    near, far = pixel_footprint(cam, (0, 0, 0)), pixel_footprint(cam, (0, 10, 0))
    out["ortho"] = {"near": near, "far": far, "projected_px": projected_pixels(cam, (0, 10, 0), near, 0)}
    return out


def as_dict(check):
    return verifier.result([check])


def main():
    out = {}

    reset()
    table = box("table", (1.6, 0.9, 0.75))
    out["dims_pass"] = as_dict(dimensions([(table, (1.6, 0.9, 0.75))], rel_tol=1e-6))
    out["dims_wrong"] = as_dict(dimensions([(table, (1.6, 0.9, 1.0))], rel_tol=0.02))

    # A prop turned 30 degrees about z: the contract records its own
    # dimensions (Object.dimensions), which the world AABB no longer equals.
    reset()
    case = box("suitcase", (0.84, 0.525, 0.674))
    case.rotation_euler.z = math.radians(30)
    bpy.context.view_layer.update()
    out["rotated_object_axes"] = as_dict(dimensions([(case, (0.84, 0.525, 0.674))], rel_tol=1e-6))
    out["rotated_world_axes"] = as_dict(dimensions([(case, (0.84, 0.525, 0.674))], rel_tol=1e-6, axes="world"))

    reset()
    crate = box("crate", (1.0, 1.0, 1.5), apply=False)
    out["unapplied_reported"] = as_dict(dimensions([(crate, (1.0, 1.0, 1.5))], rel_tol=1e-6))
    out["unapplied_fail"] = as_dict(dimensions([(crate, (1.0, 1.0, 1.5))], rel_tol=1e-6, fail_on_unapplied=True))

    reset()
    table = box("table", (1.6, 0.9, 0.75))
    chair = box("chair", (0.45, 0.5, 0.9), location=(1.5, 0, 0))
    # chair diagonal ~1.124 m, table diagonal ~1.983 m, rho ~0.567
    out["ratio_pass"] = as_dict(size_ratios([(chair, table, 0.4, 0.9)]))
    out["ratio_fail"] = as_dict(size_ratios([(chair, table, 0.7, 0.9)]))

    out["pixel_footprint"] = footprint_cases()
    emit(out)


main()
