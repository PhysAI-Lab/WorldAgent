# Generalized Evidence Patterns / 通用证据模式

Use this reference only when diagnosing a failure or explaining a non-obvious rule. It contains anonymous,
cross-scene evidence patterns rather than project histories, task configurations, or isolated
performance results. A pattern becomes a skill rule only when it reflects a solver invariant, a repeated
failure mode, an explicit user constraint, or a measurable validation improvement.

仅在诊断失败或解释非显然规则时读取本参考。这里保留的是匿名化、可跨场景复用的证据模式，
不记录项目演进、测试编号、唯一配置、单次耗时或特定工程验收值。

## Evidence Classes / 证据类别

| Solver or concern | Evidence that can support a claim | Evidence that cannot support it alone |
| --- | --- | --- |
| Bullet rigid bodies | Sequential transforms, contacts, support chains, mass/shape records, cache reopen, penetration scans | Rigid-body flags, one attractive impact frame, object count |
| Cloth | Evaluated pre-Cloth resolution, pins, collision/self-collision, convergence, sequential deformation, native-resolution diagnostic renders | Post-Cloth subdivision, smooth normals, motion blur, modifier presence |
| FLIP liquid | Cell coverage, role/export audit, contiguous cache payload, volume balance, bounds, arrivals, contacts, whitewater budget, reopened sequential validation | Bake operator success, nominal resolution, one water render, cache file count |
| Mantaflow Gas | Resolvable sources, Data continuity, evaluated density/flame grids, centroid/AABB, six-face shells and margins, cache-mode read-back | `FINISHED`, baked state, file count/size, Noise enabled, hero-camera framing |
| Particles/granular approximations | Particle conservation, occupied volume, velocity/rest metrics, ground clearance, disclosed interaction limits | Particle count alone, a pile silhouette, render instances without trajectory provenance |
| Soft bodies | Rest/current topology, binding, spring/cohesion graph, recovery transfer, deformation bounds, collision scans | Modifier presence, a low-resolution cage moving, hidden helper count |
| Coupled scenes | Explicit direction, input/output ownership, force collection isolation, bake order, cache identity, missing reaction-force disclosure | Shared timing, nearby effects, one force field affecting several solvers |
| Presentation and delivery | Physics fingerprint, cache provenance, native frames, media audit, relative dependencies, final reopen | A polished video, screenshot, upload progress, or `.blend` without required external cache |

## Recurring Failure Patterns / 重复失败模式

