"""Blender-side cases for worldagent_measure.particles (run by tests/test_measure_particles.py).

Pass "slow" after `--` to also bake the default DDR system (WORLDAGENT_SLOW_TESTS=1).
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import particles, verifier  # noqa: E402
from worldagent_measure.geometry import frame_list  # noqa: E402

# Thresholds below are test inputs, not recommended values; cases take theirs from pass_predicate.
EPS = 1e-5
ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
END = 36


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, END


def as_dict(check):
    return verifier.result([check])


def error(fn):
    try:
        fn()
    except (ValueError, verifier.CacheMissing) as exc:
        return type(exc).__name__
    return None


def cube(name, size=1.0, location=(0, 0, 0), collision=True):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    if collision:
        obj.modifiers.new("Collision", "COLLISION")
        obj.collision.damping_factor = 1.0
        obj.collision.friction_factor = 1.0
    return obj


def emitter(name, location, *, count=64, grid=8, size=0.01, deflect=True, start=1, end=1, lifetime=1000,
            normal=0.0, physics="NEWTON", collection=None, disk=False):
    bpy.ops.mesh.primitive_plane_add(size=0.4, location=location)
    obj = bpy.context.object
    obj.name = name
    mod = obj.modifiers.new("Particles", "PARTICLE_SYSTEM")
    psys = mod.particle_system
    s = psys.settings
    s.count, s.frame_start, s.frame_end, s.lifetime = count, start, end, lifetime
    s.emit_from = "FACE"
    if grid:
        s.distribution, s.grid_resolution = "GRID", grid
    s.physics_type, s.normal_factor, s.particle_size, s.use_size_deflect = physics, normal, size, deflect
    if collection is not None:
        s.collision_collection = collection
    psys.point_cache.frame_start, psys.point_cache.frame_end = 1, END
    psys.point_cache.use_disk_cache = disk
    return obj


def bake(obj, index=0):
    scene = bpy.context.scene
    with bpy.context.temp_override(scene=scene, active_object=obj, point_cache=obj.particle_systems[index].point_cache):
        bpy.ops.ptcache.bake(bake=True)


def ico(name, location=(50, 50, 50), scale=(1, 1, 0.5)):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0, location=location)
    obj = bpy.context.object
    obj.name, obj.scale = name, scale
    return obj


def disk_cases(out):
    work = tempfile.mkdtemp(prefix="wa_particles_")
    paths = {}
    for key in ("disk_ok", "disk_missing"):
        reset()
        path = os.path.join(work, f"{key}.blend")
        bpy.ops.wm.save_as_mainfile(filepath=path)
        cube("box")
        e = emitter("rests", (0, 0, 1.0), disk=True)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        bake(e)
        bpy.ops.wm.save_as_mainfile(filepath=path)
        paths[key] = path
    shutil.move(os.path.join(work, "blendcache_disk_missing"), os.path.join(work, "moved_away"))
    folder = os.path.join(work, "blendcache_disk_ok")
    out["disk_files_on_disk"] = len([f for f in os.listdir(folder) if f.endswith(".bphys")])
    for key, path in paths.items():
        bpy.ops.wm.open_mainfile(filepath=path)
        status = particles.cache_status("rests")
        row = {"status": status, "coverage": as_dict(particles.cache_coverage(["rests"], 1, END))}
        try:
            st = particles.state("rests", 24)
            row["z_min_24"] = float(st.location[st.alive][:, 2].min())
            row["raised"] = None
        except particles.ParticleCacheMissing as exc:
            row["raised"] = str(exc)
            row["recorded"] = as_dict(verifier.cache_missing_check("particle_cache", exc, system="rests/Particles"))
            row["is_cache_missing"] = isinstance(exc, verifier.CacheMissing)
        out[key] = row


def main_scene(out):
    reset()
    scene = bpy.context.scene
    box = cube("box")
    plain = cube("plain_box", location=(0, 3, 0), collision=False)
    empty = bpy.data.collections.new("no_colliders")
    scene.collection.children.link(empty)
    rests = emitter("rests", (0, 0, 1.0))
    passes = emitter("passes", (0.2, 0, 1.0), collection=empty)
    no_deflect = emitter("no_deflect", (-0.2, 0, 1.0), deflect=False)
    grain = ico("grain")
    inst = emitter("inst", (0.1, 0.1, 1.0))
    inst.particle_systems[0].settings.render_type = "OBJECT"
    inst.particle_systems[0].settings.instance_object = grain
    staggered = emitter("staggered", (5, 0, 1.0), count=100, grid=0, start=1, end=10, lifetime=5)
    escape = emitter("escape", (20, 0, 1.0), count=16, grid=0, normal=5.0)  # GRID ignores normal velocity
    pre = emitter("prerelease", (30, 0, 1.0))
    weights = pre.particle_systems[0].settings.effector_weights
    for frame, value in ((1, 0.0), (9, 0.0), (10, 1.0)):
        weights.gravity = value
        weights.keyframe_insert("gravity", frame=frame)
    child = emitter("child", (60, 0, 1.0))
    child.particle_systems[0].settings.child_type = "SIMPLE"
    unbaked = emitter("unbaked", (70, 0, 1.0))
    for e in (rests, passes, no_deflect, inst, staggered, escape, pre, child):
        bake(e)
    for f in range(1, 13):
        scene.frame_set(f)

    # sphere of static grid points (physics NO): fixed count, world space
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.5, location=(10, 0, 3))
    sphere = bpy.context.object
    sphere.name = "grid_sphere"
    s = sphere.modifiers.new("Points", "PARTICLE_SYSTEM").particle_system.settings
    s.count, s.emit_from, s.distribution, s.grid_resolution = 1000, "VOLUME", "GRID", 10
    s.physics_type, s.frame_start, s.frame_end, s.lifetime = "NO", 1, 1, 1000
    scene.frame_set(5)
    st = particles.state("grid_sphere", 5)
    out["grid_sphere"] = {"evaluated": int(st.location.shape[0]), "count": s.count,
                          "original": len(sphere.particle_systems[0].particles),
                          "mean": st.location[st.alive].mean(axis=0).tolist(), "counts": st.counts(),
                          "status": particles.cache_status("grid_sphere")}
    out["frame_restored"] = scene.frame_current

    frames = frame_list(1, END)
    out["memory_status"] = particles.cache_status(rests)
    out["coverage_ok"] = as_dict(particles.cache_coverage([rests, passes], 1, END))
    out["coverage_short"] = as_dict(particles.cache_coverage([rests], 1, 48))
    out["unbaked"] = {"coverage": as_dict(particles.cache_coverage([unbaked], 1, END)),
                      "state": error(lambda: particles.state(unbaked, 24))}
    out["out_of_range"] = error(lambda: particles.state(rests, 40))
    out["ref_errors"] = {"hair": None, "missing": error(lambda: particles.system("nope")),
                         "wrong_name": error(lambda: particles.system((rests, "other")))}
    bpy.ops.mesh.primitive_plane_add(location=(80, 0, 0))
    hair = bpy.context.object
    hair.modifiers.new("Hair", "PARTICLE_SYSTEM").particle_system.settings.type = "HAIR"
    out["ref_errors"]["hair"] = error(lambda: particles.system(hair))

    # counts: staggered emission 1..10 of 100 particles, lifetime 5
    label = particles.system(staggered).label
    out["staggered_rows"] = [row[label].counts() for row in particles.states([staggered], frames)]
    births = particles.state(staggered, 10).birth_time
    emitted = {f: int((births <= f + 1e-6).sum()) for f in frames}
    out["staggered_emitted"] = emitted
    out["counts_exact"] = as_dict(particles.counts(staggered, frames, expected_emitted=emitted, expected_alive=None,
                                                   max_abs_error=0))
    off = {f: n + 1 for f, n in emitted.items()}
    out["counts_off0"] = as_dict(particles.counts(staggered, frames, expected_emitted=off, expected_alive=None,
                                                  max_abs_error=0))
    out["counts_off1"] = as_dict(particles.counts(staggered, frames, expected_emitted=off, expected_alive=None,
                                                  max_abs_error=1))
    out["counts_none"] = error(lambda: particles.counts(staggered, frames, expected_emitted=None,
                                                        expected_alive=None, max_abs_error=0))

    # setup
    def setup(ref, **kw):
        args = dict(blender_system="newton_particles", fluid_solver=None, colliders=[box], hidden_emitter=False)
        args.update(kw)
        return as_dict(particles.particle_setup(ref, **args))

    out["setup_rests"] = setup(rests)
    out["setup_passes"] = setup(passes)
    out["setup_plain"] = setup(rests, colliders=[plain])
    out["setup_hidden"] = setup(rests, hidden_emitter=True)
    rests.show_instancer_for_render = False
    out["setup_hidden_off"] = setup(rests, hidden_emitter=True)
    rests.show_instancer_for_render = True
    out["setup_sph_on_newton"] = setup(rests, blender_system="sph_particles", fluid_solver="DDR")
    out["setup_child"] = setup(child)
    out["setup_errors"] = {"sph_without_solver": error(lambda: particles.particle_setup(
        rests, blender_system="sph_particles", fluid_solver=None, colliders=[], hidden_emitter=False)),
        "bad_system": error(lambda: particles.particle_setup(
            rests, blender_system="cloth", fluid_solver=None, colliders=[], hidden_emitter=False))}
    fluid = emitter("fluid_two", (90, 0, 1.0), physics="FLUID")
    second = fluid.modifiers.new("Second", "PARTICLE_SYSTEM").particle_system
    second.settings.physics_type = "FLUID"
    out["setup_two_fluid"] = setup((fluid, "Particles"), blender_system="sph_particles", fluid_solver="DDR",
                                   colliders=[])

    # penetration
    def pen(ref, radius="size", fr=frames, solids=(box,), max_count=0, max_fraction=0.0, **kw):
        return as_dict(particles.penetration(ref, list(solids), fr, eps_pen=EPS, radius=radius, max_count=max_count,
                                             max_fraction=max_fraction, **kw))

    out["pen_rests"] = pen(rests)
    out["pen_rests_02"] = pen(rests, radius=0.02)
    out["pen_rests_02_count63"] = pen(rests, radius=0.02, max_count=63, max_fraction=1.0)
    out["pen_rests_02_count64"] = pen(rests, radius=0.02, max_count=64, max_fraction=1.0)
    out["pen_rests_02_fraction"] = pen(rests, radius=0.02, max_count=64, max_fraction=0.99)
    out["pen_no_deflect"] = pen(no_deflect)
    out["pen_no_deflect_center"] = pen(no_deflect, radius=0.0)
    out["pen_passes"] = pen(passes)
    out["pen_inst"] = pen(inst, radius="instances")
    out["pen_inst_size"] = pen(inst)
    out["pen_errors"] = {"instancer_with_size": error(lambda: particles.penetration(
        rests, [box], frames, eps_pen=EPS, radius="size", max_count=0, max_fraction=0.0, instancer=grain)),
        "bad_count": error(lambda: particles.penetration(rests, [box], frames, eps_pen=EPS, radius="size",
                                                         max_count=-1, max_fraction=0.0)),
        "bad_fraction": error(lambda: particles.penetration(rests, [box], frames, eps_pen=EPS, radius="size",
                                                            max_count=0, max_fraction=1.5))}

    # crossings
    def cross(ref, fr, **kw):
        args = dict(eps_pen=EPS, max_count=0, max_fraction=0.0)
        args.update(kw)
        return as_dict(particles.crossings(ref, [box], fr, **args))

    out["cross_passes"] = cross(passes, frames)
    out["cross_passes_wide"] = cross(passes, [1, 24])
    out["cross_passes_tolerated"] = cross(passes, frames, max_count=64, max_fraction=1.0)
    out["cross_rests"] = cross(rests, frames)
    bpy.ops.mesh.primitive_plane_add(size=2, location=(0.2, 0, -1.0))
    sheet = bpy.context.object
    sheet.name = "sheet"
    out["cross_sheet"] = as_dict(particles.crossings(passes, [sheet], frames, eps_pen=EPS, max_count=0,
                                                     max_fraction=0.0))
    # the numpy path for a flat sheet gives the BVH's numbers
    from worldagent_measure.particles import _Solid
    solid = _Solid(sheet, bpy.context.evaluated_depsgraph_get())
    pts = np.array([(x, y, z) for x in (-1.5, -0.3, 0.2, 0.9, 1.4) for y in (-0.8, 0.0, 1.3) for z in (-1.5, -1.0, -0.2)])
    fast = solid.signed(pts)
    solid.plane, has_plane = None, solid.plane is not None
    out["fast_path"] = {"planar": has_plane, "max_diff": float(np.abs(fast - solid.signed(pts)).max())}

    # safety, region, release, rest
    out["safety_rests"] = as_dict(particles.numeric_safety(rests, frames, bounds_min=(-1, -1, 0), bounds_max=(1, 1, 2),
                                                           max_speed_m_s=10, max_step_m=0.5))
    out["safety_passes"] = as_dict(particles.numeric_safety(passes, frames, bounds_min=(-1, -1, -1),
                                                            bounds_max=(1, 1, 2), max_speed_m_s=100, max_step_m=5))
    out["safety_speed"] = as_dict(particles.numeric_safety(passes, frames, bounds_min=(-100,) * 3,
                                                           bounds_max=(100,) * 3, max_speed_m_s=2, max_step_m=5))
    out["region_escape"] = as_dict(particles.region_containment(escape, (19, -1, 0), (21, 1, 2), frames,
                                                                max_outside=0))
    out["region_escape_tolerated"] = as_dict(particles.region_containment(escape, (19, -1, 0), (21, 1, 2), frames,
                                                                          max_outside=16))
    fps = scene.render.fps / scene.render.fps_base
    out["fps"] = fps
    out["prerelease_ok"] = as_dict(particles.pre_release_motion(pre, frames, release_frame=10,
                                                                max_displacement_m=1e-6))
    out["prerelease_late"] = as_dict(particles.pre_release_motion(pre, frames, release_frame=14,
                                                                  max_displacement_m=1e-6))
    out["settle_rests"] = as_dict(particles.settling(rests, frames, rest_speed_m_s=1e-3, min_settled_fraction=1.0,
                                                     speed_source="solver", settle_by_frame=12))
    out["settle_rests_disp"] = as_dict(particles.settling(rests, frames, rest_speed_m_s=1e-3,
                                                          min_settled_fraction=1.0, speed_source="displacement",
                                                          settle_by_frame=None))
    out["settle_rests_early"] = as_dict(particles.settling(rests, frames, rest_speed_m_s=1e-3,
                                                           min_settled_fraction=1.0, speed_source="solver",
                                                           settle_by_frame=3))
    out["settle_passes"] = as_dict(particles.settling(passes, frames, rest_speed_m_s=1e-3, min_settled_fraction=1.0,
                                                      speed_source="solver", settle_by_frame=None))
    window = [f for f in frames if f >= 13]
    out["rest_disp_rests"] = as_dict(particles.rest_displacement(rests, window, max_displacement_m=1e-3,
                                                                  min_settled_fraction=1.0))
    out["rest_disp_passes"] = as_dict(particles.rest_displacement(passes, window, max_displacement_m=1e-3,
                                                                   min_settled_fraction=1.0))
    out["frame_after"] = scene.frame_current

    # every frame of rests from the solver: resting z, speed
    st = particles.state(rests, 24)
    out["rests_24"] = {"z_min": float(st.location[st.alive][:, 2].min()),
                       "z_max": float(st.location[st.alive][:, 2].max()),
                       "speed_max": float(np.linalg.norm(st.velocity[st.alive], axis=1).max())}


def gn_scene(out):
    """1000 points 0.05 m above a ground plane, instanced by Geometry Nodes with Object Info (Original)."""
    reset()
    bpy.ops.mesh.primitive_plane_add(size=4, location=(0, 0, 0))
    ground = bpy.context.object
    ground.name = "ground"
    grain = ico("gn_grain")
    verts = [(float(x), float(y), 0.05) for x in np.linspace(-1, 1, 10) for y in np.linspace(-1, 1, 100)]
    mesh = bpy.data.meshes.new("points")
    mesh.from_pydata(verts, [], [])
    points = bpy.data.objects.new("points", mesh)
    bpy.context.scene.collection.objects.link(points)
    # a static particle system on the points (physics NO), so the ref has centres
    s = points.modifiers.new("Points", "PARTICLE_SYSTEM").particle_system.settings
    s.count, s.emit_from, s.use_emit_random = len(verts), "VERT", False
    s.physics_type, s.frame_start, s.frame_end, s.lifetime = "NO", 1, 1, 1000
    tree = bpy.data.node_groups.new("instance", "GeometryNodeTree")
    tree.interface.new_socket("Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
    tree.interface.new_socket("Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    nodes, links = tree.nodes, tree.links
    group_in, group_out = nodes.new("NodeGroupInput"), nodes.new("NodeGroupOutput")
    on_points = nodes.new("GeometryNodeInstanceOnPoints")
    info = nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = grain
    on_points.inputs["Scale"].default_value = (0.01, 0.01, 0.01)
    links.new(group_in.outputs[0], on_points.inputs["Points"])
    links.new(info.outputs["Geometry"], on_points.inputs["Instance"])
    links.new(on_points.outputs["Instances"], group_out.inputs[0])
    gn = points.modifiers.new("GN", "NODES")
    gn.node_group = tree
    bpy.context.scene.frame_set(1)
    check = particles.penetration(points, [ground], [1, 2], eps_pen=EPS, radius="instances", max_count=0,
                                  max_fraction=0.0, instancer=points)
    out["gn"] = as_dict(check)
    ground.location.z = 0.045
    out["gn_low"] = as_dict(particles.penetration(points, [ground], [1], eps_pen=EPS, radius="instances",
                                                  max_count=0, max_fraction=0.0, instancer=points))
    out["gn_low_tolerated"] = as_dict(particles.penetration(points, [ground], [1], eps_pen=EPS, radius="instances",
                                                            max_count=1000, max_fraction=1.0, instancer=points))


def ddr_scene(out):
    """Default-stiffness DDR over a 2 m floor box: it explodes sideways off the floor (Blender 5.1.1, macOS)."""
    reset()
    scene = bpy.context.scene
    scene.frame_end = 48
    floor = cube("floor", size=2.0, location=(0, 0, -1.0))
    floor.collision.damping_factor, floor.collision.friction_factor = 0.9, 0.5
    bpy.ops.mesh.primitive_cube_add(size=0.5, location=(0, 0, 0.5))
    block = bpy.context.object
    block.name = "sand"
    psys = block.modifiers.new("Sand", "PARTICLE_SYSTEM").particle_system
    s = psys.settings
    s.count, s.emit_from, s.distribution, s.grid_resolution = 1000, "VOLUME", "GRID", 10
    s.frame_start, s.frame_end, s.lifetime = 1, 1, 1000
    s.physics_type, s.normal_factor, s.particle_size = "FLUID", 0.0, 0.02
    s.fluid.solver = "DDR"
    psys.point_cache.frame_start, psys.point_cache.frame_end = 1, 48
    bake(block)
    frames = frame_list(1, 48)
    out["ddr_crossings"] = as_dict(particles.crossings(block, [floor], frames, eps_pen=EPS, max_count=0,
                                                       max_fraction=0.0))
    out["ddr_safety"] = as_dict(particles.numeric_safety(block, frames, bounds_min=(-3, -3, -2), bounds_max=(3, 3, 3),
                                                         max_speed_m_s=1000, max_step_m=100))


out = {}
disk_cases(out)
main_scene(out)
gn_scene(out)
if "slow" in ARGS:
    ddr_scene(out)
emit(out)
