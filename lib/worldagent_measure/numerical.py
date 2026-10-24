"""Numerical stability over a frame range (requires bpy).

    from worldagent_measure.numerical import stability

    stability([box], frame_list(1, 120), max_displacement_m=0.05, max_speed_m_s=1.2, fps=24)

Frames are advanced sequentially and every evaluated vertex is checked for finite
coordinates, per-step displacement and speed, and bounding-box extent
[non-penetration-contract.en.md 'continuous probe'; paper 3.2.2 'Numerical'].
All thresholds are required; they come from the row's pass_predicate.
"""

from __future__ import annotations

import math

from .geometry import depsgraph, set_frame, world_mesh
from .verifier import Check


def _finite(v) -> bool:
    return math.isfinite(v.x) and math.isfinite(v.y) and math.isfinite(v.z)


def _bbox(vertices):
    """AABB of the finite vertices, or None when none are finite."""
    finite = [v for v in vertices if _finite(v)]
    if not finite:
        return None
    lo = [min(v[i] for v in finite) for i in range(3)]
    hi = [max(v[i] for v in finite) for i in range(3)]
    return lo, hi


def stability(objects: list, frames: list[int], max_displacement_m: float, max_speed_m_s: float, fps: float,
              max_extent_m: float | None = None, *, name: str = "stability") -> Check:
    """Finite coordinates, bounded per-step displacement/speed and bounded extent on every checked frame.

    Displacement is the max per-vertex distance between consecutive checked frames when the
    vertex count matches; when it changes, the bbox-center distance is used and the change is
    recorded. speed = displacement / ((frame - previous_frame) / fps). max_extent_m caps the
    largest world AABB edge of each object (explosive growth); None skips that check.
    """
    if not objects:
        raise ValueError("no objects")
    if len(frames) < 2 or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be at least two, strictly increasing: {frames}")
    if fps <= 0 or max_displacement_m < 0 or max_speed_m_s < 0 or (max_extent_m is not None and max_extent_m <= 0):
        raise ValueError("thresholds and fps must be positive")

    stats = {o.name: {"non_finite_vertex_count": 0, "non_finite_frames": [], "max_displacement_m": 0.0,
                      "max_displacement_frame": None, "max_speed_m_s": 0.0, "max_extent_m": 0.0,
                      "max_extent_frame": None, "initial_extent_m": None, "topology_changes": []}
             for o in objects}
    previous = {}
    first_failure = None

    def fail(record: dict) -> None:
        nonlocal first_failure
        if first_failure is None:
            first_failure = record

    for frame in frames:
        set_frame(frame)
        graph = depsgraph()
        for obj in objects:
            s = stats[obj.name]
            verts = world_mesh(obj, graph).vertices
            bad = sum(not _finite(v) for v in verts)
            if bad:
                s["non_finite_vertex_count"] += bad
                s["non_finite_frames"].append(frame)
                fail({"object": obj.name, "frame": frame, "reason": "non-finite coordinates", "value": bad})
            box = _bbox(verts)
            if box is not None:
                extent = max(box[1][i] - box[0][i] for i in range(3))
                if s["initial_extent_m"] is None:
                    s["initial_extent_m"] = extent
                if extent > s["max_extent_m"]:
                    s["max_extent_m"], s["max_extent_frame"] = extent, frame
                if max_extent_m is not None and extent > max_extent_m:
                    fail({"object": obj.name, "frame": frame, "reason": "extent", "value": extent,
                          "max_extent_m": max_extent_m})

            prev = previous.get(obj.name)
            if prev is not None:
                prev_frame, prev_verts, prev_box = prev
                if len(prev_verts) == len(verts):
                    steps = [(a - b).length for a, b in zip(verts, prev_verts) if _finite(a) and _finite(b)]
                    displacement = max(steps) if steps else None
                else:
                    s["topology_changes"].append({"frame": frame, "from": len(prev_verts), "to": len(verts)})
                    displacement = None
                    if box is not None and prev_box is not None:
                        displacement = math.dist([(box[0][i] + box[1][i]) / 2 for i in range(3)],
                                                 [(prev_box[0][i] + prev_box[1][i]) / 2 for i in range(3)])
                if displacement is not None:
                    speed = displacement / ((frame - prev_frame) / fps)
                    if displacement > s["max_displacement_m"]:
                        s["max_displacement_m"], s["max_displacement_frame"] = displacement, frame
                    s["max_speed_m_s"] = max(s["max_speed_m_s"], speed)
                    if displacement > max_displacement_m:
                        fail({"object": obj.name, "frame": frame, "previous_frame": prev_frame,
                              "reason": "displacement", "value": displacement,
                              "max_displacement_m": max_displacement_m})
                    if speed > max_speed_m_s:
                        fail({"object": obj.name, "frame": frame, "previous_frame": prev_frame,
                              "reason": "speed", "value": speed, "max_speed_m_s": max_speed_m_s})
            previous[obj.name] = (frame, verts, box)

    measurements = {
        "frames": [frames[0], frames[-1]],
        "frame_count": len(frames),
        "fps": fps,
        "thresholds": {"max_displacement_m": max_displacement_m, "max_speed_m_s": max_speed_m_s,
                       "max_extent_m": max_extent_m},
        "non_finite_vertex_count": sum(s["non_finite_vertex_count"] for s in stats.values()),
        "max_displacement_m": max(s["max_displacement_m"] for s in stats.values()),
        "max_speed_m_s": max(s["max_speed_m_s"] for s in stats.values()),
        "max_extent_m": max(s["max_extent_m"] for s in stats.values()),
        "objects": stats,
    }
    return Check(name, first_failure is None, measurements, first_failure)
