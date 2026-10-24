#!/usr/bin/env python3
"""Run one case verifier in a fresh Blender process and record its result.

The verifier is a case script (usually <case>/src/verify_*.py) written for this
case. The runner, not the agent, executes it, so a matrix row
can only become `pass` from a measured result:

1. hash the candidate .blend, the verifier script, any --input files, the
   spec part of manifest.json, and the shared helper library;
2. copy the candidate into a temporary directory and check the copy's hash;
3. run `blender --background --factory-startup <copy> --python-exit-code 1
   --python-expr <add lib/ to sys.path> --python <script> -- --out <result.json>
   --case <case_dir> --row <row_id>` (fresh process,
   read-only reopen of a copy [paper A.2; simulation-workflow.en.md 13.2 step 5]);
4. validate the result (see RESULT RULES), write a report under <case>/reports/,
   and update the row in manifest.json's requirements_matrix;
5. keep a copy of the verifier under reports/trace/verifiers/. When the row was measured
   before with a different verifier (path or content), a different pass_predicate, or a
   different world spec (beats, object contracts, topology), append the change to the row's
   `contract_changes` and to the case trace, with the earlier measured status, both
   verifier copies and what changed in the spec, so a reviewer can see it. A change made
   after the row failed is the one to look at: the gate and promote list such rows. This
   never changes a verdict.

RESULT RULES (the verifier writes this JSON to --out):
    {"status": "pass" | "fail" | "blocked",
     "measurements": {...},        # required, non-empty, for pass and fail
     "first_failure": {...},       # required for fail
     "reason": "...",              # required for blocked
     "frames": [start, end],       # optional
     "evidence": ["reports/..."]}  # optional extra evidence paths
A pass with no measurements, a crash, a missing or invalid result, or a
changed candidate makes the row `unknown` with the reason recorded.

Blender comes from $WORLDAGENT_BLENDER, else `blender` on PATH, else the usual install
locations for this OS (bin/worldagent_env.py). $WORLDAGENT_FLIP_PATH, when set, reaches the
verifier's Blender process through the environment.
Standard library only.

Usage: run_verifier.py <case_dir> --row <id> --script <path> --candidate <path.blend> [--input <path> ...]
Exit: 0 pass, 1 fail/blocked/unknown, 2 usage or setup error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# manifest.json keys that make up the world spec (schemas/world_spec.schema.json).
SPEC_KEYS = ("contract", "object_contracts", "topology")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import worldagent_env  # noqa: E402  (Blender lookup shared with the SessionStart hook)
import case_trace  # noqa: E402  (case history; never affects a verdict)

SPEC_HASH_KEY = "manifest.json#spec"
# Shared measurement helpers, importable inside Blender as `worldagent_measure`.
LIB_DIR = Path(__file__).resolve().parent.parent / "lib"
# The helpers measure; if they change, results they produced are stale.
LIB_HASH_KEY = "plugin:worldagent_measure"
SKIP_DIRS = {"__pycache__"}
LOG_TAIL_LINES = 40
# Every verifier version the runner ran, so a changed verifier can be diffed against the earlier one.
VERIFIER_COPIES = Path("reports") / "trace" / "verifiers"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--row", required=True, help="requirements_matrix row id")
    parser.add_argument("--script", required=True, type=Path, help="Case verifier script")
    parser.add_argument("--candidate", required=True, type=Path, help="Candidate .blend")
    parser.add_argument("--input", action="append", default=[], type=Path, help="Extra input (cache, data); repeatable")
    parser.add_argument("--timeout", type=float, default=3600, help="Seconds before the verifier is killed")
    return parser.parse_args()


def fail_setup(message: str) -> None:
    print(json.dumps({"pass": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(2)


def find_blender() -> str:
    info = worldagent_env.find_blender()
    if not info["path"]:
        fail_setup(info["problem"])
    return info["path"]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_path(path: Path) -> str:
    """Hash a file, or a directory as its sorted relative paths and file hashes (bytecode caches skipped)."""
    if path.is_file():
        return sha256_file(path)
    digest = hashlib.sha256()
    files = (p for p in path.rglob("*") if p.is_file() and not SKIP_DIRS & set(p.relative_to(path).parts))
    for item in sorted(files):
        digest.update(item.relative_to(path).as_posix().encode() + b"\0" + sha256_file(item).encode() + b"\n")
    return digest.hexdigest()


def spec_hash(manifest: dict) -> str:
    spec = {k: manifest.get(k) for k in SPEC_KEYS}
    return hashlib.sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def lib_hash() -> str:
    return sha256_path(LIB_DIR)


def rel(path: Path, case_dir: Path) -> str:
    try:
        return path.resolve().relative_to(case_dir).as_posix()
    except ValueError:
        return str(path.resolve())


def is_nonempty_object(value) -> bool:
    return isinstance(value, dict) and bool(value)


def check_result(result) -> str | None:
    """Return why the result is not acceptable, or None."""
    if not isinstance(result, dict):
        return "result is not a JSON object"
    status = result.get("status")
    if status not in ("pass", "fail", "blocked"):
        return f"result status {status!r} is not pass, fail, or blocked"
    if status in ("pass", "fail") and not is_nonempty_object(result.get("measurements")):
        return f"{status} without measurements"
    if status == "pass" and result.get("first_failure"):
        return "pass with first_failure"
    if status == "fail" and not is_nonempty_object(result.get("first_failure")):
        return "fail without first_failure"
    if status == "blocked" and not (isinstance(result.get("reason"), str) and result["reason"].strip()):
        return "blocked without reason"
    evidence = result.get("evidence", [])
    if not (isinstance(evidence, list) and all(isinstance(e, str) for e in evidence)):
        return "evidence is not a list of paths"
    return None


def tail(text: str) -> list[str]:
    return text.splitlines()[-LOG_TAIL_LINES:]


def write_json_atomic(path: Path, data: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def keep_verifier_copy(case_dir: Path, script: Path, digest: str) -> str:
    """Copy the verifier to reports/trace/verifiers/<hash12>_<name> once; return the copy's case path."""
    copy = case_dir / VERIFIER_COPIES / f"{digest[:12]}_{script.name}"
    if not copy.is_file():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(script, copy)
    return rel(copy, case_dir)


