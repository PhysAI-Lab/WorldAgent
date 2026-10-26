"""Newton particles and DDR-SPH granular approximations: state, cache, counts, colliders, escape, settling, rest (requires bpy).

    from worldagent_measure import particles, verifier

    try:
        checks = [particles.cache_coverage([sand], 1, 120),
                  particles.counts(sand, frames, expected_emitted=4000, expected_alive=None, max_abs_error=0),
                  particles.penetration(sand, [floor], frames, eps_pen=1e-5, radius="size",
                                        max_count=0, max_fraction=0.0)]
    except verifier.CacheMissing as exc:
        checks = [verifier.cache_missing_check("particle_cache", exc)]

A ref is an object, an object name, or (object or name, particle system name); the name may be
left out when the object has one particle system. Hair, keyed and boids systems are not
simulated grains here (ValueError). Physics type NO (static points) has no cache to bake.

State is read from the evaluated object (the original's particle list is empty) with
foreach_get, in world space. Only ALIVE and DYING particles are measured: UNBORN entries hold
garbage positions and DEAD ones have left the scene. Only parent particles are read; child
particles are interpolated for display and are reported by particle_setup, never measured.
Frames are differenced by the helper itself (prev_location is the previous evaluation, not
the previous frame).

A point cache is the solver's state only when it is baked, not outdated, exact, and holds
every frame of its range: a disk cache whose blendcache_ folder is missing still says baked,
with "0 frames on disk." (cache_status reads the cached frame count from point_cache.info and,
for a disk cache, also counts the .bphys files). Reading a frame of a system whose cache has a
problem, or outside its range, raises ParticleCacheMissing (a verifier.CacheMissing).

Every limit and every count/fraction tolerance is a required argument and is echoed in
measurements["thresholds"]. Every function restores scene.frame_current.
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Callable, Iterator, Mapping, NamedTuple, Sequence

import bpy
import numpy as np
from mathutils import Vector
from mathutils.kdtree import KDTree

from . import systems
from .contact import _geometry_key, _Surface
from .geometry import depsgraph, set_frame
from .verifier import CacheMissing, Check

_STATES = {e.identifier: e.value for e in bpy.types.Particle.bl_rna.properties["alive_state"].enum_items}
_MEASURED = (_STATES["ALIVE"], _STATES["DYING"])
_SIMULATED = ("NEWTON", "FLUID", "NO")
_BLENDER_SYSTEMS = {"newton_particles": "NEWTON", "sph_particles": "FLUID"}
# Offenders listed per failing frame; the counts are always complete.
_OFFENDERS = 10
_MAX_RAY_HITS = 1000


class ParticleCacheMissing(CacheMissing):
    """The particle cache is not the solver's state: not baked, outdated, not exact, frames missing, or out of range."""


class ParticleRef(NamedTuple):
    obj: object
    name: str

    @property
    def label(self) -> str:
        return f"{self.obj.name}/{self.name}"

    @property
    def psys(self):
        return self.obj.particle_systems[self.name]


def system(ref) -> ParticleRef:
    """Resolve a ref to (object, particle system name); hair, keyed and boids systems are ValueError."""
    if isinstance(ref, ParticleRef):
        return ref
    name = None
    if isinstance(ref, (tuple, list)):
        if len(ref) != 2:
            raise ValueError(f"a ref is an object or (object, system name), got {ref!r}")
        ref, name = ref
    obj = bpy.data.objects.get(ref) if isinstance(ref, str) else ref
    if obj is None:
        raise ValueError(f"no object {ref!r}")
    found = [p.name for p in getattr(obj, "particle_systems", [])]
    if name is None:
        if len(found) != 1:
            raise ValueError(f"{obj.name} has particle systems {found}; pass (object, system name)")
        name = found[0]
    elif name not in found:
        raise ValueError(f"{obj.name} has no particle system {name!r} (has {found})")
    settings = obj.particle_systems[name].settings
    if settings.type != "EMITTER":
        raise ValueError(f"{obj.name}/{name} is a {settings.type} system, not simulated particles")
    if settings.physics_type not in _SIMULATED:
        raise ValueError(f"{obj.name}/{name}: physics {settings.physics_type} is not measured here")
    return ParticleRef(obj, name)


def _check_frames(frames: Sequence[int]) -> list[int]:
    if not frames or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be non-empty and strictly increasing: {list(frames)}")
    return [int(f) for f in frames]


def _check_tolerance(max_count: int, max_fraction: float) -> None:
    if isinstance(max_count, bool) or not isinstance(max_count, int) or max_count < 0:
        raise ValueError(f"max_count must be an int >= 0, got {max_count!r}")
    if not (isinstance(max_fraction, (int, float)) and 0 <= max_fraction <= 1):
        raise ValueError(f"max_fraction must be in [0, 1], got {max_fraction!r}")


def _vec3(value, label: str) -> np.ndarray:
    v = np.asarray(value, dtype=np.float64)
    if v.shape != (3,) or not np.isfinite(v).all():
        raise ValueError(f"{label} must be three finite numbers, got {value!r}")
    return v


# ---------------------------------------------------------------- cache


def _disk_dir(cache) -> str | None:
    if cache.use_external:
        return bpy.path.abspath(cache.filepath) if cache.filepath else None
    if not bpy.data.filepath:
        return None
    stem = os.path.splitext(os.path.basename(bpy.data.filepath))[0]
    return os.path.join(os.path.dirname(bpy.data.filepath), f"blendcache_{stem}")


def _disk_frames(ref: ParticleRef, cache) -> tuple[str | None, set[int] | None]:
    """(folder, frames with a .bphys file) of a disk or external cache; frames None when the folder is missing."""
    folder = _disk_dir(cache)
    if folder is None or not os.path.isdir(folder):
        return folder, None
    prefix = cache.name or ref.obj.name.encode("utf-8").hex().upper()
    suffix = f"_{cache.index:02d}" if cache.index >= 0 else ""
    pattern = re.compile(re.escape(prefix) + r"_(\d{6})" + re.escape(suffix) + r"\.bphys$")
    frames = set()
    for entry in os.listdir(folder):
        m = pattern.match(entry)
        if m:
            frames.add(int(m.group(1)))
    return folder, frames


def cache_status(ref) -> dict:
    """The point cache of one system and its problems ([] when it is the solver's state over its range).

    Returns frame_start, frame_end, is_baked, is_outdated, info, cached_frames (parsed from info),
    storage "memory" | "disk" | "external", disk_folder and disk_files (frames with a .bphys file
    in the range; disk and external only), physics_type, exempt (physics NO: nothing to bake), problems.
    """
    ref = system(ref)
    psys = ref.psys
    cache = psys.point_cache
    physics = psys.settings.physics_type
    span = cache.frame_end - cache.frame_start + 1
    match = re.match(r"\s*(\d+) frames?", cache.info or "")
    storage = "external" if cache.use_external else "disk" if cache.use_disk_cache else "memory"
    out = {"system": ref.label, "physics_type": physics, "frame_start": cache.frame_start,
           "frame_end": cache.frame_end, "is_baked": bool(cache.is_baked), "is_outdated": bool(cache.is_outdated),
           "info": cache.info, "cached_frames": int(match.group(1)) if match else None, "storage": storage,
           "disk_folder": None, "disk_files": None, "exempt": physics == "NO", "problems": []}
    if out["exempt"]:
        return out
    problems = out["problems"]
    if not cache.is_baked:
        problems.append("not baked")
    if cache.is_outdated:
        problems.append("outdated")
    if "not exact" in (cache.info or ""):
        problems.append("not exact")
    if out["cached_frames"] is None:
        problems.append(f"cache frame count unknown (info {cache.info!r})")
    elif out["cached_frames"] < span:
        problems.append(f"{out['cached_frames']} cached frames for the range {cache.frame_start}-{cache.frame_end}")
    if storage != "memory":
        folder, frames = _disk_frames(ref, cache)
        out["disk_folder"] = folder
        if frames is None:
            problems.append(f"cache folder missing: {folder}")
        else:
            present = len([f for f in frames if cache.frame_start <= f <= cache.frame_end])
            out["disk_files"] = present
            if present < span:
                problems.append(f"{present} cache files for the range {cache.frame_start}-{cache.frame_end}")
    return out


