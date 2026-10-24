---
name: scene-simulation
description: This skill should be used when the user asks to build, simulate, bake, validate, or render a physically grounded Blender scene (rigid-body collapse, cloth, FLIP Fluids or other free-surface liquid, Mantaflow smoke or fire, particles, soft bodies, coupled terrain and water), or works on a WorldAgent case: manifest.json, world spec, story beats, requirements matrix, writing or running a verifier, the gate, stale rows, promote, scene_final.blend. Covers zero-interpenetration scenes with measured evidence and Cycles renders; excludes gameplay, encounters, and dialogue (navigability is checked only as a physical property).
---

**Host compatibility (Codex fallback).** Keep Claude Code's native token substitution. If `${CLAUDE_PLUGIN_ROOT}` is still visible literally, replace it with the absolute path from `WorldAgent plugin root:` in the session-start report before reading files or running commands, including paths in referenced documents; quote the resolved paths. If there is no session-start report (for example, the plugin hooks are not trusted yet), the plugin root is the folder two levels above the directory that contains this SKILL.md. If `$ARGUMENTS` is still visible literally, it means the user text supplied with this skill, preserving its quoted arguments. Do not rely on those tokens being shell environment variables.

Codex only: shell exports from SessionStart are not assumed. Set `WORLDAGENT_ENV_FILE` to the `WorldAgent session environment:` path from that report for shell commands. Where the body refers to an unset `WORLDAGENT_*` variable (including assets and memory/time budgets), use its resolved value from the report. Use `WORLDAGENT_*` or the WorldAgent config file instead of Claude's `/plugin configure`; invoke `/worldagent:<name>` commands as `$worldagent:<name>`.

Codex only: if SessionStart found the requested GPU but a sandboxed Blender command cannot see it, retry the same device probe through the host's permitted execution-approval mechanism. Honor a denial and report `blocked`; do not substitute another device. This adds no simulation-stage approval.

# Blender Physics Scene Simulation

Build a reproducible physical scene whose claimed motion is solver-generated, whose terrain and assets are believable at the requested camera distance, and whose final result is supported by a reopened Blender file, numeric evidence, and a complete Cycles render at the final resolution on the configured render device. Keep static scene construction, physics authority, caching, validation, refinement, and presentation separate.

This skill combines the WorldAgent simulation workflow with the post-layout scene production method of the WorldAgent scene pipeline (the terrain/population work after scene Stage 5). The scene pipeline's game-only stages are excluded.

Plugin root: `${CLAUDE_PLUGIN_ROOT}`. Its verification tools (`bin/`, `lib/`) decide whether a requirement passes; see **Verification and Promotion** below. The terrain toolkit used by Stage 1A–1G (Infinigen-based) is not bundled; when it is not installed, build terrain with Blender directly and record the substitution. The PBR/road texture library and vegetation registry it uses are not bundled either. When `$WORLDAGENT_ASSET_LIBRARY` is set (the session-start report lists its folders), use it as that local library: choose assets from it before building any, and record the choices in `manifest.json` `asset_sources` (see Stage 1). Without it, build assets procedurally and record the substitute in `manifest.json` approximations.

## Read Only What Applies

Always read the project pipeline and output template before implementation:

- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/project-pipeline-and-template.en.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/simulation-workflow.en.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/writing-verifiers.md` before writing any verifier

Then read only the applicable specialist material and quality references:

- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/scene-construction-pipeline.en.md` for Stage 1 terrain, water, vegetation, asset refinement, indoor/outdoor layout, and memory safety
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/material-playbooks.en.md`
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/flip-fluids.en.md` and `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/flip-scenario-baselines.en.md` when using FLIP Fluids
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/mantaflow-gas.en.md` when using smoke, fire, or gas
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/visual-realism-and-cinematography.en.md` for high-realism camera and lighting work
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/asset-and-scene-quality.en.md` for asset fidelity, grandeur, and measurable visual quality
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/non-penetration-contract.en.md` for universal collision and zero-penetration validation
- `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/evidence-base.md` only when a non-obvious rule needs justification

