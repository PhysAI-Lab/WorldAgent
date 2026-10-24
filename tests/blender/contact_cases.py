"""Blender-side cases for worldagent_measure.contact (run by tests/test_measure_contact.py)."""
import sys
from pathlib import Path

import bpy
from bpy_extras import anim_utils

TESTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(TESTS / "fixtures" / "scenes" / "contact"))
from blender_harness import emit  # noqa: E402
import contact_scenes as scenes  # noqa: E402

from worldagent_measure import verifier  # noqa: E402
from worldagent_measure.contact import penetration  # noqa: E402

# Geometry-query noise only (float32 BVH at metre scale); not a penetration allowance.
EPS = 1e-6


def as_dict(check):
    return verifier.result([check])


def camera_sees(obj):
    """True when a ray from the scene camera to obj's center hits obj first."""
    scene = bpy.context.scene
    origin = scene.camera.matrix_world.translation
    target = obj.matrix_world.translation
    hit, _, _, _, hit_obj, _ = scene.ray_cast(bpy.context.evaluated_depsgraph_get(), origin, (target - origin).normalized())
    return bool(hit) and hit_obj.name == obj.name


def summary(check):
    """What a long-range run must reproduce: verdict, first failure and every per-pair stat."""
    mm = check.measurements
    return {
        "passed": check.passed,
        "first_failure": check.first_failure,
        "penetrating_frame_count": mm["penetrating_frame_count"],
        "broad_phase_candidates": mm["broad_phase_candidates"],
        "triangle_overlap_pairs": mm["triangle_overlap_pairs"],
        "depth_points_tested": mm["depth_points_tested"],
        "min_signed_clearance_m": mm["min_signed_clearance_m"],
        "max_penetration_depth_m": mm["max_penetration_depth_m"],
        "candidate_pairs": sorted(mm["candidate_pairs"], key=lambda s: s["pair"]),
    }


def per_frame_reference(objects, frames):
    """summary() of the same range measured one frame per call, so nothing is reused across frames."""
    checks = [penetration(objects, [f], eps_pen=EPS) for f in frames]
    failing = [c for c in checks if not c.passed]
    pairs = {}
    for c in checks:
        for s in c.measurements["candidate_pairs"]:
            total = pairs.setdefault(tuple(s["pair"]), {**s, "candidate_frames": 0, "penetrating_frames": 0,
                                                         "max_depth_m": 0.0, "min_signed_clearance_m": None,
                                                         "ambiguous_solid": False})
            total["candidate_frames"] += s["candidate_frames"]
            total["penetrating_frames"] += s["penetrating_frames"]
            total["max_depth_m"] = max(total["max_depth_m"], s["max_depth_m"])
            total["ambiguous_solid"] = total["ambiguous_solid"] or s["ambiguous_solid"]
            if s["min_signed_clearance_m"] is not None:
                old = total["min_signed_clearance_m"]
                total["min_signed_clearance_m"] = s["min_signed_clearance_m"] if old is None else min(old, s["min_signed_clearance_m"])

    def reduce(key, fn):
        values = [c.measurements[key] for c in checks if c.measurements[key] is not None]
        return fn(values) if values else None

    return {
        "passed": not failing,
        "first_failure": failing[0].first_failure if failing else None,
        "penetrating_frame_count": len(failing),
        "broad_phase_candidates": sum(c.measurements["broad_phase_candidates"] for c in checks),
        "triangle_overlap_pairs": sum(c.measurements["triangle_overlap_pairs"] for c in checks),
        "depth_points_tested": sum(c.measurements["depth_points_tested"] for c in checks),
        "min_signed_clearance_m": reduce("min_signed_clearance_m", min),
        "max_penetration_depth_m": reduce("max_penetration_depth_m", max),
        "candidate_pairs": sorted(pairs.values(), key=lambda s: s["pair"]),
    }


def long_range(objects, frames):
    check = penetration(objects, frames, eps_pen=EPS)
    return {"result": as_dict(check), "summary": summary(check), "reference": per_frame_reference(objects, frames)}


def set_interpolation(id_data, interpolation):
    """Interpolation of every key on id_data's action ("CONSTANT": hold each key until the next)."""
    anim = id_data.animation_data
    for fcurve in anim_utils.action_get_channelbag_for_slot(anim.action, anim.action_slot).fcurves:
        for point in fcurve.keyframe_points:
            point.interpolation = interpolation


