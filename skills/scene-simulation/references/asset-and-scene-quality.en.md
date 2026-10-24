# Asset Fidelity And Grand Scene Quality

This reference defines the general visual/structural quality contract. It applies alongside the universal object and zero-penetration contract. Visual richness may not hide physics failures, and a large scene may not introduce undocumented colliders or unsupported geometry.

## Asset fidelity contract

For every hero or physics-relevant asset, record:

- real-world category, intended scale, dimensions, proportions, silhouette, center of mass, articulation, supports, openings, and contact surfaces;
- visible mesh, simulation mesh, collision mesh/proxy, applied transforms, topology and material count;
- material class and scale-appropriate roughness, metallic response, transmission/opacity, thickness, seams, edge treatment, wear, and controlled variation;
- source/procedural generator, deterministic seed, approximation, and the camera distance/resolution at which detail must remain readable.

Use a three-level construction pass:

1. Primary form: correct silhouette, proportions, thickness, massing, and contact logic.
2. Secondary construction: joints, seams, fasteners, openings, supports, panel breaks, folds, and repeated systems that explain how the object is made.
3. Micro detail: restrained surface variation, bevels, wear, fibers, grain, or roughness breakup only when it survives the target camera and sampling.

Do not compensate for incorrect primary or secondary form with textures, noise, bloom, dark shading, or depth of field. Do not use a generic primitive as a hero asset unless the request explicitly allows a blockout.

## Grand scene contract

When the prompt asks for a grand, monumental, or expansive scene, define the target before modeling:

- metric world bounds and camera-visible depth/height;
- at least one hero structure or event, supporting structures, background masses, and a readable spatial hierarchy;
- multiple zones, levels, axes, or landmarks that communicate scale without relying on object count alone;
- macro massing/terrain, meso structural modules and repeated systems, and micro construction/material detail;
- a camera or set of cameras that reveals the hierarchy and preserves safe framing for the physics event.

Grand does not mean multiplying identical boxes. Repeated assets need stable, bounded variation in placement, scale, rotation, material, and construction while preserving the site's logic. Distant geometry must still be grounded or explicitly atmospheric; no floating backdrop may be mistaken for the physical scene.

## Acceptance metrics

Add measurable fields to the manifest and reports:

- asset dimensions/proportion ratios and the count of hero/support/background asset classes;
- scene bounds, occupied-area/volume ratios, height bands or zones, support-chain coverage, and camera-visible landmark count;
- primary/secondary/micro detail counts or representative topology/material statistics;
- material plausibility checks and a locked-camera readability check at target resolution;
- repeated-instance variation ranges and a count of unsupported, floating, duplicated, or untracked decorative objects.

Use thresholds appropriate to the prompt. A realistic asset can fail because it is visibly generic or dimensionally incoherent; a grand scene can fail because it lacks hierarchy, readable scale, real supports, or meaningful spatial variation even when the physics is stable.

## Hard Gate For Asset Contact, Support, And Air Walls

Assets may have real and intentional door gaps, expansion joints, structural setbacks, suspension
clearance, or other semantically explained space. They may not acquire invisible rectangular stop faces or
air walls from a solver, reduced collider, AABB/OBB, enclosing shell, or undocumented proxy. Collision
boundaries default to the asset's authored/evaluated mesh. An analytic primitive is allowed only when the
object itself is that primitive or equivalence to its silhouette, thickness, and contact features is
proven. An AABB is broad-phase bookkeeping only, never the collider or acceptance proof.

Every visible asset must have a traceable, measured recursive support chain ending at ground, a foundation,
a load-bearing member, a valid parent/support body, or a structurally evidenced cantilever/suspension
point. Record `support_chain_status`, `support_source`, vertical gap `vertical_gap_m`, horizontal
projection/contact area, parent relation, cantilever reason, and `unsupported_floating_count`. Unsupported
floating is a hard failure; the presence of a collider, AABB proximity, hidden brace, catch floor, air wall,
or camera crop is not an explanation. Any asset-to-asset gap without scene semantics is counted in
`asset_air_wall_count` and blocks promotion.

