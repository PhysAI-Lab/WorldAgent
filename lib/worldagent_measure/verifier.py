"""Runner contract for case verifiers: read arguments, write the result JSON.

    from worldagent_measure import verifier
    from worldagent_measure.contact import penetration

    args = verifier.parse_args()
    check = penetration(...)
    verifier.write_result(args.out, [check])

Pure Python (no bpy), so it is testable outside Blender.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field


@dataclass
class Check:
    """One measured check.

    name          short id used as the key in the result's measurements
    passed        True when every measured value meets the given threshold
    measurements  measured values (numbers, counts, lists); never defaults
    first_failure first failing object/pair/frame/value, None when passed
    """

    name: str
    passed: bool
    measurements: dict
    first_failure: dict | None = None
    evidence: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.measurements:
            raise ValueError(f"check {self.name!r} has no measurements")
        if self.passed and self.first_failure:
            raise ValueError(f"check {self.name!r} passed with a first_failure")
        if not self.passed and not self.first_failure:
            raise ValueError(f"check {self.name!r} failed without a first_failure")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the runner's arguments after Blender's `--` separator."""
    argv = sys.argv if argv is None else argv
    rest = argv[argv.index("--") + 1:] if "--" in argv else []
    parser = argparse.ArgumentParser(prog="case verifier")
    parser.add_argument("--out", required=True)
    parser.add_argument("--case", required=True)
    parser.add_argument("--row", required=True)
    return parser.parse_known_args(rest)[0]


def _clean(value):
    """Make measurements JSON-safe: tuples/vectors to lists, non-finite floats to strings."""
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return str(value)
    if hasattr(value, "__len__") and hasattr(value, "__getitem__") and not isinstance(value, str):
        return [_clean(v) for v in value]
    return value


def result(checks: list[Check], frames: list[int] | None = None) -> dict:
    """Combine checks into one runner result: pass only if every check passed."""
    if not checks:
        raise ValueError("no checks")
    names = [c.name for c in checks]
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate check names: {names}")
    failed = [c for c in checks if not c.passed]
    out = {
        "status": "fail" if failed else "pass",
        "measurements": {c.name: _clean(c.measurements) for c in checks},
        "evidence": [e for c in checks for e in c.evidence],
    }
    if failed:
        out["first_failure"] = {"check": failed[0].name, **_clean(failed[0].first_failure)}
    if frames is not None:
        out["frames"] = [int(frames[0]), int(frames[1])]
    return out


def write_result(out_path: str, checks: list[Check], frames: list[int] | None = None) -> dict:
    data = result(checks, frames)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data


def write_blocked(out_path: str, reason: str) -> dict:
    """Report that the row cannot be verified, e.g. a missing FLIP Fluids add-on."""
    if not reason.strip():
        raise ValueError("blocked needs a reason")
    data = {"status": "blocked", "reason": reason}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return data
