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
- Blender runs in `<case>/.work/blender/`, where side files it writes (Mantaflow noise tiles, add-on icons) stay out of the case layout. Build every case path from `--case` (`Path(args.case) / "manifest.json"`), never from the working folder.
- `lib/` is on `sys.path`, so `import worldagent_measure` works.
- Before your script runs, the runner points every `//` path of the copy (Mantaflow and FLIP cache folders, point caches, mesh caches, images, volumes) at the candidate's own folder, so caches stored relative to the candidate are found. Do not set cache folders in the verifier. The report lists what was changed in `rebased_paths`; the candidate's folder is also in `$WORLDAGENT_CANDIDATE_DIR`. The files of a disk point cache (`blendcache_<name>/`) next to the candidate are hard-linked next to the copy (copied when the temporary folder is on another filesystem), so a large cache costs no copy and a cache Blender frees there only loses the link.
- Read your arguments with `worldagent_measure.verifier.parse_args()`. Write one JSON object to `--out` with `verifier.write_result()` or `verifier.write_blocked()`.
- Do not modify the candidate or a cache it reads (no bake, free or overwrite). The runner checks the candidate's hash, and records a fingerprint of every cache it reads (file list, sizes and modification times; no file content is read) before and after the run. If a cache changed, the row becomes `unknown` ("verifier changed a cache it read: ..."). A write that keeps both a file's size and its modification time is not seen.
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

**A missing cache fails the row.** A helper that needs a solver cache raises a subclass of `verifier.CacheMissing` (`gas.GasCacheMissing`, `particles.ParticleCacheMissing`, `liquid.LiquidCacheMissing`) when the cache is not baked, does not cover the frames, or has files missing. Catch it and record it with `verifier.cache_missing_check(name, exc)`, so the row is `fail` with reason "cache missing" instead of a crash:

```python
try:
    checks = [particles.cache_coverage([sand], start, end), ...]
except verifier.CacheMissing as exc:
    checks = [verifier.cache_missing_check("particle_cache", exc)]
```

## 3. Thresholds

Each row states its threshold in `pass_predicate` (text for people) and again in `tolerances[]` or `measures[]`, which the spec audit checks. Use those same numbers in the script; read them from the row rather than retyping them where practical. The measurement helpers have no default thresholds.

A length tolerance (penetration epsilon, contact tolerance, position error) follows the default rule: `value_m = max(screen_m, solver_m)`. `screen_m` is 5 pixels at the reference resolution at that distance from the camera: the final render resolution scaled up, aspect ratio kept, until its longer side is at least 1920 px (a 320x180 final render uses 1920x1080; 3840x2160 is used as is). Compute it with `scale.pixel_footprint`, which returns metres per pixel at a point:

```python
from worldagent_measure.scale import pixel_footprint
screen_m = 5 * pixel_footprint(camera, crate.location, (1920, 1080))  # reference resolution; camera=None: scene camera
```

`solver_m` is the solver's resolution: the Bullet collision margin for rigid bodies, one cell for grid solvers (`solver_mesh.grid_cell_size`), the collision distance for cloth and soft body. A smaller value needs basis `request` (the request asked for that precision). Numeric tolerances (centre of mass, volume) are a `ratio` of the object's size. Basis `outcome` is for a threshold derived from an outcome the request states ("the tower falls over": top block centre below 0.45 of its start height); `derivation` names the request clause. An outcome may be looser than the default rule, never tighter: an outcome `value_m` also gives `screen_m` and `solver_m`, and the spec audit rejects it when it is below `max(screen_m, solver_m)`. A tighter length needs basis `request`. A `ratio` outcome is not compared with the default rule. These values are set in the spec before any measurement; the tolerance is not an allowance to raise after a fail (`simulation-workflow.en.md`, `non-penetration-contract.en.md`).

Example row fields:

```json
"tolerances": [{"quantity": "penetration depth crate_a vs floor", "basis": "screen", "value_m": 0.003,
                "screen_m": 0.003, "solver_m": 0.001,
                "derivation": "5 px at 1920x1080 from the camera at 4.2 m = 3.0 mm; Bullet margin 1 mm"}],
"measures": [{"quantity": "crate_a speed, last second (screen_m 3 mm x 24 fps)", "comparison": "<", "value": 0.072, "unit": "m/s"}]
```