Repeat the support-chain and narrow-phase closest-point/BVH audit at static construction, every physics
probe, after baking, and after final reopen. Recompute recursively from an asset whenever it moves or its
topology changes. A nonzero count, missing evidence, or `unknown` status returns the work to the current
substep; it cannot advance to the next stage.

## Stage gates

### Contract

Write the asset fidelity and scene grandeur targets beside the physics contract. If the request is ambiguous, choose a conservative, camera-readable interpretation and record the assumption.

### Static construction

Build the macro scene and hero assets first, then secondary modules and micro detail. Keep collision boundaries and support logic explicit. Run proportion, scale, support, bounds, material, visibility, and repetition audits before a bake.

### Physics and presentation

Physics meshes may be reduced only when the contact topology remains faithful and the reduction is documented. Render-only derivatives may improve materials, lighting, camera, and detail, but must not change physics topology, transforms, solver settings, caches, or collision results.

### Final review

Review the reopened final scene at the locked camera and at a neutral diagnostic view. Confirm that the hero asset is recognizable, the requested scene scale reads immediately, construction details are not buried by lighting, and no decorative object floats, leaks through a boundary, or silently participates in physics.

### Hard realism gate for the simulated target

The object being simulated outranks background and decoration and must have its own `hero_physics_target` acceptance row. If primary form, proportions, thickness, mass distribution, support/contact surfaces, construction detail, semantic material, or forced motion is not realistic, return to modeling, material, or solver parameters and iterate. Do not hide target failure with a more complex background, higher resolution, glow, smoke, depth of field, editing, or motion blur; mark the scene complete only after every hard gate passes.

## Budget and approximation policy

If the requested scope exceeds the compute or frame budget, reduce hidden micro detail and distant repetition before reducing hero geometry, macro bounds, support topology, collision boundaries, or camera-readable scale. State what was reduced and why in `manifest.json`; do not silently replace a grand scene with a flat panorama or declare a blockout as final realism.

## General Semantic Texture Completion Mode

### Goal and priority order

When a request asks to complete the knowledge base or find the most complex, refined, semantically correct textures, the goal is reusable material knowledge, not a color pass for the current case. Select in this order:

1. **Semantic correctness**: identify the real material class, manufacturing process, environment, and camera distance first. A garment, concrete slab, galvanized steel rod, and curtain wall glass must not share one generic noise.
2. **Physical consistency**: align roughness, metallic response, transmission, thickness, fiber direction, and wear with the material.
3. **Readable multi-scale detail**: provide macro color variation, construction-scale detail, and micro normal/roughness variation. “Most complex” does not mean unscaled high-frequency noise.
4. **Camera readability**: weave, aggregate, seams, brushing, or grain must survive the locked camera and output resolution. Invisible detail must not consume solver or cache budget.

If the knowledge base has no exact match, complete the material record and generation rule first, then create a reproducible procedural node group or a sourced PBR set. Never use an unrelated “advanced texture” to hide incorrect geometry, thickness, seams, or collision behavior.

### Minimum fields for a texture entry

Record these fields for every reusable material and save them with the scene manifest:

```yaml
name: Denim Twill
semantic_class: textile.denim
real_world_scale_m: 0.001-0.01
surface_layers: [macro_color, twill_weave, fiber_micro, wear_variation]
maps: [base_color, roughness, normal, height, sheen]
physical_response: {metallic: 0.0, transmission: 0.0, sheen: 0.2, anisotropy: 0.1}
mapping: object_or_uv
tiling_m: 0.25
source_or_generator: deterministic_node_group
seed: 42
camera_readability: hero_or_midground
known_limits: no_fiber_level_geometry
```

