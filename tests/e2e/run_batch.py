#!/usr/bin/env python3
"""Run the end-to-end test cases through an agent (Codex or Claude Code) and summarize the results.

For every case in cases.json (or --only), the runner:
1. makes a workspace <out>/<id>/ and gives the agent the case's request once, asking it to run
   the WorldAgent `run` skill end to end into <out>/<id>/case/ without questions;
2. sets WORLDAGENT_TIME_BUDGET_HOURS for the run and kills the agent after --timeout-hours;
3. evaluates the case folder with the plugin's own tools (no agent involved): manifest status
   and budget, the gate verdict on the candidate the rows name, failing rows, contract changes,
   trace notes, the final render of that candidate (never a later preview or another candidate's
   render), the final scene, and the rigid body setup checks on the candidate (tests/e2e/setup_probe.py, in a fresh Blender process);
4. writes <out>/summary.json and <out>/summary.md.

The agent is the only part that differs between hosts. The plugin must be installed for the
agent (Codex: from the repo marketplace, hooks trusted; Claude Code: --plugin-dir or an installed
marketplace). Agent commands are templates (--agent-cmd), because CLI flags differ between
versions; check `codex exec --help` on the target machine and pass the right ones.

Usage:
  run_batch.py --agent codex  --out <dir> [--only ring_chain,domino_chain] [--budget-hours 3]
  run_batch.py --agent claude --out <dir> --plugin-dir <worldagent-plugin>
  run_batch.py --check-only --out <dir>          re-evaluate finished workspaces only
  run_batch.py --agent codex --out <dir> --dry-run   print the commands, run nothing
Standard library only (plus Blender for the setup probe).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE.parent.parent
sys.path.insert(0, str(PLUGIN / "bin"))
import case_trace  # noqa: E402
import gate  # noqa: E402
import run_verifier  # noqa: E402
import worldagent_env  # noqa: E402

FINAL = "scene_final.blend"
LIB = PLUGIN / "lib"
PROBE = HERE / "setup_probe.py"

PROMPTS = {
    "claude": "/worldagent:run --case {case} {request}",
    "codex": "$worldagent:run --case {case} {request}",
}
COMMANDS = {
    "claude": "claude -p --permission-mode bypassPermissions --output-format stream-json --verbose {plugin_dir} {prompt}",
    "codex": "codex exec --json --skip-git-repo-check --approve-for-me {prompt}",
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--agent", choices=sorted(COMMANDS), help="Agent CLI to run the cases with")
    p.add_argument("--out", required=True, type=Path, help="Folder for workspaces and the summary")
    p.add_argument("--cases", type=Path, default=HERE / "cases.json")
    p.add_argument("--only", default="", help="Comma-separated case ids")
    p.add_argument("--budget-hours", type=float, default=None,
                   help="WORLDAGENT_TIME_BUDGET_HOURS for each run (default: the plugin's default)")
    p.add_argument("--timeout-hours", type=float, default=None,
                   help="Kill the agent after this long (default: budget x 1.5 + 0.5 h)")
    p.add_argument("--plugin-dir", type=Path, help="Claude Code only: load the plugin from this folder")
    p.add_argument("--agent-cmd", help="Command template; {prompt} and {plugin_dir} are filled in")
    p.add_argument("--prompt-template", help="Prompt template; {case} and {request} are filled in")
    p.add_argument("--check-only", action="store_true", help="Only evaluate existing workspaces")
    p.add_argument("--dry-run", action="store_true", help="Print the commands and exit")
    p.add_argument("--no-probe", action="store_true", help="Skip the Blender setup probe")
    return p.parse_args()


def command_for(args, prompt: str) -> list[str]:
    template = args.agent_cmd or COMMANDS[args.agent]
    plugin_dir = f"--plugin-dir {shlex.quote(str(args.plugin_dir.resolve()))}" if args.plugin_dir else ""
    filled = template.replace("{plugin_dir}", plugin_dir)
    parts = shlex.split(filled.replace("{prompt}", "__PROMPT__"))
    return [prompt if part == "__PROMPT__" else part for part in parts]


def run_agent(cmd: list[str], workspace: Path, env: dict, timeout_s: float) -> dict:
    started = time.time()
    with open(workspace / "agent.log", "w", encoding="utf-8") as out, \
            open(workspace / "agent.err", "w", encoding="utf-8") as err:
        proc = subprocess.Popen(cmd, cwd=workspace, env=env, stdout=out, stderr=err, start_new_session=True)
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(proc.pid, sig)
                except ProcessLookupError:
                    break
                try:
                    proc.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    continue
    return {"exit_code": proc.returncode, "timed_out": timed_out, "agent_hours": round((time.time() - started) / 3600, 2)}


def latest(paths) -> Path | None:
    paths = sorted(paths, key=lambda p: p.stat().st_mtime)
    return paths[-1] if paths else None


def candidate_of(manifest: dict, case: Path) -> Path | None:
    """The selected candidate: the promoted one, else the one bin/limit.py recorded, else the rows' most named."""
    named = None
    for record in (manifest.get("validation"), manifest.get("limitation")):
        if isinstance(record, dict) and isinstance(record.get("candidate"), str):
            named = record["candidate"]
            break
    if not named:
        counts: dict[str, int] = {}
        for row in manifest.get("requirements_matrix") or []:
            if isinstance(row, dict) and isinstance(row.get("candidate"), str):
                counts[row["candidate"]] = counts.get(row["candidate"], 0) + 1
        named = max(counts, key=counts.get) if counts else None
    if named and (case / named).is_file():
        return case / named
    return latest(case.glob(".work/candidates/*/scene.blend"))