**Rest.** "Comes to rest" means nothing visibly moves: measure it as displacement over a rest window, not as instantaneous solver speed. Solvers keep a small velocity jitter (a few cm/s for resting Newton particles or cloth) that moves nothing on screen and still fails a speed bound. Use `numerical.rest_displacement` for meshes (rigid bodies, cloth, soft bodies) and `particles.rest_displacement` for particles: each vertex or particle's largest distance over the window from its position on the window's first frame, against a length tolerance with `value_m = max(screen_m, solver_m)` as above. Both report the speed next to it. A speed threshold needs a basis too: write it in the quantity or `pass_predicate`, for example `screen_m x fps` (one 5 px step per frame).

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

Pass as `--input` everything else the verifier depends on, so a change makes the row stale:
- every bake or cache it reads (for hashing only; the runner already points the copy at it), and the bake record `reports/bakes/<candidate>.json` when the row checks bake order;
- every case module it imports (e.g. a shared `src/_common.py`). Only the verifier script itself is hashed automatically. The runner keeps a copy of each case-local `.py` input under `reports/trace/inputs/`.

## 5. Story coverage

Every `contract.story.beats` id must be covered by at least one row. A row covers the beats listed in its `covers` field, or, without `covers`, the beat whose id equals the row id. The gate fails on an uncovered beat or on a `covers` entry that names no beat.

Beat windows are frozen with the spec, so set them from an estimate, not a guess: free fall takes `t = sqrt(2h/g)`, sliding or rolling takes distance / speed, and the window should hold the event with margin on both sides (a fall estimated at frame 10 gets a window starting several frames earlier). When an event time is uncertain, one low-resolution probe bake before the first passing spec audit shows the event frames. A row must not contradict what the request makes happen: an object the request covers, buries, or wraps is not required to stay visible, and a body the request drops is not required to keep its support.

## 6. Measurement helpers

Use `worldagent_measure` instead of re-implementing geometry. Every helper measures evaluated world-space meshes and takes its thresholds as arguments.

