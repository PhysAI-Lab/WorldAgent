"""Scenes for worldagent_measure.systems; prints one WORLDAGENT_RESULT line."""
import json

import bpy

from worldagent_measure import gas, liquid, systems, verifier


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def cube(name, location=(0, 0, 0), size=1.0):
    bpy.ops.mesh.primitive_cube_add(size=size, location=location)
    obj = bpy.context.object
    obj.name = name
    return obj


def collection(name, *objs):
    coll = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(coll)
    for obj in objs:
        coll.objects.link(obj)
    return coll


def fluid(obj, kind):
    mod = obj.modifiers.new("Fluid", "FLUID")
    mod.fluid_type = kind
    return mod


def particles(obj, physics):
    mod = obj.modifiers.new(f"P_{physics}", "PARTICLE_SYSTEM")
    mod.particle_system.settings.physics_type = physics
    return mod


def summary(obj):
    return {"systems": systems.physics_systems(obj), "all": systems.physics_systems(obj, include_disabled=True),
            "disabled": systems.disabled_systems(obj), "roles": systems.physics_roles(obj)}


def check_dict(check):
    return verifier.result([check])


def system_scene():
    reset()
    out = {}
    out["plain"] = summary(cube("plain"))
    sb = cube("jelly")
    sb.modifiers.new("Softbody", "SOFT_BODY")
    out["soft_body"] = summary(sb)
    off = cube("jelly_off")
    off.modifiers.new("Softbody", "SOFT_BODY").show_viewport = False
    out["soft_body_off"] = summary(off)
    bpy.ops.mesh.primitive_plane_add()
    flag = bpy.context.object
    flag.name = "flag"
    flag.modifiers.new("Cloth", "CLOTH")
    particles(flag, "NEWTON")
    out["cloth_and_particles"] = summary(flag)
    box = cube("box", (3, 0, 0))
    with bpy.context.temp_override(object=box, active_object=box, selected_objects=[box]):
        bpy.ops.rigidbody.object_add(type="ACTIVE")
    out["rigid_active"] = summary(box)
    floor = cube("floor", (0, 0, -3))
    with bpy.context.temp_override(object=floor, active_object=floor, selected_objects=[floor]):
        bpy.ops.rigidbody.object_add(type="PASSIVE")
    floor.rigid_body.kinematic = True
    out["rigid_passive_animated"] = summary(floor)
    loose = cube("loose", (6, 0, 0))
    with bpy.context.temp_override(object=loose, active_object=loose, selected_objects=[loose]):
        bpy.ops.rigidbody.object_add(type="ACTIVE")
    bpy.context.scene.rigidbody_world.collection.objects.unlink(loose)
    out["rigid_outside_world"] = summary(loose)
    gas_domain = cube("gas_domain", (0, 5, 0), 2)
    fluid(gas_domain, "DOMAIN").domain_settings.domain_type = "GAS"
    out["gas_domain"] = summary(gas_domain)
    liquid_domain = cube("liquid_domain", (0, 9, 0), 2)
    fluid(liquid_domain, "DOMAIN").domain_settings.domain_type = "LIQUID"
    out["liquid_domain"] = summary(liquid_domain)
    flow = cube("flow", (0, 5, 0), 0.3)
    fluid(flow, "FLOW")
    out["flow"] = summary(flow)
    sph = cube("sph_emitter", (9, 0, 0))
    particles(sph, "FLUID")
    out["sph"] = summary(sph)
    hair = cube("hair", (12, 0, 0))
    particles(hair, "NEWTON").particle_system.settings.type = "HAIR"
    keyed = cube("keyed", (15, 0, 0))
    particles(keyed, "KEYED")
    out["hair"] = summary(hair)
    out["keyed"] = summary(keyed)
    collider = cube("collider", (0, -4, 0))
    collider.modifiers.new("Collision", "COLLISION")
    out["collider"] = summary(collider)
    bpy.ops.object.effector_add(type="WIND", location=(0, -8, 0))
    out["wind"] = summary(bpy.context.object)
    out["schema_systems"] = list(systems.SYSTEMS)
    out["flip_addon_registered"] = liquid.flip_available()

    contract = lambda s, c=None: {"blender_system": s, "simulation_class": c}  # noqa: E731
    out["identity_pass"] = check_dict(systems.solver_identity(
        [(sb, contract("soft_body", "soft_body")), ("flag", contract("cloth", "cloth")),
         ("box", contract("bullet_rigid_body", "rigid_body")),
         ("plain", contract("none", "fixed")), ("floor", contract("bullet_rigid_body", "fixed"))]))
    out["identity_mismatch"] = check_dict(systems.solver_identity(
        [("plain", contract("none")), (sb, contract("cloth", "soft_body"))]))
    out["identity_none_but_solver"] = check_dict(systems.solver_identity([("flag", contract("none"))]))
    out["identity_disabled"] = check_dict(systems.solver_identity([("jelly_off", contract("soft_body"))]))
    out["identity_outside_world"] = check_dict(systems.solver_identity([("loose", contract("bullet_rigid_body"))]))
    out["identity_missing"] = check_dict(systems.solver_identity([("ghost", contract("cloth"))]))
    errors = {}
    for label, args in {"empty": [], "bad_system": [("plain", {"blender_system": "jelly_solver"})],
                        "twice": [("plain", contract("none")), ("plain", contract("none"))]}.items():
        try:
            systems.solver_identity(args)
        except ValueError as exc:
            errors[label] = str(exc)
    out["identity_errors"] = errors
    return out