If the applicable scene artifacts already exist, use the post-Stage-5 inputs from the scene pipeline: `outdoor_scene_graph.json`, `landmark_layouts.json`, and `scene_descriptions.json`. Otherwise derive equivalent spatial/terrain descriptions from the simulation contract without importing game design. Never use `game_intention.md`, encounter specifications, NPC dialogue, progression gates, or other gameplay artifacts as design goals. Navigability is in scope as a physical check, not a gameplay goal: every connection the scene graph requires must keep enough route width, headroom, walkable slope, and continuity.

For a new case, initialize the standard case layout with `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/init_case.py` when practical; do not create empty optional folders or stub scripts. FLIP Fluids is not bundled: if a task needs it, the user installs their own licensed add-on, whose installer must match `${CLAUDE_PLUGIN_ROOT}/scripts/FLIP/SHA256SUMS.txt`. Never copy the installer into a case, Git, or delivery bundle. If a case needs FLIP and no matching add-on is installed, mark the affected FLIP requirements `blocked` by running their verifiers through the runner (the verifier catches `liquid.FlipUnavailable` and calls `verifier.write_blocked()`); never report them as passed or skipped, and never fall back to Mantaflow.

## Non-Negotiable Invariants

- Convert the request into measurable acceptance criteria before an expensive bake. Preserve the exact prompt in `manifest.json`.
- Derive duration and frame count from a prompt-level story contract. Long-form, multi-event, gradual, sustained, or multi-zone requests require proportionally longer timelines. Every required beat must occur in order, remain readable, be backed by solver state, and reach its requested outcome before acceptance.
- Treat target motion, preserved support, solver-owned transforms, and complete caches as mechanical-health evidence, not semantic success. A beat passes only when trigger, activation/release, response, and requested result occur causally and measurably.
- Classify the requested motion before choosing a solver. Appearance does not determine physics.
- Declare solver authority for every visible motion. Keyed forces, emitters, motors, kinematic boundaries, lights, and cameras may be inputs; transforms or deformations claimed as solver output may not be keyed.
- Use coherent metric scale, applied transforms where required, explicit collision geometry, deterministic seeds, and meaningful mass/density relationships.
- Treat interpenetration as a hard failure. At no frame may a simulated or visible object intersect another object, terrain, support, scene boundary, or collision proxy. A small measurement epsilon may reject floating-point noise, but it is not an allowance for penetration.
- Keep simulation meshes, visible meshes, hidden helpers, render-only enhancements, and collision representations explicitly named and documented. Never hide an air wall, catch floor, scripted trajectory, or unsupported proxy.
- Collision boundaries are mesh-first and object-owned: complex architectural, furniture, prop, terrain, and fluid-obstacle contacts must use the authored/evaluated object mesh (or a documented analytic primitive that exactly matches the physical object). A large AABB/OBB, enclosing box, catch shell, or untracked proxy is prohibited. AABB overlap is only broad-phase bookkeeping and is never the physical boundary or a pass/fail proof.
- Asset-to-asset contact has the same hard rule as asset-to-fluid contact. Intentional physical clearance may remain, but a solver gap, rectangular stop face, or “air wall” introduced by a bounding-box collider is a failure; narrow-phase geometry must follow the real object silhouette and contact features.
- Unsupported floating is a separate hard failure: every asset must have a measured support chain to ground, a structural member, a valid parent/support body, or a documented physically plausible cantilever/suspension. An unexplained gap below an asset, hidden support, catch floor, air wall, or camera crop is never an acceptable explanation.
- Free-surface liquids have a mandatory surface-quality gate: production resolution must resolve the narrowest visible flow/obstacle feature, the reconstructed surface must be smooth at native output size, and faceted layers, voxel stair-steps, isolated giant chunks, spikes, shell inflation, and unexplained disconnected blobs are hard failures. Increase resolution and mesh quality, reduce source/particle inflation, and rebake until the gate passes; shader smoothing, blur, compositing, or camera hiding cannot substitute.
- Every stage and every substep is an iterative gate. Do not start the next stage while the current stage has a `fail`, `unknown`, missing evidence, or an untested hard requirement. Repeat the smallest causal loop (change one group -> falsifying probe -> audit -> comparison render -> evidence update) until the current stage is fully `pass`, even when this requires additional frames or a longer bake.
- Give every candidate and parameter revision an isolated cache path under `.work/`; never mix caches across cases or revisions.
- A successful Blender operator is not evidence. Close Blender, reopen the exact candidate in a fresh process, sample evaluated motion sequentially, and validate the actual cache and visible result.
- Keep the case root clean. Temporary scenes, caches, logs, frame sequences, failed candidates, `.blend1`, and Blender atomic-save files stay under `.work/`.

