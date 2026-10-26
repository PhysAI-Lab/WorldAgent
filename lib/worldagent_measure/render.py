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
checks that report against the candidate the verifier has open. check_movie() checks the
movie bin/render_candidate.py --encode recorded in that report, decoding it with ffprobe.

The movie helpers (ffmpeg_tool, probe_movie, frames_digest) use only ffmpeg/ffprobe and the
standard library, so bin/render_candidate.py imports this module outside Blender too.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import time
from fractions import Fraction
from pathlib import Path

try:
    import bpy
except ImportError:  # outside Blender: only the movie helpers are usable
    bpy = None

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


# Movie encoding [bin/render_candidate.py --encode]: H.264 in MP4, 4:2:0, every final frame once.
MOVIE_CODEC = "h264"
MOVIE_ENCODER = "libx264"
MOVIE_PIXEL_FORMAT = "yuv420p"
MOVIE_CRF = 18


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


class MovieToolUnavailable(RuntimeError):
    """ffmpeg or ffprobe is missing, so the movie cannot be made or checked; the row is blocked, not failed."""


def ffmpeg_tool(name: str) -> tuple[str | None, str | None]:
    """(path, problem) for 'ffmpeg' or 'ffprobe'.

    $WORLDAGENT_FFMPEG / $WORLDAGENT_FFPROBE when set (a set path that is not an executable
    is a problem, never skipped); ffprobe next to a configured ffmpeg; else the tool on PATH.
    """
    variable = f"WORLDAGENT_{name.upper()}"
    configured = os.environ.get(variable, "").strip()
    if configured:
        if Path(configured).is_file() and os.access(configured, os.X_OK):
            return configured, None
        return None, f"{variable} is not an executable file: {configured}"
    if name == "ffprobe" and os.environ.get("WORLDAGENT_FFMPEG", "").strip():
        sibling = Path(os.environ["WORLDAGENT_FFMPEG"].strip()).with_name("ffprobe")
        if sibling.is_file() and os.access(sibling, os.X_OK):
            return str(sibling), None
    found = shutil.which(name)
    if found:
        return found, None
    return None, f"{name} not found: install ffmpeg (with ffprobe) or set {variable}"


def fps_fraction(fps) -> Fraction:
    """The scene FPS as an exact rate: 24.0 -> 24, 23.976... -> 24000/1001."""
    return Fraction(fps).limit_denominator(1001)


def frames_digest(files) -> str:
    """SHA-256 over the ordered frame hashes of a render report's files list."""
    return hashlib.sha256("\n".join(str(f.get("sha256")) for f in files).encode()).hexdigest()


def probe_movie(path, ffprobe: str) -> dict:
    """Decode every frame of the movie's first video stream with ffprobe; raise RuntimeError when it cannot."""
    cmd = [ffprobe, "-v", "error", "-count_frames", "-select_streams", "v:0",
           "-show_entries", "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_read_frames:format=duration",
           "-of", "json", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout)
        stream = data["streams"][0]
        rate = Fraction(stream["r_frame_rate"])
        return {"codec": stream.get("codec_name"), "pixel_format": stream.get("pix_fmt"),
                "size": [int(stream["width"]), int(stream["height"])],
                "fps": float(rate), "fps_rational": f"{rate.numerator}/{rate.denominator}",
                "frame_count": int(stream["nb_read_frames"]),
                "duration_s": round(float(data["format"]["duration"]), 6)}
    except (ValueError, KeyError, IndexError, TypeError, ZeroDivisionError):
        raise RuntimeError(f"ffprobe could not decode {path}: {proc.stderr.strip()[-300:] or 'no video stream'}")


def check_movie(report_path, case_dir, *, name: str = "final_movie") -> Check:
    """Check the movie recorded in a final render report (bin/render_candidate.py --encode).

    Passes only when the report is a successful final render of the open candidate, its movie
    was encoded from exactly the report's frames, the movie file exists with its recorded
    SHA-256, and ffprobe decodes it to the same frame count, FPS and size as the final frames
    with the recorded codec and pixel format. Use it with check_render_report(), which checks
    the frames themselves. Pass the report and the movie to the runner with --input.
    Raises MovieToolUnavailable when the encode was blocked for a missing ffmpeg or ffprobe
    is missing now: catch it and call verifier.write_blocked(), as for a missing FLIP add-on.
    """
    case_dir = Path(case_dir)
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    movie = report.get("movie") if isinstance(report.get("movie"), dict) else {}
    files = report.get("files") or []
    if movie.get("status") == "blocked":
        raise MovieToolUnavailable(movie.get("problem") or "the movie was not encoded: ffmpeg is missing")
    failures = []

    def need(ok, **failure):
        if not ok:
            failures.append(failure)

    need(report.get("status") == "ok" and report.get("mode") == "final", problem="not a successful final render",
         mode=report.get("mode"), status=report.get("status"))
    candidate_sha = sha256_file(bpy.data.filepath) if bpy is not None and bpy.data.filepath else None
    need(report.get("candidate_sha256") == candidate_sha, problem="rendered a different candidate",
         report_candidate=report.get("candidate"))
    need(movie.get("status") == "ok", problem="no encoded movie in the report", movie_status=movie.get("status"),
         detail=movie.get("problem"))
    need(movie.get("source_frames_sha256") == frames_digest(files),
         problem="movie was not encoded from this report's frames")
    path = case_dir / str(movie.get("path") or "")
    exists = bool(movie.get("path")) and path.is_file()
    need(exists, problem="movie file missing", path=movie.get("path"))
    sha_ok = exists and sha256_file(path) == movie.get("sha256")
    need(not exists or sha_ok, problem="movie hash differs from the report", path=movie.get("path"))
    probed, ffprobe, problem = None, None, None
    if exists:
        ffprobe, problem = ffmpeg_tool("ffprobe")
        if not ffprobe:
            raise MovieToolUnavailable(problem)
        if ffprobe:
            try:
                probed = probe_movie(path, ffprobe)
            except RuntimeError as exc:
                problem = str(exc)
        need(probed is not None, problem="movie could not be decoded", detail=problem)
    if probed:
        fps = report.get("fps")
        need(probed["frame_count"] == len(files) == movie.get("frame_count"),
             problem="movie frame count differs from the final frames", movie_frames=probed["frame_count"],
             final_frames=len(files), recorded=movie.get("frame_count"))
        need(fps is not None and abs(probed["fps"] - fps) < 1e-4, problem="movie FPS differs from the render",
             movie_fps=probed["fps"], render_fps=fps)
        need(probed["size"] == report.get("resolution"), problem="movie size differs from the render",
             movie_size=probed["size"], render_size=report.get("resolution"))
        need((probed["codec"], probed["pixel_format"]) == (movie.get("codec"), movie.get("pixel_format")),
             problem="movie codec or pixel format differs from the report",
             movie=[probed["codec"], probed["pixel_format"]], recorded=[movie.get("codec"), movie.get("pixel_format")])
    measurements = {
        "report": str(report_path), "movie": movie.get("path"), "movie_status": movie.get("status"),
        "candidate_matches": report.get("candidate_sha256") == candidate_sha, "hash_matches": sha_ok,
        "final_frames": len(files), "render_fps": report.get("fps"), "render_resolution": report.get("resolution"),
        "decoded": probed, "ffprobe": ffprobe,
    }
    return Check(name, not failures, measurements, failures[0] if failures else None)
