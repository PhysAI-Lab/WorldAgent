"""Couplings between solvers in the built scene, and whether each driver was baked first.

    from worldagent_measure import coupling, verifier

    # bake script: one block per topology.solver_graph node, in bake_order
    with coupling.record_bake(case_dir, "c05", manifest, "rigid", frames=range(1, 241)):
        bpy.ops.ptcache.bake_all(bake=True)

    # verifier of the row that covers the coupled beats
    links = coupling.graph_links(manifest)
    order = coupling.bake_evidence(manifest, case_dir, "c05", frames=frames, max_export_deviation_m=0.004,
                                   unverified="report")
    verifier.write_result(args.out, [links, order])

The spec's topology.solver_graph says which node drives which, through which channel
(schemas/coupling_channels.json) [paper A.1.2 'coupling direction ... bake order'; bin/spec_audit.py
checks the graph itself]. This module checks the built scene against it:

scene_links lists every cross-system link Blender will use: Mantaflow effectors and flow
sources (domain effector_group / fluid_group), Collision objects against Cloth and Soft Body
collision collections, Hook modifiers on Cloth and Soft Body objects, force fields against
each target's effector_weights (a FLUID_FLOW field links its source_object domain to the
target: channel field_flow), keyed or MOTOR rigid body constraints and kinematic rigid
bodies (kinematic_boundary), and FLIP obstacles. A collider, effector, flow or hook is
listed only when it moves (rigid body, Cloth/Soft Body, fluid domain, keys or drivers, or a
moving parent); a static one is domain setup. Force fields are always listed.

graph_links compares them with the edges: `undeclared` (a link between objects of different
nodes, or from an object in no node, that no edge declares: e.g. a motor a script keys),
`missing` (a native-channel edge with no link in the scene; scripted_transfer and event_timing
edges need their via objects in the scene and keyed), `wrong_carrier` (an effector or flow
outside the domain's group or switched off, a FLIP obstacle of a moving body without
export_animated_mesh, a Hook below the solver modifier, a Fluid Flow field whose
source_object is not the from node's domain, an edge setting that differs from the scene),
and `unresolved` (FLIP object types that cannot be read: enable FLIP first,
liquid.enable_flip). Structural, no threshold.

record_bake (bake script) writes <case>/reports/bakes/<candidate>.json: per node the
start/end time, Blender version, cache location, a digest of the node's output, and for
each driver the driver's digest at the start of this bake. Recording is recommended, not
required: bake_evidence uses, per driver-first edge, the first evidence available:
1. the driven node's bake record, when it still matches the driven cache: the driver's
   digest now must equal its digest when the driven node was baked ("rebake" otherwise);
2. for a FLIP obstacle edge, the meshes FLIP exported (<cache>/export/export_data.sqlite3):
   the largest distance from an exported vertex to the driver's evaluated surface, at most
   max_export_deviation_m;
3. cache file times: the driver's newest cache file is not newer than the driven node's
   oldest;
4. none: the edge is listed in unverified_edges and the required `unverified` argument
   ("fail" or "report", from the row's pass_predicate) decides.
It also fails when a driver's point cache is outdated or not baked, or the driven node has no
baked output.

Digests: disk caches (Mantaflow cache_directory, FLIP cache, a node `cache` folder) hash the
sorted relative path, size and first 1 MiB of every file, so a change late in a large file
is missed; caches in the .blend hash evaluated matrices (Bullet), world vertex positions
(Cloth, Soft Body) or particle positions, rounded to 1e-6, on the frames given, stepped in
order; input nodes hash their keys and evaluated matrices and field settings per frame.

node_map and read_flip_export need no bpy; everything else requires it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import struct
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Mapping

from .verifier import Check

_CHANNELS = json.loads((Path(__file__).resolve().parents[2] / "schemas" / "coupling_channels.json")
                       .read_text(encoding="utf-8"))
UNORDERED_CHANNELS = {c["channel"] for c in _CHANNELS["channels"] if c["order"] == "none"}
# Edges matched by their via objects, not by a Blender link.
VIA_CHANNELS = ("scripted_transfer", "event_timing")
RECORDS = Path("reports") / "bakes"
FLIP_EXPORT = Path("export") / "export_data.sqlite3"
IN_BLEND = "in .blend"
_HEAD_BYTES = 1 << 20
# effector_weights attribute per field type (the FLUID_FLOW weight is called smokeflow).
_FIELD_WEIGHTS = {"FORCE": "force", "WIND": "wind", "VORTEX": "vortex", "MAGNET": "magnetic",
                  "HARMONIC": "harmonic", "CHARGE": "charge", "LENNARDJ": "lennardjones", "TEXTURE": "texture",
                  "GUIDE": "curve_guide", "BOID": "boid", "TURBULENCE": "turbulence", "DRAG": "drag",
                  "FLUID_FLOW": "smokeflow"}
_MANTAFLOW = ("mantaflow_liquid", "mantaflow_gas")


# --- the graph (no bpy) ------------------------------------------------------------


def node_map(manifest: Mapping) -> dict:
    """The manifest's solver graph as {"nodes": {id: node}, "edges": [edge], "bake_order": [...]}.

    node: {"id", "input" (keyed, force_field or None), "system" (input kind, or the common
    blender_system of its object contracts), "objects", "domain", "cache"}. edge: the spec edge
    with "driver_first" (False for event_timing). Run bin/spec_audit.py first; this raises
    ValueError only when there is no graph or a node or edge has no id.
    """
    topology = manifest.get("topology") if isinstance(manifest.get("topology"), Mapping) else {}
    graph = topology.get("solver_graph")
    if not isinstance(graph, Mapping):
        raise ValueError("manifest has no topology.solver_graph")
    contracts = {o.get("name"): o for o in manifest.get("object_contracts") or [] if isinstance(o, Mapping)}
    nodes = {}
    for node in graph.get("nodes") or []:
        if not (isinstance(node, Mapping) and isinstance(node.get("id"), str)):
            raise ValueError(f"solver_graph node without an id: {node!r}")
        objects = [o for o in node.get("objects") or [] if isinstance(o, str)]
        systems = {contracts.get(o, {}).get("blender_system") for o in objects}
        nodes[node["id"]] = {"id": node["id"], "input": node.get("input"),
                             "system": node.get("input") or (systems.pop() if len(systems) == 1 else None),
                             "objects": objects, "domain": node.get("domain"), "cache": node.get("cache")}
    edges = []
    for edge in graph.get("edges") or []:
        if not (isinstance(edge, Mapping) and isinstance(edge.get("id"), str)):
            raise ValueError(f"solver_graph edge without an id: {edge!r}")
        edges.append({**edge, "driver_first": edge.get("channel") not in UNORDERED_CHANNELS})
    return {"nodes": nodes, "edges": edges, "bake_order": list(graph.get("bake_order") or [])}


def _members(node: Mapping) -> list[str]:
    return node["objects"] + ([node["domain"]] if node.get("domain") else [])


def _nodes_of(gm: Mapping, name: str, *, solver: bool | None = None) -> list[str]:
    return [nid for nid, n in gm["nodes"].items() if name in _members(n)
            and (solver is None or (n["input"] is None) == solver)]


# --- FLIP export database (no bpy) -------------------------------------------------


def _bobj_vertices(blob: bytes) -> list[tuple[float, float, float]]:
    """Vertices of a FLIP bobj blob: int32 n, 3n float32, int32 m, 3m int32 (ffengine/trianglemesh.py)."""
    (n,) = struct.unpack_from("<i", blob, 0)
    coords = struct.unpack_from(f"<{3 * n}f", blob, 4)
    return [coords[i:i + 3] for i in range(0, 3 * n, 3)]


def read_flip_export(db_path: Path, object_name: str, frames: Iterable[int]) -> dict:
    """World-space vertices FLIP exported for object_name on each frame it has, read-only.

    Returns {"motion_type": STATIC | KEYFRAMED | ANIMATED, "frames": {frame: [(x, y, z), ...]}};
    raises KeyError when the database has no such object. STATIC: the one exported mesh on every
    frame; KEYFRAMED: the static mesh times the exported matrix of the frame; ANIMATED: the mesh
    exported for the frame (FLIP objects/flip_fluid_geometry_database.py).
    """
    frames = list(frames)
    uri = f"file:{Path(db_path).resolve()}?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    try:
        row = con.execute("SELECT object_id, object_motion_type FROM object WHERE object_name=?",
                          (object_name,)).fetchone()
        if row is None:
            raise KeyError(object_name)
        oid, motion = row
        out: dict[int, list] = {}
        if motion == "ANIMATED":
            for frame, blob in con.execute("SELECT frame_id, mesh_animated_data FROM mesh_animated WHERE object_id=?",
                                           (oid,)):
                if frame in frames and blob:
                    out[frame] = _bobj_vertices(blob)
        else:
            static = con.execute("SELECT mesh_static_data FROM mesh_static WHERE object_id=?", (oid,)).fetchone()
            vertices = _bobj_vertices(static[0]) if static and static[0] else []
            if motion == "STATIC":
                out = {f: vertices for f in frames}
            else:
                for row in con.execute("SELECT * FROM mesh_keyframed WHERE object_id=?", (oid,)):
                    frame, m = row[2], row[3:19]
                    if frame in frames:
                        out[frame] = [tuple(m[4 * r] * x + m[4 * r + 1] * y + m[4 * r + 2] * z + m[4 * r + 3]
                                            for r in range(3)) for x, y, z in vertices]
        return {"motion_type": motion, "frames": out}
    finally:
        con.close()


# --- scene links (bpy) -------------------------------------------------------------


def _dynamic(obj, seen=None) -> bool:
    """Moves over time: a non-passive or animated rigid body, Cloth/Soft Body, fluid domain, keys or drivers."""
    from .rigid_setup import animated_paths

    body = getattr(obj, "rigid_body", None)
    if body is not None and (body.type == "ACTIVE" or body.kinematic):
        return True
    for mod in getattr(obj, "modifiers", []):
        if mod.type in ("CLOTH", "SOFT_BODY") or (mod.type == "FLUID" and mod.fluid_type == "DOMAIN"):
            return True
    if animated_paths(obj):
        return True
    seen = seen or set()
    parent = obj.parent
    if parent is not None and parent.name not in seen:
        seen.add(obj.name)
        return _dynamic(parent, seen)
    return False


def _flip_type(obj) -> str | None:
    from .systems import _flip_type as flip_type  # same package; add-on or stored value
    return flip_type(obj)


def _flip_export_animated(obj) -> bool | None:
    """flip_fluid.obstacle.export_animated_mesh; the stored value without the add-on (unset: False)."""
    settings = getattr(obj, "flip_fluid", None)
    if settings is not None:
        return bool(settings.obstacle.export_animated_mesh)
    try:
        group = obj.bl_system_properties_get()
    except AttributeError:
        group = obj
    try:
        data = group.get("flip_fluid") if group is not None else None
        obstacle = data.get("obstacle") if data is not None else None
    except (TypeError, AttributeError):
        return None
    return bool(obstacle.get("export_animated_mesh")) if obstacle is not None else False


def _keyed_values(obj, prefix: str) -> dict:
    from .rigid_setup import _animation

    curves, drivers = _animation(obj)
    out = {}
    for fc in curves:
        if fc.data_path.startswith(prefix):
            out.setdefault(fc.data_path, set()).update(round(k.co[1], 9) for k in fc.keyframe_points)
    for path in drivers:
        if path.startswith(prefix):
            out.setdefault(path, set()).add("driver")
    return {k: sorted(v, key=str) for k, v in out.items()}


def scene_links(scene=None) -> list[dict]:
    """Every cross-system link in the scene: {"driver", "driven", "channel", "carrier", "detail"}.

    driver/driven/carrier are object names (carrier: the Fluid Flow field of a field_flow link,
    else None). For Mantaflow the driven object is the domain. Reads the current frame's
    settings (switches can be keyed).
    """
    import bpy

    from . import systems

    scene = scene or bpy.context.scene
    links: list[dict] = []

    def add(driver, driven, channel, carrier=None, **detail):
        links.append({"driver": driver.name, "driven": driven.name, "channel": channel,
                      "carrier": carrier.name if carrier is not None else None, "detail": detail})

    objects = list(scene.objects)
    for obj in objects:
        running = systems.physics_systems(obj)
        for system in running:
            if system in _MANTAFLOW:
                found = systems.effectors(obj, system=system, scene=scene)
                for kind, channel in (("effectors", "effector"), ("flows", "flow_source")):
                    for e in found[kind]:
                        driver = scene.objects[e["object"]]
                        if _dynamic(driver):
                            add(driver, obj, channel, in_group=e["in_group"], acts=e["acts"], enabled=e["enabled"],
                                active=e["active"], group=found["group" if kind == "effectors" else "flow_group"])
            elif system in ("cloth", "soft_body"):
                found = systems.effectors(obj, system=system, scene=scene)
                for e in found["effectors"]:
                    driver = scene.objects[e["object"]]
                    if _dynamic(driver):
                        add(driver, obj, "collision", in_group=e["in_group"], acts=e["acts"], enabled=e["enabled"],
                            active=e["active"], group=found["group"])
        solver_index = next((i for i, m in enumerate(obj.modifiers) if m.type in ("CLOTH", "SOFT_BODY")), None)
        if solver_index is not None:
            for i, mod in enumerate(obj.modifiers):
                if mod.type == "HOOK" and mod.object is not None and _dynamic(mod.object):
                    add(mod.object, obj, "hook", modifier=mod.name, above_solver=i < solver_index,
                        enabled=bool(mod.show_viewport))

    # Force fields against each target's effector weights.
    targets = []
    world = scene.rigidbody_world
    if world is not None and world.enabled and world.collection is not None:
        for body in world.collection.all_objects:
            if body.rigid_body is not None and body.rigid_body.type == "ACTIVE" and not body.rigid_body.kinematic:
                targets.append((body, world.effector_weights, "rigidbody_world.effector_weights"))
    for obj in objects:
        for mod in obj.modifiers:
            if mod.type in ("CLOTH", "SOFT_BODY"):
                targets.append((obj, mod.settings.effector_weights, f'modifiers["{mod.name}"].settings.effector_weights'))
            elif mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
                targets.append((obj, mod.domain_settings.effector_weights,
                                f'modifiers["{mod.name}"].domain_settings.effector_weights'))
            elif mod.type == "PARTICLE_SYSTEM" and mod.particle_system.settings.type == "EMITTER" \
                    and mod.particle_system.settings.physics_type in ("NEWTON", "FLUID"):
                psys = mod.particle_system
                targets.append((obj, psys.settings.effector_weights,
                                f'particle_systems["{psys.name}"].settings.effector_weights'))
    for field_obj in objects:
        field = getattr(field_obj, "field", None)
        if field is None or field.type == "NONE":
            continue
        for target, weights, prop in targets:
            if target == field_obj:
                continue
            if weights.collection is not None and field_obj.name not in weights.collection.all_objects:
                continue
            attr = _FIELD_WEIGHTS.get(field.type)
            weight = weights.all * (getattr(weights, attr) if attr else 1.0)
            if weight == 0:
                continue
            if field.type == "FLUID_FLOW":
                if field.source_object is not None and field.source_object != target:
                    add(field.source_object, target, "field_flow", carrier=field_obj, weight=weight, flow=field.flow,
                        weights=prop)
            else:
                add(field_obj, target, "force_field", type=field.type, strength=field.strength, weight=weight,
                    weights=prop)

    # Keyed or motor constraints, and kinematic rigid bodies, drive Bullet bodies.
    for obj in objects:
        constraint = getattr(obj, "rigid_body_constraint", None)
        if constraint is not None:
            keyed = _keyed_values(obj, "rigid_body_constraint.")
            if constraint.type == "MOTOR" or keyed:
                for body in (constraint.object1, constraint.object2):
                    if body is not None and body.rigid_body is not None and body.rigid_body.type == "ACTIVE":
                        add(obj, body, "kinematic_boundary", constraint_type=constraint.type,
                            enabled=bool(constraint.enabled), keyed_values=keyed)
    if world is not None and world.enabled and world.collection is not None:
        members = list(world.collection.all_objects)
        actives = [b for b in members if b.rigid_body is not None and b.rigid_body.type == "ACTIVE"
                   and not b.rigid_body.kinematic]
        for driver in members:
            body = driver.rigid_body
            if body is not None and (body.kinematic or (body.type == "PASSIVE" and _dynamic(driver))):
                for target in actives:
                    add(driver, target, "kinematic_boundary", rigid_body="kinematic")

    # FLIP obstacles that move.
    domains = [o for o in objects if _flip_type(o) == "TYPE_DOMAIN"]
    for obj in objects:
        if _flip_type(obj) == "TYPE_OBSTACLE" and _dynamic(obj):
            for domain in domains:
                add(obj, domain, "obstacle", export_animated_mesh=_flip_export_animated(obj),
                    rigid_body=obj.rigid_body is not None)
    return links


def _resolve_setting(key: str, via: list[str]):
    """[(object name, RNA path)] a settings key refers to: '<object>.<path>' or a path for every via object."""
    import bpy

    head = key
    while "." in head:
        head = head.rsplit(".", 1)[0]
        if head in bpy.data.objects:
            return [(head, key[len(head) + 1:])]
    return [(name, key) for name in via]


def _same(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-9)
    return a == b


def graph_links(manifest: Mapping, scene=None, *, name: str = "coupling_graph") -> Check:
    """The scene's cross-system links against topology.solver_graph (see the module docstring)."""
    import bpy

    from .rigid_setup import animated_paths

    gm = node_map(manifest)
    nodes = gm["nodes"]
    links = scene_links(scene)
    undeclared, missing, wrong, unchecked = [], [], [], []
    matched: set[str] = set()

    def via(e):
        return [v for v in e.get("via") or [] if isinstance(v, str)]

    def matches(e, link, drivers) -> bool:
        source = nodes.get(e.get("from"))
        if source is None:
            return False
        if e["channel"] in VIA_CHANNELS:  # the script or switch writes the via objects; they need no node
            return link["driver"] in via(e) or link["carrier"] in via(e)
        if e["from"] not in drivers:
            return False
        names = set(e.get("objects") or []) | set(via(e)) | ({source["domain"]} if source.get("domain") else set())
        if e["channel"] == "kinematic_boundary" and source["input"] == "keyed":
            return link["driver"] in names
        return e["channel"] == link["channel"] and (link["driver"] in names or link["carrier"] in names)

    for link in links:
        driven_nodes = _nodes_of(gm, link["driven"], solver=True)
        if not driven_nodes:
            continue  # the driven object is in no solver node: not part of the graph
        to = driven_nodes[0]
        drivers = _nodes_of(gm, link["driver"]) + (_nodes_of(gm, link["carrier"]) if link["carrier"] else [])
        if to in drivers:
            continue  # inside one solver
        target = nodes[to]
        found = [e for e in gm["edges"] if e.get("to") == to and matches(e, link, drivers)
                 and (not e.get("targets") or link["driven"] in e["targets"] or link["driven"] == target["domain"])]
        if not found:
            undeclared.append({**link, "problem": f"{link['channel']} from {link['driver']} to {link['driven']} "
                               f"({to}) is not a solver_graph edge" + ("" if drivers else
                                                                       f"; {link['driver']} is in no node")})
            continue
        matched.update(e["id"] for e in found)
        d = link["detail"]
        problem = None
        if link["channel"] in ("effector", "flow_source", "collision") and not d.get("in_group"):
            problem = f"{link['driver']} is not in {link['driven']}'s group {d.get('group')}"
        elif link["channel"] in ("effector", "flow_source", "collision") and not d.get("acts"):
            problem = f"{link['driver']}'s {link['channel']} is switched off on this frame"
        elif link["channel"] == "obstacle" and d.get("rigid_body") and d.get("export_animated_mesh") is False:
            problem = (f"{link['driver']} is a moving FLIP obstacle without export_animated_mesh; FLIP sees only "
                       "its first pose")
        elif link["channel"] == "hook" and not d.get("above_solver"):
            problem = f"Hook modifier {d.get('modifier')} is below the solver modifier on {link['driven']}"
        if problem:
            wrong.append({**link, "edges": [e["id"] for e in found], "problem": problem})

    for e in gm["edges"]:
        source = nodes.get(e.get("from"))
        if source is None or e.get("to") not in nodes:
            continue
        if e["channel"] in VIA_CHANNELS:
            if not via(e):
                unchecked.append({"edge": e["id"], "reason": "no via objects to check"})
            for v in via(e):
                obj = bpy.data.objects.get(v)
                if obj is None:
                    missing.append({"edge": e["id"], "problem": f"via object {v!r} is not in the scene"})
                elif not animated_paths(obj):
                    missing.append({"edge": e["id"], "problem": f"via object {v} has no keys or drivers"})
        elif e["channel"] == "kinematic_boundary" and source["input"] == "keyed":
            present = [bpy.data.objects.get(o) for o in e.get("objects") or []]
            if not any(o is not None and _dynamic(o) for o in present):
                missing.append({"edge": e["id"], "problem": f"none of {e.get('objects')} is keyed, driven or "
                                "kinematic in the scene"})
        elif e["id"] not in matched:
            missing.append({"edge": e["id"], "problem": f"no {e['channel']} from {e['from']} to {e['to']} in the "
                            "scene"})
        if e["channel"] == "field_flow":
            domain_names = set(_members(source))
            for v in via(e):
                obj = bpy.data.objects.get(v)
                field = getattr(obj, "field", None)
                if obj is None or field is None or field.type != "FLUID_FLOW":
                    wrong.append({"edge": e["id"], "problem": f"{v} is not a Fluid Flow force field"})
                elif field.source_object is None or field.source_object.name not in domain_names:
                    src = field.source_object.name if field.source_object else None
                    wrong.append({"edge": e["id"], "problem": f"{v} reads {src}, not the {e['from']} domain"})
        for key, want in (e.get("settings") or {}).items():
            for obj_name, path in _resolve_setting(key, via(e)):
                obj = bpy.data.objects.get(obj_name)
                try:
                    have = obj.path_resolve(path) if obj is not None else None
                except ValueError:
                    have = None
                if obj is None or have is None:
                    wrong.append({"edge": e["id"], "problem": f"cannot read {path} on {obj_name}"})
                elif not _same(have, want):
                    wrong.append({"edge": e["id"], "problem": f"{obj_name}.{path} is {have}, the spec says {want}"})

    unresolved = [o.name for o in (scene or bpy.context.scene).objects if _flip_type(o) == "UNKNOWN"]
    measurements = {"links": links, "undeclared": undeclared, "missing": missing, "wrong_carrier": wrong,
                    "unchecked": unchecked, "unresolved": unresolved,
                    "flip_addon_registered": hasattr(bpy.types.Object, "flip_fluid")}
    first = None
    for label, items in (("undeclared", undeclared), ("missing", missing), ("wrong_carrier", wrong)):
        if items:
            first = {"kind": label, **{k: v for k, v in items[0].items() if k != "detail"}}
            break
    if first is None and unresolved:
        first = {"kind": "unresolved", "objects": unresolved,
                 "problem": "FLIP object types cannot be read; enable FLIP first (liquid.enable_flip)"}
    return Check(name, first is None, measurements, first)


