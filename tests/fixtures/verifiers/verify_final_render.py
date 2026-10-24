"""Render row verifier for the render tests: the newest render report must be a final render of this candidate."""
import os
from pathlib import Path

from worldagent_measure import render, verifier

args = verifier.parse_args()
reports = sorted(Path(args.case, "reports").glob("render_*.json"))
check = render.check_render_report(reports[-1], args.case, expected_device=os.environ.get("EXPECTED_DEVICE"))
verifier.write_result(args.out, [check])