Image textures must record source, license, resolution, color space, real coverage size, and tileability. Procedural materials must record node-group version, random seed, coordinate space, and every scale parameter. An entry without provenance, scale, and known limits is not complete knowledge-base data.

### Multi-scale construction of semantic materials

- **Macro layer (meters to decimeters)**: contamination, sun fading, color blocks, panel joints, and facade zoning. Feed this mainly into Base Color and broad Roughness.
- **Construction layer (millimeters to centimeters)**: brick joints, concrete aggregate, garment folds, towel loops, denim twill, and galvanized brushing. Feed this mainly into Normal/Height and directional Roughness.
- **Micro layer (microns to millimeters)**: fibers, grains, fine scratches, and pores. Use only restrained Bump, Sheen, or Roughness breakup; do not create floating geometry.

Use 2-4 spatially distinct Noise/Voronoi/Wave layers with explicit scale normalization, ColorRamp, and Map Range conversion to real-world meters. Do not reuse one node group by changing only Base Color; at minimum change direction, frequency, response channels, and physical parameters.

### Common semantic-to-texture mappings

| Semantic object | Detail that must read | Primary nodes/response |
| --- | --- | --- |
| Cotton T-shirt / knit | knit direction, soft fibers, high-frequency roughness breakup | Wave + Noise, higher Roughness, restrained Sheen |
| Denim trousers | diagonal twill, local fading, stiffer creases | Rotated Wave, Noise-driven color/Roughness, bounded Bump |
| Terry towel | looped pile, absorbent roughness, bound edges | Voronoi/high-frequency Noise, controlled Bump, high Roughness |
| Silk/satin skirt | elongated highlight, low roughness, slight anisotropy | Directional Wave, Anisotropy/Sheen, low Bump |
| Rooftop concrete | aggregate, pores, stains, contraction joints | Voronoi + Noise, high Roughness, no mirror highlight compensation |
| Galvanized/painted drying rod | brushing, coating granularity, edge wear | Directional Wave, separate Metallic and Roughness, subtle Bump |
| City brick/painted facade | brick joints or coating grain, rain marks, frame seams | Real-scale Height/Normal, macro stains, local Roughness |
| Curtain wall glass | low-amplitude reflection variation, seams, light dust | low Roughness, Coat/Transmission, never opaque pure blue |

### Blender implementation boundary

1. Build reusable Node Groups or a material library first; material instances should override only color, scale, and bounded variants.
2. With Object/Generated coordinates, explicitly handle object scale. With UVs, preserve real-world coverage size and do not hide seams by arbitrary random scaling.
3. Keep materials on the presentation side of `Cloth -> Solidify -> Subdivision` and similar solver chains. Material nodes must not change physical topology, mass, collision boundaries, or caches.
4. When real thickness, hems, cuffs, or brick joints are needed, add construction geometry first. Normal, displacement, and highlights only supplement correct primary and secondary form.
5. Tier texture complexity by camera distance: hero assets may retain fiber/aggregate frequencies, midground assets retain construction detail, and background assets retain macro color and silhouette. Do not run the whole scene at hero frequency.
6. Image and procedural textures may be combined, but the manifest must state color management, tiling, provenance, random seed, and render-engine compatibility.

### Texture acceptance gates

With the camera locked, check at minimum:

- the semantic category reads from the material itself, rather than from misleading lighting or labels;
- Base Color, Roughness, Metallic, Normal/Height, and Transmission/Sheen are plausible for that material;
- macro, construction, and micro layers are each explainable, with no repeated tiling, scale error, or unbounded noise;
- seams, UV stretch, normal direction, transparency sorting, and actual thickness are correct;
- physics caches, collisions, topology, and visible boundaries are identical before and after material enhancement;
- reopening the `.blend` preserves node groups, external texture paths, color spaces, and random seeds;
- the report distinguishes geometry detail, PBR maps, procedural nodes, and render-only decoration instead of presenting all of them as solver output.