# --- digests and bake records (bpy) ------------------------------------------------


def _frames(frames) -> list[int]:
    out = [int(f) for f in frames]
    if not out:
        raise ValueError("no frames")
    return out


def _num(x: float) -> str:
    v = round(float(x), 6)
    return f"{(0.0 if v == 0 else v):.6f}"


def _disk_digest(folder: Path) -> str | None:
    files = sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []
    if not files:
        return None
    h = hashlib.sha256()
    for p in files:
        with open(p, "rb") as f:
            head = f.read(_HEAD_BYTES)
        h.update(f"{p.relative_to(folder).as_posix()}\0{p.stat().st_size}\0".encode())
        h.update(hashlib.sha256(head).digest())
    return h.hexdigest()


def _domain_object(node: Mapping):
    import bpy

    for name in _members(node):
        obj = bpy.data.objects.get(name)
        if obj is None:
            continue
        if any(m.type == "FLUID" and m.fluid_type == "DOMAIN" for m in obj.modifiers) or _flip_type(obj) == "TYPE_DOMAIN":
            return obj
    return None


def _cache_dir(node: Mapping, case_dir: Path) -> Path | None:
    """Folder holding the node's baked files, or None for a cache kept in the .blend."""
    import bpy

    if node["system"] in _MANTAFLOW:
        domain = _domain_object(node)
        for mod in getattr(domain, "modifiers", []):
            if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
                return Path(bpy.path.abspath(mod.domain_settings.cache_directory))
    if node["system"] == "flip_fluids":
        domain = _domain_object(node)
        settings = getattr(domain, "flip_fluid", None)
        if settings is not None:
            return Path(settings.domain.cache.get_cache_abspath())
    cache = node.get("cache")
    if isinstance(cache, str) and cache and cache != IN_BLEND:
        return case_dir / cache
    return None