## Verification and Promotion

The plugin, not the agent, decides whether a requirement passes. Every stage gate above is recorded through these tools.

Environment: at session start the plugin reports Blender, FLIP Fluids, and memory (from its settings, else auto-detected) and exports `WORLDAGENT_BLENDER`, `WORLDAGENT_FLIP_PATH`, and `WORLDAGENT_MEMORY_BUDGET_GB` to every shell command. If Blender was not found, ask the user to set the plugin's Blender path (`/plugin configure worldagent`). If FLIP Fluids was not found, FLIP requirements will be `blocked`; tell the user before planning a FLIP case. When `WORLDAGENT_MEMORY_BUDGET_GB` is set, keep every single Blender process below it in place of the fixed figure in the memory-safety rules. Run `python3 ${CLAUDE_PLUGIN_ROOT}/bin/worldagent_env.py` to re-check after the user changes anything.

1. **World spec (Stage 0).** Write the spec into `manifest.json`: story beats under `contract.story.beats` (id, prompt_clause, owner, frame_interval, pass_predicate, minimum_readable_seconds, depends_on), one entry per claimed object under `object_contracts` (name, role, solver_owner, visible_mesh, simulation_mesh, dimensions_m, initial_support, acceptance), and the scene graph under `topology`. Formats: `${CLAUDE_PLUGIN_ROOT}/schemas/world_spec.schema.json`.
2. **Spec audit.** Run `python3 ${CLAUDE_PLUGIN_ROOT}/bin/spec_audit.py <case>/manifest.json` and fix every error before building.
3. **Requirements matrix.** Turn every hard requirement into a `requirements_matrix` row (id, text, category, pass_predicate with a numeric threshold, status `unknown`, and `covers` listing the story beats it verifies). Every beat must be covered. Formats: `${CLAUDE_PLUGIN_ROOT}/schemas/requirements.schema.json`.
4. **Verifiers.** For each row write `<case>/src/verify_<row>.py` using the `worldagent_measure` helpers, following `writing-verifiers.md`.
5. **Run.** Execute each verifier through `python3 ${CLAUDE_PLUGIN_ROOT}/bin/run_verifier.py <case> --row <id> --script <verifier> --candidate <candidate.blend> [--input <cache>]`. The runner opens a copy of the candidate in a fresh Blender process and records the result in the row and in `reports/`.
6. **Gate.** `python3 ${CLAUDE_PLUGIN_ROOT}/bin/gate.py <case> --candidate <candidate.blend>` passes only when the spec audit passes, every beat is covered, and every row is a runner-measured pass on this candidate with unchanged inputs. `--mark-stale` marks rows whose inputs changed.
7. **Final render (Stage 6).** When every other row passes, render the final sequence of the same candidate with `bin/render_candidate.py` and verify the render and media rows. They are rows like any other, so promote cannot pass before them.
8. **Promote.** `python3 ${CLAUDE_PLUGIN_ROOT}/bin/promote.py <case> --candidate <candidate.blend>` re-runs every row's verifier on the candidate, runs the gate, and only then installs `scene_final.blend` and sets the manifest status to `validated`.

Rules:
- Never write a row's status, evidence, `verified_by`, `input_hashes`, or any `reports/verify_*.json` by hand. Never write `scene_final.blend` except through promote.
- A required solver or add-on that is missing gives `blocked` (written by the verifier through the runner with `verifier.write_blocked()`), never `pass`.
- Until promote succeeds, the case is not complete. Report which rows are not passing and why; do not describe the scene as done, validated, or final.
- Story order, shot coverage, gas, cloth and elastic, and coupled-response rows have measurement helpers too (`causal`, `shot`, `gas`, `deformable`); see `writing-verifiers.md`.
- Record every refinement attempt (hypothesis, the one causal change group, result, reason) with `python3 ${CLAUDE_PLUGIN_ROOT}/bin/case_trace.py note <case> ...`; `case_trace.py show <case>` prints the case history. The runner, gate, and promote add their own events.
- Do the step the user asked for, then report and stop. "Bake it" means build and bake a candidate, not verify, render, or promote it. Continue to a later step only when the user asks for it or used `/worldagent:run`.
- When a row fails, fix the scene or the bake, not the requirement. Do not change a verifier, a `pass_predicate`, or the world spec to make a row pass (`writing-verifiers.md`, "Changing a verifier or the spec after a fail"). Such changes are recorded and shown to the user.
- The user can run the steps one by one with `/worldagent:new`, `/worldagent:bake`, `/worldagent:verify`, `/worldagent:render`, and `/worldagent:promote`, or all of them with `/worldagent:run`.

