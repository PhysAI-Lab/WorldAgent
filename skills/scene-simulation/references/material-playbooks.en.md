# Special-Material Blender Playbooks (Layer 2)

Read only the sections relevant to the request. Historical values below indicate calibration direction or workload range, not portable presets. Recalibrate for scene scale, mesh resolution, timestep, and contact speed.

This is the specialized layer and does not replace the universal object contract. Every participating object must first declare identity, solver ownership, scale, mass/density, collision boundaries, initial state, and acceptance fields; these playbooks only add category-specific obligations. Regardless of category, object-object, object-scene, solver-surface/collider, and visible-surface/boundary pairs must have zero interpenetration on every frame (`contact.penetration` measures it). See `non-penetration-contract.en.md` for the shared audit rules.

## 1. Rigid Bodies, Impacts, And Structural Collapse

### Capability boundary

Use Bullet for gravity, collision, friction, damping, constraints, and failure of discrete bricks, chunks, projectiles, vehicles, racks, and structural modules. It is not a continuous bending or fracture-propagation model.

Solver-generated collapse means debris transforms are not hand-keyed. Legitimate keyed inputs can include motor enable, force strength, kinematic-state release, or a non-rendering contact driver.

### Geometry and structure

- Use closed, non-overlapping pieces. Prefer BOX for regular bricks/floor modules and SPHERE for cannonballs. Blender builds primitive shapes from the object's bounds around its origin and along local Z, so a primitive that does not match the mesh (for example a CYLINDER shape on a cylinder lying along X) adds an invisible collider or leaves parts uncovered (`rigid_setup.collision_shape_fit` measures it).
- If every brick must participate, assert every brick is ACTIVE and in the rigid-body world collection (`rigid_setup.bodies_in_world` measures it); do not retain a static wall substitute.
- Recursively audit supports. Bridges, arches, towers, and cantilevers need columns or lower courses that reach ground.
- Share mesh data to reduce memory, while preserving separate objects and solver state.
- Bullet uses each body's object origin as its center of mass. Keep the origin of every simulated body at its center of mass; if you apply location to one, set its origin back (`origin_set(type='ORIGIN_CENTER_OF_MASS')`). `worldagent_measure.rigid_setup.origin_at_center_of_mass` checks it.
- Create visual diversity through structure type, footprint, height, setbacks, roof/crown, facade, and irregular layout, not merely random box scaling.

### Mass and collision

- Derive stone, brick, and metal masses from evaluated volume and density. Report median brick mass, projectile mass, and their ratio.
- Use finite-mass dynamic projectiles rather than infinite-mass kinematic penetrators.
- High friction, low restitution, and moderate damping are reasonable masonry directions but require calibration.
- Dense stacks often need zero or very small additional collision margins to avoid a frame-1 separation explosion.
- Split Impulse reduces velocity injection from penetration correction but cannot repair serious initial overlap.
- Choose the collision shape from the topology the story needs. CONVEX_HULL fills holes and concavities, so rings, chain links, hooks, and handles collide as solid discs and cannot interlock (`rigid_setup.collision_shape_fit` measures how far a hull or primitive stands out of the visible mesh). Do not then disable their collisions and join them with constraints: that replaces the requested contact with joints. For bodies that must hook or interlock, try a COMPOUND shape built from convex pieces that keep the hole open; this is the usual Bullet approach but has not yet been validated in this plugin's test runs, so probe it first. MESH (GImpact) on interlocked concave bodies diverged within a few frames in a plugin test run (Blender 5.1.2), even at 100 substeps. If neither holds, report the case as limited rather than changing the physics.
- For complex library assets (luggage, crates, furniture), build the Bullet collider in this order and measure each step with `rigid_setup.collision_shape_fit`:
  1. a primitive (BOX, CAPSULE, CYLINDER) or CONVEX_HULL, when its protrusion out of the visible mesh is within the row's tolerance;
  2. otherwise a compound of a few primitives or convex hulls parented as children of the body (Bullet COMPOUND);
  3. otherwise a convex decomposition. Decomposition tools can crash or run for many minutes on detailed meshes, so try the simpler shapes first.

  Use MESH shapes only for PASSIVE bodies. Record the chosen representation and its measured fit in the object contract. A collider larger than the visible mesh makes bodies react before visible contact.

