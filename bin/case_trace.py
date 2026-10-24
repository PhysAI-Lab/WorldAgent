#!/usr/bin/env python3
"""Case trace: what was tried, what failed, what changed, and what fixed it.

Every runner, gate and promote run appends one event to `<case>/reports/trace.jsonl`; the
agent adds refinement notes with `case_trace.py note`; `show` puts under each note what the runner
measured next for the rows it names (or the next gate run), so an attempt's outcome is visible even
when its note was left `pending`. When the world spec has changed since the
last event, a copy of the new spec is kept under `reports/trace/spec_<hash>.json` and a
`spec_change` event lists what changed (beats, objects, regions, contract fields). This
records the refinement loop the skill asks for (hypothesis, one causal change group,
before/after evidence, reason for acceptance or rejection) [skill.md 'For every refinement
loop record ...'] and keeps accepted and failed attempts for later study [paper, future
work: learning from accepted and failed simulations].

The trace is a log, not evidence: the gate and promote never read it, so editing it cannot
change a verdict. Writing it never fails the calling tool; problems are reported on stderr.

Usage:
  case_trace.py note <case> --hypothesis TEXT --change TEXT --result accepted|rejected|pending
                [--reason TEXT] [--row ID ...] [--evidence PATH ...] [--metric KEY=VALUE ...]
  case_trace.py show <case> [--row ID] [--json]
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

TRACE = Path("reports") / "trace.jsonl"
SPEC_DIR = Path("reports") / "trace"
SPEC_KEYS = ("contract", "object_contracts", "topology")  # same spec part the runner hashes


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def spec_part(manifest: dict) -> dict:
    return {k: manifest.get(k) for k in SPEC_KEYS}


def spec_hash(manifest: dict) -> str:
    return hashlib.sha256(json.dumps(spec_part(manifest), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def read(case_dir: Path) -> list[dict]:
    path = case_dir / TRACE
    if not path.is_file():
        return []
    events = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events


def _ids(items, key: str) -> set:
    return {i.get(key) for i in items or [] if isinstance(i, dict) and i.get(key) is not None}


def spec_diff(old: dict, new: dict) -> dict:
    """Added/removed/changed beats, objects and regions, and changed contract fields."""
    diff = {}

    def compare(label, old_items, new_items, key):
        a = {i[key]: i for i in old_items or [] if isinstance(i, dict) and key in i}
        b = {i[key]: i for i in new_items or [] if isinstance(i, dict) and key in i}
        part = {"added": sorted(set(b) - set(a)), "removed": sorted(set(a) - set(b)),
                "changed": sorted(k for k in set(a) & set(b) if a[k] != b[k])}
        if any(part.values()):
            diff[label] = part

    story = lambda s: ((s.get("contract") or {}).get("story") or {}).get("beats")  # noqa: E731
    compare("beats", story(old), story(new), "id")
    compare("objects", old.get("object_contracts"), new.get("object_contracts"), "name")
    compare("regions", (old.get("topology") or {}).get("regions"), (new.get("topology") or {}).get("regions"), "id")
    old_c, new_c = old.get("contract") or {}, new.get("contract") or {}
    fields = sorted(k for k in set(old_c) | set(new_c) if k != "story" and old_c.get(k) != new_c.get(k))
    if fields:
        diff["contract_fields"] = fields
    if (old.get("topology") or {}).get("edges") != (new.get("topology") or {}).get("edges"):
        diff["topology_edges"] = "changed"
    return diff


def record(case_dir: Path, event: dict, manifest: dict | None = None) -> None:
    """Append an event; first log a spec_change if the spec differs from the last one seen."""
    try:
        case_dir = Path(case_dir)
        (case_dir / SPEC_DIR).mkdir(parents=True, exist_ok=True)
        if manifest is None:
            manifest = json.loads((case_dir / "manifest.json").read_text(encoding="utf-8"))
        events = read(case_dir)
        digest = spec_hash(manifest)
        last = next((e.get("spec") for e in reversed(events) if e.get("spec")), None)
        lines = []
        if digest != last:
            snapshot = SPEC_DIR / f"spec_{digest[:12]}.json"
            (case_dir / snapshot).write_text(json.dumps(spec_part(manifest), indent=2, ensure_ascii=False),
                                             encoding="utf-8")
            change = {"time": now(), "type": "spec_change", "spec": digest, "snapshot": snapshot.as_posix()}
            if last:
                old = case_dir / SPEC_DIR / f"spec_{last[:12]}.json"
                change["previous"] = last
                if old.is_file():
                    change["diff"] = spec_diff(json.loads(old.read_text(encoding="utf-8")), spec_part(manifest))
            lines.append(change)
        lines.append({"time": now(), "spec": digest, **event})
        with open(case_dir / TRACE, "a", encoding="utf-8") as f:
            for line in lines:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - the trace must never break the tool that writes it
        print(f"trace: could not record event: {exc}", file=sys.stderr)


def note_outcomes(events: list[dict]) -> dict[int, list[dict]]:
    """Index of each note event -> what was measured after it.

    A note that names rows gets, per row, the first runner measurement of that row recorded
    after the note; a note without rows gets the first gate run after it. The note's own
    `result` is what the agent wrote at the time; this is what the runner measured next, so a
    note left `pending` still shows how the attempt turned out.
    """
    out: dict[int, list[dict]] = {}
    for i, note in enumerate(events):
        if note.get("type") != "note":
            continue
        later = events[i + 1:]
        found = []
        for rid in note.get("rows") or []:
            hit = next((e for e in later if e.get("type") == "verify" and e.get("row") == rid), None)
            found.append({"row": rid, "status": hit.get("status") if hit else None,
                          "candidate": hit.get("candidate") if hit else None, "time": hit.get("time") if hit else None})
        if not note.get("rows"):
            hit = next((e for e in later if e.get("type") == "gate"), None)
            if hit:
                found.append({"gate": "pass" if hit.get("pass") else "fail", "failing_rows": hit.get("failing_rows"),
                              "candidate": hit.get("candidate"), "time": hit.get("time")})
        out[i] = found
    return out


def history(events: list[dict], row: str | None = None) -> dict:
    """Per-row status sequence, spec changes, notes, gate and promote outcomes."""
    rows: dict[str, list] = {}
    for e in events:
        if e.get("type") == "verify":
            rows.setdefault(e.get("row"), []).append(
                {k: e.get(k) for k in ("time", "status", "candidate", "first_failure", "reason", "report")})
    if row is not None:
        rows = {row: rows.get(row, [])}
    pick = lambda t: [e for e in events if e.get("type") == t and (row is None or row in (e.get("rows") or [row]))]  # noqa: E731
    outcomes = note_outcomes(events)
    notes = [{**e, "measured_after": outcomes[i]} for i, e in enumerate(events)
             if e.get("type") == "note" and (row is None or row in (e.get("rows") or [row]))]
    return {"rows": rows, "spec_changes": [e for e in events if e.get("type") == "spec_change"],
            "notes": notes, "gates": [e for e in events if e.get("type") == "gate"],
            "promotions": [e for e in events if e.get("type") == "promote"],
            "frozen": [e for e in events if e.get("type") == "spec_frozen"],
            "limits": [e for e in events if e.get("type") == "limit"]}


def show(case_dir: Path, row: str | None) -> str:
    h = history(read(case_dir), row)
    out = []
    for rid, seq in sorted(h["rows"].items(), key=lambda kv: str(kv[0])):
        steps = " -> ".join(f"{s['status']}" + (f" ({s['candidate']})" if s.get("candidate") else "") for s in seq)
        out.append(f"row {rid}: {steps or 'never run'}")
    for f in h["frozen"]:
        out.append(f"{f['time']} spec frozen ({f.get('baseline')})")
    for c in h["spec_changes"]:
        out.append(f"{c['time']} spec changed {json.dumps(c.get('diff', 'first version'), ensure_ascii=False)}")
    for n in h["notes"]:
        out.append(f"{n['time']} note [{n.get('result')}] {n.get('hypothesis')} | change: {n.get('change')}"
                   + (f" | reason: {n['reason']}" if n.get("reason") else ""))
        for m in n.get("measured_after") or []:
            if "gate" in m:
                out.append(f"    measured next: gate {m['gate']} on {m['candidate']}"
                           + (f"; failing: {', '.join(m.get('failing_rows') or [])}" if m.get("failing_rows") else ""))
            elif m["status"] is None:
                out.append(f"    measured next: {m['row']} not measured since")
            else:
                out.append(f"    measured next: {m['row']} {m['status']} on {m['candidate']}")
    for g in h["gates"]:
        out.append(f"{g['time']} gate {'pass' if g.get('pass') else 'fail'} on {g.get('candidate')}"
                   + (f"; failing: {', '.join(g.get('failing_rows') or [])}" if g.get("failing_rows") else ""))
    for p in h["promotions"]:
        out.append(f"{p['time']} promote {'done' if p.get('promoted') else 'refused'}"
                   + (f": {p.get('reason')}" if p.get("reason") else ""))
    for lim in h["limits"]:
        out.append(f"{lim['time']} limited on {lim.get('candidate')}: {lim.get('reason')}"
                   + (f"; failing: {', '.join(lim.get('failing_rows') or [])}" if lim.get("failing_rows") else ""))
    return "\n".join(out) if out else "no trace yet"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    note = sub.add_parser("note", help="record one refinement attempt")
    note.add_argument("case_dir", type=Path)
    note.add_argument("--hypothesis", required=True)
    note.add_argument("--change", required=True, help="the one causal change group")
    note.add_argument("--result", required=True, choices=["accepted", "rejected", "pending"])
    note.add_argument("--reason", default="")
    note.add_argument("--row", action="append", default=[], dest="rows")
    note.add_argument("--evidence", action="append", default=[])
    note.add_argument("--metric", action="append", default=[], help="KEY=VALUE, e.g. max_depth_m=0.002")
    view = sub.add_parser("show", help="print the case history")
    view.add_argument("case_dir", type=Path)
    view.add_argument("--row")
    view.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if not (args.case_dir / "manifest.json").is_file():
        print(f"no manifest.json in {args.case_dir}", file=sys.stderr)
        raise SystemExit(2)
    if args.command == "note":
        metrics = dict(m.split("=", 1) for m in args.metric if "=" in m)
        record(args.case_dir, {"type": "note", "hypothesis": args.hypothesis, "change": args.change,
                               "result": args.result, "reason": args.reason, "rows": args.rows,
                               "evidence": args.evidence, "metrics": metrics})
        print(json.dumps({"recorded": True, "trace": TRACE.as_posix()}))
    else:
        print(json.dumps(history(read(args.case_dir), args.row), indent=2, ensure_ascii=False) if args.json
              else show(args.case_dir, args.row))


if __name__ == "__main__":
    main()
