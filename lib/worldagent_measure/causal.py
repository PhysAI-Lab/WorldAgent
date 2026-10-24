"""Causal order of story events, and response to contact (requires bpy).

    from worldagent_measure import causal

    hit = causal.contact_onset(ball, glass, frames, contact_tol_m=1e-3)
    near = causal.closest_approach(ball, glass, frames)   # when hit is None: how close they came
    leak = causal.region_entry(water, (-1, -1, 0), (1, 1, 0.2), frames)
    causal.order({"impact": hit, "leak": leak}, [("impact", "leak")], min_lag_frames=1)

The world story says which event must come before which [paper A.1.1 'temporal/causal
dependency'; paper 3.2.2 'Causal': cause before effect, checked through contact, response,
and temporal order]. The detectors below measure when an event first happens on the
evaluated scene; `order` then checks every declared cause-effect pair and, optionally, that
each event falls inside its beat's frame interval. A missing event fails: a beat that never
happens is not "in order". `response_to_contact` checks a coupled pair: after the cause
touches the effect, the effect must start moving within the allowed lag [paper 'Coupled:
consistent state exchange'].

Every threshold (contact tolerance, motion threshold, lags) is a required argument from the
row's pass_predicate; nothing here picks one. Events are found on the frames given, so a
coarse frame list can only report the first sampled frame of an event.
"""

from __future__ import annotations

import math
from typing import Mapping, Sequence

from mathutils import Vector

from .geometry import depsgraph, set_frame, world_mesh
from .support import surface_gap
from .verifier import Check


def _check_frames(frames: Sequence[int]) -> None:
    if not frames or any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError(f"frames must be non-empty and strictly increasing: {list(frames)}")


def contact_onset(a, b, frames: Sequence[int], contact_tol_m: float) -> int | None:
    """First frame whose evaluated surfaces of a and b are within contact_tol_m (0 when they intersect)."""
    _check_frames(frames)
    if contact_tol_m < 0:
        raise ValueError("contact_tol_m must be >= 0")
    for frame in frames:
        set_frame(frame)
        graph = depsgraph()
        if surface_gap(world_mesh(a, graph), world_mesh(b, graph)) <= contact_tol_m:
            return frame
    return None


def closest_approach(a, b, frames: Sequence[int]) -> dict:
    """Smallest evaluated surface gap between a and b over the frames, and the gap on every frame.

    Use it to see why contact_onset found no contact. Rigid bodies collide on their collision
    shapes, which Bullet enlarges by each body's collision margin, so the visible meshes of two
    touching bodies can stay apart by up to the sum of their margins; a fast body can also touch
    and leave between two sampled frames. Compare min_gap_m with those before choosing contact_tol_m.
    """
    _check_frames(frames)
    gaps = []
    for frame in frames:
        set_frame(frame)
        graph = depsgraph()
        gaps.append([frame, surface_gap(world_mesh(a, graph), world_mesh(b, graph))])
    frame, gap = min(gaps, key=lambda fg: fg[1])
    return {"min_gap_m": gap, "frame": frame, "gaps": gaps}


def _positions(obj, graph) -> list:
    return world_mesh(obj, graph).vertices


def motion_onset(obj, frames: Sequence[int], min_displacement_m: float) -> int | None:
    """First frame where some evaluated vertex has moved more than min_displacement_m from frames[0].

    With a changing vertex count (remeshing, fracture) the bounding-box centre is compared instead.
    """
    _check_frames(frames)
    if min_displacement_m <= 0:
        raise ValueError("min_displacement_m must be > 0")
    set_frame(frames[0])
    start = _positions(obj, depsgraph())
    if not start:
        raise ValueError(f"{obj.name} has no vertices")
    start_centre = sum(start, Vector()) / len(start)
    for frame in frames[1:]:
        set_frame(frame)
        now = _positions(obj, depsgraph())
        if len(now) == len(start):
            moved = max((p - q).length for p, q in zip(now, start))
        else:
            moved = (sum(now, Vector()) / max(len(now), 1) - start_centre).length if now else math.inf
        if moved > min_displacement_m:
            return frame
    return None


def region_entry(obj, region_min, region_max, frames: Sequence[int]) -> int | None:
    """First frame where any evaluated vertex of obj lies inside the axis-aligned world box."""
    _check_frames(frames)
    lo, hi = Vector(region_min), Vector(region_max)
    if any(lo[i] > hi[i] for i in range(3)):
        raise ValueError("region_min must not exceed region_max")
    for frame in frames:
        set_frame(frame)
        for p in _positions(obj, depsgraph()):
            if all(lo[i] <= p[i] <= hi[i] for i in range(3)):
                return frame
    return None


