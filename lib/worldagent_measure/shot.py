"""Shot coverage: is the event's object actually seen by the camera during its beat? (requires bpy)

    from worldagent_measure import shot

    shot.coverage([(lava, (60, 180)), (crater, (1, 60))], min_visible_fraction=0.2,
                  min_frame_fraction=0.9)

Shot planning must keep the key interaction and outcome visible during the relevant story
interval, and trigger, activation, response, and outcome must be observable from the
specified camera [paper A.1.1 'Shot planning'; paper 3.2.2 'Causal'; skill.md Stage 0
'camera evidence']. For each target and each frame of its interval, surface points of the
evaluated mesh are projected through the scene camera of that frame (camera switches on
markers are followed) and counted as seen when they fall inside the frame, between the
clip planes, and are not hidden behind other geometry (a ray from the camera hits the
target first). The target is covered on a frame when the seen fraction reaches
min_visible_fraction; the target passes when the covered frames reach min_frame_fraction
of its interval. The fraction of the image its seen points span is reported as framing
information.

Limits: occlusion uses scene geometry as solid, so glass or other transparent surfaces count
as blockers; volumes (smoke) are not meshes and cannot be targets. Points are a subsample of
at most max_points vertices, reported with the result. Both thresholds are required
arguments from the row's pass_predicate.
"""

from __future__ import annotations

from typing import Sequence

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from .geometry import depsgraph, set_frame, world_mesh
from .verifier import Check

# Ray offsets in metres so a ray does not stop on the point's own face; numeric only.
_SURFACE_EPS_M = 1e-4


def _sample(points: list, max_points: int) -> list:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]


def seen_fraction(obj, camera, graph, *, max_points: int) -> tuple[float, float, int]:
    """(fraction of sampled points seen, fraction of the image spanned by seen points, points sampled)."""
    scene = bpy.context.scene
    points = _sample(world_mesh(obj, graph).vertices, max_points)
    if not points:
        return 0.0, 0.0, 0
    cam_eval = camera.evaluated_get(graph)
    origin = cam_eval.matrix_world.translation
    forward = (cam_eval.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))).normalized()
    ortho = camera.data.type == "ORTHO"
    clip_start, clip_end = camera.data.clip_start, camera.data.clip_end
    seen = []
    for p in points:
        ndc = world_to_camera_view(scene, cam_eval, p)
        if not (0.0 <= ndc.x <= 1.0 and 0.0 <= ndc.y <= 1.0 and clip_start <= ndc.z <= clip_end):
            continue
        start = p - forward * (p - origin).dot(forward) if ortho else origin
        to_point = p - start
        distance = to_point.length
        if distance <= _SURFACE_EPS_M:
            seen.append(ndc)
            continue
        hit, _, _, _, hit_obj, _ = scene.ray_cast(graph, start, to_point / distance, distance=distance - _SURFACE_EPS_M)
        if hit and hit_obj is not None and getattr(hit_obj, "original", hit_obj).name != obj.name:
            continue
        seen.append(ndc)
    if not seen:
        return 0.0, 0.0, len(points)
    span = (max(s.x for s in seen) - min(s.x for s in seen)) * (max(s.y for s in seen) - min(s.y for s in seen))
    return len(seen) / len(points), span, len(points)


def coverage(targets: Sequence[tuple], *, min_visible_fraction: float, min_frame_fraction: float,
             camera=None, frame_step: int = 1, max_points: int = 400, name: str = "shot_coverage") -> Check:
    """Each (object, (start, end)) target is seen on enough frames of its interval.

    camera      a camera object, or None for the scene camera of each frame
    frame_step  check every n-th frame of each interval (the last frame is always checked)
    """
    if not targets:
        raise ValueError("no targets")
    if not 0 < min_visible_fraction <= 1 or not 0 < min_frame_fraction <= 1:
        raise ValueError("fractions must be in (0, 1]")
    if frame_step < 1 or max_points < 1:
        raise ValueError("frame_step and max_points must be >= 1")
    rows, first_failure = [], None
    for obj, (start, end) in targets:
        if end < start:
            raise ValueError(f"bad interval for {obj.name}: {start}..{end}")
        frames = list(range(start, end + 1, frame_step))
        if frames[-1] != end:
            frames.append(end)
        covered, worst, no_camera, spans = 0, None, [], []
        for frame in frames:
            set_frame(frame)
            cam = camera or bpy.context.scene.camera
            if cam is None:
                no_camera.append(frame)
                continue
            fraction, span, sampled = seen_fraction(obj, cam, depsgraph(), max_points=max_points)
            spans.append(span)
            if fraction >= min_visible_fraction:
                covered += 1
            if worst is None or fraction < worst[1]:
                worst = (frame, fraction, cam.name)
        frame_fraction = covered / len(frames)
        row = {"object": obj.name, "interval": [start, end], "frames_checked": len(frames),
               "frames_covered": covered, "covered_fraction": frame_fraction,
               "worst_frame": worst[0] if worst else None, "worst_seen_fraction": worst[1] if worst else None,
               "max_image_span": max(spans) if spans else 0.0, "frames_without_camera": no_camera}
        rows.append(row)
        if first_failure is None and (no_camera or frame_fraction < min_frame_fraction):
            first_failure = {"object": obj.name, "covered_fraction": frame_fraction,
                             "worst_frame": row["worst_frame"], "worst_seen_fraction": row["worst_seen_fraction"],
                             "camera": worst[2] if worst else None,
                             "problem": "no scene camera" if no_camera else "not seen on enough frames"}
    measurements = {"targets": rows, "min_visible_fraction": min_visible_fraction,
                    "min_frame_fraction": min_frame_fraction, "max_points": max_points, "frame_step": frame_step}
    return Check(name, first_failure is None, measurements, first_failure)
