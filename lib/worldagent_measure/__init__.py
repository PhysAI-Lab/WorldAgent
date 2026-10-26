"""Shared measurement helpers for case verifiers.

Case verifiers (<case>/src/verify_*.py) import this package inside Blender;
the runner (bin/run_verifier.py) puts lib/ on sys.path.

Every helper measures and returns a Check; none decides a threshold on its own.
Thresholds come from the row's pass_predicate and are passed in by the case
script. Modules:

    verifier    argument parsing and result writing (runner contract); CacheMissing
    geometry    evaluated world-space meshes, BVH trees, AABBs, frame iteration
    contact     interpenetration / contact (paper Eq. 2)          [A5]
    scale       metric dimensions against object contracts          [A6]
    numerical   finite coordinates, displacement and velocity jumps [A6]; rest as window
                displacement of every vertex;
                trends and bounds over any per-frame series (pure Python)
    support     recursive support chain, floating objects           [A7]
    navigability route width, headroom, slope, continuity           [A7]
    causal      causal order of story events, response to contact
    shot        shot coverage: event objects seen by the camera during their beats
    liquid      liquid surface quality and volume                   [A7]
    gas         Mantaflow smoke and fire: setup before the bake, cache coverage, density
                continuity, wall loss, clearance to the domain faces
    particles   Newton and SPH particles: cache, setup, counts, colliders, tunnelling, escape, settling,
                rest as window displacement
    deformable  cloth and soft bodies: deformation, self-intersection, recovery, setup, free fall,
                visible-to-simulation transfer
    systems     object -> Blender system, declared vs scene, effectors and colliders
    coupling    couplings between solvers in the built scene against the solver graph, bake order
    solver_mesh obstacle meshes a grid liquid solver can voxelize
    rigid_setup rigid body origins at the center of mass, constraints Bullet ignores
    render      Cycles render device setup for the final render
"""

from .verifier import Check

__all__ = ["Check"]
