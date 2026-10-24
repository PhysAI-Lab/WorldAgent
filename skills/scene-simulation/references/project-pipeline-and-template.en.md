# Project Pipeline And Output Template

Use this reference for every new case. It standardizes stage ownership, artifact locations, promotion rules, and root-directory hygiene. Solver-specific parameters remain in the material playbooks.

## Canonical Case Layout

The case root is a delivery surface, not a scratch directory.

```text
case/
|-- README.md                 short human handoff
|-- manifest.json             canonical prompt, contract, status, paths, facts, limits
|-- scene_final.blend         created only after reopened validation passes
|-- src/                      reconstruction/configuration scripts actually used
|-- reports/                  machine-readable stage reports
|-- previews/                 optional requested milestone stills
|-- media/                    optional requested video or delivered native frames
|-- source/                   optional delivered editable/render-only derivatives
`-- .work/                    disposable masters, candidates, caches, frames, logs, backups
```

Only `README.md`, `manifest.json`, and a promoted `scene_final.blend` belong as files in the root. Create `previews/`, `media/`, and `source/` only when required. Do not create empty placeholder scripts or reports.

Recommended internal paths:

```text
src/build.py
src/config.json                 optional when manifest is not enough
src/probe.py                    only when a probe script is needed
src/bake.py                     only when a separate bake entrypoint is needed
src/validate.py
src/render.py                   only when rendering is requested

reports/static.json
reports/probe.json
reports/bake.json
reports/validation.json
reports/media.json              only when media is delivered
reports/checksums.sha256        only when integrity evidence is required

