#!/usr/bin/env python3
"""Check that a Blender physics case follows the clean output layout."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ALLOWED_ROOT_FILES = {"README.md", "manifest.json", "scene_final.blend"}
ALLOWED_ROOT_DIRS = {"src", "reports", "previews", "media", "source", ".work"}
REQUIRED_ROOT_FILES = {"README.md", "manifest.json"}
REQUIRED_ROOT_DIRS = {"src", "reports", ".work"}
OPTIONAL_DIRS = {"previews", "media", "source"}
FINAL_STATUSES = {"validated", "delivered", "limited"}
KNOWN_STATUSES = {"contract", "built", "probed", "baked", "validated", "delivered", "limited", "failed"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_dir", type=Path)
    return parser.parse_args()


def has_files(path: Path) -> bool:
    return any(item.is_file() for item in path.rglob("*"))


def main() -> None:
    args = parse_args()
    root = args.case_dir.resolve()
    errors: list[str] = []
    warnings: list[str] = []

    if not root.is_dir():
        errors.append(f"Case directory does not exist: {root}")
        entries: list[Path] = []
    else:
        entries = list(root.iterdir())

    root_files = {item.name for item in entries if item.is_file()}
    root_dirs = {item.name for item in entries if item.is_dir()}
    unexpected_files = sorted(root_files - ALLOWED_ROOT_FILES)
    unexpected_dirs = sorted(root_dirs - ALLOWED_ROOT_DIRS)
    missing_files = sorted(REQUIRED_ROOT_FILES - root_files)
    missing_dirs = sorted(REQUIRED_ROOT_DIRS - root_dirs)

    if unexpected_files:
        errors.append("Unexpected root files: " + ", ".join(unexpected_files))
    if unexpected_dirs:
        errors.append("Unexpected root directories: " + ", ".join(unexpected_dirs))
    if missing_files:
        errors.append("Missing root files: " + ", ".join(missing_files))
    if missing_dirs:
        errors.append("Missing root directories: " + ", ".join(missing_dirs))

    for optional in sorted(OPTIONAL_DIRS & root_dirs):
        if not has_files(root / optional):
            errors.append(f"Optional directory is empty and should not exist: {optional}")

    manifest = None
    manifest_path = root / "manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"Invalid manifest.json: {exc}")

    if isinstance(manifest, dict):
        status = manifest.get("status")
        if status not in KNOWN_STATUSES:
            errors.append(f"Unknown manifest status: {status!r}")
        final_exists = (root / "scene_final.blend").is_file()
        if final_exists and status not in FINAL_STATUSES:
            errors.append(f"scene_final.blend exists while status is {status!r}")
        if status in {"validated", "delivered"} and not final_exists:
            errors.append(f"status {status!r} requires scene_final.blend")
        validation = manifest.get("validation")
        if status in {"validated", "delivered"} and isinstance(validation, dict):
            if validation.get("passed") is not True:
                errors.append(f"status {status!r} requires validation.passed=true")

    forbidden_suffixes = (".blend1", ".blend@", ".log", ".tmp")
    forbidden_root = sorted(name for name in root_files if name.lower().endswith(forbidden_suffixes))
    if forbidden_root:
        errors.append("Forbidden temporary root files: " + ", ".join(forbidden_root))

    if (root / ".work").is_dir() and not has_files(root / ".work"):
        warnings.append(".work is empty; this is valid immediately after initialization")

    report = {
        "pass": not errors,
        "case_dir": str(root),
        "root_files": sorted(root_files),
        "root_directories": sorted(root_dirs),
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()