### Projectile launch and collision filtering

For solver-driven launches:

1. Create an ACTIVE/SPHERE projectile and hidden PASSIVE anchor.
2. Use a Bullet MOTOR constraint briefly to establish launch velocity.
3. Compute a ballistic solution from refreshed world-space muzzle and target coordinates.
4. Disable the motor; gravity, damping, and contact control the rest of the trajectory.
5. Give the anchor an empty collision mask.

When projectile-projectile interaction is explicitly excluded, assign unique collision channels per launcher; bricks and foundations listen to all projectile channels, while projectiles share none. Blender has a finite channel count. Beyond it, safely reuse channels only when same-channel shots cannot meet, or revise the interaction design.

### Constraints and release

- Breakable fixed constraints can model local connections, but dense large-scale networks may transmit penetration-correction energy through an entire building.
- Calibrate threshold direction and vertical/horizontal differences on a small assembly.
- If constraints cause systematic explosions, use local constraints, layered stacking, or keep structures kinematic until the declared contact solver step and release them under the causal rule below.
- Do not continue transform animation after release.
- Bullet only evaluates constraint empties that are in the collection assigned to `scene.rigidbody_world.constraints`. `bpy.ops.rigidbody.constraint_add` creates and assigns it; constraints built directly through the Python API can leave it unset, and then every constraint is ignored without an error (observed in a plugin test run, Blender 5.1.2). `worldagent_measure.rigid_setup.constraints_in_world` checks it.
- A POINT or HINGE constraint joins the two bodies at the empty's location. Place it where the bodies physically connect, and declare constraints in the spec only when the requested object really has a joint there.
- An animated creature can transfer impact through hidden kinematic foot/fist/tail colliders aligned with the visible rig; document them.
- When impact is claimed to cause release or collapse, keep the target stable before the declared contact trigger.
  Release may occur in the same solver step. If Bullet requires dynamic participation one solver step earlier,
  disclose that input approximation and prove zero pre-contact response plus contact within the declared
  temporal tolerance; an arbitrary earlier frame-based release is not impact-driven behavior.
- Record trigger/contact frame, release or activation frame, first target-response frame, and requested-result
  frame. Require causal ordering and treat target motion without the requested hit, topple, break, displacement,
  or other terminal result as semantic failure.

### Solve and validate

Increase substeps before iterations for fast contact. Prior large scenes used roughly 6-20 substeps and 14-60 iterations; choose new values from minimum thickness, speed, and probe data.

Damping is not friction. Linear and angular damping slow every motion as if the body moved through a thick fluid: high values stop a sliding body without its contacts doing the work and suppress pendulum swings. Treat damping as a calibrated, declared material property, not as a tool to make a deceleration or rest threshold pass. Constraint chains can keep a small residual jitter from the iterative solver; reduce it with more substeps or solver iterations and report what remains, rather than raising a rest threshold after seeing the result.

Do not toggle `point_cache.use_disk_cache` on the rigid body world cache in a background Blender process: it crashed Blender (access violation in `BKE_ptcache_toggle_disk_cache`) in a plugin test run (Blender 5.1.2). The default memory cache is baked with the scene and saved in the `.blend`.

Validate:

- no spontaneous pre-event movement;
- launch departure, motor shutdown, and collision masks;
- closest target distance, first-contact pair/point, trigger frame, activation/release frame, response onset,
  and requested-result frame per impact chain;
- displaced counts across independent regions and low/mid/high bands;
- counts above several displacement thresholds, maximum horizontal travel/drop, and scene safety bounds;
- zero transform keys on governed active bodies, excluding declared kinematic input (`rigid_setup.keyed_active_bodies` measures it);
- complete caches after reopening (`rigid_setup.cache_coverage` measures it).

A foreground wall deflecting a high shot is a valid physical outcome. Do not tunnel through it merely to hit a
planned target, but report the requested target event as unmet rather than promoting physical plausibility as
semantic completion.

## 2. Cloth, Garments, And Membranes

### Mesh, spatial resolution, and modifier order

