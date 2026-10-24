# Scene Construction Pipeline

Detailed Stage 1 substeps of the `scene-simulation` skill, moved here unchanged so the skill body stays short. Stage numbers in parentheses ("Scene Stage 6 Turn 1", "non-game Stage 7") refer to the WorldAgent scene pipeline, not to this skill's Stages 0–6.

## Stage 1 substeps

### 1A. Primordial terrain configuration (Scene Stage 6 Turn 1)

- Use the terrain pipe's coordinate mapping: terrain `x = scene x` (east/west), terrain `y = scene z` (north/south), terrain `z = scene y` (elevation). Map `world_span.width_m/height_m` to horizontal bounds.
- Enumerate deterministic Voronoi cells (normally tile size 200 m, frequency `1/(tile_size*0.67)`, randomness recorded, seed recorded). Assign each cell a natural tile type and `height_offset` from terrain/region evidence. `height_offset` is the ground floor elevation, never a fake mountain peak.
- Use `MultiMountains` parameters that produce visible natural undulation: non-ocean/non-wetland cells normally have `max_freq <= 0.002`, `height >= 30`, and `coverage >= 0.4`; adapt for the actual biome while avoiding flat procedural tiles. Use `Coast` only where a real coastline exists.
- Add feature elements in strict order: automatic atmosphere, mountains, optional upside-down/floating formations only when the brief demands them, then both `voronoi_rocks` and `warped_rocks` (at least one of each in every terrain config). Set mountain/formation `base_elevation` to the nearest cell's `height_offset`.
- Water is not modelled in this turn. Put riverbeds, lakebeds, and seabeds into the natural elevation via `height_offset`, without flattening for buildings.
- For each feature: think from the description, propose parameters, query the latest cumulative heightmap (`xy_resolution=32` for preview, 64 for verification), compare shape/height/extent, and adjust at most twice. Perform a final 64x64 full-bound query and check for holes, spikes, clipping, and landmark-elevation plausibility.
- Set `populated_bounds=[x_min,x_max,y_min,y_max,z_min,z_max]` with a ceiling above the effective tallest peak (`max_height_offset + max_mountain_height + 100 m` is a safe baseline) and a floor below the lowest sea/coast cell and sea depth. Raise the ceiling if the white model shows flat-topped clipping.

Artifact: `terrain/terrain_config.json`, complete seeds/parameters, and `terrain/terrain_white_model.blend` exported from a clean headless Blender process. Keep the white model geometry-only; no textures, water, or vegetation.

Gate: schema-valid config, required rock elements present, coherent elevation profile, no ceiling clipping, reproducible seed record, and a non-empty mesh preview.

### 1B. Hydrology and water topology (Scene Stage 6 Turn 2)

- Analyze the final combined baked heightmap, never an individual element. Compute filled heightmap, D8 flow direction/accumulation, depressions, watershed basins, and ridge lines with the terrain hydrology API; save all arrays and metadata.
- Choose water density from biome/narrative evidence, not arbitrary decoration. Plan in strict order: sea, lakes, rivers, streams. Sea uses the determined level and all pixels below it. Lakes use qualifying depressions at spill level. Rivers trace connected high-accumulation components downhill; streams descend from distributed ridge sources to a river/lake.
- Enforce monotone descent for every path, stop at sea/lake/boundary, estimate width from local accumulation, and distinguish water from lava materials. Use a documented target coverage and run at most three threshold-adjustment iterations; report any justified deviation.

Artifacts: `water_topology.json`, `water_mask.npy` (`0 none, 1 sea, 2 lake, 3 river, 4 stream`), `filled_heightmap.npy`, `flow_direction.npy`, `flow_accumulation.npy`, `watershed_basins.npy`, depression data, and `water_system_visualization.png`.

Gate: valid topology and mask, intelligible visualization, coverage within 50% of target or explicitly justified, all paths downhill, no river source below sea level, and positive lake area/depth.

### 1C. Terrain meshing (Scene Stage 6 Turn 3)

- Convert the SDF elements to a renderable mesh without applying materials. Estimate memory for candidate subdivisions before running. Prefer uniform meshing when it fits; use adaptive resolution around hero landmarks/terrain features when the estimate exceeds the budget.
- Preserve adequate geometry for the locked camera. For sub-1 km terrains target approximately 5 m voxels when feasible; for 1-2 km terrains approximately 10 m; for larger areas use adaptive/coarser distant regions while retaining hero detail. Record mode, resolution, bounds, estimated memory, triangle count, and any resolution mask.

