# FLIP Scene Decision Guide

Use this reference to choose a first profile by scene mechanism. It intentionally provides no fixed dimensions,
frame counts, resolutions, focal lengths, or one-off experiment parameters. Derive every task from real scale,
action, camera, cell coverage, story duration, and resource budget, then pass the parent structural,
low-resolution physics, metrics, render, and reopen gates. The parent skill and general workflow take precedence.

## Scene Matrix

| Scene mechanism | Domain/input starting point | First validation focus | Effect promotion direction | Camera starting point | Required metrics |
| --- | --- | --- | --- | --- | --- |
| Beach or shoreline wave | Continuous bathymetry, initial water or finite wave train; separate near-camera interaction from distant water | No whitewater: prove establishment, breaking, run-up, retreat, and finite boundary | Add whitewater/Sheeting one variable at a time; refine from shoreline cell coverage and convergence | Slightly oblique shore-facing medium/long lens; move closer before enlarging Domain | Alongshore arrival spread, run-up, obstacle contact, volume, whitewater, surface, boundary |
| Enclosed-water impact | Thick pool/container shell, watertight collision proxy, initial level below edge | No whitewater/surface tension/viscosity: prove obstacle timing, displacement, drainage, and level | Add only required whitewater or surface effects after bulk displacement passes | Low three-quarter view with subject and waterline visible | Contact, leakage, peak timing, volume, level, late settling, zero penetration |
| Waterfall or drop | Top Inflow, initial pool, downstream Outflow, closed rock proxies | Prove gravity-driven fall, curtain continuity, impact, and drainage | Add Foam/Spray/Bubble after base flow; use a separate mist particle/volume system | Side three-quarter showing source, curtain, impact, and pool | In/out volume, level, first impact, stable curtain, trapped liquid, top clearance |
| Product or container splash | Fitted Domain, finite Inflow, FLIP Curve Guide, catch region | No whitewater/surface tension/Sheeting: prove path, clearance, and contact | Compare surface tension, Sheeting, and whitewater one variable at a time | Product-first three-quarter medium/long lens | Path, entry/exit timing, wrap angle, contact, sheet continuity, drop scale |
| Viscous pour | Scale-fitted nozzle/container and resolvable channel; add internal bubbles later | Compare scale-anchored low/medium/high viscosity candidates and FLIP/APIC damping | Add bubbles and fine effects only after bulk motion is stable | Macro three-quarter that does not expose a finite Domain | Method, Viscosity, Ratio, Substeps, iterations, breakup, folds, impact, trapped air |
| Boat or object wake | Watertight collision proxy, local FLIP interaction, distant Ocean/procedural water | No whitewater: prove hull contact, bow/stern waves, wake direction, and boundary clearance | Add whitewater from wake width and speed evidence; record Froude state | Shore low three-quarter telephoto | Contact, wave timing, wake angle, speed, margins, volume |
| River, rapid, or flood | Continuous riverbed/canyon, upstream Inflow, downstream Outflow, resolvable obstacles | No whitewater: prove section levels, arrival, in/out balance, and downstream progress | Add whitewater by impact and transport regions | Low along-flow view with side angle for drops | Section levels, arrival, discharge continuity, obstacle contact, speed, residual volume |
| Lakeside rain or runoff | FLIP near-shore runoff plus Ocean/procedural distance; rain as independent particles/geometry | No whitewater: prove level, runoff, raindrop contact, and system ownership | Add only required whitewater and wet-contact material response | Layered shot: FLIP foreground, rain middle, distant water | Level, runoff volume, wave arrival, raindrop contact, FLIP/Ocean seam |
| Breakwater, cave, or tidal structure | Thick structure, resolvable opening, offshore wave train, interior return flow, distant water | No whitewater: prove outside impact, overtopping, opening speed, interior level, and return flow | Add whitewater by impact/opening/interior regions | Inside-structure three-quarter keeping wave, opening, and return readable | Three-zone levels, opening speed, in/out/residual volume, whitewater, boundary |

## General Decision Boundaries

- Start gravity-dominated open water with viscosity, surface tension, and Sheeting off. Test those variables
  for small splashes or viscous liquids only after the base path passes.
- FLIP owns camera-near interaction. Ocean/procedural water and independent rain/mist systems must be named,
  assigned their own solver owner, and prevented from colliding twice with FLIP.
- Use finite Fluid for initial volume, Inflow for sustained supply, and volumetric Outflow for intended drainage;
  include every change in the volume budget.
- Waterfall mist, raindrops, distant water, and render displacement cannot pose as FLIP output; record ownership.
- Derive every candidate from `cell_size`, cells across critical features, story duration, and resource budget;
  this matrix selects mechanisms, not prescriptions.
- Every effector/asset collision boundary must come from the asset's evaluated mesh or a proven equivalent
  analytic shape. AABB/OBB shells, enclosing boxes, catch floors, and air walls are prohibited. Each asset
  also needs a support chain to ground/structure/valid parent/evidenced cantilever; `asset_air_wall_count`
  and `unsupported_floating_count` must be zero.
- Production liquid must be free of faceting, voxel steps, spikes, and isolated giant chunks at native size.
  Derive resolution from the narrowest flow/obstacle gap/sheet/wave feature; low resolution is timing-only,
  and artifacts require single-cause iterations and rebakes.

## Nine Semantic Review Moments

The contact sheet covers establishment/preroll, source start or first approach, first contact, bulk deformation
or crest growth, maximum impact, transport around the obstacle, whitewater/secondary interaction,
drainage/wake/retreat, and late volume/boundary state. Animated cameras also include every anchor, nearest/widest
lens position, and intermediate intervals; regenerate the contact sheet after camera changes even when the cache
is reusable.
