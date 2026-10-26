# General Blender Physics Workflow (Layer 1)

Use this workflow for any Blender built-in physics task. Read `material-playbooks.en.md` for solver-specific guidance. The goal is not merely a scene that appears to move, but a reproducible, bakeable, auditable simulation project.

## 1. Convert The Request Into An Acceptance Contract

Create a requirements matrix before modeling:

| Category | Resolve explicitly |
| --- | --- |
| Software | Blender version, allowed solvers/add-ons, built-in-only constraint |
| Assets | Source paths, reuse/isolation boundary, topology/scale permissions, licenses, packing |
| Physics | Material behavior, solver, gravity, units, mass relationships, colliders, coupling |
| Time | Frame range, FPS, event frame, preroll, settling window |
| Narrative/story | Prompt length, narrative clauses, story beats, ordered/simultaneous relations, readable duration, ending, and coverage evidence |
| Motion source | Inputs that may be keyed and outputs that must be solver-generated |
| Quality | Fidelity, stability, zero interpenetration, asset fidelity, scene scale/grandeur, proxy/approximation policy |
| Output | `.blend`, caches, config, scripts, reports, previews, video, checksums |
| Independence | Whether the case must be rebuilt without prior scenes, parameters, or caches |
| Rendering | Locked camera, engine/device, resolution, frame sequence, encoding |

Replace vague goals with measurable statements:

- “No premature collapse” becomes a pre-event displacement threshold.
- “Large-scale destruction” becomes displacement across multiple regions and height bands.
- “No penetration” becomes a per-frame zero-intersection test for solver surfaces, visible surfaces, and every reachable scene boundary. Any positive depth fails; a measurement epsilon may only classify coincident contact and floating-point noise.
- “Assets as realistic as possible” becomes declared thresholds for scale, proportions, silhouette, construction/contact logic, material response, and readability from the target camera, with hero assets and approximations recorded.
- “A grand scene” becomes declared bounds, zones/height bands, hero and supporting structures, macro/meso/micro hierarchy, and camera-readable scale rather than simply more repeated boxes.
- “All clothing is simulated” becomes full visible-vertex participation plus a nonzero evaluated mesh delta when Cloth is toggled at the same frame.
- “Made entirely of particles” becomes no visible shell, correct first-frame live count, expected solver type, and baked caches.

Write acceptance conditions before an expensive bake. Otherwise tuning tends to optimize selected images instead of the requested behavior.

## 1.1 General And Specialized Layers

The general layer answers what every simulated object must declare and prove. The specialized layer answers what an individual material or solver class additionally needs. A specialized playbook may add obligations but may not relax the general gates.

Create one manifest entry for every object whose motion, deformation, emission, or contact is part of the claim. At minimum record its stable name and role, solver owner, visible/simulation/proxy meshes, metric dimensions and applied transforms, material and mass/density, initial support and gaps, collision shape/thickness/margin/mask, allowed input channels, cache range, event/contact/final metrics, and a zero-interpenetration predicate over the complete frame range.

The general flow is contract -> topology and object-role graph -> static no-overlap audit -> per-object solver configuration -> continuous probe -> isolated bake -> fresh-process reopen -> frame-by-frame validation -> promotion. A physics modifier or `is_baked` flag is not proof that the contract passed.

Only after the general layer passes should the relevant playbook be loaded. Cloth adds pin groups, surface resolution, thickness, and self-collision requirements; rigid bodies add mass, shape, margin, substeps, and support-chain requirements; fluids add domain/source/effector coverage and volume checks; soft bodies add rest topology, springs, binding, and recovery checks. Every specialized validation must still pass object-object, object-scene, solver-surface/collider, visible-surface/boundary, and declared self-collision scans.

See `non-penetration-contract.en.md` for the complete object fields, detection methods, report fields, and retry policy.

See `asset-and-scene-quality.en.md` for the general asset/scene contract, metrics, and budget policy. It is part of the general layer; material playbooks may only add requirements on top of it.

## 1.2 Prompt-Driven Duration And Story Contract