- Cloth has no independent voxel-resolution control. Spatial resolution is the evaluated mesh entering the Cloth modifier. Subdivision, displacement, or normals after Cloth improve presentation but cannot support a high-resolution simulation claim.
- Create a `cloth_resolution_contract` for every cloth object. Record real dimensions/area, solver vertices/edges/faces, median and p95 rest-edge length, vertices per square meter, the smallest intended fold wavelength, solver edges per target fold, and projected solver-edge length in pixels at the closest shot. Keep requested, successfully assigned, and reopened actual values separate. (`deformable.cloth_setup` measures the solver mesh, modifier order, and Collision modifiers.)
- When the user explicitly requests very high or extremely fine cloth, lock a high-resolution gate before modeling: at least eight solver edges across the smallest target fold wavelength and a p95 projected solver edge no larger than two pixels in the closest delivered shot. Both must pass. Without a target camera, use the fold-wavelength gate and record the missing camera criterion in the manifest.
- Use a regular, quasi-uniform solver mesh that follows the real pattern silhouette. Reject long skinny triangles, duplicates, zero-area faces, initial self-intersection (`deformable.deformation` measures it), and abrupt local density changes. Report the edge-length distribution and worst cells, not only total face count.
- If Subdivision supplies solver density, use a fixed level before Cloth, name it `SolverSubdivision`, and record its level and evaluated topology entering Cloth; applying that topology to the mesh is also valid. View-dependent or frame-varying adaptive subdivision must not feed Cloth. Any solver-topology change requires a new candidate and cache.
- A typical stack is `Armature/Hook -> SolverSubdivision (optional) -> Cloth -> Solidify -> RenderSubdivision`. Fixed-topology Mask/Smooth modifiers may precede Cloth when documented.
- Numerically validate the base solver surface and then the final Solidify/RenderSubdivision surface. Penetration of either surface fails.

### Simulation clock

- Cloth ignores the scene FPS: one frame simulates `time_scale × 0.031623 s` (the Speed Multiplier; measured in Blender 5.1.1), whatever `scene.render.fps` is. For real-time motion set `time_scale = 31.62 × fps_base / fps` (1.3176 at 24 fps, 0.6588 at 48 fps). Soft Body behaves the same way (see §6).
- Do not compensate a wrong clock with extra gravity or force. Verify the clock with a measured free fall of a test object against `g t² / 2`.
- A cloth stays at its last cached state after the cache end frame, which can look like rest. Make the cache range cover the whole story before reading a final state.

### High-resolution solve and convergence

- Cloth `quality` controls quality steps per frame and `collision_quality` controls collision solve quality. They improve temporal/iterative resolution but cannot repair a coarse mesh; mesh density likewise cannot compensate for insufficient time or collision quality.
- For an explicitly extremely fine cloth task, start the high-fidelity candidate at `quality >= 20` and, when object or self-collision occurs, `collision_quality >= 10`. Strong wind, fast attachments, tight folding, or multilayer contact should start higher and escalate from the first failing frame. These numbers are starting gates, not substitutes for convergence evidence.
- Enable self-collision for hero cloth that can fold, wrinkle, conform, or contact other garments. Disable it only when topology and the complete motion range prove self-contact impossible, and record the reason. Calibrate object and self-collision distance against physical thickness, p95 edge length, and maximum per-step displacement.
- Before the full bake, create at least three independent candidates: the high-resolution baseline, a spatial refinement, and a temporal/collision refinement. Keep quality steps fixed in the spatial candidate while reducing target-region linear edge length to no more than 0.75 of baseline or increasing solver faces by at least 1.5x. Keep topology fixed in the temporal/collision candidate while increasing `quality` and contact-relevant `collision_quality` by at least 1.5x. Candidates must not share caches.
- Compare the spatial and temporal/collision refinements independently through stable UVs, same-source topology mapping, or recorded surface samples over preroll, first forcing, maximum fold/contact, and final state. Report median/p95 normalized surface displacement, hem or free-edge amplitude, peak velocity, contact/self-contact counts, maximum curvature or fold wavelength, and first penetration frame.
- The extremely fine tier passes only when both independent refinement comparisons change median normalized surface position by no more than 0.5% of characteristic object length, p95 by no more than 1%, free-edge amplitude and peak velocity by no more than 3% each, and every candidate has zero penetration. Otherwise refine the corresponding spatial topology or temporal/collision quality, changing one causal group at a time.
- Record solver vertices/faces, edge statistics, `quality`, `collision_quality`, self-collision state, distances, runtime, peak memory, and convergence deltas in `probe.json`, `bake.json`, and `validation.json`. Reopen the candidate and verify actual RNA values and evaluated topology.
- If compute or memory cannot meet the gate, mark the result `limited` or `blocked` and preserve the measured gap. Do not substitute post-Cloth subdivision, normals, displacement, motion blur, or selected attractive frames for high-resolution solving.

