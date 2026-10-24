"""Blender-side cases for worldagent_measure.support (run by tests/test_measure_support.py)."""
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import verifier  # noqa: E402
from worldagent_measure.geometry import aabb_overlap, frame_list, world_mesh  # noqa: E402
from worldagent_measure.support import support_chain  # noqa: E402

TOL = 1e-4
SPEC = {"floor": "foundation", "table": "floor", "box": "table"}


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
    bpy.context.view_layer.update()
    return obj


def floor():
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
    bpy.context.active_object.name = "floor"


def table_scene(box_lo, box_hi):
    reset()
    floor()
    box("table", (-0.5, -0.5, 0.0), (0.5, 0.5, 0.75))
    return box("box", box_lo, box_hi)


def run(supports, frames=(1,), foundations=(), **kw):
    return verifier.result([support_chain(supports, foundations, list(frames), TOL, **kw)])


def main():
    out = {}

    table_scene((-0.15, -0.15, 0.75), (0.15, 0.15, 1.05))
    out["on_table"] = run(SPEC)
    out["on_table_foundation_list"] = run({"table": "floor", "box": "table"}, foundations=["floor"])

    table_scene((-0.15, -0.15, 0.75 - 0.0005), (0.15, 0.15, 1.05))
    out["slight_penetration"] = run(SPEC)

    table_scene((-0.15, -0.15, 0.80), (0.15, 0.15, 1.10))
    out["floating_5cm"] = run(SPEC)

    # 5 mm off the table's side at table-top height: AABBs overlap with a 1 cm margin, no support.
    side = table_scene((0.505, -0.15, 0.75), (0.805, 0.15, 1.05))
    out["beside_aabb_near"] = run(SPEC)
    out["beside_aabb_near"]["aabb_overlap_1cm"] = aabb_overlap(
        world_mesh(side).bbox, world_mesh(bpy.data.objects["table"]).bbox, margin=0.01)

    # On the floor, touching the table's side face, but declared as resting on the table.
    table_scene((0.5, -0.15, 0.0), (0.8, 0.15, 0.3))
    out["beside_on_floor"] = run(SPEC)

    table_scene((-0.15, -0.15, 0.75), (0.15, 0.15, 1.05))
    out["missing_supporter"] = run({"floor": "foundation", "table": "floor", "box": "shelf"})
    out["cycle"] = run({"table": "box", "box": "table"})
    out["undeclared_supporter"] = run({"box": "table"})

    # The table hovers 2 cm above the floor: both the table and the box on it are unsupported.
    reset()
    floor()
    box("table", (-0.5, -0.5, 0.02), (0.5, 0.5, 0.77))
    box("box", (-0.15, -0.15, 0.77), (0.15, 0.15, 1.07))
    out["lower_link_broken"] = run(SPEC)

    # A beam on two posts ('post_a|post_b'); then one post is 1 cm short.
    for label, height_b in (("two_posts", 1.0), ("two_posts_one_short", 0.99)):
        reset()
        floor()
        box("post_a", (-1.0, -0.1, 0.0), (-0.8, 0.1, 1.0))
        box("post_b", (0.8, -0.1, 0.0), (1.0, 0.1, height_b))
        box("beam", (-1.0, -0.1, 1.0), (1.0, 0.1, 1.1))
        out[label] = run({"floor": "foundation", "post_a": "floor", "post_b": "floor", "beam": "post_a|post_b"})

    # A door held by its frame at the hinge side, 1 cm above the floor.
    reset()
    floor()
    box("frame", (-0.1, -0.05, 0.0), (0.0, 0.05, 2.2))
    box("door", (0.0, -0.02, 0.01), (0.9, 0.02, 2.1))
    hinged = {"floor": "foundation", "frame": "floor", "door": "frame"}
    out["hinge_attached"] = run(hinged, attached=["door"])
    out["hinge_as_resting"] = run(hinged)

    # Box rests on the table for frames 1-5, then is lifted by frame 10.
    obj = table_scene((-0.15, -0.15, 0.75), (0.15, 0.15, 1.05))
    for frame, z in ((1, 0.9), (5, 0.9), (10, 1.1)):
        obj.location.z = z
        obj.keyframe_insert("location", index=2, frame=frame)
    out["lifted_later"] = run(SPEC, frames=frame_list(1, 10))

    emit(out)


main()
