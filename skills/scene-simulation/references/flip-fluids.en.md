# FLIP Fluids Liquid Workflow

Use this reference for free-surface liquid solved with the FLIP Fluids add-on. It adopts a staged
realistic-liquid workflow while retaining the parent skill's general simulation process, per-object
contracts, story coverage, isolated candidate caches, fresh-process reopen validation, and hard
zero-penetration gate. The parent skill and general contracts take precedence in a conflict. This
reference does not change the Mantaflow gas workflow or the built-in particle `FLUID/DDR` granular
approximation, and neither may silently substitute for FLIP.

Read [flip-scenario-baselines.en.md](flip-scenario-baselines.en.md) and select the nearest scene type.
Every number there is a cross-scene starting candidate and remains unaccepted until the current task passes
the corresponding gates.

## 1. Solve Class And Shot Contract

Choose one primary solve class instead of applying defaults across scales:

| Class | Typical scenes | Initial physics strategy |
| --- | --- | --- |
| Gravity-dominated open water | Beaches, waterfalls, wakes, rivers, storm runoff, floods, tidal structures | Use real scale and gravity; initially disable viscosity, surface tension, and Sheeting |
| Enclosed-water impact | A person entering a pool, vessel impact | Use real scale and gravity; verify obstacle timing and volume before whitewater |
| Small clear splash | Product water ribbons, bottle splashes | Lock product and camera, then fit the Domain; test surface tension and Sheeting only after the path works |
| Viscous pour | Slime, syrup, mud-like bulk liquid | Run a scale-anchored logarithmic viscosity scan and monitor solver stability |

Before opening Blender, write these fields into the manifest and `story_contract`:

- the primary action and frame intervals for establishment, source start, first contact, peak,
  transport, drainage/retreat, and late state;
- real dimensions, FPS, shot duration, delivery resolution, and the minimum readable duration of
  every story beat;
- initial volume, every Fluid/Inflow/Outflow, expected volume trend, open boundaries, and permitted
  final liquid regions;
- camera-position class, lens range, nearest point, visible context, and nine semantic review moments;
- ownership of each visible effect by FLIP, Ocean, particles, geometry, or material, plus any absent
  two-way coupling;
- time, disk, memory, and particle limits for probe, medium, and production bakes, and the approval
  condition for expensive stages.

For ships and open channels, record Froude state or at least velocity, characteristic length, and
water depth. Never accelerate physics, omit story beats, or use editing to force an incomplete solve
into a default short clip.

## 2. Runtime And Configuration Identity

Treat Blender, the FLIP Fluids add-on, license state, resolved profile, and cache as one reproducible
identity.

- A watermark-free result requires a matching installed and licensed runtime. The add-on is not
  bundled: the user installs their own 1.8.8 copy, whose installer must match the plugin's
  `scripts/FLIP/SHA256SUMS.txt`. Never copy the installer into a case, Git, or a delivery bundle.
  If no matching runtime is installed, mark the affected FLIP requirements `blocked`. Never silently
  fall back to Mantaflow or a demo runtime, and do not fetch or reference project code outside this skill.
- Record Blender version/build, FLIP Fluids version/release, license state, profile name, source-profile
  hash, resolved-profile hash, frame range, FPS, unit scale, cache path, and hardware/thread settings.
- Keep the purchased installer, extracted runtime, and license data out of generated cases, Git, and
  delivery bundles. Record only the version and checksum.
- Reject runtime/profile mismatches before build, bake, and render. Put every controllable physics
  value in a versioned profile and read actual RNA values back after saving and reopening; requested
  values alone are not evidence.

The following FLIP Fluids 1.8.8 entries have been inspected. They are version evidence, not a claim
of cross-version compatibility. Check existence, write success, and read-back equality and record
limitations.

| Concern | Property group or entry |
| --- | --- |
| Object role | `object.flip_fluid.object_type` |
| Grid/timeline | `domain.flip_fluid.domain.simulation` |
| Cache | `domain.flip_fluid.domain.cache.cache_directory` |
| Surface mesh | `domain.flip_fluid.domain.surface` |
| World/liquid | `domain.flip_fluid.domain.world` |
| Whitewater | `domain.flip_fluid.domain.whitewater` |
| Advanced/CFL/threads | `domain.flip_fluid.domain.advanced` |
| Command-line bake | `bpy.ops.flip_fluid_operators.bake_fluid_simulation_cmd()` |

A command-line bake requires a saved file, the active scene containing exactly one Domain, and a
writable isolated cache directory.