def effector_scene():
    reset()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 10
    out = {}
    domain = cube("Domain", (0, 0, 1), 4)
    d = fluid(domain, "DOMAIN").domain_settings
    d.domain_type = "GAS"
    lid = cube("lid", (0, 0, 2.5), 0.5)
    fluid(lid, "EFFECTOR")
    wall = cube("wall", (1.5, 0, 1), 0.5)
    fluid(wall, "EFFECTOR").effector_settings.effector_type = "GUIDE"
    outside = cube("outside", (-1.5, 0, 1), 0.5)
    fluid(outside, "EFFECTOR").show_viewport = False
    keyed = cube("keyed_effector", (0, 1.5, 1), 0.5)
    ks = fluid(keyed, "EFFECTOR").effector_settings
    ks.use_effector = True
    ks.keyframe_insert("use_effector", frame=1)
    ks.use_effector = False
    ks.keyframe_insert("use_effector", frame=5)
    source = cube("source", (0, 0, 0), 0.3)
    fluid(source, "FLOW")
    stray = cube("stray_flow", (0, -1, 0), 0.3)
    fluid(stray, "FLOW").flow_settings.flow_type = "FIRE"
    out["all_scene"] = systems.effectors(domain)
    d.effector_group = collection("Effectors", lid, wall, keyed)
    d.fluid_group = collection("Flows", source)
    scene.frame_set(1)
    out["grouped_frame1"] = systems.effectors(domain)
    scene.frame_set(6)
    out["grouped_frame6"] = systems.effectors(domain, system="mantaflow_gas")

    reset()
    bpy.ops.mesh.primitive_plane_add(location=(0, 0, 2))
    flag = bpy.context.object
    flag.name = "flag"
    cloth = flag.modifiers.new("Cloth", "CLOTH")
    particles(flag, "NEWTON")
    near = cube("near", (0, 0, 0))
    near.modifiers.new("Collision", "COLLISION")
    near.collision.thickness_outer = 0.03
    far = cube("far", (3, 0, 0))
    far.modifiers.new("Collision", "COLLISION")
    far.collision.use = False
    cube("no_collision", (6, 0, 0))
    out["cloth_all"] = systems.effectors(flag, system="cloth")
    cloth.collision_settings.collection = collection("Colliders", near)
    out["cloth_group"] = systems.effectors(flag, system="cloth")
    out["particles"] = systems.effectors(flag, system="newton_particles")
    jelly = cube("jelly", (0, 3, 3))
    jelly.modifiers.new("Softbody", "SOFT_BODY")
    out["soft_body"] = systems.effectors(jelly)
    errors = {}
    for label, call in {"ambiguous": lambda: systems.effectors(flag),
                        "not_covered": lambda: systems.effectors(near, system="bullet_rigid_body"),
                        "absent": lambda: systems.effectors(near, system="cloth")}.items():
        try:
            call()
        except ValueError as exc:
            errors[label] = str(exc)
    out["errors"] = errors
    return out


def cache_missing():
    return {"gas": issubclass(gas.GasCacheMissing, verifier.CacheMissing),
            "liquid": issubclass(liquid.LiquidCacheMissing, verifier.CacheMissing),
            "runtime_error": issubclass(gas.GasCacheMissing, RuntimeError)}


out = {"systems": system_scene(), "effectors": effector_scene(), "cache_missing": cache_missing()}
print("WORLDAGENT_RESULT " + json.dumps(out, default=str))
