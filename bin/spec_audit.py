#!/usr/bin/env python3
"""Audit the world spec in a case manifest.json before construction.

Checks the cross-references a JSON schema cannot express [paper A.1.1
'Specification audit'; skill.md Stage 0]:

- beat ids are unique, intervals are ordered and inside the contract frame range
- each beat lasts at least its minimum readable duration at the contract FPS
- depends_on names existing beats, has no cycles, and prerequisites start first
- the beat count reaches required_beat_count
- object names are unique and every initial_support resolves
- topology region ids are unique and every edge joins existing regions
- every object names its simulation_class and blender_system, and a system that is not
  preferred for the class (schemas/solver_classes.json) has a solver_choice_reason with a
  reason and evidence [SKILL.md 'Solver Classification']; the object classes and
  contract.simulation_class agree
- when the manifest has a requirements_matrix: every row names a measured quantity
  (tolerances or measures), and every distance, penetration, position or error
  tolerance states its basis and derivation. Default rule: max(5 px at the final delivery
  resolution at that distance, the solver's resolution); smaller only on request; numeric
  tolerances as a ratio of object size [implementation decision 2026-10-24]

Shape is checked by schemas/world_spec.schema.json; this script only reports
malformed parts it cannot read. Standard library only.

The first time the audit passes on a case's manifest.json, the spec is frozen: a copy and
its hash go to <case>/reports/spec_baseline.json and the case trace gets a `spec_frozen`
event. Later runs never overwrite it. The gate and promote compare the current spec with
it and list every change for review, whether or not any row was measured in between.

Usage: spec_audit.py <manifest.json> [--out report.json]
Exit: 0 pass, 1 audit errors, 2 unreadable manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import case_trace  # noqa: E402  (spec part and hash shared with the runner and the trace)

# The spec the case was built against [skill.md Stage 0: spec audit before construction].
BASELINE = Path("reports") / "spec_baseline.json"

# Support targets that are not objects [non-penetration-contract.en.md support chain].
SUPPORT_KEYWORDS = {"foundation", "ground"}
# Several supports may be listed in one string, e.g. "wall_a|wall_b".
SUPPORT_SEPARATOR = "|"

# Physical target -> preferred Blender systems [SKILL.md 'Solver Classification'].
SOLVER_CLASSES = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "solver_classes.json")
                            .read_text(encoding="utf-8"))
PREFERRED_SYSTEMS = {name: c["preferred"] for name, c in SOLVER_CLASSES["classes"].items()}
SYSTEMS = set(SOLVER_CLASSES["systems"])
# Object classes that no solver moves; they need not appear in contract.simulation_class.
UNSIMULATED_CLASSES = {"fixed", "kinematic"}
# Case-level only: each object of a coupled system keeps its own class.
COUPLED = "coupled"

TOLERANCE_BASES = ("screen", "solver", "numeric", "request")
COMPARISONS = {"<", "<=", ">", ">=", "==", "!=", "within"}
# A measure in one of these units bounded from above (or equal) is a tolerance and needs a basis.
LENGTH_UNITS = {"m", "cm", "mm", "um", "µm", "μm", "nm"}
LOWER_BOUNDS = {">", ">="}
# value_m may differ from max(screen_m, solver_m) by this much (rounding in the derivation).
DEFAULT_RULE_SLACK = 0.05


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--out", type=Path, help="Also write the report to this file")
    return parser.parse_args()


def interval(value) -> tuple[int, int] | None:
    if (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    ):
        return value[0], value[1]
    return None


def find_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    state: dict[str, int] = {}
    stack: list[str] = []

    def visit(node: str) -> list[str] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if nxt not in graph:
                continue
            if state.get(nxt) == 1:
                return stack[stack.index(nxt):] + [nxt]
            if nxt not in state:
                found = visit(nxt)
                if found:
                    return found
        stack.pop()
        state[node] = 2
        return None

    for node in graph:
        if node not in state:
            found = visit(node)
            if found:
                return found
    return None


def number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def blank(value) -> bool:
    return not (isinstance(value, str) and value.strip())


def solver_choice_problem(obj: dict) -> str | None:
    """Why the object's solver choice is not acceptable, or None."""
    cls, system = obj.get("simulation_class"), obj.get("blender_system")
    if cls not in PREFERRED_SYSTEMS or system not in SYSTEMS:
        return f"missing or unknown simulation_class/blender_system ({cls!r}, {system!r})"
    if system in PREFERRED_SYSTEMS[cls]:
        return None
    reason = obj.get("solver_choice_reason")
    reason = reason if isinstance(reason, dict) else {}
    evidence = reason.get("evidence")
    if blank(reason.get("reason")) or not (isinstance(evidence, list) and evidence
                                           and not any(blank(e) for e in evidence)):
        return (f"{system} is not a preferred system for {cls} ({', '.join(PREFERRED_SYSTEMS[cls])}); "
                "record solver_choice_reason with reason and evidence (measured report paths)")
    return None


