# Mantaflow Gas Smoke, Fire, And Explosion Workflow

Use this reference for smoke, fire, thermal plumes, fog-like gas, and explosion columns solved by Blender
Mantaflow Gas. This reference governs smoke-specific construction, diagnosis, and voxel evidence. The parent
skill still takes precedence for story coverage, solver authority, zero penetration, isolated candidate
caches, fresh-process validation, authorization, and delivery. Gas plus Bullet is not a validated detonation
model or automatic two-way fluid-structure coupling; describe extreme explosions as visual-scale gas/rigid
approximations.

## 1. Smoke Contract And Solver Authority

Convert phrases such as "realistic smoke," "violent explosion," and "sustained column" into falsifiable
`story_contract` fields:

- event source, location, direction, start/stop frames, and the pre-event interval that must remain empty;
- order and minimum readable duration of short outburst/combustion, sustained root supply, rise and widening,
  lateral drift, dilution, and aftermath;
- column width, height, and velocity ranges relative to fixed scale references such as building width,
  impact floor, roof, ground, or another hero dimension;
- authority of every Flow, Domain, Effector, Wind/Force Field, Bullet input, camera, and render-only effect;
- expected density region, six-face Domain clearance, open/closed boundaries, permitted exits, and observation window;
- Data/Noise resolution, cache mode, disk/time budget, preview and delivery requirements, and pass thresholds.

Cause precedes effect. Smoke, fire, scorching, breach, or damage silhouette must not appear before the event.
Afterward, the smoke root must remain attached to a real fuel/heat/density source. A flash, black blob,
procedural volume animation, or visibility switch cannot replace Mantaflow output. Once inputs stop, claimed
gas output evolves through buoyancy, entrainment, diffusion, wind, and boundaries rather than keyed translation.

## 2. Runtime Identity, Roles, And Resolvable Scale

Treat Blender build, Mantaflow settings, cache mode, and cache directory as one runtime identity. Record actual
RNA values after read-back, not only requested script values.

Declare at minimum:

- one Gas Domain with world bounds, transforms, `resolution_max`, time scale, CFL/time steps, vorticity,
  adaptive settings, boundary modes, Data/Noise formats, and cache path;
- geometry, Flow/source type, density, fuel, temperature, initial velocity, surface distance, subframes,
  active frames, and moving/deforming export policy for every Flow;
- target and non-target collections for every Effector, collider, and control force field;
- Mantaflow authority over smoke, flame, temperature, and final high-resolution volume, with lights, materials,
  camera, and compositing kept in presentation.

Estimate base voxel size from:

`voxel_size ~= max(domain_dimensions) / resolution_max`

Record base voxels across each source's thinnest axis, critical opening, obstacle, and expected root width.
Important sources must span multiple base voxels. Prove continuous sampling of a fast or moving small source
with subframes/time steps. A denser Noise grid cannot repair missed Data samples. When enlarging a Domain,
estimate a voxel-preserving starting resolution with:

`new_resolution_max ~= old_resolution_max * new_max_dimension / old_max_dimension`

Then recheck thin-source coverage, memory, cache, and time steps. The formula never replaces a new probe.

## 3. Build The Column In Layers

Reuse the causal chain, not absolute values: **wide layered sources -> continuous Data transport -> stable Domain
with clearance -> Noise only after base flow passes -> sequential-frame, voxel, and reopen evidence**.

1. **Define the space-time envelope first.** A contact or ignition should lead to a short outburst, sustained
   root smoke, rising and widening old smoke, and later dilution. The top needs measurable net rise while the
   root stays causally connected; an expanding stationary sphere fails.
2. **Separate source stages.** Use offset, staggered, differently sized sources for short combustion, sustained
   smoke, sustained fire, and asymmetric entrainment. One giant source must not perform every stage, and all
   sources must not share identical location, dimensions, and active frames.
3. **Separate generation from transport.** Density/fuel/temperature generate smoke and fire; temperature
   buoyancy and initial velocity establish early direction. A broad, moderate upward Wind may approximate
   unresolved thermal-plume convection/entrainment for high-temperature smoke, but it must be disclosed as
   an engineering approximation rather than extra blast force or measured thermal-fluid behavior.
