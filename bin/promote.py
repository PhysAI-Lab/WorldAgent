#!/usr/bin/env python3
"""Promote a gated candidate to the case's scene_final.blend.

Only a candidate that passes bin/gate.py becomes the final scene; an
unvalidated or earlier file is never final [skill.md Stage 5;
project-pipeline-and-template.en.md 'promote with an explicit final copy';
non-penetration-contract.en.md Promotion]. Steps:

1. check the case layout (scripts/check_case_layout.py);
2. refuse if scene_final.blend exists, unless --replace is given and the
   existing file differs from the candidate (checked first, so these refusals
   change nothing but a line in the case trace, reports/trace.jsonl);
3. re-verify: run every row's verifier again through the runner on exactly this
   candidate, with the inputs it recorded. Final acceptance is the candidate
   reopened in a fresh process passing every check [paper A.2.2 'Feedback and
   acceptance'], so promotion never relies on earlier records. A row that was
   never measured by the runner (no verified_by) cannot be re-verified and
   blocks promotion. The new runner reports and row updates are kept;
4. run the gate on --candidate; if it fails, nothing is promoted;
5. copy the candidate and check the copy's hash equals the hash every row was
   measured on;
6. move an existing final scene to .work/superseded/ with a timestamp (never
   deleted), install the copy, write the gate report to reports/, set manifest
   status "validated",
   validation.passed true, validation.gate_report, artifacts.final_scene
   "scene_final.blend", and add the report to artifacts.reports;
7. check the case layout again; on failure undo step 6 and fail.

The output and the gate report list `contract_changes`: rows whose verifier, pass_predicate,
tolerances, measures, world spec or case-local .py inputs were changed after a non-pass
measurement; `spec_baseline`: what changed in the world spec since it was frozen at the first
passing spec audit; and `solver_deviations`: objects simulated by a Blender system that is not
preferred for their simulation_class, with the recorded reason (bin/gate.py REVIEW).
They do not block promotion; they are what a person should diff before trusting the result.

`final_relative_paths` (output and gate report) lists the `//` paths the runner rebased while
re-verifying (its reports' `rebased_paths`) that resolve to a different place from
scene_final.blend in the case root than from the candidate's folder: a cache written as
`//../../cache/...` next to .work/candidates/<id>/, or a disk point cache, which Blender reads
from `//blendcache_scene_final/`. Promote does not rewrite them: saving a rewritten copy
would change the bytes, and the final scene must be the file every row was measured on.
Opening scene_final.blend directly reads those caches only once they are moved there or
written as absolute paths. Listed for review; it does not block promotion.

Failed candidates stay under .work/ untouched [paper A.2.2 'Feedback and
acceptance']. Standard library only.

Blender comes from $WORLDAGENT_BLENDER or PATH, as for the runner.

Usage: promote.py <case_dir> --candidate <path.blend> [--replace]
Exit: 0 promoted, 1 refused (gate, layout, existing final), 2 unreadable manifest or setup error.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import run_verifier  # noqa: E402
import case_trace  # noqa: E402  (case history; never affects a verdict)

FINAL_NAME = "scene_final.blend"
LAYOUT_CHECK = Path(__file__).resolve().parents[1] / "scripts" / "check_case_layout.py"
RUNNER = Path(__file__).resolve().parent / "run_verifier.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--candidate", required=True, type=Path, help="Candidate .blend to promote")
    parser.add_argument("--replace", action="store_true",
                        help="Replace an existing, different scene_final.blend (old one moved to .work/superseded/)")
    return parser.parse_args()


# Set by main so every exit records its outcome in the case trace.
_CASE: Path | None = None
_CANDIDATE: str | None = None


def emit(data: dict, code: int) -> None:
    if _CASE is not None and (_CASE / "manifest.json").is_file():
        case_trace.record(_CASE, {"type": "promote", "promoted": bool(data.get("promoted")), "candidate": _CANDIDATE,
                             "reason": data.get("reason") or data.get("error"),
                             "failing_rows": [r.get("row") for r in data.get("failing_rows", [])],
                             "sha256": data.get("sha256")})
    print(json.dumps(data, indent=2, ensure_ascii=False), file=sys.stdout if code == 0 else sys.stderr)
    raise SystemExit(code)


def check_layout(case_dir: Path) -> dict:
    if not LAYOUT_CHECK.is_file():
        emit({"promoted": False, "error": f"layout checker not found: {LAYOUT_CHECK}"}, 2)
    proc = subprocess.run([sys.executable, str(LAYOUT_CHECK), str(case_dir)], capture_output=True, text=True)
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"pass": False, "errors": [f"layout checker exited {proc.returncode}: {proc.stderr.strip()}"]}


def reverify(case_dir: Path, candidate: Path) -> list[dict]:
    """Run every row's verifier again on this candidate; return per-row outcomes.

    Each row is re-run with the verifier and extra inputs it recorded; the spec and
    helper-library hashes are recomputed by the runner itself.
    """
    manifest = gate.load_manifest(case_dir)
    rows = manifest.get("requirements_matrix")
    if not (isinstance(rows, list) and rows):
        emit({"promoted": False, "reason": "requirements_matrix is missing or empty"}, 1)
    never = [r.get("id") for r in rows if not (isinstance(r, dict) and isinstance(r.get("verified_by"), str))]
    if never:
        emit({"promoted": False, "reason": "rows never measured by the runner cannot be re-verified",
              "rows": never}, 1)
    fixed = {run_verifier.SPEC_HASH_KEY, run_verifier.LIB_HASH_KEY}
    outcomes = []
    for row in rows:
        recorded = row.get("input_hashes") if isinstance(row.get("input_hashes"), dict) else {}
        inputs = [k for k in recorded if k not in fixed and k not in (row["verified_by"], row.get("candidate"))]
        cmd = [sys.executable, str(RUNNER), str(case_dir), "--row", row["id"],
               "--script", str(gate.resolve(row["verified_by"], case_dir)), "--candidate", str(candidate)]
        for item in inputs:
            cmd += ["--input", str(gate.resolve(item, case_dir))]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode == 2:
            emit({"promoted": False, "reason": f"re-verification of row {row['id']!r} could not run",
                  "error": proc.stderr.strip()}, 2)
        try:
            outcomes.append(json.loads(proc.stdout))
        except json.JSONDecodeError:
            outcomes.append({"row": row["id"], "status": "unknown", "problem": proc.stderr.strip()})
    return outcomes


def final_relative_paths(case_dir: Path, reverified: list[dict]) -> list[dict]:
    """Rebased `//` paths from the re-verification reports that scene_final.blend would resolve elsewhere."""
    out, seen = [], set()
    for outcome in reverified:
        try:
            report = json.loads((case_dir / outcome["report"]).read_text(encoding="utf-8"))
        except (KeyError, TypeError, OSError, ValueError):
            continue
        for entry in report.get("rebased_paths") or []:
            if not (isinstance(entry, dict) and isinstance(entry.get("from"), str) and entry["from"].startswith("//")):
                continue
            if entry.get("read_from_copy"):
                final = case_dir / f"blendcache_{Path(FINAL_NAME).stem}"
            else:
                final = case_dir / entry["from"][2:]
            final = os.path.normpath(str(final))
            key = (entry.get("datablock"), entry.get("property"), entry["from"])
            if final != entry.get("to") and key not in seen:
                seen.add(key)
                out.append({"datablock": entry.get("datablock"), "property": entry.get("property"),
                            "from": entry["from"], "candidate_resolves_to": entry.get("to"),
                            "final_resolves_to": final, "row": outcome.get("row")})
    return out


def main() -> None:
    args = parse_args()
    case_dir = args.case_dir.resolve()
    candidate = args.candidate.resolve()
    global _CASE, _CANDIDATE
    _CASE, _CANDIDATE = case_dir, str(args.candidate)

    layout = check_layout(case_dir)
    if not layout.get("pass"):
        emit({"promoted": False, "reason": "case layout fails before promotion", "layout": layout}, 1)
    if not candidate.is_file():
        emit({"promoted": False, "error": f"candidate does not exist: {candidate}"}, 2)
    # Refusals that do not depend on measurement come first, so they change nothing.
    final = case_dir / FINAL_NAME
    if final.exists():
        if not args.replace:
            emit({"promoted": False, "reason": f"{FINAL_NAME} exists; pass --replace to supersede it"}, 1)
        if run_verifier.sha256_file(final) == run_verifier.sha256_file(candidate):
            emit({"promoted": False, "reason": f"{FINAL_NAME} already equals this candidate"}, 1)
    try:
        reverified = reverify(case_dir, candidate)
    except gate.Unreadable as exc:
        emit({"promoted": False, "error": str(exc)}, 2)
    try:
        report = gate.evaluate(case_dir, candidate)
    except gate.Unreadable as exc:
        emit({"promoted": False, "error": str(exc)}, 2)
    if not report["pass"]:
        failing = [v for v in report["rows"] if v["verdict"] != "pass"]
        emit({"promoted": False, "reason": "gate did not pass", "errors": report["errors"],
              "failing_rows": failing, "reverified": reverified, "spec_baseline": report["spec_baseline"],
              "solver_deviations": report["solver_deviations"]}, 1)
    expected = report["candidate_sha256"]

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    superseded = None

    # Copy inside .work/ first so a partial copy never sits in the case root.
    work = case_dir / ".work"
    staging = work / f"promote_{stamp}.blend"
    shutil.copy2(candidate, staging)
    if run_verifier.sha256_file(staging) != expected:
        staging.unlink()
        emit({"promoted": False, "reason": "candidate copy hash differs from the hash the rows were measured on"}, 1)

    manifest_path = case_dir / "manifest.json"
    old_manifest_text = manifest_path.read_text(encoding="utf-8")
    if final.exists():
        superseded = work / "superseded" / f"scene_final_{stamp}.blend"
        superseded.parent.mkdir(parents=True, exist_ok=True)
        os.replace(final, superseded)
    os.replace(staging, final)

    reports = case_dir / "reports"
    reports.mkdir(exist_ok=True)
    gate_report_path = reports / f"gate_{stamp}.json"
    report["promoted_to"] = FINAL_NAME
    report["reverified"] = reverified
    report["final_relative_paths"] = final_relative_paths(case_dir, reverified)
    report["superseded"] = run_verifier.rel(superseded, case_dir) if superseded else None
    run_verifier.write_json_atomic(gate_report_path, report)
    gate_report = run_verifier.rel(gate_report_path, case_dir)

    manifest = json.loads(old_manifest_text)
    manifest["status"] = "validated"
    validation = manifest.get("validation") if isinstance(manifest.get("validation"), dict) else {}
    validation.update(passed=True, gate_report=gate_report, candidate=report["candidate"],
                      candidate_sha256=expected)
    manifest["validation"] = validation
    artifacts = manifest.get("artifacts") if isinstance(manifest.get("artifacts"), dict) else {}
    artifacts["final_scene"] = FINAL_NAME
    artifacts["reports"] = list(artifacts.get("reports") or []) + [gate_report]
    manifest["artifacts"] = artifacts
    run_verifier.write_json_atomic(manifest_path, manifest)

    layout = check_layout(case_dir)
    if not layout.get("pass"):
        # Undo: restore the manifest and the previous final scene; keep the gate report as a diagnostic.
        manifest_path.write_text(old_manifest_text, encoding="utf-8")
        final.unlink()
        if superseded:
            os.replace(superseded, final)
        emit({"promoted": False, "reason": "case layout fails after promotion; promotion undone",
              "layout": layout, "gate_report": gate_report}, 1)

    emit({"promoted": True, "final_scene": FINAL_NAME, "candidate": report["candidate"],
          "sha256": expected, "gate_report": gate_report,
          "superseded": report["superseded"], "contract_changes": report["contract_changes"],
          "spec_baseline": report["spec_baseline"], "solver_deviations": report["solver_deviations"],
          "final_relative_paths": report["final_relative_paths"]}, 0)


if __name__ == "__main__":
    main()