## Solver Classification

| Physical target | Preferred Blender system | Boundary |
| --- | --- | --- |
| Impact, collapse, rolling, stacking, finite fragments | Bullet rigid bodies and constraints | Not continuous fracture |
| Fabric, flags, membranes, garments | Cloth | Pre-Cloth spatial resolution, temporal/collision quality, convergence, and visual fidelity must pass |
| Free-surface liquid | FLIP Fluids | Compatible runtime, resolvable sources/channels/obstacles, and auditable cache required |
| Smoke, fire, buoyant gas | Mantaflow Gas | Usually one-way coupled to destruction |
| Independent ballistic particles | Newton particles | No complete dense contact network |
| Dense granular approximation | FLUID/DDR SPH particles | Not DEM; disclose compressibility and coupling limits |
| Spring-like elastic deformation | Soft Body | Not general FEM |
| Coupled systems | Explicit solver graph | Record direction, ownership, bake order, and missing reaction forces |

For rigid fragmentation, define body count, active/passive split, characteristic piece size, impact-zone resolution, projectile-to-piece scale, and movement threshold before baking. Render-only debris cannot substitute for solver-authoritative fragmentation.

## Stage-Gated Workflow

Do not merge stages merely to save filenames. Each stage has an artifact and a gate; advance only after the gate passes.

### Stage 0: Contract and Topology

- Record Blender version, renderer/device, assets and licenses, independence boundary, units, gravity, FPS, frame range, event timing, keyed inputs, solver outputs, requested deliverables, asset-fidelity target, grandeur target, and numeric pass/fail thresholds.
- Parse every sequential, gradual, simultaneous, and final-state obligation into `story_contract` entries containing prompt clause, order/dependency, solver owner, minimum readable duration, frame interval, camera evidence, and pass predicate. Record prompt length, beat count, long-form classification, duration formula, minimum seconds, and resulting frames. Use the workflow's long-form duration floor when explicit duration is insufficient.
- Build a topology/interaction graph for regions, supports, openings, terrain transitions, water paths, sources, destinations, and solver coupling. Simple scenes may keep it inside the manifest.
- Estimate scene bounds, mesh resolution, instance count, render cost, and solver workload before building.

Gate: every requested phenomenon and story beat has an owner, observable metric, readable time allocation, and path through the scene; the frame range covers the complete story plus preroll and aftermath.

Duration is elastic in service of the complete story. At a fixed requested FPS, increase the total frame count when physical onset, transport, interaction, settling, or aftermath cannot remain readable. Do not compress a long process with extreme time scale, frame dropping, interpolation, or an abbreviated source pulse. If the user's explicit duration is shorter than the contracted physical and readable minimum, record the conflict and keep the candidate `blocked`/`limited` rather than silently deleting beats.

### Mandatory liquid-surface quality gate

For every free-surface liquid candidate, define and measure a surface-quality contract before a full bake:

- choose `resolution_max`/FLIP resolution from the narrowest visible flow width, obstacle gap, sheet thickness, and wave height; require multiple cells across every critical feature and record the worst cell coverage;
- run low-resolution timing only as a probe, then a medium/production candidate with mesh reconstruction enabled; inspect the actual evaluated surface at native output size, not only a shaded viewport;
- use the solver's improved/volume-preserving mesher, appropriate mesh/particle radius, positive/negative smoothing iterations, and smooth normals where available; these settings must preserve volume and contact topology rather than blur them;
- reject faceted or layered surfaces, repeated voxel steps, needle spikes, giant isolated chunks, shell-inflated sources, disconnected blobs, and frame-to-frame popping; compare connected components, edge/normal statistics, volume trend, and close/mid/wide renders;
- when an artifact appears, classify it as resolution, source shell/particle inflation, meshing/concavity, timestep/velocity, obstacle topology, or material-only. Change one causal group, run the shortest probe that can falsify it, and rebake the candidate if the solver surface changed. A render-only blur, hidden plane, or reflection trick is never an acceptable fix.