### Rendered visual-fidelity approval

- After numeric convergence and zero penetration pass but before promotion to `scene_final.blend`, close and reopen the same candidate and render cloth diagnostics from that candidate or a read-only render derivative with an identical physics fingerprint. Without rendered-pixel evidence, the cloth simulation must not be marked passed.
- Render two evidence sets at native delivery resolution: one with the final camera, materials, and lighting, and one neutral-light-gray diagnostic with no bump, normal, or displacement under a grazing area light. Disable depth of field and motion blur for the diagnostic. Fog, smoke, strong glow, dark exposure, or downscaling must not hide mesh roughness; use enough samples that noise does not obstruct review.
- Include the initial state, first visible forcing, maximum curvature or fold/contact, peak free-edge velocity, and final state, plus at least five consecutive native frames around the highest-risk moment. Provide unscaled 100% pixel crops from the closest hero shot covering silhouette, high-curvature folds, pin-weight transition, and critical collision/self-collision regions; use separate crops when one cannot cover all regions.
- At 100% native size, fail on any clearly visible polygonal silhouette stepping; folds made of straight segments, triangular/quad facets, or a regular grid; diagonal/weft-warp grid imprint; faceted highlights; jagged Solidify thickness, seams, or hems; hard pin-transition kinks; frame-to-frame facet popping, flicker, or spikes; or large cloth regions moving synchronously like rigid plates. Any obvious item fails.
- Wool, terry, or coarse linen may have intentionally rough material response when its scale is correct and temporally stable. If the final look is rough while the neutral diagnostic is smooth, fix material, normals, thickness, or sampling. If the neutral diagnostic is also rough, return to solver topology/temporal quality and create a new cache. Presentation fixes cannot turn an unconverged solve into a pass.
- `reports/validation.json` must contain `cloth_visual_fidelity`: candidate id, renderer, native dimensions, camera, reviewed frames and consecutive window, diagnostic/final render paths, 100% crop paths, motion-blur/DOF state, per-item results, first failing frame/region, and review conclusion. Promote cloth only when `cloth_visual_fidelity.passed == true` and numeric convergence, zero penetration, and final-visible-surface checks also pass.

### Moving boundaries and pins

- More fully pinned area is not automatically more stable; it can make cloth appear unsimulated.
- Hanging cloth may pin the first row and taper the second. Tight clothing may give all visible vertices nonzero weights below 1.0, allowing the armature to supply target shape while Cloth adds small inertial motion.
- For large attachment rotations, make the fully pinned top row an area-free connection edge. A pinned face can become a rigid plane the garment crosses.
- Keep attachment/neckline row depth monotonic so the first free row cannot invert through the pin row.
- Do not simply parent the whole cloth to a moving rigid body; Hook only the attachment vertices.

### Relative material behavior

- Silk/light scarf: low mass, low bending, lower air damping, high wind response.
- Linen/interlined fabric: higher mass and stretch/bend stiffness, higher damping, restrained motion.
- Cotton/pleated fabric: intermediate response; ensure initial pleats do not intersect.
- Tight activewear: high but subunit pin weights and measurable millimeter-scale solver deformation.

Blender Wind strength is not meters per second. Calibrate relative behavior through hem displacement, velocity, and stability.

### Collision and anti-penetration

