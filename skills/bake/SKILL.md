---
name: bake
description: Build and bake a candidate for a WorldAgent case whose spec is written; stops after the bake so the user can look before verifying. Use /worldagent:verify next.
argument-hint: <case_dir> [notes for this bake]
disable-model-invocation: true
---

**Host compatibility (Codex fallback).** Keep Claude Code's native token substitution. If `${CLAUDE_PLUGIN_ROOT}` is still visible literally, replace it with the absolute path from `WorldAgent plugin root:` in the session-start report before reading files or running commands, including paths in referenced documents; quote the resolved paths. If there is no session-start report (for example, the plugin hooks are not trusted yet), the plugin root is the folder two levels above the directory that contains this SKILL.md. If `$ARGUMENTS` is still visible literally, it means the user text supplied with this skill, preserving its quoted arguments. Do not rely on those tokens being shell environment variables.

Codex only: shell exports from SessionStart are not assumed. Set `WORLDAGENT_ENV_FILE` to the `WorldAgent session environment:` path from that report for shell commands. Where the body refers to an unset `WORLDAGENT_*` variable (including assets and memory/time budgets), use its resolved value from the report. Use `WORLDAGENT_*` or the WorldAgent config file instead of Claude's `/plugin configure`; invoke `/worldagent:<name>` commands as `$worldagent:<name>`.

Codex only: where the body says to invoke the Claude `Skill` tool with `worldagent:scene-simulation`, read `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/SKILL.md` instead, resolving the root as above.

# Bake a WorldAgent candidate

Arguments: `$ARGUMENTS` — the case directory, then optionally notes from the user for this bake (for example, what to change since the last candidate). The session start sets `WORLDAGENT_BLENDER`; if it reported Blender as not found, ask the user to set the plugin's Blender path.

1. Invoke the Skill tool with `worldagent:scene-simulation`. Read `<case_dir>/manifest.json`. If it has no world spec or `spec_audit.py` does not pass on it, stop and tell the user to finish the spec with `/worldagent:new` first.
2. Build the scene, library-first when `$WORLDAGENT_ASSET_LIBRARY` is set, following the skill's Stages 1–4: static audit, probes, then the bake. Keep the solver resolution, bake time and memory within any budget the user gave, and record the choices in the manifest.
3. Save the baked candidate to `<case_dir>/.work/candidates/<id>/scene.blend` with a new `<id>`; never overwrite an earlier candidate. Keep caches under `.work/`. If this bake changes something to fix a failing row, record it with `case_trace.py note`.
4. Report and stop: the candidate path, what was built and baked, solver settings, bake time, anything the probes or the bake showed (instability, penetration, missing cache), and approximations. Do not verify, render, or promote. Suggest `/worldagent:verify <case_dir>` as the next step.
