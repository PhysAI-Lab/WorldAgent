"""Recursive support chain and unsupported floating objects (requires bpy).

    from worldagent_measure.support import support_chain

    supports = {o["name"]: o["initial_support"] for o in spec["object_contracts"]}
    support_chain(supports, foundations=["floor"], frames=frame_list(1, 240), contact_tol_m=1e-4)

Every declared support relation must have measured contact on every frame, and
the chain must reach a foundation; `unsupported_floating_count` must be zero.
Mere presence of a collider or AABB proximity does not pass
[non-penetration-contract.en.md step 8]. Contact is measured on evaluated
world-space meshes with vertical rays, so the supporter has to be under the
object's footprint, not beside it (attachments declared by the case use the
surface-to-surface gap instead). contact_tol_m is a required argument: it
classifies coincident contact and floating-point noise only
[non-penetration-contract.en.md 'measurement epsilon'].

Limitation: contact is found where a vertex or face centroid of one surface lies
over the other within contact_tol_m. Two surfaces that touch only along crossing
edges (a box on a thin rod with no vertex over it) are not detected; refine the
mesh or verify such relations separately. Static stability (centre of mass over
the contact region) is not checked here; a simulated object that tips loses
contact on a later frame and fails then.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

import bpy
from mathutils import Vector

from .geometry import depsgraph, set_frame, world_mesh
from .verifier import Check

# initial_support values that mark the object itself as the root of its chain.
FOUNDATION_TOKENS = ("foundation", "ground")

DOWN = Vector((0.0, 0.0, -1.0))
UP = Vector((0.0, 0.0, 1.0))


def closed_mesh(mesh) -> bool:
    """True when every triangle edge is shared by exactly two triangles.

    Only a closed surface has a meaningful inside and outside; an open plane's
    normal may face either way, so its face orientation is not used.
    """
    count: dict = {}
    for tri in mesh.triangles:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            key = (a, b) if a < b else (b, a)
            count[key] = count.get(key, 0) + 1
    return bool(count) and all(c == 2 for c in count.values())


def parse_supporters(value: str | Sequence[str]) -> list[str]:
    """initial_support 'a|b' or ['a', 'b'] -> ['a', 'b']."""
    names = value.split("|") if isinstance(value, str) else list(value)
    names = [n.strip() for n in names if n and n.strip()]
    if not names:
        raise ValueError(f"empty support declaration {value!r}")
    tokens = [n for n in names if n in FOUNDATION_TOKENS]
    if tokens and len(tokens) != len(names):
        raise ValueError(f"{value!r} mixes {tokens} with object names; a foundation token must stand alone")
    return names


def _ray_gaps(sources: list, target_bvh, direction: Vector, start_offset: float,
              target_closed: bool) -> list[tuple[float, Vector]]:
    """Gap along `direction` from each source point to the target surface.

    Each ray starts start_offset behind the point so touching or slightly
    penetrating points still hit. On a closed target only faces turned toward
    the ray count: a back-face hit is the far side of the solid (the ray started
    inside it, or passed along its boundary), not a supporting surface. Deeper
    penetration is the contact check's job [contact module, paper Eq. 2].
    """
    out = []
    for p in sources:
        origin = p - direction * start_offset
        hit, normal, _, dist = target_bvh.ray_cast(origin, direction)
        if hit is None or (target_closed and normal.dot(direction) > 0):
            continue
        out.append((dist - start_offset, p))
    return out


def _sample_points(mesh) -> list:
    centroids = [(mesh.vertices[a] + mesh.vertices[b] + mesh.vertices[c]) / 3 for a, b, c in mesh.triangles]
    return list(mesh.vertices) + centroids


def contact_gap(obj_mesh, supporter_mesh, contact_tol_m: float) -> float:
    """Smallest vertical gap between the object's underside and the supporter's top, in metres.

    Rays go down from the object's vertices and face centroids onto the
    supporter, and up from the supporter's points (inside the object's xy
    footprint) onto the object. inf when no supporter surface lies under the
    object's footprint at all. Negative values mean penetration.
    """
    start_offset = max(4 * contact_tol_m, 1e-3)
    obj_bvh, sup_bvh = obj_mesh.bvh(), supporter_mesh.bvh()
    gaps = [g for g, _ in _ray_gaps(_sample_points(obj_mesh), sup_bvh, DOWN, start_offset,
                                          closed_mesh(supporter_mesh))]
    lo, hi = obj_mesh.bbox
    under = [p for p in _sample_points(supporter_mesh)
             if lo.x <= p.x <= hi.x and lo.y <= p.y <= hi.y and p.z <= hi.z]
    gaps += [g for g, _ in _ray_gaps(under, obj_bvh, UP, start_offset, closed_mesh(obj_mesh))]
    return min(gaps) if gaps else math.inf


def surface_gap(obj_mesh, supporter_mesh) -> float:
    """Smallest surface-to-surface distance in any direction, 0 when the surfaces intersect.

    For attachments (hinge, wall mount, suspension point) where the supporter is
    not below the object. Measured from vertices and face centroids of each mesh
    to the other surface; never from AABBs.
    """
    obj_bvh, sup_bvh = obj_mesh.bvh(), supporter_mesh.bvh()
    if obj_bvh.overlap(sup_bvh):
        return 0.0
    best = math.inf
    for points, tree in ((_sample_points(obj_mesh), sup_bvh), (_sample_points(supporter_mesh), obj_bvh)):
        for p in points:
            hit = tree.find_nearest(p)
            if hit[0] is not None:
                best = min(best, hit[3])
    return best


def declared_cycles(declared: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """Object -> a declared support loop it lies on. A loop never reaches a foundation."""
    out: dict = {}

    def visit(node, path):
        for nxt in declared.get(node, ()):
            if nxt in path:
                loop = path[path.index(nxt):]
                for i, member in enumerate(loop):
                    out.setdefault(member, loop[i:] + loop[:i] + [member])
            elif nxt in declared:
                visit(nxt, path + [nxt])

    for start in declared:
        visit(start, [start])
    return out


def _resolve(name: str):
    obj = bpy.data.objects.get(name)
    if obj is None or obj.type != "MESH":
        return None
    return obj


def support_chain(supports: Mapping[str, str | Sequence[str]], foundations: Iterable[str],
                  frames: Sequence[int], contact_tol_m: float, *, attached: Iterable[str] = (),
                  name: str = "support_chain") -> Check:
    """Recursive support-chain audit over frames [non-penetration-contract.en.md step 8, 'Report' paragraph].

    supports: object name -> declared supporter(s) (`object_contracts[].initial_support`):
        one name, several joined by '|' (every one must be in contact), or
        'foundation'/'ground' for a chain root.
    foundations: supporter names that end a chain (e.g. the floor slab). The
        link to a foundation is measured; the foundation itself is not audited.
    frames: frames to check (use geometry.frame_list for a full range).
    contact_tol_m: required; a gap <= contact_tol_m is contact.
    attached: objects held by a hinge, wall mount, or suspension point rather
        than resting on their supporter [non-penetration-contract.en.md
        'cantilever/suspension point']; their link is the surface-to-surface
        gap in any direction. The case must justify each one.

    An object is unsupported on a frame when any link on its chain lacks
    contact, a declared supporter is missing or not a mesh, a supporter has no
    declared support of its own, or the chain loops.
    """
    if contact_tol_m < 0:
        raise ValueError(f"negative contact_tol_m {contact_tol_m}")
    if not frames:
        raise ValueError("no frames")
    foundations = set(foundations)
    attached = set(attached)
    declared = {obj: parse_supporters(value) for obj, value in supports.items()}
    audited = [o for o, sups in declared.items() if o not in foundations and sups[0] not in FOUNDATION_TOKENS]
    if not audited:
        raise ValueError("no objects to audit: every declared object is a foundation")
    cycles = declared_cycles({o: sups for o, sups in declared.items() if o not in foundations})

    per_object = {o: {"status": "supported", "chain": None, "max_gap_m": None, "first_failure": None}
                  for o in audited}
    first_failure = None

    for frame in frames:
        set_frame(frame)
        graph = depsgraph()
        meshes: dict = {}
        links: dict = {}

        def mesh(n):
            if n not in meshes:
                obj = _resolve(n)
                meshes[n] = world_mesh(obj, graph) if obj is not None else None
            return meshes[n]

        def link_gap(o, s):
            if (o, s) not in links:
                links[(o, s)] = (surface_gap(mesh(o), mesh(s)) if o in attached
                                 else contact_gap(mesh(o), mesh(s), contact_tol_m))
            return links[(o, s)]

        def walk(o):
            """(chain text, failure or None) for object o."""
            if o in foundations or (o in declared and declared[o][0] in FOUNDATION_TOKENS):
                return o, None
            if o in cycles:
                return " -> ".join(cycles[o]), {
                    "object": o, "supporter": cycles[o][1], "reason": "support cycle", "cycle": cycles[o]}
            if o not in declared:
                return o, {"object": o, "supporter": None, "reason": "supporter has no declared support"}
            if mesh(o) is None:
                return o, {"object": o, "supporter": None, "reason": "object missing or not a mesh"}
            parts, failure = [], None
            for s in declared[o]:
                if mesh(s) is None:
                    failure = failure or {"object": o, "supporter": s, "reason": "declared supporter missing or not a mesh"}
                    parts.append(s + " (missing)")
                    continue
                gap = link_gap(o, s)
                if gap > contact_tol_m and failure is None:
                    failure = {"object": o, "supporter": s, "gap_m": gap,
                               "reason": "no attachment contact" if o in attached else "no contact under footprint"}
                sub_chain, sub_failure = walk(s)
                failure = failure or sub_failure
                parts.append(sub_chain)
            below = parts[0] if len(parts) == 1 else "(" + " | ".join(parts) + ")"
            return f"{o} -> {below}", failure

        for o in audited:
            chain, failure = walk(o)
            rec = per_object[o]
            rec["chain"] = rec["chain"] or chain
            own = [link_gap(o, s) for s in declared[o] if mesh(o) is not None and mesh(s) is not None]
            if own:
                worst = max(own)
                if rec["max_gap_m"] is None or worst > rec["max_gap_m"]:
                    rec["max_gap_m"] = worst
                    rec["max_gap_frame"] = frame
            if failure is not None and rec["first_failure"] is None:
                rec["status"] = "unsupported"
                rec["first_failure"] = {"audited_object": o, "frame": frame, **failure}
                if first_failure is None:
                    first_failure = rec["first_failure"]

    unsupported = sum(1 for rec in per_object.values() if rec["status"] != "supported")
    measurements = {
        "objects_checked": len(audited),
        "frames_checked": len(frames),
        "frames": [int(frames[0]), int(frames[-1])],
        "contact_tol_m": contact_tol_m,
        "attached": sorted(attached),
        "unsupported_floating_count": unsupported,
        "per_object": per_object,
    }
    return Check(name, unsupported == 0, measurements, first_failure)