def final_render_of(case: Path, candidate: Path | None) -> dict:
    """The newest final render report of exactly this candidate (same SHA-256), preferring a successful one.

    Previews and renders of other candidates are never picked; when there is no final render of
    the candidate the result says so and counts the reports that were skipped.
    """
    reports = []
    for path in sorted(case.glob("reports/render_*.json")):
        try:
            reports.append((path, json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    if candidate is None:
        return {"problem": "no selected candidate", "skipped_reports": len(reports)}
    sha = run_verifier.sha256_file(candidate)
    matching = [(p, r) for p, r in reports if r.get("mode") == "final" and r.get("candidate_sha256") == sha]
    if not matching:
        return {"problem": f"no final render of {candidate.relative_to(case)}", "skipped_reports": len(reports)}
    path, r = ([m for m in matching if m[1].get("status") == "ok"] or matching)[-1]
    out = {k: r.get(k) for k in ("mode", "status", "candidate", "resolution", "samples", "problem")}
    out.update(frames=len(r.get("files") or []), device=(r.get("device") or {}).get("device_type"),
               seconds=r.get("render_seconds"), report=str(path.relative_to(case)),
               skipped_reports=len(reports) - 1)
    if isinstance(r.get("movie"), dict):
        out["movie"] = {k: r["movie"].get(k) for k in ("status", "path", "frame_count", "fps", "problem")}
    return out


def setup_probe(candidate: Path, workspace: Path) -> dict:
    info = worldagent_env.find_blender()
    if not info["path"]:
        return {"error": info["problem"]}
    out = workspace / "setup_probe.json"
    cmd = [info["path"], "--background", "--factory-startup", str(candidate), "--python-exit-code", "1",
           "--python-expr", f"import sys; sys.path.insert(0, {json.dumps(str(LIB))})",
           "--python", str(PROBE), "--", "--out", str(out)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if proc.returncode != 0 or not out.is_file():
        return {"error": f"probe exited {proc.returncode}: {proc.stderr.strip()[-500:]}"}
    return json.loads(out.read_text(encoding="utf-8"))


def evaluate(workspace: Path, probe: bool) -> dict:
    case = workspace / "case"
    result: dict = {"case_dir": str(case)}
    if not (case / "manifest.json").is_file():
        result["problem"] = "no manifest.json: the agent did not create the case"
        return result
    manifest = json.loads((case / "manifest.json").read_text(encoding="utf-8"))
    result["status"] = manifest.get("status")
    result["budget"] = manifest.get("budget")
    rows = [r for r in manifest.get("requirements_matrix") or [] if isinstance(r, dict)]
    result["rows"] = len(rows)
    candidate = candidate_of(manifest, case)
    result["candidate"] = str(candidate.relative_to(case)) if candidate else None
    try:
        report = gate.evaluate(case, candidate)
        result["gate_pass"] = report["pass"]
        result["gate_errors"] = report["errors"]
        result["not_passing"] = {v["row"]: v["verdict"] for v in report["rows"] if v["verdict"] != "pass"}
        result["contract_changes"] = [c["row"] for c in report.get("contract_changes", [])]
    except gate.Unreadable as exc:
        result["gate_error"] = str(exc)
    events = case_trace.read(case)
    notes = [e for e in events if e.get("type") == "note"]
    result["trace"] = {"events": len(events), "notes": len(notes),
                       "note_results": sorted({str(n.get("result")) for n in notes}),
                       "candidates_verified": len({e.get("candidate") for e in events if e.get("type") == "verify"})}
    result["render"] = final_render_of(case, candidate)
    final = case / FINAL
    result["final_scene"] = final.is_file()
    if probe and candidate:
        result["setup_probe"] = setup_probe(candidate, workspace)
    return result


def flag_probe(probe: dict | None) -> str:
    if not probe:
        return "-"
    if probe.get("error"):
        return "probe error"
    failing = [k for k, v in probe.items() if isinstance(v, dict) and v.get("passed") is False]
    return ", ".join(failing) if failing else ("ok" if probe.get("rigid_bodies") else "no rigid bodies")


def write_summary(out: Path, results: list[dict]) -> None:
    (out / "summary.json").write_text(json.dumps(results, indent=2, ensure_ascii=False, default=str) + "\n",
                                      encoding="utf-8")
    lines = ["| case | agent h | exit | status | gate | not passing | contract changes | notes | render | final | setup review |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        run = r.get("run") or {}
        exit_text = "timeout" if run.get("timed_out") else run.get("exit_code", "-")
        render = r.get("render") or {}
        if render.get("mode"):
            render_text = (f"{render['mode']} {render.get('status')} {render.get('frames')}f "
                           f"{'x'.join(map(str, render.get('resolution') or []))}")
        else:
            render_text = render.get("problem") or "none"
        not_passing = ", ".join(f"{k}:{v}" for k, v in (r.get("not_passing") or {}).items()) or "-"
        lines.append(f"| {r['id']} | {run.get('agent_hours', '-')} | {exit_text} | {r.get('status', r.get('problem', '-'))} "
                     f"| {r.get('gate_pass', '-')} | {not_passing} | {', '.join(r.get('contract_changes') or []) or '-'} "
                     f"| {(r.get('trace') or {}).get('notes', '-')} | {render_text} | {r.get('final_scene', '-')} "
                     f"| {flag_probe(r.get('setup_probe'))} |")
    (out / "summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    if not args.check_only and not args.agent:
        sys.exit("--agent is required unless --check-only")
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"]
    wanted = {c.strip() for c in args.only.split(",") if c.strip()}
    unknown = wanted - {c["id"] for c in cases}
    if unknown:
        sys.exit(f"unknown case ids: {', '.join(sorted(unknown))}")
    cases = [c for c in cases if not wanted or c["id"] in wanted]
    budget = args.budget_hours or worldagent_env.time_budget(None)["hours"]
    timeout_s = (args.timeout_hours or budget * 1.5 + 0.5) * 3600
    if not args.dry_run:
        args.out.mkdir(parents=True, exist_ok=True)
    results = []
    for case in cases:
        workspace = (args.out / case["id"]).resolve()
        entry = {"id": case["id"], "kind": case.get("kind")}
        if not args.check_only:
            prompt = (args.prompt_template or PROMPTS[args.agent]).format(case="case", request=case["request"])
            cmd = command_for(args, prompt)
            if args.dry_run:
                print(f"[{case['id']}] cwd={workspace}\n  " + shlex.join(cmd))
                continue
            if (workspace / "case").exists():
                sys.exit(f"{workspace / 'case'} exists; use another --out or --check-only")
            workspace.mkdir(parents=True, exist_ok=True)
            env = {**os.environ, "WORLDAGENT_TIME_BUDGET_HOURS": f"{budget:g}"}
            print(f"[{case['id']}] running {args.agent} (budget {budget:g} h, timeout {timeout_s / 3600:.1f} h)", flush=True)
            entry["run"] = {"agent": args.agent, "command": cmd, **run_agent(cmd, workspace, env, timeout_s)}
            (workspace / "run.json").write_text(json.dumps(entry["run"], indent=2) + "\n", encoding="utf-8")
            print(f"[{case['id']}] agent finished: {entry['run']}", flush=True)
        elif (workspace / "run.json").is_file():
            entry["run"] = json.loads((workspace / "run.json").read_text(encoding="utf-8"))
        if args.dry_run:
            continue
        entry.update(evaluate(workspace, probe=not args.no_probe))
        results.append(entry)
        write_summary(args.out, results)
    if results:
        print((args.out / "summary.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