def cache_coverage(refs, frame_start: int, frame_end: int, *, name: str = "particle_cache") -> Check:
    """Every system's cache is the solver's state (cache_status problems empty) and spans [frame_start, frame_end].

    A cache that ends early because every particle is dead from its last frame on (a bake stops
    there) covers the later frames: ended_all_dead is True in its status.
    """
    if frame_end < frame_start:
        raise ValueError("frame_end must be >= frame_start")
    refs = [system(r) for r in (refs if isinstance(refs, list) else [refs])]
    if not refs:
        raise ValueError("no systems")
    per_system, first_failure = {}, None
    for ref in refs:
        status = cache_status(ref)
        if not status["exempt"] and (status["frame_start"] > frame_start or (
                status["frame_end"] < frame_end and not _ended_dead(ref, status))):
            status["problems"].append(f"cache frames {status['frame_start']}-{status['frame_end']} do not cover "
                                      f"{frame_start}-{frame_end}")
        per_system[ref.label] = status
        if status["problems"] and first_failure is None:
            first_failure = {"object": ref.obj.name, "system": ref.name, "reason": status["problems"][0],
                             "problems": status["problems"], "info": status["info"]}
    measurements = {"requested": [frame_start, frame_end], "systems": per_system,
                    "failing_systems": [k for k, s in per_system.items() if s["problems"]]}
    return Check(name, first_failure is None, measurements, first_failure)


# ---------------------------------------------------------------- state


@dataclass
class ParticleState:
    frame: int
    system: str             # "<object>/<system>"
    location: np.ndarray    # (N, 3) float64 world space; non-finite values kept
    velocity: np.ndarray    # (N, 3) m/s
    size: np.ndarray        # (N,) particle size (collision radius with use_size_deflect)
    state: np.ndarray       # (N,) int, DNA alive_state
    birth_time: np.ndarray  # (N,) frames
    die_time: np.ndarray    # (N,) frames

    @property
    def alive(self) -> np.ndarray:
        """ALIVE or DYING: the particles in the world on this frame."""
        return np.isin(self.state, _MEASURED)

    def counts(self) -> dict:
        s = self.state
        return {"unborn": int((s == _STATES["UNBORN"]).sum()), "alive": int((s == _STATES["ALIVE"]).sum()),
                "dying": int((s == _STATES["DYING"]).sum()), "dead": int((s == _STATES["DEAD"]).sum()),
                "total": int(s.size)}


def _read(ref: ParticleRef, frame: int, graph) -> ParticleState:
    psys = ref.obj.evaluated_get(graph).particle_systems[ref.name]
    parts = psys.particles
    n = len(parts)
    vec = np.empty(n * 3, dtype=np.float32)
    out = {}
    for attr in ("location", "velocity"):
        parts.foreach_get(attr, vec)
        out[attr] = vec.reshape(n, 3).astype(np.float64)
    one = np.empty(n, dtype=np.float32)
    for attr in ("size", "birth_time", "die_time"):
        parts.foreach_get(attr, one)
        out[attr] = one.astype(np.float64)
    st = np.empty(n, dtype=np.int32)
    parts.foreach_get("alive_state", st)
    return ParticleState(frame, ref.label, out["location"], out["velocity"], out["size"], st.astype(np.int64),
                         out["birth_time"], out["die_time"])


def _ended_dead(ref: ParticleRef, status: dict) -> bool:
    """The bake stopped at frame_end because every particle was dead there and emission had ended.

    A bake shortens point_cache.frame_end to the last frame with a live particle; later frames
    then hold only dead particles, which is the solver's state, so they may be read.
    """
    if "ended_all_dead" not in status:
        dead = False
        if not status["problems"] and ref.psys.settings.frame_end <= status["frame_end"]:
            start = bpy.context.scene.frame_current
            try:
                set_frame(status["frame_end"])
                st = _read(ref, status["frame_end"], depsgraph())
                dead = bool(st.state.size) and bool((st.state == _STATES["DEAD"]).all())
            finally:
                set_frame(start)
        status["ended_all_dead"] = dead
    return status["ended_all_dead"]


def _require(ref: ParticleRef, status: dict, frame: int) -> None:
    if status["exempt"]:
        return
    if status["problems"]:
        raise ParticleCacheMissing(f"{ref.label}: {'; '.join(status['problems'])} (info {status['info']!r})")
    if frame > status["frame_end"] and _ended_dead(ref, status):
        return
    if not status["frame_start"] <= frame <= status["frame_end"]:
        raise ParticleCacheMissing(f"{ref.label}: frame {frame} is outside the cache "
                                   f"{status['frame_start']}-{status['frame_end']}")


def state(ref, frame: int) -> ParticleState:
    """The system's state on frame; raises ParticleCacheMissing when its cache is not the solver's state there."""
    ref = system(ref)
    status = cache_status(ref)
    _require(ref, status, frame)
    start = bpy.context.scene.frame_current
    try:
        set_frame(frame)
        return _read(ref, frame, depsgraph())
    finally:
        set_frame(start)


def states(refs, frames: Sequence[int]) -> Iterator[dict[str, ParticleState]]:
    """{system label: state} per frame, one frame_set per frame for all systems.

    Every cache is checked before the first frame is read (ParticleCacheMissing). The frame is
    restored when the iterator is exhausted or closed.
    """
    frames = _check_frames(frames)
    refs = [system(r) for r in (refs if isinstance(refs, list) else [refs])]
    statuses = {r.label: cache_status(r) for r in refs}
    for r in refs:
        for f in (frames[0], frames[-1]):
            _require(r, statuses[r.label], f)
    start = bpy.context.scene.frame_current
    try:
        for frame in frames:
            set_frame(frame)
            graph = depsgraph()
            yield {r.label: _read(r, frame, graph) for r in refs}
    finally:
        set_frame(start)


def _series(ref, frames) -> Iterator[ParticleState]:
    ref = system(ref)
    for row in states([ref], frames):
        yield row[ref.label]


def _where(ref: ParticleRef) -> dict:
    return {"object": ref.obj.name, "system": ref.name}


# ---------------------------------------------------------------- setup


def _nearest_spacing(points: np.ndarray) -> float | None:
    """Median nearest-neighbour distance (KD-tree), None for fewer than two points."""
    if len(points) < 2:
        return None
    tree = KDTree(len(points))
    for i, p in enumerate(points):
        tree.insert(Vector(p), i)
    tree.balance()
    return float(np.median([tree.find_n(Vector(p), 2)[1][2] for p in points]))


