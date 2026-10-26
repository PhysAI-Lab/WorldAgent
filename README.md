# WorldAgent

WorldAgent is a plugin for [Claude Code](https://claude.com/claude-code) and [Codex](https://developers.openai.com/codex) that builds physically grounded Blender scenes: rigid-body collapses, cloth, liquids (FLIP Fluids), smoke and fire, particles, soft bodies, and terrain with water. Every requirement of a scene is checked by a measurement. The agent cannot mark its own work as passed.

## How it works

1. **Spec.** Your request becomes a world spec (story beats with frame windows, a contract for every object, and the scene layout) and a requirements matrix: one row per hard requirement, each with a measured quantity and a numeric pass condition. A spec audit checks that the spec is consistent before anything is built.
2. **Build and bake.** The agent builds the scene in Blender, using a local asset library first when one is configured, and bakes a candidate `.blend`.
3. **Verify.** Each row has a verifier script. The plugin's runner executes it in a fresh Blender process on a copy of the candidate and records the result. A row becomes `pass` only through the runner.
4. **Refine.** For a failing row, the agent changes the scene or the bake, one cause at a time, and measures again. It does not change the requirement.
5. **Render and promote.** The final sequence is rendered with Cycles at full resolution and encoded into a movie, and both are checked like any other row. `promote` measures every row again on that exact candidate and installs it as `scene_final.blend` only if all of them pass.

A case that cannot meet every requirement ends as `limited`. It is rendered so you can see it, its failing rows are reported with their measured values, and it is not promoted.

## Requirements

- **Claude Code** or **Codex CLI** with plugin support. Codex support is tested with CLI 0.164.0.
- **Blender 5.1.**
- **Python 3.** The plugin's scripts and hooks use only the standard library.
- **A render device** for the final Cycles render. The default is NVIDIA OptiX. CUDA, HIP, Metal, oneAPI, any available GPU, or CPU can be configured instead.
- **ffmpeg and ffprobe** for encoding and checking the final movie. Without them the movie and its row are reported as `blocked`, never as passed.
- **FLIP Fluids 1.8.8 add-on (optional).** It is not included. Install your own licensed copy. Without it, FLIP requirements are reported as `blocked`, never as passed, and the plugin does not substitute another liquid solver.

## Installation and updates

Each release raises the version in both plugin manifests and is tagged `worldagent--v<version>` (for example `worldagent--v0.3.0`). [CHANGELOG.md](CHANGELOG.md) lists what changed. Both hosts keep a copy of the installed version, so pushing new commits does not change an installed plugin until you update it.

### Claude Code

Install, in a Claude Code session:

```
/plugin install worldagent --marketplace PhysAI-Lab/WorldAgent
```

or from your shell:

```bash
claude plugin install worldagent --marketplace PhysAI-Lab/WorldAgent
```

Update to the latest release:

```bash
claude plugin marketplace update worldagent
claude plugin update worldagent@worldagent
```

Then restart Claude Code, or run `/reload-plugins` in a session. Claude Code updates a plugin only when its version changes. Automatic updates are off for this marketplace unless you turn them on under `/plugin` → Marketplaces.

### Codex

Install from your shell:

```bash
codex plugin marketplace add PhysAI-Lab/WorldAgent
codex plugin add worldagent@worldagent
```

To stay on one release instead of following the main branch, add the marketplace at its tag:

```bash
codex plugin marketplace add PhysAI-Lab/WorldAgent --ref worldagent--v0.3.0
```

Update to the latest release (or to the release the marketplace is pinned to):

```bash
codex plugin marketplace upgrade worldagent
codex plugin add worldagent@worldagent
```

Start a new Codex session after installing or updating, and trust the plugin's hooks in `/hooks`. The hooks report the environment at session start, guard the final scene, and check completion claims. Codex asks again whenever the hooks change, so an update that changes them needs a new review.

## Quick start

Run the whole pipeline from one request:

```
/worldagent:run "Fifteen pine dominoes stand in a line on an oak table. A small pusher taps the first one and the chain falls within four seconds. Static side camera. Final 1920x1080 Cycles render."
```

In Codex, use `$worldagent:run` with the same text.

The session starts with a short report on the machine: which Blender and FLIP Fluids were found, the render device, the asset library, memory, and the time budget. Check it before your first run.

## Commands

| Claude Code | Codex | Arguments | What it does |
|---|---|---|---|
| `/worldagent:new` | `$worldagent:new` | `<case_dir> <scene request>` | Creates the case, writes the world spec and the requirements matrix, and runs the spec audit until it passes. |
| `/worldagent:bake` | `$worldagent:bake` | `<case_dir> [notes]` | Builds the scene and bakes a new candidate, then stops so you can look at it. |
| `/worldagent:verify` | `$worldagent:verify` | `<case_dir> [candidate] [row ids]` | Runs each row's verifier through the runner, writing any missing verifier from the row's pass condition, and reports a verdict per row. |
| `/worldagent:render` | `$worldagent:render` | `<case_dir> [candidate] [--preview --frames <list>]` | Renders the final sequence (or preview frames), encodes the final movie with `render_candidate.py --encode`, and checks both. |
| `/worldagent:promote` | `$worldagent:promote` | `<case_dir> [candidate] [--replace]` | Measures every row again and installs `scene_final.blend` only if all pass. |
| `/worldagent:run` | `$worldagent:run` | `[--case <case_dir>] <scene request>` | Runs every step without stopping for approval, within a time budget. With `--case` it resumes an existing case. |

Each step command does its own step and stops. Only you start these commands; the agent does not run them on its own. The main skill, `scene-simulation`, loads by itself when you ask for a physics scene or work on a case.

Row verdicts are `pass`, `fail`, `blocked` (a required solver or add-on is missing), `unknown` (not measured, or the verifier crashed), `stale` (something the row measured has changed since), and `unverified` (marked pass without valid runner evidence).

## Configuration

Every setting is optional. Anything you leave empty is detected or uses its default.

| Setting | Environment variable | Default |
|---|---|---|
| Blender executable | `WORLDAGENT_BLENDER` | `blender` on `PATH`, then the usual install locations for macOS, Linux and Windows |
| FLIP Fluids folder (the folder that contains `flip_fluids_addon`) | `WORLDAGENT_FLIP_PATH` | Blender's user add-on and extension folders |
| FLIP Fluids installer to check against the published checksum | `WORLDAGENT_FLIP_INSTALLER` | No check |
| Render device: `OPTIX`, `CUDA`, `HIP`, `METAL`, `ONEAPI`, `ANY_GPU` or `CPU` | `WORLDAGENT_RENDER_DEVICE` | `OPTIX` |
| Final render resolution, `WIDTHxHEIGHT` | `WORLDAGENT_RENDER_RESOLUTION` | `1920x1080` |
| Cycles samples for previews and the final render | `WORLDAGENT_RENDER_SAMPLES` | Each scene's own setting |
| Asset library folder | `WORLDAGENT_ASSET_LIBRARY` | None; assets are built procedurally |
| Time budget for `run`, in hours | `WORLDAGENT_TIME_BUDGET_HOURS` | `3` |
| Memory budget per Blender process, in GB | `WORLDAGENT_MEMORY_BUDGET_GB` | The skill's built-in memory rules |
| ffmpeg executable for the final movie (environment variable only) | `WORLDAGENT_FFMPEG` | `ffmpeg` on `PATH` |
| ffprobe executable for checking the movie (environment variable only) | `WORLDAGENT_FFPROBE` | Next to ffmpeg, then `ffprobe` on `PATH` |

Where to set them:

- **Claude Code:** `/plugin configure worldagent`, or the environment variables above. ffmpeg and ffprobe are set only through their environment variables or `PATH`; without them the movie is reported as `blocked`.
- **Codex:** the environment variables above, or a config file at `~/.config/worldagent/config.json` (`$XDG_CONFIG_HOME/worldagent/config.json` when that is set; `WORLDAGENT_CONFIG_FILE` points to another file). Its keys are `blender_path`, `flip_addon_path`, `flip_installer`, `render_device`, `render_resolution`, `render_samples`, `asset_library_path`, `memory_budget_gb` and `time_budget_hours`:

  ```json
  {
    "render_device": "CUDA",
    "render_resolution": "1280x720"
  }
  ```

A setting made in the plugin takes precedence over an environment variable, and an environment variable over the config file. To see what the plugin finds on your machine at any time:

```bash
python3 bin/worldagent_env.py          # add --json for machine-readable output
```

A time budget written in the request to `run` takes precedence over the setting for that case.

## What the plugin enforces

These are checks in the plugin's tools and hooks, not instructions the agent is trusted to follow.

- **Only measured passes count.** A pass needs a runner report and the hashes of everything it measured: the candidate, the verifier, the inputs passed with `--input` (such as caches), the spec and the measurement library. The gate re-hashes them, so any later change makes the row `stale`. Caches the runner only rebases are checked for changes during the run.
- **The final scene is re-measured.** `promote` runs every verifier again on the exact file it installs and checks the file's hash. A hook blocks every other attempt to write `scene_final.blend` or `scene_final_portable.blend`. An existing final scene is moved aside, never deleted. When the candidate's relative cache paths would point elsewhere from the case root, promote also writes `scene_final_portable.blend`, a copy with only those paths rewritten. It reopens that copy in a fresh Blender and checks that each path reaches the same folder as before and that nothing else changed. `scene_final.blend` stays byte-identical to the measured file.
- **Every row is measurable.** The spec audit rejects a row that names no measured quantity. Every distance or penetration tolerance states its basis: by default the larger of 5 pixels at that distance and the solver's own resolution. The 5 pixels are taken at the final resolution scaled up to a longer side of at least 1920 px, so a low test resolution does not loosen tolerances. A smaller value is allowed only when the request asks for it, size-relative tolerances are given as a ratio of the object's size, and a threshold taken from an outcome the request states (basis `outcome`) names that clause. An outcome may loosen a length tolerance but never tighten it: an outcome length also states the 5-pixel and solver lengths and must be at least the larger of them.
- **Solver choices are justified.** Every object names its physical class and the Blender system that simulates it. A system that is not the preferred one for that class needs a reason backed by measured reports, and the gate and promote list every such object for review.
- **Changed requirements are listed.** When a row's verifier, a case module it imports, its pass condition or its tolerances change after a failed measurement, the gate and promote list it. The spec is frozen the first time its audit passes with a requirements matrix that covers every beat, together with each row's pass condition, tolerances and measures. Every later change to beats, objects (including their sizes), layout or those row fields is listed too. Changes to how the scene is built (which solver or input drives an object or beat) are listed separately from changes to what it must show. These lists do not block anything; they show you what to review.
- **Coupled solvers follow a declared graph.** When objects are simulated by two or more Blender systems, the spec audit requires a solver graph: each edge must use a channel Blender actually provides between those two systems (listed in `schemas/coupling_channels.json`), approximations and scripted transfers must say what they assume, the bake order must put every driver first, and every cross-solver effect a story beat claims must have an edge or be marked not achievable. The gate and promote list the approximations and omitted reactions for review.
- **A case is `limited` only after it is measured.** The tool that records a `limited` result refuses until every row except the render rows has been measured on the chosen candidate.
- **"Done" means promoted.** If the agent says it is finished while a case it worked on has not been promoted, a hook stops it once and lists the rows that do not pass.
- **Thresholds live in the rows.** The measurement helpers have no built-in pass thresholds. Every number comes from the row's pass condition and its `tolerances` or `measures`, where you can read it.

The write guard is a guard against mistakes, not a security boundary.

## Case folder

```
<case_dir>/
├── manifest.json        world spec, requirements matrix, status, asset sources, approximations
├── src/                 build scripts and verifiers (verify_<row>.py)
├── reports/             runner reports, gate reports, render reports, frozen spec, case trace
├── media/               final renders and the final movie (final_<stamp>.mp4)
├── previews/            preview renders
├── .work/               candidates, caches and logs
├── scene_final.blend    written only by promote
└── scene_final_portable.blend   promote's copy whose relative cache paths resolve from here (only when needed)
```

`python3 bin/case_trace.py show <case_dir>` prints the case history: every measurement, spec change and refinement attempt, with what was measured after each attempt.

## Repository layout

```
.claude-plugin/          Claude Code manifest, settings and catalog
.codex-plugin/           Codex manifest
.agents/plugins/         Codex catalog
skills/                  scene-simulation (main skill and references) and the six commands
hooks/                   session report, final-scene write guard, completion check
bin/                     spec audit, runner, gate, promote, limit, render tool, environment check, case trace
lib/worldagent_measure/  measurement helpers that verifiers use inside Blender: contact, scale, numerical,
                         support, navigability, liquid, solver_mesh, causal, shot, gas, particles,
                         deformable, systems, coupling, render, rigid_setup
schemas/                 JSON schemas for the world spec and the requirements matrix, solver classes,
                         coupling channels
scripts/                 case setup, layout check, FLIP Fluids checksum
tests/                   unit tests, Blender tests and end-to-end test scenes
```

## Development

Load the plugin from a clone and check its structure:

```bash
claude --plugin-dir .
claude plugin validate .
```

Run `/reload-plugins` after you change files. In Codex, run `codex plugin marketplace add .` from the clone, reinstall with `codex plugin add worldagent@worldagent` after each change, and start a new session.

Run the tests:

```bash
export WORLDAGENT_BLENDER=/path/to/blender
uv run --no-project --with jsonschema python -m unittest discover -s tests -p 'test_*.py'
```

Tests that need Blender are skipped when it is not found, and the FLIP Fluids tests are skipped without `WORLDAGENT_FLIP_PATH`. `tests/e2e/run_batch.py` runs the test scenes in `tests/e2e/cases.json` end to end through Claude Code or Codex and writes a summary table; see its `--help`.

`tests/e2e/smoke_cases.json` has one very small scene per solver class (rigid body, cloth, FLIP liquid, gas, Newton particles, SPH granular, soft body, and a rigid body driving FLIP): 2 seconds at 24 fps, 320x180, 8 samples, procedural geometry. Run it with either host:

```bash
python3 tests/e2e/run_batch.py --agent claude --plugin-dir . --cases tests/e2e/smoke_cases.json --budget-hours 1 --out <dir>
python3 tests/e2e/run_batch.py --agent codex --cases tests/e2e/smoke_cases.json --budget-hours 1 --out <dir>
```

Add `--only smoke_gas,smoke_softbody` to run some of them. A case may set an `environment` map of `WORLDAGENT_*` settings (here the small render resolution and sample count) that the runner adds to the agent's environment. The FLIP cases need the FLIP Fluids add-on; without it their liquid rows are `blocked`.

## Not included

The plugin does not include the FLIP Fluids add-on or an Infinigen-based terrain toolkit. Without the toolkit, terrain is built with Blender directly and the substitution is recorded in the case.

## License

MIT. See [LICENSE](LICENSE). FLIP Fluids is a separate add-on under its own license and is not covered by this one.