4. **Isolate force fields.** Put Gas-shaping Wind and Bullet blast force in separate collections and prove with
   on/off differentials that Wind does not drive rigid bodies, fragments, or props. Spatial falloff is not
   system isolation.
5. **Prefer a stable fixed Domain.** Fit it around the expected plume path, not only the emitter, with clearance
   above and downwind. Promote Adaptive Domain only after Data/Noise continuity, cache stability, and an
   interpretable boundary probe. Return to a fixed Domain when density vanishes, flame remains without smoke,
   or cache behavior is unstable.
6. **Manage smoke age explicitly.** Start explosion smoke with Dissolve off so post-source smoke rises, entrains,
   and dilutes naturally. When short-lived fog/steam or fixed-domain accumulation truly requires Dissolve,
   validate lifetime, vertical density profile, root continuity, and aftermath per frame, and verify the
   current Blender RNA direction with a short probe. A property containing `speed` may still control lifetime;
   never infer adjustment direction from its name.
7. **Data before Noise.** Base Data must first prove continuous source sampling, correct direction, root
   connection, centroid/top net rise, and Domain safety. Noise only adds small-scale curl and cannot repair
   gaps, wrong direction, weak supply, or boundary contact.
8. **Separate physics from material gain.** Flow density is a solver input; Principled Volume density,
   absorption, and scattering alter visibility; flame/temperature attributes drive fire. Judge cache under
   fixed exposure and a neutral diagnostic material before tuning smoke color, absorption, or blackbody.
   Emitters remain solver-visible but hidden during viewport acceptance and rendering.

## 4. Probe, Bake, And Candidate Promotion

Increase cost in stages:

1. **Structural preflight:** validate story contract, units, roles, no pre-event smoke/fire, Domain/source voxel
   coverage, force isolation, boundary modes, writable isolated cache, and the parent skill's zero-penetration
   gate for every visible or rigid object.
2. **Short Data probe:** run a continuous window through pre-event, first smoke, root connection, and initial
   rise. Advance a stateful solve sequentially. Bracket low/medium/high candidates while changing one mechanism
   group at a time.
3. **Full Data candidate:** without relying on material gain, validate source sampling, root, centroid, plume
   top, width, lateral drift, total-density proxy, six boundaries, and aftermath. Data failure cannot be hidden
   with Noise.
4. **Cache-path short test:** test uncertain Noise or cache modes in a short window with the same Domain,
   resolution ratio, format, and parameters as production. It proves only the technical path, not late smoke
   age, shape, or boundary acceptance.
5. **Noise candidate:** read the reopened final high-resolution grid and prove zero peak before impact, nonzero
   afterward, grid dimensions consistent with resolution, and safe margins. `use_noise`, baked state, operator
   `FINISHED`, file count, or file size is insufficient evidence.
6. **Production bake:** freeze a configuration hash, create a new candidate/cache ID, bake completely, and
   monitor process, logs, CPU, memory, disk, cache growth, completion markers, and savestates. Never reuse a
   cache after a physics or cache-mode change.
7. **Fresh-process reopen:** after process exit and complete markers, reopen the same `.blend` and cache,
   sequentially read actual grids, and render the hero camera plus at least two full-Domain axial diagnostics.
   Do not promote while bake, monitor, or required render remains active.

A cache may report baked/`FINISHED` while the final high-resolution grid is empty or zero. Short-test the
selected cache mode and format against the current Blender version and read actual content back; never turn
one cache mode into a cross-version prescription.

## 5. Reopened Voxel Boundary Probe

Run across sequential frames in the active gas interval, not only the final frame or hero camera:

1. Update frame and dependency graph. From the evaluated Domain object's Fluid modifier settings read
   `density_grid`, `flame_grid` when needed, `domain_resolution`, and cell/voxel scale. A raw object may return
   an empty grid or `(0,0,0)` in some background sessions. If smoke is expected but the grid cannot be read,
   report `UNVERIFIED`; never interpret it as zero smoke.