## 3. Scene, Resolution, And Geometry

1. Use coherent real-world metric scale, normally `1 BU = 1 m`. Apply role-required transforms and
   compute bounds from evaluated world-space geometry.
2. Lock a geometry-only camera previs before sizing the Domain. Proxy liquid, action paths, collision
   proxies, control objects, context, and nine review moments must pass first. The Domain serves the
   accepted shot; the camera must not later be used to conceal it.
3. Separate high-detail render meshes, closed-manifold solver meshes, numeric control objects, and
   render-only context. Record their relationship in object contracts. Solver collision boundaries
   default to the authored/evaluated object mesh; a reduced mesh is allowed only after a narrow-phase
   proof that contact topology, thickness, and closest-point error remain equivalent. Large AABB/OBB
   shells, enclosing boxes, catch floors, air walls, and undocumented proxies are prohibited; an AABB
   is broad-phase bookkeeping only and can never be the FLIP effector or asset boundary. Collision
   surfaces require consistent normals, continuity, and real thickness.
4. Estimate before approving geometry:

   `cell_size ~= max(domain_dimensions) / resolution`

   Record cells across the narrowest important dimension. Every source, opening, channel, water depth,
   collision wall, and shot-critical obstacle feature must span multiple cells; no participating volume
   may be smaller than one cell, and primary openings and animated obstacles should span three to four
   cells. A sub-cell source is a failed probe even if the bake completes; never compensate with extreme
   velocity.
5. The Domain is one axis-aligned, unrotated rectangular mesh with no modifier and no animated location,
   rotation, or scale. Fit it around every reachable liquid and whitewater path, declaring top, bottom,
   lateral, and downstream Outflow margins. Empty Domain volume wastes spatial resolution.
6. Continuous bathymetry, channels, pool walls, caves, bridges, and Outflows need resolvable thickness.
   Remove tiny grooves and decorations that the grid cannot resolve but that can trap particles. Keep
   initial Fluid objects and Inflows completely inside the Domain within reconstruction tolerance.
7. Define a volume budget. Use Fluid for finite initial water and Inflow for sustained supply; place an
   Inflow at a meaningful upstream/boundary location with velocity aligned to a reachable passage. Use
   a volumetric Outflow only for intended drainage and account for removed volume. Give preroll enough
   time to establish a pool, current, or wave train.
8. Give moving Obstacles and pushers startup clearance and sufficient subframe motion resolution to
   avoid initial penetration and tunneling. Hide and label pushers, wave makers, guide curves, catch
   regions, and context-water controls; never present them as physical scene elements.
9. Use FLIP for camera-near interaction and Ocean/procedural water only as distant render context.
   Match water level, normals, IOR, absorption, scatter, and roughness at the seam. The systems must not
   collide twice in the same volume or be described as one solve.
10. Pin each external asset's ID, version, source, license, and checksum. Pack textures when delivery
    requires it; signed URLs, credentials, commercial plug-ins, caches, and full image sequences do not
    belong in Git.

Do not voxelize large irrelevant terrain for distant assets. Crop simulation terrain, fit the Domain,
or split the scene. Context outside the Domain must be named, non-colliding, and never described as
simulated liquid. Recursively trace every visible asset to ground, foundation, load-bearing member,
valid parent, or a physically justified cantilever/suspension point; `unsupported_floating_count` must
be zero. Real asset clearances need a door gap, expansion joint, structural setback, or other scene
semantics. A rectangular stop face or invisible spacing introduced by reduced collision is an
`asset_air_wall_count` failure.

## 4. FLIP Roles And Animation Export

Extend the general per-object contracts with:

- exactly one `TYPE_DOMAIN` per `.blend`, including metric bounds and safety margins; independently
  build and bake separate liquid systems unless they must interact in one shared Domain;
- visible/simulation mesh, role lifetime, Enabled frames, initial state, and animation/export policy for
  each `TYPE_FLUID`, `TYPE_INFLOW`, `TYPE_OUTFLOW`, `TYPE_OBSTACLE`, and `TYPE_FORCE_FIELD`;
- FLIP ownership of liquid-surface and whitewater output, with keyed pushers, Inflows, Obstacles, Force
  Fields, cameras, lights, and materials declared as inputs;
- resolution, cell size, critical cell coverage, world scale, gravity, velocity transfer, CFL, surface,
  viscosity, surface tension, sheeting, whitewater, and particle budget;