def _state_digest(node: Mapping, frames: list[int]) -> str:
    """sha256 of the node's evaluated state on frames, stepped in order."""
    import bpy

    from .rigid_setup import _animation

    scene = bpy.context.scene
    current = scene.frame_current
    objects = [(name, bpy.data.objects.get(name)) for name in _members(node)]
    h = hashlib.sha256()
    if node["input"] is not None:
        for name, obj in objects:
            curves, drivers = _animation(obj) if obj is not None else ([], set())
            for fc in sorted(curves, key=lambda c: (c.data_path, c.array_index)):
                h.update(f"{name}:{fc.data_path}[{fc.array_index}]".encode())
                h.update(" ".join(_num(v) for k in fc.keyframe_points for v in k.co).encode())
            h.update(" ".join(sorted(drivers)).encode())
    try:
        for frame in frames:
            scene.frame_set(frame)
            graph = bpy.context.evaluated_depsgraph_get()
            h.update(f"#{frame}".encode())
            for name, obj in objects:
                if obj is None:
                    h.update(f"{name}:missing".encode())
                    continue
                ev = obj.evaluated_get(graph)
                h.update(name.encode())
                if node["system"] in ("cloth", "soft_body") and ev.type == "MESH":
                    mesh = ev.to_mesh()
                    try:
                        m = ev.matrix_world
                        h.update(" ".join(_num(c) for v in mesh.vertices for c in (m @ v.co)).encode())
                    finally:
                        ev.to_mesh_clear()
                elif node["system"] in ("newton_particles", "sph_particles"):
                    for psys in ev.particle_systems:
                        h.update(" ".join(_num(c) for p in psys.particles if p.alive_state == "ALIVE"
                                          for c in p.location).encode())
                else:
                    h.update(" ".join(_num(c) for row in ev.matrix_world for c in row).encode())
                    field = getattr(obj, "field", None)
                    if node["input"] is not None and field is not None and field.type != "NONE":
                        src = field.source_object.name if field.source_object else ""
                        h.update(f"{field.type} {_num(field.strength)} {_num(field.flow)} {src}".encode())
    finally:
        scene.frame_set(current)
    return h.hexdigest()