Do not choose a fixed frame count and squeeze the prompt into it. First decompose the exact prompt into a `story_contract`, then derive seconds and frames from the physical and readable time needed to complete that story. Longer prompts, more independent events, sustained processes, and more spatial zones normally require longer simulations; never apply the same 4-5 second or 120-frame template to a long multi-stage request.

`manifest.json.contract.story` must record at least:

- `prompt_length_chars`, narrative-clause count, required entity/zone count, `required_beat_count`, and the reason for its `long_form` classification;
- for every beat: exact prompt clause, stable id, prerequisite/order/simultaneity, physics owner, allowed inputs, expected solved state, frame interval, minimum physical and readable duration, camera/numeric evidence, and pass predicate;
- `preroll_seconds`, `establishment_seconds`, per-beat time, transitions, `aftermath_seconds`, total `minimum_duration_seconds`, FPS, resulting `frame_range`, and any explicit user duration;
- `story_coverage`: status and actual frame interval per clause, evidence paths, first failure frame, omission/order/occlusion cause, and corrective action.

For every causal event, also record the trigger predicate/frame, initiating object or input, first contact or
equivalent causal evidence, activation/release predicate/frame when applicable, response-onset frame, and
requested-result predicate/frame. Require `trigger <= activation/release <= response <= result`, allowing
same-frame equality only when the solver step and adjacent-frame evidence make the ordering unambiguous.
A response that begins before its trigger is a hard causal failure. Motion, intact supports, solver-owned
transforms, and a complete cache are necessary mechanical health checks but do not pass a beat when the actor never reaches
the target or the requested result never occurs.

Classify a prompt as `long_form` when any condition holds: approximately 800 CJK characters or 500 whitespace-delimited words; at least four required recognizable story beats; at least three events/zones that need separate presentation; or explicit sustained wording such as continuous, gradual, repeated, sequential, final, or complete process. These are lower-bound triggers and must not be used to downgrade an actually multi-stage story.

When the user has not supplied a duration sufficient for the complete story, a long-form contract uses these minimum allocations: at least 2 seconds for establishment, at least 1.5 readable seconds for each major beat's setup/action/result window, at least 0.5 seconds between non-simultaneous beats, at least 2 seconds of final state/aftermath, and at least 12 seconds total. Use the greater of 12 seconds and:

`minimum_duration_seconds = preroll + establishment + sum(max(physical_duration_i, readable_duration_i)) + transitions + aftermath`

Static descriptive clauses do not justify invented physics events, but their required establishing, inspection, or spatial-reveal time still belongs in the shot and duration budget. Adding a required sequential beat must never reduce total duration. Beats may overlap only when the prompt makes them simultaneous and each remains readable in both visual and numeric evidence.

Preserve the requested FPS and plausible physical timescale by increasing total frames. Do not lower FPS, raise `time_scale`, compress forces/sources, subsample, interpolate, or speed up in post to fit a long story into a short clip. If an explicit user duration is shorter than the complete-story minimum, report the conflict and request adjustment or keep the result `blocked`; never silently omit a beat.

Acceptance requires `story_coverage.coverage_ratio == 1.0`, empty `missing_beats`, `partial_beats`, `order_violations`, `causal_order_violations`, and `unreadable_beats`, and cache/media end frames that reach the contracted final state. A solved beat hidden by the camera, cut in editing, or implied only through smoke, lighting, titles, or other presentation effects is still uncovered. Promotion requires `story_validation.passed == true`.

### 1.3 Hard Quality Gates And Per-Step Iteration

The following requirements apply to every candidate and are not optional presentation polish:

