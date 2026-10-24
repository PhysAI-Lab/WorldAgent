---
name: new
description: Start a new WorldAgent physics scene case from a prompt; creates the case folder, writes the world spec and requirements matrix, and audits the spec before any building. Use /worldagent:bake next.
argument-hint: <case_dir> <exact scene request>
disable-model-invocation: true
---

**Host compatibility (Codex fallback).** Keep Claude Code's native token substitution. If `${CLAUDE_PLUGIN_ROOT}` is still visible literally, replace it with the absolute path from `WorldAgent plugin root:` in the session-start report before reading files or running commands, including paths in referenced documents; quote the resolved paths. If there is no session-start report (for example, the plugin hooks are not trusted yet), the plugin root is the folder two levels above the directory that contains this SKILL.md. If `$ARGUMENTS` is still visible literally, it means the user text supplied with this skill, preserving its quoted arguments. Do not rely on those tokens being shell environment variables.

Codex only: shell exports from SessionStart are not assumed. Set `WORLDAGENT_ENV_FILE` to the `WorldAgent session environment:` path from that report for shell commands. Where the body refers to an unset `WORLDAGENT_*` variable (including assets and memory/time budgets), use its resolved value from the report. Use `WORLDAGENT_*` or the WorldAgent config file instead of Claude's `/plugin configure`; invoke `/worldagent:<name>` commands as `$worldagent:<name>`.

Codex only: where the body says to invoke the Claude `Skill` tool with `worldagent:scene-simulation`, read `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/SKILL.md` instead, resolving the root as above.

# New WorldAgent case

Arguments: `$ARGUMENTS` — the first word is the case directory; the rest is the user's exact scene request. If either is missing, ask for it.

1. Invoke the Skill tool with `worldagent:scene-simulation`, then read `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/project-pipeline-and-template.en.md` and `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/simulation-workflow.en.md`.
2. If the case directory exists and is not empty, stop and ask the user; never delete or reuse its contents. Otherwise write the exact request, verbatim, to a temporary file outside the case and create the case:
   `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/init_case.py <case_dir> --name <name> --prompt-file <request.txt> --simulation-class <class> [--fps N --frame-start A --frame-end B]`
   Use the case directory's basename as `<name>` (letters, digits, `_`, `-` only). Classes: rigid_body, cloth, liquid, gas, particles, granular_approximation, soft_body, coupled; repeat `--simulation-class` for coupled cases. Choose them from the skill's solver classification.
3. Write the world spec into `<case_dir>/manifest.json` (Stage 0): `contract.frame_range`, `contract.fps`, story beats under `contract.story.beats`, one entry per claimed object under `object_contracts`, and the scene graph under `topology`. Follow `${CLAUDE_PLUGIN_ROOT}/schemas/world_spec.schema.json`.
4. If `$WORLDAGENT_ASSET_LIBRARY` is set, choose assets from it before planning to build any (library-first). Read its `index.json` if present, otherwise list its folders and each asset's `meta.json`. For every visible object in `object_contracts`, add an entry to a top-level `asset_sources` list in `manifest.json`: `object`, `library_path` (relative to the library, or null), `sha256`, `license`, and `reason`. Use a library asset when its category and real-world size fit the object contract; when none fits, set `library_path` to null and say why. If the variable is not set, skip this step.
5. Write `requirements_matrix`: one row per hard requirement with `id`, `text`, `category` (one of target_asset_realism, scene, physics, story_time, materials, camera, media, delivery), a numeric `pass_predicate`, status `unknown`, and `covers` naming the beats it verifies (a story row may instead reuse its beat id as its row id). Every beat must be covered. Follow `${CLAUDE_PLUGIN_ROOT}/schemas/requirements.schema.json`.
6. Run `python3 ${CLAUDE_PLUGIN_ROOT}/bin/spec_audit.py <case_dir>/manifest.json`. Fix every error and run it again until it passes.
7. Report the beats, the rows, the asset choices, and the audit result. Say that the case is at status `contract` and not yet verified; describe the audit as passing, never the case as done or complete. Stop here: do not start building until the user agrees with the spec and asks for the bake (`/worldagent:bake <case_dir>`).
