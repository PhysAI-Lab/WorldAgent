#!/usr/bin/env python3
"""Initialize a clean Blender physics case without placeholder artifacts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SIMULATION_CLASSES = (
    "rigid_body",
    "cloth",
    "liquid",
    "gas",
    "particles",
    "granular_approximation",
    "soft_body",
    "coupled",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path, help="New or empty case directory")
    parser.add_argument("--name", required=True, help="Stable case name using letters, digits, _ or -")
    parser.add_argument("--title", help="Human-readable title; defaults to the case name")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Exact user request")
    prompt_group.add_argument("--prompt-file", type=Path, help="UTF-8 file containing the exact request")
    parser.add_argument(
        "--simulation-class",
        action="append",
        choices=SIMULATION_CLASSES,
        required=True,
        dest="simulation_classes",
        help="Repeat for coupled cases",
    )
    parser.add_argument("--blender-version", default="unspecified")
    parser.add_argument("--frame-start", type=int)
    parser.add_argument("--frame-end", type=int)
    parser.add_argument("--fps", type=int)
    return parser.parse_args()


def fail(message: str) -> None:
    print(json.dumps({"pass": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(2)


def read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        if not args.prompt_file.is_file():
            fail(f"Prompt file does not exist: {args.prompt_file}")
        prompt = args.prompt_file.read_text(encoding="utf-8")
    else:
        prompt = args.prompt or ""
    prompt = prompt.strip()
    if not prompt:
        fail("Prompt must not be empty")
    return prompt


def main() -> None:
    args = parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", args.name):
        fail("--name must be 1-64 characters using letters, digits, _ or -")
    if args.frame_start is not None and args.frame_end is not None and args.frame_end < args.frame_start:
        fail("--frame-end must be greater than or equal to --frame-start")
    if args.fps is not None and args.fps <= 0:
        fail("--fps must be positive")

    root = args.case_dir.resolve()
    if root.exists() and any(root.iterdir()):
        fail(f"Refusing to initialize a non-empty directory: {root}")
    root.mkdir(parents=True, exist_ok=True)
    for directory in ("src", "reports", ".work"):
        (root / directory).mkdir(exist_ok=True)

    prompt = read_prompt(args)
    frame_range = None
    if args.frame_start is not None or args.frame_end is not None:
        frame_range = [args.frame_start, args.frame_end]

    manifest = {
        "schema_version": 1,
        "case": args.name,
        "prompt": prompt,
        "status": "contract",
        "contract": {
            "blender": args.blender_version,
            "simulation_class": args.simulation_classes,
            "units": "metric",
            "frame_range": frame_range,
            "fps": args.fps,
            "required_outputs": ["scene_final.blend", "manifest.json", "src/", "reports/"],
            "acceptance": [],
        },
        "classification": {
            "indoor": None,
            "outdoor": None,
            "terrain": None,
            "bounds_m": None,
            "solver_ownership": [],
        },
        "configuration": {"requested": {}, "actual_reopened": {}},
        "artifacts": {"final_scene": None, "sources": [], "reports": []},
        "validation": {"passed": False, "summary": {}},
        "approximations": [],
        "limitations": [],
        "render_only": [],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    title = args.title or args.name.replace("_", " ").replace("-", " ").title()
    readme = f"""# {title}

Status: `contract`

The canonical request, acceptance contract, artifact index, validation summary, approximations, and limitations are recorded in `manifest.json`.

- Final validated scene: `scene_final.blend` (created only after reopened validation passes)
- Reconstruction source: `src/`
- Stage reports: `reports/`
- Disposable candidates, caches, frames, logs, and backups: `.work/`
"""
    (root / "README.md").write_text(readme, encoding="utf-8")

    print(
        json.dumps(
            {
                "pass": True,
                "case_dir": str(root),
                "root_files": ["README.md", "manifest.json"],
                "directories": ["src", "reports", ".work"],
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

