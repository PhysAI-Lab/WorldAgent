# Visual Realism And Cinematography

Use this reference after the physical simulation is valid and the task requires a refined model, attractive rendering, or camera movement. It defines presentation quality without allowing presentation effects to replace solver evidence.

## Realism Budget

Spend the budget in this order:

1. **Scale and silhouette:** calibrate metric scale, proportions, thickness, center of mass, support logic, and camera-resolvable size. Wrong scale breaks collisions, highlights, depth of field, and perceived motion at once.
2. **Construction detail:** add brick courses, tile courses, eaves, fasteners, edge treatment, assembly direction, and layer relationships that follow how the object is built. Instance repeated assets, but retain independent rigid-body state for independent solver objects.
3. **Material response:** metals need plausible highlights and roughness; wood and masonry need controlled color/roughness variation; wetness, dust, scorching, and mud need a spatial and temporal rationale. Avoid flat colors, excessive mirror response, and indiscriminate glow.
4. **Lighting and atmosphere:** establish key, fill, rim, and contact shadow before adding fog, smoke, sparks, volumetric light, and color management. Atmospheric density must not hide the subject, contacts, or penetration failures.
5. **Shot-scale readability:** confirm that detail survives at the target resolution. Unresolvable micro-geometry only increases file, dependency-graph, and render cost.

## Physics And Presentation Boundary

- After physics passes, copy a render-only derivative before adding high-detail visible meshes, instanced tiles, normal/displacement, decals, smoke, fire, glare, depth of field, motion blur, or grading.
- The derivative must not change participating topology, transforms, modifier order, collision masks, solver settings, or cache paths. If a high-resolution visible surface is driven by a reduced solver body, record the transfer, offset, topology invariants, and visible-surface penetration check.
- Keep presentation enhancements in a separate collection or namespace and label them render-only. Never hide a wall, catch floor, air wall, or camera occlusion that conceals a physical failure.
- Record paths, packing state, and licenses for textures, HDRIs, fonts, and external models; reopen the final scene and audit for broken links.

## Shot List And Motivated Camera Motion

Write a shot list before keying the camera. Cover at least:

- **Establishing state:** communicate scale, environment, subject, and the location of forces or sources.
- **First release/contact:** keep the projectile, cloth boundary, fluid surface, or destruction origin readable.
- **Main interaction/peak:** allow a motivated push-in, lateral move, or orbit while keeping the subject in a safe frame area.
- **Settling/aftermath:** slow down or pull back to show debris, fluid settling, smoke, and residual motion.

Use continuous paths with bounded acceleration and motivated aim points. Camera position, orientation, focus, lens, shutter, exposure, and lights may be keyed; never key the output position, rotation, or deformation of objects that should be solved. Avoid arbitrary shake, sudden zooms, crossing colliders, clip-plane loss of important objects, excessive motion blur, or camera motion used to hide numerical failure. Diagnostic-render long lenses, low angles, heavy occlusion, and depth-of-field shots before the final sequence.

Record camera matrices, focal length, sensor, clip planes, focus, shutter/motion blur, resolution, FPS, and reopened RNA values.

## Render And Media Acceptance

1. Close and reopen the final physical candidate, create a render-only derivative, and save it separately.
2. Low-cost render the initial, first-effect, peak, and final frames; check subject readability, focus, exposure, materials, atmospheric occlusion, and camera continuity.
3. After passing, render a continuous native PNG/EXR master sequence and encode video. Do not make interpolation or subsampling the only deliverable.
4. Check the final sequence and video for missing, black, frozen, and undecodable frames, exact count/FPS/duration/dimensions/pixel format, and a contact sheet. Label flashes, smoke, glow, sound, and volumetric fog as presentation enhancements.

### Final Visual Hard Gate For Surfaces And Support

At native 1920x1080 close, mid, and wide frames, inspect evaluated liquid and asset contacts directly.
Liquid may not show faceting, voxel stair-steps, spikes, layered breaks, isolated giant chunks, source-shell
inflation, disconnected blobs, or temporal pops. Record critical-feature cell coverage, mesh generator,
particle/mesh radii, positive/negative smoothing, facet/normal metrics, volume trend, `giant_chunk_count`,
`spike_count`, and temporal-pop count. Reflections, fog, glare, depth of field, motion blur, compositing,
or occlusion may not hide geometry defects.

Run narrow-phase BVH/closest-point checks against the actual evaluated mesh for walls, floors, thresholds,
furniture, table legs, columns, paper, and every other asset. Confirm no bounding-box air wall or rectangular
stop face; an AABB is candidate pairing only. Recursively audit each asset's ground/structural support chain,
vertical gap, and cantilever reason, and record `unsupported_floating_count`. Both the PNG/EXR sequence and
encoded video must pass. Any nonzero `asset_air_wall_count`, `bbox_proxy_count`,
`untracked_collider_count`, or `unsupported_floating_count` fails media acceptance.

If any visual-review item is `fail`, `unknown`, or missing evidence, remain in the current render-review
substep. Change one attributable causal group, rerender the shortest continuous frames that can falsify
the hypothesis, and repeat until the substep is fully `pass`; do not proceed to encoding or bypass it with
post effects.
