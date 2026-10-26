#!/usr/bin/env python3
"""Decide whether a case candidate meets the strict completion rule.

A candidate is complete only when every requirements_matrix row is `pass` and
all rows were measured on the same final candidate [simulation-workflow.en.md
13.3]. The gate trusts only runner evidence (bin/run_verifier.py) that is still
current [paper A.2.2 'Feedback and acceptance']. It reads files
and recomputes hashes; it never runs Blender.

GATE RULES (the gate passes only if all hold):
- manifest.json has a non-empty requirements_matrix list;
- the world spec passes bin/spec_audit.py's audit();
- every contract.story.beats id is covered by at least one row, and no row's
  `covers` names a beat that does not exist [paper A.1.1 Specification audit:
  missing requirements remain unverified; paper A.2: matrix rows cover the
  spec's requirements; simulation-workflow.en.md 1.2 empty missing_beats].
  A row covers the beats in its `covers` list; a row without `covers` covers
  the beat whose id equals its own id, if any. Coverage only needs a covering
  row to exist; whether that row passes is decided by its verdict below;
- every row has status `pass`, and for every pass row:
  - verified_by, candidate, input_hashes (non-empty object) and evidence
    (non-empty list) are present; input_hashes contains verified_by, the
    candidate and manifest.json#spec;
  - evidence[0] is an existing report under reports/ whose row, status,
    candidate and input_hashes equal the row's; every other evidence path exists;
- every input_hashes entry hashed now (file, directory, the spec part of
  manifest.json, or the plugin's helper library) equals the recorded hash;
  otherwise the row is `stale`;
- all pass rows name the same candidate (the --candidate one when given).

Row verdicts: pass, fail, unknown, blocked (the row's own status), stale (an
input changed, whatever the status), unverified (status pass without valid,
current runner evidence, or not measured on the gate candidate).

REVIEW (never changes the verdict): `contract_changes` lists every row whose verifier,
pass_predicate, or world spec was changed after the row measured something other than pass
(see bin/run_verifier.py step 5). Rewriting a failing verifier, loosening a threshold, or
moving a beat's window is how a check gets weaker without any verdict being faked, so a
person should look at each listed change before accepting the result.
`spec_baseline` compares the world spec with the one frozen when the spec audit first
passed (bin/spec_audit.py) and lists what changed since: object sizes, beats, inputs and
topology changed before any row was measured show up here too. `solver_deviations` lists every
object simulated by a Blender system that is not preferred for its simulation_class
(schemas/solver_classes.json), with the recorded solver_choice_reason, so a person can judge
the reason; a deviation without a reason already fails the spec audit. `limited` reports whether
a `limited` case status was recorded by bin/limit.py (runner results on every non-render
row) or written some other way.

--mark-stale writes `stale` (input_hashes kept) into exactly the rows whose
inputs changed; other rows are left as they are [paper A.2.2 'invalidate
dependent cache and evidence, keep unaffected components'].

Standard library only.

Usage: gate.py <case_dir> [--candidate <path.blend>] [--mark-stale] [--out report.json]
Exit: 0 pass, 1 not pass, 2 unreadable manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_verifier  # noqa: E402  (hashing shared with the runner)
import spec_audit  # noqa: E402
import case_trace  # noqa: E402  (case history; never affects a verdict)

SPEC_HASH_KEY = run_verifier.SPEC_HASH_KEY
ROW_STATUSES = ("pass", "fail", "unknown", "blocked", "stale")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("case_dir", type=Path)
    parser.add_argument("--candidate", type=Path, help="Candidate .blend every row must have been measured on")
    parser.add_argument("--mark-stale", action="store_true", help="Write stale into rows whose inputs changed")
    parser.add_argument("--out", type=Path, help="Also write the report to this file")
    return parser.parse_args()


class Unreadable(Exception):
    pass


def load_manifest(case_dir: Path) -> dict:
    path = case_dir / "manifest.json"
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Unreadable(f"cannot read {path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise Unreadable(f"{path} is not a JSON object")
    return manifest


def resolve(key: str, case_dir: Path) -> Path:
    path = Path(key)
    return path if path.is_absolute() else case_dir / path


def changed_inputs(hashes: dict, manifest: dict, case_dir: Path) -> list[str]:
    """Return the input_hashes keys whose current hash differs (or cannot be computed)."""
    changed = []
    for key, recorded in hashes.items():
        if key == SPEC_HASH_KEY:
            current = run_verifier.spec_hash(manifest)
        elif key == run_verifier.LIB_HASH_KEY:
            current = run_verifier.lib_hash()
        else:
            path = resolve(key, case_dir)
            current = run_verifier.sha256_path(path) if path.exists() else None
        if current != recorded:
            changed.append(key)
    return changed


def evidence_problem(row: dict, case_dir: Path) -> str | None:
    """Return why a measured row's runner evidence (for its current status) is not acceptable, or None."""
    status = row.get("status")
    for field in ("verified_by", "candidate"):
        if not (isinstance(row.get(field), str) and row[field]):
            return f"{status} without {field}"
    hashes = row.get("input_hashes")
    if not (isinstance(hashes, dict) and hashes):
        return f"{status} without input_hashes"
    for key in (row["verified_by"], row["candidate"], SPEC_HASH_KEY):
        if key not in hashes:
            return f"input_hashes does not cover {key}"
    evidence = row.get("evidence")
    if not (isinstance(evidence, list) and evidence and all(isinstance(e, str) for e in evidence)):
        return f"{status} without evidence"
    first = Path(evidence[0])
    if first.is_absolute() or not first.parts or first.parts[0] != "reports":
        return f"evidence[0] {evidence[0]!r} is not a runner report under reports/"
    try:
        report = json.loads((case_dir / first).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"cannot read runner report {evidence[0]}: {exc}"
    if not isinstance(report, dict):
        return f"runner report {evidence[0]} is not a JSON object"
    for field, expected in (("row", row.get("id")), ("status", status), ("candidate", row["candidate"]),
                            ("input_hashes", hashes)):
        if report.get(field) != expected:
            return f"runner report {evidence[0]} {field} does not match the row"
    for extra in evidence[1:]:
        if not resolve(extra, case_dir).exists():
            return f"evidence {extra!r} does not exist"
    return None


def story_coverage(manifest: dict, rows: list) -> tuple[dict, list[str], list[dict], list[str]]:
    """Return (coverage {beat: [row ids]}, missing_beats, unknown_covers, errors) for the story beats."""
    contract = manifest.get("contract")
    story = contract.get("story") if isinstance(contract, dict) else None
    beats = story.get("beats") if isinstance(story, dict) else None
    beat_ids = [b["id"] for b in beats or [] if isinstance(b, dict) and isinstance(b.get("id"), str)]
    coverage: dict[str, list[str]] = {bid: [] for bid in beat_ids}
    unknown: list[dict] = []
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if "covers" not in row:
            covers = [rid] if rid in coverage else []
        elif isinstance(row["covers"], list) and all(isinstance(c, str) and c for c in row["covers"]):
            covers = row["covers"]
        else:
            errors.append(f"row {rid!r} covers is not a list of beat ids")
            continue
        for bid in covers:
            if bid in coverage:
                if rid not in coverage[bid]:
                    coverage[bid].append(rid)
            else:
                unknown.append({"row": rid, "beat": bid})
    missing = [bid for bid, covering in coverage.items() if not covering]
    if missing:
        errors.append("story beats without a requirements_matrix row: " + ", ".join(missing))
    if unknown:
        errors.append("covers names beats that do not exist: "
                      + ", ".join(f"{u['row']} -> {u['beat']}" for u in unknown))
    return coverage, missing, unknown, errors


def contract_changes(rows: list) -> list[dict]:
    """Rows whose verifier or pass_predicate changed after a non-pass measurement, for review."""
    out = []
    for row in rows:
        changes = row.get("contract_changes") if isinstance(row, dict) else None
        if not isinstance(changes, list):
            continue
        after_non_pass = [c for c in changes if isinstance(c, dict) and c.get("previous_status") != "pass"]
        if after_non_pass:
            out.append({"row": row.get("id"), "changes": after_non_pass})
    return out


def spec_baseline(case_dir: Path, manifest: dict) -> dict:
    """The world spec compared with the one frozen at the first passing spec audit, for review."""
    path = case_dir / spec_audit.BASELINE
    if not path.is_file():
        return {"baseline": None, "changed": None, "problem": "no frozen spec; run bin/spec_audit.py"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        frozen, digest = data["spec"], data["spec_hash"]
    except (OSError, ValueError, KeyError, TypeError):
        return {"baseline": spec_audit.BASELINE.as_posix(), "changed": None, "problem": "frozen spec is unreadable"}
    out = {"baseline": spec_audit.BASELINE.as_posix(), "frozen_utc": data.get("frozen_utc"),
           "changed": run_verifier.spec_hash(manifest) != digest}
    if out["changed"]:
        out["diff"] = case_trace.spec_diff(frozen, case_trace.spec_part(manifest)) or "changed"
    return out


def limited_status(case_dir: Path, manifest: dict) -> dict | None:
    """For a `limited` case: whether bin/limit.py recorded it, with its report; None otherwise."""
    if manifest.get("status") != "limited":
        return None
    record = manifest.get("limitation")
    report = record.get("report") if isinstance(record, dict) else None
    if not (isinstance(report, str) and (case_dir / report).is_file()):
        return {"recorded": False, "problem": "status limited was not recorded by bin/limit.py"}
    return {"recorded": True, "report": report, "candidate": record.get("candidate")}


def evaluate(case_dir: Path, candidate: Path | None = None, manifest: dict | None = None) -> dict:
    """Return the gate report. Raises Unreadable when manifest.json cannot be read."""
    case_dir = case_dir.resolve()
    if manifest is None:
        manifest = load_manifest(case_dir)
    errors: list[str] = []
    rows = manifest.get("requirements_matrix")
    if not (isinstance(rows, list) and rows):
        errors.append("requirements_matrix is missing or empty")
        rows = []
    audit_errors = spec_audit.audit(manifest)
    if audit_errors:
        errors.append(f"spec audit failed with {len(audit_errors)} error(s)")
    # Every story beat must have a row [simulation-workflow.en.md 13.1 'individually testable rows'].
    coverage, missing_beats, unknown_covers, coverage_errors = story_coverage(manifest, rows)
    errors.extend(coverage_errors)

    target = run_verifier.rel(candidate, case_dir) if candidate is not None else None
    if candidate is not None and not candidate.is_file():
        errors.append(f"candidate does not exist: {target}")

    verdicts = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            verdicts.append({"row": f"requirements_matrix[{i}]", "verdict": "unverified", "reason": "row is not an object"})
            continue
        status = row.get("status")
        hashes = row.get("input_hashes")
        changed = changed_inputs(hashes, manifest, case_dir) if isinstance(hashes, dict) else []
        entry = {"row": row.get("id"), "status": status, "candidate": row.get("candidate")}
        if changed:
            entry.update(verdict="stale", reason="inputs changed: " + ", ".join(changed), changed_inputs=changed)
        elif status == "pass":
            problem = evidence_problem(row, case_dir)
            entry.update(verdict="unverified" if problem else "pass", reason=problem)
        elif status in ROW_STATUSES:
            entry.update(verdict=status, reason=row.get("reason"))
        else:
            entry.update(verdict="unverified", reason=f"unknown row status {status!r}")
        verdicts.append(entry)

    # Same final candidate for every measured row [simulation-workflow.en.md 13.3].
    measured = sorted({v["candidate"] for v in verdicts if v["verdict"] == "pass"})
    if target is None and len(measured) == 1:
        target = measured[0]
    elif target is None and len(measured) > 1:
        errors.append("pass rows were measured on different candidates: " + ", ".join(measured))
    if target is not None:
        for v in verdicts:
            if v["verdict"] == "pass" and v["candidate"] != target:
                v.update(verdict="unverified", reason=f"measured on {v['candidate']}, not on {target}")

    candidate_sha256 = None
    if target is not None:
        row_by_id = {r.get("id"): r for r in rows if isinstance(r, dict)}
        recorded = {row_by_id[v["row"]]["input_hashes"][target] for v in verdicts if v["verdict"] == "pass"}
        if len(recorded) == 1:
            candidate_sha256 = recorded.pop()

    passed = not errors and bool(verdicts) and all(v["verdict"] == "pass" for v in verdicts)
    return {
        "pass": passed,
        "case_dir": str(case_dir),
        "candidate": target,
        "candidate_sha256": candidate_sha256 if passed else None,
        "errors": errors,
        "spec_audit_errors": audit_errors,
        "coverage": coverage,
        "missing_beats": missing_beats,
        "unknown_covers": unknown_covers,
        "rows": verdicts,
        "contract_changes": contract_changes(rows),
        "spec_baseline": spec_baseline(case_dir, manifest),
        "solver_deviations": spec_audit.solver_deviations(manifest),
        "limited": limited_status(case_dir, manifest),
    }


def mark_stale(case_dir: Path, report: dict) -> list[str]:
    """Write stale into manifest rows the report found stale; return their ids."""
    case_dir = case_dir.resolve()
    stale = {v["row"]: v["reason"] for v in report["rows"] if v["verdict"] == "stale"}
    manifest = load_manifest(case_dir)
    marked = []
    for row in manifest.get("requirements_matrix", []):
        if isinstance(row, dict) and row.get("id") in stale and row.get("status") != "stale":
            row["status"] = "stale"
            row["reason"] = stale[row["id"]]
            marked.append(row["id"])
    if marked:
        run_verifier.write_json_atomic(case_dir / "manifest.json", manifest)
    return marked


def main() -> None:
    args = parse_args()
    candidate = args.candidate.resolve() if args.candidate else None
    try:
        report = evaluate(args.case_dir, candidate)
        if args.mark_stale:
            report["marked_stale"] = mark_stale(args.case_dir, report)
    except Unreadable as exc:
        print(json.dumps({"pass": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    case_trace.record(args.case_dir, {"type": "gate", "pass": report["pass"], "candidate": report.get("candidate"),
                                 "failing_rows": [r["row"] for r in report.get("rows", []) if r.get("verdict") != "pass"],
                                 "errors": len(report.get("errors", [])),
                                 "contract_changes": [c["row"] for c in report.get("contract_changes", [])],
                                 "spec_changed_since_baseline": (report.get("spec_baseline") or {}).get("changed"),
                                 "solver_deviations": [d["object"] for d in report.get("solver_deviations", [])],
                                 "marked_stale": report.get("marked_stale", [])})
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