2. Verify `len(grid) == res_x * res_y * res_z`. Define significant density with predeclared absolute and
   relative-to-peak thresholds and record sensitivity. Strict `> 0` overcounts numerical tails; an excessive
   threshold misses thin smoke before deletion.
3. Compute significant-density index AABB, peak, total-density proxy `sum(density) * voxel_volume`, centroid,
   and occupied volume. Compute clearance to all six faces in layers and physical units. Transform voxel
   centers with the Domain world matrix for world coordinates; never assume zero rotation or unit scale.
   Mantaflow's `cell_size` is in the Domain's local units: multiply it by the Domain object's scale for world
   metres (`gas.grid_stats` reports `cell_size_m` this way).
4. For the outer face and inward guard layers on all six sides, record maximum density, density sum,
   significant-voxel count, and fraction of total density. Derive guard depth from voxel size, velocity, time
   step, and the prior physical thickness rather than hard-coding one layer or one integer. The outermost
   layer is Mantaflow's boundary layer and always reads zero, also where gas leaves through an open face, so
   it proves nothing: measure the guard share in layers 1..`guard_layers` inward from it (`gas.density` and
   `gas.grid_stats` require `guard_layers >= 1`, report each face in `guard_fraction_by_face` and the depth in
   `guard_depth_m`). With Noise upres the guard layers are noise cells.
5. Record six boundary modes such as `use_collision_border_*`. Open boundaries permit exit but do not replace
   clearance; accumulation, flattening, or reflection at a closed face also means the Domain is inadequate.
   Main smoke during the observation window must not rely on deletion or reflection to form its shape.
6. Rising shell density followed by an unexplained total/side-density drop at an open face indicates deletion.
   Accumulation or reflection at a closed face fails the boundary gate. With Noise, validate Data margins at
   minimum and repeat against the final OpenVDB/high-resolution grid when readable.

Minimum report fields: candidate/cache ID, Blender build, frame, grid resolution, voxel size, absolute/relative
thresholds, significant index/world AABB, six-face clearance in layers and meters, six shell metrics, peak,
total-density proxy, centroid, boundary modes, Data/Noise layer, cache mode, and `PASS / FAIL / UNVERIFIED`.

## 6. Distinguish Three Kinds Of Disappearance

| Observation | Required evidence | Conclusion and direction |
| --- | --- | --- |
| Solver deletion/boundary influence | AABB nears a face, shell density rises, then total or side density drops without cause; a closed face may accumulate/reflect | Enlarge Domain or change path/boundary and rebake; never hide with transparency/crop |
| Material/exposure invisibility | Voxel density, centroid, and AABB remain continuous while normal-material render weakens | Compare neutral diagnostic and final material at fixed exposure before changing physics |
| Camera crop/occlusion | World density exists and is clear of Domain, but AABB/samples leave frustum or are occluded | Compare full-Domain diagnostic with hero camera; composition is not solver evidence |

A diagnostic pass may show Domain wireframe, guard shell, or orthogonal slices from at least two axes, but
these helpers stay out of final images. Visual diagnostics find box shapes, flat cuts, and crops; voxel probes
prove clearance. Neither substitutes for the other.

## 7. Physics, Visual, And Event-Handoff Gates

Validate at minimum:

- no pre-event source result, smoke, fire, scorch, or breach; a whole/damaged model handoff has continuous
  world pose, silhouette, and visibility, with no precut hole, patch, or material difference revealing the event;
- first smoke location, frame, and direction follow the cause; root stays connected, old smoke rises and
  widens, local input creates gradual asymmetry, and late dilution/aftermath has a cause instead of sudden loss;
- Flow, Domain, Wind, collision proxies, and diagnostics are hidden in final viewport and render while the
  reopened baked volume remains visible;
- Gas and Bullet caches respond and reopen completely; active rigid bodies have no forged transform keys,
  Gas/Bullet force collections are isolated, and all visible bodies still pass parent every-frame penetration;