- Environment, body, and moving rack surfaces need Collision modifiers; rigid-body status alone is insufficient (`deformable.cloth_setup` measures it).
- Tie collision distance to edge length, physical thickness, and speed. Prior meter-scale scenes used roughly 5-18 mm, but this is not a universal range.
- Self-collision is expensive, but performance is not a reason to disable folding or contact that the claim requires. Follow the self-collision, quality, convergence, and visual-fidelity gates above.
- Start adjacent garments with real separation.
- Check normals and two-sided behavior for glass and rails.
- Coarse body proxies can inflate tight garments. Prefer body masks, professional garment topology, and accurate local colliders; ensure masks do not expose the pelvis or torso gaps.

Suggested scan:

1. Disable render-only Solidify/Subdivision.
2. Per frame, test cloth against glass planes, floor height, rail cross-sections, moving-rack BVHs, garment-pair BVHs, and self-contact candidates.
3. Separate tiny adjacent-fold candidates at the clamp from visible cross-surface penetration.
4. Inspect multiple final rendered frames after thickness/subdivision.

### Character-driven cloth

- Armature deformation precedes Cloth. Root motion, pins, and collision proxies must share the same rig evaluation.
- Prefer pose-space retargeting between matching bones. Copying local Euler values across different bone rolls can cross arms and splay legs.
- Validate facing direction, root speed, support-foot sliding, step width/crossing, elbow flexion, arm swing, and hand separation across the full clip.
- Match root travel to support-foot velocity after orienting the character along the actual path.
- Bake the final retargeted action and remove temporary source rigs and constraints.

### Rigid-body/cloth coupling

- Represent a detailed tubular rack with a stable, simple Bullet collision core; parent visible detail to it.
- Exclude coarse bench/pot rigid proxies that overlap the core at release.
- A broad, high kinematic load plate can transfer a disclosed wind-like overturning moment over many frames, reducing tunneling.
- After release the rack is Bullet-driven; attachment Hooks follow it and free cloth continues solving.
- Validate rack position/tilt/lateral drift/final rest and continued nonzero cloth deformation.

## 3. FLIP Fluids Liquids

Read [flip-fluids.en.md](flip-fluids.en.md) for every free-surface liquid task.
It replaces the former Mantaflow-liquid workflow. Do not silently fall back to
Mantaflow when FLIP Fluids or a licensed watermark-free runtime is required.

Core obligations:

- record Blender/add-on/runtime identity and requested-versus-reopened RNA values;
- classify real-scale open water, enclosed impact, small splash, or viscous pour before freezing the shot, volume, and story contracts;
- use exactly one Domain and explicit Fluid, Inflow, Outflow, Obstacle, and Force
  Field roles as applicable;
- compute cell size from Domain dimensions and resolution, and prove that every
  important source, passage, water depth, and obstacle feature spans multiple cells;
- validate closed collision geometry (`solver_mesh.solver_mesh_validity` measures closedness and thickness in cells), Domain coverage, pusher/moving-obstacle
  clearance, continuous bathymetry, and zero initial overlap before baking;
- FLIP effectors and asset contacts default to the asset's authored/evaluated mesh. A reduced surface is
  allowed only after a narrow-phase proof of equivalent contact topology, thickness, and closest-point
  error. AABB/OBB shells, enclosing boxes, catch floors, air walls, and undocumented proxies are forbidden;
  an AABB is broad-phase only;
- prove motion and license state at low resolution before production, freeze
  simulation-affecting inputs, and isolate each candidate cache;
- Low resolution proves timing only, not a faceted, voxel-stepped, or giant-chunk-free liquid surface.
  Production resolution must cover the narrowest flow/obstacle gap/sheet/wave feature and record the mesh
  generator, particle/mesh radii, positive/negative smoothing, and native-size facet, spike,
  `giant_chunk_count`, and temporal-pop metrics;
- add Whitewater, Surface Tension, Sheeting, or Viscosity only as one-variable
  candidates after base flow passes;
- audit completion markers, cache payloads, bounds, arrivals, contacts,
  inflow/outflow volume balance, whitewater types/budget, support chains (`support.support_chain` measures them), asset-air-wall counts, and
  every-frame zero penetration after reopening;
- treat camera, lighting, and shader changes as render-only only after proving
  that exported physics inputs, the resolved physics hash, and cache are unchanged;