def particle_setup(ref, *, blender_system: str, fluid_solver: str | None, colliders: Sequence,
                   hidden_emitter: bool, name: str = "particle_setup") -> Check:
    """Structural setup of one system, at the current frame (call before baking, and in the verifier).

    blender_system  the object contract's blender_system: "newton_particles" (physics NEWTON) or
                    "sph_particles" (physics FLUID)
    fluid_solver    "DDR" or "CLASSICAL" for sph_particles (required there), None for newton_particles
    colliders       objects the particles must collide with (names accepted; may be empty)
    hidden_emitter  True when the emitter is sampling geometry that must not render

    Fails, every problem listed: physics type differs from blender_system; FLUID solver differs;
    a listed collider has no Collision modifier, has it off in the viewport, or collision.use is
    off; collision_collection is set and a listed collider is not in it (the particles pass
    through it); hidden_emitter and the emitter renders (not hide_render, show_instancer_for_render
    and visible_camera all on); sph_particles and the emitter has more than one FLUID system.
    Child particles are reported (child_type, child counts), never a failure: only parents are
    simulated and measured.

    solver_length_m is the particle length the tolerance rule uses: Newton, particle_size when
    use_size_deflect is on (the collision radius), else None; SPH, the median nearest-neighbour
    spacing of the alive particles on the current frame (call it on the first emission frame).
    """
    if blender_system not in _BLENDER_SYSTEMS:
        raise ValueError(f"blender_system must be one of {list(_BLENDER_SYSTEMS)}, got {blender_system!r}")
    if blender_system == "sph_particles" and fluid_solver not in ("DDR", "CLASSICAL"):
        raise ValueError(f"sph_particles needs fluid_solver 'DDR' or 'CLASSICAL', got {fluid_solver!r}")
    if blender_system == "newton_particles" and fluid_solver is not None:
        raise ValueError("fluid_solver is only for sph_particles")
    ref = system(ref)
    obj, psys, s = ref.obj, ref.psys, ref.psys.settings
    graph = depsgraph()
    evaluated = obj.evaluated_get(graph).particle_systems[ref.name]
    n = len(evaluated.particles)
    loc = np.empty(n * 3, dtype=np.float32)
    st = np.empty(n, dtype=np.int32)
    evaluated.particles.foreach_get("location", loc)
    evaluated.particles.foreach_get("alive_state", st)
    alive_points = loc.reshape(n, 3).astype(np.float64)[np.isin(st, _MEASURED)]
    spacing = _nearest_spacing(alive_points)
    problems = []

    expected = _BLENDER_SYSTEMS[blender_system]
    settings = {
        "physics_type": s.physics_type, "count": s.count, "particles": n, "emit_from": s.emit_from,
        "distribution": s.distribution, "grid_resolution": s.grid_resolution, "frame_start": s.frame_start,
        "frame_end": s.frame_end, "lifetime": s.lifetime, "lifetime_random": s.lifetime_random,
        "particle_size": s.particle_size, "size_random": s.size_random, "use_size_deflect": s.use_size_deflect,
        "use_die_on_collision": s.use_die_on_collision, "mass": s.mass, "damping": s.damping,
        "drag_factor": s.drag_factor, "brownian_factor": s.brownian_factor, "integrator": s.integrator,
        "timestep": s.timestep, "subframes": s.subframes, "use_adaptive_subframes": s.use_adaptive_subframes,
        "courant_target": s.courant_target, "gravity_weight": s.effector_weights.gravity,
        "collision_collection": s.collision_collection.name if s.collision_collection else None,
        "render_type": s.render_type,
        "instance_object": s.instance_object.name if s.instance_object else None,
        "instance_collection": s.instance_collection.name if s.instance_collection else None,
        "child_type": s.child_type,
        "child_count": {"viewport": s.child_percent, "render": s.rendered_child_count} if s.child_type != "NONE" else None,
        "fluid": None,
    }
    if s.physics_type != expected:
        problems.append(f"physics type {s.physics_type}, the contract's {blender_system} needs {expected}")
    if s.physics_type == "FLUID" and s.fluid is not None:
        f = s.fluid
        settings["fluid"] = {"solver": f.solver, "stiffness": f.stiffness, "repulsion": f.repulsion,
                             "rest_density": f.rest_density, "fluid_radius": f.fluid_radius,
                             "use_factor_radius": f.use_factor_radius, "linear_viscosity": f.linear_viscosity,
                             "stiff_viscosity": f.stiff_viscosity, "spring_force": f.spring_force}
        if fluid_solver is not None and f.solver != fluid_solver:
            problems.append(f"fluid solver {f.solver}, expected {fluid_solver}")
    fluid_systems = [p.name for p in obj.particle_systems
                     if p.settings.type == "EMITTER" and p.settings.physics_type == "FLUID"]
    if blender_system == "sph_particles" and len(fluid_systems) > 1:
        problems.append(f"{len(fluid_systems)} FLUID systems on the emitter {fluid_systems}; one dense body is one system")

    emitter = {"hide_render": obj.hide_render, "show_instancer_for_render": obj.show_instancer_for_render,
               "visible_camera": obj.visible_camera}
    emitter["renders"] = not obj.hide_render and obj.show_instancer_for_render and obj.visible_camera
    if hidden_emitter and emitter["renders"]:
        problems.append("the emitter renders (hide_render off, show_instancer_for_render and visible_camera on)")

    collider_rows = {}
    listed = [bpy.data.objects.get(c) if isinstance(c, str) else c for c in colliders]
    if any(c is None for c in listed):
        raise ValueError(f"collider not found among {list(colliders)}")
    if listed:
        actual = {"NEWTON": "newton_particles", "FLUID": "sph_particles"}.get(s.physics_type)
        entries = {}
        if actual is not None:
            try:
                found = systems.effectors(obj, system=actual, particle_system=ref.name)
                entries = {e["object"]: e for e in found["effectors"]}
            except ValueError as exc:
                problems.append(f"colliders not readable: {exc}")
        for c in listed:
            e = entries.get(c.name)
            row = {"has_collision_modifier": any(m.type == "COLLISION" for m in c.modifiers),
                   "rigid_body": c.rigid_body is not None}
            if e is not None:
                row.update(enabled=e["enabled"], active=e["active"], in_collision_collection=e["in_group"],
                           acts=e["acts"], settings=e["settings"])
                row["settings"].update(damping_factor=c.collision.damping_factor,
                                       friction_factor=c.collision.friction_factor)
            collider_rows[c.name] = row
            if not row["has_collision_modifier"]:
                problems.append(f"collider {c.name} has no Collision modifier"
                                + (" (a rigid body alone does not collide with particles)" if row["rigid_body"] else ""))
            elif e is not None:
                if not e["enabled"]:
                    problems.append(f"collider {c.name}: Collision modifier off in the viewport")
                if not e["active"]:
                    problems.append(f"collider {c.name}: collision.use is off")
                if not e["in_group"]:
                    problems.append(f"collider {c.name} is not in collision_collection {settings['collision_collection']}")

    if s.physics_type == "NEWTON":
        length = s.particle_size if s.use_size_deflect else None
        basis = "particle_size (use_size_deflect)" if s.use_size_deflect else "use_size_deflect off: no collision radius"
    elif s.physics_type == "FLUID":
        length, basis = spacing, "median nearest-neighbour spacing of alive particles on this frame"
    else:
        length, basis = None, f"physics {s.physics_type}"
    measurements = {"system": ref.label, "frame": bpy.context.scene.frame_current, "blender_system": blender_system,
                    "fluid_solver": fluid_solver, "settings": settings, "emitter": emitter,
                    "fluid_systems_on_emitter": fluid_systems, "colliders": collider_rows,
                    "alive_on_frame": int(len(alive_points)), "nearest_spacing_m": spacing,
                    "solver_length_m": length, "solver_length_basis": basis, "problems": problems,
                    "limitations": ["only parent particles are simulated and measured"]
                    if s.child_type != "NONE" else []}
    first_failure = {**_where(ref), "reason": problems[0], "problems": problems} if problems else None
    return Check(name, first_failure is None, measurements, first_failure)


# ---------------------------------------------------------------- counts and safety


