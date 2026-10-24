# Universal Object And Zero-Penetration Contract

This reference defines the shared contract for every Blender simulation. It applies before any solver-specific playbook. Cloth, rigid body, fluid, particle, soft-body, and coupled workflows may add requirements, but none may weaken this contract.

## Hard rule

There must be no interpenetration at any sequential frame:

- simulated object versus simulated object;
- simulated object versus visible scene geometry, terrain, supports, or reachable boundaries;
- solver surface versus collider;
- recovered/high-resolution visible surface versus any object or scene boundary;
- self-intersection when the material/solver exposes a self-collision claim.

Any positive geometric penetration depth is a failed candidate. A measurement epsilon is permitted only to classify coincident contact and floating-point noise; it must be listed separately and may not become a visual or physical penetration allowance. If the requirement cannot be met with the chosen solver and resolution, mark the case `failed` or `limited` and explain why. Assets may not acquire air walls or rectangular stop faces from AABB/OBB proxies. Every asset must also have a traceable, measured support chain to ground, a foundation, a load-bearing member, a valid parent/support body, or a physically justified cantilever/suspension point. Unsupported floating is a hard failure; hidden braces, catch floors, air walls, and camera crops are not explanations.

## General contract: one entry per simulated object

Create a manifest entry before building each object whose motion, deformation, emission, or contact is part of the claim.

| Field | Required decision |
| --- | --- |
| Identity | Stable name, role, collection, source/visible mesh, simulation mesh, and documented proxy |
| Solver ownership | One solver that owns the claimed output; inputs may be forces, emitters, pins, constraints, motors, or kinematic boundaries |
| Scale | Metric dimensions, applied scale/rotation, origin/parent, characteristic thickness and smallest contact feature |
| Material | Material class, mass/density or volumetric relationship, stiffness/damping/viscosity parameters where applicable |
| Collision | Collision shape, thickness/margin, collision mask/collection, self-collision policy, and every reachable scene boundary |
| Initial state | Support chain, contacts, separation gaps, velocity, deformation/rest state, and any pre-roll |
| Outputs | Solver-owned channels, cache path/range, and metrics for event, contact, bounds, and final state |
| Acceptance | Exact predicates, including a zero-interpenetration scan over the complete frame range |

Do not treat the presence of a physics modifier, a cache flag, or a successful operator as proof of any field above.

## Collision construction

1. Build visible scene boundaries before configuring motion. Apply transforms on participating objects and use a scale-appropriate collision representation.
2. Choose a shape that cannot miss the intended contact: analytic primitive for simple shapes, closed mesh/BVH for complex static surfaces, and a documented reduced surface only when it preserves the relevant contact topology.
3. Give thin surfaces a real, scale-appropriate thickness. Tune collision margin, cloth thickness, soft-body thickness, and fluid effector thickness together with the smallest feature and substep size.
4. Add every required collision layer explicitly. A passive rigid body does not automatically collide with Cloth or Soft Body; a render-only mesh does not automatically collide with particles or fluids.
5. Remove initial overlap before baking. Do not rely on solver separation impulses to repair invalid starting geometry.
6. Avoid single-frame extreme velocities or forces. Reduce per-step displacement, extend impulses, and increase substeps/continuous collision settings when the displacement can exceed the thinnest collision feature.
7. Never hide a catch floor, air wall, enclosing shell, or scripted trajectory. A proxy is allowed only when documented in the object entry and included in the same penetration and visibility audit.
8. Run a recursive support-chain audit for every visible asset: measure the path to ground/structure, vertical gap, horizontal footprint, parent relation, and cantilever reason. `unsupported_floating_count` must be zero; the mere presence of a collider, AABB proximity, or an off-camera support does not pass.

## General workflow and gates

### Contract and topology

Map every requested phenomenon to an owner and write the object entries. Build a topology/interaction graph for supports, openings, collision paths, force sources, destinations, and coupling direction.

### Static audit

Before a bake, evaluate the scene at the initial frame and run:

- object-object overlap tests for all reachable pairs;
- object-scene and object-support tests;
- self-intersection tests for deformables that claim self-collision;
- finite-coordinate, applied-transform, topology, modifier-order, and collision-layer checks;
- support-chain audit to the declared foundation or boundary.

The initial penetration set must be empty.

### Continuous probe

Advance frames sequentially through pre-event, first contact, peak interaction, and early settling. On every frame, record object bounds, finite coordinates, velocity/displacement, active counts, and penetration results. A probe with one penetrating frame fails even if the full bake later appears visually acceptable.

### Bake and reopen

Bake each candidate in an isolated cache path. Close Blender, reopen the exact candidate in a fresh process, and run the same sequential scan on evaluated solver surfaces and final visible surfaces. Validate the complete range, not only milestone frames.

### Promotion

Promote only a candidate with zero measured penetration, complete caches, valid object contracts, and no hidden prohibited helper. Keep failed candidates under `.work/` with the measured first frame, pair, region, depth, and causal revision.

## Detection guidance

- Plane, box, sphere, capsule, and other simple boundaries: use analytic signed-distance or interval tests.
- Closed meshes and terrain: use evaluated world-space BVH, nearest-point, ray parity, and local thickness checks as appropriate.
- Cloth and deformable surfaces: test the evaluated solver mesh and then the visible post-transfer mesh; include self-collision pairs when claimed.
- Volumes and particles: test particle radius/volume against scene boundaries and report unresolved sub-grid contact as a limitation instead of calling it zero penetration.
- Coupled solvers: validate each interface in both representations and preserve the declared direction of state transfer.

Use conservative pair broad-phase culling only to reduce work; every candidate pair that may touch must reach a narrow-phase test. An AABB overlap is a contact candidate or warning, not measured penetration: valid touching objects commonly have overlapping AABBs. Do not make an AABB-only warning a pass/fail gate. Classify contact versus penetration with the appropriate narrow-phase signed-depth or geometric test, then report the first failing frame, object names, boundary/region, measured depth, and whether the failure is solver-surface or visible-surface.

Report each asset's `support_chain_status`, support source, gap, and `unsupported_floating_count`, plus
`bbox_proxy_count`, `untracked_collider_count`, and `asset_air_wall_count` for every reachable pair. A real
asset clearance needs scene/structural semantics; a gap caused by reduced collision, broken parenting, or a
bounding box is an air wall and fails. Counts must come from the actual narrow-phase audit, not default zeros.

## Specialized layer

After the general contract passes, read the relevant playbook and add class-specific obligations:

- Cloth: pinned groups, surface resolution, mass, bending/stretching, air coupling, thickness, collider layers, and self-collision.
- Rigid bodies: finite masses, shape/margin, substeps/iterations, contact restitution/friction, support chain, and continuous collision for fast bodies.
- Fluids/gas: domain coverage, source/effector thickness, cell scale, CFL/adaptive steps, closed boundaries, and volume/escape checks.
- Particles/granular approximations: particle radius, live counts, collision mode, spacing, emission/settling, and disclosed approximation limits.
- Soft bodies: rest topology, binding, springs, damping, goal policy, closed or supported surface, and recovery/transfer checks.

These requirements explain *how* a category should be solved; they do not replace the per-object contract or the zero-penetration gate.

## Failure and retry

When a scan fails, change one causal group at a time: starting geometry, collider representation/thickness, transforms/scale, temporal resolution, solver stiffness/damping, force/source input, or coupling order. Re-run the shortest probe that can falsify the proposed fix before a full bake. Do not fix a penetration by keying the claimed output, adding an undocumented helper, hiding the affected frame, or changing only the render.
