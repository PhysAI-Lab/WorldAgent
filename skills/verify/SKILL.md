---
name: verify
description: Run a WorldAgent case's verifiers through the plugin runner on a candidate and report the gate verdict for each requirement row.
argument-hint: <case_dir> [candidate.blend] [row ids...]
disable-model-invocation: true
---

**Host compatibility (Codex fallback).** Keep Claude Code's native token substitution. If `${CLAUDE_PLUGIN_ROOT}` is still visible literally, replace it with the absolute path from `WorldAgent plugin root:` in the session-start report before reading files or running commands, including paths in referenced documents; quote the resolved paths. If there is no session-start report (for example, the plugin hooks are not trusted yet), the plugin root is the folder two levels above the directory that contains this SKILL.md. If `$ARGUMENTS` is still visible literally, it means the user text supplied with this skill, preserving its quoted arguments. Do not rely on those tokens being shell environment variables.

Codex only: shell exports from SessionStart are not assumed. Set `WORLDAGENT_ENV_FILE` to the `WorldAgent session environment:` path from that report for shell commands. Where the body refers to an unset `WORLDAGENT_*` variable (including assets and memory/time budgets), use its resolved value from the report. Use `WORLDAGENT_*` or the WorldAgent config file instead of Claude's `/plugin configure`; invoke `/worldagent:<name>` commands as `$worldagent:<name>`.

# Verify a WorldAgent case

Arguments: `$ARGUMENTS` — the case directory, then optionally the candidate `.blend` and row ids. Without a candidate, use the most recently modified `<case_dir>/.work/candidates/*/scene.blend` and say which one you chose; if none exists, stop and ask. Without row ids, verify every row. The session start sets `WORLDAGENT_BLENDER`; if it reported Blender as not found, ask the user to set the plugin's Blender path (`/plugin configure worldagent`).

1. Read `<case_dir>/manifest.json`. For each selected row, find its verifier (`verified_by`, or `<case_dir>/src/verify_<row>.py`). If a row has no verifier, write one following `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/writing-verifiers.md`, using the thresholds in the row's `tolerances` and `measures` (stated in words in `pass_predicate`).
2. Run each verifier through the runner, with the same candidate path for every row, passing every cache or case module it reads as `--input` (for hashing; the runner points the candidate copy's relative cache paths at the candidate's folder, so the verifier does not set cache folders itself):
   `python3 ${CLAUDE_PLUGIN_ROOT}/bin/run_verifier.py <case_dir> --row <id> --script <verifier> --candidate <candidate> [--input <path> ...]`
3. Run the gate on the same candidate: `python3 ${CLAUDE_PLUGIN_ROOT}/bin/gate.py <case_dir> --candidate <candidate>`. If it reports stale rows, re-run those rows (step 2) rather than editing them.
4. Report:
   - a table of rows with verdict (`pass`, `fail`, `unknown`, `blocked`, `stale`, or `unverified` — status pass without current runner evidence on this candidate), the first failure or reason, and the report path;
   - any gate errors (spec audit, missing beats, unknown `covers`);
   - every verifier written in this run, with the threshold it uses.
   - any row that became `unknown` because the verifier changed a cache it read.
   - every row in the gate report's `contract_changes`: its verifier, an imported case module, `pass_predicate`, `tolerances`, `measures`, or the world spec was changed after a non-pass result. Give the row, the previous status, what changed (`spec_changed`, `pass_predicate_changed`, `tolerances_changed`, `measures_changed`, `inputs_changed`), and the two verifier copies (`from.copy`, `to.copy`) when the verifier changed. This does not change the verdict, but always report it.
   - every object in the gate report's `solver_deviations` (a Blender system that is not preferred for its `simulation_class`), with its recorded reason.
   Do not edit row statuses or reports yourself, and do not call the case complete; only promote does that.
5. Stop after the report. Do not fix failing rows, re-bake, render, or promote unless the user asks. Suggest the next step: a fix and a new candidate with `/worldagent:bake`, or `/worldagent:render` when only the render and media rows remain.
