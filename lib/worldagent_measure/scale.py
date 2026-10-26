"""Metric dimensions and relative size coherence (requires bpy).

    from worldagent_measure.scale import dimensions, pixel_footprint, size_ratios

    dimensions([(table, (1.6, 0.9, 0.75))], rel_tol=0.02)
    size_ratios([(chair, table, 0.4, 0.9)])
    5 * pixel_footprint(camera, crate.location, (1920, 1080))   # screen basis of a tolerance

Dimensions are measured along the object's own axes, scaled: the evaluated
counterpart of Blender's `Object.dimensions`. That is what `object_contracts[].dimensions_m`
records, including for rotated props [non-penetration-contract.en.md 'Scale']. axes="world" measures the world AABB instead.
Tolerances and ratio bounds are required arguments: neither the repo nor the paper
gives rho bounds, so no defaults are provided here.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

from .geometry import depsgraph, world_mesh

AXES_MODES = ("object", "world")
from .verifier import Check

AXES = ("x", "y", "z")
# Only floating-point noise from the 4x4 decomposition, not a design tolerance.
_TRANSFORM_NOISE = 1e-9

MEASURES = ("bbox_diagonal",)


def unapplied_transform(obj) -> dict | None:
    """World scale/rotation that is not identity, or None [simulation-workflow.en.md 4.1, 'apply scale before simulation']."""
    _, rotation, scale = obj.matrix_world.decompose()
    angle = rotation.angle
    scaled = any(not math.isclose(s, 1.0, abs_tol=_TRANSFORM_NOISE) for s in scale)
    rotated = not math.isclose(angle, 0.0, abs_tol=_TRANSFORM_NOISE)
    if not (scaled or rotated):
        return None
    return {"scale": list(scale), "rotation_angle_rad": angle}


def extents(obj, graph=None, axes: str = "object") -> list[float]:
    """Extents of the evaluated mesh: along the object's scaled local axes, or the world AABB."""
    if axes == "world":
        lo, hi = world_mesh(obj, graph).bbox
        return [hi[i] - lo[i] for i in range(3)]
    if axes != "object":
        raise ValueError(f"unknown axes {axes!r}; known: {AXES_MODES}")
    evaluated = obj.evaluated_get(graph or depsgraph())
    mesh = evaluated.to_mesh()
    try:
        if not mesh.vertices:
            raise ValueError(f"{obj.name} has no vertices")
        local = [[v.co[i] for v in mesh.vertices] for i in range(3)]
    finally:
        evaluated.to_mesh_clear()
    scale = evaluated.matrix_world.to_scale()
    return [(max(local[i]) - min(local[i])) * abs(scale[i]) for i in range(3)]


def dimensions(objects_with_expected: list, rel_tol: float, *, fail_on_unapplied: bool = False,
               axes: str = "object", name: str = "dimensions", graph=None) -> Check:
    """Compare evaluated extents to contract dimensions_m per axis [paper Eq. 1 text, 'verify specified dimensions'].

    objects_with_expected: [(obj, (dx, dy, dz)), ...] in metres along the object's axes
    (axes="object", like Object.dimensions) or the world axes (axes="world").
    rel_tol: required relative tolerance, |measured - expected| / expected <= rel_tol.
    Unapplied object scale/rotation is always measured; it fails only with fail_on_unapplied.
    """
    if not objects_with_expected:
        raise ValueError("no objects")
    if rel_tol < 0:
        raise ValueError(f"negative rel_tol {rel_tol}")
    graph = graph or depsgraph()
    per_object = {}
    unapplied = {}
    first_failure = None
    max_rel_error = 0.0
    for obj, expected in objects_with_expected:
        expected = [float(e) for e in expected]
        if len(expected) != 3 or any(e <= 0 for e in expected):
            raise ValueError(f"{obj.name}: expected dimensions must be 3 positive values, got {expected}")
        measured = extents(obj, graph, axes)
        rel_errors = [abs(m - e) / e for m, e in zip(measured, expected)]
        max_rel_error = max(max_rel_error, *rel_errors)
        per_object[obj.name] = {"measured_m": measured, "expected_m": expected, "rel_error": rel_errors}
        if first_failure is None:
            for axis, m, e, err in zip(AXES, measured, expected, rel_errors):
                if err > rel_tol:
                    first_failure = {"object": obj.name, "axis": axis, "value": m, "expected": e,
                                     "rel_error": err, "rel_tol": rel_tol}
                    break
        transform = unapplied_transform(obj)
        if transform is not None:
            unapplied[obj.name] = transform
            if fail_on_unapplied and first_failure is None:
                first_failure = {"object": obj.name, "reason": "unapplied scale/rotation", **transform}
    measurements = {
        "axes": axes,
        "rel_tol": rel_tol,
        "max_rel_error": max_rel_error,
        "objects": per_object,
        "unapplied_transform_count": len(unapplied),
        "unapplied_transform_objects": sorted(unapplied),
        "unapplied_transforms": unapplied,
        "fail_on_unapplied": fail_on_unapplied,
    }
    return Check(name, first_failure is None, measurements, first_failure)


