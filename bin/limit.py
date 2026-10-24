#!/usr/bin/env python3
"""Record that a case stops as `limited`: rows still fail, and the runner measured them.

`/worldagent:run` ends a case it cannot complete with this tool; the status is never written
by hand. It refuses unless every row other than the render and media rows (categories
`media` and `delivery`) has a current runner result on the given candidate: status pass,
fail or blocked, measured on that candidate, inputs unchanged, and a runner report under
reports/ that matches the row (the same evidence check the gate applies to pass rows). At
least one of those rows must not pass; a candidate whose rows all pass goes to the final
render and promote instead.

It then writes reports/limit_<stamp>.json (the reason, the case budget, every non-passing
row with its first failure or reason, and the gate report), sets the manifest status to
"limited" with a `limitation` record that points at that report, and adds a `limit` event
to the case trace. The gate lists a `limited` status that has no such record. Recording a
limit never promotes anything, and a later passing candidate can still be promoted.

Standard library only; no Blender.

Usage: limit.py <case_dir> --candidate <path.blend> --reason TEXT
Exit: 0 recorded, 1 refused, 2 unreadable manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate  # noqa: E402
import run_verifier  # noqa: E402
import case_trace  # noqa: E402

# Rows measured on the final render, which a limited case renders after it is recorded.
RENDER_CATEGORIES = {"media", "delivery"}
MEASURED = ("pass", "fail", "blocked")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--candidate", required=True, type=Path, help="Best candidate, measured by the runner")
    parser.add_argument("--reason", required=True, help="Why refinement stopped (budget, no progress, resolution)")
    return parser.parse_args()


def unmeasured(rows: list, verdicts: list, case_dir: Path, target: str) -> list[dict]:
    """Non-render rows without a current runner result on the target candidate."""
    stale = {v["row"] for v in verdicts if v.get("verdict") == "stale"}
    out = []
    for row in rows:
        if not isinstance(row, dict) or row.get("category") in RENDER_CATEGORIES:
            continue
        rid, status = row.get("id"), row.get("status")
        if rid in stale:
            problem = "inputs changed since it was measured"
        elif status not in MEASURED:
            problem = f"status {status!r} is not a runner measurement"
        elif row.get("candidate") != target:
            problem = f"measured on {row.get('candidate')}, not on {target}"
        else:
            problem = gate.evidence_problem(row, case_dir)
        if problem:
            out.append({"row": rid, "status": status, "problem": problem})
    return out


def refuse(reason: str, **extra) -> None:
    print(json.dumps({"recorded": False, "reason": reason, **extra}, indent=2, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    args = parse_args()
    case_dir = args.case_dir.resolve()
    candidate = args.candidate.resolve()
    if not args.reason.strip():
        refuse("--reason is empty")
    if not candidate.is_file():
        refuse(f"candidate does not exist: {candidate}")
    try:
        manifest = gate.load_manifest(case_dir)
        report = gate.evaluate(case_dir, candidate, manifest)
    except gate.Unreadable as exc:
        print(json.dumps({"recorded": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    target = report["candidate"]
    rows = manifest.get("requirements_matrix") or []
    missing = unmeasured(rows, report["rows"], case_dir, target)
    if missing:
        refuse("every row except the render and media rows must be measured by the runner on this candidate first",
               rows=missing)
    failing = [{"row": r["id"], "status": r["status"], "first_failure": r.get("first_failure"), "reason": r.get("reason")}
               for r in rows if isinstance(r, dict) and r.get("category") not in RENDER_CATEGORIES
               and r.get("status") != "pass"]
    if not failing:
        refuse("every measured row passes on this candidate; render it and promote instead")

    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = case_dir / "reports" / f"limit_{stamp}.json"
    record = {"time": stamp, "candidate": target, "candidate_sha256": run_verifier.sha256_file(candidate),
              "reason": args.reason.strip(), "budget": manifest.get("budget"), "failing_rows": failing,
              "gate": report}
    path.parent.mkdir(parents=True, exist_ok=True)
    run_verifier.write_json_atomic(path, record)
    manifest["status"] = "limited"
    manifest["limitation"] = {"report": run_verifier.rel(path, case_dir), "candidate": target,
                              "reason": record["reason"], "failing_rows": [f["row"] for f in failing]}
    run_verifier.write_json_atomic(case_dir / "manifest.json", manifest)
    case_trace.record(case_dir, {"type": "limit", "candidate": target, "reason": record["reason"],
                                 "failing_rows": [f["row"] for f in failing]}, manifest)
    print(json.dumps({"recorded": True, "status": "limited", "report": manifest["limitation"]["report"],
                      "failing_rows": failing}, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