def _expected_of(value, label: str) -> Callable[[int], int | None] | None:
    if value is None:
        return None
    if callable(value):
        return value
    if isinstance(value, Mapping):
        return lambda frame: value.get(frame)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} must be an int, a {{frame: n}} mapping, a callable or None")
    return lambda _frame: value


def counts(ref, frames: Sequence[int], *, expected_emitted, expected_alive, max_abs_error: int,
           name: str = "particle_counts") -> Check:
    """Per-frame unborn/alive/dying/dead and emitted (alive + dying + dead) against expected counts.

    expected_emitted, expected_alive  an int, a {frame: n} mapping (frames not in it are not
                                      checked), a callable frame -> n, or None (not checked); at
                                      least one is required
    max_abs_error                     largest allowed |measured - expected|
    Also fails when emitted decreases between checked frames or alive exceeds emitted.
    """
    frames = _check_frames(frames)
    emitted_of = _expected_of(expected_emitted, "expected_emitted")
    alive_of = _expected_of(expected_alive, "expected_alive")
    if emitted_of is None and alive_of is None:
        raise ValueError("counts needs expected_emitted, expected_alive or both")
    if isinstance(max_abs_error, bool) or not isinstance(max_abs_error, int) or max_abs_error < 0:
        raise ValueError("max_abs_error must be an int >= 0")
    ref = system(ref)
    rows, first_failure, previous = [], None, None
    for st in _series(ref, frames):
        c = st.counts()
        row = {"frame": st.frame, **c, "emitted": c["alive"] + c["dying"] + c["dead"],
               "expected_emitted": None, "expected_alive": None}
        problems = []
        for key, of, measured in (("emitted", emitted_of, row["emitted"]),
                                  ("alive", alive_of, c["alive"] + c["dying"])):
            want = of(st.frame) if of is not None else None
            if want is None:
                continue
            row[f"expected_{key}"] = int(want)
            if abs(measured - int(want)) > max_abs_error:
                problems.append({"reason": f"{key} count off", "value": measured, "expected": int(want),
                                 "limit": max_abs_error})
        if previous is not None and row["emitted"] < previous:
            problems.append({"reason": "emitted decreased", "value": row["emitted"], "previous": previous})
        if c["alive"] + c["dying"] > row["emitted"]:
            problems.append({"reason": "alive exceeds emitted", "value": c["alive"] + c["dying"],
                             "emitted": row["emitted"]})
        if problems and first_failure is None:
            first_failure = {**_where(ref), "frame": st.frame, **problems[0]}
        previous = row["emitted"]
        rows.append(row)
    settings = ref.psys.settings
    measurements = {"system": ref.label, "frames": rows, "settings_count": settings.count,
                    "total": rows[-1]["total"], "max_alive": max(r["alive"] + r["dying"] for r in rows),
                    "thresholds": {"max_abs_error": max_abs_error}}
    return Check(name, first_failure is None, measurements, first_failure)


def numeric_safety(ref, frames: Sequence[int], *, bounds_min: Sequence[float], bounds_max: Sequence[float],
                   max_speed_m_s: float, max_step_m: float, name: str = "particle_numeric_safety") -> Check:
    """Alive particles stay finite, inside the bounds box, below a speed and a per-step displacement.

    bounds_min, bounds_max  world box every alive particle must stay in (inclusive)
    max_speed_m_s           largest allowed solver speed
    max_step_m              largest allowed displacement of one particle between consecutive checked
                            frames (alive on both), so it depends on the frame spacing passed
    """
    frames = _check_frames(frames)
    lo, hi = _vec3(bounds_min, "bounds_min"), _vec3(bounds_max, "bounds_max")
    if (lo > hi).any() or max_speed_m_s < 0 or max_step_m < 0:
        raise ValueError("bounds_min must be <= bounds_max and limits >= 0")
    ref = system(ref)
    rows, first_failure, prev = [], None, None
    for st in _series(ref, frames):
        alive = st.alive
        finite = np.isfinite(st.location).all(axis=1) & np.isfinite(st.velocity).all(axis=1)
        bad_finite = np.flatnonzero(alive & ~finite)
        ok = alive & finite
        outside = np.flatnonzero(ok & ~((st.location >= lo) & (st.location <= hi)).all(axis=1))
        speed = np.where(ok, np.linalg.norm(np.where(ok[:, None], st.velocity, 0.0), axis=1), 0.0)
        step = np.zeros(len(st.state))
        if prev is not None:
            both = ok & prev[1] & (len(prev[0]) == len(st.location))
            step = np.where(both, np.linalg.norm(np.where(both[:, None], st.location - prev[0], 0.0), axis=1), 0.0)
        row = {"frame": st.frame, "alive": int(alive.sum()), "non_finite": int(bad_finite.size),
               "outside_bounds": int(outside.size), "max_speed_m_s": float(speed.max(initial=0.0)),
               "max_step_m": float(step.max(initial=0.0)) if prev is not None else None}
        rows.append(row)
        if first_failure is None:
            fast, far = np.flatnonzero(speed > max_speed_m_s), np.flatnonzero(step > max_step_m)
            for reason, idx, value, limit in (
                    ("non-finite position or velocity", bad_finite, None, None),
                    ("outside the bounds", outside, None, [lo.tolist(), hi.tolist()]),
                    ("speed above limit", fast, row["max_speed_m_s"], max_speed_m_s),
                    ("step above limit", far, row["max_step_m"], max_step_m)):
                if idx.size:
                    first_failure = {**_where(ref), "frame": st.frame, "reason": reason, "count": int(idx.size),
                                     "value": value, "limit": limit, "particle": int(idx[0]),
                                     "position": st.location[idx[0]].tolist(),
                                     "particles": idx[:_OFFENDERS].tolist()}
                    break
        prev = (st.location, ok)
    measurements = {"system": ref.label, "frames": rows,
                    "non_finite": sum(r["non_finite"] for r in rows),
                    "outside_bounds_frames": [r["frame"] for r in rows if r["outside_bounds"]],
                    "max_speed_m_s": max(r["max_speed_m_s"] for r in rows),
                    "max_step_m": max((r["max_step_m"] for r in rows if r["max_step_m"] is not None), default=None),
                    "thresholds": {"bounds_min": lo.tolist(), "bounds_max": hi.tolist(),
                                   "max_speed_m_s": max_speed_m_s, "max_step_m": max_step_m}}
    return Check(name, first_failure is None, measurements, first_failure)


# ---------------------------------------------------------------- solids