def solver_deviations(manifest: dict) -> list[dict]:
    """Objects simulated by a system that is not preferred for their class, with their recorded reason."""
    out = []
    objects = manifest.get("object_contracts")
    for obj in objects if isinstance(objects, list) else []:
        if not isinstance(obj, dict):
            continue
        cls, system = obj.get("simulation_class"), obj.get("blender_system")
        if cls in PREFERRED_SYSTEMS and system in SYSTEMS and system not in PREFERRED_SYSTEMS[cls]:
            reason = obj.get("solver_choice_reason")
            reason = reason if isinstance(reason, dict) else {}
            out.append({"object": obj.get("name"), "simulation_class": cls, "blender_system": system,
                        "preferred": PREFERRED_SYSTEMS[cls], "reason": reason.get("reason"),
                        "evidence": reason.get("evidence")})
    return out


def tolerance_problems(tol) -> list[str]:
    """Why one requirements row tolerance does not state a usable basis; empty when it does."""
    if not isinstance(tol, dict) or blank(tol.get("quantity")):
        return ["not an object with a quantity"]
    problems = []
    basis = tol.get("basis")
    if basis not in TOLERANCE_BASES:
        problems.append(f"missing or unknown basis {basis!r}; one of {', '.join(TOLERANCE_BASES)}")
    if blank(tol.get("derivation")):
        problems.append("no derivation: state the calculation, the solver setting, or the request clause")
    value, ratio = tol.get("value_m"), tol.get("ratio")
    has_value, has_ratio = number(value) and value > 0, number(ratio) and ratio > 0
    if has_value == has_ratio:
        problems.append("give exactly one positive value_m or ratio")
    if has_ratio and blank(tol.get("of")):
        problems.append("ratio without of (the length it is a fraction of)")
    if basis == "numeric" and not has_ratio:
        problems.append("numeric tolerance as an absolute length; give a ratio of the object's size")
    if basis in ("screen", "solver"):
        parts = {k: tol.get(k) for k in ("screen_m", "solver_m") if number(tol.get(k))}
        if f"{basis}_m" not in parts:
            problems.append(f"basis {basis} without {basis}_m")
        elif has_value:
            default = max(parts.values())
            if not default <= value <= default * (1 + DEFAULT_RULE_SLACK):
                problems.append(f"value_m {value:g} is not max({', '.join(parts)}) = {default:g}; "
                                "a different value needs basis request")
    return problems