def node_digest(node: Mapping, case_dir: Path, frames: Iterable[int]) -> dict:
    """{"digest": sha256 or None (no baked files), "source": "disk" | "state", "cache": case path or 'in .blend'}."""
    case_dir = Path(case_dir).resolve()
    folder = _cache_dir(node, case_dir)
    if folder is not None:
        try:
            rel = folder.resolve().relative_to(case_dir).as_posix()
        except ValueError:
            rel = str(folder)
        return {"digest": _disk_digest(folder), "source": "disk", "cache": rel}
    return {"digest": _state_digest(node, _frames(frames)), "source": "state", "cache": IN_BLEND}


def _records_path(case_dir: Path, candidate: str) -> Path:
    if not candidate or "/" in candidate or "\\" in candidate:
        raise ValueError(f"candidate must be a plain id, got {candidate!r}")
    return Path(case_dir).resolve() / RECORDS / f"{candidate}.json"


def _read_records(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@contextmanager
def record_bake(case_dir, candidate: str, manifest: Mapping, node: str, *, frames: Iterable[int]):
    """Wrap one node's bake; on success write its record to <case>/reports/bakes/<candidate>.json.

    The record (key = node id): node, started_utc, ended_utc, blender, cache, digest, frames
    [first, last], and drivers {driver node: {"digest_at_bake", "recorded" (the driver's own
    recorded digest or None), "driver_unrecorded"}} for each driver-first edge into the node.
    A driver without a record is noted, never an error; bake_evidence judges. Nothing is written
    when the bake raises.
    """
    import bpy

    case_dir = Path(case_dir).resolve()
    frames = _frames(frames)
    gm = node_map(manifest)
    if node not in gm["nodes"]:
        raise ValueError(f"{node!r} is not a solver_graph node")
    path = _records_path(case_dir, candidate)
    records = _read_records(path)
    drivers = {}
    for e in gm["edges"]:
        if e.get("to") == node and e["driver_first"] and e.get("from") in gm["nodes"] and e["from"] not in drivers:
            own = records.get(e["from"])
            drivers[e["from"]] = {"digest_at_bake": node_digest(gm["nodes"][e["from"]], case_dir, frames)["digest"],
                                  "recorded": own.get("digest") if isinstance(own, dict) else None,
                                  "driver_unrecorded": not isinstance(own, dict)}
    started = _utc()
    yield
    output = node_digest(gm["nodes"][node], case_dir, frames)
    records = _read_records(path)
    records[node] = {"node": node, "started_utc": started, "ended_utc": _utc(), "blender": bpy.app.version_string,
                     "cache": output["cache"], "digest": output["digest"], "digest_source": output["source"],
                     "frames": [frames[0], frames[-1]], "drivers": drivers}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _point_caches(node: Mapping) -> list[tuple[str, object]]:
    """(label, PointCache) of the node's in-Blender solver caches."""
    import bpy

    out = []
    if node["system"] == "bullet_rigid_body":
        world = bpy.context.scene.rigidbody_world
        if world is not None:
            out.append(("rigidbody_world", world.point_cache))
    for name in node["objects"]:
        obj = bpy.data.objects.get(name)
        for mod in getattr(obj, "modifiers", []):
            if mod.type in ("CLOTH", "SOFT_BODY") and node["system"] in ("cloth", "soft_body"):
                out.append((f"{name} {mod.name}", mod.point_cache))
            elif mod.type == "PARTICLE_SYSTEM" and node["system"] in ("newton_particles", "sph_particles"):
                out.append((f"{name} {mod.particle_system.name}", mod.particle_system.point_cache))
    return out


def _file_times(folder: Path | None) -> tuple[float, float] | None:
    files = [p for p in folder.rglob("*") if p.is_file()] if folder is not None and folder.is_dir() else []
    if not files:
        return None
    times = [p.stat().st_mtime for p in files]
    return min(times), max(times)


def _export_deviation(db: Path, objects: list[str], frames: list[int]) -> dict:
    """Largest distance from FLIP's exported obstacle vertices to each driver's evaluated surface."""
    import bpy
    from mathutils import Vector

    from .geometry import set_frame, world_mesh

    per_object, missing, worst = {}, [], None
    for name in objects:
        obj = bpy.data.objects.get(name)
        try:
            exported = read_flip_export(db, name, frames)
        except KeyError:
            missing.append(name)
            continue
        if obj is None:
            missing.append(name)
            continue
        largest = None
        for frame in sorted(exported["frames"]):
            set_frame(frame)
            bvh = world_mesh(obj).bvh()
            for v in exported["frames"][frame]:
                hit = bvh.find_nearest(Vector(v))
                d = hit[3] if hit[0] is not None else math.inf
                if largest is None or d > largest["distance_m"]:
                    largest = {"distance_m": d, "frame": frame}
        per_object[name] = {"motion_type": exported["motion_type"], "frames": len(exported["frames"]),
                            "max": largest}
        if largest and (worst is None or largest["distance_m"] > worst["distance_m"]):
            worst = {"object": name, **largest}
    return {"objects": per_object, "not_exported": missing, "worst": worst}


def bake_evidence(manifest: Mapping, case_dir, candidate: str, *, frames: Iterable[int],
                  max_export_deviation_m: float, unverified: str, name: str = "bake_order") -> Check:
    """Each driver-first edge's driver was baked before its driven node, by the first evidence available.

    frames                  frames the digests and the FLIP export comparison step through
    max_export_deviation_m  FLIP obstacle edges: largest allowed distance from an exported vertex to the
                            driver's evaluated surface (the row's tolerance, e.g. one FLIP cell)
    unverified              "fail" or "report": what an edge with no evidence does to the check
    Digests are taken on the context scene. See the module docstring for the evidence order.
    measurements: {"edges": [...], "unverified_edges", "unverified", "records", "frames"}; each
    edge entry has evidence bake_record | flip_export | file_times | None.
    """
    if unverified not in ("fail", "report"):
        raise ValueError(f'unverified must be "fail" or "report", got {unverified!r}')
    if not (isinstance(max_export_deviation_m, (int, float)) and max_export_deviation_m > 0):
        raise ValueError("max_export_deviation_m must be a positive length")
    frames = _frames(frames)
    case_dir = Path(case_dir).resolve()
    gm = node_map(manifest)
    nodes = gm["nodes"]
    path = _records_path(case_dir, candidate)
    records = _read_records(path)
    digests: dict[str, dict] = {}

    def digest(nid):
        if nid not in digests:
            digests[nid] = node_digest(nodes[nid], case_dir, frames)
        return digests[nid]

    entries, unverified_edges = [], []
    for e in gm["edges"]:
        a, b = e.get("from"), e.get("to")
        if not e["driver_first"] or a not in nodes or b not in nodes:
            continue
        entry = {"edge": e["id"], "from": a, "to": b, "channel": e.get("channel"), "evidence": None, "notes": []}
        entries.append(entry)
        driven, driver = digest(b), digest(a)
        if driven["digest"] is None:
            entry["problem"] = f"{b} has no baked output in {driven['cache']}"
            continue
        for label, pc in _point_caches(nodes[a]):
            if pc.is_outdated or not pc.is_baked:
                entry["problem"] = (f"{a} point cache ({label}) is {'outdated' if pc.is_outdated else 'not baked'} "
                                    f"while {b} is baked: bake {a}, then rebake {b}")
        if "problem" in entry:
            continue
        rec = records.get(b) if isinstance(records.get(b), dict) else None
        drivers = rec.get("drivers") if rec and isinstance(rec.get("drivers"), dict) else {}
        if rec is None:
            entry["notes"].append(f"no bake record for {b}")
        elif rec.get("digest") != driven["digest"]:
            entry["notes"].append(f"the bake record of {b} does not match its current cache")
        elif a not in drivers:
            entry["notes"].append(f"the bake record of {b} has no digest of {a}")
        else:
            entry["evidence"] = "bake_record"
            if drivers[a].get("digest_at_bake") != driver["digest"]:
                entry["problem"] = f"{a} changed after {b} was baked: rebake {b}"
            continue
        if e.get("channel") == "obstacle" and nodes[b]["system"] == "flip_fluids":
            folder = _cache_dir(nodes[b], case_dir)
            db = folder / FLIP_EXPORT if folder is not None else None
            if db is not None and db.is_file():
                deviation = _export_deviation(db, [o for o in e.get("objects") or []], frames)
                entry["export"] = deviation
                worst = deviation["worst"]
                if deviation["not_exported"]:
                    entry["evidence"] = "flip_export"
                    entry["problem"] = f"FLIP exported no mesh for {', '.join(deviation['not_exported'])}: rebake {b}"
                    continue
                if worst is not None:
                    entry["evidence"] = "flip_export"
                    if worst["distance_m"] > max_export_deviation_m:
                        entry["problem"] = (f"{worst['object']} is {worst['distance_m']:.4g} m from the mesh FLIP "
                                            f"exported at frame {worst['frame']} (> {max_export_deviation_m:g} m): "
                                            f"{a} changed after {b} was baked, rebake {b}")
                    continue
                entry["notes"].append("FLIP exported none of the frames")
            else:
                entry["notes"].append(f"no FLIP export database for {b}")
        times_a, times_b = _file_times(_cache_dir(nodes[a], case_dir)), _file_times(_cache_dir(nodes[b], case_dir))
        if times_a and times_b:
            entry["evidence"] = "file_times"
            entry["file_times"] = {"driver_newest": times_a[1], "driven_oldest": times_b[0]}
            if times_a[1] > times_b[0]:
                entry["problem"] = f"{a} cache files are newer than {b}'s: rebake {b}"
            continue
        entry["notes"].append("no cache files to compare" if not (times_a or times_b)
                              else f"{a if not times_a else b} keeps its cache in the .blend")
        unverified_edges.append(e["id"])

    failing = [x for x in entries if "problem" in x]
    measurements = {"edges": entries, "unverified_edges": unverified_edges, "unverified": unverified,
                    "records": path.relative_to(case_dir).as_posix() if path.is_file() else None,
                    "frames": [frames[0], frames[-1], len(frames)]}
    first = None
    if failing:
        first = {k: failing[0][k] for k in ("edge", "from", "to", "evidence", "problem")}
    elif unverified_edges and unverified == "fail":
        first = {"edges": unverified_edges, "problem": "no bake record, FLIP export or cache file times for these "
                 "edges; record bakes with coupling.record_bake"}
    return Check(name, first is None, measurements, first)
