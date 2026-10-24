"""Shared measurement helpers for case verifiers.

Case verifiers (<case>/src/verify_*.py) import this package inside Blender;
the runner (bin/run_verifier.py) puts lib/ on sys.path.

Every helper measures and returns a Check; none decides a threshold on its own.
Thresholds come from the row's pass_predicate and are passed in by the case
script. Modules:

    verifier    argument parsing and result writing (runner contract)
    geometry    evaluated world-space meshes, BVH trees, AABBs, frame iteration
    contact     interpenetration / contact (paper Eq. 2)          [A5]
    scale       metric dimensions against object contracts          [A6]
    numerical   finite coordinates, displacement and velocity jumps [A6]
    support     recursive support chain, floating objects           [A7]
    navigability route width, headroom, slope, continuity           [A7]
    liquid      liquid surface quality and volume                   [A7]
    solver_mesh obstacle meshes a grid liquid solver can voxelize
    rigid_setup rigid body origins at the center of mass, constraints Bullet ignores
"""

from .verifier import Check

__all__ = ["Check"]