Artifacts: `terrain/terrain_mesh.blend` and `terrain/mesh_metadata.json`.

Gate: valid Blender file, reasonable triangle count for the declared scope, estimated meshing peak under 1.5 GB where that budget applies, and no loss of camera-readable terrain detail.

### 1D. Terrain derivative maps (Scene Stage 6 Turn 4)

Compute all maps from the final combined heightmap: slope, normalized altitude, curvature, roughness, solar exposure, peaks, erosion/wear, sediment, occlusion, normals, flow, and walkability/water exclusion. Save same-shape arrays in the pipeline-compatible `.raw`/`.npy` formats and produce a multi-panel analysis image. Derivatives are the only inputs used to drive continuous material and scatter rules; do not key rules to cell IDs or element ownership.

Artifacts: derivative rasters, updated metadata with `analysis_maps`, and `terrain_analysis.png`.

Gate: matching shapes/byte sizes, finite ranges, normals and slope consistent with geometry, erosion concentrated in plausible valleys, and water masks aligned with the heightmap.

### 1E. High-fidelity terrain materials (Scene Stage 6 Turn 5)

- Browse the available PBR library first. Select 8-12 theme-appropriate 2K-4K sets, including at least four categories, two distinct rock/cliff sets, two differently toned ground-cover sets, and a wet/drainage set. Sample Color/albedo RGB before committing; reject textures whose temperature conflicts with the story.
- Drive rules only with continuous `slope_deg`, `altitude_norm`, `curvature`, `roughness_norm`, `flow_norm`, and `solar_exposure`. Rules are AND-combined, last matching rule has highest priority, and overlapping ranges plus smoothstep `blend_width` prevent seams. Use roughly 5-8 for fine overlays, 8-12 for ordinary transitions, and 15-22 for broad altitude transitions.
- No texture may cover more than 35% of terrain vertices. Require at least three visibly distinct color zones, no visible Voronoi boundaries, no hard bands, plausible wet channels/rock on steep faces, and non-obvious tiling. Correct by splitting ranges, replacing palette-conflicting textures, or increasing blend width; rerun at most three correction iterations.
- Apply materials in a separate Blender session when imports are heavy, pack textures only in a fresh session, and retain relative source paths until packing is safe.

Artifacts: `terrain/terrain_textured.blend`, `material_rules.json`, and at least three content-validated previews: overhead, perspective NE, perspective SW.

Gate: packed valid scene, material coverage/palette statistics pass, all previews show real terrain (pixel standard deviation >15 and uniform area <85%), and visual inspection passes the checklist above.

### 1F. Ecological scatter planning (Scene Stage 6 Turn 6)

- Derive moisture, riparian distance, water exclusion, wind exposure, and LOD from the final heightmap, hydrology, solar/occlusion maps, and landmark distances. Save all maps with ranges and shape checks.
- Design 6-12 ecologically coherent layers from the region description and the available registry: mature trees, saplings, shrubs, ground cover, flowers, dead vegetation, or biome-specific equivalents. Each layer has a niche, category, factory, concrete asset filter, density, spacing, scale variation, priority, blend width, and conditions. Always exclude water and avoid conditions so strict that a layer produces zero instances.
- Use LOD mask values 0-3 with density falloff toward distant regions; preserve hero zones within the highest detail band. Keep repeated placement deterministic and varied.
- Export `scatter_instances.json` and report counts by layer, category, LOD, and skipped reason.

The following scene-composition primitives are mandatory and must be retained whenever the scene contains regional assets or built paths: continuous zone-weight fields, Poisson-disk placement, soft spatial falloff, and measured road/structure support refinement. Represent each visual zone with one or more metric anchors, radius, strength, and falloff. Compute a per-pixel weight field, normalize overlaps, and reserve the residual as a background channel:

`effective_density = base_density * zone_weight(pixel) * condition_mask(pixel)`.

Use Poisson-disk sampling per rule with explicit minimum spacing, then apply the terrain condition mask and LOD density. This makes density transition gradually across zones, allows distinct asset pools per ecological/cinematic region, and avoids hard borders or a single repeated biome. Validate that every asset ID exists, that zones cover the intended scene, and that a preview shows perceptible but natural regional differences.

Gate: map shapes/ranges valid, at least six layers and the required ecological categories, total instances meaningful for scene size, no zero-instance layer without justification, and no water placement.

### 1G. Populate water and vegetation (Scene Stage 6 Turn 7)

