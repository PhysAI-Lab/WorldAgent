"""Numerical stability over a frame range, rest over a window, and trends and bounds over any per-frame series.

    from worldagent_measure.numerical import rest_displacement, stability, trend, within

    stability([box], frame_list(1, 120), max_displacement_m=0.05, max_speed_m_s=1.2, fps=24)
    rest_displacement([cloth], frame_list(96, 120), max_displacement_m=0.003)
    trend(rows, "total", direction="decreasing", min_total_change=0.5, max_reversal=0.05)
    within(rows, ("z_quantiles", "0.99"), lo=None, hi=2.0)

stability: frames are advanced sequentially and every evaluated vertex is checked for finite
coordinates, per-step displacement and speed, and bounding-box extent
[non-penetration-contract.en.md 'continuous probe'; paper 3.2.2 'Numerical'].
rest_displacement: rest as visible stillness. Every vertex's largest distance over the window
from its position on the window's first frame, against a length (normally screen_m), so solver
jitter that moves nothing on screen does not fail a rest row; the step speed is reported only.
trend/within: `rows` is any per-frame list of dicts (a helper's "frames" rows, gas.density
measurements, liquid.volume rows); `metric` is a key or a key path into each row.
All thresholds are required; they come from the row's pass_predicate.
stability needs bpy; trend and within are pure Python.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

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
    from .geometry import depsgraph, set_frame, world_mesh  # bpy, only inside Blender

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


def rest_displacement(objects: list, frames: list[int], *, max_displacement_m: float,
                      name: str = "rest_displacement") -> Check:
    """No vertex moves more than max_displacement_m from its position on the window's first frame.

    For cloth, soft bodies and rigid bodies coming to rest: the window displacement is the largest
    per-vertex distance from frames[0] over the checked frames (world space, evaluated mesh).
    When an object's vertex count differs from frames[0], its bbox-centre distance is used and
    the change is recorded. The step speed (largest per-vertex distance between consecutive
    checked frames / time, scene fps) is reported next to it, not judged.
    """
    if not objects:
        raise ValueError("no objects")
    if len(frames) < 2 or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be at least two, strictly increasing: {frames}")
    if max_displacement_m < 0:
        raise ValueError("max_displacement_m must be >= 0")
    import bpy  # only inside Blender

    from .geometry import depsgraph, set_frame, world_mesh

    render = bpy.context.scene.render
    fps = render.fps / render.fps_base
    stats = {o.name: {"max_window_displacement_m": 0.0, "max_window_displacement_frame": None,
                      "vertex": None, "max_step_speed_m_s": 0.0, "topology_changes": []} for o in objects}
    origin, previous = {}, {}
    first_failure = None

    def centre(verts):
        box = _bbox(verts)
        return None if box is None else [(box[0][i] + box[1][i]) / 2 for i in range(3)]

    for frame in frames:
        set_frame(frame)
        graph = depsgraph()
        for obj in objects:
            s = stats[obj.name]
            verts = world_mesh(obj, graph).vertices
            if obj.name not in origin:
                origin[obj.name] = verts
                previous[obj.name] = (frame, verts)
                continue
            start = origin[obj.name]
            vertex, disp = None, None
            if len(start) == len(verts):
                dists = [((a - b).length if _finite(a) and _finite(b) else math.inf) for a, b in zip(verts, start)]
                if dists:
                    vertex = max(range(len(dists)), key=dists.__getitem__)
                    disp = dists[vertex]
            else:
                s["topology_changes"].append({"frame": frame, "from": len(start), "to": len(verts)})
                a, b = centre(verts), centre(start)
                disp = math.dist(a, b) if a is not None and b is not None else math.inf
            if disp is not None and disp > s["max_window_displacement_m"]:
                s.update(max_window_displacement_m=disp, max_window_displacement_frame=frame, vertex=vertex)
            if disp is not None and disp > max_displacement_m and first_failure is None:
                first_failure = {"object": obj.name, "frame": frame, "reason": "moved during the rest window",
                                 "value": disp, "limit": max_displacement_m, "vertex": vertex,
                                 "window_start": frames[0]}
            prev_frame, prev_verts = previous[obj.name]
            if len(prev_verts) == len(verts):
                steps = [(a - b).length for a, b in zip(verts, prev_verts) if _finite(a) and _finite(b)]
                if steps:
                    s["max_step_speed_m_s"] = max(s["max_step_speed_m_s"], max(steps) * fps / (frame - prev_frame))
            previous[obj.name] = (frame, verts)

    measurements = {
        "window": [frames[0], frames[-1]],
        "frame_count": len(frames),
        "fps": fps,
        "thresholds": {"max_displacement_m": max_displacement_m},
        "max_window_displacement_m": max(s["max_window_displacement_m"] for s in stats.values()),
        "max_step_speed_m_s": max(s["max_step_speed_m_s"] for s in stats.values()),
        "objects": stats,
    }
    return Check(name, first_failure is None, measurements, first_failure)


def _path(metric: str | Sequence) -> tuple:
    path = (metric,) if isinstance(metric, str) else tuple(metric)
    if not path:
        raise ValueError("empty metric path")
    return path


def _lookup(row: Mapping, path: tuple):
    """The finite number at path in row, or None (missing key, None, non-number, non-finite)."""
    value = row
    for key in path:
        if isinstance(value, Mapping) and key in value:
            value = value[key]
        elif isinstance(value, (list, tuple)) and isinstance(key, int) and -len(value) <= key < len(value):
            value = value[key]
        else:
            return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _series(rows: Sequence[Mapping], path: tuple, frame_key: str):
    """(frames, values, first missing) over rows in the given order."""
    frames = [row.get(frame_key) if isinstance(row, Mapping) else None for row in rows]
    values = [_lookup(row, path) if isinstance(row, Mapping) else None for row in rows]
    missing = next(({"frame": f, "index": i, "reason": "missing value"}
                    for i, (f, v) in enumerate(zip(frames, values)) if v is None), None)
    return frames, values, missing


def trend(rows: Sequence[Mapping], metric: str | Sequence, *, direction: str, min_total_change: float,
          max_reversal: float, frame_key: str = "frame", name: str = "trend") -> Check:
    """A per-frame value moves in one direction: enough in total, never far back.

    direction         "increasing" or "decreasing"
    min_total_change  required (last - first), signed by direction, >= this
    max_reversal      largest single step against direction (between consecutive rows) <= this
    Rows are taken in the order given. A missing, None or non-finite value fails ("missing value").
    """
    if direction not in ("increasing", "decreasing"):
        raise ValueError(f"direction must be 'increasing' or 'decreasing', not {direction!r}")
    if len(rows) < 2:
        raise ValueError(f"trend needs at least two rows, got {len(rows)}")
    if not (math.isfinite(min_total_change) and math.isfinite(max_reversal)) or max_reversal < 0:
        raise ValueError("min_total_change must be finite and max_reversal finite and >= 0")
    path = _path(metric)
    frames, values, missing = _series(rows, path, frame_key)
    measurements = {"metric": ".".join(map(str, path)), "direction": direction, "frames": frames, "values": values,
                    "thresholds": {"min_total_change": min_total_change, "max_reversal": max_reversal},
                    "total_change": None, "max_reversal": None, "max_reversal_frames": None}
    if missing is not None:
        return Check(name, False, measurements, {"metric": measurements["metric"], **missing})
    sign = 1.0 if direction == "increasing" else -1.0
    total = sign * (values[-1] - values[0])
    reversal, at = 0.0, None
    for i in range(1, len(values)):
        back = -sign * (values[i] - values[i - 1])
        if back > reversal:
            reversal, at = back, [frames[i - 1], frames[i]]
    measurements.update(total_change=total, max_reversal=reversal, max_reversal_frames=at)
    first_failure = None
    if total < min_total_change:
        first_failure = {"metric": measurements["metric"], "frame": frames[-1], "reason": "total change",
                         "value": total, "limit": min_total_change, "first_frame": frames[0]}
    elif reversal > max_reversal:
        first_failure = {"metric": measurements["metric"], "frame": at[1], "previous_frame": at[0],
                         "reason": "reversal", "value": reversal, "limit": max_reversal}
    return Check(name, first_failure is None, measurements, first_failure)


def within(rows: Sequence[Mapping], metric: str | Sequence, *, lo: float | None, hi: float | None,
           frame_key: str = "frame", name: str = "within") -> Check:
    """Every per-frame value lies in [lo, hi] (inclusive); None leaves that side open, one bound is required.

    A missing, None or non-finite value fails ("missing value"). first_failure is the first row out of range.
    """
    if lo is None and hi is None:
        raise ValueError("within needs lo, hi or both")
    if any(b is not None and not math.isfinite(b) for b in (lo, hi)) or (lo is not None and hi is not None and lo > hi):
        raise ValueError(f"bounds must be finite with lo <= hi: lo={lo}, hi={hi}")
    if not rows:
        raise ValueError("no rows")
    path = _path(metric)
    frames, values, missing = _series(rows, path, frame_key)
    label = ".".join(map(str, path))
    out = [i for i, v in enumerate(values)
           if v is not None and ((lo is not None and v < lo) or (hi is not None and v > hi))]
    finite = [v for v in values if v is not None]
    measurements = {"metric": label, "frames": frames, "values": values, "thresholds": {"lo": lo, "hi": hi},
                    "min": min(finite) if finite else None, "max": max(finite) if finite else None,
                    "out_of_range_count": len(out), "out_of_range_frames": [frames[i] for i in out]}
    first_failure = None
    if missing is not None or out:
        first = min(([missing["index"]] if missing else []) + out)
        if missing is not None and first == missing["index"]:
            first_failure = {"metric": label, **missing}
        else:
            v = values[first]
            first_failure = {"metric": label, "frame": frames[first], "index": first,
                             "reason": "below lo" if lo is not None and v < lo else "above hi", "value": v,
                             "limit": lo if lo is not None and v < lo else hi}
    return Check(name, first_failure is None, measurements, first_failure)
