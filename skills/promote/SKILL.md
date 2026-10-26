---
name: promote
description: Promote a WorldAgent candidate to scene_final.blend; re-runs every verifier on the candidate and installs it only if the gate passes. Requires every row to have been run by /worldagent:verify.
argument-hint: <case_dir> [candidate.blend] [--replace]
disable-model-invocation: true
---

**Host compatibility (Codex fallback).** Keep Claude Code's native token substitution. If `${CLAUDE_PLUGIN_ROOT}` is still visible literally, replace it with the absolute path from `WorldAgent plugin root:` in the session-start report before reading files or running commands, including paths in referenced documents; quote the resolved paths. If there is no session-start report (for example, the plugin hooks are not trusted yet), the plugin root is the folder two levels above the directory that contains this SKILL.md. If `$ARGUMENTS` is still visible literally, it means the user text supplied with this skill, preserving its quoted arguments. Do not rely on those tokens being shell environment variables.

Codex only: shell exports from SessionStart are not assumed. Set `WORLDAGENT_ENV_FILE` to the `WorldAgent session environment:` path from that report for shell commands. Where the body refers to an unset `WORLDAGENT_*` variable (including assets and memory/time budgets), use its resolved value from the report. Use `WORLDAGENT_*` or the WorldAgent config file instead of Claude's `/plugin configure`; invoke `/worldagent:<name>` commands as `$worldagent:<name>`.

# Promote a WorldAgent candidate

Arguments: `$ARGUMENTS` — the case directory, optionally the candidate `.blend`, and optionally `--replace` to supersede an existing final scene. Without a candidate, use the one every row's `candidate` field names when they all agree; otherwise ask. The session start sets `WORLDAGENT_BLENDER`; if it reported Blender as not found, ask the user to set the plugin's Blender path (`/plugin configure worldagent`).

1. Read `<case_dir>/manifest.json`. Promote re-runs only verifiers the runner has already recorded: if any row has no `verified_by`, stop and tell the user to run `/worldagent:verify` first. The render and media rows need the final render; if they were never measured, tell the user to run `/worldagent:render` first.
2. Run `python3 ${CLAUDE_PLUGIN_ROOT}/bin/promote.py <case_dir> --candidate <candidate> [--replace]`. It re-runs every row's verifier on the candidate in a fresh Blender process, runs the gate, and only then installs `scene_final.blend`. This can take as long as all verifiers together.
3. Exit 0: report the final scene, its SHA-256, and the gate report path (JSON on stdout). If `contract_changes` is not empty, list each row with its previous status and what changed (verifier copies, `pass_predicate`, world spec), and tell the user to review them: these rows pass only after their verifier, a case module it imports, `pass_predicate`, `tolerances`, `measures`, or the world spec was changed following a failure. Also list every object in `solver_deviations` with its reason, and every entry in `final_relative_paths`: relative paths (caches written next to the candidate, disk point caches) that `scene_final.blend` in the case root would resolve to a different place. Promote does not rewrite them; opening `scene_final.blend` directly finds those caches only after they are moved there or written as absolute paths.
4. Exit 1: the gate or a check refused. Report the reason and every failing row with its first failure or reason (JSON on stdout or stderr). Exit 2: a setup error (missing candidate, Blender not found, unreadable manifest), not a failed gate; report it as such.
5. Never copy or write `scene_final.blend` any other way, and do not describe the case as complete unless promote exited 0.