class _Solid:
    """A solid's _Surface plus numpy bounds and, for a flat open mesh that covers its convex hull, a plane."""

    def __init__(self, obj, graph):
        self.surface = _Surface(obj, graph)
        self.name = self.surface.name
        lo, hi = self.surface.bbox
        self.lo, self.hi = np.array(lo[:]), np.array(hi[:])
        self.plane = None if self.surface.closed else self._plane()

    def _plane(self):
        """(origin, unit normal, 2D basis, hull half-planes) when the mesh is one flat convex sheet, else None."""
        mesh = self.surface.mesh
        v = np.array([p[:] for p in mesh.vertices])
        tris = np.array(mesh.triangles)
        if not len(tris):
            return None
        a, b, c = v[tris[:, 0]], v[tris[:, 1]], v[tris[:, 2]]
        cross = np.cross(b - a, c - a)
        areas = np.linalg.norm(cross, axis=1)
        if (areas <= 0).any():
            return None
        normals = cross / areas[:, None]
        n = normals[0]
        scale = max(float(np.ptp(v, axis=0).max()), 1.0)
        if (normals @ n < 1 - 1e-9).any() or (np.abs((v - v[0]) @ n) > 1e-7 * scale).any():
            return None
        u = np.cross(n, [1.0, 0.0, 0.0] if abs(n[0]) < 0.9 else [0.0, 1.0, 0.0])
        u /= np.linalg.norm(u)
        w = np.cross(n, u)
        pts = np.stack([(v - v[0]) @ u, (v - v[0]) @ w], axis=1)
        hull = _hull(pts)
        if len(hull) < 3:
            return None
        hull_area = 0.5 * abs(np.sum(hull[:, 0] * np.roll(hull[:, 1], -1) - np.roll(hull[:, 0], -1) * hull[:, 1]))
        if abs(0.5 * areas.sum() - hull_area) > 1e-6 * hull_area:
            return None  # holes, overlaps or a concave outline: the BVH handles it
        edges = np.roll(hull, -1, axis=0) - hull
        inward = np.stack([-edges[:, 1], edges[:, 0]], axis=1)  # counter-clockwise hull: left normals point in
        return v[0], n, u, w, hull, inward

    def signed(self, points: np.ndarray) -> np.ndarray:
        """Signed centre distance of each point to the surface, negative inside (contact's inside rule)."""
        out = np.empty(len(points))
        todo = np.ones(len(points), dtype=bool)
        if self.plane is not None and len(points):
            origin, n, u, w, hull, inward = self.plane
            rel = points - origin
            q = np.stack([rel @ u, rel @ w], axis=1)
            inside = (((q[:, None, :] - hull[None, :, :]) * inward[None, :, :]).sum(axis=2) >= 0).all(axis=1)
            out[inside] = rel[inside] @ n
            todo = ~inside
        bvh, closed = self.surface.bvh, self.surface.closed
        for i in np.flatnonzero(todo):
            p = Vector(points[i])
            loc, normal, _, dist = bvh.find_nearest(p)
            if loc is None:
                out[i] = math.inf
                continue
            behind = (p - loc).dot(normal) < 0
            inside = behind and (self.surface.contains(p) if closed else True)
            out[i] = -dist if inside else dist
        return out

    def near(self, points: np.ndarray, grow) -> np.ndarray:
        g = np.asarray(grow, dtype=np.float64)
        g = g[:, None] if g.ndim else g
        return ((points >= self.lo - g) & (points <= self.hi + g)).all(axis=1)


def _hull(points: np.ndarray) -> np.ndarray:
    """Counter-clockwise convex hull (monotone chain) of 2D points."""
    pts = sorted(set(map(tuple, np.round(points, 12))))
    if len(pts) < 3:
        return np.array(pts)

    def half(seq):
        out = []
        for p in seq:
            while len(out) >= 2 and ((out[-1][0] - out[-2][0]) * (p[1] - out[-2][1])
                                     - (out[-1][1] - out[-2][1]) * (p[0] - out[-2][0])) <= 0:
                out.pop()
            out.append(p)
        return out

    lower, upper = half(pts), half(reversed(pts))
    return np.array(lower[:-1] + upper[:-1])


class _Solids:
    """Solids re-read every frame and rebuilt only when their evaluated geometry changes (contact._geometry_key)."""

    def __init__(self, objects):
        self.objects = [bpy.data.objects[o] if isinstance(o, str) else o for o in objects]
        if not self.objects:
            raise ValueError("no solids")
        self.keys, self.solids, self.built = {}, {}, 0

    def update(self, graph) -> list[_Solid]:
        for o in self.objects:
            key = _geometry_key(o, graph)
            if self.keys.get(o.name) != key:
                self.solids[o.name], self.keys[o.name] = _Solid(o, graph), key
                self.built += 1
        return [self.solids[o.name] for o in self.objects]


def _instances(graph, ref: ParticleRef, instancer, mesh_cache: dict):
    """(world vertices list per instance, centres (k,3), ids) of the visible instances of the system or instancer."""
    matrices, ids, meshes = [], [], []
    for inst in graph.object_instances:
        if not inst.is_instance or inst.parent is None:
            continue
        if instancer is not None:
            if inst.parent.name != instancer.name:
                continue
        elif inst.parent.name != ref.obj.name or inst.particle_system is None \
                or inst.particle_system.name != ref.name:
            continue
        if inst.object.type != "MESH":
            raise ValueError(f"instance of {inst.object.name} is a {inst.object.type}, not a mesh")
        key = inst.object.name
        if key not in mesh_cache:
            data = inst.object.data
            co = np.empty(len(data.vertices) * 3, dtype=np.float32)
            data.vertices.foreach_get("co", co)
            mesh_cache[key] = np.c_[co.reshape(-1, 3).astype(np.float64), np.ones(len(data.vertices))]
        matrices.append(np.array(inst.matrix_world))
        ids.append(int(inst.persistent_id[0]))
        meshes.append(key)
    worlds = [(mesh_cache[k] @ m.T)[:, :3] for m, k in zip(matrices, meshes)]
    centres = np.array([m[:3, 3] for m in matrices]).reshape(-1, 3)
    return worlds, centres, np.array(ids, dtype=np.int64)


def _offender(ref, solid_name, index, clearance, centre, position):
    return {"particle": int(index), "other_object": solid_name, "clearance_m": float(clearance),
            "center_clearance_m": None if centre is None or not math.isfinite(centre) else float(centre), "position": list(map(float, position))}


def _exceeds(bad: int, of: int, max_count: int, max_fraction: float) -> bool:
    return bad > max_count or (of > 0 and bad / of > max_fraction)


