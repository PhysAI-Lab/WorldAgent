# Writing a case verifier

A verifier checks one `requirements_matrix` row of one case. Write it per case under `<case>/src/verify_<row>.py`; cases differ too much for one shared verifier. The plugin runner executes it, and a row becomes `pass` only through the runner. Never write a row's status, evidence, or a `reports/verify_*.json` file yourself: promotion re-measures every row, so hand-written results never count.

Paths below are relative to the plugin root, which the skill gives as `${CLAUDE_PLUGIN_ROOT}`.

## 1. Script contract

The runner calls:

```
blender --background --factory-startup <copy of candidate.blend> --python-exit-code 1 \
  --python <case>/src/verify_<row>.py -- --out <result.json> --case <case_dir> --row <row_id>
```

- Blender opens a **copy** of the candidate in a fresh process with factory settings. Enable any add-on you need (e.g. FLIP Fluids) inside the script.
- `lib/` is on `sys.path`, so `import worldagent_measure` works.
- Read your arguments with `worldagent_measure.verifier.parse_args()`. Write one JSON object to `--out` with `verifier.write_result()` or `verifier.write_blocked()`.
- Do not modify the candidate. The runner compares its hash before and after the run.
- An uncaught exception is a crash. The row becomes `unknown`, with the log tail kept in the report.

## 2. Result

```json
{
  "status": "pass | fail | blocked",
  "measurements": { "min_signed_gap_m": -0.0004, "frames_checked": 240 },
  "first_failure": { "object": "box", "other_object": "table", "frame": 37, "value": -0.0004 },
  "reason": "only for blocked",
  "frames": [1, 240],
  "evidence": ["reports/contact_sheet_r12.png"]
}
```

| status | must include |
|---|---|
| `pass` | non-empty `measurements`, no `first_failure` |
| `fail` | non-empty `measurements` and `first_failure` |
| `blocked` | `reason` (e.g. FLIP Fluids not installed) |

Anything else becomes `unknown`: a pass without measurements, a missing or invalid result, a crash, a timeout, or a changed candidate.

**Measure, don't assert.** Every number in `measurements` must come from the scene you opened. Never write a count you did not compute (e.g. `"bbox_proxy_count": 0` with no proxy scan).

## 3. Thresholds

Each row states its own threshold in `pass_predicate` (e.g. "signed gap >= -1e-3 m on every frame"). Use that same threshold in the script. The measurement helpers have no default thresholds. Use a tolerance only to classify floating-point noise, and report it in `measurements` (`simulation-workflow.en.md`, `non-penetration-contract.en.md`).

## 4. Run it

```
export WORLDAGENT_BLENDER=/path/to/Blender   # Blender 5.1; set by the plugin at session start
python3 ${CLAUDE_PLUGIN_ROOT}/bin/run_verifier.py <case_dir> --row <row_id> \
  --script <case_dir>/src/verify_<row>.py --candidate <case_dir>/.work/candidates/<id>/scene.blend \
  [--input <case_dir>/.work/cache/<bake>]
```

The runner then:
- writes `reports/verify_<row>_<UTC time>.json` with the result, the Blender version, the input hashes, and the log tails;
- updates the row: `status`, `evidence` (the report first), `verified_by`, `input_hashes`, `candidate`, and `first_failure` or `reason`.

`input_hashes` covers the candidate, the script itself, every `--input`, the spec part of `manifest.json`, and the shared helper library. If any of them changes later, the gate treats the row as `stale`.

Pass as `--input` everything else the verifier depends on:
- every bake or cache it reads;
- every case module it imports (e.g. a shared `src/_common.py`). Only the verifier script itself is hashed automatically.

## 5. Story coverage

Every `contract.story.beats` id must be covered by at least one row. A row covers the beats listed in its `covers` field, or, without `covers`, the beat whose id equals the row id. The gate fails on an uncovered beat or on a `covers` entry that names no beat.

## 6. Measurement helpers

Use `worldagent_measure` instead of re-implementing geometry. Every helper measures evaluated world-space meshes and takes its thresholds as arguments.

