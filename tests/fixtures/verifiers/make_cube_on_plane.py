"""Build the runner test scene: a 1 m cube on a ground plane.

blender --background --factory-startup --python make_cube_on_plane.py -- <out.blend> [box_bottom_z]
"""
import sys
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[0]
bottom = float(argv[1]) if len(argv) > 1 else 0.0
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_plane_add(size=10, location=(0, 0, 0))
bpy.context.object.name = "ground"
bpy.ops.mesh.primitive_cube_add(size=1, location=(0, 0, bottom + 0.5))
bpy.context.object.name = "box"
bpy.ops.wm.save_as_mainfile(filepath=out)