Use this exact memory-safe order in the populate script: load terrain blend; create all water meshes while the depsgraph is lightweight; import each unique vegetation asset once; place linked instances; save without `pack_all()` and without rendering. Never run depsgraph-heavy operations after tens of thousands of instances are present.

- Rivers/streams: build subdivided ribbons along world-space paths, Shrinkwrap to terrain with a small negative offset, delete underground faces using the baked heightmap, and assign water/lava material.
- Lakes/sea: create sufficiently subdivided discs/planes at water level and delete a face only when *all* vertices are above terrain; keep shoreline-straddling faces. Use about 10 m sea subdivision to avoid staircase coastlines.
- Vegetation: deduplicate glTF imports, create linked duplicates, set transform from scatter data, then ground using a pre-placement depsgraph raycast or heightmap lookup. The asset bottom must touch terrain; skip a miss or any required correction over 5 m and log it. Do not hide bad placement with a catch floor.
- Keep water and lava materials distinct. Record placed/skipped counts, imported assets, polygon/vertex totals, and all approximations.

Render and pack in a second independent Blender process only after the populate process exits. Use a camera from the actual mesh bounding box, `clip_end=10000.0`, and a correct `to_track_quat('Z','Y')` look-at. Verification renders may use EEVEE with at least 8 samples to conserve memory, but the final requested render is Cycles at 1920x1080 on the configured render device (`worldagent_measure.render.setup_device`).

Artifacts: `terrain/terrain_populated.blend`, populated previews, and placement statistics.

Gate: every water body represented, no water inside solid terrain, no floating vegetation, retained textures, manageable polygon count, and nonblank previews.

### 1H. Derived asset, placement, and render specifications (non-game Stage 7)

- Derive `asset_manifest.json` from the declared scene objects and environment assets. De-duplicate by asset identity, use unique snake_case IDs, count instances accurately, and record source, seed, dimensions, topology, material count, LOD levels, estimated triangles, and fidelity status. Every declared object appears exactly once in a category.
- Derive `prop_placement.json` in a documented local Y-up metric coordinate system. Convert descriptive placement hints into concrete positions, rotations, and scales. Keep objects within bounds, at least 0.5 m apart unless intentional contact is documented, and ensure walls, floors, ceilings, doors, supports, and furniture relationships are physically plausible.
- Derive `render_specifications.json` per space/shot: natural and artificial lights, physically plausible intensity/color, atmosphere/fog (0.0-0.15), particles, dominant materials, palette, and a one-sentence mood. These are visual/story specifications, not gameplay instructions.

After the natural terrain is accepted, apply support-surface refinement for any declared structures, bridges, roads, camera paths, or other physically necessary contacts. Use measured footprints and target elevations from the topology/layout, lower or blend only the affected vertices with a soft falloff, preserve drainage and the natural horizon, and never flatten an entire region for convenience. Recalculate normals and affected derivative maps, then repeat the static support/no-overlap audit. Every accommodation (footprint, target, blend width, maximum displacement, reason) belongs in `manifest.json`; unsupported or hidden catch surfaces are prohibited.

Gate: complete manifests and placements, no missing or duplicate assets, all objects grounded/supported, and render specs consistent with the story and camera plan.

## Stage 1 Quality Refinement: Asset, Material, and Cinematic Review

The library pass is only a starting point. Repeatedly inspect hero close-ups, mid-distance shots, overhead composition, and the final render at native resolution. Replace any generic, low-detail, repetitive, malformed, or visually implausible asset before promotion.

- Library-first policy: use the local terrain/road PBR library and vegetation registry (the folder in `$WORLDAGENT_ASSET_LIBRARY`, when set) for the first layout pass so spatial coverage is fast and measurable.
- Replacement policy: if an asset lacks believable silhouette, branch/rock structure, thickness, UV scale, physically plausible roughness/normal/displacement, contact geometry, or species/geology specificity, replace it with a high-resolution authored/procedural asset. No external 3D model downloads or external 3D foundation models are allowed.
- Procedural generation is encouraged: Blender Python/Geometry Nodes, Infinigen factories, deterministic mesh construction, and procedural PBR/shader networks are acceptable. Record the generator, seed, prompt, parameters, and approximation in the manifest.
- Trees must show species-specific trunk taper, branch hierarchy, bark breakup, roots/contact, leaf/needle clustering, age/damage variation, and non-identical silhouettes. A billboard, crossed planes, repeated cylinder trunk, or generic lollipop canopy fails.
- Rocks must show geologically coherent massing, stratification/fracture logic, irregular contact footprint, scale variation, and micro roughness/displacement. Repeated rounded blobs or low-resolution faceted stand-ins fail.
- Materials must survive close-up: correct scale, roughness/metallic/transmission response, edge wear, wetness, dirt accumulation, and variation. Do not use bloom, noise, fog, or dark exposure to hide missing geometry, intersections, or incorrect scale.
- Keep visible, simulation, and collision meshes explicitly related. Any reduction must preserve contact topology and be recorded.

