"""Blender-side cases for worldagent_measure.causal and .shot (run by tests/test_measure_story.py).

Keyframes are linear, so contact and motion frames are known exactly:
- pusher (1 m cube) moves x = -5 + 0.5 (f - 1): its +x face reaches the box's -x face at frame 9;
- box (1 m cube at x = 0) holds still until frame 11, then moves 0.2 m per frame;
- early_box (beside a second pusher) starts moving toward it at frame 3, before they touch at frame 8;
- water (0.2 m cube) enters the region x in [3, 4] at frame 16.
"""
import math
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import causal, shot, verifier  # noqa: E402
from worldagent_measure.geometry import frame_list  # noqa: E402

FRAMES = frame_list(1, 25)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.frame_start, bpy.context.scene.frame_end = 1, 25


def cube(name, size, location):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.active_object
    obj.name = name
    return obj


def linear(obj):
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
        for k in fc.keyframe_points:
            k.interpolation = "LINEAR"


def key_x(obj, keys):
    for frame, x in keys:
        obj.location.x = x
        obj.keyframe_insert("location", index=0, frame=frame)
    linear(obj)


def as_dict(check):
    return verifier.result([check])


def main():
    out = {}
    reset()
    pusher = cube("pusher", 1, (-5, 0, 0.5))
    key_x(pusher, [(1, -5), (9, -1), (25, -1)])
    box = cube("box", 1, (0, 0, 0.5))
    key_x(box, [(1, 0), (11, 0), (21, 2)])
    pusher2 = cube("pusher2", 1, (-5, 3, 0.5))
    key_x(pusher2, [(1, -5), (9, -1), (25, -1)])
    early = cube("early_box", 1, (0, 3, 0.5))
    key_x(early, [(1, 0), (2, 0), (12, -1)])
    short = cube("short_pusher", 1, (-5, 6, 0.5))
    key_x(short, [(1, -5), (9, -1.003), (25, -1.003)])  # stops 3 mm short, like two 1.5 mm collision margins
    target = cube("target_box", 1, (0, 6, 0.5))
    water = cube("water", 0.2, (0, -3, 0.1))
    key_x(water, [(1, 0), (21, 4)])  # x = 0.2 (f - 1): x - 0.1 reaches 3 between frames 16 and 17

    hit = causal.contact_onset(pusher, box, FRAMES, contact_tol_m=1e-3)
    moved = causal.motion_onset(box, FRAMES, min_displacement_m=0.01)
    entered = causal.region_entry(water, (3, -4, 0), (4, -2, 1), FRAMES)
    never = causal.region_entry(water, (30, 30, 30), (31, 31, 31), FRAMES)
    out["onsets"] = {"contact": hit, "motion": moved, "entry": entered, "never": never}
    events = {"push": hit, "slide": moved, "spill": entered}
    out["order_pass"] = as_dict(causal.order(events, [("push", "slide"), ("slide", "spill")], min_lag_frames=1,
                                             windows={"push": (1, 10), "spill": (15, 25)}))
    out["order_reversed"] = as_dict(causal.order(events, [("spill", "push")], min_lag_frames=1))
    out["order_same_frame"] = as_dict(causal.order({"a": 9, "b": 9}, [("a", "b")], min_lag_frames=1))
    out["order_too_late"] = as_dict(causal.order(events, [("push", "slide")], min_lag_frames=1, max_lag_frames=2))
    out["order_missing"] = as_dict(causal.order({"push": hit, "flood": never}, [("push", "flood")], min_lag_frames=1))
    out["order_window"] = as_dict(causal.order(events, [], min_lag_frames=0, windows={"spill": (1, 10)}))
    out["response_pass"] = as_dict(causal.response_to_contact(pusher, box, FRAMES, contact_tol_m=1e-3,
                                                              min_displacement_m=0.01, max_lag_frames=3))
    out["response_slow"] = as_dict(causal.response_to_contact(pusher, box, FRAMES, contact_tol_m=1e-3,
                                                              min_displacement_m=0.01, max_lag_frames=2))
    out["response_early"] = as_dict(causal.response_to_contact(pusher2, early, FRAMES, contact_tol_m=1e-3,
                                                               min_displacement_m=0.01, max_lag_frames=30))
    out["short_contact"] = causal.contact_onset(short, target, FRAMES, contact_tol_m=1e-3)
    near = causal.closest_approach(short, target, FRAMES)
    out["short_closest"] = {"min_gap_m": near["min_gap_m"], "frame": near["frame"], "n": len(near["gaps"])}
    out["response_no_contact"] = as_dict(causal.response_to_contact(short, target, FRAMES, contact_tol_m=1e-3,
                                                                    min_displacement_m=0.01, max_lag_frames=3))
    pairs, windows = causal.story_dependencies({"contract": {"story": {"beats": [
        {"id": "tip", "frame_interval": [1, 10]},
        {"id": "fall", "frame_interval": [11, 20], "depends_on": ["tip"]}]}}})
    out["story_dependencies"] = {"pairs": pairs, "windows": {k: list(v) for k, v in windows.items()}}

    # Shot: camera at y = -10 looking along +y; the wall hides `hidden`; `outside` is far off frame.
    reset()
    target = cube("target", 1, (0, 0, 0.5))
    hidden = cube("hidden", 1, (4, 0, 0.5))
    cube("wall", 3, (4, -3, 0.5))
    outside = cube("outside", 1, (60, 0, 0.5))
    bpy.ops.object.camera_add(location=(0, -10, 0.5), rotation=(math.pi / 2, 0, 0))
    camera = bpy.context.active_object
    camera.data.lens = 18
    bpy.context.scene.camera = camera
    out["shot_pass"] = as_dict(shot.coverage([(target, (1, 5))], min_visible_fraction=0.5, min_frame_fraction=1.0))
    out["shot_hidden"] = as_dict(shot.coverage([(target, (1, 5)), (hidden, (1, 5))], min_visible_fraction=0.5,
                                               min_frame_fraction=1.0))
    out["shot_outside"] = as_dict(shot.coverage([(outside, (1, 3))], min_visible_fraction=0.1, min_frame_fraction=0.5))
    bpy.context.scene.camera = None
    out["shot_no_camera"] = as_dict(shot.coverage([(target, (1, 2))], min_visible_fraction=0.5, min_frame_fraction=1.0))
    out["shot_explicit_camera"] = as_dict(shot.coverage([(target, (1, 4))], camera=camera, frame_step=2,
                                                        min_visible_fraction=0.5, min_frame_fraction=1.0))
    emit(out)


main()