def penetration(ref, solids: Sequence, frames: Sequence[int], *, eps_pen: float, radius, max_count: int,
                max_fraction: float, instancer=None, name: str = "particle_penetration") -> Check:
    """Alive particles (or their visible instances) inside or closer than their radius to solid meshes.

    radius        "size": each particle's size (the collision radius with use_size_deflect);
                  a float: a radius stated in the spec; "instances": the visible instance mesh,
                  measured at its vertices from depsgraph.object_instances (the instance matrix,
                  never the instanced object's own scale)
    instancer     with radius="instances": the object whose instances are the grains (a Geometry
                  Nodes point object); None: the particle system's own instances
    eps_pen       required; geometry-query noise only, not a penetration allowance
    max_count, max_fraction
                  a frame fails when more than max_count particles, or more than max_fraction of
                  the measured ones (alive particles, or instances), have clearance < -eps_pen

    Clearance = signed centre distance - radius (negative inside). For a closed solid a point is
    inside when the nearest face's back side and ray parity agree; for an open solid (ground,
    terrain) when it is behind the nearest face. Only particles within radius + eps_pen + the
    largest particle size of a solid's AABB are measured (the extra size reports resting
    contact; min_clearance_m is None when nothing came that close), so a particle that went
    through and left an open solid is not seen here: crossings finds it. A flat open sheet
    that covers its convex hull is measured in numpy (same numbers). The centre clearance
    (min_center_clearance_m) is always reported too; in instancer mode it is that of the
    ref's particles.
    """
    frames = _check_frames(frames)
    if not (isinstance(eps_pen, (int, float)) and math.isfinite(eps_pen) and eps_pen >= 0):
        raise ValueError(f"eps_pen must be finite and >= 0, got {eps_pen}")
    _check_tolerance(max_count, max_fraction)
    if isinstance(radius, str):
        if radius not in ("size", "instances"):
            raise ValueError(f"radius must be 'size', 'instances' or a number, got {radius!r}")
    elif not (isinstance(radius, (int, float)) and math.isfinite(radius) and radius >= 0):
        raise ValueError(f"radius must be a finite number >= 0, got {radius!r}")
    if instancer is not None and radius != "instances":
        raise ValueError("instancer is only used with radius='instances'")
    ref = system(ref)
    instancer = bpy.data.objects[instancer] if isinstance(instancer, str) else instancer
    bodies = _Solids(solids)
    mesh_cache = {}
    rows, first_failure = [], None
    min_clear = min_centre = None
    for st in _series(ref, frames):
        graph = depsgraph()
        solid_list = bodies.update(graph)
        alive = np.flatnonzero(st.alive)
        pts = st.location[alive]
        finite = np.isfinite(pts).all(axis=1)
        alive, pts = alive[finite], pts[finite]
        # Report band: clearances of particles up to one particle size beyond the limit are measured
        # too, so resting contact shows its (positive) clearance.
        band = float(st.size[alive].max(initial=0.0))
        if radius == "instances":
            worlds, centres, ids = _instances(graph, ref, instancer, mesh_cache)
            bound = np.array([np.linalg.norm(w - c, axis=1).max() for w, c in zip(worlds, centres)]) \
                if worlds else np.zeros(0)
            own = float(bound.max(initial=0.0))
        else:
            own = st.size[alive] if radius == "size" else float(radius)
        centre = np.full(len(pts), math.inf)
        centre_solid = np.full(len(pts), -1)
        for k, solid in enumerate(solid_list):
            mask = solid.near(pts, np.asarray(own) + eps_pen + band)
            if mask.any():
                d = solid.signed(pts[mask])
                idx = np.flatnonzero(mask)
                better = d < centre[idx]
                centre[idx[better]], centre_solid[idx[better]] = d[better], k
        if radius == "instances":
            clear = np.full(len(worlds), math.inf)
            clear_solid = np.full(len(worlds), -1)
            for k, solid in enumerate(solid_list):
                for i in np.flatnonzero(solid.near(centres, bound + eps_pen + band)):
                    d = solid.signed(worlds[i]).min()
                    if d < clear[i]:
                        clear[i], clear_solid[i] = d, k
            # Particle instances carry the particle index; Geometry Nodes instances are numbered in order.
            measured_ids = np.arange(len(worlds)) if instancer is not None else ids
            positions, measured = centres, len(worlds)
            centre_for = {int(i): c for i, c in zip(alive, centre)} if instancer is None else {}
        else:
            clear, clear_solid = centre - own, centre_solid
            measured_ids, positions, measured = alive, pts, len(pts)
            centre_for = None
        bad = np.flatnonzero(clear < -eps_pen)
        finite_clear = clear[np.isfinite(clear)]
        finite_centre = centre[np.isfinite(centre)]
        row = {"frame": st.frame, "measured": int(measured), "tested_centres": int(np.isfinite(centre).sum()),
               "bad": int(bad.size), "bad_fraction": bad.size / measured if measured else 0.0,
               "min_clearance_m": float(finite_clear.min()) if finite_clear.size else None,
               "min_center_clearance_m": float(finite_centre.min()) if finite_centre.size else None}
        rows.append(row)
        if row["min_clearance_m"] is not None:
            min_clear = row["min_clearance_m"] if min_clear is None else min(min_clear, row["min_clearance_m"])
        if row["min_center_clearance_m"] is not None:
            min_centre = row["min_center_clearance_m"] if min_centre is None \
                else min(min_centre, row["min_center_clearance_m"])
        if first_failure is None and _exceeds(bad.size, measured, max_count, max_fraction):
            order = bad[np.argsort(clear[bad])][:_OFFENDERS]
            offenders = []
            for i in order:
                if centre_for is None:
                    c = centre[i]
                else:
                    c = centre_for.get(int(measured_ids[i]))
                offenders.append(_offender(ref, solid_list[clear_solid[i]].name, measured_ids[i], clear[i],
                                           c, positions[i]))
            worst = offenders[0]
            first_failure = {**_where(ref), "frame": st.frame, "reason": "particles inside or within radius of a solid",
                             "count": int(bad.size), "fraction": row["bad_fraction"],
                             "limit": {"max_count": max_count, "max_fraction": max_fraction},
                             "value": worst["clearance_m"], "particle": worst["particle"],
                             "other_object": worst["other_object"], "position": worst["position"],
                             "first_offenders": offenders}
    settings = ref.psys.settings
    limitations = ["particle centres and the stated radius only; contact below the particle spacing is not resolved"]
    if radius == "instances":
        limitations.append("instances are measured at their mesh vertices")
    measurements = {"system": ref.label, "radius": radius, "instancer": instancer.name if instancer else None,
                    "use_size_deflect": settings.use_size_deflect, "eps_pen_m": eps_pen,
                    "solids": {s.name: {"closed": s.surface.closed, "planar_fast_path": s.plane is not None}
                               for s in bodies.solids.values()},
                    "surfaces_built": bodies.built, "frames": rows,
                    "min_clearance_m": min_clear, "min_center_clearance_m": min_centre,
                    "bad_frames": [r["frame"] for r in rows if r["bad"]],
                    "max_bad": max(r["bad"] for r in rows),
                    "thresholds": {"eps_pen": eps_pen, "max_count": max_count, "max_fraction": max_fraction},
                    "limitations": limitations}
    return Check(name, first_failure is None, measurements, first_failure)


def _ray_hits(surface: _Surface, p0: Vector, direction: Vector, length: float) -> list:
    """(t, normal) of every face hit along p0 + t * direction, 0 <= t <= length."""
    hits, t, origin = [], 0.0, p0
    for _ in range(_MAX_RAY_HITS):
        loc, normal, _, dist = surface.bvh.ray_cast(origin, direction, length - t)
        if loc is None:
            break
        t += dist
        hits.append((t, normal))
        t += surface.ray_step
        if t >= length:
            break
        origin = p0 + direction * t
    return hits


def _depth(surface: _Surface, q: Vector) -> float:
    loc, normal, _, dist = surface.bvh.find_nearest(q)
    if loc is None or (q - loc).dot(normal) >= 0 or not surface.contains(q):
        return 0.0
    return dist


def _crossed(surface: _Surface, p0: np.ndarray, p1: np.ndarray, eps_pen: float) -> bool:
    a, b = Vector(p0), Vector(p1)
    seg = b - a
    length = seg.length
    if length == 0:
        return False
    d = seg / length
    hits = _ray_hits(surface, a, d, length)
    if not surface.closed:
        # Through an open sheet: the end lies on the other side of a hit face by more than eps_pen.
        for t, n in hits:
            hit = a + d * t
            s0, s1 = (a - hit).dot(n), (b - hit).dot(n)
            if s0 * s1 < 0 and abs(s1) > eps_pen:
                return True
        return False
    for k, (t_in, n) in enumerate(hits):
        if d.dot(n) >= 0:
            continue  # leaving, not entering
        t_out = next((t for t, m in hits[k + 1:] if d.dot(m) > 0), None)
        probes = [a + d * (0.5 * (t_in + t_out))] if t_out is not None else [a + d * (0.5 * (t_in + length)), b]
        if max(_depth(surface, q) for q in probes) > eps_pen:
            return True
    return False