| Observed pattern | Generalized rule |
| --- | --- |
| An operator returns success but expected motion or volume is absent | Read actual evaluated solver state and cache payload after reopening; execution status is not result evidence. |
| A source or opening is smaller than the solver cell/voxel | Compute physical cell size and require important features to span multiple cells before increasing speed or force. |
| Raising nominal resolution makes sampling less stable | Check moving-source thickness, time steps, subframes, CFL, transforms, and actual cell coverage; more cells are not automatically more accurate. |
| Enlarging a fluid or gas Domain causes coarse or broken behavior | Scale resolution with Domain size as a starting estimate, then re-probe the thinnest source and critical passage. |
| A finite liquid or gas volume disappears near a boundary | Measure world/index AABB, six-face margins and shell activity; distinguish solver deletion/reflection from material fade and camera crop. |
| A high-resolution or Noise cache is marked baked but empty | Read the final evaluated grid/mesh, verify dimensions and nonzero payload, and test the selected cache path before production. |
| A render-only change unexpectedly alters simulation | Compare resolved physics hash, participating topology, exported inputs, cache path, timestamps, and cache content before reuse. |
| Old cache survives a parameter revision | Give every physics revision a new candidate/cache ID and reject mismatched configuration fingerprints on reopen. |
| A hidden emitter, proxy, force field, or diagnostic object leaks into the result | Audit viewport, render, collection, ray, and solver visibility separately; hiding one channel does not prove all channels. |
| Hiding a helper also removes or changes the solve | Verify dependency-graph participation independently from viewport/render visibility and preserve required solver evaluation. |
| A global force moves unrelated objects | Isolate solver collections and prove target/non-target behavior with force-field on/off differentials. |
| A whole/damaged or visible/simulation handoff jumps | Compare adjacent-frame world pose, silhouette, visibility, velocity where applicable, and overlap/gap; require one continuous handoff. |
| A local event causes symmetric, simultaneous global response | Check source locality, constraint/load propagation, activation counts, and near/far timing; local input should normally spread progressively. |
| Target motion, support, cache, and solver-ownership checks pass but the requested event is absent | Separate mechanical health from semantic completion; require measurable trigger, activation/release, response, target reach, and terminal result. |
| A target activates or moves before the claimed impact/source trigger | Record adjacent-frame causal milestones and fail any chain whose response precedes its trigger. |
| A conservative AABB overlap warning is treated as penetration | Use AABB only for broad-phase pairing; normal contact requires narrow-phase geometry or signed-depth classification. |
| Mean metrics pass while one object or region fails | Report counts, maxima, quantiles, failed identities/frames, and spatial groups rather than averages alone. |
| A threshold is loosened after seeing a failed result | Keep the predeclared threshold; record the candidate as limited/failed and justify any future contract revision before rerunning. |
| A render looks realistic but the target asset or physics is crude | Keep target realism and solver evidence as separate hard rows; background, smoke, glow, depth of field, and editing cannot compensate. |
| A media result is smooth but omits native solver time | Audit native frame count, FPS, duration, duplicates, dropped/interpolated frames, and story coverage before encoding acceptance. |

## Diagnostic Order / 诊断顺序

Fix the earliest failed layer because later layers cannot repair it:

1. semantic contract, coordinate/side conventions, event order, and solver authority;
2. scale, initial state, transforms, topology, support, source/collider resolvability, and no overlap;
3. solver inputs, collection isolation, temporal sampling, cache identity, and continuous probe;
4. actual reopened cache content, bounds, contacts, volume/density continuity, and penetration;
5. visible-surface transfer, materials, lighting, camera, and helper visibility;
6. native sequence, media encoding, portability, and requested delivery.

先修复最早失败层级。材质不能修复错误体积，Noise 不能修复断源，镜头不能制造未发生的接触，
剪辑也不能补齐被截断的故事节点。

## Three-State Evidence / 三态证据

Every mandatory check must produce structured `PASS`, `FAIL`, or `UNVERIFIED` with the inspected artifact,
frame/window, threshold, measured value, and failure reason. A hard failure makes the candidate fail. A hard
check that is `UNVERIFIED` also blocks promotion; do not place it only in notes and calculate an overall pass
from explicit failures. Approximation disclosure is allowed only after every related hard boundary is proven.

每个必检项必须记录被检查制品、帧/窗口、阈值、实测值和失败原因。硬检查不可读或证据缺失时
保持 `UNVERIFIED` 并阻止晋级，不能把未知项移入备注后仍输出总体通过。

## Generalization Policy / 泛化原则

- Preserve solver invariants, repeated failure mechanisms, authorization limits, and measurable gates.
- Convert useful numeric relationships into scale-aware formulas or candidate ranges; do not preserve unique
  coordinates, hashes, machine timings, cache sizes, frame counts, or isolated tolerances.
- Keep approximation boundaries explicit: Bullet is not continuous fracture, FLIP and Mantaflow are not
  automatic two-way FSI, particle SPH is not DEM, Mantaflow Gas is not validated detonation, and Blender Soft
  Body is not general FEM.
- A known-good configuration may seed a new probe only after removing case identity and re-deriving scale,
  time, cell/voxel coverage, budget, and thresholds for the current task.
- Verified facts, unverified assumptions, render-only effects, and known limitations remain separate in reports.