- **No faceted or giant liquid artifacts:** choose liquid resolution from the narrowest visible flow width, obstacle gap, sheet thickness, and wave height, and record the worst critical-cell coverage. Low resolution may validate timing only; medium/production resolution must reconstruct the liquid surface and be inspected at native output size. Use the solver's improved mesher, scale-appropriate mesh/particle radii, positive/negative smoothing iterations, and consistent normals. Layered facets, voxel stair-steps, spikes, isolated giant chunks, source-shell inflation, disconnected blobs, and temporal pops fail.
- **Liquid artifact repair order:** classify the cause as insufficient resolution, source `surface_distance`/particle inflation, meshing concavity, timestep/velocity, obstacle topology, or material-only reflection. Change one causal group, run the shortest falsifying continuous probe, and rebake when solver parameters, source, or obstacles changed. Reduce source-shell thickness, raise resolution, improve meshing, and correct the real source volume before blur, compositing, hidden planes, or camera occlusion.
- **Object-owned boundaries:** fluid effectors, architecture, furniture, props, terrain, and asset contacts must use authored/evaluated mesh surfaces or a proven equivalent analytic shape. Large AABB/OBB shells, enclosing boxes, catch floors, air walls, and undocumented proxies are prohibited; AABBs are broad-phase bookkeeping only and never the collider or acceptance proof.
- **No asset air walls:** intentional physical clearance is allowed, but a solver gap, rectangular stop face, or invisible spacing caused by a bounding-box/reduced collider is a failure. Run narrow-phase BVH/closest-point/signed-depth checks for every reachable pair and report `bbox_proxy_count`, `untracked_collider_count`, `asset_air_wall_count`, and the first failed pair; any nonzero count fails.
- **Iterate every substep to completion:** Stage 0, every Stage 1 substep, probe, bake, reopen, and render review has its own status, evidence, and gate. Do not enter the next step while the current one is `fail`, `unknown`, or missing evidence. Repeat “one causal change group -> shortest falsifying probe -> static/dynamic audit -> comparison render -> matrix update” until the current step is fully `pass`; “continue now and validate later” and a single attractive frame are not gates.

Write these hard gates to `requirements_matrix` and `manifest`, and retain liquid-surface metrics, collision representations, air-wall counts, iteration number, first failing frame, and corrective action in reports.

## 2. Use A Rebuildable Project Shape

A case root is a delivery surface, not a working directory. Use the canonical layout in `project-pipeline-and-template.en.md`:

