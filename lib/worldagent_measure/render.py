"""Cycles render device setup for the final render.

`scene.cycles.device` accepts only 'CPU' or 'GPU'; the GPU backend (OPTIX, CUDA, HIP,
METAL, ONEAPI) is chosen in the Cycles add-on preferences. setup_device() does both and
returns what was actually enabled, so the manifest can record it.

The device type comes from the argument, else $WORLDAGENT_RENDER_DEVICE (exported at
session start from the plugin's render-device setting), else OPTIX. ANY_GPU takes the
first available of GPU_ORDER. A requested GPU type that is not available raises
RenderDeviceError; it never falls back to the CPU.

render_frames() is the Blender side of bin/render_candidate.py, which renders a candidate and
writes a render report. check_render_report() is for a case's render/media verifier: it
checks that report against the candidate the verifier has open.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import time
from pathlib import Path

import bpy

from worldagent_runtime import value as setting

from .verifier import Check

GPU_ORDER = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")
DEFAULT_DEVICE = "OPTIX"
# Final delivery size [skill.md Stage 6], unless the plugin's render-resolution setting
# ($WORLDAGENT_RENDER_RESOLUTION, exported at session start) says otherwise.
DEFAULT_RESOLUTION = (1920, 1080)


def parse_resolution(text) -> tuple[int, int] | None:
    """'1280x720' -> (1280, 720); None for anything else."""
    try:
        width, height = (int(v) for v in str(text).lower().strip().split("x"))
    except (TypeError, ValueError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def final_resolution() -> tuple[int, int]:
    return parse_resolution(setting("WORLDAGENT_RENDER_RESOLUTION")) or DEFAULT_RESOLUTION


def default_samples() -> int | None:
    """$WORLDAGENT_RENDER_SAMPLES (the plugin's render-samples setting), or None to keep the scene's own."""
    try:
        samples = int(setting("WORLDAGENT_RENDER_SAMPLES", ""))
    except ValueError:
        return None
    return samples if samples > 0 else None


class RenderDeviceError(RuntimeError):
    pass


def _prefs():
    return bpy.context.preferences.addons["cycles"].preferences


def available_types() -> list[str]:
    """GPU backends this Blender build can use on this machine, in GPU_ORDER (empty on a CPU-only machine)."""
    prefs = _prefs()
    found = []
    for item in prefs.get_device_types(bpy.context):
        kind = item[0]
        if kind in GPU_ORDER:
            prefs.compute_device_type = kind
            prefs.refresh_devices()
            if any(d.type == kind for d in prefs.devices):
                found.append(kind)
    return sorted(found, key=GPU_ORDER.index)


def setup_device(scene=None, device_type: str | None = None) -> dict:
    """Configure Cycles on the requested device; returns {'device_type', 'devices', 'denoiser'}."""
    scene = scene or bpy.context.scene
    requested = (device_type or setting("WORLDAGENT_RENDER_DEVICE") or DEFAULT_DEVICE).upper()
    scene.render.engine = "CYCLES"
    if requested == "CPU":
        scene.cycles.device = "CPU"
        return {"device_type": "CPU", "devices": ["CPU"], "denoiser": scene.cycles.denoiser}
    available = available_types()
    if requested == "ANY_GPU":
        if not available:
            raise RenderDeviceError("no GPU render device available")
        kind = next(k for k in GPU_ORDER if k in available)
    elif requested in GPU_ORDER:
        if requested not in available:
            raise RenderDeviceError(f"render device {requested} not available (found: {', '.join(available) or 'none'})")
        kind = requested
    else:
        raise RenderDeviceError(f"unknown render device {requested}; use CPU, ANY_GPU or one of {', '.join(GPU_ORDER)}")
    prefs = _prefs()
    prefs.compute_device_type = kind
    prefs.refresh_devices()
    enabled = []
    for d in prefs.devices:
        d.use = d.type == kind
        if d.use:
            enabled.append(d.name)
    if not enabled:
        raise RenderDeviceError(f"no {kind} device could be enabled")
    scene.cycles.device = "GPU"
    return {"device_type": kind, "devices": enabled, "denoiser": scene.cycles.denoiser}


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def png_size(path) -> tuple[int, int] | None:
    """(width, height) from a PNG header, or None when the file is not a PNG."""
    with open(path, "rb") as f:
        head = f.read(24)
    if len(head) < 24 or head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", head[16:24])


def render_frames(out_dir, frames=None, resolution=None, samples: int | None = None,
                  device_type: str | None = None, scene=None) -> dict:
    """Render frames of the open scene with Cycles to <out_dir>/frame_NNNN.png; return the settings used.

    frames None renders the scene's whole frame range. resolution None uses final_resolution();
    samples None uses default_samples(), else keeps the scene's own setting.
    The device comes from setup_device(), so an unavailable device raises RenderDeviceError.
    """
    scene = scene or bpy.context.scene
    if scene.camera is None:
        raise RuntimeError("scene has no active camera")
    device = setup_device(scene, device_type)
    resolution = resolution or final_resolution()
    samples = samples if samples is not None else default_samples()
    scene.render.resolution_x, scene.render.resolution_y = resolution
    scene.render.resolution_percentage = 100
    if samples is not None:
        scene.cycles.samples = samples
    scene.render.image_settings.file_format = "PNG"
    scene_range = [scene.frame_start, scene.frame_end]
    frames = list(frames) if frames else list(range(scene.frame_start, scene.frame_end + 1))
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    written = []
    for frame in frames:
        scene.frame_set(frame)
        path = out_dir / f"frame_{frame:04d}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        if not path.is_file():
            raise RuntimeError(f"frame {frame} was not written to {path}")
        written.append(str(path))
    return {
        "engine": scene.render.engine,
        "device": device,
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "resolution_percentage": scene.render.resolution_percentage,
        "samples": scene.cycles.samples,
        "use_denoising": scene.cycles.use_denoising,
        "denoiser": scene.cycles.denoiser,
        "view_transform": scene.view_settings.view_transform,
        "look": scene.view_settings.look,
        "fps": scene.render.fps / scene.render.fps_base,
        "scene_frame_range": scene_range,
        "frames": frames,
        "camera": scene.camera.name,
        "files": written,
        "render_seconds": round(time.time() - started, 3),
    }


def job_main() -> None:
    """Entry point when bin/render_candidate.py runs Blender; writes the job result to --result."""
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser(prog="render job")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--result", required=True)
    parser.add_argument("--frames", default="")
    parser.add_argument("--resolution", default="")
    parser.add_argument("--samples", type=int)
    parser.add_argument("--device")
    parser.add_argument("--flip", action="store_true")
    args = parser.parse_args(argv)
    result: dict = {"blender": bpy.app.version_string}
    try:
        if args.flip:
            from . import liquid
            liquid.enable_flip()
        frames = [int(f) for f in args.frames.split(",") if f] or None
        resolution = parse_resolution(args.resolution) if args.resolution else None
        result.update(render_frames(args.out_dir, frames, resolution, args.samples, args.device))
        result["status"] = "ok"
    except RenderDeviceError as exc:
        result.update(status="blocked", problem=str(exc))
    except Exception as exc:  # noqa: BLE001 - every failure is reported, none is hidden
        blocked = type(exc).__name__ == "FlipUnavailable"
        result.update(status="blocked" if blocked else "error", problem=f"{type(exc).__name__}: {exc}")
    Path(args.result).write_text(json.dumps(result, indent=2), encoding="utf-8")


def check_render_report(report_path, case_dir, *, expected_device: str | None = None,
                        resolution=None, scene=None, name: str = "final_render") -> Check:
    """Check a final render report from bin/render_candidate.py against the open candidate.

    Passes only when the report is a successful final render of exactly this candidate (same
    SHA-256 as the open file) with Cycles at the final resolution (`resolution`, else
    final_resolution(): 1920x1080 unless the plugin's setting changes it), every frame of the scene's frame range
    rendered, and every listed file present with its recorded hash and PNG size.
    expected_device: when given, the device type the render must have used; ANY_GPU accepts
    any GPU type but not the CPU.
    Pass the report and its output folder to the runner with --input, so a new render makes
    the row stale.
    """
    scene = scene or bpy.context.scene
    expected = list(resolution) if resolution else list(final_resolution())
    case_dir = Path(case_dir)
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    failures = []

    def need(ok, **failure):
        if not ok:
            failures.append(failure)

    need(report.get("status") == "ok", problem="render did not succeed", status=report.get("status"),
         detail=report.get("problem"))
    need(report.get("mode") == "final", problem="not a final render", mode=report.get("mode"))
    candidate_sha = sha256_file(bpy.data.filepath) if bpy.data.filepath else None
    need(report.get("candidate_sha256") == candidate_sha, problem="rendered a different candidate",
         report_candidate=report.get("candidate"))
    need(report.get("engine") == "CYCLES", problem="engine is not Cycles", engine=report.get("engine"))
    need(report.get("resolution") == expected and report.get("resolution_percentage") == 100,
         problem=f"resolution is not {expected[0]}x{expected[1]} at 100%", resolution=report.get("resolution"))
    device = (report.get("device") or {}).get("device_type")
    if expected_device:
        wanted = expected_device.upper()
        ok = device not in (None, "CPU") if wanted == "ANY_GPU" else device == wanted
        need(ok, problem="rendered on another device", device=device, expected=wanted)
    expected_frames = list(range(scene.frame_start, scene.frame_end + 1))
    rendered = report.get("frames") or []
    missing = [f for f in expected_frames if f not in rendered]
    need(not missing, problem="frames missing from the render", missing=missing[:20], missing_count=len(missing))
    bad_files = []
    for entry in report.get("files") or []:
        path = case_dir / entry.get("path", "")
        if not path.is_file():
            bad_files.append({"path": entry.get("path"), "problem": "missing"})
        elif sha256_file(path) != entry.get("sha256"):
            bad_files.append({"path": entry.get("path"), "problem": "hash differs"})
        elif list(png_size(path) or []) != expected:
            bad_files.append({"path": entry.get("path"), "problem": "image size", "size": png_size(path)})
    need(len(report.get("files") or []) == len(rendered), problem="file list does not match the frames")
    need(not bad_files, problem="output files do not match the report", first=bad_files[:1],
         count=len(bad_files))
    measurements = {
        "report": str(report_path), "status": report.get("status"), "mode": report.get("mode"),
        "candidate_matches": report.get("candidate_sha256") == candidate_sha,
        "engine": report.get("engine"), "resolution": report.get("resolution"), "device_type": device,
        "samples": report.get("samples"), "denoiser": report.get("denoiser"),
        "frames_expected": len(expected_frames), "frames_rendered": len(rendered),
        "missing_frame_count": len(missing), "bad_file_count": len(bad_files),
    }
    return Check(name, not failures, measurements, failures[0] if failures else None)
