"""Blender-side cases for worldagent_measure.rigid_setup (run by tests/test_measure_rigid_setup.py)."""
import sys
from pathlib import Path

import math

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import rigid_setup, verifier  # noqa: E402


def as_dict(check):
    return verifier.result([check])


def body(name, location, size=0.1):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    bpy.ops.rigidbody.object_add(type="ACTIVE")
    return obj


def main():
    out = {}
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.rigidbody.world_add()
    good = body("good", (0, 0, 1))
    moved = body("moved", (2, 0, 1))
    # Applying location moves the origin to the world origin while the cube stays at (2, 0, 1).
    bpy.ops.object.select_all(action="DESELECT")
    moved.select_set(True)
    bpy.context.view_layer.objects.active = moved
    bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)
    out["origin_good_only"] = as_dict(rigid_setup.origin_at_center_of_mass([good], max_offset_m=1e-6))
    out["origin_all"] = as_dict(rigid_setup.origin_at_center_of_mass(max_offset_m=1e-3))
    com, method = rigid_setup.center_of_mass(moved)
    out["moved_com"] = [list(com), method]

    # A constraint made with the operator joins the world's constraint collection.
    bpy.ops.object.empty_add(location=(1, 0, 1))
    joint = bpy.context.object
    joint.name = "joint_op"
    bpy.ops.rigidbody.constraint_add(type="POINT")
    joint.rigid_body_constraint.object1, joint.rigid_body_constraint.object2 = good, moved
    out["constraints_ok"] = as_dict(rigid_setup.constraints_in_world())
    # Unassigning the collection leaves the constraint in the scene but ignored by Bullet.
    bpy.context.scene.rigidbody_world.constraints = None
    out["constraints_ignored"] = as_dict(rigid_setup.constraints_in_world())

    out["bodies_ok"] = as_dict(rigid_setup.bodies_in_world())
    scene = bpy.context.scene
    scene.rigidbody_world.collection.objects.unlink(moved)
    out["bodies_outside"] = as_dict(rigid_setup.bodies_in_world())
    scene.rigidbody_world.collection.objects.link(moved)
    moved.rigid_body.enabled = False
    bpy.ops.object.empty_add(location=(5, 0, 0))
    plain = bpy.context.object
    plain.name = "plain"
    out["bodies_disabled"] = as_dict(rigid_setup.bodies_in_world([moved, plain]))
    moved.rigid_body.keyframe_insert("enabled", frame=1)
    out["bodies_pinned_off"] = as_dict(rigid_setup.bodies_in_world([moved]))
    moved.rigid_body.enabled = True
    moved.rigid_body.keyframe_insert("enabled", frame=5)
    scene.frame_set(1)  # off at frame 1, switched on at frame 5
    out["bodies_animated_dynamic"] = as_dict(rigid_setup.bodies_in_world([moved]))
    moved.animation_data_clear()
    moved.rigid_body.enabled = True

    out["keyed_none"] = as_dict(rigid_setup.keyed_active_bodies())
    good.keyframe_insert("location", frame=1)
    out["keyed_active"] = as_dict(rigid_setup.keyed_active_bodies())
    out["keyed_declared"] = as_dict(rigid_setup.keyed_active_bodies(kinematic=["good"]))
    good.rigid_body.kinematic = True
    # A single key pins kinematic on (no release), as a keyed pusher often has.
    good.rigid_body.keyframe_insert("kinematic", frame=1)
    out["keyed_kinematic"] = as_dict(rigid_setup.keyed_active_bodies())
    good.rigid_body.kinematic = False
    good.rigid_body.keyframe_insert("kinematic", frame=5)
    out["keyed_release"] = as_dict(rigid_setup.keyed_active_bodies())

    # Tiny range so the bake stays fast.
    scene.frame_start, scene.frame_end = 1, 5
    cache = scene.rigidbody_world.point_cache
    cache.frame_start, cache.frame_end = 1, 5
    out["cache_unbaked"] = as_dict(rigid_setup.cache_coverage(1, 5))
    bpy.ops.ptcache.bake_all(bake=True)
    out["cache_baked"] = as_dict(rigid_setup.cache_coverage(1, 5))
    out["cache_short"] = as_dict(rigid_setup.cache_coverage(1, 10))
    bpy.ops.ptcache.free_bake_all()
    bpy.ops.rigidbody.world_remove()
    out["cache_no_world"] = as_dict(rigid_setup.cache_coverage(1, 5))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.rigidbody.world_add()

    def shaped(name, shape, margin=0.0):
        obj = bpy.context.object
        obj.name = name
        bpy.ops.rigidbody.object_add(type="ACTIVE")
        obj.rigid_body.collision_shape = shape
        obj.rigid_body.use_margin = True
        obj.rigid_body.collision_margin = margin
        return obj

    def fit(objects):
        return as_dict(rigid_setup.collision_shape_fit(objects, max_protrusion_m=1e-3, max_uncovered_m=1e-3))

    # A cylinder mesh lying along X: Bullet's CYLINDER is always along local Z.
    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=0.4, location=(0, 0, 1), rotation=(0, math.pi / 2, 0))
    bpy.ops.object.transform_apply(location=False, rotation=True, scale=False)
    along_x = shaped("cyl_x", "CYLINDER")
    out["fit_cylinder_x"] = fit([along_x])
    bpy.ops.mesh.primitive_cylinder_add(radius=0.05, depth=0.4, location=(1, 0, 1))
    out["fit_cylinder_z"] = fit([shaped("cyl_z", "CYLINDER")])
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(2, 0, 1))
    box = shaped("box", "BOX")
    out["fit_box"] = fit([box])
    bpy.ops.mesh.primitive_cube_add(size=0.1, location=(3, 0, 1), scale=(4, 1, 1))
    out["fit_sphere_on_bar"] = fit([shaped("bar", "SPHERE")])
    # A thin box with use_margin off gets Blender's 0.04 margin, larger than its 0.01 half extent.
    bpy.ops.mesh.primitive_cube_add(size=0.02, location=(4, 0, 1))
    thin = shaped("thin", "BOX")
    thin.rigid_body.use_margin = False
    out["fit_thin_default_margin"] = fit([thin])
    # A CONVEX_HULL fills a torus hole: the hull's cap over the hole sits sqrt(R^2 + r^2) - r
    # from the tube at the hole centre (R 0.1, r 0.02: 0.0820 m; inner hole radius 0.08 m).
    bpy.ops.mesh.primitive_torus_add(major_radius=0.1, minor_radius=0.02, location=(5, 0, 1))
    out["fit_torus_hull"] = fit([shaped("torus", "CONVEX_HULL")])
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(6, 0, 1))
    out["fit_box_hull"] = fit([shaped("box_hull", "CONVEX_HULL", margin=0.0005)])
    bpy.ops.mesh.primitive_monkey_add(size=0.2, location=(7, 0, 1))
    shaped("trimesh", "MESH")
    out["fit_all"] = fit(None)
    emit(out)


main()