def story_dependencies(manifest: Mapping) -> tuple[list[tuple[str, str]], dict[str, tuple[int, int]]]:
    """(cause, effect) pairs from each beat's depends_on, and each beat's frame_interval."""
    beats = ((manifest.get("contract") or {}).get("story") or {}).get("beats") or []
    pairs = [(dep, b["id"]) for b in beats for dep in b.get("depends_on") or []]
    windows = {b["id"]: tuple(b["frame_interval"]) for b in beats if b.get("frame_interval")}
    return pairs, windows


def order(events: Mapping[str, int | None], dependencies: Sequence[tuple[str, str]], *, min_lag_frames: int,
          max_lag_frames: int | None = None, windows: Mapping[str, Sequence[int]] | None = None,
          name: str = "causal_order") -> Check:
    """Every (cause, effect) pair: both events happened and min_lag <= effect - cause <= max_lag.

    events          event name -> first frame (None when it never happened), e.g. from the detectors
    dependencies    (cause, effect) names; all names must be in events
    min_lag_frames  required: 1 means the effect must come strictly after the cause; 0 allows the same frame
    max_lag_frames  optional upper bound on effect - cause
    windows         optional event name -> (start, end) frame interval it must fall in (e.g. its beat)
    """
    if min_lag_frames < 0 or (max_lag_frames is not None and max_lag_frames < min_lag_frames):
        raise ValueError("need 0 <= min_lag_frames <= max_lag_frames")
    unknown = sorted({n for pair in dependencies for n in pair} - set(events))
    if unknown:
        raise ValueError(f"dependencies name unknown events: {unknown}")
    windows = windows or {}
    unknown = sorted(set(windows) - set(events))
    if unknown:
        raise ValueError(f"windows name unknown events: {unknown}")
    failures = []
    for event, frame in sorted(events.items()):
        if frame is None:
            failures.append({"event": event, "problem": "never happened"})
        elif event in windows:
            start, end = windows[event]
            if not start <= frame <= end:
                failures.append({"event": event, "frame": frame, "window": [start, end],
                                 "problem": "outside its frame interval"})
    pairs = []
    for cause, effect in dependencies:
        c, e = events[cause], events[effect]
        lag = None if c is None or e is None else e - c
        pairs.append({"cause": cause, "effect": effect, "cause_frame": c, "effect_frame": e, "lag_frames": lag})
        if lag is None:
            continue
        if lag < min_lag_frames:
            failures.append({"cause": cause, "effect": effect, "lag_frames": lag,
                             "problem": "effect before cause" if lag < 0 else "effect too soon after cause"})
        elif max_lag_frames is not None and lag > max_lag_frames:
            failures.append({"cause": cause, "effect": effect, "lag_frames": lag, "problem": "effect too late"})
    measurements = {"events": dict(events), "pairs": pairs, "min_lag_frames": min_lag_frames,
                    "max_lag_frames": max_lag_frames, "failure_count": len(failures)}
    return Check(name, not failures, measurements, failures[0] if failures else None)


def response_to_contact(cause, effect, frames: Sequence[int], *, contact_tol_m: float, min_displacement_m: float,
                        max_lag_frames: int, name: str = "response_to_contact") -> Check:
    """The effect starts moving no earlier than first contact and at most max_lag_frames after it.

    Motion is measured from the frame of first contact, so an effect that was already moving
    before contact does not count as a response, and one that moves before any contact fails.
    """
    _check_frames(frames)
    if max_lag_frames < 0:
        raise ValueError("max_lag_frames must be >= 0")
    before = motion_onset(effect, frames, min_displacement_m)
    hit = contact_onset(cause, effect, frames, contact_tol_m)
    measurements = {"contact_frame": hit, "contact_tol_m": contact_tol_m,
                    "min_displacement_m": min_displacement_m, "max_lag_frames": max_lag_frames}
    if hit is None:
        closest = closest_approach(cause, effect, frames)
        measurements["response_frame"] = None
        measurements["closest_approach"] = {"min_gap_m": closest["min_gap_m"], "frame": closest["frame"]}
        return Check(name, False, measurements, {"cause": cause.name, "effect": effect.name,
                                                 "problem": "no contact on the checked frames",
                                                 "min_gap_m": closest["min_gap_m"], "frame": closest["frame"]})
    if before is not None and before < hit:
        measurements["response_frame"] = before
        return Check(name, False, measurements, {"cause": cause.name, "effect": effect.name, "frame": before,
                                                 "problem": "effect moved before contact"})
    after = [f for f in frames if f >= hit]
    response = motion_onset(effect, after, min_displacement_m) if len(after) > 1 else None
    measurements["response_frame"] = response
    lag = None if response is None else response - hit
    measurements["lag_frames"] = lag
    if lag is None or lag > max_lag_frames:
        return Check(name, False, measurements, {"cause": cause.name, "effect": effect.name, "contact_frame": hit,
                                                 "lag_frames": lag, "problem": "no response within the allowed lag"})
    return Check(name, True, measurements)