- deliver the external FLIP cache with a relative path when another machine must
  scrub or rerender it; a `.blend` alone is insufficient.

After the general FLIP guide, read
[flip-scenario-baselines.en.md](flip-scenario-baselines.en.md) and select the
nearest scene candidate. Every candidate requires validation in the current task
and cannot replace its acceptance thresholds.

## 4. Mantaflow Gas, Fire, And Explosion Effects

Read [mantaflow-gas.en.md](mantaflow-gas.en.md) for every Mantaflow Gas task. It governs smoke, fire,
thermal plumes, fog-like gas, and explosion columns while the parent story, zero-penetration, cache-isolation,
and delivery contracts remain active.

Core obligations:

- define the pre-event empty interval, first smoke, sustained supply, rise/widening, dilution, and aftermath,
  and disclose the Gas/Bullet approximation boundary;
- derive base voxel size from Domain and `resolution_max`, proving multiple voxels across the thinnest critical
  source axis and continuous sampling of moving sources;
- use staggered, offset, differently sized sources for short combustion, sustained smoke, and fire rather than
  one spherical source for every stage;
- separate smoke generation from transport, and isolate Gas Wind from Bullet blast force with collections and
  on/off differentials;
- prove stable Data, root continuity, centroid/top net rise, and six-face Domain clearance before Noise;
- never treat baked/`FINISHED`, file count, or an attractive hero camera as proof of cache content, valid Noise,
  or safe boundaries;
- after reopening, read evaluated density/flame grids sequentially and record significant AABB, six-face
  clearance/shells, total-density proxy, centroid, boundary modes, and `PASS / FAIL / UNVERIFIED` (`gas.density` measures total density and the share at the Domain walls);
- distinguish solver boundary deletion, material/exposure invisibility, and camera crop; unreadable hard checks
  block promotion;
- create and completely bake a new cache after any source, Domain, transport, Dissolve, Data/Noise, or cache-mode change.

Blender Gas solves density, fuel, temperature, buoyancy, vorticity, and flame while Bullet solves fragments.
They do not automatically form a validated detonation or two-way fluid-structure solve; never claim engineering
explosion accuracy.

## 5. Sand, Grains, And Granular Approximations

### State the solver boundary

Blender has no native DEM:

- Newton particles provide per-particle gravity, velocity, damping, and environment collision, but not a complete grain-contact network.
- `FLUID / DDR` SPH provides neighborhood density, repulsion, and viscosity, but no true contact forces, force chains, friction cone, or static grain friction.
- Bullet grains model contact but rarely scale to millions.

Do not describe SPH as engineering-grade dry sand.

### Representation choices

- Artistic sandcastle destruction: rigid chunks plus smaller grains/dust.
- Independent falling grains: Newton particles; limited pile realism.
- Dense built-in-only approximation: one DDR-SPH system.
- If the object must consist of particles from frame 1, pre-generate them on negative frames. Do not swap a visible shell for particles at release.
- Volume emitters are computational sampling geometry and must be hidden from rendering and rays.

### One coupled system and sampling

Do not split one dense body into multiple independent SPH systems. They do not share neighborhoods and may interpenetrate, conserving count while losing macroscopic occupied volume.

- Use one closed volume and one particle system.
- Prefer `VOLUME / GRID` for uniform initial spacing and density.
- Record target count and actual grid points inside the volume.
- Keep particle spacing, interaction radius, display radius, and collision thickness coherent.

### Release and rest

- Keep gravity, pressure/repulsion, and noise zero before release.
- Enable gravity at release and ramp pressure/stiffness over several frames to avoid a dense-system pressure explosion.
- Excess viscosity/damping creates slurry; insufficient values create a thin disk.
- SPH lacks static grain friction. If later damping ramps, disabled gravity/pressure, or an internal field creates visual rest, label it as an approximation and verify final inputs return to zero plus velocity stays settled.
- A force field is a disclosed solver input, not an invisible collision guide.

### Visible grains

- Randomize among several low-poly grain shapes, sizes, colors, and rotations to reduce Grid regularity and fluid appearance.
- Check rotational rest when using velocity alignment or dynamic rotation.
- Ensure visible grain half-height is below the minimum particle-center ground clearance.
- For Cycles, instance grains from baked positions through a point mesh/Geometry Nodes without changing trajectories.

