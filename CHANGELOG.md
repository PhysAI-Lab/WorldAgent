# Changelog

This project follows [Semantic Versioning](https://semver.org/). While the version is below 1.0.0, a minor release can change commands, settings or case files.

## 0.3.0 — 2026-10-27

### Breaking changes

Cases written for 0.2.0 must be updated before the gate accepts them:

- Every object contract needs `simulation_class` and `blender_system`. An object whose system is not the preferred one for its class needs `solver_choice_reason` with a reason and evidence.
- Every requirements row needs `tolerances` or `measures`. Each tolerance states its basis (`screen`, `solver`, `numeric`, `request` or `outcome`) and how it was derived.
- A case simulated by more than one Blender system needs `topology.solver_graph`.
- `gas.density` and `gas.grid_stats` require `guard_layers`.
- Changes to the measurement helpers make earlier measurements stale; run the rows again.

### Added

- **Measurable rows with stated tolerances.** The default tolerance is the larger of 5 pixels at the final resolution (taken at no less than 1920x1080) and the solver's resolution. A tighter value needs basis `request`. A row that only states a visual judgement is refused.
- **Solver choice check.** The spec audit compares each object's Blender system with the class table in `schemas/solver_classes.json`. The gate and promote list every deviation with its reason.
- **Coupling graph** for scenes that use more than one solver: nodes, edges, bake order and the interactions each story beat claims, checked against `schemas/coupling_channels.json`. `coupling.graph_links` finds couplings in the scene that the graph does not declare, and `coupling.bake_evidence` checks the bake order.
- **Setup checks before baking:** `deformable.soft_body_setup`, `gas.gas_setup`, `particles.particle_setup`, `systems.solver_identity`.
- **New measurements:** soft body free fall and visible-to-simulation deviation; particle counts, penetration and crossings with count and fraction limits, region containment, settling; gas cache coverage and domain boundary clearance; rest by window displacement (`numerical.rest_displacement`, `particles.rest_displacement`); `scale.pixel_footprint`; `numerical.trend` and `within`.
- **Movie encoding:** `render_candidate.py --encode` (or `--encode-report`) writes an H.264 movie of the final frames; `render.check_movie` checks it. A missing ffmpeg makes the movie row `blocked`.
- **Portable final scene:** when relative cache paths would not resolve from the case root, promote also writes `scene_final_portable.blend` with only those paths rewritten and checks that nothing else changed. The write guard protects it like `scene_final.blend`.
- `tests/e2e/smoke_cases.json`: one small scene per solver class for a quick end-to-end run in either host.

### Changed

- The spec is frozen once the requirements matrix covers every story beat, and the frozen copy includes the rows. Spec changes are reported apart from changes that only describe how the scene is built.
- A duration stated in the request takes precedence over the long-form minimum.
- The runner resolves relative cache paths against the candidate's folder and marks a row `unknown` if its verifier changes a cache it read. Blender runs in `<case>/.work/blender/`, so its side files no longer land in the case root.
- Contract changes also record edits to case-local Python inputs and to a row's tolerances or measures.
- `shot.coverage` ignores objects that are not rendered. `causal.order` reports pairs on the same frame as `simultaneous` and accepts the pairs a row lists.
- The skill explains how to estimate event timing before setting beat windows, and lists solver limits that a row should not test (gas density is not conserved; granular piles have no static friction).

### Fixed

- The gas boundary check could never fail, and gas cell sizes ignored the domain's scale.
- A Mantaflow cache read as empty in the verifier's copy of the candidate.
- The batch summary could report a preview instead of the selected candidate's final render.

## 0.2.0 — 2026-10-24

### Added

- **Frozen spec.** The first time the spec audit passes, the world spec is saved to `reports/spec_baseline.json`. The gate and promote list every later change to story beats, objects (including their sizes) and layout, even if no row was measured in between.
- **`bin/limit.py`.** A case that stops with failing rows is recorded as `limited` by this tool. It refuses until every row except the render rows has been measured on the chosen candidate, and it writes the failing rows with their first failures to `reports/limit_<stamp>.json`. The gate reports a `limited` status that was set any other way.
- Codex catalog at `.agents/plugins/marketplace.json`, so Codex installs from this repository with `codex plugin marketplace add PhysAI-Lab/WorldAgent`.

### Changed

- `/worldagent:run` measures the first baked candidate with the runner before refining. It stops refining only when every row passes, the time budget runs out, a failure is below the solver's resolution, or several different kinds of change have made no measured progress.
- Library assets are placed and scaled uniformly only. An asset that does not fit the object's contract is recorded as an approximation instead of being reshaped, and the object contract is left as written.
- Both manifests declare the MIT license.
- The README is rewritten for users of this repository.

### Fixed

- Tests no longer read the user's WorldAgent config file or a Codex session's settings from the machine they run on.

## 0.1.0 — 2026-10-24

First release, for Claude Code and Codex.

- Commands `new`, `bake`, `verify`, `render`, `promote`, and `run` for the whole pipeline.
- World spec with a spec audit, and a requirements matrix with a numeric pass condition per row.
- Runner that executes each row's verifier in a fresh Blender process on a copy of the candidate; a gate that accepts only current runner results; promote that measures every row again before installing `scene_final.blend`.
- Final render tool with a render report that the render rows check.
- Hooks for the session environment report, the final-scene write guard, and the completion check.
- Measurement helpers for contact, penetration, support, scale, liquids, gas, cloth and soft bodies, rigid-body setup, story order, shot coverage and renders.
- Settings for Blender, FLIP Fluids, render device, resolution, samples, asset library, memory budget and time budget.
