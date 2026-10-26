#!/usr/bin/env python3
"""Render a candidate with Cycles and write a render report the render/media rows can check.

Final mode (the default) renders every frame of the scene's frame range at the final resolution
(1920x1080, or the plugin's render-resolution setting, $WORLDAGENT_RENDER_RESOLUTION) on the
configured render device into <case>/media/final_<stamp>/ [skill.md Stage 6]. --preview
renders chosen frames, optionally smaller, into <case>/previews/preview_<stamp>/; a preview
report says mode "preview" and never satisfies a final-render check.

Steps:
1. hash the candidate .blend;
2. run `blender --background --factory-startup <candidate> --python-exit-code 1 --python-expr
   <render job>` in a fresh process. The candidate opens in place, so relative cache paths
   resolve as they do for the bake, and is never saved;
3. check the candidate hash is unchanged;
4. hash every frame and read its PNG size, then write reports/render_<stamp>.json with the
   candidate hash, Blender version, device, resolution, samples, denoiser, color management,
   FPS, frames and files.
A render that fails or is blocked (device unavailable, FLIP Fluids missing) keeps its partial
frames under .work/renders/ and still writes a report with the reason.

--encode (final mode only) then encodes the final frames into media/<output folder>.mp4 with
ffmpeg: H.264 (libx264, CRF 18), yuv420p, at the render's FPS, every frame exactly once (no
interpolation, retiming, dropped or repeated frames). --encode-report <report> encodes an
existing successful final render the same way without rendering again. Before encoding, every
frame must still match its recorded hash; afterwards ffprobe decodes the movie and its frame
count, FPS and size must match the frames. The result is stored in the render report as
"movie" (status, path, codec, encoder, pixel_format, crf, fps, fps_rational, frame_count,
duration_s, size, sha256, source_frames_sha256, ffmpeg, encoded_utc, problem), for
worldagent_measure.render.check_movie. ffmpeg comes from $WORLDAGENT_FFMPEG, else PATH, and
ffprobe from $WORLDAGENT_FFPROBE, else next to that ffmpeg, else PATH; when either is missing
the movie is "blocked" with the reason. A failed encode leaves nothing in media/.

The device comes from --device, else $WORLDAGENT_RENDER_DEVICE, else OPTIX
(worldagent_measure.render.setup_device); it never falls back to the CPU. --flip enables the
FLIP Fluids add-on first, which a scene with a FLIP domain needs to load its mesh cache.
Blender is found as for the runner. Standard library only (plus ffmpeg/ffprobe for --encode).

Usage: render_candidate.py <case_dir> --candidate <path.blend> [--samples N] [--device TYPE] [--flip] [--encode]
       render_candidate.py <case_dir> --candidate <path.blend> --preview [--frames 1,48,96 | 1-24]
                           [--resolution 960x540] [--samples N] [--device TYPE] [--flip]
       render_candidate.py <case_dir> --encode-report reports/render_<stamp>.json
Exit: 0 rendered (and encoded when asked), 1 blocked or failed, 2 usage or setup error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_verifier  # noqa: E402  (hashing, Blender lookup, report writing shared with the runner)
import case_trace  # noqa: E402  (case history; never affects a verdict)

LIB_DIR = run_verifier.LIB_DIR
sys.path.insert(0, str(LIB_DIR))
from worldagent_measure import render  # noqa: E402  (movie helpers only; no Blender needed)

JOB = "from worldagent_measure import render; render.job_main()"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--candidate", type=Path, help="Candidate .blend to render (not with --encode-report)")
    parser.add_argument("--preview", action="store_true", help="Render chosen frames, not the final sequence")
    parser.add_argument("--frames", default="", help="Preview only: 1,48,96 or 1-24")
    parser.add_argument("--resolution", default="", help="Preview only: WIDTHxHEIGHT")
    parser.add_argument("--samples", type=int,
                        help="Cycles samples (default: $WORLDAGENT_RENDER_SAMPLES, else the scene's own setting)")
    parser.add_argument("--device", help="CPU, ANY_GPU, OPTIX, CUDA, HIP, METAL or ONEAPI")
    parser.add_argument("--flip", action="store_true", help="Enable FLIP Fluids before rendering")
    parser.add_argument("--timeout", type=float, default=None, help="Seconds before Blender is killed")
    parser.add_argument("--encode", action="store_true", help="Final only: encode the frames into media/ with ffmpeg")
    parser.add_argument("--encode-report", type=Path,
                        help="Encode the frames of this existing final render report; renders nothing")
    return parser.parse_args()


def fail_setup(message: str) -> None:
    print(json.dumps({"rendered": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(2)


def parse_frames(text: str) -> list[int]:
    frames: list[int] = []
    for part in filter(None, text.split(",")):
        if "-" in part.strip()[1:]:
            start, end = part.rsplit("-", 1)
            frames += range(int(start), int(end) + 1)
        else:
            frames.append(int(part))
    if any(b <= a for a, b in zip(frames, frames[1:])):
        raise ValueError("frames must be increasing")
    return frames


def encode(case_dir: Path, report: dict) -> dict:
    """Encode a successful final render's frames into media/; return the report's "movie" record."""
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    files = report.get("files") or []
    frames = report.get("frames") or []
    movie = {"status": "error", "problem": None, "encoded_utc": stamp}
    if report.get("mode") != "final" or report.get("status") != "ok" or not report.get("output_dir"):
        return {**movie, "problem": "only a successful final render is encoded"}
    out_dir = case_dir / report["output_dir"]
    expected = [run_verifier.rel(out_dir / f"frame_{f:04d}.png", case_dir) for f in frames]
    if not files or [e.get("path") for e in files] != expected or frames != list(range(frames[0], frames[-1] + 1)):
        return {**movie, "problem": "frames are not one contiguous frame_NNNN.png sequence in the output folder"}
    for entry in files:
        path = case_dir / entry["path"]
        if not path.is_file() or run_verifier.sha256_file(path) != entry.get("sha256"):
            return {**movie, "problem": f"{entry['path']} is missing or differs from the render report"}
    width, height = report.get("resolution") or [0, 0]
    if width % 2 or height % 2:
        return {**movie, "problem": f"{width}x{height} has an odd side; {render.MOVIE_PIXEL_FORMAT} needs even sides"}
    ffmpeg, problem = render.ffmpeg_tool("ffmpeg")
    ffprobe, problem2 = render.ffmpeg_tool("ffprobe")
    if not (ffmpeg and ffprobe):
        return {**movie, "status": "blocked", "problem": problem or problem2}
    rate = render.fps_fraction(report["fps"])
    rate_text = f"{rate.numerator}/{rate.denominator}"
    movie_path = case_dir / "media" / f"{out_dir.name}.mp4"
    staging = case_dir / ".work" / "encodes" / movie_path.name
    staging.parent.mkdir(parents=True, exist_ok=True)
    version = subprocess.run([ffmpeg, "-version"], capture_output=True, text=True).stdout.splitlines()
    release = re.match(r"ffmpeg version n?(\d+)\.(\d+)", version[0] if version else "")
    # -fps_mode replaced -vsync in ffmpeg 5.1; git builds ("version N-...") are newer.
    sync = "-vsync" if release and (int(release[1]), int(release[2])) < (5, 1) else "-fps_mode"
    # -framerate sets each input frame's duration; passthrough keeps every frame exactly once.
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
           "-framerate", rate_text, "-start_number", str(frames[0]), "-i", str(out_dir / "frame_%04d.png"),
           "-frames:v", str(len(frames)), sync, "passthrough", "-an",
           "-c:v", render.MOVIE_ENCODER, "-crf", str(render.MOVIE_CRF), "-pix_fmt", render.MOVIE_PIXEL_FORMAT,
           "-vf", f"scale=out_color_matrix=bt709:out_range=tv,format={render.MOVIE_PIXEL_FORMAT}",
           "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709",
           "-movflags", "+faststart", str(staging)]
    movie.update(ffmpeg=version[0].strip() if version else "unknown", encoder=render.MOVIE_ENCODER,
                 crf=render.MOVIE_CRF, fps_rational=rate_text)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not staging.is_file():
        staging.unlink(missing_ok=True)
        return {**movie, "problem": f"ffmpeg exited {proc.returncode}: {proc.stderr.strip()[-500:]}"}
    try:
        probed = render.probe_movie(staging, ffprobe)
    except RuntimeError as exc:
        staging.unlink(missing_ok=True)
        return {**movie, "problem": str(exc)}
    mismatch = [k for k, ok in (("frame count", probed["frame_count"] == len(frames)),
                                ("fps", probed["fps_rational"] == rate_text),
                                ("size", probed["size"] == [width, height])) if not ok]
    if mismatch:
        staging.unlink(missing_ok=True)
        return {**movie, "problem": f"encoded movie differs from the frames in {', '.join(mismatch)}", "decoded": probed}
    movie_path.parent.mkdir(exist_ok=True)
    os.replace(staging, movie_path)
    movie.update(status="ok", path=run_verifier.rel(movie_path, case_dir), codec=probed["codec"],
                 pixel_format=probed["pixel_format"], fps=probed["fps"], frame_count=probed["frame_count"],
                 duration_s=probed["duration_s"], size=probed["size"], sha256=run_verifier.sha256_file(movie_path),
                 source_frames_sha256=render.frames_digest(files))
    return movie


def encode_existing(case_dir: Path, report_arg: Path) -> None:
    report_path = report_arg if report_arg.is_absolute() else case_dir / report_arg
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        fail_setup(f"cannot read render report {report_arg}: {exc}")
    finish_encode(case_dir, report_path, report)


def finish_encode(case_dir: Path, report_path: Path, report: dict) -> None:
    """Encode, store the movie record in the render report, print the result and exit."""
    movie = encode(case_dir, report)
    report["movie"] = movie
    run_verifier.write_json_atomic(report_path, report)
    rel_report = run_verifier.rel(report_path, case_dir)
    case_trace.record(case_dir, {"type": "encode", "status": movie["status"], "problem": movie["problem"],
                                 "candidate": report.get("candidate"), "candidate_sha256": report.get("candidate_sha256"),
                                 "movie": movie.get("path"), "report": rel_report})
    print(json.dumps({"rendered": report.get("status") == "ok", "mode": report.get("mode"),
                      "status": report.get("status"), "frames": len(report.get("files") or []),
                      "output_dir": report.get("output_dir"), "report": rel_report,
                      "encoded": movie["status"] == "ok", "movie": movie}, indent=2, ensure_ascii=False))
    raise SystemExit(0 if movie["status"] == "ok" else 1)


def main() -> None:
    args = parse_args()
    case_dir = args.case_dir.resolve()
    if not (case_dir / "manifest.json").is_file():
        fail_setup(f"no manifest.json in {case_dir}")
    if args.encode_report:
        if args.candidate or args.preview or args.encode:
            fail_setup("--encode-report encodes an existing render; it takes no --candidate, --preview or --encode")
        encode_existing(case_dir, args.encode_report)
    if not args.candidate:
        fail_setup("--candidate is required")
    if args.encode and args.preview:
        fail_setup("--encode is for a final render; previews are not encoded")
    candidate = args.candidate.resolve()
    if not candidate.is_file() or candidate.suffix != ".blend":
        fail_setup(f"candidate is not a .blend file: {args.candidate}")
    if not args.preview and (args.frames or args.resolution):
        fail_setup("--frames and --resolution are for --preview; a final render is every frame at the final "
                   "resolution (1920x1080 unless the plugin's render-resolution setting changes it)")
    try:
        frames = parse_frames(args.frames)
        if args.resolution:
            width, height = (int(v) for v in args.resolution.lower().split("x"))
            if width <= 0 or height <= 0:
                raise ValueError
    except ValueError:
        fail_setup(f"cannot read --frames {args.frames!r} or --resolution {args.resolution!r}")
    info = run_verifier.worldagent_env.find_blender()
    if not info["path"]:
        fail_setup(info["problem"])
    blender = info["path"]

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    mode = "preview" if args.preview else "final"
    out_dir = case_dir / ("previews" if args.preview else "media") / f"{mode}_{stamp}"
    before = run_verifier.sha256_file(candidate)
    version = subprocess.run([blender, "--version"], capture_output=True, text=True).stdout.splitlines()

    job_args = ["--out-dir", str(out_dir), "--frames", ",".join(map(str, frames)),
                "--resolution", args.resolution.lower()]
    if args.samples is not None:
        job_args += ["--samples", str(args.samples)]
    if args.device:
        job_args += ["--device", args.device]
    if args.flip:
        job_args.append("--flip")
    lib_expr = f"import sys; sys.path.insert(0, {json.dumps(str(LIB_DIR))})"
    problem, proc, job = None, None, {}
    with tempfile.TemporaryDirectory(prefix="worldagent_render_") as tmp:
        result_path = Path(tmp) / "job.json"
        cmd = [blender, "--background", "--factory-startup", str(candidate), "--python-exit-code", "1",
               "--python-expr", lib_expr, "--python-expr", JOB, "--", "--result", str(result_path), *job_args]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout, cwd=case_dir)
        except subprocess.TimeoutExpired:
            problem = f"render timed out after {args.timeout:g} s"
        if problem is None:
            try:
                job = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                problem = f"render job wrote no result (Blender exit code {proc.returncode})"
    if run_verifier.sha256_file(candidate) != before:
        problem = "candidate .blend changed during the render"
    status = "error" if problem else job.get("status", "error")
    problem = problem or job.get("problem")

    files = []
    for path in map(Path, job.get("files") or []):
        size = None
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            size = [int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")]
        files.append({"path": run_verifier.rel(path, case_dir), "sha256": run_verifier.sha256_file(path), "size": size})
    if status != "ok" and out_dir.exists():
        # Keep partial frames for diagnosis, but out of media/ and previews/.
        kept = case_dir / ".work" / "renders" / f"{status}_{mode}_{stamp}"
        kept.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(out_dir), kept)
        for entry in files:
            entry["path"] = run_verifier.rel(kept / Path(entry["path"]).name, case_dir)
        if not any(out_dir.parent.iterdir()):
            out_dir.parent.rmdir()

    reports = case_dir / "reports"
    reports.mkdir(exist_ok=True)
    report_path = reports / f"render_{stamp}.json"
    report = {
        "mode": mode,
        "status": status,
        "problem": problem,
        "started_utc": stamp,
        "candidate": run_verifier.rel(candidate, case_dir),
        "candidate_sha256": before,
        "blender": version[0].strip() if version else "unknown",
        "output_dir": run_verifier.rel(out_dir, case_dir) if status == "ok" else None,
        **{k: v for k, v in job.items() if k not in ("status", "problem", "files", "blender")},
        "files": files,
        "returncode": proc.returncode if proc else None,
        "stderr_tail": run_verifier.tail(proc.stderr) if proc and status != "ok" else [],
    }
    run_verifier.write_json_atomic(report_path, report)
    case_trace.record(case_dir, {"type": "render", "mode": mode, "status": status, "problem": problem,
                                 "candidate": report["candidate"], "candidate_sha256": before,
                                 "frames": len(files), "report": run_verifier.rel(report_path, case_dir)})
    if args.encode and status == "ok":
        finish_encode(case_dir, report_path, report)
    print(json.dumps({"rendered": status == "ok", "mode": mode, "status": status, "problem": problem,
                      "frames": len(files), "output_dir": report["output_dir"],
                      "report": run_verifier.rel(report_path, case_dir)}, indent=2, ensure_ascii=False))
    raise SystemExit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
