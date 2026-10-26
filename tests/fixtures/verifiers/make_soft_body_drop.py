"""Build the Soft Body example scene: a jelly cube dropped onto a Collision plate, baked for frames 1-24.

blender --background --factory-startup --python make_soft_body_drop.py -- <out.blend> [goal]

"jelly" (Soft Body, Goal off, media friction 0, real-time speed for 24 fps) starts 0.5 m above
"plate"; "jelly_render" is a copy of its mesh driven by a bound Surface Deform. With "goal" the
new modifier's default Goal is left on, which pins every vertex.
"""
import math
import sys

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[0]
keep_goal = "goal" in argv[1:]
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps, scene.render.fps_base = 24, 1.0
scene.frame_start, scene.frame_end = 1, 24

bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, 0))
plate = bpy.context.active_object
plate.name = "plate"
plate.modifiers.new("Collision", "COLLISION")
plate.collision.thickness_outer = 0.002

bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0, 0, 0.6))
jelly = bpy.context.active_object
jelly.name = "jelly"
bpy.ops.object.mode_set(mode="EDIT")
bpy.ops.mesh.subdivide(number_cuts=2)
bpy.ops.object.mode_set(mode="OBJECT")
soft = jelly.modifiers.new("SoftBody", "SOFT_BODY")
s = soft.settings
s.use_goal = keep_goal
s.friction = 0.0
s.speed = math.sqrt(1000) * scene.render.fps_base / scene.render.fps
soft.point_cache.frame_start, soft.point_cache.frame_end = 1, 24

render = bpy.data.objects.new("jelly_render", jelly.data.copy())
render.matrix_world = jelly.matrix_world
scene.collection.objects.link(render)
deform = render.modifiers.new("SurfaceDeform", "SURFACE_DEFORM")
deform.target = jelly
with bpy.context.temp_override(object=render, active_object=render):
    bpy.ops.object.surfacedeform_bind(modifier=deform.name)

scene.frame_set(1)
with bpy.context.temp_override(point_cache=soft.point_cache, object=jelly, active_object=jelly):
    bpy.ops.ptcache.bake(bake=True)
bpy.ops.wm.save_as_mainfile(filepath=out)