### Validation beyond particle count

Combine:

- target/actual/live counts, solver/system count, and cache state;
- zero pre-release motion;
- mid-event moving/downward/lateral/fast-upward counts;
- P99 height/radius, maximum height, and numeric safety bounds;
- dense occupied volume from fixed voxels with a minimum particles-per-voxel threshold;
- retained-volume proxy, top/bottom radius, and slope taper;
- velocity P90/P99 across several final frames;
- particle-center and visible-instance ground penetration;
- absence of rigid world, visible shell, or undisclosed guide when prohibited.

Voxel occupancy depends on porosity and thresholds; never treat it alone as exact continuum volume.

## 6. Soft And Elastic Bodies With High-Resolution Recovery

### Choose a representation

| Method | Strength | Main risk |
| --- | --- | --- |
| Full source mesh directly as Soft Body | No proxy-fidelity dispute | Slow and unstable for thin/disconnected complex meshes |
| Closed reduced cage + Mesh/Surface Deform | Fast and stable | Loose cage causes stand-off and visible offset |
| Internal tetrahedral spring body | Shape-aware volume core | Complex construction; Blender Mesh stores no tetra-cell primitive |
| Simplified closed surface + explicit displacement recovery | Controllable and auditable | Requires manifold simulation mesh and rigorous transfer validation |

If the request requires an internal tetrahedralization, do not substitute a box, bounding volume, or loose primitive hull. If it requires the simplified surface itself to simulate, do not silently replace it with a volumetric cage.

### High-resolution invariants

- Preserve original visible vertex, edge, polygon, loop, UV, material, and image data.
- Put no Decimate, Remesh, Build, or topology-changing modifier on the visible object.
- Repair manifold defects only on the simulation copy and document them.
- Record original, visible, simulation, and rest topology in the manifest.

### Internal tetrahedralization

1. Clean/validate the source surface and build a BVH.
2. Sample nodes strictly inside the source shape.
3. Generate candidate tetrahedra with Delaunay.
4. Reject tetrahedra crossing outside using robust inside/BVH tests.
5. Require every spring edge to belong to a retained tetrahedron.
6. Extract triangles owned by one tetrahedron as the boundary surface.
7. Store four-index cell connectivity in JSON and a Blender Text block.
8. For thin/complex models, a closed boundary from validated internal nodes may support collision/binding, but retain all tetra cells and springs.

Apply matching scale and rotation to visible and tetra bodies before binding. Unapplied transforms produced multi-object severe penetration; open boundaries and unnatural upright thin-object orientation also failed independently.

### Simplified surface and explicit recovery

1. Copy the source to a reduced simulation surface and apply the reduction.
2. Enforce closed manifold topology; fix isolated boundary flaps only on this copy.
3. Copy a static rest surface from the reduced state.
4. Run Soft Body only on the dynamic reduced surface.
5. In Geometry Nodes compute `current_position - rest_position` explicitly on the correct point domain.
6. Sample displacement from the nearest rest triangle and add it to the untouched visible surface.
7. Verify the result is identical with auxiliary objects viewport/render hidden.

Explicit current-minus-rest displacement is easier to audit than broad, context-dependent deformation weights.

### Soft-body stability

- Soft Body ignores the scene FPS, like Cloth: one frame simulates `speed × 0.031623 s` (measured in Blender 5.1.1). For real time set `speed = 31.62 × fps_base / fps` (1.3176 at 24 fps, 0.6588 at 48 fps). Do not compensate with extra gravity; verify with a measured free fall.
- A new Soft Body modifier has Goal enabled, so the body hangs in place. Disable Goal for free fall; use structural edge springs for shape.
- A support that is only a PASSIVE rigid body is passed through: Soft Body collides only with objects that have a Collision modifier. Give every support one.
- A body freezes at its last cached state after the cache end frame, which can look like rest. Make the cache range cover the whole story before measuring a final state.
- Stronger shape retention comes from pull/push, shear/bend stiffness, damping, tighter automatic steps, and lower error tolerance.
- Self-collision can destabilize complex multipart surfaces. If disabled, prove cohesion with constant topology, connected-component metrics, edge-stretch limits, bounded growth, and internal cohesion edges.
- Connect decorative closed components to the main body with a small number of hidden structural edges when needed.
- Give thin assets a non-degenerate solver thickness/core without creating unacceptable render-surface offset.
- Change fall orientation or displayed scale before blindly increasing stiffness; an upright bed or aircraft is an unnecessarily hard contact case.