Read simulated state from the evaluated scene, never from an object's own properties: after `set_frame` (or `scene.frame_set`), `obj.location` and `obj.rotation_euler` still hold the authored transform, while the solver's result is in `obj.evaluated_get(depsgraph).matrix_world`. The helpers do this for you. Re-implementing a measurement is also where verifier bugs come from (for example, measuring a ring's radius from the world origin instead of its centre); use `scale.dimensions` and the other helpers first.

| Module | Measures |
|---|---|
| `contact` | Interpenetration between objects on every frame: triangle intersection, containment, depth; self-intersecting meshes |
| `scale` | Dimensions along each object's axes against the contract; size ratios between related objects; unapplied transforms |
| `numerical` | Non-finite coordinates, displacement and speed jumps, extent growth; rest as window displacement of every vertex (`rest_displacement`); any per-frame series moving one way (`trend`) or staying inside bounds (`within`) |
| `support` | Measured contact under each object's footprint and a support chain to a foundation |
| `navigability` | Route continuity, step height, slope, headroom, width |
| `liquid` | Liquid surface quality, volume, liquid inside solids (Mantaflow or FLIP Fluids) |
| `solver_mesh` | Obstacle meshes a grid liquid solver voxelizes: closed, oriented, not self-intersecting, thick enough in grid cells |
| `causal` | When events first happen (contact, start of motion, entry into a region), how close two surfaces came and whether causes come before effects with the allowed lag and inside their beats; response after contact for coupled pairs |
| `shot` | Whether each event's object is seen by the scene camera (in frame, not hidden) on enough frames of its beat |
| `gas` | Mantaflow smoke and fire. Before the bake (`gas_setup`): voxel size against sources and named lengths, Flow type, emission and active frames, Flows and Effectors in the Domain and its collections, declared obstacles as Effectors, hidden Effectors, cache type, range and folder, fps and time scale. After the bake: the cache read is the one baked (`cache_coverage`), total density per frame, sudden loss or jump, gas next to the walls (`density`), clearance to the six Domain faces (`boundary_clearance`) |
| `particles` | Newton particles and DDR-SPH granular approximations: cache state and coverage, setup (`particle_setup`: physics type, SPH solver, colliders, hidden emitter), counts per frame, finite and bounded motion, penetration of solids by particles or their visible instances, tunnelling between frames (`crossings`), particles leaving a region, motion before release, settling by speed (`settling`), rest as window displacement (`rest_displacement`) |
| `rigid_setup` | Rigid body setup Bullet does not report: origin against center of mass, primitive collision shapes against the visible mesh, bodies and constraints outside the rigid body world, keyed active bodies, cache coverage |
| `deformable` | Cloth and elastic bodies: edge stretch and compression against a rest frame, motion per step (also relative to edge length and cloth collision distance), self-intersection, recovery of the rest shape; cloth setup before the bake (`cloth_setup`: solver mesh entering Cloth, modifier order, settings, clock, pressure, colliders); Soft Body setup (`soft_body_setup`: other solvers on the object, Goal against free fall, modifier order, closed simulation mesh, visible-mesh binding, support Collision modifiers, cache range; springs, steps and clock reported); measured free fall against scene gravity (`free_fall`); distance from the visible mesh to the simulation surface (`transfer_deviation`) |
| `systems` | Which Blender systems run on each object (`physics_systems`), declared `blender_system` against the scene (`solver_identity`), and the effectors, flows and colliders a solver interacts with (`effectors`) |
| `coupling` | Links between solvers in the built scene (effectors, flows, FLIP obstacles, collisions, hooks, force fields, keyed motors) against `topology.solver_graph` (`graph_links`); whether each driver was baked before what it drives (`bake_evidence`); bake records (`record_bake`) |
| `render` | Whether a render report from `bin/render_candidate.py` is a final Cycles render at the final resolution (1920x1080 unless the plugin's setting changes it) of every frame of this candidate, on the expected device, with unchanged output files; whether its encoded movie matches those frames (`check_movie`) |

Choosing arguments:

- **Support.** `support_chain` looks for a supporter under each object's footprint. Pass wall-mounted, hinged, or suspended objects (sconces, wall panels, doors) as `attached`; they are then measured by the closest surface gap in any direction. Justify each one in the script; marking everything `attached` hides floating objects.
- **Dimensions.** `object_contracts[].dimensions_m` is Blender's `Object.dimensions`, along the object's own axes. `scale.dimensions` measures that by default (`axes="object"`). Use `axes="world"` only when the contract states world-axis extents.
- **Liquid obstacles.** Pass the meshes the solver voxelizes (e.g. every enabled Mantaflow effector) to `solver_mesh.solver_mesh_validity`, with `cell_size_m=grid_cell_size(domain)` and `min_cells` chosen from `flip-fluids.en.md` §3 item 4 (at least 1; collision walls multiple cells; primary openings and animated obstacles 3–4). Declare `groups` for coverings the solver fills together with their support, such as a carpet on a floor or panels on a wall, derived from the contracts' `initial_support`. Each member must touch another within `group_contact_tol_m`. Thin obstacles that stand alone (doors, paper) are never grouped.
- **Contact on authored assets.** Authored meshes often join overlapping shells or self-intersect. `contact.penetration` tests each closed shell separately and reports self-intersecting meshes in `self_intersecting_meshes`. A failure whose `first_failure.self_intersecting` is non-empty has an ambiguous depth: fix the asset (make it a valid solid) rather than raising `eps_pen`.
- **Static architecture.** To treat fixed architecture as one union, pass explicit pairs (moving objects against everything, moving against moving) to `contact.penetration` instead of all pairs, and check the architecture meshes themselves with `solver_mesh`.
- **Story order.** Measure each beat's key event with `causal.contact_onset`, `motion_onset`, or `region_entry`, then pass the frames to `causal.order` with the beats' `depends_on` pairs and `frame_interval` windows (`causal.story_dependencies(manifest)` reads both). Use `min_lag_frames=1` when the effect must come strictly after its cause. Each pair reports a `relation` (`missing`, `effect_before_cause`, `simultaneous`, `effect_after_cause`); a pair on the same sampled frame fails ("cause and effect on the same sampled frame"). Some pairs can share a frame at the delivery fps (impact and squash, contact and splash): list them in `allow_simultaneous=[("impact", "squash")]`, or measure at a higher fps, unless the request states a gap between them. `min_lag_frames=0` accepts the same frame for every pair; prefer listing the pairs. An event that never happens fails, so a skipped beat cannot pass as "in order". For contact between rigid bodies, `contact_tol_m` must cover the sum of the two bodies' collision margins (their visible meshes stop that far apart), and must stay below their starting gap. When `contact_onset` returns `None`, call `causal.closest_approach` to see how close the surfaces came before changing anything.
- **Coupled systems.** For a pair the spec couples (an impactor and what it pushes, a liquid and a floating body), `causal.response_to_contact` requires the effect to start moving after first contact and within `max_lag_frames`; moving before contact fails. State the coupling direction in the spec; this measures the response, not the reaction force. For the coupling itself, `coupling.graph_links(manifest)` compares the links Blender will use with `topology.solver_graph`: a link no edge declares (`undeclared`, e.g. a motor a script keys), an edge with no link (`missing`), an effector, flow or hook set up so it does not act (`wrong_carrier`). `coupling.bake_evidence(manifest, args.case, <candidate id>, frames=..., max_export_deviation_m=..., unverified=...)` checks each driver was baked before the node it drives, using the first evidence available: the bake record written by `coupling.record_bake`, then for a FLIP obstacle the meshes FLIP exported, then cache file times. `unverified` (`"fail"` or `"report"`, from the row's `pass_predicate`) decides an edge with no evidence. A beat interaction marked `achievable: false` has no channel: its row calls `verifier.write_blocked()` with the spec's reason.
- **Systems.** `systems.solver_identity([(obj, contract), ...])` checks that each object's declared `blender_system` runs on it (a modifier off in the viewport does not run). `systems.effectors(target)` lists the objects a solver interacts with and whether each acts on the current frame; step the frames yourself to sample keyed switches.
- **Series.** For a per-frame value a helper reports (the per-frame rows in `measurements["frames"]`, or a list of dicts you build), `numerical.trend(rows, metric, direction=..., min_total_change=..., max_reversal=...)` requires it to move one way, and `numerical.within(rows, metric, lo=..., hi=...)` keeps it in bounds. `metric` is a key or a path of keys into each row. A missing value fails.
- **Shot coverage.** Pass each beat's key object with the beat's `frame_interval` to `shot.coverage`. The scene camera of each frame is used, so camera switches on markers are followed. Only objects that appear in the final render block rays: objects hidden from render, with camera visibility off, only in render-excluded collections, or with volume-only materials (a smoke domain) are passed through and listed in `not_rendered`. Objects hidden in the viewport but rendered are unhidden while measuring (`unhidden_for_test`). A target that is not in the render fails. Glass and other transparent rendered surfaces still count as blockers, and volumes cannot be targets: cover a smoke plume through the objects it interacts with, or through its emitter.
- **Gas setup.** Run `gas.gas_setup(domain, start, end, flows=..., obstacles=..., cache_root=".work/cache", ...)` before every bake; it never bakes. `flows` maps each Flow to the first frame it may emit (its cause beat), and a Flow active earlier fails. `obstacles` are the visible objects the gas must hit, from the spec's beats and topology; each must be an enabled collision Effector, itself or through a hidden proxy listed in `proxies`. A hidden Effector that is neither declared nor a listed proxy fails as an air wall. A cache type of REPLAY fails: it is Blender's default, so set `cache_type` to `ALL` or `MODULAR`. A cache folder outside `cache_root` is reported, not failed. Thresholds you leave out of a check must be written as `None` (reported, not checked).
- **Gas.** In every row that reads the gas cache, run `gas.cache_coverage` first, then `gas.density`, then `gas.boundary_clearance`; on a REPLAY cache the later two would simulate while stepping frames. `gas.density` reads the baked density grid of the evaluated domain; bake first. An unbaked domain reads as zero density, so pass `min_total` whenever the gas must exist. `guard_layers` is required and must be at least 1: Mantaflow's outermost cell layer always reads zero, so the guard layers start at the second layer from each face (`guard_layers=2` is the second and third). A high `boundary_fraction` means gas pressing on or leaving through the domain walls; `guard_fraction_by_face` shows which face, and `guard_depth_m` the guard depth in metres. `cell_size_m` is in world metres (the domain's object scale applied). `max_boundary_fraction` is required; `None` reports it without a check. On an Adaptive Domain the wall checks (`density` with a number for `max_boundary_fraction`, `boundary_clearance`) fail with verdict UNVERIFIED, because the grid moves inside the Domain: use a fixed Domain, or record the row as `limited`. Speeds come from the density box (`front_speed_m_s`); Mantaflow's `velocity_grid` is in grid units, not m/s, so do not read it. Whether the loss is visible on camera is a separate `shot.coverage` row.
- **Cloth and elastic bodies.** Bake (or step the frames in order) before measuring. Choose `rest_frame` before the load. Read `largest_step_over_collision_distance` before setting `max_step_m`: steps many times the collision distance let cloth tunnel. Use `deformable.recovery` only for bodies that must return to shape after the load is removed.
- **Soft bodies.** Call `deformable.soft_body_setup` on the rest frame with `free_fall` from the contract (no `initial_support`), the story frame range, `baked=True`, the supports and the visible meshes driven from the simulation object. It fails only on structure; springs, steps and the clock are reported for the row to judge. Measure the fall with `deformable.free_fall` on consecutive frames ending before `causal.contact_onset`, with `contact_tol_m` at least the reported `solver_distance_m` (a Soft Body rests about 0.9 x the support's collision thickness above it); set `max_initial_speed_m_s` above the integration offset (about 0.05 x g x frame time). Unless the spec states slow motion, add `measures` on `soft_body_setup.time.clock_ratio_5_1` (`within` around 1) and `soft_body_setup.mass_and_media.gravity_weight` (`== 1`): a raised gravity weight passes `free_fall` while springs and damping still run slow. Use `solver_distance_m` as `solver_m` in the tolerance rule (§3) for contact and transfer tolerances. Run `deformable.transfer_deviation` on every visible mesh driven from the simulation, also when its Surface Deform is bound: binding is no evidence that the mesh follows.
- **Particles.** Run `particles.cache_coverage` first: a disk cache whose folder is missing still says baked. A bake that ends early because every particle has died covers the later frames, which read as all dead. Pass the object contract's `blender_system` to `particles.particle_setup` (with `fluid_solver` for `sph_particles`). `penetration` and `crossings` need `max_count` and `max_fraction`; use `radius="instances"` to measure the visible grains instead of the particle centres. `penetration` misses a particle that went through an open solid and left it; `crossings` finds it. The per-frame rows of the particle helpers (for example `counts` or `settling`) feed `numerical.trend` and `numerical.within`.
- **Render and media.** Render with `${CLAUDE_PLUGIN_ROOT}/bin/render_candidate.py` (see the skill's Stage 6), never with a one-off script: only its report records the candidate hash, device, resolution, frames and file hashes. In the verifier, call `render.check_render_report(<case>/reports/render_<stamp>.json, args.case, expected_device=...)` on the report of the final render, and run the row with that report and its `media/final_<stamp>/` folder as `--input`, so a new render makes the row stale. For the movie, render with `--encode` (or encode an existing final render with `--encode-report`) and call `render.check_movie(<report>, args.case)` alongside `check_render_report`; pass the report and `media/final_<stamp>.mp4` as `--input`. When ffmpeg or ffprobe is missing, `check_movie` raises `render.MovieToolUnavailable`: catch it and call `verifier.write_blocked()`, so the row is `blocked`, not failed. Do not write an encoding script in the case. A check that only looks for image files under `media/` passes on three preview frames; it is not a render check.
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
- do not raise a row's `tolerances` or loosen its `measures` after a fail; they are frozen with the spec;
- record the change with `case_trace.py note --row <row_id>`: what the old verifier got wrong, the evidence, and why the new one still checks the row's `pass_predicate`.

The runner keeps a copy of every verifier version it runs under `reports/trace/verifiers/`, and of every case-local `.py` input under `reports/trace/inputs/`, and records each change in the row's `contract_changes` (`pass_predicate_changed`, `tolerances_changed`, `measures_changed`, `spec_changed`, `inputs_changed`). The gate and promote list every row whose verifier, imported case modules, `pass_predicate`, `tolerances`, `measures`, or world spec changed after a non-pass result, so the user can see what changed. The list does not change any verdict; it shows the user where to look. The gate's `spec_baseline` entry also compares the world spec with the copy frozen when the spec audit first passed (`reports/spec_baseline.json`), so a spec change made before any row was measured is listed as well.

## 8. Examples

- `${CLAUDE_PLUGIN_ROOT}/tests/fixtures/verifiers/verify_box_on_ground.py`: the smallest complete verifier.
- `${CLAUDE_PLUGIN_ROOT}/tests/fixtures/verifiers/verify_contact_example.py`: a verifier built on `worldagent_measure.contact`.
- `${CLAUDE_PLUGIN_ROOT}/tests/fixtures/verifiers/verify_final_movie.py`: a media row built on `render.check_movie`.
- `${CLAUDE_PLUGIN_ROOT}/tests/fixtures/verifiers/verify_soft_body_example.py`: a free-falling Soft Body with a driven visible mesh (`soft_body_setup`, `free_fall`, `transfer_deviation`); `make_soft_body_drop.py` builds its scene.

The Blender tests under `${CLAUDE_PLUGIN_ROOT}/tests/blender/` (`gas_setup_cases.py`, `particle_cases.py`, `soft_body_cases.py`, `coupling_cases.py`, `systems_cases.py`) build small scenes and call each helper; read them for working arguments.