def audit(manifest: dict) -> list[dict]:
    errors: list[dict] = []

    def error(check: str, path: str, message: str) -> None:
        errors.append({"check": check, "path": path, "message": message})

    contract = manifest.get("contract")
    contract = contract if isinstance(contract, dict) else {}
    story = contract.get("story")
    story = story if isinstance(story, dict) else {}
    beats = story.get("beats")
    if not isinstance(beats, list) or not beats:
        error("malformed", "contract.story.beats", "missing or empty")
        beats = []

    frame_range = interval(contract.get("frame_range"))
    fps = contract.get("fps")
    fps = fps if isinstance(fps, (int, float)) and not isinstance(fps, bool) and fps > 0 else None

    # Beats
    by_id: dict[str, dict] = {}
    spans: dict[str, tuple[int, int]] = {}
    for i, beat in enumerate(beats):
        path = f"contract.story.beats[{i}]"
        if not isinstance(beat, dict) or not isinstance(beat.get("id"), str):
            error("malformed", path, "beat without a string id")
            continue
        bid = beat["id"]
        path = f"{path}({bid})"
        if bid in by_id:
            error("unique_beat_id", path, f"duplicate beat id {bid!r}")
            continue
        by_id[bid] = beat

        span = interval(beat.get("frame_interval"))
        if span is None:
            error("malformed", path + ".frame_interval", "not [start, end] integers")
            continue
        spans[bid] = span
        start, end = span
        if start > end:
            error("frame_order", path + ".frame_interval", f"start {start} > end {end}")
        if frame_range and (start < frame_range[0] or end > frame_range[1]):
            error(
                "frame_range",
                path + ".frame_interval",
                f"{list(span)} outside contract.frame_range {list(frame_range)}",
            )
        minimum = beat.get("minimum_readable_seconds")
        if fps and isinstance(minimum, (int, float)) and start <= end:
            seconds = (end - start + 1) / fps
            if seconds < minimum:
                error(
                    "readable_duration",
                    path,
                    f"{seconds:.2f} s at {fps} fps < minimum_readable_seconds {minimum}",
                )

    graph: dict[str, list[str]] = {}
    for bid, beat in by_id.items():
        deps = beat.get("depends_on", [])
        if not isinstance(deps, list):
            error("malformed", f"beat {bid}.depends_on", "not a list")
            deps = []
        graph[bid] = [d for d in deps if isinstance(d, str)]
        for dep in graph[bid]:
            if dep == bid:
                error("dependency", f"beat {bid}.depends_on", "depends on itself")
            elif dep not in by_id:
                error("dependency", f"beat {bid}.depends_on", f"unknown beat {dep!r}")
            elif bid in spans and dep in spans and spans[dep][0] >= spans[bid][0]:
                error(
                    "dependency_order",
                    f"beat {bid}.depends_on",
                    f"prerequisite {dep!r} starts at {spans[dep][0]}, not before {bid!r} at {spans[bid][0]}",
                )
    cycle = find_cycle({k: [d for d in v if d != k] for k, v in graph.items()})
    if cycle:
        error("dependency", "contract.story.beats", "cycle: " + " -> ".join(cycle))

    required = story.get("required_beat_count")
    if isinstance(required, int) and not isinstance(required, bool) and len(by_id) < required:
        error("beat_count", "contract.story", f"{len(by_id)} beats < required_beat_count {required}")

    # Objects
    objects = manifest.get("object_contracts")
    if not isinstance(objects, list) or not objects:
        error("malformed", "object_contracts", "missing or empty")
        objects = []
    names: set[str] = set()
    for i, obj in enumerate(objects):
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
            error("malformed", f"object_contracts[{i}]", "object without a string name")
            continue
        if obj["name"] in names:
            error("unique_object_name", f"object_contracts[{i}]", f"duplicate name {obj['name']!r}")
        names.add(obj["name"])
    for i, obj in enumerate(objects):
        if not isinstance(obj, dict) or not isinstance(obj.get("name"), str):
            continue
        support = obj.get("initial_support")
        if support is None:
            continue
        if not isinstance(support, str):
            error("malformed", f"object_contracts[{i}]({obj['name']}).initial_support", "not a string")
            continue
        for target in (t.strip() for t in support.split(SUPPORT_SEPARATOR)):
            if target == obj["name"]:
                error("support", f"object_contracts[{i}]({obj['name']}).initial_support", "supports itself")
            elif target not in names and target not in SUPPORT_KEYWORDS:
                error(
                    "support",
                    f"object_contracts[{i}]({obj['name']}).initial_support",
                    f"{target!r} is not an object name or one of {sorted(SUPPORT_KEYWORDS)}",
                )
        span = interval(obj.get("frame_range"))
        if span and frame_range and (span[0] < frame_range[0] or span[1] > frame_range[1]):
            error(
                "frame_range",
                f"object_contracts[{i}]({obj['name']}).frame_range",
                f"{list(span)} outside contract.frame_range {list(frame_range)}",
            )
        problem = solver_choice_problem(obj)
        if problem:
            error("solver_choice", f"object_contracts[{i}]({obj['name']})", problem)

    # Case classes and object classes agree [SKILL.md 'Solver Classification'].
    case_classes = contract.get("simulation_class")
    if isinstance(case_classes, list):
        object_classes: dict[str, list[str]] = {}
        for obj in objects:
            if isinstance(obj, dict) and obj.get("simulation_class") in PREFERRED_SYSTEMS:
                object_classes.setdefault(obj["simulation_class"], []).append(str(obj.get("name")))
        for cls in case_classes:
            if cls != COUPLED and cls not in PREFERRED_SYSTEMS:
                error("simulation_class", "contract.simulation_class", f"unknown class {cls!r}")
            elif cls != COUPLED and cls not in object_classes:
                error("simulation_class", "contract.simulation_class", f"lists {cls} but no object has that class")
        for cls, members in object_classes.items():
            if cls not in UNSIMULATED_CLASSES and cls not in case_classes:
                error("simulation_class", "contract.simulation_class",
                      f"{', '.join(members)} {'is' if len(members) == 1 else 'are'} {cls}, "
                      f"which {case_classes} does not list")

    # Topology
    topology = manifest.get("topology")
    topology = topology if isinstance(topology, dict) else {}
    regions = topology.get("regions")
    if not isinstance(regions, list) or not regions:
        error("malformed", "topology.regions", "missing or empty")
        regions = []
    region_ids: set[str] = set()
    for i, region in enumerate(regions):
        rid = region.get("id") if isinstance(region, dict) else None
        if not isinstance(rid, str):
            error("malformed", f"topology.regions[{i}]", "region without a string id")
        elif rid in region_ids:
            error("unique_region_id", f"topology.regions[{i}]", f"duplicate region id {rid!r}")
        else:
            region_ids.add(rid)
    edges = topology.get("edges", [])
    for i, edge in enumerate(edges if isinstance(edges, list) else []):
        if not (isinstance(edge, list) and len(edge) == 3 and all(isinstance(e, str) for e in edge)):
            error("malformed", f"topology.edges[{i}]", "not [from, connection, to]")
            continue
        for end in (edge[0], edge[2]):
            if end not in region_ids:
                error("topology", f"topology.edges[{i}]", f"unknown region {end!r}")

    # Requirements rows, once the matrix exists (the gate reports a missing one).
    rows = manifest.get("requirements_matrix")
    for i, row in enumerate(rows if isinstance(rows, list) else []):
        if not isinstance(row, dict):
            continue
        path = f"requirements_matrix[{i}]({row.get('id')})"
        tolerances, measures = row.get("tolerances"), row.get("measures")
        if not (isinstance(tolerances, list) and tolerances) and not (isinstance(measures, list) and measures):
            error("measurable", path, "names no measured quantity (tolerances or measures); a visual "
                  "judgement nobody can measure is not a row, review it in the render")
        for j, tol in enumerate(tolerances if isinstance(tolerances, list) else []):
            for problem in tolerance_problems(tol):
                error("tolerance_basis", f"{path}.tolerances[{j}]", problem)
        for j, measure in enumerate(measures if isinstance(measures, list) else []):
            if not (isinstance(measure, dict) and not blank(measure.get("quantity"))
                    and measure.get("comparison") in COMPARISONS and "value" in measure):
                error("malformed", f"{path}.measures[{j}]", "not {quantity, comparison, value}")
            elif measure.get("unit") in LENGTH_UNITS and measure["comparison"] not in LOWER_BOUNDS:
                error("tolerance_basis", f"{path}.measures[{j}]",
                      f"{measure['comparison']} {measure['value']} {measure['unit']} is a length tolerance; "
                      "state it under tolerances with its basis")

    return errors