Read simulated state from the evaluated scene, never from an object's own properties: after `set_frame` (or `scene.frame_set`), `obj.location` and `obj.rotation_euler` still hold the authored transform, while the solver's result is in `obj.evaluated_get(depsgraph).matrix_world`. The helpers do this for you. Re-implementing a measurement is also where verifier bugs come from (for example, measuring a ring's radius from the world origin instead of its centre); use `scale.dimensions` and the other helpers first.

| Module | Measures |
|---|---|
| `contact` | Interpenetration between objects on every frame: triangle intersection, containment, depth; self-intersecting meshes |
| `scale` | Dimensions along each object's axes against the contract; size ratios between related objects; unapplied transforms |
| `numerical` | Non-finite coordinates, displacement and speed jumps, extent growth |
| `support` | Measured contact under each object's footprint and a support chain to a foundation |
| `navigability` | Route continuity, step height, slope, headroom, width |
| `liquid` | Liquid surface quality, volume, liquid inside solids (Mantaflow or FLIP Fluids) |
| `solver_mesh` | Obstacle meshes a grid liquid solver voxelizes: closed, oriented, not self-intersecting, thick enough in grid cells |
| `causal` | When events first happen (contact, start of motion, entry into a region), how close two surfaces came and whether causes come before effects with the allowed lag and inside their beats; response after contact for coupled pairs |
| `shot` | Whether each event's object is seen by the scene camera (in frame, not hidden) on enough frames of its beat |
| `gas` | Mantaflow smoke and fire: total density per frame, sudden loss or jump, gas on the domain walls |
| `rigid_setup` | Rigid body setup Bullet does not report: origin against center of mass, primitive collision shapes against the visible mesh, bodies and constraints outside the rigid body world, keyed active bodies, cache coverage |
| `deformable` | Cloth and elastic bodies: edge stretch and compression against a rest frame, motion per step (also relative to edge length and cloth collision distance), self-intersection, recovery of the rest shape; cloth setup before the bake (`cloth_setup`: solver mesh entering Cloth, modifier order, settings, colliders) |
| `render` | Whether a render report from `bin/render_candidate.py` is a final Cycles render at the final resolution (1920x1080 unless the plugin's setting changes it) of every frame of this candidate, on the expected device, with unchanged output files |

Choosing arguments:

- **Support.** `support_chain` looks for a supporter under each object's footprint. Pass wall-mounted, hinged, or suspended objects (sconces, wall panels, doors) as `attached`; they are then measured by the closest surface gap in any direction. Justify each one in the script; marking everything `attached` hides floating objects.
- **Dimensions.** `object_contracts[].dimensions_m` is Blender's `Object.dimensions`, along the object's own axes. `scale.dimensions` measures that by default (`axes="object"`). Use `axes="world"` only when the contract states world-axis extents.
- **Liquid obstacles.** Pass the meshes the solver voxelizes (e.g. every enabled Mantaflow effector) to `solver_mesh.solver_mesh_validity`, with `cell_size_m=grid_cell_size(domain)` and `min_cells` chosen from `flip-fluids.en.md` §3 item 4 (at least 1; collision walls multiple cells; primary openings and animated obstacles 3–4). Declare `groups` for coverings the solver fills together with their support, such as a carpet on a floor or panels on a wall, derived from the contracts' `initial_support`. Each member must touch another within `group_contact_tol_m`. Thin obstacles that stand alone (doors, paper) are never grouped.
- **Contact on authored assets.** Authored meshes often join overlapping shells or self-intersect. `contact.penetration` tests each closed shell separately and reports self-intersecting meshes in `self_intersecting_meshes`. A failure whose `first_failure.self_intersecting` is non-empty has an ambiguous depth: fix the asset (make it a valid solid) rather than raising `eps_pen`.
- **Static architecture.** To treat fixed architecture as one union, pass explicit pairs (moving objects against everything, moving against moving) to `contact.penetration` instead of all pairs, and check the architecture meshes themselves with `solver_mesh`.
- **Story order.** Measure each beat's key event with `causal.contact_onset`, `motion_onset`, or `region_entry`, then pass the frames to `causal.order` with the beats' `depends_on` pairs and `frame_interval` windows (`causal.story_dependencies(manifest)` reads both). Use `min_lag_frames=1` when the effect must come strictly after its cause; simultaneous cause and effect is a failure the paper calls out. An event that never happens fails, so a skipped beat cannot pass as "in order". For contact between rigid bodies, `contact_tol_m` must cover the sum of the two bodies' collision margins (their visible meshes stop that far apart), and must stay below their starting gap. When `contact_onset` returns `None`, call `causal.closest_approach` to see how close the surfaces came before changing anything.
- **Coupled systems.** For a pair the spec couples (an impactor and what it pushes, a liquid and a floating body), `causal.response_to_contact` requires the effect to start moving after first contact and within `max_lag_frames`; moving before contact fails. State the coupling direction in the spec; this measures the response, not the reaction force.
- **Shot coverage.** Pass each beat's key object with the beat's `frame_interval` to `shot.coverage`. The scene camera of each frame is used, so camera switches on markers are followed. Glass and other transparent meshes count as blockers, and volumes cannot be targets: cover a smoke plume through the objects it interacts with, or through its emitter.
- **Gas.** `gas.density` reads the baked density grid of the evaluated domain; bake first. An unbaked domain reads as zero density, so pass `min_total` whenever the gas must exist. A high `boundary_fraction` means gas pressing on or leaving through the domain walls; whether the loss is visible on camera is a separate `shot.coverage` row.
- **Cloth and elastic bodies.** Bake (or step the frames in order) before measuring. Choose `rest_frame` before the load. Read `largest_step_over_collision_distance` before setting `max_step_m`: steps many times the collision distance let cloth tunnel. Use `deformable.recovery` only for bodies that must return to shape after the load is removed.
- **Render and media.** Render with `${CLAUDE_PLUGIN_ROOT}/bin/render_candidate.py` (see the skill's Stage 6), never with a one-off script: only its report records the candidate hash, device, resolution, frames and file hashes. In the verifier, call `render.check_render_report(<case>/reports/render_<stamp>.json, args.case, expected_device=...)` on the report of the final render, and run the row with that report and its `media/final_<stamp>/` folder as `--input`, so a new render makes the row stale. A check that only looks for image files under `media/` passes on three preview frames; it is not a render check.
- **FLIP Fluids.** Call `liquid.enable_flip()` (it finds the add-on through `WORLDAGENT_FLIP_PATH`, which the plugin sets at session start), catch `liquid.FlipUnavailable`, and call `verifier.write_blocked()`. Never measure a Mantaflow substitute.

## 7. Record what you tried

The runner, gate, and promote add an event to `<case>/reports/trace.jsonl` on every run, and the trace keeps a copy of the spec whenever it changes. Record each refinement attempt yourself, with the one causal change group and the outcome:

```
python3 ${CLAUDE_PLUGIN_ROOT}/bin/case_trace.py note <case_dir> --hypothesis "box sinks: collision margin is 0" \
  --change "collision margin 0 -> 0.002 m" --result accepted --reason "max depth 0.004 -> 0 m" --row contact
python3 ${CLAUDE_PLUGIN_ROOT}/bin/case_trace.py show <case_dir> [--row <row_id>]
```

Name the rows a note is about (`--row`): `show` then lists under each note what the runner measured next for those rows, so the outcome is recorded even if you never write a follow-up note. The trace is a log. The gate and promote never read it, so it neither proves nor blocks anything.

### Changing a verifier or the spec after a fail

A failing row means the scene or the bake is wrong until shown otherwise. Fix those first. Change the verifier or the spec only when it is wrong itself, and then:

- do not replace a helper with a weaker proxy. If a helper reports nothing where you expected a result (for example `causal.contact_onset` returns `None`), find out why: print the measured values on the frames around the event and check the tolerance against the collision margin, the mesh resolution, and how far objects move per frame. Report what you found. Do not switch to a check that no longer measures the requirement, such as comparing positions against where an object started;
- do not raise a tolerance to cover a measured violation. A penetration epsilon is for coincident contact and floating-point noise only [non-penetration-contract.en.md]. If the solver cannot meet the threshold, the row fails and the case is `limited`; say so;
- do not change what the row requires: moving a beat's frame window, loosening a `pass_predicate`, or changing an object's collision representation or constraints so that the requested physics no longer happens (for example, joints in place of rings that hang by contact) is changing the request, not fixing the scene. If the requested physics cannot be produced with this solver, the row fails and the case is `limited`; say so;
- record the change with `case_trace.py note --row <row_id>`: what the old verifier got wrong, the evidence, and why the new one still checks the row's `pass_predicate`.

The runner keeps a copy of every verifier version it runs under `reports/trace/verifiers/` and records each change in the row's `contract_changes`. The gate and promote list every row whose verifier, `pass_predicate`, or world spec changed after a non-pass result, so the user can see what changed. The list does not change any verdict; it shows the user where to look.

## 8. Examples

- `${CLAUDE_PLUGIN_ROOT}/tests/fixtures/verifiers/verify_box_on_ground.py`: the smallest complete verifier.
- `${CLAUDE_PLUGIN_ROOT}/tests/fixtures/verifiers/verify_contact_example.py`: a verifier built on `worldagent_measure.contact`.