- initial/permitted final regions, inflow/outflow volume, obstacle contacts, Domain clearance, and
  every-frame zero-penetration predicates.

Apply required transforms and repair normals before export. Validate manifold geometry and Domain
containment with a script or report. Keep `Export Animated Mesh` off for ordinary location, rotation,
and scale keyframes. Enable it only when evaluated mesh vertices change each frame, such as armature,
shape-key, parent-dependent, or evaluated-mesh deformation. Export topology must remain constant.
For product splashes use FLIP Fluids Curve Guide/Force Field; do not substitute Blender's built-in
Force Field and claim equivalent behavior.

## 5. Establish Base Flow First

1. Begin with Whitewater off. R64-R96 can probe motion and timing quickly. Surface Subdivision `0`
   can judge motion only, not holes, leaks, watertightness, sheets, or final surface quality. Test surface
   integrity at Subdivision `1` or higher and commonly move to R160-R220 for effects development.
   Production resolution follows cell coverage, convergence, and measured images; R280-R320 is a common
   target, not a universal requirement. Low resolution proves timing only; it cannot prove a smooth
   surface or absence of giant chunks. Production resolution must put multiple cells across the
   narrowest flow, obstacle gap, sheet thickness, and wave height, then inspect the evaluated liquid
   surface at native output size.
2. If no liquid appears, check role, Enabled frames, cell coverage, manifold state, initial overlap,
   velocity direction, and Domain coverage in that order. If volume rises or disappears continuously,
   inspect sources, drains, surface, and residual volume per frame. Fix drainage and geometry before
   materials.
3. Start large natural water with viscosity, surface tension, and Sheeting off. Test Sheeting as one
   variable only when a physically expected thin curtain or crest tears too early.
4. Prove the path of a small clear splash with surface tension and Sheeting off, then compare controlled
   one-variable candidates at medium resolution under fixed camera and lighting.
5. For a viscous liquid, use real dimensions and one short R64 test to define artistic baseline `V`.
   FLIP Fluids viscosity depends on scene scale and is not a real-world physical unit. Freeze other
   inputs and compare `V/10`, `V`, and `10V`. Record Simulation Method, Viscosity, PIC/FLIP Ratio,
   minimum/maximum Substeps, iteration limit, Solver Stress/Error, shape retention, and impact timing.
6. FLIP and APIC are distinct Simulation Method candidates. Under FLIP, raising PIC/FLIP Ratio is a
   separate damping candidate; do not describe PIC as a third independent method.

Give each candidate/profile isolated scene, cache, render, report paths, and a configuration hash.
Change one causal group at a time and run the shortest probe that can falsify it before another long bake.

### 5.1 Liquid Surface Quality And Giant-Chunk Hard Gate

Surface quality is a production acceptance item, not optional material polish. Every production candidate
must record `resolution`, critical-feature cell coverage, `mesh_generator`, `mesh_particle_radius`,
`mesh_smoothen_pos`, `mesh_smoothen_neg`, `mesh_scale`, the normal policy, and close/mid/wide reviews at
native output size. Any visible faceting, voxel stair-step, spike, layered break, source-shell inflation,
isolated giant liquid chunk, disconnected blob, or temporal pop fails; shader blur, reflection, fog, depth
of field, compositing, or camera occlusion may not hide it.

When a giant chunk appears, diagnose in this order and change one causal group per iteration:

1. Check resolution and `surface_distance` against the narrowest flow/obstacle feature; raise resolution
   and rerun the shortest continuous probe when coverage is insufficient.
2. Check the source's real volume, particle radius, and mesh particle radius for shell inflation; correct
   the source mesh/radii and rerun the probe.
3. Check meshing concavity, positive/negative smoothing iterations, and `mesh_scale`; preserve volume and
   contact topology while tuning.
4. Only then inspect velocity, timestep, obstacle topology, and material reflection. Any solver or
   collision-mesh change requires a new cache and full rebake; a material-only change still requires a
   native-resolution geometry review.

Promotion requires `fluid_surface_quality.pass`, `giant_chunk_count == 0`, `spike_count == 0`,
`bbox_proxy_count == 0`, `untracked_collider_count == 0`, `asset_air_wall_count == 0`, and
`unsupported_floating_count == 0`. Compute these fields with evidence for every frame/pair; a default zero
is not an audit.

## 6. Add Surface, Whitewater, And Material In Layers

- Enable Foam, Spray, and Bubble only after base flow passes. Keep Dust off unless the shot explicitly
  needs FLIP-driven particles. Use a separate particle or volume system for waterfall mist and visible
  raindrops, and declare system ownership.