- smoke reads as continuous volume with buoyancy, entrainment, and absorption/scatter, not gray solid spheres,
  repeated beads, a fixed box, or material-only noise;
- the hero camera retains scale and event context but does not prove boundary safety; composition, material,
  solver, and cache problems remain separate diagnoses.

Any of these is a hard failure: pre-event result; smoke/fire without cause; main smoke deleted/reflected at a
Domain face and hidden by material/camera; an expected evaluated Data/Noise grid is empty, zero, or unreadable
but claimed as passing; leaked Domain/source/control geometry; animated blobs posing as simulation; operator
success without continuous probe and representative frames; changed physics with reused cache; Wind/Blast
driving out-of-contract objects; or any visible/rigid object penetration or unexplained handoff jump.

Each hard check is only `PASS / FAIL / UNVERIFIED`. A confirmed hard failure fails the candidate. An
`UNVERIFIED` hard check also blocks promotion and is recorded under the parent skill as limited/failed or
blocked, never washed into `PASS`. Approximation disclosure applies only after boundary safety is directly
proved, for non-hard uncertainties such as turbulence spectrum or unmeasured material values. It creates no
additional promotion state and does not relax the parent skill's acceptance result.

## 8. Symptom-Driven Diagnosis

| Symptom | Check first | Do not start with |
| --- | --- | --- |
| Beads or periodic dark nodes | Overlapping sources, moving-source sampling, source thickness, voxels, subframes/time steps | Raising material density only |
| Expands in place without rise | Temperature, initial velocity, buoyancy direction, upward Wind, top clearance | Color or "viscosity" explanation |
| Column too narrow or straight | Source width, sustained supply, Wind range/strength, root connection | Increasing the same narrow source |
| Sudden late disappearance | Six boundaries, Dissolve/age, cache content, material, camera | Raising material gain directly |
| Fades at a fixed height | Vertical profile, old-smoke dilution, sky contrast, Domain top, frustum | Assuming Domain contact from image fade |
| Noise baked but empty | Reopened evaluated high-res grid length/peak, cache mode, anomalous file size | Trusting `FINISHED` or file count |
| Coarse or broken after enlargement | Old/new voxel scale, resolution ratio, thinnest-source coverage | Enlarging Domain alone |
| Unrelated objects drift together | Collections/solver weights and force-field on/off differential | Merely lowering Wind and accepting pollution |
| White blob, black box, or line | Emitter/Domain/proxy visibility, coplanar geometry, exposure | Darkening the entire image |

For each iteration, name the visual anchor and failing frame, verify current-version RNA semantics, locate the
geometry/generation/transport/boundary/cache/material/camera layer, change one mechanism group, bracket
low/medium/high candidates, and test both intended effect and counterexamples.

## 9. Cache Invalidation, Presentation, And Delivery

Changes to Domain bounds/resolution/adaptive/boundaries, time scale/CFL, Flow/Effector/Force Field,
source frames/geometry/velocity, density/fuel/temperature, vorticity/buoyancy, Dissolve, Data/Noise settings,
cache mode/format, or physical time range require a new cache and rebake. Camera, lights, fixed exposure,
volume shader, samples, and compositor may reuse only after proving unchanged physics hash, cache path,
timestamps, and content.

The reopened `.blend` must directly show the final baked volume at representative frames in an appropriate
viewport mode. Audit `hide_render`, `hide_viewport`/`hide_set`, collection exclusion, and `visible_get()` so
hiding helpers does not hide solver output. Preview establishment, pre-event, first smoke/contact, handoff,
peak, formed column, sustained transport, dilution/aftermath, and final state, with adjacent frames around fast
events. Generate final media only when requested under the parent skill and never use editing, exposure, or
crop to hide failure.

For cross-machine scrub/render, deliver a relative cache path, Blender build, configuration hash, cache
mode/format, frame range, file/logical byte counts, reopen command, and limitations. A `.blend` without its
required external Gas cache cannot reproduce a baked result. Whether that cache, previews, or video belong
in default delivery still follows the parent skill's minimal-delivery rules.