def crossings(ref, solids: Sequence, frames: Sequence[int], *, eps_pen: float, max_count: int,
              max_fraction: float, name: str = "particle_crossings") -> Check:
    """Particles whose path between consecutive checked frames went through a solid (tunnelling).

    The path is the straight segment between the helper's own samples of a particle alive on
    both frames. Closed solid: the segment enters a face and reaches deeper than eps_pen inside
    (sampled at the middle of the inside part, and at the end when it ends inside). Open solid:
    the end lies on the other side of a hit face by more than eps_pen.
    max_count, max_fraction: a frame pair fails when more than max_count segments, or more than
    max_fraction of the particles alive on both frames, crossed.
    Particles that died between the frames are counted apart (died_between), not as crossings.
    With frame steps > 1 a particle that crossed and came back between checked frames is not
    seen, and a straight chord can cut a corner the particle went around.
    """
    frames = _check_frames(frames)
    if len(frames) < 2:
        raise ValueError("crossings needs at least two frames")
    if not (isinstance(eps_pen, (int, float)) and math.isfinite(eps_pen) and eps_pen >= 0):
        raise ValueError(f"eps_pen must be finite and >= 0, got {eps_pen}")
    _check_tolerance(max_count, max_fraction)
    ref = system(ref)
    bodies = _Solids(solids)
    rows, first_failure, prev = [], None, None
    total = died = 0
    for st in _series(ref, frames):
        solid_list = bodies.update(depsgraph())
        ok = st.alive & np.isfinite(st.location).all(axis=1)
        if prev is not None:
            p_loc, p_ok, p_frame = prev
            both = np.flatnonzero(p_ok & ok)
            died_now = int((p_ok & ~st.alive).sum())
            died += died_now
            a, b = p_loc[both], st.location[both]
            seg_lo, seg_hi = np.minimum(a, b), np.maximum(a, b)
            crossed = {}
            tested = 0
            for solid in solid_list:
                cand = ((seg_hi >= solid.lo - eps_pen) & (seg_lo <= solid.hi + eps_pen)).all(axis=1)
                cand &= (a != b).any(axis=1)
                for i in np.flatnonzero(cand):
                    if both[i] in crossed:
                        continue
                    tested += 1
                    if _crossed(solid.surface, a[i], b[i], eps_pen):
                        crossed[int(both[i])] = (solid.name, a[i], b[i])
            total += len(crossed)
            row = {"frame": st.frame, "previous_frame": p_frame, "alive_both": int(both.size),
                   "segments_tested": tested, "crossings": len(crossed),
                   "fraction": len(crossed) / both.size if both.size else 0.0, "died_between": died_now}
            rows.append(row)
            if first_failure is None and _exceeds(len(crossed), both.size, max_count, max_fraction):
                offenders = [{"particle": i, "other_object": s, "from": a0.tolist(), "to": b0.tolist()}
                             for i, (s, a0, b0) in list(crossed.items())[:_OFFENDERS]]
                first_failure = {**_where(ref), "frame": st.frame, "previous_frame": p_frame,
                                 "reason": "particles crossed a solid between checked frames",
                                 "count": len(crossed), "fraction": row["fraction"],
                                 "limit": {"max_count": max_count, "max_fraction": max_fraction},
                                 "value": len(crossed), "particle": offenders[0]["particle"],
                                 "other_object": offenders[0]["other_object"], "first_offenders": offenders}
        prev = (st.location, ok, st.frame)
    step_max = max(b - a for a, b in zip(frames, frames[1:]))
    limitations = []
    if step_max > 1:
        limitations.append("a particle that crossed and came back between checked frames is not seen")
    kill = [o.name for o in bodies.objects if any(m.type == "COLLISION" for m in o.modifiers)
            and o.collision.use_particle_kill]
    measurements = {"system": ref.label, "eps_pen_m": eps_pen, "frames": rows, "crossings": total,
                    "died_between": died, "kill_colliders": kill, "frame_step_max": step_max,
                    "solids": {s.name: {"closed": s.surface.closed} for s in bodies.solids.values()},
                    "thresholds": {"eps_pen": eps_pen, "max_count": max_count, "max_fraction": max_fraction},
                    "limitations": limitations}
    return Check(name, first_failure is None, measurements, first_failure)


# ---------------------------------------------------------------- escape, release, rest

_FACES = ("-x", "+x", "-y", "+y", "-z", "+z")


def region_containment(ref, region_min: Sequence[float], region_max: Sequence[float], frames: Sequence[int], *,
                       max_outside: int, name: str = "particle_region") -> Check:
    """Alive particles outside a world box, per frame, and where each one first left it.

    max_outside  largest allowed number of alive particles outside the box (inclusive bounds)
    The exit face of a particle is the side it is farthest beyond on its first outside frame.
    """
    frames = _check_frames(frames)
    lo, hi = _vec3(region_min, "region_min"), _vec3(region_max, "region_max")
    if (lo > hi).any():
        raise ValueError("region_min must be <= region_max")
    if isinstance(max_outside, bool) or not isinstance(max_outside, int) or max_outside < 0:
        raise ValueError("max_outside must be an int >= 0")
    ref = system(ref)
    rows, exits, first_failure = [], {}, None
    for st in _series(ref, frames):
        alive = st.alive
        p = st.location
        below, above = lo - p, p - hi
        beyond = np.where(np.isfinite(p), np.maximum(below, above), np.inf)
        outside = np.flatnonzero(alive & (beyond > 0).any(axis=1))
        for i in outside:
            if int(i) not in exits:
                axis = int(np.argmax(beyond[i]))
                face = _FACES[2 * axis + (1 if above[i][axis] >= below[i][axis] else 0)]
                exits[int(i)] = {"particle": int(i), "frame": st.frame, "face": face, "position": p[i].tolist()}
        row = {"frame": st.frame, "alive": int(alive.sum()), "outside": int(outside.size)}
        rows.append(row)
        if first_failure is None and outside.size > max_outside:
            first = exits[int(outside[0])]
            first_failure = {**_where(ref), "frame": st.frame, "reason": "particles outside the region",
                             "value": int(outside.size), "limit": max_outside, "particle": first["particle"],
                             "face": first["face"], "position": first["position"], "exit_frame": first["frame"]}
    by_face = {f: 0 for f in _FACES}
    for e in exits.values():
        by_face[e["face"]] += 1
    ordered = sorted(exits.values(), key=lambda e: (e["frame"], e["particle"]))
    measurements = {"system": ref.label, "region": [lo.tolist(), hi.tolist()], "frames": rows,
                    "max_outside_measured": max(r["outside"] for r in rows), "exited": len(exits),
                    "exits_by_face": by_face, "first_exit_frame": ordered[0]["frame"] if ordered else None,
                    "first_exits": ordered[:_OFFENDERS], "thresholds": {"max_outside": max_outside}}
    return Check(name, first_failure is None, measurements, first_failure)


def pre_release_motion(ref, frames: Sequence[int], *, release_frame: int, max_displacement_m: float,
                       name: str = "particle_pre_release") -> Check:
    """Before release_frame, no alive particle moves more than max_displacement_m from its first checked position.

    Only frames < release_frame are read; at least one is required. A particle's reference is
    its position on the first checked frame it is alive.
    """
    frames = [f for f in _check_frames(frames) if f < release_frame]
    if not frames:
        raise ValueError(f"no frame before release_frame {release_frame}")
    if max_displacement_m < 0:
        raise ValueError("max_displacement_m must be >= 0")
    ref = system(ref)
    rows, first_failure, origin, seen = [], None, None, None
    for st in _series(ref, frames):
        alive = st.alive & np.isfinite(st.location).all(axis=1)
        if origin is None:
            origin, seen = np.zeros_like(st.location), np.zeros(len(alive), dtype=bool)
        new = alive & ~seen
        origin[new], seen = st.location[new], seen | alive
        disp = np.where(alive, np.linalg.norm(np.where(alive[:, None], st.location - origin, 0.0), axis=1), 0.0)
        row = {"frame": st.frame, "alive": int(alive.sum()), "max_displacement_m": float(disp.max(initial=0.0))}
        rows.append(row)
        moved = np.flatnonzero(disp > max_displacement_m)
        if first_failure is None and moved.size:
            i = int(moved[np.argmax(disp[moved])])
            first_failure = {**_where(ref), "frame": st.frame, "reason": "moved before release",
                             "value": float(disp[i]), "limit": max_displacement_m, "particle": i,
                             "position": st.location[i].tolist(), "count": int(moved.size)}
    measurements = {"system": ref.label, "release_frame": release_frame, "frames": rows,
                    "max_displacement_m": max(r["max_displacement_m"] for r in rows),
                    "thresholds": {"max_displacement_m": max_displacement_m}}
    return Check(name, first_failure is None, measurements, first_failure)


