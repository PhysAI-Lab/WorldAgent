"""Blender-side probe for run_batch.py: rigid body setup checks on a finished candidate.

blender --background --factory-startup <candidate.blend> --python-expr <add lib/ to sys.path>
        --python setup_probe.py -- --out <result.json>

The 1 mm limits here only flag values for a person to review in the batch summary; they are
not requirement thresholds and decide nothing about the case.
"""
import json
import sys

import bpy

from worldagent_measure import rigid_setup

REVIEW_LIMIT_M = 1e-3


def summary(check):
    m = check.measurements
    out = {"passed": check.passed}
    if check.first_failure:
        out["first_failure"] = check.first_failure
    for key in ("bodies_checked", "largest_offset", "constraint_count", "ignored_count", "failing_count",
                "not_checked"):
        if key in m:
            out[key] = m[key]
    return out


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    out_path = argv[argv.index("--out") + 1]
    result = {"rigid_bodies": len(rigid_setup.rigid_bodies())}
    if result["rigid_bodies"]:
        checks = {
            "origin_at_center_of_mass": lambda: rigid_setup.origin_at_center_of_mass(max_offset_m=REVIEW_LIMIT_M),
            "collision_shape_fit": lambda: rigid_setup.collision_shape_fit(max_protrusion_m=REVIEW_LIMIT_M,
                                                                           max_uncovered_m=REVIEW_LIMIT_M),
            "bodies_in_world": rigid_setup.bodies_in_world,
            "constraints_in_world": rigid_setup.constraints_in_world,
            "keyed_active_bodies": rigid_setup.keyed_active_bodies,
        }
        for name, run in checks.items():
            try:
                result[name] = summary(run())
            except Exception as exc:  # noqa: BLE001 - report every probe failure, never stop the batch
                result[name] = {"error": f"{type(exc).__name__}: {exc}"}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str)


main()
