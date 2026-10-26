"""Which Blender system simulates an object, and which objects a solver collides with (requires bpy).

    from worldagent_measure import systems

    systems.physics_systems(jelly)                   # ["soft_body"]
    systems.solver_identity([(jelly, contract), ("floor", floor_contract)])
    systems.effectors(domain)                         # Mantaflow effectors and flows of a domain
    systems.effectors(flag, system="cloth")           # Collision objects the cloth collides with

physics_systems maps an object to the names of schemas/solver_classes.json `systems` (read from
that file, so names cannot drift): Soft Body modifier -> soft_body, Cloth modifier -> cloth,
rigid body in a scene's rigid body world collection -> bullet_rigid_body, Fluid DOMAIN ->
mantaflow_gas or mantaflow_liquid (by domain_type), particle system of physics type NEWTON ->
newton_particles and FLUID -> sph_particles (hair systems are not simulated particles), active
FLIP Fluids domain -> flip_fluids; no system -> ["none"]. Only systems that run count: a
modifier off in the viewport, or a rigid body outside every rigid body world, is listed by
physics_systems(obj, include_disabled=True) and in disabled_systems(obj), not by default.
Fluid FLOW/EFFECTOR, Collision, force fields, Dynamic Paint, rigid body constraints and FLIP
non-domain types are roles, not systems (physics_roles).

FLIP Fluids: with the add-on registered its object settings are read; without it a stored
object type that is a plain string is still read, but a stored enum index cannot be (role
"flip_unresolved"). Enable FLIP first (liquid.enable_flip) when a contract names flip_fluids.

solver_identity is structural (no threshold): each declared blender_system is among the
object's running systems, and "none" means no running system.

effectors(target) lists the objects a solver interacts with, each marked with whether it acts
(see its docstring for the return shape). It reads the current frame's settings (use_effector,
use_inflow and collision.use can be keyed), so step frames yourself to sample them over time.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, Mapping

import bpy

from .verifier import Check

_SCHEMA = Path(__file__).resolve().parents[2] / "schemas" / "solver_classes.json"
SYSTEMS: tuple[str, ...] = tuple(json.loads(_SCHEMA.read_text(encoding="utf-8"))["systems"])

_PARTICLE_SYSTEMS = {"NEWTON": "newton_particles", "FLUID": "sph_particles"}
_DOMAIN_SYSTEMS = {"GAS": "mantaflow_gas", "LIQUID": "mantaflow_liquid"}
_EFFECTOR_TARGETS = ("mantaflow_gas", "mantaflow_liquid", "cloth", "soft_body", "newton_particles",
                     "sph_particles")


def _system(name: str) -> str:
    if name not in SYSTEMS:  # a mapping here that the schema does not know is a bug in this module
        raise AssertionError(f"{name!r} is not in {_SCHEMA.name} systems")
    return name


def _in_rigid_world(obj) -> bool:
    for scene in obj.users_scene:
        world = scene.rigidbody_world
        if world is not None and world.enabled and world.collection is not None \
                and obj.name in world.collection.all_objects:
            return True
    return False


def _flip_type(obj) -> str | None:
    """FLIP object_type ("TYPE_DOMAIN", ...), "UNKNOWN" for a stored enum index, None for no FLIP role."""
    settings = getattr(obj, "flip_fluid", None)
    if settings is not None:
        return settings.object_type if getattr(settings, "is_active", True) else None
    from .liquid import _stored_flip_type  # same package; reads the .blend's stored value
    return _stored_flip_type(obj)


def _systems(obj) -> list[tuple[str, bool]]:
    """(system name, runs) for every system on obj, in modifier order; rigid body and FLIP last."""
    found = []
    for mod in getattr(obj, "modifiers", []):
        on = bool(mod.show_viewport)
        if mod.type == "SOFT_BODY":
            found.append(("soft_body", on))
        elif mod.type == "CLOTH":
            found.append(("cloth", on))
        elif mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
            found.append((_DOMAIN_SYSTEMS[mod.domain_settings.domain_type], on))
        elif mod.type == "PARTICLE_SYSTEM":
            settings = mod.particle_system.settings
            if settings.type == "EMITTER" and settings.physics_type in _PARTICLE_SYSTEMS:
                found.append((_PARTICLE_SYSTEMS[settings.physics_type], on))
    if getattr(obj, "rigid_body", None) is not None:
        found.append(("bullet_rigid_body", _in_rigid_world(obj)))
    if _flip_type(obj) == "TYPE_DOMAIN":
        found.append(("flip_fluids", True))
    return [(_system(name), on) for name, on in found]


def physics_systems(obj, *, include_disabled: bool = False) -> list[str]:
    """Names of the systems that simulate obj (schema `systems` names), ["none"] when there is none."""
    names = list(dict.fromkeys(name for name, on in _systems(obj) if on or include_disabled))
    return names or [_system("none")]


def disabled_systems(obj) -> list[str]:
    """Systems present on obj that do not run: modifier off in the viewport, rigid body in no rigid body world."""
    running = {name for name, on in _systems(obj) if on}
    return list(dict.fromkeys(name for name, on in _systems(obj) if not on and name not in running))


def physics_roles(obj) -> list[str]:
    """Physics roles that are not systems of their own.

    mantaflow_flow, mantaflow_effector, collision, force_field, dynamic_paint_canvas,
    dynamic_paint_brush, rigid_body_passive, rigid_body_animated, rigid_body_constraint, hair,
    keyed_particles or boids_particles for those particle physics types (physics type NO, as
    on a Mantaflow liquid domain's own particle system, is no role), flip_<type> for FLIP
    non-domain objects (flip_obstacle, flip_fluid, flip_inflow, ...), flip_unresolved.
    """
    roles = []
    for mod in getattr(obj, "modifiers", []):
        if mod.type == "FLUID" and mod.fluid_type in ("FLOW", "EFFECTOR"):
            roles.append(f"mantaflow_{mod.fluid_type.lower()}")
        elif mod.type == "COLLISION":
            roles.append("collision")
        elif mod.type == "DYNAMIC_PAINT" and mod.ui_type in ("CANVAS", "BRUSH"):
            roles.append(f"dynamic_paint_{mod.ui_type.lower()}")
        elif mod.type == "PARTICLE_SYSTEM":
            settings = mod.particle_system.settings
            if settings.type == "HAIR":
                roles.append("hair")
            elif settings.physics_type not in _PARTICLE_SYSTEMS and settings.physics_type != "NO":
                roles.append(f"{settings.physics_type.lower()}_particles")
    field = getattr(obj, "field", None)
    if field is not None and field.type != "NONE":
        roles.append("force_field")
    body = getattr(obj, "rigid_body", None)
    if body is not None:
        if body.type == "PASSIVE":
            roles.append("rigid_body_passive")
        if body.kinematic:
            roles.append("rigid_body_animated")
    if getattr(obj, "rigid_body_constraint", None) is not None:
        roles.append("rigid_body_constraint")
    flip = _flip_type(obj)
    if flip == "UNKNOWN":
        roles.append("flip_unresolved")
    elif flip and flip != "TYPE_DOMAIN":
        roles.append("flip_" + flip.removeprefix("TYPE_").lower())
    return list(dict.fromkeys(roles))


def solver_identity(objects_with_contracts: Iterable[tuple], *, name: str = "solver_identity") -> Check:
    """Each object's declared blender_system runs on it in the scene.

    objects_with_contracts: (object or object name, contract) pairs; a contract is a world-spec
    object contract (a mapping with blender_system, optionally simulation_class). "none" passes
    only when the object has no running system. Failures, in input order: the object is not in
    bpy.data.objects ("object missing"); the declared system is on the object but does not run
    ("declared system disabled"); it is not on the object ("declared system not in scene");
    "none" is declared and a system runs ("declared none but a system runs").
    """
    pairs = list(objects_with_contracts)
    if not pairs:
        raise ValueError("no objects")
    per_object, first_failure = {}, None
    for item, contract in pairs:
        if not isinstance(contract, Mapping):
            raise ValueError(f"contract for {item!r} is not a mapping")
        declared = contract.get("blender_system")
        if declared not in SYSTEMS:
            raise ValueError(f"{item!r}: blender_system {declared!r} is not one of {list(SYSTEMS)}")
        obj = bpy.data.objects.get(item) if isinstance(item, str) else item
        label = item if isinstance(item, str) else obj.name
        if label in per_object:
            raise ValueError(f"{label} listed twice")
        record = {"declared": declared, "simulation_class": contract.get("simulation_class")}
        reason = None
        if obj is None:
            record.update(systems=None, disabled_systems=None, roles=None)
            reason = "object missing"
        else:
            running = physics_systems(obj)
            record.update(systems=running, disabled_systems=disabled_systems(obj), roles=physics_roles(obj))
            if declared not in running:
                reason = ("declared none but a system runs" if declared == "none"
                          else "declared system disabled" if declared in record["disabled_systems"]
                          else "declared system not in scene")
        record["matches"] = reason is None
        per_object[label] = record
        if reason and first_failure is None:
            first_failure = {"object": label, "reason": reason, "declared": declared, "systems": record["systems"]}
    measurements = {"objects": per_object, "mismatched": [k for k, r in per_object.items() if not r["matches"]],
                    "flip_addon_registered": hasattr(bpy.types.Object, "flip_fluid")}
    return Check(name, first_failure is None, measurements, first_failure)


def _members(collection) -> set | None:
    return None if collection is None else {o.name for o in collection.all_objects}


def _target_settings(target, system: str, particle_system: str | None):
    """(group collection, RNA path of the group property, modifier or particle system) of target's system."""
    candidates = []
    for mod in target.modifiers:
        if system in ("mantaflow_gas", "mantaflow_liquid") and mod.type == "FLUID" and mod.fluid_type == "DOMAIN" \
                and _DOMAIN_SYSTEMS[mod.domain_settings.domain_type] == system:
            candidates.append((mod.domain_settings.effector_group,
                               f'modifiers["{mod.name}"].domain_settings.effector_group', mod))
        elif system == "cloth" and mod.type == "CLOTH":
            candidates.append((mod.collision_settings.collection,
                               f'modifiers["{mod.name}"].collision_settings.collection', mod))
        elif system == "soft_body" and mod.type == "SOFT_BODY":
            candidates.append((mod.settings.collision_collection,
                               f'modifiers["{mod.name}"].settings.collision_collection', mod))
        elif system in ("newton_particles", "sph_particles") and mod.type == "PARTICLE_SYSTEM":
            psys = mod.particle_system
            if psys.settings.type == "EMITTER" and _PARTICLE_SYSTEMS.get(psys.settings.physics_type) == system \
                    and particle_system in (None, psys.name):
                candidates.append((psys.settings.collision_collection,
                                   f'particle_systems["{psys.name}"].settings.collision_collection', psys))
    if not candidates:
        raise ValueError(f"{target.name} has no {system}" + (f" system {particle_system!r}" if particle_system else ""))
    if len(candidates) > 1:
        raise ValueError(f"{target.name} has {len(candidates)} {system} systems; pass particle_system=")
    return candidates[0]


def effectors(target, *, system: str | None = None, particle_system: str | None = None, scene=None) -> dict:
    """The objects target's solver interacts with, each marked with whether it acts on this frame.

    system           which of target's systems (physics_systems names): mantaflow_gas,
                     mantaflow_liquid, cloth, soft_body, newton_particles, sph_particles; None
                     when target has exactly one of them. bullet_rigid_body and flip_fluids are
                     not covered (Bullet ignores Collision modifiers; FLIP obstacles are FLIP types).
    particle_system  the particle system's name when the object has several of that system
    scene            whose objects are candidates; default the target's first scene

    Returns
        {"target": name, "system": system, "particle_system": name or None,
         "group": collection name or None (None: every candidate in the scene counts),
         "group_property": RNA path of that group on target,
         "effectors": [entry, ...],   # Mantaflow: FLUID EFFECTOR objects; others: Collision objects
         "flows": [entry, ...],       # Mantaflow only: FLUID FLOW objects, group = domain fluid_group
         "flow_group": collection name or None}
    entry
        {"object", "modifier", "kind": "fluid_effector" | "fluid_flow" | "collision",
         "enabled": modifier on in the viewport, "show_render": modifier on in render,
         "active": use_effector / use_inflow / collision.use on the current frame,
         "in_group": in the group (True when no group is set), "acts": enabled and active and in_group,
         "settings": {...}}
    settings: fluid_effector effector_type, surface_distance, subframes, use_plane_init;
    fluid_flow flow_type, flow_behavior, flow_source, particle_system, surface_distance,
    volume_density, subframes, use_plane_init, density, temperature, fuel_amount;
    collision thickness_outer, thickness_inner, damping, cloth_friction, permeability,
    stickiness, use_culling, use_normal, use_particle_kill.
    Lists hold every candidate in the scene, also those outside the group (in_group False), in
    scene object order; target itself is left out.
    """
    present = [s for s in physics_systems(target) if s in _EFFECTOR_TARGETS]
    if system is None:
        if len(present) != 1:
            raise ValueError(f"{target.name}: pass system= (supported systems on it: {present})")
        system = present[0]
    if system not in _EFFECTOR_TARGETS:
        raise ValueError(f"effectors does not cover {system!r}; supported: {list(_EFFECTOR_TARGETS)}")
    group, group_property, owner = _target_settings(target, system, particle_system)
    scene = scene or (target.users_scene[0] if target.users_scene else bpy.context.scene)
    members = _members(group)
    out = {"target": target.name, "system": system,
           "particle_system": owner.name if system.endswith("_particles") else None,
           "group": group.name if group is not None else None, "group_property": group_property,
           "effectors": [], "flows": [], "flow_group": None}
    mantaflow = system.startswith("mantaflow_")
    flow_members = None
    if mantaflow:
        flow_group = owner.domain_settings.fluid_group
        flow_members = _members(flow_group)
        out["flow_group"] = flow_group.name if flow_group is not None else None

    def entry(obj, mod, kind, active, group_names, settings):
        in_group = group_names is None or obj.name in group_names
        enabled = bool(mod.show_viewport)
        return {"object": obj.name, "modifier": mod.name, "kind": kind, "enabled": enabled,
                "show_render": bool(mod.show_render), "active": bool(active), "in_group": in_group,
                "acts": enabled and bool(active) and in_group, "settings": settings}

    for obj in scene.objects:
        if obj == target:
            continue
        for mod in obj.modifiers:
            if mantaflow and mod.type == "FLUID" and mod.fluid_type == "EFFECTOR":
                s = mod.effector_settings
                out["effectors"].append(entry(obj, mod, "fluid_effector", s.use_effector, members, {
                    "effector_type": s.effector_type, "surface_distance": s.surface_distance,
                    "subframes": s.subframes, "use_plane_init": s.use_plane_init}))
            elif mantaflow and mod.type == "FLUID" and mod.fluid_type == "FLOW":
                s = mod.flow_settings
                out["flows"].append(entry(obj, mod, "fluid_flow", s.use_inflow, flow_members, {
                    "flow_type": s.flow_type, "flow_behavior": s.flow_behavior, "flow_source": s.flow_source,
                    "particle_system": s.particle_system.name if s.particle_system else None,
                    "surface_distance": s.surface_distance, "volume_density": s.volume_density,
                    "subframes": s.subframes, "use_plane_init": s.use_plane_init, "density": s.density,
                    "temperature": s.temperature, "fuel_amount": s.fuel_amount}))
            elif not mantaflow and mod.type == "COLLISION":
                c = obj.collision
                out["effectors"].append(entry(obj, mod, "collision", c.use, members, {
                    "thickness_outer": c.thickness_outer, "thickness_inner": c.thickness_inner,
                    "damping": c.damping, "cloth_friction": c.cloth_friction, "permeability": c.permeability,
                    "stickiness": c.stickiness, "use_culling": c.use_culling, "use_normal": c.use_normal,
                    "use_particle_kill": c.use_particle_kill}))
    return out
