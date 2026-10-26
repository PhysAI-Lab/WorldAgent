"""A Bullet body as a FLIP obstacle, checked by worldagent_measure.coupling (needs the FLIP add-on).

Args after --: <flip_addon_parent_dir> <work_dir>
Bakes the Bullet body, then FLIP, then reads the obstacle mesh FLIP exported; moves the body's
start and rebakes Bullet only (FLIP is now stale); finally turns export_animated_mesh off.
"""
import json
import sys
from pathlib import Path

import bpy

from worldagent_measure import coupling, liquid

flip_dir, work = sys.argv[sys.argv.index("--") + 1:][:2]
work = Path(work)
case = work / "case"
case.mkdir(parents=True, exist_ok=True)
out = {}

sys.path.insert(0, flip_dir)
liquid.enable_flip()
for obj in list(bpy.data.objects):  # read_factory_settings would unload the add-on
    bpy.data.objects.remove(obj, do_unlink=True)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 10
bpy.ops.wm.save_as_mainfile(filepath=str(case / "scene.blend"))

bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1.0))
domain = bpy.context.object
domain.name = "flip_domain"
domain.flip_fluid.is_active = True
domain.flip_fluid.object_type = "TYPE_DOMAIN"
domain.flip_fluid.domain.simulation.resolution = 24
domain.flip_fluid.domain.cache.cache_directory = str(case / "cache_flip")

bpy.ops.mesh.primitive_uv_sphere_add(radius=0.4, location=(-0.4, 0, 0.5))
water = bpy.context.object
water.name = "water"
water.flip_fluid.is_active = True
water.flip_fluid.object_type = "TYPE_FLUID"

bpy.ops.mesh.primitive_cube_add(size=0.4, location=(0.4, 0, 1.6))
crate = bpy.context.object
crate.name = "crate"
bpy.ops.rigidbody.object_add()
crate.rigid_body.type = "ACTIVE"
scene.rigidbody_world.point_cache.frame_start, scene.rigidbody_world.point_cache.frame_end = 1, 10
crate.flip_fluid.is_active = True
crate.flip_fluid.object_type = "TYPE_OBSTACLE"
crate.flip_fluid.obstacle.export_animated_mesh = True

m = {"object_contracts": [{"name": "crate", "blender_system": "bullet_rigid_body"},
                          {"name": "water", "blender_system": "flip_fluids"}],
     "topology": {"solver_graph": {
         "nodes": [{"id": "rigid", "objects": ["crate"], "cache": "in .blend"},
                   {"id": "water", "objects": ["water"], "domain": "flip_domain"}],
         "edges": [{"id": "crate_in_water", "from": "rigid", "to": "water", "channel": "obstacle",
                    "objects": ["crate"], "omitted_reactions": ["buoyancy and drag on the crate"]}],
         "bake_order": ["rigid", "water"]}}}
frames = list(range(1, 11))


def evidence(tol):
    c = coupling.bake_evidence(m, case, "c01", frames=frames, max_export_deviation_m=tol, unverified="fail")
    e = c.measurements["edges"][0]
    return {"status": "pass" if c.passed else "fail", "evidence": e["evidence"], "problem": e.get("problem"),
            "worst": (e.get("export") or {}).get("worst"), "notes": e["notes"]}


bpy.ops.ptcache.bake_all(bake=True)
bpy.ops.object.select_all(action="DESELECT")
domain.select_set(True)
bpy.context.view_layer.objects.active = domain
out["bake_result"] = list(bpy.ops.flip_fluid_operators.bake_fluid_simulation_cmd())
bpy.ops.wm.save_mainfile()

db = case / "cache_flip" / "export" / "export_data.sqlite3"
out["db_exists"] = db.is_file()
exported = coupling.read_flip_export(db, "crate", frames)
out["motion_type"] = exported["motion_type"]
out["exported_frames"] = sorted(exported["frames"])
out["exported_vertices"] = len(exported["frames"][frames[-1]]) if exported["frames"] else 0
links = coupling.graph_links(m)
out["links"] = {"status": "pass" if links.passed else "fail", "first_failure": links.first_failure,
                "links": [[l["driver"], l["driven"], l["channel"]] for l in links.measurements["links"]]}
out["fresh"] = evidence(1e-3)

# Move the crate's start and rebake Bullet only: FLIP still holds the old obstacle path.
bpy.ops.ptcache.free_bake_all()
scene.frame_set(1)
crate.location.z += 0.1
bpy.ops.ptcache.bake_all(bake=True)
out["stale"] = evidence(1e-3)

crate.flip_fluid.obstacle.export_animated_mesh = False
links = coupling.graph_links(m)
out["no_export_animated"] = {"status": "pass" if links.passed else "fail",
                             "wrong_carrier": [w["problem"] for w in links.measurements["wrong_carrier"]]}
print("WORLDAGENT_RESULT " + json.dumps(out, default=str))
