"""Blender-side cases for worldagent_measure.numerical (run by tests/test_measure_numerical.py)."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import verifier  # noqa: E402
from worldagent_measure.geometry import frame_list  # noqa: E402
from worldagent_measure.numerical import rest_displacement, stability  # noqa: E402

FPS = 24


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = FPS


def cube(name="cube"):
    bpy.ops.mesh.primitive_cube_add(size=1)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def set_interpolation(obj, interpolation):
    """Blender 5 stores F-curves in action layers/channelbags; walk both layouts."""
    action = obj.animation_data.action
    curves = []
    if hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    curves.extend(bag.fcurves)
    else:
        curves.extend(action.fcurves)
    for fc in curves:
        for kp in fc.keyframe_points:
            kp.interpolation = interpolation


def animate(obj, path, keys, interpolation):
    for frame, value in keys:
        setattr(obj, path, value)
        obj.keyframe_insert(data_path=path, frame=frame)
    set_interpolation(obj, interpolation)


def as_dict(check):
    return verifier.result([check])


def main():
    out = {}
    frames = frame_list(1, 25)

    # 2.4 m over 24 frames: 0.1 m/frame, 2.4 m/s.
    reset()
    obj = cube()
    animate(obj, "location", [(1, (0, 0, 0)), (25, (2.4, 0, 0))], "LINEAR")
    out["steady_pass"] = as_dict(stability([obj], frames, max_displacement_m=0.2, max_speed_m_s=3.0, fps=FPS))

    # 10 m between frames 10 and 11: 240 m/s.
    reset()
    obj = cube()
    animate(obj, "location", [(1, (0, 0, 0)), (10, (0, 0, 0)), (11, (10, 0, 0))], "CONSTANT")
    out["jump_fail"] = as_dict(stability([obj], frames, max_displacement_m=100.0, max_speed_m_s=3.0, fps=FPS))

    reset()
    obj = cube()
    obj.data.vertices[0].co.x = float("nan")
    out["nan_fail"] = as_dict(stability([obj], frames, max_displacement_m=0.2, max_speed_m_s=3.0, fps=FPS))

    # Scale 1 -> 10: largest AABB edge grows from 1 m to 10 m.
    reset()
    obj = cube()
    animate(obj, "scale", [(1, (1, 1, 1)), (25, (10, 10, 10))], "LINEAR")
    out["extent_fail"] = as_dict(stability([obj], frames, max_displacement_m=100.0, max_speed_m_s=1000.0, fps=FPS,
                                           max_extent_m=5.0))

    # Jitter between 0 and 1 mm every frame: 1 mm steps (0.024 m/s at 24 fps), never more than 1 mm from the start.
    reset()
    obj = cube()
    animate(obj, "location", [(f, (0.001 * (f % 2), 0, 0)) for f in range(1, 26)], "CONSTANT")
    out["rest_jitter"] = as_dict(rest_displacement([obj], frames, max_displacement_m=0.003))

    # Slow creep, 1 mm per frame: 24 mm over the window.
    reset()
    obj = cube()
    animate(obj, "location", [(1, (0, 0, 0)), (25, (0.024, 0, 0))], "LINEAR")
    out["rest_creep"] = as_dict(rest_displacement([obj], frames, max_displacement_m=0.003))

    emit(out)


main()
