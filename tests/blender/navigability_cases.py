"""Blender-side cases for worldagent_measure.navigability (run by tests/test_measure_navigability.py).

Scene: a corridor along x (floor top at z = 0, side walls at y = +-1, ceiling
plane at z = 3) crossed at x = 0 by a partition with a 1.0 m wide doorway
under a lintel. The route runs along y = 0 from x = -4 to x = 4.
"""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import verifier  # noqa: E402
from worldagent_measure.navigability import route  # noqa: E402

THRESHOLDS = {"min_width_m": 0.8, "min_headroom_m": 2.0, "max_slope_deg": 10.0, "max_step_m": 0.2,
              "sample_step_m": 0.05}
STRAIGHT = [(-4, 0, 0), (4, 0, 0)]


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def box(name, lo, hi):
    """Axis-aligned box spanning world corners lo..hi, scale applied."""
    size = [h - l for l, h in zip(lo, hi)]
    center = [(l + h) / 2 for l, h in zip(lo, hi)]
    bpy.ops.mesh.primitive_cube_add(size=1, location=center)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def wedge(name, x0, x1, rise, half_width=1.0):
    """Closed ramp from (x0, z = 0) up to (x1, z = rise), outward normals."""
    verts = [(x0, -half_width, 0), (x1, -half_width, 0), (x1, -half_width, rise),
             (x0, half_width, 0), (x1, half_width, 0), (x1, half_width, rise)]
    faces = [(0, 1, 2), (3, 5, 4), (0, 3, 4, 1), (1, 4, 5, 2), (0, 2, 5, 3)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def corridor(lintel_z=2.1, floor_gap=None):
    """Return (walkable, obstacles) for the corridor scene."""
    reset()
    if floor_gap is None:
        walkable = [box("floor", (-5, -1, -0.1), (5, 1, 0))]
    else:
        walkable = [box("floor_a", (-5, -1, -0.1), (floor_gap[0], 1, 0)),
                    box("floor_b", (floor_gap[1], -1, -0.1), (5, 1, 0))]
    obstacles = [
        box("wall_left", (-5, 1.0, 0), (5, 1.1, 3)),
        box("wall_right", (-5, -1.1, 0), (5, -1.0, 3)),
        box("partition_left", (-0.05, 0.5, 0), (0.05, 1.0, 3)),
        box("partition_right", (-0.05, -1.0, 0), (0.05, -0.5, 3)),
        box("lintel", (-0.05, -0.5, lintel_z), (0.05, 0.5, 3)),
    ]
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 3))
    bpy.context.active_object.name = "ceiling"
    obstacles.append(bpy.context.active_object)
    return walkable, obstacles


def run(points, walkable, obstacles, **overrides):
    bpy.context.view_layer.update()
    return verifier.result([route(points, walkable, obstacles, **{**THRESHOLDS, **overrides})])


def main():
    out = {}

    walkable, obstacles = corridor()
    out["doorway_clear"] = run(STRAIGHT, walkable, obstacles)

    walkable, obstacles = corridor()
    obstacles.append(box("crate", (-0.3, -0.3, 0), (0.3, 0.3, 1.0)))
    out["doorway_blocked"] = run(STRAIGHT, walkable, obstacles)

    # Off the route line but narrowing the doorway to 0.6 m.
    walkable, obstacles = corridor()
    obstacles.append(box("crate", (-0.3, 0.1, 0), (0.3, 0.5, 1.0)))
    out["doorway_narrowed"] = run(STRAIGHT, walkable, obstacles)

    walkable, obstacles = corridor(lintel_z=1.8)
    out["low_lintel"] = run(STRAIGHT, walkable, obstacles)

    for label, height in (("step_ok", 0.15), ("step_high", 0.3)):
        walkable, obstacles = corridor()
        walkable.append(box("platform", (1.0, -1, 0), (5, 1, height)))
        out[label] = run([(-4, 0, 0), (0.95, 0, 0), (1.05, 0, height), (4, 0, height)], walkable, obstacles)

    for label, rise in (("ramp_gentle", 0.1), ("ramp_steep", 0.6)):
        walkable, obstacles = corridor()
        walkable += [wedge("ramp", 1.0, 2.0, rise), box("landing", (2.0, -1, 0), (5, 1, rise))]
        out[label] = run([(-4, 0, 0), (1.0, 0, 0), (2.0, 0, rise), (4, 0, rise)], walkable, obstacles)

    walkable, obstacles = corridor(floor_gap=(1.0, 1.5))
    out["floor_gap"] = run(STRAIGHT, walkable, obstacles)

    emit(out)


main()