### Visible-world collision and no-air-wall policy

- Approve only visible, physically present, reachable deck, shell, trough, or frame surfaces.
- A visible environment surface may need both PASSIVE rigid-body and Collision roles.
- Use a small declared collision thickness: too large creates an air wall, too small tunnels. The boundary
  defaults to the actual evaluated mesh; AABB/OBB is candidate pairing only, never a stop face.
- Prohibit hidden catch floors, boxes, safety planes, and mismatched support geometry. Any proxy must record
  source, evaluated-mesh hash, thickness, and equivalence evidence.
- Run narrow-phase BVH/closest-point checks for walls, furniture, table legs, columns, paper, and every asset.
  Recursively prove each support chain to ground/load-bearing structure/valid parent/evidenced cantilever.
  Report `bbox_proxy_count`, `untracked_collider_count`, `asset_air_wall_count`, `support_chain_status`,
  and `unsupported_floating_count` (`support.support_chain` measures the chain and floating count); any nonzero or unknown result fails.

### No-fragmentation and no-penetration validation

Across start, descent, first impact, rebound, and final samples, check:

- all bodies descend and remain finite (`numerical.stability` measures finite coordinates and explosive growth);
- simulation/visible/rest topology stays constant;
- high-resolution and low-resolution motion/recovery agree;
- maximum edge stretch, bounds-diagonal growth, and component separation remain bounded (`deformable.deformation` measures edge stretch; `deformable.recovery` measures return to rest edge lengths);
- local deck ray/BVH clearance per asset;
- a reduced solver body only when equivalence evidence is recorded; the evaluated/visible surface is the
  authoritative boundary and relaxed tolerances may not create an air wall or hide floating;
- auxiliary viewport/render hiding and zero wire/in-front/all-edge flags;
- visible deformation still works while auxiliaries are hidden.

## 7. Cross-Material Coupling And Presentation

Write the coupling graph:

```text
animation/force/source input
        -> solver A
        -> moving boundary or event signal
        -> solver B
        -> render representation
```

State one-way vs two-way behavior and omitted reactions. For example, a cannon may be visual-only while a motor launches a finite-mass cannonball.

### Cache order

- Make driving animation/boundaries deterministic before dependent bakes.
- Validate Bullet motion before baking Cloth attached through Hooks.
- Preserve repeatable Armature/kinematic evaluation during Cloth or Soft Body bake.
- Gas and destruction that merely share event timing can cache independently; do not call this two-way coupling.

### Presentation boundary

Dust, flashes, smoke, glow, fog, impact lights, and audio can improve readability but must not hide physical failure. Keep them in separate collections or a render-only derivative and report that they do not alter baked trajectories.

## 8. Quick Selection Table

| Required phenomenon | Choose | Do not claim |
| --- | --- | --- |
| Brick-by-brick collapse | Bullet | Continuous fracture or structural-engineering accuracy |
| Body-driven folds and garment motion | Armature + Cloth | Cloth reaction on the character unless separately modeled |
| River over terrain | FLIP Fluids + Obstacle terrain | Features smaller than cells are resolved or automatic two-way FSI |
| Fireball and rising smoke | Mantaflow Gas | Validated detonation or automatic two-way FSI |
| Independent falling visual grains | Newton particles | Full contact network or true angle of repose |
| Million-grain built-in-only approximation | Single DDR-SPH system | DEM, force chains, true static friction |
| Strong elastic free-fall object | Reduced-surface Soft Body | FEM constitutive behavior |
| High-resolution elastic appearance | Reduced body + explicit transfer | Direct full-resolution simulation when it was not used |

## 9. Semantic Textures

For reusable, semantically correct material and texture work, see "General Semantic Texture Completion Mode" in `asset-and-scene-quality.en.md`.
