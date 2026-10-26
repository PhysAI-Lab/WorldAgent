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

Occluders are the objects that appear to the camera in the final render, decided per frame:
an object is left out when it is hidden from render (`hide_render`), has camera ray visibility
off (`visible_camera`; FLIP Fluids turns it off on its domain, fluid, inflow and outflow
objects), sits only in collections that are excluded from the view layer, hidden from render
or set to indirect only, or has only volume materials (no Surface shader on any material, as
on a smoke domain). Rays pass through those objects, and the result lists them with the
reason (`not_rendered`) and, per target, how many rays each one would have stopped
(`passed_through`). Objects that render but are hidden in the viewport (eye or monitor icon,
on the object or its collection) are not in the viewport depsgraph that ray casts use, so
they are unhidden while measuring and restored afterwards (`unhidden_for_test`). A target
that does not appear in the render is seen on no frame (`target_not_rendered`).

Limits: occlusion treats every rendered surface as solid, so glass, water or other transparent
surfaces still count as blockers (only volume-only materials pass); volumes (smoke) are not
meshes and cannot be targets. Instanced geometry (collection instances, particle instances)
always blocks, whatever the visibility of its source object. Modifiers that differ between
viewport and render (show_viewport / show_render, subdivision levels) are measured as in the
viewport. Points are a subsample of at most max_points vertices, reported with the result.
Both thresholds are required arguments from the row's pass_predicate.
"""

from __future__ import annotations

from typing import Mapping, Sequence

import bpy
from bpy_extras.object_utils import world_to_camera_view
from mathutils import Vector

from .geometry import depsgraph, set_frame, world_mesh
from .verifier import Check

# Ray offsets in metres so a ray does not stop on the point's own face; numeric only.
_SURFACE_EPS_M = 1e-4
# Most surfaces of objects left out of the render one ray may pass through before it
# counts as blocked; a guard against an endless loop, not a measurement threshold.
_MAX_PASS_THROUGH = 256


def _sample(points: list, max_points: int) -> list:
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(i * step)] for i in range(max_points)]


def _volume_only(obj) -> bool:
    """Every material slot holds a node material whose active output has a Volume and no Surface shader."""
    slots = getattr(obj, "material_slots", None)
    if not slots:
        return False
    for slot in slots:
        mat = slot.material
        tree = getattr(mat, "node_tree", None) if mat is not None else None
        if tree is None or not getattr(mat, "use_nodes", True):
            return False
        outputs = [n for n in tree.nodes if n.type == "OUTPUT_MATERIAL"]
        out = next((n for n in outputs if n.is_active_output), outputs[0] if outputs else None)
        if out is None or out.inputs["Surface"].is_linked or not out.inputs["Volume"].is_linked:
            return False
    return True


def render_visibility(view_layer=None) -> tuple[dict[str, str], dict[str, list]]:
    """(objects left out of the camera's render -> reason, viewport-hidden rendered objects -> what hides them).

    The second dict maps an object name to the hiding items to clear so ray casts see it:
    ("object", obj), ("eye", obj), ("collection", coll), ("layer", layer_collection).
    """
    view_layer = view_layer or bpy.context.view_layer
    rendered: dict[str, list] = {}  # name -> viewport hiders on its first rendered collection path
    reasons: dict[str, str] = {}

    def walk(layer, hiders, why):
        coll = layer.collection
        if why is None:
            if layer.exclude:
                why = "collection excluded from the view layer"
            elif coll.hide_render:
                why = "collection hidden from render"
            elif layer.indirect_only:
                why = "collection set to indirect only"
        if why is None:
            hiders = hiders + [h for h, on in ((("collection", coll), coll.hide_viewport),
                                               (("layer", layer), layer.hide_viewport)) if on]
        for obj in coll.objects:
            if why is None:
                rendered.setdefault(obj.name, hiders)
            else:
                reasons.setdefault(obj.name, why)
        for child in layer.children:
            walk(child, hiders, why)

    walk(view_layer.layer_collection, [], None)
    excluded, unhide = {}, {}
    for name in set(rendered) | set(reasons):
        obj = bpy.data.objects[name]
        if obj.hide_render:
            excluded[name] = "hide_render"
        elif not getattr(obj, "visible_camera", True):
            excluded[name] = "visible_camera off"
        elif name not in rendered:
            excluded[name] = reasons[name]
        elif _volume_only(obj):
            excluded[name] = "volume-only materials"
        else:
            hiders = list(rendered[name])
            if obj.hide_viewport:
                hiders.append(("object", obj))
            if obj.hide_get(view_layer=view_layer):
                hiders.append(("eye", obj))
            if hiders:
                unhide[name] = hiders
    return excluded, unhide


class _Unhider:
    """Clears viewport hiding on rendered objects and restores every original value at the end."""

    def __init__(self, view_layer):
        self.view_layer, self.saved = view_layer, {}

    def apply(self, unhide: Mapping[str, list]) -> bool:
        changed = False
        for hiders in unhide.values():
            for kind, item in hiders:
                key = (kind, item.name if kind != "layer" else item.collection.name)
                if key not in self.saved:
                    self.saved[key] = (kind, item)
                if kind == "object" and item.hide_viewport:
                    item.hide_viewport, changed = False, True
                elif kind == "eye" and item.hide_get(view_layer=self.view_layer):
                    item.hide_set(False, view_layer=self.view_layer)
                    changed = True
                elif kind in ("collection", "layer") and item.hide_viewport:
                    item.hide_viewport, changed = False, True
        if changed:
            self.view_layer.update()
        return changed

    def restore(self) -> None:
        for kind, item in self.saved.values():
            if kind == "eye":
                item.hide_set(True, view_layer=self.view_layer)
            else:
                item.hide_viewport = True
        if self.saved:
            self.view_layer.update()


def seen_fraction(obj, camera, graph, *, max_points: int, excluded: Mapping[str, str] | None = None,
                  passed_through: dict | None = None) -> tuple[float, float, int]:
    """(fraction of sampled points seen, fraction of the image spanned by seen points, points sampled).

    excluded        object names rays pass through (see render_visibility); None blocks on everything
    passed_through  optional dict, incremented per excluded object a ray passed through
    """
    scene = bpy.context.scene
    excluded = excluded or {}
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
        direction = to_point / distance
        blocked = False
        for _ in range(_MAX_PASS_THROUGH + 1):
            hit, location, _, _, hit_obj, matrix = scene.ray_cast(graph, start, direction,
                                                                  distance=distance - _SURFACE_EPS_M)
            if not hit or hit_obj is None:
                break
            original = getattr(hit_obj, "original", hit_obj)
            if original.name == obj.name:
                break
            # an instance (matrix differs from the object's own) always blocks
            instance = any(abs(a - b) > 1e-6 for ra, rb in zip(matrix, hit_obj.matrix_world) for a, b in zip(ra, rb))
            if original.name not in excluded or instance:
                blocked = True
                break
            if passed_through is not None:
                passed_through[original.name] = passed_through.get(original.name, 0) + 1
            step = (location - start).dot(direction) + _SURFACE_EPS_M
            start, distance = start + direction * step, distance - step
            if distance <= _SURFACE_EPS_M:
                break
        else:
            blocked = True
        if not blocked:
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
    Objects that do not appear in the render are not occluders (module docstring); they are
    listed in measurements["not_rendered"] (name -> reason).
    """
    if not targets:
        raise ValueError("no targets")
    if not 0 < min_visible_fraction <= 1 or not 0 < min_frame_fraction <= 1:
        raise ValueError("fractions must be in (0, 1]")
    if frame_step < 1 or max_points < 1:
        raise ValueError("frame_step and max_points must be >= 1")
    view_layer = bpy.context.view_layer
    unhider = _Unhider(view_layer)
    rows, first_failure, not_rendered = [], None, {}
    try:
        for obj, (start, end) in targets:
            if end < start:
                raise ValueError(f"bad interval for {obj.name}: {start}..{end}")
            frames = list(range(start, end + 1, frame_step))
            if frames[-1] != end:
                frames.append(end)
            covered, worst, no_camera, spans, passed, hidden_target = 0, None, [], [], {}, {}
            for frame in frames:
                set_frame(frame)
                excluded, unhide = render_visibility(view_layer)
                unhider.apply(unhide)
                not_rendered.update(excluded)
                cam = camera or bpy.context.scene.camera
                if cam is None:
                    no_camera.append(frame)
                    continue
                if obj.name in excluded:
                    hidden_target.setdefault(excluded[obj.name], []).append(frame)
                    fraction, span = 0.0, 0.0
                else:
                    fraction, span, _ = seen_fraction(obj, cam, depsgraph(), max_points=max_points,
                                                      excluded=excluded, passed_through=passed)
                spans.append(span)
                if fraction >= min_visible_fraction:
                    covered += 1
                if worst is None or fraction < worst[1]:
                    worst = (frame, fraction, cam.name)
            frame_fraction = covered / len(frames)
            row = {"object": obj.name, "interval": [start, end], "frames_checked": len(frames),
                   "frames_covered": covered, "covered_fraction": frame_fraction,
                   "worst_frame": worst[0] if worst else None, "worst_seen_fraction": worst[1] if worst else None,
                   "max_image_span": max(spans) if spans else 0.0, "frames_without_camera": no_camera,
                   "passed_through": dict(sorted(passed.items())),
                   "target_not_rendered": {why: fs for why, fs in hidden_target.items()} or None}
            rows.append(row)
            if first_failure is None and (no_camera or frame_fraction < min_frame_fraction):
                problem = ("no scene camera" if no_camera else
                           "target not in the final render" if hidden_target else "not seen on enough frames")
                first_failure = {"object": obj.name, "covered_fraction": frame_fraction,
                                 "worst_frame": row["worst_frame"], "worst_seen_fraction": row["worst_seen_fraction"],
                                 "camera": worst[2] if worst else None, "problem": problem}
    finally:
        unhider.restore()
    measurements = {"targets": rows, "min_visible_fraction": min_visible_fraction,
                    "min_frame_fraction": min_frame_fraction, "max_points": max_points, "frame_step": frame_step,
                    "not_rendered": dict(sorted(not_rendered.items())),
                    "unhidden_for_test": sorted({f"{kind}:{item.name if kind != 'layer' else item.collection.name}"
                                                 for kind, item in unhider.saved.values()})}
    return Check(name, first_failure is None, measurements, first_failure)
