"""Example case verifier: a jelly falls freely onto a plate and its render mesh follows the simulation.

Template for Soft Body rows. The runner opens a copy of the candidate and puts lib/ on sys.path:

    blender --background --factory-startup <copy.blend> --python verify_soft_body_example.py \
        -- --out <result.json> --case <case_dir> --row <row_id>

Order: the setup check on the rest frame (structure only), the fall measured on the frames
before first contact, then the render mesh against the simulation surface on every frame.
"""
import json
from pathlib import Path

import bpy

from worldagent_measure import verifier
from worldagent_measure.causal import contact_onset
from worldagent_measure.deformable import free_fall, soft_body_setup, transfer_deviation
from worldagent_measure.geometry import frame_list, set_frame

# Same values as the row's pass_predicate and tolerances.
MAX_ACCELERATION_ERROR_REL = 0.02
# Above the Soft Body integration offset (about 0.05 x g x frame time = 0.02 m/s at 24 fps).
MAX_INITIAL_SPEED_M_S = 0.05
# Tolerance rule: max(5 px at the final resolution, solver distance); the row records the basis.
MAX_TRANSFER_DISTANCE_M = 0.004

args = verifier.parse_args()
manifest = json.loads((Path(args.case) / "manifest.json").read_text(encoding="utf-8"))
start, end = manifest["contract"]["frame_range"]
jelly, plate, render = (bpy.data.objects[n] for n in ("jelly", "plate", "jelly_render"))

set_frame(start)
setup = soft_body_setup(jelly, free_fall=True, frame_start=start, frame_end=end, baked=True,
                        supports=[plate], visible=[render])
checks = [setup]
if setup.passed:
    # A Soft Body stops about 0.9 x thickness_outer above a Collision surface, so the contact
    # tolerance must be at least the solver distance.
    onset = contact_onset(jelly, plate, frame_list(start, end), contact_tol_m=setup.measurements["solver_distance_m"])
    fall_end = (onset or end + 1) - 1
    checks.append(free_fall(jelly, frame_list(start, fall_end), max_acceleration_error_rel=MAX_ACCELERATION_ERROR_REL,
                            max_initial_speed_m_s=MAX_INITIAL_SPEED_M_S))
    checks.append(transfer_deviation(render, jelly, frame_list(start, end), max_distance_m=MAX_TRANSFER_DISTANCE_M))
verifier.write_result(args.out, checks, frames=[start, end])