def characteristic_size(obj, measure: str, graph=None) -> float:
    """Same measure and units for every object [paper Eq. 1, 'e.g. bbox diagonal'].

    Uses the object-axis box, so the size does not change when the object is rotated.
    """
    if measure != "bbox_diagonal":
        raise ValueError(f"unknown measure {measure!r}; known: {MEASURES}")
    return math.sqrt(sum(e * e for e in extents(obj, graph)))


def size_ratios(pairs_with_bounds: list, measure: str = "bbox_diagonal", *, name: str = "size_ratios",
                graph=None) -> Check:
    """rho_ij = d_i / d_j must lie in [rho_min, rho_max] for each related pair [paper Eq. 1].

    pairs_with_bounds: [(obj_i, obj_j, rho_min, rho_max), ...]; bounds come from the
    entity contract / spatial relation and are always required.
    """
    if not pairs_with_bounds:
        raise ValueError("no pairs")
    graph = graph or depsgraph()
    sizes = {}
    pairs = []
    first_failure = None
    for obj_i, obj_j, rho_min, rho_max in pairs_with_bounds:
        if not 0 < rho_min <= rho_max:
            raise ValueError(f"{obj_i.name}/{obj_j.name}: bad bounds [{rho_min}, {rho_max}]")
        for obj in (obj_i, obj_j):
            if obj.name not in sizes:
                sizes[obj.name] = characteristic_size(obj, measure, graph)
        d_i, d_j = sizes[obj_i.name], sizes[obj_j.name]
        if d_j <= 0:
            raise ValueError(f"{obj_j.name} has zero {measure}")
        rho = d_i / d_j
        ok = rho_min <= rho <= rho_max
        pairs.append({"pair": [obj_i.name, obj_j.name], "rho": rho, "rho_min": rho_min, "rho_max": rho_max,
                      "passed": ok})
        if not ok and first_failure is None:
            first_failure = {"pair": [obj_i.name, obj_j.name], "object": obj_i.name, "value": rho,
                             "rho_min": rho_min, "rho_max": rho_max}
    measurements = {
        "measure": measure,
        "sizes_m": sizes,
        "pairs": pairs,
        "failed_pair_count": sum(not p["passed"] for p in pairs),
    }
    return Check(name, first_failure is None, measurements, first_failure)


def pixel_footprint(camera, point, resolution=None, *, scene=None, graph=None) -> float:
    """World length of one pixel at `point`, seen from `camera` at `resolution` (metres).

    The screen basis of a distance tolerance is 5 of these at the final delivery resolution
    (schemas/requirements.schema.json `tolerance`). camera: a camera object, or None for the
    scene camera; its evaluated transform at the current frame is used. resolution: (x, y) in
    pixels; None uses the scene's render resolution and percentage. Perspective cameras: the
    length at the point's depth along the view axis; orthographic: the same at every depth.
    Follows Blender's sensor fit (AUTO fits the larger side to sensor_width) and pixel aspect.
    With non-square pixels the shorter side is returned.
    """
    scene = scene or bpy.context.scene
    camera = camera or scene.camera
    if camera is None or camera.type != "CAMERA":
        raise ValueError("no camera object")
    data = camera.data
    if data.type not in ("PERSP", "ORTHO"):
        raise ValueError(f"{camera.name}: {data.type} camera has no single pixel size")
    render = scene.render
    if resolution is None:
        resolution = (render.resolution_x * render.resolution_percentage / 100,
                      render.resolution_y * render.resolution_percentage / 100)
    res_x, res_y = (float(r) for r in resolution)
    if res_x <= 0 or res_y <= 0:
        raise ValueError(f"bad resolution {resolution}")
    aspect_x, aspect_y = res_x * render.pixel_aspect_x, res_y * render.pixel_aspect_y
    fit = data.sensor_fit
    if fit == "AUTO":
        fit = "HORIZONTAL" if aspect_x >= aspect_y else "VERTICAL"
    if data.type == "ORTHO":
        size = data.ortho_scale
    else:
        matrix = camera.evaluated_get(graph or depsgraph()).matrix_world.normalized()
        depth = -(matrix.inverted() @ Vector(point)).z
        if depth <= 0:
            raise ValueError(f"point {tuple(point)} is not in front of {camera.name}")
        sensor = data.sensor_height if data.sensor_fit == "VERTICAL" else data.sensor_width
        size = sensor / data.lens * depth
    if fit == "HORIZONTAL":
        frame_x, frame_y = size, size * aspect_y / aspect_x
    else:
        frame_x, frame_y = size * aspect_x / aspect_y, size
    return min(frame_x / res_x, frame_y / res_y)
