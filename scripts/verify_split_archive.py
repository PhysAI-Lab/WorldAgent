#!/usr/bin/env python3
"""Verify checksums and optionally test a split Zstandard archive stream."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


CHUNK_SIZE = 8 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Verify split archive parts against a sha256sum-format manifest."
    )
    parser.add_argument("parts_dir", type=Path)
    parser.add_argument("--checksums", default="PARTS_SHA256SUMS.txt")
    parser.add_argument("--test-stream", action="store_true")
    return parser.parse_args()


def load_manifest(path: Path) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text().splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        fields = line.split(maxsplit=1)
        if len(fields) != 2 or len(fields[0]) != 64:
            raise ValueError(f"Invalid checksum line {line_number}: {raw_line!r}")
        digest, filename = fields[0].lower(), fields[1].lstrip(" *")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ValueError(f"Invalid SHA-256 on line {line_number}")
        candidate = Path(filename)
        if candidate.name != filename or filename in seen:
            raise ValueError(
                f"Unsafe or duplicate filename on line {line_number}: {filename!r}"
            )
        seen.add(filename)
        entries.append((digest, filename))
    if not entries:
        raise ValueError("Checksum manifest is empty")
    return entries


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def test_zstd_stream(paths: list[Path]) -> None:
    executable = shutil.which("zstd")
    if executable is None:
        raise RuntimeError("zstd is required with --test-stream")
    process = subprocess.Popen(
        [executable, "-t"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert process.stdin is not None
    try:
        for path in paths:
            with path.open("rb") as source:
                while chunk := source.read(CHUNK_SIZE):
                    process.stdin.write(chunk)
        process.stdin.close()
        stdout = process.stdout.read() if process.stdout is not None else b""
        stderr = process.stderr.read() if process.stderr is not None else b""
        return_code = process.wait()
    except BrokenPipeError:
        process.kill()
        stdout, stderr = process.communicate()
        return_code = process.returncode
    if return_code != 0:
        detail = (stdout + stderr).decode(errors="replace").strip()
        raise RuntimeError(f"Zstandard stream test failed: {detail}")


def main() -> None:
    args = parse_args()
    parts_dir = args.parts_dir.resolve()
    manifest_path = Path(args.checksums)
    if not manifest_path.is_absolute():
        manifest_path = parts_dir / manifest_path
    entries = load_manifest(manifest_path)

    records = []
    paths = []
    failed = False
    for expected, filename in entries:
        path = parts_dir / filename
        if not path.is_file():
            records.append({"file": filename, "status": "missing"})
            failed = True
            continue
        actual = sha256_file(path)
        status = "pass" if actual == expected else "mismatch"
        records.append(
            {
                "file": filename,
                "bytes": path.stat().st_size,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "status": status,
            }
        )
        paths.append(path)
        failed = failed or status != "pass"

    stream_status = "not_requested"
    if args.test_stream and not failed:
        test_zstd_stream(paths)
        stream_status = "pass"

    report = {
        "status": "fail" if failed else "pass",
        "parts_dir": str(parts_dir),
        "part_count": len(entries),
        "total_bytes": sum(record.get("bytes", 0) for record in records),
        "zstd_stream": stream_status,
        "parts": records,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
