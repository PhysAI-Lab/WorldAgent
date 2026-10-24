"""Example case verifier: no interpenetration between the box and the table on any frame [paper Eq. 2].

Template for case verifiers that use worldagent_measure.
The runner opens a copy of the candidate and puts lib/ on sys.path, then calls:

    blender --background --factory-startup <copy.blend> --python verify_contact_example.py \
        -- --out <result.json> --case <case_dir> --row <row_id>
"""
import json
from pathlib import Path

from worldagent_measure import verifier
from worldagent_measure.contact import penetration
from worldagent_measure.geometry import frame_list

# Same value as the row's pass_predicate ("signed clearance >= -1e-6 m on every frame").
# eps_pen only absorbs geometry-query noise; it is not a penetration allowance.
EPS_PEN_M = 1e-6
# Every object that can touch another one in this case; all pairs are checked.
OBJECTS = ["box", "table"]

args = verifier.parse_args()
manifest = json.loads((Path(args.case) / "manifest.json").read_text(encoding="utf-8"))
start, end = manifest["contract"]["frame_range"]

check = penetration(OBJECTS, frame_list(start, end), eps_pen=EPS_PEN_M)
verifier.write_result(args.out, [check], frames=[start, end])
