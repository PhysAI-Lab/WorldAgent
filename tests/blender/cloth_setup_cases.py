"""Scenes for worldagent_measure.deformable.cloth_setup; prints one WORLDAGENT_RESULT line."""
import json

import bpy

from worldagent_measure import verifier
from worldagent_measure.deformable import cloth_setup

N = 8  # quads per side of the base grid
SIZE = 1.0  # base grid side, before object scale
SCALE = 2.0  # uniform object scale, so world-scale lengths differ from local ones


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def grid(name):
    """N x N quads of side SIZE / N in the XY plane, object scale SCALE."""
    step = SIZE / N
    verts = [(i * step - SIZE / 2, j * step - SIZE / 2, 0.0) for j in range(N + 1) for i in range(N + 1)]
    faces = [(j * (N + 1) + i, j * (N + 1) + i + 1, (j + 1) * (N + 1) + i + 1, (j + 1) * (N + 1) + i)
             for j in range(N) for i in range(N)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.location = (0, 0, 1)
    obj.scale = (SCALE, SCALE, SCALE)
    return obj


def cube(name, collision):
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    if collision:
        obj.modifiers.new("Collision", "COLLISION")
    else:
        bpy.ops.rigidbody.object_add(type="PASSIVE")
    return obj


def good_cloth(name="cloth"):
    obj = grid(name)
    solver = obj.modifiers.new("SolverSubdivision", "SUBSURF")
    solver.subdivision_type = "SIMPLE"
    solver.levels = solver.render_levels = 1
    cloth = obj.modifiers.new("Cloth", "CLOTH")
    cloth.settings.quality = 7
    cloth.collision_settings.collision_quality = 3
    cloth.collision_settings.use_self_collision = True
    cloth.collision_settings.distance_min = 0.01
    cloth.collision_settings.self_distance_min = 0.004
    cloth.settings.mass = 0.2
    obj.vertex_groups.new(name="pin")
    cloth.settings.vertex_group_mass = "pin"
    solid = obj.modifiers.new("Solidify", "SOLIDIFY")
    solid.thickness = 0.002
    render = obj.modifiers.new("RenderSubdivision", "SUBSURF")
    render.levels, render.render_levels = 1, 2
    bpy.context.view_layer.update()
    return obj


def as_dict(check):
    return verifier.result([check])


def visibility(obj):
    return [[m.name, m.show_viewport, m.show_render, getattr(m, "levels", None)] for m in obj.modifiers]


out = {}

reset()
good = good_cloth()
wall = cube("wall", collision=True)
before = visibility(good)
objects_before = sorted(o.name for o in bpy.data.objects)
out["good"] = as_dict(cloth_setup(good, colliders=[wall]))
out["visibility_restored"] = before == visibility(good)
out["objects_unchanged"] = objects_before == sorted(o.name for o in bpy.data.objects)

reset()
cloth = good_cloth()
rigid_only = cube("rigid_only", collision=False)
out["collider_without_collision"] = as_dict(cloth_setup(cloth, colliders=[rigid_only]))

reset()
cloth = good_cloth()
cloth.modifiers.move(cloth.modifiers.find("Solidify"), 0)
bpy.context.view_layer.update()
out["solidify_before_cloth"] = as_dict(cloth_setup(cloth))

reset()
cloth = good_cloth()
cloth.modifiers.remove(cloth.modifiers["Cloth"])
out["no_cloth"] = as_dict(cloth_setup(cloth))

reset()
cloth = good_cloth()
cloth.modifiers["SolverSubdivision"].render_levels = 2
out["levels_differ"] = as_dict(cloth_setup(cloth))

reset()
cloth = good_cloth()
cloth.modifiers["SolverSubdivision"].use_adaptive_subdivision = True
out["adaptive"] = as_dict(cloth_setup(cloth))

print("WORLDAGENT_RESULT " + json.dumps(out, default=str))