```text
case/
|-- README.md
|-- manifest.json
|-- scene_final.blend          created only after promotion
|-- src/
|-- reports/
|-- previews/                  optional
|-- media/                     optional
|-- source/                    optional delivered masters/derivatives
`-- .work/                    candidates, caches, frames, logs, backups
```

Only `README.md`, `manifest.json`, and the promoted `scene_final.blend` belong as root files. Create optional directories only when the task needs them. Keep build, probe, bake, validation, and render entrypoints under `src/`; keep stage evidence under `reports/`; keep all disposable work under `.work/`. Run the plugin's `scripts/check_case_layout.py` before handoff.

`manifest.json` is the canonical record for the exact prompt, contract, story contract and coverage matrix, status, input assets and stable identifiers, seed, units, gravity, frame range, solver settings, object roles, density/mass, collision shapes and masks, artifact paths, camera, renderer, actual reopened RNA values, approximations, limitations, and validation summary. Keep detailed measurements in `reports/` instead of creating more root files.

## 3. Preflight Assets And Environment

### 3.1 Version and API

- Launch the target Blender executable in background mode and confirm its version.
- Use a minimal API probe for every critical RNA property, enum, and operator.
- Report requested values, values successfully written, reopened actual values, and missing/read-only properties separately.
- Do not call a physics simulation GPU-computed because Cycles uses OptiX/CUDA. Most Blender built-in solvers remain CPU workloads.

### 3.2 Paths and dependencies

- Resolve and check input/output paths before a long process. Use relative paths or packed resources for a self-contained delivery.
- Append rather than link when external Blender libraries are forbidden. Audit `bpy.data.libraries` and image paths after reopening.
- Cloud-sync software may lock Blender's atomic temporary save file. Save a working `.blend` in a system temporary directory and copy it to the delivery directory after a successful save.
- Give every case and parameter revision a separate cache directory.

### 3.3 Geometry and transforms

Record per object:

- source and evaluated vertex, edge, polygon, loop, UV-layer, and material counts;
- world bounds, dimensions, evaluated volume, center, matrix, parent, and non-uniform scale;
- boundary/non-manifold edges, zero-area faces, duplicate points, degenerate axes, normal consistency, and connected components;
- animation, shape keys, topology-changing modifiers, and external constraints.

Normalize participating objects to coherent units. Unless the workflow explicitly relies on object-level transforms, apply scale before simulation. Apply the same required scale and rotation to visible and reduced deformable bodies before binding. Retained non-unit transforms have caused systematic collision and high-resolution recovery mismatches.

### 3.4 Materials and visibility

- Check importer-generated alpha links. An OBJ material declared opaque can still become invisible if a PNG alpha channel is connected to Principled Alpha.
- Remove only an incorrect alpha link; preserve color, normal, roughness, and metallic maps.
- Audit auxiliary viewport/render visibility, display type, wire overlay, all-edge overlay, and in-front flags.
- Verify that hiding an auxiliary object does not stop the dependency graph from driving the visible result.

### 3.5 Asset And Scene Quality Preflight

- Audit hero-asset proportions, silhouette, thickness, center of mass, contact surfaces, and construction logic before textures or micro detail; materials must not conceal geometric errors.
- Record primary form, secondary construction, micro detail, and readability at the target camera/resolution.
- For a grand-scene request, define world bounds, zones/height bands, hero structure, support structures, background masses, and camera path before adding repetition.
- Check for floating, duplicated, unsupported, untracked, or undocumented decorative assets and write the result to the manifest and static report.
- Asset fidelity and scene grandeur are delivery gates alongside zero interpenetration. When detail budget is limited, reduce distant micro detail before hero form, support topology, collision boundaries, or camera-readable scale.

## 4. Scale, Mass, And Time

### 4.1 Scale

- Use real or internally coherent metric scale. Scale governs collision margin, soft-body tolerance, fluid cell size, and camera clipping.
- Express an absolute tolerance relative to feature size, collision thickness, or solver-cell size.
- A high nominal resolution can remain spatially coarse over a domain hundreds of meters wide.

### 4.2 Mass

When inertia matters, calculate:

`mass = evaluated_volume * material_density`

Report minimum, median, and maximum masses and the ratio between interacting object classes. Use finite-mass projectiles. If artistic scaling departs from physical density, record the factor and purpose.

### 4.3 Temporal resolution

- FPS defines output time; substeps, iterations, CFL, or adaptive steps determine numerical resolution.
- Fast small objects, thin colliders, stiff systems, and dense contact need more substeps, not only more iterations.
- Include preroll before the event and enough frames for settling or residual flow.
- Total frame count is also governed by the story contract in 1.2. Numerical substeps and narrative duration are separate dimensions; more substeps cannot replace enough story frames.
- Prefer a longer input pulse and smaller per-step displacement over a single extreme impulse.

Prior projects ranged from about 6-40 Bullet substeps/frame and 14-60 iterations. These values show workload range, not universal presets. Calibrate against minimum collision thickness, velocity, and probe results.

## 5. Separate Inputs From Solver Outputs

Classify every animated channel:

| Class | Examples | Keying policy |
| --- | --- | --- |
| Boundary/input | Wind, force strength, fuel/inflow, motor enable, kinematic collider | Allowed when disclosed |
| Visual input | Light, camera, material | Allowed; isolate from physics |
| Solver output | Debris transform, projectile trajectory after launch, cloth deformation, fluid surface, soft-body deformation | Do not fake |

Validation should scan location, rotation, and scale F-curves on governed objects. The presence of a physics modifier does not prove that visible motion came from it.

Declare coupling direction:

- Armature -> Cloth: bones supply a target shape; Cloth adds inertia and local deformation.
- Rigid Body -> Hook -> Cloth: rigid motion moves pinned boundaries; free cloth vertices keep solving.
- Separate force-field collections can prevent accidental double application to Bullet, FLIP Fluids, and Mantaflow Gas.
- Destruction and gas normally share timing or input forces; they are not automatically a two-way fluid-structure solve.

## 6. Collision Design

- Zero interpenetration is a hard gate: object-object, object-scene, solver-surface/collider, visible-surface/boundary, and declared self-collision pairs must have no positive geometric penetration depth on any frame.
- Match collision shape to function: BOX for regular bricks, SPHERE for cannonballs, MESH for complex static environment. Avoid complex dynamic mesh collision when a stable representation exists.
- Detect initial overlap. Additional collision margin on dense stacks can inject large separating impulses on frame 1.
- Use zero/small margins plus Split Impulse for dense rigid stacks when appropriate. Cloth and soft bodies need adequate substeps and scale-appropriate thickness.
- A passive rigid body does not replace a Cloth/Soft Body Collision modifier. Add every interaction layer explicitly.
- Create rigid-body constraints with `bpy.ops.rigidbody.constraint_add` only while the constraint object is really selected and active in the view layer (`view_layer.objects.active = obj`, `obj.select_set(True)`). Under `bpy.context.temp_override` in Blender 5.1.1 the operator can add nothing (`rigid_body_constraint` stays `None`) or crash Blender. Check that `rigid_body_constraint` exists after the call.
- Put only physically reachable visible environment objects in collision collections to reduce cost and unintended contacts.
- Hidden proxies may stabilize collision, transfer a load, or carry a reduced simulation only when permitted and documented. Never use them as undisclosed catch floors, air walls, or scripted trajectories.

## 7. Build, Probe, Bake, Promote

### 7.1 Build an unbaked master

- Start from `--factory-startup` or a named base scene. Create collections, objects, modifiers, constraints, and worlds in dependency order.
- Centralize tunable settings instead of scattering magic constants.
- Set deterministic random seeds and deterministic tie-breaking for equal placement/sampling candidates.
- Save and reopen the unbaked master once to catch session-only state and broken paths.

### 7.2 Initial static audit

Check object counts and names, solver-collection membership, ACTIVE/PASSIVE/KINEMATIC states, masses,
collision shapes/masks, recursive support to ground, initial overlaps, unsupported geometry, hidden colliders,
finite transforms, modifier order, pin groups, bindings, cache ranges, visible topology, and auxiliary roles.
Use AABB only to shortlist support/contact candidates; final support and collision evidence must use the
actual evaluated mesh with ray/closest-point/BVH tests.

### 7.3 Short continuous probe

Advance sequentially from the start through first contact. Avoid relying on isolated `frame_set()` jumps when a solver needs accumulated state.

Sample start, pre-event, event onset, first contact, peak interaction, and early settling. Record center/bounds, displacement, velocity or frame delta, non-finite coordinates, penetration, active counts, and material-specific metrics.

Calibrate a reduced but dynamically similar subsystem before the full scene: one garment, one projectile, one inlet segment, one soft body, or a smaller particle body that preserves particle spacing.

### 7.4 Formal bake

- Write to a candidate path and isolated cache directory.
- Record input, config hash, range, duration, cache objects, operator result, warnings, and errors.
- Do not modify topology, transforms, solver settings, or cache range during a bake.
- For multiple solvers, define bake order and report each cache separately.
- Cache the complete `story_contract` range, including every beat, transition, and final aftermath. A hero instant or peak-only window is not a complete candidate.

### 7.5 Reopen and promote

Close Blender, start a new process, open the candidate, and run read-only validation. Promote only a passing candidate. Keep the unbaked master when future tuning is expected. A true `is_baked` flag is not enough; resample actual motion and confirm paths/ranges.

## 8. General Validation Framework

### 8.1 Static assertions

- Correct Blender version, range, FPS, units, and gravity.
- Expected solver and object counts.
- Allowed external library/path count.
- No prohibited transform keys on physical outputs.
- Complete cache ranges.
- Declared hidden-proxy count and purpose.
- Topology and modifier compliance.

### 8.2 Dynamic assertions

- Near-zero pre-event displacement/velocity.
- Sufficient post-event movement across required objects, regions, and height bands.
- Finite coordinates, bounded velocities, and bounded scene growth.
- Expected final behavior: settling, continued flow, retained volume, or completed collapse.
- No penetration of any depth, spurious launch, disappearance, unintended topology change, or leaked auxiliary geometry.
- Story beats occur in the contracted order and duration, reach measurable requested outcomes, and have 100% coverage with no omission, unreadable compression, premature cut, or camera/effects-only substitute.

Report counts, quantiles, maxima, and grouped statistics. An average can hide a single numerical explosion or a material group that never moved.

### 8.3 Intersection and penetration

- Scan the base simulation mesh with render-only Solidify/Subdivision disabled.
- Use analytic tests for planes/simple primitives and BVH/rays/nearest-point checks for complex surfaces.
- For recovered high-resolution deformables, test both solver and visible surfaces; the visible surface is authoritative for delivery.
- Any positive penetration depth fails; do not split penetration into "minor" and "severe" to relax the gate.
- A measurement epsilon may only classify coincident contact and floating-point noise. Report it separately; it is not an accepted physical or visual penetration tolerance.
- Scan every sequential frame and every reachable pair/boundary; broad-phase culling may reduce work but cannot replace narrow-phase geometry tests.
- Report the first failing frame, object pair, region, signed depth, and whether the failure is on the solver surface or visible surface.

### 8.4 Support audit

For bricks, buildings, cannon pads, bridges, arches, racks, or any structure expected to begin supported:

1. detect direct ground contact;
2. detect XY overlap and bounded vertical gap with lower objects;
3. recursively prove the support chain reaches a foundation;
4. report direct, indirectly supported, and unsupported counts;
5. repair and rerun until unsupported count is zero.

Do not treat solver-driven falling from an invalid initial state as a valid support model. Record
`support_chain_status`, support source, `vertical_gap_m`, horizontal footprint, and
`unsupported_floating_count`; count asset-to-asset gaps without scene semantics as `asset_air_wall_count`.

### 8.5 Fluid Surface Quality

For every free-surface liquid, record critical-feature cell coverage, native-resolution evaluated-mesh
facet/normal indicators, connected-component outliers, volume trend, smoothing/meshing parameters,
`giant_chunk_count`, `spike_count`, and temporal-pop count. Low-resolution probes may establish timing
only. Production acceptance requires multiple cells across the narrowest flow, obstacle gap, sheet, and
wave feature and close/mid/wide native-size review. Faceting, voxel steps, spikes, source-shell inflation,
giant isolated chunks, disconnected blobs, or unexplained frame-to-frame pops fail; increase resolution,
correct source/particle radius or meshing, and rebake as needed. Do not hide the defect with shading,
reflection, fog, compositor, or camera occlusion.

## 9. Rendering And Media Are Separate Evidence

### 9.1 Render-only derivative

After physics passes, copy the scene for material, HDRI, lighting, camera, sample, color-management, or output changes. Do not alter participating topology, transforms, modifier order, solver settings, or cache path. Compare before/after reports and prove simulation preservation.

### 9.2 Preview strategy

- Low-sample render the initial, first-effect, peak, and final states.
- Check framing, readable scale, volume occlusion, transparent-object artifacts, and leaked auxiliaries.
- Use diagnostic renders to align an HDRI, sun, clip range, or narrow telephoto composition.

### 9.3 Video delivery

- Render a continuous PNG/EXR master sequence before encoding lossy video.
- Disclose temporal subsampling or interpolation. Provide a native-every-frame version when simulation fidelity matters.
- Verify continuous filenames, exact frame count, FPS, duration, dimensions, pixel format, codec, and full decode.
- Scan for black, frozen, and missing frames; inspect a contact sheet extracted from the final video.
- Label sound, flashes, smoke, glow, or fog added for presentation as render-only effects.
- Check the final video against `story_coverage`: each beat's onset, action, result, and required aftermath must be visible and ordered correctly, and media duration must not be shorter than the accepted story range.

### 9.4 Realism And Model Detail

Spend realism in layers instead of blindly increasing polygon count:

1. Calibrate proportions, silhouette, thickness, support logic, contact shadows, and camera-resolvable scale first. Textures and post effects cannot repair incorrect scale or collision behavior.
2. Add construction-consistent detail next: joints, brick or tile courses, eaves, fasteners, wear, dirt, burn marks, edge treatment, and roughness variation. Detail must be resolvable at the target pixel and sampling scale and must not silently become a collider.
3. High-resolution visible meshes, instanced decoration, normal/displacement detail, decals, smoke, fire, glare, and color management belong in a render-only derivative. When a visible surface is driven by a reduced solver body, record the transfer method, offset, and topology invariants.
4. Materials must match scale, energy response, and lighting context: metals need plausible highlights and roughness, masonry needs restrained micro-variation, and wetness, dust, or scorching need a spatial and temporal rationale. A uniform color or excessive glow must not hide penetration, unsupported pieces, or incorrect proportions.

### 9.5 Motivated Camera Motion

Camera motion is a render input, not a physics input. Write a shot list before keying the camera:

- Cover at least the establishing state, first release/contact, main interaction or peak, and settling/aftermath. For each shot, define the subject, aim point, lens, focus, and entry/exit timing.
- Use continuous paths with bounded acceleration. Keep the subject in a safe frame area and avoid crossing colliders, arbitrary shake, sudden zooms, clip-plane loss of projectiles or debris, or motion blur used to conceal solver failures.
- Camera, focus, shutter, exposure, and lights may be keyed. Do not key the position, rotation, or deformation output of objects that are supposed to be solved. Record the camera matrices, lens, shutter/motion-blur, resolution, and reopened RNA values.
- Use diagnostic renders for long lenses, low angles, and heavy occlusion. Check horizon, verticals, frustum clipping, focus, and event readability; camera polish never replaces multi-frame numeric probing.

### 9.6 Render acceptance

Render from the reopened final candidate, not the current session or an earlier revision. Low-cost render the initial, first-effect, every story beat's peak/result, and final frames to check materials, detail, exposure, focus, camera motion, and story readability before the continuous native sequence. Validate story coverage/order, missing, black, frozen, and undecodable frames, exact count/FPS/duration/dimensions, and a contact sheet; keep the render-only derivative and prove that caches, solver settings, and physics-object counts are unchanged.

## 10. Performance And Reliability

- Estimate object, constraint, particle, voxel, and frame counts before building. Dependency-graph overhead, collision pairs, memory, cache I/O, or rendering may dominate.
- Share mesh data or instance repeated visible assets. Keep independent objects where solver state must be independent.
- A reduced model must preserve dynamically relevant topology and scale. Do not collapse every shape to a box unless a pure collision proxy is allowed.
- For large particle renders, read baked positions into a point mesh and instance grains with Geometry Nodes without altering trajectories.
- Save candidate files and stage reports during long runs.
- Change one causal parameter group per retry and retain useful failed reports.

## 11. Recurring Failure Patterns

| Symptom | Likely cause | First response |
| --- | --- | --- |
| Frame-1 explosion | Initial overlap, excessive margin, dense constraint correction | Repair spacing, reduce margin, use Split Impulse, rerun short probe |
| High-speed tunneling | Per-step travel exceeds thickness, thin surface, unapplied transform | More substeps, longer input, appropriate thickness, apply transforms |
| Bake succeeds but nothing visible happens | Source below cell size, wrong collection, unsupported RNA property | Compute resolvable scale and audit actual reopened settings |
| Final state keeps drifting | Insufficient damping/friction or missing support | Calibrate settling; disclose any visual rest approximation |
| Floating structure | Broken support chain or inherited non-uniform transform | Run support audit, remove bad parenting, recompute world placement |
| Constraint network explodes | Penetration correction distributes energy globally | Remove overlap; tune time resolution; consider stacked/local structure |
| Visible deformable separates from solver | Non-unit transform, failed bind, wrong field domain | Apply matching transforms, verify bind, transfer explicit current-rest displacement |
| Hiding auxiliaries stops deformation | Visibility affects dependency evaluation | Test viewport/render hiding independently from evaluation |
| Smooth video is temporally inaccurate | Subsampling, interpolation, stale candidate | Deliver native frames and verify count |
| Transparent volumes cast black artifacts | Sampling volume remains ray-visible or transparent bounce limit is low | Hide from rays or raise transparent bounces |

## 12. Completion Checklist

- [ ] Exact prompt and independence boundary are preserved.
- [ ] `story_contract` covers every narrative clause; long-form duration/frame budget passes and `story_validation.passed == true`.
- [ ] Solver choice and limitations are explicit.
- [ ] Units, gravity, transforms, density, and mass ratios are auditable.
- [ ] Topology, support, overlap, collision layers, and modifier order pass.
- [ ] An unbaked master can be rebuilt.
- [ ] A continuous short probe passes.
- [ ] All final solver objects have complete caches.
- [ ] A fresh Blender process reopens and validates the final artifact.
- [ ] No prohibited animation, air wall, auxiliary wireframe, or dependency remains.
- [ ] Material-specific invariants pass.
- [ ] Stills and video come from the same final candidate.
- [ ] Realism layers, render-only detail, shot list, and camera-motion parameters are recorded.
- [ ] Camera paths are continuous and readable and do not hide numerical or collision failures.
- [ ] Native-frame media, decode, black/frozen-frame, and contact-sheet checks pass.
- [ ] The report separates verified facts, approximations, limitations, and render-only enhancements.

## 13. Iterate Until Every Requirement Passes

### 13.1 Build a requirements matrix

Before the first modeling pass, decompose the prompt into individually testable rows in `requirements_matrix`. Each row must contain:

- the requirement text and category (target-asset realism, scene, physics, story/time, materials, camera, media, or delivery);
- observable evidence (numeric probes, BVH/penetration scans, reopen validation, locked-camera stills, or native frames);
- pass threshold (`pass_predicate` in words, with its numbers in `tolerances` and `measures`; see the skill's Verification and Promotion step 3), current status, failed frame/object, and next corrective action;
- first-discovered version, most recent validation version, and final candidate path.

“Looks close” and “the render is polished” are not passing states. The object being simulated must be listed separately as `hero_physics_target`; its quality cannot be diluted by background detail, lighting, smoke, depth of field, or other decoration.

### 13.2 Iteration loop

Create a traceable candidate for every round and run the same sequence:

1. **Target realism review**: re-check the target's real-world category, dimensions, proportions, silhouette, thickness, mass/density, supports, contact surfaces, construction details, and semantic material. If a critical item is missing, repair the asset before formal baking.
2. **Static and material review**: lock the camera and run neutral diagnostic renders. Confirm that the target is immediately recognizable, that macro/construction/micro detail follows real manufacturing logic, and that texture scale is correct rather than hiding bad geometry.
3. **Short physics probe**: test the initial, first-effect, peak, and final windows. Confirm that the target is forced, deformed, contacts, retains volume, or settles as required. Any penetration, floating, spurious launch, no-motion state, or topology change fails the round.
4. **Formal solve and quantitative validation**: run cache, penetration, support, boundary, velocity, displacement, volume, contact, and grouped-statistics checks; retain the first failing frame and evidence.
5. **Reopen and presentation review**: close Blender, reopen the candidate, resample motion, and render initial/first-effect/peak/final states. Confirm materials, camera, target readability, and video frames all come from the same candidate.
6. **Update the matrix and decide the next round**: if any hard requirement is `fail`, `unknown`, or lacks evidence, record one causal change group, preserve the failed report, and repeat steps 1-5. Change one causal group at a time so improvement remains attributable.

### 13.3 Strict completion rule

Mark a candidate `complete` only when all of the following are true:

- every `requirements_matrix` row is `pass`, with no visual approximation substituted for physics or target-realism requirements;
- the simulated target's primary form, construction detail, semantic texture, scale, contact topology, and motion all pass independent checks;
- physics, reopen, locked-camera, and media validation all use the same final candidate;
- approximations, unresolved scales, render-only enhancements, external dependencies, and limitations are recorded;
- `story_coverage` is 100%, every beat is ordered and readable, backed by the declared solved state, and reaches the prompt's complete ending;
- target failure is never “fixed” by adding background objects, increasing resolution, smoke/glow, editing, or motion blur.

Compute budget may prioritize distant repetition and hidden micro detail, but it is not a completion reason when target realism, key structure, collision boundaries, or core motion are still below threshold. If external references, software capability, or an unresolved user decision blocks a hard requirement, keep that row `blocked` and report the gap; do not call an incomplete candidate final.

### 13.4 Iteration record

Save at least this information for every round:

```yaml
iteration: 03
candidate: output/candidate-03.blend
changed_group: [cloth_air_damping, target_material_roughness]
target_asset_status: pass
physics_status: pass
visual_status: fail
failed_requirements: [target_texture_scale]
evidence: [probe.json, frame_0056.png, reopen_report.json]
next_action: reduce weave scale to real-world millimeter range and rerender
```