This gate is mandatory even when the liquid is visually attractive in one frame. `fluid_surface_quality.pass == true` and `giant_chunk_count == 0` are required for promotion.

### Mesh-owned collision boundary gate

For every fluid effector, rigid collider, cloth/soft-body obstacle, and asset contact, record `collision_representation`, `source_object`, `evaluated_mesh_hash` or equivalent topology evidence, effective thickness, and margin. The default representation is the actual evaluated mesh surface. Analytic primitives are permitted only when the object is itself that primitive or the equivalence is proven. Validate with narrow-phase BVH/closest-point/signed-distance tests and report `bbox_proxy_count`, `untracked_collider_count`, `asset_air_wall_count`, and the first failing object pair. Promotion requires all reported counts to be zero and no failing pair.

### Stage 1: Static Scene and Natural World Construction

Use the scene pipeline logic in the reference named below. It replaces the original static-layout method while preserving the simulation contract. Start from the validated Stage 0 topology and any post-Stage-5 spatial artifacts. Build the land as if no architecture exists, then place architecture/props on the resulting support surfaces.

Classify indoor, outdoor, and terrain content before geometry creation. Use one shared height-field terrain surface wherever the requested terrain is representable as a height field; document another representation for caves, overhangs, or other topology a height field cannot express. Do not pre-model the dynamic output of a later solver. Static decorative water is allowed only when it is not the simulated event. Establish procedural materials, basic lighting, a diagnostic camera, hero assets, and macro/meso/micro scale before final render polish.

Library-first: when `$WORLDAGENT_ASSET_LIBRARY` is set, build from `asset_sources` in `manifest.json`. If it is missing, create it first: read the library's `index.json` (or its folders and each `meta.json`) and add one entry per visible object with `object`, `library_path` (or null), `sha256`, `license` and `reason`. Append each chosen asset into the scene with `bpy.data.libraries.load(path, link=False)` so the candidate does not depend on the library, check that the file's SHA-256 matches the entry, and change only placement and the scale needed to meet the object contract. Build an object procedurally only when its entry has no library asset, and keep the reason. The replacement policy below still applies to library assets.

Follow substeps 1A–1H (terrain, hydrology, meshing, derivative maps, materials, scatter, population, derived asset/placement/render specifications), the Stage 1 quality refinement loop, indoor and outdoor layout coverage, and memory and process safety in `${CLAUDE_PLUGIN_ROOT}/skills/scene-simulation/references/scene-construction-pipeline.en.md`. Read it whenever the case has terrain, vegetation, water bodies, multi-space architecture, or heavy asset imports.

Gate: every 1A–1H gate in that reference passes; manifests and placements are complete; all objects are grounded or supported.

### Simulation-coupled scene refinement

The scene and simulation may refine one another, but ownership stays explicit. Use the simulation stages (3–5) to generate evidence such as fluid wetness/erosion/sediment fields, impact/debris footprints, cloth contact, smoke deposition, or stress-driven material changes. Feed those measured fields back into terrain derivatives, material masks, asset placement, and camera blocking only when the feedback is causally justified. Then rerun the shortest falsifying probe, update the static audit if geometry changed, and rebake the affected candidate. Never replace solver-owned motion with a keyed pose or add a render-only effect while claiming the simulation produced it. Record coupling direction, transfer method, resolution, timing, and limitations in the topology graph and manifest.

This is a mandatory closed loop, not a one-way handoff:

`scene layout/assets -> simulation probe/bake -> measured failure or improvement -> scene revision or solver revision -> static audit -> simulation re-run`.

When a simulation exposes a problem, classify it before editing: return to scene construction for wrong topology, scale, support, collider shape, water/terrain contact, asset fidelity, material response, camera blocking, or missing narrative context; return to the simulation stage for solver parameters, cache range, time resolution, force/source input, or coupling ownership. Change one causal group at a time, keep before/after evidence, and repeat the loop until both physics and visual criteria pass. A render-only patch is never an acceptable substitute for a scene or solver correction.

