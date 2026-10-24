"""Real FLIP Fluids bake measured by worldagent_measure.liquid (needs the FLIP add-on).

Args after --: <flip_addon_parent_dir> <work_dir> <phase>
  phase "bake":    build a small FLIP scene, bake it, measure it, save the baked .blend
  phase "noflip":  reopen the baked .blend without FLIP and expect a blocked result
"""
import json
import math
import sys
from pathlib import Path

import bpy

from worldagent_measure import liquid

flip_dir, work, phase = sys.argv[sys.argv.index("--") + 1:][:3]
work = Path(work)
out = {}

if phase == "noflip":
    domain = bpy.data.objects["flip_domain"]
    out["is_flip_domain"] = liquid.is_flip_domain(domain)
    try:
        liquid.surface_object(domain, 5)
        out["blocked"] = False
    except liquid.FlipUnavailable as exc:
        out["blocked"] = True
        out["reason"] = str(exc)
    print("WORLDAGENT_RESULT " + json.dumps(out))
    raise SystemExit(0)

sys.path.insert(0, flip_dir)
liquid.enable_flip()
# Empty the factory scene without read_factory_settings, which would unload the add-on.
for obj in list(bpy.data.objects):
    bpy.data.objects.remove(obj, do_unlink=True)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 12

bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1.0))
domain = bpy.context.object
domain.name = "flip_domain"
domain.flip_fluid.is_active = True
domain.flip_fluid.object_type = "TYPE_DOMAIN"
d = domain.flip_fluid.domain
d.simulation.resolution = 32
d.cache.cache_directory = str(work / "cache")

radius = 0.4
bpy.ops.mesh.primitive_uv_sphere_add(radius=radius, location=(0, 0, 1.2), segments=48, ring_count=24)
fluid = bpy.context.object
fluid.name = "fluid_ball"
fluid.flip_fluid.is_active = True
fluid.flip_fluid.object_type = "TYPE_FLUID"

bpy.ops.mesh.primitive_cube_add(size=0.6, location=(0, 0, 0.3))
block = bpy.context.object
block.name = "obstacle_block"
block.flip_fluid.is_active = True
block.flip_fluid.object_type = "TYPE_OBSTACLE"

bpy.ops.wm.save_as_mainfile(filepath=str(work / "flip_scene.blend"))
bpy.ops.object.select_all(action="DESELECT")
domain.select_set(True)
bpy.context.view_layer.objects.active = domain
out["bake_result"] = list(bpy.ops.flip_fluid_operators.bake_fluid_simulation_cmd())
bpy.ops.wm.save_as_mainfile(filepath=str(work / "flip_scene.blend"))

frames = list(range(1, 13))
surfaces = {}
for f in frames:
    s = liquid.surface_object(domain, f)
    surfaces[f] = {"vertices": len(s.vertices), "triangles": len(s.triangles),
                   "open_edges": liquid.open_edge_count(s.triangles),
                   "volume_m3": liquid.signed_volume(s) if liquid.open_edge_count(s.triangles) == 0 else None,
                   "spike_ratio": liquid.edge_spike_ratio(s)}
out["is_flip_domain"] = liquid.is_flip_domain(domain)
out["surfaces"] = surfaces
out["sphere_volume_m3"] = 4.0 / 3.0 * math.pi * radius ** 3

q = liquid.surface_quality(domain, frames, max_edge_spike_ratio=1e9, max_components=10**6,
                           max_volume_jump_rel=1e9, max_component_jump=10**6, max_zero_area_faces=10**6)
out["quality"] = {"passed": q.passed, "measurements_keys": sorted(q.measurements)}
c = liquid.surface_inside_solids(domain, [block], frames, eps_pen=1e-3)
out["inside_obstacle"] = {"passed": c.passed, "first_failure": c.first_failure,
                          "measurements": {k: v for k, v in c.measurements.items() if not isinstance(v, list)}}
print("WORLDAGENT_RESULT " + json.dumps(out, default=str))