- Tune generation thresholds from real velocity and scale. Define a particle budget before baking and
  monitor cache growth. Measure generation, lifespan, and transport separately in impact zones, openings,
  wakes, channels, caves, or shorelines instead of checking only peaks or rendered density.
- Inspect surface subdivisions, smoothing value/iterations, and particle scale at native output size.
  Never use excessive smoothing to hide penetration, volume, or reconstruction failure.
- Evaluate the unmodified `FF Water` preset first. Start IOR near `1.333`, then tune roughness,
  transmission, absorption, and scatter across shallow, deep, crest, foam, and wet-contact frames.
  Keep absorption and scatter independently controllable.
- Use one material family for FLIP and context water so shading does not create an artificial seam.
  Do not add render-only displacement to collision geometry after baking unless the visible/collision
  mismatch is explicitly accepted and recorded.

## 7. Stage Gates, Physical Validation, And Evidence

Pass these gates in increasing cost order. One attractive frame cannot authorize a long bake:

1. **Shot definition:** freeze story beats, real scale, initial candidates, camera range, measurable
   acceptance criteria, and resource limits.
2. **Previs:** without liquid, approve action timing, proxy geometry, controls, context coverage, and
   nine review moments.
3. **Structure:** validate runtime identity, roles, transforms, manifold state, cell thickness, Domain
   coverage, animation export, isolated cache, license, disk budget, profile hash, and initial zero penetration.
4. **Low-resolution physics:** with Whitewater off, finish an R64-R96 continuous probe through setup,
   first forcing/contact, peak, transport, and late state; require completion markers and expected payloads.
5. **Metrics:** freeze thresholds before viewing results. Measure volume balance, level, boundaries,
   velocity, arrival, contact, solver failures, and scene-specific sections. Never move a failed threshold;
   record an exception and classify the candidate limited/failed.
6. **Sparse render:** render 7-9 low-sample native-size frames and a contact sheet. Check readability,
   boundaries, empty World, crop, material seams, black edges, watermark, duplicates, and control geometry.
7. **Medium-resolution effects:** add only necessary Whitewater, Surface Tension, Sheeting, or Viscosity.
   Change one variable per candidate and compare fixed frames, camera, and lighting.
8. **Licensed runtime:** before an expensive watermark-free bake, complete a short low-resolution
   bake/render with the exact production runtime. Compare a known demo frame when available, record
   license metadata and cache provenance, and confirm no watermark geometry.
9. **Production:** freeze profile/hash into a new candidate and bake to `.work/cache/<candidate_id>/`.
   Keep periodic savestates for long bakes. Monitor liveness, CPU, memory, free disk, cache byte/file
   growth, completed-frame markers, log errors, and savestate freshness. Elapsed time alone is not progress.
   FLIP baking is normally CPU-dominated; reserve GPUs for rendering unless measurements show otherwise.
10. **Reopen and sequence:** after exit, require complete markers/payloads and sequentially validate the
    same candidate in a fresh process. Before encoding, audit every frame's number, decode, dimensions,
    uniqueness, blank/black edge, Domain, watermark, assets, and time continuity.

Every stage substep must have its own `status`, evidence path, first failing frame, and iteration count.
While any substep is `fail`, `unknown`, or missing evidence, run the loop “one causal change group ->
shortest continuous probe -> narrow-phase collision/support audit -> comparison frames -> report update”
until that substep is `pass`. Collision audits must use the effector/asset evaluated mesh; AABB/OBB is
broad-phase pairing only and never an acceptance boundary. Preserve a long story by increasing total
frames for transport, interaction, settling, and aftermath; never compress it with acceleration or frame
dropping.

A production candidate requires at minimum: complete contiguous story-range cache; finite vertices and
particles with no explosive bounds; contracted direction, speed, width, arrival, and contact; liquid and
whitewater inside the Domain except at declared open boundaries/Outflows; a volume budget covering initial,
inflow, outflow, and residual quantities; no unexplained surface/particle collapse or runaway growth;
required whitewater types within budget; and zero fluid/obstacle, fluid/terrain, visible-surface/boundary,
and documented-proxy penetration on every sequential frame. Startup exclusions may affect arrival or
baseline-volume metrics but never penetration.