### Stage 2: Static Layout Audit

Check unsupported or floating objects, unreasonable object intersections, initial collider overlap, terrain grounding, recursive support to ground, transforms, topology, normals, visibility, asset proportions/material response, scene bounds, hierarchy, and repeated-instance variation. Distinguish intentional cantilevers, bridges, embedded foundations, and valid contact from errors. After moving an object, re-ground and re-check it. For every reachable fluid/asset pair and asset/asset pair, prove that the collision boundary follows the evaluated object mesh rather than a large bounding box; intentional clearances must be measured, and solver-created air gaps fail.

Artifact: `reports/static.json`.

Gate: no unexplained floating, overlap, support, topology, dependency, or visibility failures; every collision boundary exists; initial interpenetration scan is empty; asset and grandeur criteria pass.

### Stage 3: Physics Configuration and Probe

- Configure only solver systems that own requested motion. Centralize parameters and record requested versus actual Blender/add-on RNA values.
- Run a short continuous probe through pre-event stability, first release/contact, early interaction, and initial settling. Never validate by jumping to isolated frames.
- Calibrate an expensive full scene with a reduced subsystem only when scale ratios controlling stability are preserved.
- For liquid, include a surface-quality probe that measures critical cell coverage, evaluated mesh smoothness/facet indicators, connected components, volume trend, wall/asset clearance, and giant-chunk/spike count. A low-resolution probe may validate timing only; it cannot pass the final surface-quality gate.
- For collision, run a narrow-phase mesh audit after any broad-phase candidate list. AABB/OBB extents may prioritize pairs but must never be used as the collider itself or as proof that an object has an air wall.

Artifacts: applicable source files under `src/` and `reports/probe.json`.

Gate: pre-event motion below threshold, intended input activates, collision/coupling is visible, coordinates finite, continuous probe has zero interpenetration, and no prohibited keyed output/helper exists.

### Stage 4: Candidate Bake

- Freeze topology, transforms, solver settings, cache range, dependencies, and approved asset versions before baking.
- Bake each candidate to `.work/candidates/<candidate_id>/scene.blend` with external caches under `.work/cache/<candidate_id>/`.
- Record config hash, range, duration, cache objects, warnings, operator results, memory use, and causal changes from the previous candidate.

Artifacts: candidate scene/cache and `reports/bake.json`.

Gate: complete cache coverage over the full story range, finite coordinates, no explosive growth or penetration, no missing cache, intact visible-surface transfer, and no truncated story beat.

### Stage 5: Reopen and Validate

- Close Blender, reopen the exact candidate in a fresh process, and run read-only static and sequential dynamic validation.
- Verify object counts, solver roles, keyed channels, cache state/range, motion milestones, bounds, support invariants, material-specific metrics, asset version hashes, and camera-visible composition.
- Scan every sequential frame for object-object, object-scene, solver-surface, visible-surface, water-terrain, and asset-terrain interpenetration; any positive depth fails promotion.
- Scan every sequential frame for solver-created air gaps and rectangular stop faces at reachable contacts. Compare fluid/asset surfaces to the actual evaluated object mesh with BVH/closest-point tests; report `bbox_proxy_count`, `untracked_collider_count`, `asset_air_wall_count`, and measured contact gaps. Any unexplained gap or nonzero proxy count fails promotion.
- Audit every asset's recursive support chain before and after simulation. Report `unsupported_floating_count`, the first unsupported object, vertical gap, support source, and whether the support is visible/structural. Any unexplained floating asset fails promotion, even when the collision and render otherwise look plausible.
- For liquid, inspect native-resolution close/mid/wide frames and record `fluid_surface_quality`: critical cell coverage, smoothing/meshing settings, facet metric, connected-component outliers, volume trend, spike/giant-chunk count, and temporal popping. Any visible faceting, voxel stair-step, giant chunk, or unexplained blob fails promotion.
- Validate `story_coverage` against the exact prompt. Coverage must be 100% with no missing, partial, unreadable, out-of-order, premature, or presentation-only substitute beat. Record it in `reports/validation.json`; that file is a narrative summary, while the gate's coverage check and the runner reports are authoritative.
- For Cloth, run native-resolution visual-fidelity renders after numeric validation and before promotion. No polygonal, faceted, grid-imprinted, jagged, or temporally popping cloth appearance is acceptable.
- Do not promote yet. The render and media rows are part of the gate, so promotion follows the final render (Stage 6).

