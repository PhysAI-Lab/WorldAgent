"""Scene builders for the contact helper tests (tests/blender/contact_cases.py, verify_contact_example.py).

A table slab (closed mesh, top at TABLE_TOP) and a box that rests on it or sinks into it.
Also usable from the command line to save a candidate .blend:

    blender --background --factory-startup --python contact_scenes.py -- <out.blend> <sink_m>
"""
import sys

import bpy

TABLE_TOP = 0.75
TABLE_SIZE = (1.2, 0.8, 0.05)
BOX_SIZE = 0.3


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def cuboid(name, size, location):
    """Closed box mesh with the given world dimensions, scale applied."""
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    return obj


def table():
    return cuboid("table", TABLE_SIZE, (0, 0, TABLE_TOP - TABLE_SIZE[2] / 2))


def box(sink=0.0, name="box", x=0.0):
    """Box whose bottom is `sink` metres below the table top (0 = resting exactly on it)."""
    return cuboid(name, (BOX_SIZE,) * 3, (x, 0, TABLE_TOP - sink + BOX_SIZE / 2))


def ground_plane():
    """Open mesh (single quad) at z = 0."""
    bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = "ground"
    return obj


def table_with_box(sink=0.0):
    reset()
    t, b = table(), box(sink)
    bpy.context.view_layer.update()
    return t, b


def hidden_box(sink):
    """Sunk box behind an opaque wall as seen from the scene camera."""
    t, b = table_with_box(sink)
    bpy.ops.object.camera_add(location=(0, -4, TABLE_TOP + 0.15), rotation=(1.5708, 0, 0))
    bpy.context.scene.camera = bpy.context.active_object
    wall = cuboid("wall", (2.0, 0.05, 2.0), (0, -2, 1.0))
    bpy.context.view_layer.update()
    return t, b, wall


def contained_box(outer_size, inner_size, inner_offset):
    """Small closed box fully inside a larger closed box: no surface crossing."""
    reset()
    outer = cuboid("outer", (outer_size,) * 3, (0, 0, 0))
    inner = cuboid("inner", (inner_size,) * 3, inner_offset)
    bpy.context.view_layer.update()
    return outer, inner


def animated_box(sink_by_frame):
    """Box keyed on every frame: {frame: sink_m}; integer frames hit the keys exactly."""
    t, b = table_with_box(0.0)
    for frame, sink in sorted(sink_by_frame.items()):
        b.location.z = TABLE_TOP - sink + BOX_SIZE / 2
        b.keyframe_insert("location", index=2, frame=frame)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = min(sink_by_frame), max(sink_by_frame)
    return t, b


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:]
    table_with_box(float(argv[1]) if len(argv) > 1 else 0.0)
    bpy.ops.wm.save_as_mainfile(filepath=argv[0])
