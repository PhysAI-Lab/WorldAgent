"""Route width, headroom, slope, step height, and continuity (requires bpy).

    from worldagent_measure.navigability import route

    route([(-5, 0, 0), (5, 0, 0)], walkable=[floor], obstacles=[walls, door_frame, sofa],
          min_width_m=0.8, min_headroom_m=2.0, max_slope_deg=10, max_step_m=0.2, sample_step_m=0.05)

Every connection the scene graph requires must keep enough route width,
headroom, walkable slope, and continuity; furniture blocking a doorway fails
[skill.md 'Navigability is in scope as a physical check'; paper notes
'Navigability']. Neither the repo nor the paper gives numeric thresholds,
so every threshold is a required argument.

Method, per sample along the route polyline (evaluated world-space meshes):
    ground    ray down onto `walkable`; none within reach is a gap (continuity)
    step      height change between consecutive ground hits
    slope     angle of the ground face normal from vertical (treads of stairs
              are flat; risers show up as steps, not slope)
    headroom  ray up from the ground against obstacles and walkable
    width     horizontal rays to both sides, perpendicular to the route, at
              several heights between max_step_m (low enough to step over)
              and min_headroom_m; width = left + right distance
A ray whose first hit is a back face of a closed mesh started inside that
solid, so that clearance is 0 (an obstacle standing on the route). This needs
outward face normals on closed meshes; open meshes (planes) never count as inside.
A gap or step narrower than sample_step_m can fall between samples.
"""

from __future__ import annotations

import math
from typing import Sequence

import bpy
from mathutils import Vector

from .geometry import depsgraph, set_frame, world_mesh
from .support import closed_mesh
from .verifier import Check

DOWN = Vector((0.0, 0.0, -1.0))
UP = Vector((0.0, 0.0, 1.0))
# Lift ray origins off the hit surface so they do not hit it again; numeric only.
_SURFACE_LIFT = 1e-4


def _objects(items) -> list:
    out = []
    for item in items:
        obj = bpy.data.objects.get(item) if isinstance(item, str) else item
        if obj is None:
            raise ValueError(f"object {item!r} not found")
        out.append(obj)
    return out


def _trees(objects, graph) -> list:
    out = []
    for obj in objects:
        mesh = world_mesh(obj, graph)
        out.append((obj.name, mesh.bvh(), closed_mesh(mesh)))
    return out


def _cast(trees: list, origin: Vector, direction: Vector, distance: float | None = None):
    """Nearest hit over (name, bvh, closed) trees: (distance, name, normal, inside) or None."""
    best = None
    for tree_name, tree, closed in trees:
        hit, normal, _, dist = (tree.ray_cast(origin, direction) if distance is None
                                else tree.ray_cast(origin, direction, distance))
        if hit is not None and (best is None or dist < best[0]):
            best = (dist, tree_name, normal, closed and normal.dot(direction) > 0)
    return best


def sample_polyline(points: Sequence, step: float) -> list[tuple[Vector, Vector]]:
    """(point, horizontal unit direction) every `step` metres, endpoints included."""
    pts = [Vector((float(p[0]), float(p[1]), float(p[2]) if len(p) > 2 else 0.0)) for p in points]
    if len(pts) < 2:
        raise ValueError("a route needs at least two points")
    out = []
    for i, (a, b) in enumerate(zip(pts, pts[1:])):
        seg = b - a
        flat = Vector((seg.x, seg.y, 0.0))
        if flat.length == 0:
            raise ValueError(f"route segment {i} has no horizontal extent")
        direction = flat.normalized()
        n = max(1, math.ceil(flat.length / step))
        first = 0 if i == 0 else 1
        out += [(a.lerp(b, k / n), direction) for k in range(first, n + 1)]
    return out


