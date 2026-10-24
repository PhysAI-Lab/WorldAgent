"""Scenes for worldagent_measure.solver_mesh; prints one WORLDAGENT_RESULT line."""
import json

import bmesh
import bpy

from worldagent_measure import verifier
from worldagent_measure.solver_mesh import grid_cell_size, solver_mesh_validity


def reset():
    for obj in list(bpy.data.objects):
        bpy.data.objects.remove(obj, do_unlink=True)


def box(name, size, location=(0, 0, 0)):
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.scale = size
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    bpy.context.view_layer.update()
    return obj


def edit(obj, fn):
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    fn(bm)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()
    bpy.context.view_layer.update()


def as_dict(check):
    return verifier.result([check])


out = {}
CELL = 0.1

reset()
solid = box("solid", (1.0, 1.0, 1.0))
out["clean"] = as_dict(solver_mesh_validity([solid], cell_size_m=CELL, min_cells=1))
out["topology_only"] = as_dict(solver_mesh_validity([solid]))

# An extra vertex in the middle of an edge turns the two adjacent quads into 5-gons with
# collinear vertices; triangulating them gives zero-area triangles that are not defects.
reset()
ngon = box("ngon", (1.0, 1.0, 1.0))
edit(ngon, lambda bm: bmesh.ops.subdivide_edges(bm, edges=[bm.edges[0]], cuts=1))
out["collinear_ngon"] = as_dict(solver_mesh_validity([ngon]))

reset()
holed = box("holed", (1.0, 1.0, 1.0))
edit(holed, lambda bm: bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY"))
out["open"] = as_dict(solver_mesh_validity([holed]))

reset()
a = box("slab", (1.0, 1.0, 0.5), (0, 0, 0.25))
b = box("slab_part", (1.0, 1.0, 0.6), (0.4, 0, 0.3))
bpy.ops.object.select_all(action="DESELECT")
a.select_set(True)
b.select_set(True)
bpy.context.view_layer.objects.active = a
bpy.ops.object.join()
bpy.context.view_layer.update()
out["self_intersecting"] = as_dict(solver_mesh_validity([a]))

reset()
door = box("door_leaf", (0.9, 0.05, 2.0))
out["thin"] = as_dict(solver_mesh_validity([door], cell_size_m=CELL, min_cells=1))

reset()
flipped = box("flipped", (1.0, 1.0, 1.0))
edit(flipped, lambda bm: bmesh.ops.reverse_faces(bm, faces=bm.faces[:]))
out["inverted"] = as_dict(solver_mesh_validity([flipped]))

# A 3 mm carpet lying on a 0.5 m floor slab: alone it is far below one cell, but the solver
# fills floor and carpet as one union. Grouping is accepted only if the carpet really touches.
reset()
floor = box("floor", (2.0, 2.0, 0.5), (0, 0, 0.25))
carpet = box("carpet", (1.0, 1.0, 0.003), (0, 0, 0.5 + 0.0015))
out["carpet_alone"] = as_dict(solver_mesh_validity([floor, carpet], cell_size_m=CELL, min_cells=1))
out["carpet_grouped"] = as_dict(solver_mesh_validity([floor, carpet], cell_size_m=CELL, min_cells=1,
                                                     groups={"floor": ["floor", "carpet"]},
                                                     group_contact_tol_m=1e-6))
carpet.location.z += 0.05
bpy.context.view_layer.update()
out["carpet_floating_grouped"] = as_dict(solver_mesh_validity([floor, carpet], cell_size_m=CELL, min_cells=1,
                                                              groups={"floor": ["floor", "carpet"]},
                                                              group_contact_tol_m=1e-6))
group_errors = {}
for key, call in {
    "member_not_checked": lambda: solver_mesh_validity([floor], cell_size_m=CELL, min_cells=1,
                                                       groups={"g": ["floor", "carpet"]}, group_contact_tol_m=0),
    "no_tolerance": lambda: solver_mesh_validity([floor, carpet], cell_size_m=CELL, min_cells=1,
                                                 groups={"g": ["floor", "carpet"]}),
}.items():
    try:
        call()
        group_errors[key] = None
    except ValueError as exc:
        group_errors[key] = type(exc).__name__
out["group_errors"] = group_errors

reset()
dom = box("domain", (4.0, 2.0, 2.0))
fluid = dom.modifiers.new("Fluid", "FLUID")
fluid.fluid_type = "DOMAIN"
fluid.domain_settings.domain_type = "LIQUID"
fluid.domain_settings.resolution_max = 32
out["cell_size"] = grid_cell_size(dom)
plain = box("plain", (1.0, 1.0, 1.0), (5, 0, 0))

errors = {}
for key, call in {
    "no_objects": lambda: solver_mesh_validity([]),
    "cell_without_min": lambda: solver_mesh_validity([dom], cell_size_m=CELL),
    "not_a_domain": lambda: grid_cell_size(plain),
}.items():
    try:
        call()
        errors[key] = None
    except ValueError as exc:
        errors[key] = type(exc).__name__
out["errors"] = errors

print("WORLDAGENT_RESULT " + json.dumps(out, default=str))