Artifacts: `reports/validation.json`.

Gate: every required criterion passes, or the manifest explicitly marks a limited/failed candidate rather than a final success.

### Stage 6: Presentation, Final Render, Media Validation, and Promotion

- Render the candidate whose physics, scene, story, and camera rows all pass. If presentation needs changes (lights, materials, camera polish), make them as a new candidate, prove that physics object counts, participating topology, solver settings, caches, and asset identities are unchanged, and verify every row on it again.
- Render milestone previews first, then render the complete native-resolution sequence. Render both with `python3 ${CLAUDE_PLUGIN_ROOT}/bin/render_candidate.py <case_dir> --candidate <candidate>`: add `--preview --frames <list> [--resolution WxH]` for previews (written to `previews/`), and nothing for the final sequence (every frame at the final resolution, written to `media/final_<stamp>/`). Add `--flip` when the scene has a FLIP domain and `--samples N` to set samples. Each run writes `reports/render_<stamp>.json`. The render/media rows check that report with `worldagent_measure.render.check_render_report` (see `references/writing-verifiers.md`). Do not render with one-off scripts. Preserve every accepted story beat and its readable duration; do not edit, time-remap, interpolate, or crop away physics evidence.
- Final delivery for this skill is a complete sequence/movie rendered with Blender Cycles at the final resolution: 1920x1080 (`resolution_x=1920`, `resolution_y=1080`) unless the plugin's render-resolution setting (`$WORLDAGENT_RENDER_RESOLUTION`) sets another; samples come from the render-samples setting (`$WORLDAGENT_RENDER_SAMPLES`) when set, else the scene. It is rendered on the render device in `$WORLDAGENT_RENDER_DEVICE` (OPTIX unless the plugin's render-device setting says otherwise; the session-start report shows it). Set the device with `worldagent_measure.render.setup_device(scene)` from `${CLAUDE_PLUGIN_ROOT}/lib`, not by hand: `scene.cycles.device` accepts only `'CPU'` or `'GPU'`, and the GPU type lives in the Cycles preferences. Record the device type and devices it returns, plus Blender/driver, samples, denoiser, color management, FPS, frame count, codec/pixel format, and output hash. If the configured device is unavailable, `setup_device` raises; do not fall back to CPU or EEVEE and call the render compliant; report it as blocked.
- Validate frame numbering/count, FPS, duration, dimensions, codec/pixel format, full decode, black frames, exact duplicate frames, camera continuity, exposure continuity, and a contact sheet. Check pixel statistics and inspect representative close/mid/wide shots.
- Confirm the final render is beautiful and refined: believable assets/materials, macro-to-micro richness, complete story, intentional camera narration, smooth shot transitions, coherent atmosphere/light, and no visible solver or asset defects. Repeat the relevant causal refinement stage until the evidence improves or a declared compute limit is reached.

- When the render and media rows pass, promote the candidate to root `scene_final.blend`, only through the plugin's promote (see **Verification and Promotion**). An unvalidated or earlier file is never final.

Artifacts: `scene_final.blend`, requested `previews/` and `media/` plus `reports/media.json` and the final quality review. Keep all intermediate derivatives under `.work/`.

Gate: media comes from the reopened accepted candidate, contains the complete story in order, is a valid Cycles output at the final resolution on the configured render device, and does not conceal solver or layout failure.

## Failure and Retry Policy