def settling(ref, frames: Sequence[int], *, rest_speed_m_s: float, min_settled_fraction: float, speed_source: str,
             settle_by_frame: int | None, name: str = "particle_settling") -> Check:
    """The fraction of alive particles slower than rest_speed_m_s reaches min_settled_fraction and holds to the last frame.

    speed_source     "solver": the particle velocity; "displacement": distance from the previous
                     checked frame / its time (scene fps), so the first frame has no value
    settle_by_frame  the fraction must hold from this frame or earlier; None: not checked
    time_to_rest_frame is the first checked frame from which the fraction stays >= min_settled_fraction.
    A frame with no alive particles, or no speed, counts as not settled.
    Both sources are instantaneous speeds, so solver jitter that is invisible on screen can fail
    them; for "comes to rest" use rest_displacement, or give rest_speed_m_s a basis (screen_m x fps).
    """
    frames = _check_frames(frames)
    if speed_source not in ("solver", "displacement"):
        raise ValueError(f"speed_source must be 'solver' or 'displacement', got {speed_source!r}")
    if rest_speed_m_s < 0 or not 0 <= min_settled_fraction <= 1:
        raise ValueError("rest_speed_m_s must be >= 0 and min_settled_fraction in [0, 1]")
    ref = system(ref)
    render = bpy.context.scene.render
    fps = render.fps / render.fps_base
    rows, prev = [], None
    for st in _series(ref, frames):
        alive = st.alive & np.isfinite(st.location).all(axis=1)
        speed = None
        if speed_source == "solver":
            speed = np.linalg.norm(st.velocity[alive], axis=1)
        elif prev is not None:
            both = alive & prev[1]
            speed = np.linalg.norm(st.location[both] - prev[0][both], axis=1) * fps / (st.frame - prev[2])
        row = {"frame": st.frame, "alive": int(alive.sum()), "settled_fraction": None, "max_speed_m_s": None,
               "median_speed_m_s": None}
        if speed is not None and speed.size:
            row.update(settled_fraction=float((speed < rest_speed_m_s).mean()), max_speed_m_s=float(speed.max()),
                       median_speed_m_s=float(np.median(speed)))
        rows.append(row)
        prev = (st.location, alive, st.frame)
    rest = None
    for row in reversed(rows):
        if row["settled_fraction"] is None or row["settled_fraction"] < min_settled_fraction:
            break
        rest = row["frame"]
    first_failure = None
    if rest is None:
        last = rows[-1]
        first_failure = {**_where(ref), "frame": last["frame"], "reason": "not settled at the last frame",
                         "value": last["settled_fraction"], "limit": min_settled_fraction}
    elif settle_by_frame is not None and rest > settle_by_frame:
        first_failure = {**_where(ref), "frame": rest, "reason": "settled too late", "value": rest,
                         "limit": settle_by_frame}
    measurements = {"system": ref.label, "speed_source": speed_source, "fps": fps, "frames": rows,
                    "time_to_rest_frame": rest,
                    "thresholds": {"rest_speed_m_s": rest_speed_m_s, "min_settled_fraction": min_settled_fraction,
                                   "settle_by_frame": settle_by_frame}}
    return Check(name, first_failure is None, measurements, first_failure)


def rest_displacement(ref, frames: Sequence[int], *, max_displacement_m: float, min_settled_fraction: float,
                      name: str = "particle_rest_displacement") -> Check:
    """Over the rest window, at least min_settled_fraction of the particles stay within max_displacement_m of where they were.

    Rest as visible stillness: a particle's window displacement is its largest distance, over
    the checked frames, from its position on the first one. Solver velocity jitter that moves
    nothing on screen does not fail this, unlike settling(). max_displacement_m is normally
    screen_m (5 px at the reference resolution at that distance). Particles alive on the first
    frame are tracked; one that dies or turns non-finite inside the window counts as not settled.
    The solver speed and the step speed (distance between consecutive checked frames / time) are
    reported, not judged.
    """
    frames = _check_frames(frames)
    if len(frames) < 2:
        raise ValueError("the rest window needs at least two frames")
    if max_displacement_m < 0 or not 0 <= min_settled_fraction <= 1:
        raise ValueError("max_displacement_m must be >= 0 and min_settled_fraction in [0, 1]")
    ref = system(ref)
    render = bpy.context.scene.render
    fps = render.fps / render.fps_base
    origin = tracked = prev = None
    rows = []
    for st in _series(ref, frames):
        alive = st.alive & np.isfinite(st.location).all(axis=1)
        if origin is None:
            origin, tracked = st.location.copy(), alive.copy()
            window = np.zeros(len(alive))
            lost = np.zeros(len(alive), dtype=bool)
        lost |= tracked & ~alive
        both = tracked & alive
        disp = np.zeros(len(alive))
        disp[both] = np.linalg.norm(st.location[both] - origin[both], axis=1)
        window = np.maximum(window, disp)
        row = {"frame": st.frame, "alive": int(alive.sum()), "max_window_displacement_m": float(disp.max(initial=0.0)),
               "max_solver_speed_m_s": float(np.linalg.norm(st.velocity[both], axis=1).max(initial=0.0)),
               "max_step_speed_m_s": None}
        if prev is not None:
            step = both & prev[1]
            row["max_step_speed_m_s"] = float((np.linalg.norm(st.location[step] - prev[0][step], axis=1)
                                               * fps / (st.frame - prev[2])).max(initial=0.0))
        rows.append(row)
        prev = (st.location, alive, st.frame)
    count = int(tracked.sum())
    moved = tracked & ((window > max_displacement_m) | lost)
    settled = (count - int(moved.sum())) / count if count else None
    first_failure = None
    if not count:
        first_failure = {**_where(ref), "frame": frames[0], "reason": "no alive particles on the first frame",
                         "value": 0, "limit": min_settled_fraction}
    elif settled < min_settled_fraction:
        worst = int(np.argmax(np.where(moved & ~lost, window, -1.0))) if (moved & ~lost).any() else None
        first_failure = {**_where(ref), "frame": frames[-1], "reason": "moved during the rest window",
                         "value": settled, "limit": min_settled_fraction, "count": int(moved.sum()),
                         "lost": int(lost.sum())}
        if worst is not None:
            first_failure.update(particle=worst, displacement_m=float(window[worst]),
                                 position=origin[worst].tolist())
    tracked_window = window[tracked & ~lost]
    measurements = {"system": ref.label, "fps": fps, "window": [frames[0], frames[-1]], "frames": rows,
                    "tracked": count, "lost": int(lost.sum()), "settled_fraction": settled,
                    "max_window_displacement_m": float(tracked_window.max(initial=0.0)),
                    "median_window_displacement_m": float(np.median(tracked_window)) if tracked_window.size else None,
                    "max_solver_speed_m_s": max(r["max_solver_speed_m_s"] for r in rows),
                    "max_step_speed_m_s": max((r["max_step_speed_m_s"] for r in rows[1:]), default=None),
                    "thresholds": {"max_displacement_m": max_displacement_m,
                                   "min_settled_fraction": min_settled_fraction}}
    return Check(name, first_failure is None, measurements, first_failure)