def main():
    out = {}

    table, box = scenes.table_with_box(0.0)
    out["resting"] = as_dict(penetration([table, box], [1], eps_pen=EPS))

    table, box = scenes.table_with_box(0.02)
    out["sunk"] = as_dict(penetration([table, box], [1], eps_pen=EPS))
    out["sunk_explicit_pair"] = as_dict(penetration([("box", "table")], [1], eps_pen=EPS))

    table, box = scenes.table_with_box(1e-4)
    out["shallow_eps_small"] = as_dict(penetration([table, box], [1], eps_pen=1e-5))
    out["shallow_eps_large"] = as_dict(penetration([table, box], [1], eps_pen=1e-3))

    table, box, wall = scenes.hidden_box(0.02)
    out["hidden_box_visible"] = camera_sees(box)
    out["hidden_wall_visible"] = camera_sees(wall)
    out["hidden"] = as_dict(penetration([table, box, wall], [1], eps_pen=EPS))

    outer, inner = scenes.contained_box(1.0, 0.2, (0.2, 0, 0))
    out["contained"] = as_dict(penetration([outer, inner], [1], eps_pen=EPS))

    table, box = scenes.animated_box({1: 0.0, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.01, 6: 0.03})
    out["animated"] = as_dict(penetration([table, box], list(range(1, 7)), eps_pen=EPS))
    out["animated_frame_after"] = bpy.context.scene.frame_current

    scenes.reset()
    ground, box = scenes.ground_plane(), scenes.cuboid("box", (0.3,) * 3, (0, 0, 0.15))
    bpy.context.view_layer.update()
    out["ground_resting"] = as_dict(penetration([ground, box], [1], eps_pen=EPS))
    box.location.z = 0.15 - 0.05
    bpy.context.view_layer.update()
    out["ground_sunk"] = as_dict(penetration([ground, box], [1], eps_pen=EPS))

    scenes.reset()
    a = scenes.cuboid("a", (0.3,) * 3, (0, 0, 0))
    b = scenes.cuboid("b", (0.3,) * 3, (2, 0, 0))
    bpy.context.view_layer.update()
    out["apart"] = as_dict(penetration([a, b], [1], eps_pen=EPS))

    errors = {}
    for key, call in {
        "no_eps": lambda: penetration([a, b], [1]),
        "negative_eps": lambda: penetration([a, b], [1], eps_pen=-1.0),
        "no_frames": lambda: penetration([a, b], [], eps_pen=EPS),
        "self_pair": lambda: penetration([(a, a)], [1], eps_pen=EPS),
    }.items():
        try:
            call()
            errors[key] = None
        except (TypeError, ValueError) as e:
            errors[key] = type(e).__name__
    out["errors"] = errors

    # Two overlapping closed shells joined into one object, as authored assets often are
    # (e.g. wall panels). Whole-mesh parity is even inside their overlap.
    scenes.reset()
    shell_a = scenes.cuboid("slab", (2.0, 2.0, 0.5), (0, 0, 0.25))
    # Taller second shell: its side wall crosses the first shell's top face, and points just
    # under that top face are inside both shells (whole-mesh parity there is even).
    shell_b = scenes.cuboid("slab_part", (2.0, 2.0, 0.6), (0.5, 0, 0.3))
    bpy.ops.object.select_all(action="DESELECT")
    shell_a.select_set(True)
    shell_b.select_set(True)
    bpy.context.view_layer.objects.active = shell_a
    bpy.ops.object.join()
    box = scenes.cuboid("box", (0.3,) * 3, (0.25, 0, 0.5 + 0.15 - 0.02))
    bpy.context.view_layer.update()
    out["joined_shells"] = as_dict(penetration([shell_a, box], [1], eps_pen=EPS))

    # Static table, box held then lowered linearly over frames 10..20, then held sunk 0.02 m.
    table, box = scenes.table_with_box(0.0)
    for frame, sink in ((1, 0.0), (10, 0.0), (20, 0.02), (30, 0.02)):
        box.location.z = scenes.TABLE_TOP - sink + scenes.BOX_SIZE / 2
        box.keyframe_insert("location", index=2, frame=frame)
    set_interpolation(box, "LINEAR")
    out["static_table"] = long_range([table, box], list(range(1, 31)))

    # Transform only: the box hovers 0.1 m above the table, then jumps 0.02 m into it on frame 6.
    table, box = scenes.table_with_box(0.0)
    for frame, sink in ((1, -0.1), (6, 0.02)):
        box.location.z = scenes.TABLE_TOP - sink + scenes.BOX_SIZE / 2
        box.keyframe_insert("location", index=2, frame=frame)
    set_interpolation(box, "CONSTANT")
    out["transform_jump"] = long_range([table, box], list(range(1, 9)))

    # Deformation only (object transform never changes): a shape key lowers every vertex 0.02 m.
    table, box = scenes.table_with_box(0.0)
    box.shape_key_add(name="Basis")
    sunk = box.shape_key_add(name="sunk", from_mix=False)
    for point in sunk.data:
        point.co.z -= 0.02
    for frame, value in ((1, 0.0), (5, 1.0)):
        sunk.value = value
        sunk.keyframe_insert("value", frame=frame)
    set_interpolation(box.data.shape_keys, "CONSTANT")
    out["shape_key"] = long_range([table, box], list(range(1, 8)))

    # Deformation by an animated modifier: Displace along Z by its strength (no texture).
    table, box = scenes.table_with_box(0.0)
    displace = box.modifiers.new("displace", "DISPLACE")
    displace.direction, displace.mid_level = "Z", 0.0
    for frame, strength in ((1, 0.0), (4, -0.02)):
        displace.strength = strength
        box.keyframe_insert('modifiers["displace"].strength', frame=frame)
    set_interpolation(box, "CONSTANT")
    out["modifier"] = long_range([table, box], list(range(1, 7)))

    # Rigid body: an active box dropped onto a passive table; Bullet writes the box transform per frame.
    table, box = scenes.table_with_box(-0.2)
    scene = bpy.context.scene
    bpy.ops.rigidbody.world_add()
    for obj, kind in ((table, "PASSIVE"), (box, "ACTIVE")):
        bpy.context.view_layer.objects.active = obj
        bpy.ops.rigidbody.object_add(type=kind)
        obj.rigid_body.collision_shape = "BOX"
    scene.frame_start, scene.frame_end = 1, 40
    scene.rigidbody_world.point_cache.frame_start, scene.rigidbody_world.point_cache.frame_end = 1, 40
    scene.frame_set(1)
    out["rigid_body"] = long_range([table, box], list(range(1, 41)))

    emit(out)


main()
