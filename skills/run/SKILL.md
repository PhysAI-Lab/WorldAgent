---
name: run
description: Run a WorldAgent case end to end from one scene request, without stopping for approval; spec, build and bake, verify and refine, final render, promote. Resumes an existing case when --case names one.
argument-hint: [--case <case_dir>] <scene request>
disable-model-invocation: true
---

**Host compatibility (Codex fallback).** Keep Claude Code's native token substitution. If `${CLAUDE_PLUGIN_ROOT}` is still visible literally, replace it with the absolute path from `WorldAgent plugin root:` in the session-start report before reading files or running commands, including paths in referenced documents; quote the resolved paths. If there is no session-start report (for example, the plugin hooks are not trusted yet), the plugin root is the folder two levels above the directory that contains this SKILL.md. If `$ARGUMENTS` is still visible literally, it means the user text supplied with this skill, preserving its quoted arguments. Do not rely on those tokens being shell environment variables.

Codex only: shell exports from SessionStart are not assumed. Set `WORLDAGENT_ENV_FILE` to the `WorldAgent session environment:` path from that report for shell commands. Where the body refers to an unset `WORLDAGENT_*` variable (including assets and memory/time budgets), use its resolved value from the report. Use `WORLDAGENT_*` or the WorldAgent config file instead of Claude's `/plugin configure`; invoke `/worldagent:<name>` commands as `$worldagent:<name>`.

Codex only: where the body says to invoke the Claude `Skill` tool with `worldagent:scene-simulation`, read `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/SKILL.md` instead, resolving the root as above.

# Run a WorldAgent case end to end

Arguments: `$ARGUMENTS` — optionally `--case <case_dir>`, then the user's exact scene request. The user asked for the whole pipeline, so do not stop to ask for approval between stages; ask only when a step below says to. The session start sets `WORLDAGENT_BLENDER`; if it reported Blender as not found, stop and ask the user to set the plugin's Blender path.

1. Invoke the Skill tool with `worldagent:scene-simulation` and follow it for every stage below.
2. Choose the case directory and say which one in one line:
   - with `--case`: use it. If it holds a `manifest.json`, resume: read the manifest and its `status`, skip what is already done, and continue at the first unfinished step below. If it exists, is not empty, and has no `manifest.json`, stop and ask; never delete or reuse its contents. If no request was given, resume only.
   - without `--case`: use `cases/<name>` under the current directory, with `<name>` a short snake_case name for the scene taken from the request (letters, digits, `_`). If that directory exists, add `_2`, `_3`, and so on; never write into an existing case.
   - with neither a request nor an existing case: ask for the request.
   **Time budget.** Use the budget the user gave in the request, else `$WORLDAGENT_TIME_BUDGET_HOURS` (the session-start report shows it; the default is 3 h). Record it in `manifest.json` as `budget` with `time_hours`, `source`, and `started_utc` (keep the original start when resuming). Before every new bake or refinement round, compare the elapsed time plus the expected duration of that round (from the bakes so far) and of the final render against the budget; if it would not fit, stop refining (step 5).
3. **Spec.** Follow steps 1–6 of `${CLAUDE_PLUGIN_ROOT}/skills/new/SKILL.md` (create the case, world spec, asset choices, requirements matrix, spec audit until it passes). Then report the beats and rows in a short list and continue without waiting.
4. **Build and bake.** Follow steps 2–3 of `${CLAUDE_PLUGIN_ROOT}/skills/bake/SKILL.md`.
5. **Verify and refine.** Follow steps 1–3 of `${CLAUDE_PLUGIN_ROOT}/skills/verify/SKILL.md` for every row except the render and media rows. For each failing row, change the scene or the bake, one causal group at a time, bake a new candidate, verify again, and record each attempt with `case_trace.py note`. Never weaken a verifier, a tolerance, a `pass_predicate`, or the world spec (a beat's window, an object's collision or constraints) to make a row pass (`references/writing-verifiers.md`, "Changing a verifier or the spec after a fail"). Stop refining when every row passes, when an attempt makes no measured progress, when the next round would not fit the time budget, or when a failing row is below the solver's resolution (the skill's Failure and Retry Policy). If rows still fail, set the case to `limited`, render the candidate with the most passing rows as in step 6 so the user has a video, report the rows that still fail with their measured values, and do not promote.
6. **Final render.** When every other row passes, follow steps 2–3 of `${CLAUDE_PLUGIN_ROOT}/skills/render/SKILL.md` for that candidate. A `blocked` render (device not available) stops here; report it.
7. **Promote.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/bin/promote.py <case_dir> --candidate <candidate>`. If `scene_final.blend` already exists, do not pass `--replace` unless the user said to replace it; report and stop.
8. **Report** briefly:
   - promoted or not, the final scene and its SHA-256, and the gate report path;
   - every row with its verdict, and the first failure for any row that does not pass;
   - the render: device, samples, frame count, and the `media/` folder;
   - every row in `contract_changes`, with what changed (verifier copies, `pass_predicate`, world spec);
   - assets used, approximations, and anything blocked or skipped.
   Call the case done only if promote exited 0.