For a finite wave also measure stable baseline, crest/breaking peaks, alongshore arrival spread, run-up,
obstacle contacts, late settling, volume change, and surface bounds. For a river or sustained Inflow also
measure discharge continuity and downstream progress. Classify a failure as geometry, physics, input/drainage,
boundary, surface, whitewater, material, camera, cache, render, or delivery, and fix the earliest failed layer.
A material cannot repair incorrect volume, and a camera cannot make a required offscreen collision happen.

Retain resolved profile/hash, build/probe/bake/validation reports, metrics, contact sheet, render audit,
sequence manifest, manually reviewed frames, and important delivery hashes. Do not promote or report
completion while a bake, render worker, monitor, encoder, or required transfer remains active.

## 8. Finite Boundaries And Camera

Classify a straight cutoff before fixing it: a finite surface edge, raised bathymetry/context, material seam,
compositor/output bar, and missing cache payload require different responses. Apply this order and record
whether each control is bake-time or render-time:

1. Move the camera closer, use a longer lens, reduce lateral offset, and target the true interaction rather
   than the Domain center.
2. Add continuous, physically explainable render-only shoreline, seabed, riverbank, pool edge, or distant
   water without contradicting the simulation.
3. Match FLIP/context material. After baking, `FF_GeometryNodesSurface` Remove/Flatten and Transition Mask
   may be used, but verify liquid, Whitewater, and Particle displacement together.
4. Use `Domain > Surface > Meshing Against Boundary` only when edge handling must be permanently generated
   into the cached surface. Set it before baking; changes to Removed Sides, Removal Distance, or other surface
   boundary settings require a new cache.
5. If every acceptable shot still exposes the finite surface, enlarge the Domain and rebake. Never cover a
   cutoff with an unexplained textured plane.

Begin with a slightly oblique three-quarter view. A flat front view hides depth, while an overly wide side
angle exposes the Domain. Use a low camera near the water to emphasize scale, speed, and whitewater. Prefer
a medium or long lens for finite simulations; use wide angle only when continuous physical/context geometry
can cover it. Leave room along flow and keep hero obstacles clear of frame edges. Move a camera only in
service of the event, and review early, peak, and late frames at the nearest and widest lens, every anchor,
and intermediate intervals. A safe first frame does not prove the whole path.

## 9. Cache Reuse And Rebake

| Change | Action |
| --- | --- |
| Camera transform/lens/animation | Reuse after proving unchanged physics config, resolved hash, and cache timestamps |
| Lighting, color management, compositor, samples, output size | Reuse |
| Water/terrain shader, packed textures, UV | Usually reuse |
| Named non-colliding context outside the Domain | Reuse after role/export audit |
| Post-bake `FF_GeometryNodesSurface` Remove/Flatten | Usually reuse; verify surface and whitewater/particle displacement together |
| Domain size/location/resolution, world scale, gravity, timeline beyond cache | Rebake |
| Initial Fluid, Inflow, Outflow, Obstacle, pusher, Force Field, or animation/export setting | Rebake |
| Collision bathymetry, Obstacle displacement, viscosity, surface tension, sheeting | Rebake |
| Whitewater generation, type, emission, lifespan, or budget | Rebake |
| Cached boundary meshing under `Domain > Surface` | Rebake |
| Missing/corrupt cache frame | Repair or resume first; rebake only when recovery fails |

If an object's or property's effect is unclear, inspect whether FLIP evaluates, exports, or generates it
during bake. If it does, treat the change as cache-invalidating. A render-only derivative records
`simulation_changed=false`, `rebake_performed=false`, the source cache, and unchanged resolved physics hash.

## 10. Render Audit And Portable Delivery

Render a native image sequence before encoding video. Parallel devices receive disjoint deterministic frame
sets, and physical device identities are verified before launch. Hide Domain, Fluid/Inflow, Outflow, pusher,
and Force Field controls in final renders while retaining reconstructed surface and whitewater. Encode only
an entirely audited sequence.

A `.blend` stores FLIP configuration and cache display objects, not the complete surface/whitewater payload.
When another machine must scrub or rerender: copy the accepted scene instead of editing the production source;
place the complete cache in the delivery area using a relative path such as `//cache/<cache-name>`; reopen and
resolve it with the required licensed runtime; verify frame range, markers/payloads, materials, packed images,
active camera, and unintended absolute paths; record runtime identity, hashes, file count, logical bytes, and
limitations without including a purchased installer or credentials.

For split cache delivery, use the plugin's `scripts/verify_split_archive.py` to verify every part SHA-256 and test the
concatenated stream when `zstd` is available. Verify before transfer and after download. Missing parts or a
browser progress bar do not prove a valid remote bundle.