def spec_change(case_dir: Path, before_digest: str | None, manifest: dict) -> dict | str | None:
    """What changed in the world spec since the spec with this hash, or None when it is the same.

    Uses the trace's copy of the earlier spec (reports/trace/spec_<hash12>.json) for the diff;
    returns "changed" when that copy is missing.
    """
    if not before_digest or before_digest == spec_hash(manifest):
        return None
    try:
        old = json.loads((case_dir / case_trace.SPEC_DIR / f"spec_{before_digest[:12]}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "changed"
    return case_trace.spec_diff(old, case_trace.spec_part(manifest)) or "changed"


def contract_change(row: dict, case_dir: Path, script_rel: str, script_sha: str, predicate,
                    manifest: dict) -> dict | None:
    """Describe how this run's verifier, pass_predicate or world spec differs from the row's last measurement, or None."""
    before_script = row.get("verified_by")
    before_hashes = row.get("input_hashes") if isinstance(row.get("input_hashes"), dict) else {}
    before_sha = before_hashes.get(before_script) if isinstance(before_script, str) else None
    if not before_sha:
        return None  # never measured by the runner
    before_status, before_predicate, before_report = row.get("status"), None, None
    evidence = row.get("evidence")
    if isinstance(evidence, list) and evidence and isinstance(evidence[0], str):
        before_report = evidence[0]
        try:
            report = json.loads((case_dir / before_report).read_text(encoding="utf-8"))
            # The report keeps the measured status; the row may since have been marked stale.
            before_status = report.get("status", before_status)
            before_predicate = report.get("pass_predicate")
        except (OSError, ValueError, AttributeError):
            pass
    script_changed = (before_script, before_sha) != (script_rel, script_sha)
    predicate_changed = before_predicate is not None and before_predicate != predicate
    spec_changed = spec_change(case_dir, before_hashes.get(SPEC_HASH_KEY), manifest)
    if not (script_changed or predicate_changed or spec_changed):
        return None
    before_copy = case_dir / VERIFIER_COPIES / f"{before_sha[:12]}_{Path(before_script).name}"
    return {
        "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "previous_status": before_status,
        "previous_report": before_report,
        "verifier_changed": script_changed,
        "from": {"script": before_script, "sha256": before_sha,
                 "copy": rel(before_copy, case_dir) if before_copy.is_file() else None},
        "to": {"script": script_rel, "sha256": script_sha},
        "pass_predicate_changed": {"from": before_predicate, "to": predicate} if predicate_changed else None,
        "spec_changed": spec_changed,
    }


def main() -> None:
    args = parse_args()
    case_dir = args.case_dir.resolve()
    manifest_path = case_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail_setup(f"cannot read {manifest_path}: {exc}")
    rows = manifest.get("requirements_matrix") if isinstance(manifest, dict) else None
    if not isinstance(rows, list):
        fail_setup("manifest.json has no requirements_matrix list")
    row = next((r for r in rows if isinstance(r, dict) and r.get("id") == args.row), None)
    if row is None:
        fail_setup(f"requirements_matrix has no row {args.row!r}")
    for label, path in [("script", args.script), ("candidate", args.candidate)] + [("input", p) for p in args.input]:
        if not path.exists():
            fail_setup(f"{label} does not exist: {path}")
    if not args.candidate.is_file() or args.candidate.suffix != ".blend":
        fail_setup(f"candidate is not a .blend file: {args.candidate}")
    blender = find_blender()

    script = args.script.resolve()
    candidate = args.candidate.resolve()
    hashes = {rel(candidate, case_dir): sha256_file(candidate), rel(script, case_dir): sha256_file(script)}
    for path in args.input:
        hashes[rel(path, case_dir)] = sha256_path(path.resolve())
    hashes[SPEC_HASH_KEY] = spec_hash(manifest)
    hashes[LIB_HASH_KEY] = lib_hash()
    script_rel = rel(script, case_dir)
    # Compare with the row's last measurement before anything new is written.
    change = contract_change(row, case_dir, script_rel, hashes[script_rel], row.get("pass_predicate"), manifest)
    verifier_copy = keep_verifier_copy(case_dir, script, hashes[script_rel])

    version = subprocess.run([blender, "--version"], capture_output=True, text=True).stdout.splitlines()
    blender_version = version[0].strip() if version else "unknown"

    started = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    problem = None
    result = None
    proc = None
    with tempfile.TemporaryDirectory(prefix="worldagent_verify_") as tmp:
        copy = Path(tmp) / candidate.name
        shutil.copy2(candidate, copy)
        if sha256_file(copy) != hashes[rel(candidate, case_dir)]:
            fail_setup("candidate copy hash differs from the original")
        out = Path(tmp) / "result.json"
        # --python-exit-code must precede --python: Blender applies arguments in order.
        # Blender ignores PYTHONPATH by default, so put the helper library on sys.path first.
        lib_expr = f"import sys; sys.path.insert(0, {json.dumps(str(LIB_DIR))})"
        cmd = [blender, "--background", "--factory-startup", str(copy), "--python-exit-code", "1",
               "--python-expr", lib_expr, "--python", str(script),
               "--", "--out", str(out), "--case", str(case_dir), "--row", args.row]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=args.timeout, cwd=case_dir)
        except subprocess.TimeoutExpired:
            problem = f"verifier timed out after {args.timeout:g} s"
        if problem is None and proc.returncode != 0:
            problem = f"verifier exited with code {proc.returncode}"
        if problem is None:
            if not out.is_file():
                problem = "verifier wrote no result to --out"
            else:
                try:
                    result = json.loads(out.read_text(encoding="utf-8"))
                except json.JSONDecodeError as exc:
                    problem = f"result is not valid JSON: {exc}"
        if problem is None:
            problem = check_result(result)
    if problem is None and sha256_file(candidate) != hashes[rel(candidate, case_dir)]:
        problem = "candidate .blend changed during verification"

    status = "unknown" if problem else result["status"]
    reports = case_dir / "reports"
    reports.mkdir(exist_ok=True)
    report_path = reports / f"verify_{args.row}_{started}.json"
    n = 1
    while report_path.exists():  # two runs of a row in the same second keep both reports
        n += 1
        report_path = reports / f"verify_{args.row}_{started}_{n}.json"
    report = {
        "row": args.row,
        "status": status,
        "problem": problem,
        "started_utc": started,
        "blender": blender_version,
        "command": ["blender", "--background", "--factory-startup", "<candidate copy>", "--python-exit-code", "1",
                    "--python-expr", "<add lib/ to sys.path>", "--python",
                    rel(script, case_dir), "--", "--out", "<result.json>", "--case", "<case_dir>", "--row", args.row],
        "candidate": rel(candidate, case_dir),
        "input_hashes": hashes,
        "pass_predicate": row.get("pass_predicate"),
        "verifier_copy": verifier_copy,
        "result": result,
        "returncode": proc.returncode if proc else None,
        "stdout_tail": tail(proc.stdout) if proc else [],
        "stderr_tail": tail(proc.stderr) if proc else [],
    }
    write_json_atomic(report_path, report)

    # Re-read so concurrent edits made while Blender ran are not lost.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    row = next(r for r in manifest["requirements_matrix"] if isinstance(r, dict) and r.get("id") == args.row)
    if change:
        change["to"]["copy"] = verifier_copy
        change["report"] = rel(report_path, case_dir)
        change["status"] = status
        row["contract_changes"] = list(row.get("contract_changes") or []) + [change]
        case_trace.record(case_dir, {"type": "contract_change", "row": args.row, **change}, manifest)
    row["status"] = status
    row["evidence"] = [rel(report_path, case_dir)] + ([] if problem else list(result.get("evidence", [])))
    row["verified_by"] = rel(script, case_dir)
    row["input_hashes"] = hashes
    row["candidate"] = rel(candidate, case_dir)
    row.pop("first_failure", None)
    row.pop("reason", None)
    if problem:
        row["reason"] = problem
    elif status == "fail":
        row["first_failure"] = result["first_failure"]
    elif status == "blocked":
        row["reason"] = result["reason"]
    write_json_atomic(manifest_path, manifest)
    case_trace.record(case_dir, {"type": "verify", "row": args.row, "status": status, "candidate": row["candidate"],
                            "candidate_sha256": hashes.get(row["candidate"]), "verified_by": row["verified_by"],
                            "verifier_sha256": hashes[script_rel],
                            "first_failure": row.get("first_failure"), "reason": row.get("reason"),
                            "report": rel(report_path, case_dir)}, manifest)

    print(json.dumps({"row": args.row, "status": status, "problem": problem, "report": rel(report_path, case_dir)},
                     indent=2, ensure_ascii=False))
    raise SystemExit(0 if status == "pass" else 1)


if __name__ == "__main__":
    main()