.work/scene_unbaked.blend
.work/candidates/<candidate_id>/scene.blend
.work/cache/<candidate_id>/
.work/frames/
.work/logs/
.work/backups/
```

Use a stable candidate ID derived from the serialized physics configuration or an explicit revision name. Never reuse a candidate cache after changing topology, transforms, solver settings, source behavior, or frame range.

## Root Hygiene Rules

- Never write `scene_unbaked.blend`, render derivatives, probe scenes, test-number scenes, logs, caches, or frame sequences to the root.
- Do not leave `.blend1`, `.blend@`, autosave files, temporary encodes, or failed candidates in the root. For scripted builds, set Blender's save-version preference to zero when backups are not required, or redirect working saves to `.work/backups/`.
- Save synchronized-drive work to `.work/` or a system temporary path first; promote with an explicit final copy/save only after success.
- Keep stdout/stderr in `.work/logs/` only when logs are useful. Do not create a log file merely because a command ran.
- Native frame sequences stay in `.work/frames/` by default. Copy them to `media/frames/` only when the user requests the master sequence.
- If multiple final scenes are requested, place them in `media/` or `source/` according to purpose and point `manifest.json` to the primary scene. Do not add ad hoc numbered files to the root.

Run the plugin's `scripts/check_case_layout.py <case>` before handoff.

## Manifest Is The Canonical Record

Do not split the prompt, design decisions, configuration summary, artifact index, and result status across many root-level Markdown and JSON files. Consolidate them in `manifest.json`; keep detailed numeric evidence in `reports/`.

Recommended manifest shape:

```json
{
  "schema_version": 1,
  "case": "case_name",
  "prompt": "exact user request",
  "status": "contract|built|probed|baked|validated|delivered|limited|failed",
  "contract": {
    "blender": "requested version",
    "simulation_class": ["rigid_body"],
    "units": "metric",
    "frame_range": [1, 180],
    "fps": 24,
    "story": {
      "long_form": false,
      "prompt_length_chars": 0,
      "required_beat_count": 0,
      "minimum_duration_seconds": 0.0,
      "duration_basis": [],
      "beats": []
    },
    "required_outputs": ["scene_final.blend"],
    "acceptance": [],
    "asset_fidelity": {
      "target": "realistic",
      "hero_assets": [],
      "acceptance": []
    },
    "scene_grandeur": {
      "target": "grand",
      "zones": [],
      "landmarks": [],
      "acceptance": []
    }
  },
  "classification": {
    "indoor": false,
    "outdoor": true,
    "terrain": false,
    "bounds_m": [100, 100, 50],
    "solver_ownership": []
  },
  "configuration": {
    "requested": {},
    "actual_reopened": {}
  },
  "iteration_policy": {
    "substep_status_required": true,
    "advance_requires": ["status=pass", "evidence_present", "hard_gates_pass"],
    "loop": ["one_causal_change_group", "shortest_falsifying_probe", "audit", "comparison_render", "matrix_update"]
  },
  "fluid_surface_quality": {
    "required": "when_free_surface_liquid",
    "pass": false,
    "critical_cell_coverage": {},
    "mesh_settings": {},
    "facet_metric": null,
    "giant_chunk_count": 0,
    "spike_count": 0,
    "temporal_pop_count": 0
  },
  "collision_boundary_contract": {
    "status": "unknown",
    "representation": "evaluated_mesh_or_proven_equivalent",
    "bbox_proxy_count": null,
    "untracked_collider_count": null,
    "asset_air_wall_count": null
  },
  "support_chain": {
    "status": "unknown",
    "assets": []
  },
  "unsupported_floating_count": null,
  "artifacts": {
    "final_scene": "scene_final.blend",
    "sources": [],
    "reports": []
  },
  "validation": {
    "passed": false,
    "summary": {}
  },
  "approximations": [],
  "limitations": [],
  "render_only": []
}
```

Update the status only when the corresponding gate has actually passed. `scene_final.blend` must not exist while status is merely `contract`, `built`, `probed`, or `baked`.

## Stage Artifacts And Gates

| Stage | Primary artifact | Required gate |
| --- | --- | --- |
| 0. Contract/topology | manifest contract, optional topology under `src/` | Every phenomenon and story beat has an owner, frame interval, path, and metric |
| 1. Static scene | `.work/scene_unbaked.blend` | Clean rebuild, correct roles, evaluated-mesh boundaries, support chains, and dependencies |
| 2. Static audit | `reports/static.json` | No unexplained float, air wall, overlap, support, topology, or visibility failure; proxy counts are zero |
| 3. Physics/probe | `reports/probe.json` | Stable preroll, intended activation, finite early motion, and passing surface-quality/narrow-phase collision probes |
| 4. Candidate bake | `.work/candidates/<id>/scene.blend`, `reports/bake.json` | Complete cache and no fatal condition |
| 5. Reopen/promote | `scene_final.blend`, `reports/validation.json` | Fresh-process validation, zero support/air-wall counts, liquid-surface quality pass, and 100% story coverage |
| 6. Media | optional `previews/`, `media/`, `reports/media.json` | Output comes from the accepted candidate, has no faceting/giant chunks/floating assets at native size, completes the story, and decodes correctly |

## Per-Substep Iteration And Promotion Discipline

Stage 0, every Stage 1 substep, every probe, bake, reopen, and render review must have its own
`status`, candidate ID, hypothesis, causal change group, evidence path, first failing frame, iteration
count, and pass predicate. If the status is `fail`, `unknown`, or evidence is missing, remain in that
substep and repeat “one causal change group -> shortest falsifying probe -> static/dynamic audit ->
comparison frames -> matrix update” until it is fully `pass`. A single attractive frame, later validation,
camera occlusion, or render effect cannot replace a hard gate.

Liquid cases must record `fluid_surface_quality` (critical-feature cell coverage, meshing/smoothing settings,
facet metric, giant-chunk/spike/temporal-pop counts, and volume trend). Complex scenes must record
`collision_boundary_contract` (evaluated-mesh source/hash, thickness, margin, `bbox_proxy_count`,
`untracked_collider_count`, `asset_air_wall_count`) and a `support_chain` for every asset. Any nonzero
air-wall/proxy/unsupported-floating count blocks promotion.

Story duration follows physical and readable time: if transport, interaction, settling, or aftermath cannot
fit the current range, increase total frames at the fixed FPS and rebake. Do not shorten the story with
acceleration, frame dropping, interpolation, or a compressed source pulse.

## Stage 0 Topology

Use a topology or interaction graph when the scene contains multiple spatial regions, support chains, openings, collision routes, fluid paths, or coupled solvers. Nodes should describe dimensions, material, support, permeability or collision role, and solver ownership. Edges should describe containment, adjacency, support, openings, flow paths, collision paths, or coupling direction.

The graph is a planning and validation artifact, not mandatory bureaucracy. A simple single-object drop test may record the same information directly in the manifest.

## Stage 1 Static Scene Guidance

- Classify indoor/outdoor/terrain content and estimate bounds before generating geometry.
- Use stable descriptive names derived from topology roles.
- Ground structures against sampled terrain or explicit supports. Make doors, windows, gaps, and channels real geometry when they affect motion.
- Prefer one shared height-field terrain mesh for hills, valleys, slopes, channels, and terraces when a height field can represent the requested shape. Do not force caves or overhangs into that representation.
- Scatter repeated vegetation or debris with deterministic instancing instead of creating unrelated full-geometry copies.
- Make long roads, fences, walls, rails, and channels follow the support surface.
- Skip geometry that is supposed to be produced by a later solver. A static placeholder is allowed only when it cannot be mistaken for the simulated output and is documented.

## Minimal Output Matrix

| Artifact | Default | Location |
| --- | --- | --- |
| Final validated scene | Yes, on success | `scene_final.blend` |
| Manifest | Yes | `manifest.json` |
| Reconstruction source/config | Yes | `src/` |
| Applicable reports | Yes | `reports/` |
| README | Yes, concise | `README.md` |
| Milestone previews | Only if requested or needed for visual acceptance | `previews/` |
| Encoded video | Only if requested | `media/` |
| Native delivered frames | Only if explicitly requested | `media/frames/` |
| Editable unbaked master | Only if requested | `source/scene_unbaked.blend` |
| Render-only derivative | Only if requested | `source/scene_render.blend` |
| External cache | Only if required for playback/rebake | `source/cache/` or documented dependency |
| Failed candidates and logs | Never as deliverables | `.work/` |