- Stop a candidate on non-finite values, explosive growth, any penetration, missing caches, hidden prohibited colliders, broken high-resolution transfer, unsupported assets, or visibly generic hero assets.
- Stop promotion when a required beat is absent, too short, out of order, physically incomplete, not visible in the requested output, or replaced by editing/render-only effects. Extend the timeline and rebake instead of accelerating physics. If the requested duration conflicts with the complete story, report the conflict as blocked rather than silently dropping content.
- Change one causal group at a time: geometry/transforms, collision representation, temporal resolution, solver stiffness/damping, force/source input, asset generation, material rules, camera/lighting, or other render-only settings.
- Re-run the shortest probe or render comparison that can falsify the proposed fix before another full bake.
- Know when a row cannot pass at this resolution. If the failing measurement is at or below what the solver resolves (for a grid solver, a feature or error no larger than about one cell; `solver_mesh.grid_cell_size` gives it), the fix is a higher resolution, not another collider or offset tweak. Estimate the bake time at that resolution; if it does not fit the time budget, stop refining that row, mark the case `limited`, and report the measured value next to the cell size.
- Keep failed candidates only when they provide useful evidence, and keep them under `.work/`, never at the case root.

## Layered Simulation Contract

Every case has two instruction layers. The general layer is mandatory for every simulated object; the specialized layer adds obligations for a material or solver class and may not relax the general layer.

### General layer: one contract per simulated object

Before building, create one manifest entry for each object whose motion, deformation, emission, or contact is part of the claim. Record stable name, role, solver owner, visible mesh, simulation mesh, documented proxy, metric dimensions, applied transforms, origin/parent, material class, mass/density, initial support/contact, collision participation/shape/thickness/margin/mask/collection, self-collision policy, reachable scene boundaries, allowed inputs versus solver-owned outputs, and initial/event/contact/final metrics with exact pass predicates including zero interpenetration.

The general workflow is: contract -> topology/object-role graph -> static no-overlap audit -> per-object solver configuration -> continuous probe -> isolated bake -> fresh-process reopen -> per-frame validation -> final render -> promotion. A physics modifier or successful bake operator is not evidence.

### Specialized layer: class-specific obligations

Read only the playbook for each requested class. It adds topology, parameter, coupling, cache, and material-specific checks: Cloth pinned groups and measured pre-Cloth resolution plus convergence and thickness/self-collision; rigid-body shapes, mass, margins, contact, and support; FLIP runtime identity, Domain/Fluid/Inflow/Outflow/Obstacle coverage, cell resolvability, cache payload, and volume; Mantaflow staged Data/Noise validation and density continuity; Soft Body rest topology, binding, springs, and recovery. Attach these settings to object entries. Every specialized validation still passes the general zero-penetration scan.

If a solver or Blender version cannot satisfy the no-penetration predicate, stop and report a failed or limited candidate. Never replace failed solver output with keyed deformation or render-only geometry while calling it a successful simulation.

## Asset and Scene Quality Contract

Physics correctness does not excuse crude assets or an undersized scene. Treat fidelity and scale as measurable contract fields before final rendering.

- Use coherent real-world proportions, thickness, articulation, contact points, center of mass, and material response. Hero assets must be recognizable by silhouette and construction, not color alone.
- Build primary form first, secondary construction (joints, seams, supports, openings) second, and micro detail only when it survives the target resolution. Do not spend geometry on invisible details while proportions/contact are wrong.
- Use physically plausible roughness, metallic response, transmission/opacity, wear, seams, edge treatment, and variation. Do not hide defects with bloom, noise, fog, or dark shading.
- For grand/monumental scenes, define coherent bounds, multiple zones/height layers, hero/support/background hierarchy, and a camera path that reveals scale. Populate macro, meso, and micro layers; repetition follows site logic and stable variation, never generic boxes.
- Keep the scene physically complete: supports reach foundations, openings are real, contact paths are reachable, distant masses do not float, and decorative geometry cannot become an untracked collider. If scope exceeds budget, preserve macro topology, hero assets, collision boundaries, and camera-readable scale, then disclose the reduction.

Asset realism, scene grandeur, story completeness, camera narration, and final polish are acceptance criteria. A stable but generic or undersized scene is not deliverable.

## Minimal Deliverables

Always deliver only the requested final products: `scene_final.blend` after successful promotion; `manifest.json` with prompt, contract, configuration, artifact paths, verified facts, approximations, limitations, asset sources/seeds/prompts, and status; reconstruction source/config under `src/`; and applicable reports under `reports/`. Put requested final previews/movie under `media/` and keep caches/failed candidates/temporary scripts under `.work/`. The final response must distinguish verified facts from approximations and render-only effects and mention validations that could not run.
