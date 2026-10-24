# World Agent

A plugin for Claude Code and Codex for building physically grounded Blender scenes whose requirements are measured, not claimed. Each case starts with a world spec (story beats, object contracts, scene topology) and a requirements matrix. Every row in the matrix is checked by a verifier script that the plugin's runner executes in a fresh Blender process on a copy of the candidate `.blend`. A row becomes `pass` only from a measured result. A gate decides whether a candidate is complete, and only `promote` installs it as `scene_final.blend`, after it re-measures every row.

For a detailed explanation of the checks and the safeguards against cheating, see [How WorldAgent Verifies a Scene](../docs/plugin/verification-overview.md).

## Requirements

- **Claude Code or Codex CLI** with plugin support. Codex integration is tested with CLI 0.164.0.
- **Blender 5.1.** Found automatically (the plugin setting, `WORLDAGENT_BLENDER`, `blender` on `PATH`, or the usual install location for your OS); see [Configuration](#configuration).
- **Python 3.** The runtime scripts and hooks use only the standard library.
- **FLIP Fluids 1.8.8 add-on (optional).** It is not bundled. Install your own licensed copy; the plugin finds it in Blender's user add-on or extension folders, or in a folder you configure. The installer must match the checksum in `scripts/FLIP/SHA256SUMS.txt`. Do not copy the installer into a case, Git or a delivery bundle. Without the add-on, FLIP requirement rows are recorded as `blocked`, not as passed or skipped. They never fall back to Mantaflow.
- **A render device** for the final Cycles render (1920x1080 unless you set another resolution). The default is NVIDIA OptiX; set the plugin's render device to `CUDA`, `HIP`, `METAL`, `ONEAPI`, `ANY_GPU` (first available GPU) or `CPU` to use another. If the configured device is not available, the render is reported as blocked.

## Install and load

From the marketplace (replace `<owner>/<repo>` with the repository that holds this plugin):

```
/plugin install worldagent --marketplace <owner>/<repo>
```

During development, from the repository root:

```bash
claude --plugin-dir ./worldagent-plugin
```

After you change plugin files, run `/reload-plugins` in the session. To check the plugin structure:

```bash
claude plugin validate worldagent-plugin
```

### Codex CLI

From the repository root, register the local catalog and install the plugin:

```bash
codex plugin marketplace add .
codex plugin add worldagent@worldagent
codex plugin list --marketplace worldagent --json
```

The repository's `.agents/plugins/marketplace.json` and `.claude-plugin/marketplace.json`
both point to `./worldagent-plugin`. The existing catalog inside the plugin is retained for Claude development compatibility. The two manifests share the same skills, scripts
and hooks. For a repository hosted elsewhere, the marketplace command also accepts
an owner/repository or Git URL.

Start a new Codex session and review/trust the plugin hooks in `/hooks`. Trust is tied
to the hook contents; changed hooks require review again. This is host setup, not an
approval between simulation stages. The session-start report must show the plugin
root and environment file before relying on hooks or detected settings.

Use `$worldagent:new`, `$worldagent:bake`, `$worldagent:verify`, `$worldagent:render`,
`$worldagent:promote`, or `$worldagent:run`, followed by the same arguments shown below.
For example, `$worldagent:run --case my_case <scene request>` runs the full pipeline.
The six command skills require explicit invocation; `scene-simulation` can load when
relevant to the request. Shell prompts containing `$` must be single-quoted.

Plugins are installed into a cache. After editing the source, reinstall with
`codex plugin add worldagent@worldagent` and start a new session; verify the installed
copy before testing. Use a local/repository marketplace for this hook-bearing plugin.

Shared skill bodies retain Claude Code's `${CLAUDE_PLUGIN_ROOT}` and `$ARGUMENTS`
tokens and its native Skill-tool instructions. A compatibility note at the top of
each skill tells Codex how to resolve unexpanded tokens from the session-start report
and user text, read the main skill in place of the Claude tool, and use reported
settings when shell variables are unset. Claude keeps its native substitution.

## Configuration

At the start of every session a hook checks the machine and prints a short report: the Blender version and path, whether FLIP Fluids was found (and its version), the render device, the asset library's folders, total memory, and the time budget for `/worldagent:run`. In Claude Code it exports the resolved `WORLDAGENT_*` values to shell commands. In Codex it saves a separate environment file for each session; the runner and Blender helpers can read it without inheriting the hook process environment. Values come from explicit plugin/tool settings, environment variables, a saved session snapshot, then the config file; remaining values use the existing detection and defaults. SessionStart refreshes detection without reusing an old snapshot.

| Setting | What it sets | Left empty |
|---|---|---|
| Blender executable | `WORLDAGENT_BLENDER` | `WORLDAGENT_BLENDER` from your shell, then `blender` on `PATH`, then the usual install locations for macOS, Linux or Windows |
| FLIP Fluids folder | `WORLDAGENT_FLIP_PATH`, the folder that contains `flip_fluids_addon` | Blender's user add-on and extension folders for that Blender version; if not found, FLIP rows are `blocked` |
| FLIP Fluids installer | Checks the zip against `scripts/FLIP/SHA256SUMS.txt` and reports the result | No check |
| Render device | `WORLDAGENT_RENDER_DEVICE`; the final render sets Cycles to this device (`lib/worldagent_measure/render.py`) | `WORLDAGENT_RENDER_DEVICE` from your shell, else `OPTIX` |
| Asset library folder | `WORLDAGENT_ASSET_LIBRARY`; the skill browses it before building assets procedurally and records every asset it uses | `WORLDAGENT_ASSET_LIBRARY` from your shell; otherwise no library, and assets are built procedurally |
| Final render resolution | `WORLDAGENT_RENDER_RESOLUTION`, `WIDTHxHEIGHT`; final renders and the render check use it | `WORLDAGENT_RENDER_RESOLUTION` from your shell, else 1920x1080 |
| Render samples | `WORLDAGENT_RENDER_SAMPLES`; Cycles samples for previews and the final render (`--samples` overrides it per run) | `WORLDAGENT_RENDER_SAMPLES` from your shell, else each scene's own setting |
| Time budget for `/worldagent:run` (hours) | `WORLDAGENT_TIME_BUDGET_HOURS`; `/worldagent:run` records it in the manifest and stops refining when the next round would not fit, renders its best candidate and reports the case as `limited` | `WORLDAGENT_TIME_BUDGET_HOURS` from your shell, else 3 hours |
| Memory budget per Blender process (GB) | `WORLDAGENT_MEMORY_BUDGET_GB`; the skill keeps every single Blender process below it | The skill's memory-safety rules apply as written |

In Claude Code, open the settings with `/plugin configure worldagent` (with `--plugin-dir` the settings dialog does not open on its own), or check the machine at any time:

```bash
python3 worldagent-plugin/bin/worldagent_env.py          # add --json for machine-readable output
```

### Config file and session handoff

Codex users can export `WORLDAGENT_*` before launching the CLI or create
`~/.config/worldagent/config.json` (`$XDG_CONFIG_HOME/worldagent/config.json` when set).
`WORLDAGENT_CONFIG_FILE` selects another file. Keys match the Claude settings:
`blender_path`, `flip_addon_path`, `flip_installer`, `render_device`,
`render_resolution`, `render_samples`, `asset_library_path`, `memory_budget_gb`,
and `time_budget_hours`. Omitted keys retain existing detection and defaults.
For example, to explicitly choose CPU rendering:

```json
{
  "render_device": "CPU"
}
```

The session hook prints `WorldAgent plugin root:` and `WorldAgent session environment:`.
In Codex, prefix commands with `WORLDAGENT_ENV_FILE="<reported environment file>"`
(or export it again in each new shell). Do not assume one shell's exports reach the next.
The helpers also find the snapshot using a Codex session/thread ID when inherited.
Explicit `WORLDAGENT_*` variables override snapshot values. The snapshot is stored
under `PLUGIN_DATA` when available, with a locator under the user cache directory;
its name is unique to the session and plugin installation. A resumed Codex session keeps
its original start time. Claude retains its existing transcript-based session timing and environment-file exports. Starting a new session picks up changed config settings.

## Usage

| Command | Arguments | What it does |
|---|---|---|
| `/worldagent:new` | `<case_dir> <exact scene request>` | Creates the case folder (`scripts/init_case.py`), writes the world spec and the requirements matrix into `manifest.json`, and runs the spec audit until it passes. The case is then at status `contract`. |
| `/worldagent:bake` | `<case_dir> [notes]` | Builds the scene and bakes a new candidate to `.work/candidates/<id>/scene.blend`, then stops so you can look before verifying. |
| `/worldagent:verify` | `<case_dir> [candidate.blend] [row ids...]` | Runs each row's verifier through the runner on one candidate, writes missing verifiers from the row's `pass_predicate`, then runs the gate and reports a verdict per row. |
| `/worldagent:render` | `<case_dir> [candidate.blend] [--preview --frames <list>]` | Renders the final Cycles sequence with `bin/render_candidate.py` (or preview frames) and verifies the render and media rows. |
| `/worldagent:promote` | `<case_dir> [candidate.blend] [--replace]` | Re-runs every verifier on the candidate, runs the gate, and installs `scene_final.blend` only if the gate passes. |
| `/worldagent:run` | `[--case <case_dir>] <scene request>` | Runs the whole pipeline without stopping for approval: spec, build and bake, verify and refine, final render, promote. Without `--case` it creates `cases/<name>` from the request; with `--case` naming an existing case it resumes where the case stopped. A case whose rows still fail after refinement is reported as `limited` and is not rendered or promoted. |

Each command does its own step and stops; `/worldagent:run` does them all. These six commands are user-only in both hosts. The main skill, `scene-simulation`, loads automatically when you ask the agent to build, simulate, bake, validate or render a physics scene, or to work on a case.

Typical flow, step by step (`/worldagent:run "<scene request>"` does all of it in one go):

1. `/worldagent:new my_case "<scene request>"`: review the beats and rows, and agree on the spec.
2. `/worldagent:bake my_case`: builds and bakes a candidate to `<case_dir>/.work/candidates/<id>/scene.blend`, then stops.
3. `/worldagent:verify my_case`: reports a verdict per row and stops. Fix the scene for each failing row, bake a new candidate, and verify again. Rows whose inputs changed show as `stale` and must be run again.
4. `/worldagent:render my_case`: runs `bin/render_candidate.py`, which renders every frame of the candidate at the final resolution with Cycles on the configured device into `media/final_<stamp>/` and writes `reports/render_<stamp>.json` (candidate hash, device, settings, frame hashes). The render rows check that report; run `/worldagent:verify my_case` again for them.
5. `/worldagent:promote my_case`: on success it reports the final scene, its SHA-256 and the gate report.

Row verdicts are `pass`, `fail`, `unknown`, `blocked`, `stale` and `unverified` (status `pass` without current runner evidence on this candidate).

Every runner, gate and promote run is logged to `<case>/reports/trace.jsonl`, with a copy of the spec each time it changes; the agent adds a note for each refinement attempt. `python3 worldagent-plugin/bin/case_trace.py show <case_dir>` prints the history, with what the runner measured next under each note. The trace is a log only: no verdict depends on it.

## What is enforced

- **Write guard** (`hooks/guard_final_scene.py`, PreToolUse). Blocks file-tool writes (including Codex patch add/update/delete/move targets and added save code), shell commands (copy, move, link, delete, redirect and similar) and Python saves whose target is `scene_final.blend`. A command segment that runs the plugin's `promote.py` is allowed. Reading the final scene is allowed. This is a guardrail against mistakes, not a security boundary.
- **Done check** (`hooks/stop_unverified.py`, Stop). If the agent's last message claims completion and a case changed in this session is not `validated`, the stop is blocked once and the rows that do not pass are listed. A second stop in a row is always allowed.

- **Contract changes** (`bin/run_verifier.py`, listed by the gate and promote). The runner keeps a copy of every verifier version under `reports/trace/verifiers/` and records in the row when its verifier, its `pass_predicate`, or the world spec (beats, object contracts, topology) changed since the row's last measurement. The gate report and promote output list every row where such a change came after a non-pass result, with what changed and both verifier copies. A rewritten verifier, a loosened threshold, a moved beat window, or a swapped collision setup can make a row pass without meeting the original requirement; this list shows where to look. It does not change any verdict.

Only `bin/promote.py` writes `scene_final.blend`. It re-runs every row's verifier on that exact candidate, runs the gate, checks that the copied file has the hash every row was measured on, and only then installs it. An existing final scene is moved to `.work/superseded/`, never deleted.

## Layout

```
worldagent-plugin/
├── .claude-plugin/plugin.json   Claude manifest and settings
├── .codex-plugin/plugin.json    Codex manifest
├── skills/
│   ├── scene-simulation/        main skill and its English references
│   ├── new/  bake/  verify/  render/  promote/  run/  user-only slash commands
├── hooks/                       session environment, write guard, done check
├── bin/
│   ├── spec_audit.py            audits the world spec before building
│   ├── run_verifier.py          runs one verifier in a fresh Blender process, records the result
│   ├── gate.py                  decides whether a candidate is complete (no Blender)
│   ├── promote.py               re-verifies, gates and installs scene_final.blend
│   ├── render_candidate.py      renders a candidate (final sequence or previews) and writes a render report
│   ├── worldagent_env.py        finds Blender, FLIP Fluids and memory (used by the runner and the session hook)
│   └── case_trace.py            case history: tool runs, spec changes, refinement notes
├── lib/worldagent_runtime.py    shared config and session handoff
├── lib/worldagent_measure/      measurement helpers that verifiers import inside Blender
│                                (contact, scale, numerical, support, navigability, liquid, solver_mesh,
│                                 causal, shot, gas, deformable, render, rigid_setup)
├── schemas/                     JSON schemas for the world spec and requirements matrix
├── scripts/                     case setup and layout check, split-archive check, FLIP checksum
└── tests/                       unit and Blender tests, fixtures
```

Configuration helper changes also invalidate previous measurement hashes. The measurement helpers measure and return results. They never pick a threshold. Every threshold comes from the row's `pass_predicate`.

## Running the tests

From the plugin root:

```bash
export WORLDAGENT_BLENDER=/path/to/blender
uv run --no-project --with jsonschema python -m unittest discover -s tests -p 'test_*.py'
```

Tests that need Blender are skipped when no Blender is found. To run the test scenes end to end through an agent (Codex or Claude Code) and get a summary table, use `tests/e2e/run_batch.py` (see its `--help`; the scenes are in `tests/e2e/cases.json`). The Codex batch command uses `--approve-for-me`: a workspace-write sandbox with automatic review of execution-approval requests. Device access can differ from the SessionStart probe: an OPTIX device visible to the hook may be unavailable inside the agent shell. Retry the device probe using the host's permitted escalation mechanism. If access is denied or the device is still missing, the render remains `blocked`. A `--ask-for-approval never` override prevents this recovery. Use `--agent-cmd` with an execution policy approved for the target machine when GPU access requires it; choose CPU explicitly only when that is the intended render device. A limited-budget batch is an integration check and does not establish that every scene was promoted.

The FLIP bake tests also need `WORLDAGENT_FLIP_PATH`, set to the directory that contains `flip_fluids_addon`; otherwise they are skipped.

## Not included

The Infinigen-based terrain toolkit, the game pipeline, `claude_harness` and the non-English references are not part of this plugin. They remain in the WorldAgent repository. When the terrain toolkit is not installed, the skill builds terrain with Blender directly and records the substitution.