Use prompts such as the following as a generation brief for a procedural asset, then inspect and iterate the generated result rather than accepting the first output:

```text
Create a deterministic, production-ready [species/geology] asset for a physically grounded cinematic scene.
Reference: [real-world species/formation], habitat [biome], age/state [age, weathering, damage].
Geometry: real metric dimensions; primary silhouette, secondary construction, and camera-visible micro detail;
correct taper, contacts/roots or stable base, non-manifold-free closed surfaces, no floating pieces.
Materials: 2K-4K PBR or procedural albedo, normal, roughness, displacement, AO; physically plausible scale,
wetness, dust, edge wear, and color variation. Use three or more deterministic shape/tilt/branch variants.
Composition: no repeated clones, no billboard/cross-plane foliage, no primitive-only template look,
no decorative noise hiding topology, no external 3D model. Seed=[seed], generator=[Blender/Infinigen/script].
Negative checks: generic lollipop tree, identical rocks, faceted low-poly silhouette, texture stretching,
unrealistic scale, unsupported intersections, hidden catch floor, and untracked collider.
Output: source script/config, asset files, manifest entry, and close/mid/far inspection renders.
```

For every refinement loop record: hypothesis, one causal change group, seed/config hash, before/after renders, measured metrics, and the reason for acceptance or rejection. Record it in the case trace with the plugin's `bin/case_trace.py note`. Continue until asset realism, material realism, render realism, story completeness, camera narration, shot-to-shot continuity, scene richness, and overall polish improve consistently with evidence. Do not declare satisfaction from a single attractive frame.

## Indoor and outdoor layout coverage

The layout method covers both the exterior world and interior architecture. Use the post-Stage-5 spatial artifacts as follows:

- `outdoor_scene_graph.json` supplies world-space landmarks, regions, elevations, terrain transitions, and exterior relationships.
- `landmark_layouts.json` supplies each landmark's internal spatial graph: estimated width/depth/height, spaces such as `room`, `corridor`, `platform`, `open_area`, `entrance`, and `courtyard`, plus typed connections (`doorway`, `passage`, `stairs`, `ladder`, `elevator`, `open`, `bridge`, or `tunnel`) and spatial relationships such as above/below/adjacent. Every space must connect to another space; every exterior arrival must link to an interior entrance; all declared spaces must be reachable without relying on gameplay rules.
- `scene_descriptions.json` supplies the source of truth for each indoor or outdoor space: physical purpose, environmental needs, object counts/roles/positions/descriptions, a vivid artist-facing description, and `spatial_properties` for floor, ceiling, walls, elevation changes, natural light, and artificial light. Use these descriptions to derive the asset manifest, concrete placement, materials, lighting, and cameras.

For indoor spaces, build real load paths and real openings: floors bear on foundations or structural members, walls/ceilings meet without gaps, doors and windows are actual apertures, stairs/ramps have usable dimensions, and bridges or balconies have supports and guard geometry. Use local Y-up metric coordinates for room-level placement, then map each space into the landmark/world coordinate frame. Treat corridors, entrances, shafts, and courtyards as physical transition volumes, not abstract graph edges. Indoor water, smoke, cloth, rigid bodies, or debris must use explicit room boundaries and documented colliders; hidden catch floors and untracked blockers are forbidden. Apply the same high-fidelity asset review, zero-penetration scan, camera storytelling, and scene-to-simulation refinement loop indoors and outdoors.

## Memory and Process Safety

Estimate peak memory before every heavy Blender script. On WSL2-like 12 GB systems, keep a single process below about 8 GB (when the plugin's memory budget is configured, `WORLDAGENT_MEMORY_BUDGET_GB` replaces this figure). Loading blends, importing glTF, packing textures, Cycles BVH, and depsgraph evaluation stack. Never combine heavy imports with `pack_all()` and Cycles rendering. Use the two-session pattern: heavy load/import/water/placement -> save without packing/rendering; fresh open -> pack/re-save -> render. Perform Shrinkwrap, raycasts, modifier application, and other depsgraph-heavy water operations before placing large numbers of instances. Log the estimate when loading more than 500 MB of assets. Set camera `clip_end=10000.0` for kilometer-scale terrain.

