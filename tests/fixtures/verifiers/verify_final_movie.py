"""Media row verifier for the encode tests: the newest render report's movie must match its final frames."""
from pathlib import Path

from worldagent_measure import render, verifier

args = verifier.parse_args()
reports = sorted(Path(args.case, "reports").glob("render_*.json"))
try:
    check = render.check_movie(reports[-1], args.case)
except render.MovieToolUnavailable as exc:
    verifier.write_blocked(args.out, str(exc))
else:
    verifier.write_result(args.out, [check])
