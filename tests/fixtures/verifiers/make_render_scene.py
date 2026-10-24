"""Build the render test scene: a cube, a camera and a light, frames 1-2.

blender --background --factory-startup --python make_render_scene.py -- <out.blend>
"""
import sys
import bpy

out = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.mesh.primitive_cube_add(size=1)
bpy.ops.object.light_add(type="SUN", location=(2, -2, 4))
bpy.ops.object.camera_add(location=(0, -6, 1), rotation=(1.45, 0, 0))
scene = bpy.context.scene
scene.camera = bpy.context.object
scene.frame_start, scene.frame_end = 1, 2
bpy.ops.wm.save_as_mainfile(filepath=out)
