# Changelog

This project follows [Semantic Versioning](https://semver.org/). While the version is below 1.0.0, a minor release can change commands, settings or case files.

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
