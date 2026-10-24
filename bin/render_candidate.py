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

The device comes from --device, else $WORLDAGENT_RENDER_DEVICE, else OPTIX
(worldagent_measure.render.setup_device); it never falls back to the CPU. --flip enables the
FLIP Fluids add-on first, which a scene with a FLIP domain needs to load its mesh cache.
Blender is found as for the runner. Standard library only.

Usage: render_candidate.py <case_dir> --candidate <path.blend> [--samples N] [--device TYPE] [--flip]
       render_candidate.py <case_dir> --candidate <path.blend> --preview [--frames 1,48,96 | 1-24]
                           [--resolution 960x540] [--samples N] [--device TYPE] [--flip]
Exit: 0 rendered, 1 blocked or failed, 2 usage or setup error.
"""

from __future__ import annotations

import argparse
import json
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
JOB = "from worldagent_measure import render; render.job_main()"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--preview", action="store_true", help="Render chosen frames, not the final sequence")
    parser.add_argument("--frames", default="", help="Preview only: 1,48,96 or 1-24")
    parser.add_argument("--resolution", default="", help="Preview only: WIDTHxHEIGHT")
    parser.add_argument("--samples", type=int,
                        help="Cycles samples (default: $WORLDAGENT_RENDER_SAMPLES, else the scene's own setting)")
    parser.add_argument("--device", help="CPU, ANY_GPU, OPTIX, CUDA, HIP, METAL or ONEAPI")
    parser.add_argument("--flip", action="store_true", help="Enable FLIP Fluids before rendering")
    parser.add_argument("--timeout", type=float, default=None, help="Seconds before Blender is killed")
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


def main() -> None:
    args = parse_args()
    case_dir = args.case_dir.resolve()
    candidate = args.candidate.resolve()
    if not (case_dir / "manifest.json").is_file():
        fail_setup(f"no manifest.json in {case_dir}")
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
    print(json.dumps({"rendered": status == "ok", "mode": mode, "status": status, "problem": problem,
                      "frames": len(files), "output_dir": report["output_dir"],
                      "report": run_verifier.rel(report_path, case_dir)}, indent=2, ensure_ascii=False))
    raise SystemExit(0 if status == "ok" else 1)


if __name__ == "__main__":
    main()