def route(path_points: Sequence, walkable: Sequence, obstacles: Sequence, min_width_m: float,
          min_headroom_m: float, max_slope_deg: float, max_step_m: float, sample_step_m: float, *,
          frame: int | None = None, name: str = "route") -> Check:
    """Measure one required route [skill.md 'route width, headroom, walkable slope, and continuity'].

    path_points: route polyline [(x, y, z), ...]; z is the nominal walking
        surface height (ground is searched from z + min_headroom_m down to
        z - max_step_m), e.g. door and region centres from the scene graph.
    walkable: objects (or names) that can be walked on: floors, ramps, steps.
    obstacles: objects (or names) that block: walls, door frames, ceilings, furniture.
    All thresholds are required; sample_step_m is the spacing along the route.
    """
    for label, value in (("min_width_m", min_width_m), ("min_headroom_m", min_headroom_m),
                         ("max_step_m", max_step_m), ("sample_step_m", sample_step_m)):
        if value <= 0:
            raise ValueError(f"{label} must be positive, got {value}")
    if not 0 <= max_slope_deg < 90:
        raise ValueError(f"max_slope_deg must be in [0, 90), got {max_slope_deg}")
    if max_step_m >= min_headroom_m:
        raise ValueError("max_step_m must be below min_headroom_m")
    if frame is not None:
        set_frame(frame)
    graph = depsgraph()
    ground_trees = _trees(_objects(walkable), graph)
    block_trees = _trees(_objects(obstacles), graph) + ground_trees
    if not ground_trees:
        raise ValueError("no walkable objects")

    samples = sample_polyline(path_points, sample_step_m)
    n_heights = max(2, math.ceil((min_headroom_m - max_step_m) / sample_step_m) + 1)
    heights = [max_step_m + (min_headroom_m - max_step_m) * k / (n_heights - 1) for k in range(n_heights)]
    heights[0] += _SURFACE_LIFT
    heights[-1] -= _SURFACE_LIFT

    min_width = math.inf
    min_headroom = math.inf
    max_slope = 0.0
    max_step = 0.0
    first_failure = None
    prev_ground = None

    def fail(index, point, check, value, obj=None, **extra):
        nonlocal first_failure
        if first_failure is None:
            first_failure = {"sample": index, "point": list(point), "check": check, "value": value,
                             "object": obj, "frame": frame, **extra}

    for index, (point, direction) in enumerate(samples):
        hit = _cast(ground_trees, point + UP * min_headroom_m, DOWN, min_headroom_m + max_step_m)
        if hit is None:
            fail(index, point, "ground", None, reason="no walkable surface within reach")
            prev_ground = None
            continue
        dist, ground_name, normal, _ = hit
        ground = point + UP * (min_headroom_m - dist)

        slope = math.degrees(math.acos(min(1.0, abs(normal.z))))
        max_slope = max(max_slope, slope)
        if slope > max_slope_deg:
            fail(index, ground, "slope", slope, ground_name, limit=max_slope_deg)

        if prev_ground is not None:
            step = abs(ground.z - prev_ground.z)
            max_step = max(max_step, step)
            if step > max_step_m:
                fail(index, ground, "step", step, ground_name, limit=max_step_m)
        prev_ground = ground

        up = _cast(block_trees, ground + UP * _SURFACE_LIFT, UP)
        headroom = math.inf if up is None else (0.0 if up[3] else up[0] + _SURFACE_LIFT)
        min_headroom = min(min_headroom, headroom)
        if headroom < min_headroom_m:
            fail(index, ground, "headroom", headroom, up[1], limit=min_headroom_m)

        side = Vector((-direction.y, direction.x, 0.0))
        for h in heights:
            origin = ground + UP * h
            left = _cast(block_trees, origin, side)
            right = _cast(block_trees, origin, -side)
            inside = next((r for r in (left, right) if r is not None and r[3]), None)
            if inside is not None:
                width = 0.0
                blocker = inside[1]
            else:
                width = (math.inf if left is None else left[0]) + (math.inf if right is None else right[0])
                blocker = min((r for r in (left, right) if r is not None), key=lambda r: r[0], default=(0, None))[1]
            min_width = min(min_width, width)
            if width < min_width_m:
                fail(index, ground, "width", width, blocker, height_m=h, limit=min_width_m)
                break

    measurements = {
        "samples": len(samples),
        "route_length_m": sum((Vector(b[:2]) - Vector(a[:2])).length for a, b in zip(path_points, path_points[1:])),
        "min_width_m": min_width,
        "min_headroom_m": min_headroom,
        "max_slope_deg": max_slope,
        "max_step_m": max_step,
        "thresholds": {"min_width_m": min_width_m, "min_headroom_m": min_headroom_m,
                       "max_slope_deg": max_slope_deg, "max_step_m": max_step_m, "sample_step_m": sample_step_m},
        "first_blocking_sample": None if first_failure is None else first_failure["sample"],
    }
    return Check(name, first_failure is None, measurements, first_failure)
