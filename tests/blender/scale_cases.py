"""Blender-side cases for worldagent_measure.scale (run by tests/test_measure_scale.py)."""
import sys
from pathlib import Path

import math

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import verifier  # noqa: E402
from worldagent_measure.scale import dimensions, size_ratios  # noqa: E402


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

    emit(out)


main()