def evidence_errors(manifest: dict, case_dir: Path) -> list[dict]:
    """solver_choice_reason.evidence paths that are not existing files or folders inside the case."""
    errors = []
    objects = manifest.get("object_contracts")
    for i, obj in enumerate(objects if isinstance(objects, list) else []):
        reason = obj.get("solver_choice_reason") if isinstance(obj, dict) else None
        evidence = reason.get("evidence") if isinstance(reason, dict) else None
        for j, item in enumerate(evidence if isinstance(evidence, list) else []):
            if blank(item):
                continue
            path = (case_dir / item).resolve()
            if not path.is_relative_to(case_dir) or not path.exists():
                errors.append({"check": "solver_choice",
                               "path": f"object_contracts[{i}]({obj.get('name')}).solver_choice_reason.evidence[{j}]",
                               "message": f"{item!r} is not an existing path inside the case"})
    return errors


def freeze(case_dir: Path, manifest: dict) -> dict:
    """Keep the first spec that passes the audit; return where it is and whether the current spec matches."""
    path = case_dir / BASELINE
    digest = case_trace.spec_hash(manifest)
    if path.is_file():
        try:
            frozen = json.loads(path.read_text(encoding="utf-8")).get("spec_hash")
        except (OSError, ValueError, AttributeError):
            frozen = None
        return {"path": BASELINE.as_posix(), "created": False, "matches": frozen == digest}
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"spec_hash": digest, "frozen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "spec": case_trace.spec_part(manifest)}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    case_trace.record(case_dir, {"type": "spec_frozen", "baseline": BASELINE.as_posix()}, manifest)
    return {"path": BASELINE.as_posix(), "created": True, "matches": True}


def main() -> None:
    args = parse_args()
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"pass": False, "error": f"cannot read manifest: {exc}"}), file=sys.stderr)
        raise SystemExit(2)
    if not isinstance(manifest, dict):
        print(json.dumps({"pass": False, "error": "manifest is not a JSON object"}), file=sys.stderr)
        raise SystemExit(2)

    errors = audit(manifest)
    if args.manifest.name == "manifest.json":
        errors += evidence_errors(manifest, args.manifest.resolve().parent)
    report = {"pass": not errors, "manifest": str(args.manifest.resolve()), "errors": errors}
    if report["pass"] and args.manifest.name == "manifest.json":
        report["spec_baseline"] = freeze(args.manifest.resolve().parent, manifest)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
