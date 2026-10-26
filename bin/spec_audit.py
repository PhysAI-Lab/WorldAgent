#!/usr/bin/env python3
"""Audit the world spec in a case manifest.json before construction.

Checks the cross-references a JSON schema cannot express [paper A.1.1
'Specification audit'; skill.md Stage 0]:

- beat ids are unique, intervals are ordered and inside the contract frame range
- each beat lasts at least its minimum readable duration at the contract FPS
- contract.frame_range lasts at least story.minimum_duration_seconds, and at least the 12 s
  long-form minimum when story.long_form is true and the request states no duration
  (story.requested_duration_seconds) [simulation-workflow.en.md 1.2]
- depends_on names existing beats, has no cycles, and prerequisites start first
- the beat count reaches required_beat_count
- object names are unique and every initial_support resolves (an object released in free
  fall has none: the field is omitted)
- topology region ids are unique and every edge joins existing regions
- every object names its simulation_class and blender_system, and a system that is not
  preferred for the class (schemas/solver_classes.json) has a solver_choice_reason with a
  reason and evidence [SKILL.md 'Solver Classification']; the object classes and
  contract.simulation_class agree
- when the manifest has a requirements_matrix: every row names a measured quantity
  (tolerances or measures), and every distance, penetration, position or error
  tolerance states its basis and derivation. Default rule: max(5 px at the reference
  resolution at that distance, the solver's resolution); smaller only on request; numeric
  tolerances as a ratio of object size; basis outcome for a threshold taken from an outcome
  the request states, which may be looser than the default rule but not tighter (an outcome
  value_m also gives screen_m and solver_m and is at least their max). The reference resolution is the final render
  resolution scaled up, aspect kept, to a longer side of at least 1920 px; a screen
  derivation that names only smaller resolutions is rejected
- when the manifest has a requirements_matrix: every beat is covered by a row, and no row's
  covers names an unknown beat (story_coverage, shared with the gate)
- when objects are simulated by two or more Blender systems, or contract.simulation_class
  lists coupled: topology.solver_graph exists (solver_graph_required); each solver node's
  objects share one simulating blender_system and every simulated object is in exactly one
  solver node, input nodes name kinematic contracts or allowed_inputs (solver_node); each
  edge joins its nodes through a channel schemas/coupling_channels.json lists for their
  systems (solver_edge); approximations, scripted transfers, omitted reactions and Fluid
  Flow carriers are stated (coupling_disclosure); bake_order lists every node once, drivers
  first, without cycles (bake_order); an object resting on another node's object has an edge
  between the two nodes (coupling_support); every beat interaction is provided by an edge or
  marked achievable: false with a reason (coupling_claim). coupling_summary() lists the
  approximations, not-achievable claims and omitted reactions for review (report `coupling`;
  the gate and promote show it); it never changes the verdict

Shape is checked by schemas/world_spec.schema.json; this script only reports
malformed parts it cannot read. Standard library only.

The first time the audit passes on a case's manifest.json with a non-empty
requirements_matrix (so every beat is covered), the spec is frozen: the spec part, its hash
and each row's id, pass_predicate, tolerances and measures go to
<case>/reports/spec_baseline.json and the case trace gets a `spec_frozen` event. A passing
run without a matrix freezes nothing. Later runs never overwrite the baseline. The gate and
promote compare the current spec and rows with it and list every change for review, whether
or not any row was measured in between.

Usage: spec_audit.py <manifest.json> [--out report.json]
Exit: 0 pass, 1 audit errors, 2 unreadable manifest.
"""

from __future__ import annotations

import argparse
import json
import re
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
# What one Blender system can pass to another [material-playbooks.en.md section 7].
CHANNELS = json.loads((Path(__file__).resolve().parent.parent / "schemas" / "coupling_channels.json")
                      .read_text(encoding="utf-8"))
INPUT_KINDS = tuple(CHANNELS["inputs"])
# Channels whose driven solver does not read the driver's output, so bake order does not matter.
UNORDERED_CHANNELS = {c["channel"] for c in CHANNELS["channels"] if c["order"] == "none"}
SCRIPTED_TRANSFER = "scripted_transfer"
FIELD_FLOW = "field_flow"
FLUID_SYSTEMS = {"flip_fluids", "mantaflow_liquid", "mantaflow_gas"}

TOLERANCE_BASES = ("screen", "solver", "numeric", "request", "outcome")
COMPARISONS = {"<", "<=", ">", ">=", "==", "!=", "within"}
# A measure in one of these units bounded from above (or equal) is a tolerance and needs a basis.
LENGTH_UNITS = {"m", "cm", "mm", "um", "µm", "μm", "nm"}
LOWER_BOUNDS = {">", ">="}
# value_m may differ from max(screen_m, solver_m) by this much (rounding in the derivation).
DEFAULT_RULE_SLACK = 0.05
# screen_m is taken at the final resolution scaled up to at least this longer side, so a low
# test resolution does not loosen tolerances.
REFERENCE_LONG_SIDE_PX = 1920
RESOLUTION = re.compile(r"\b(\d{2,5})\s*[x×]\s*(\d{2,5})\b")
# Long-form floor when the request states no duration [simulation-workflow.en.md 1.2].
LONG_FORM_MINIMUM_SECONDS = 12
# Row fields frozen with the spec: what the row measures and its thresholds.
FROZEN_ROW_KEYS = ("id", "pass_predicate", "tolerances", "measures")


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
    if basis == "outcome" and has_value:
        # An outcome may loosen a length bound past the default rule, never tighten it.
        missing = [k for k in ("screen_m", "solver_m") if not number(tol.get(k))]
        if missing:
            problems.append(f"basis outcome on a length without {' and '.join(missing)}; give both so the "
                            "audit can check value_m against the default rule")
        else:
            default = max(tol["screen_m"], tol["solver_m"])
            if value * (1 + DEFAULT_RULE_SLACK) < default:
                problems.append(f"basis outcome value_m {value:g} is tighter than max(screen_m, solver_m) = "
                                f"{default:g}; an outcome may be looser than the default rule, never tighter. "
                                "A tighter value needs basis request")
    if number(tol.get("screen_m")) and not blank(tol.get("derivation")):
        sizes = [(int(w), int(h)) for w, h in RESOLUTION.findall(tol["derivation"])]
        if sizes and max(max(size) for size in sizes) < REFERENCE_LONG_SIDE_PX:
            named = ", ".join(f"{w}x{h}" for w, h in sizes)
            problems.append(f"screen_m taken at {named}; take 5 px at the reference resolution, the final "
                            f"resolution scaled up to a longer side of at least {REFERENCE_LONG_SIDE_PX} px")
    return problems


def story_coverage(manifest: dict, rows: list) -> tuple[dict, list[str], list[dict], list[str]]:
    """Return (coverage {beat: [row ids]}, missing_beats, unknown_covers, errors) for the story beats.

    A row covers the beats in its `covers` list; a row without `covers` covers the beat whose
    id equals its own id, if any. Used by audit() and by bin/gate.py.
    """
    contract = manifest.get("contract")
    story = contract.get("story") if isinstance(contract, dict) else None
    beats = story.get("beats") if isinstance(story, dict) else None
    beat_ids = [b["id"] for b in beats or [] if isinstance(b, dict) and isinstance(b.get("id"), str)]
    coverage: dict[str, list[str]] = {bid: [] for bid in beat_ids}
    unknown: list[dict] = []
    errors: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        rid = row.get("id")
        if "covers" not in row:
            covers = [rid] if rid in coverage else []
        elif isinstance(row["covers"], list) and all(isinstance(c, str) and c for c in row["covers"]):
            covers = row["covers"]
        else:
            errors.append(f"row {rid!r} covers is not a list of beat ids")
            continue
        for bid in covers:
            if bid in coverage:
                if rid not in coverage[bid]:
                    coverage[bid].append(rid)
            else:
                unknown.append({"row": rid, "beat": bid})
    missing = [bid for bid, covering in coverage.items() if not covering]
    if missing:
        errors.append("story beats without a requirements_matrix row: " + ", ".join(missing))
    if unknown:
        errors.append("covers names beats that do not exist: "
                      + ", ".join(f"{u['row']} -> {u['beat']}" for u in unknown))
    return coverage, missing, unknown, errors


def channel_entries(channel, driver: str | None, driven: str | None) -> list[dict]:
    """Rows of schemas/coupling_channels.json for channel from driver to driven (system or input kind)."""
    return [c for c in CHANNELS["channels"] if c["channel"] == channel
            and (driver in c["from"] or "any" in c["from"]) and (driven in c["to"] or "any" in c["to"])]


def channels_between(driver: str | None, driven: str | None) -> list[str]:
    """Channel names the table lists from driver to driven, without the generic ones (any -> any)."""
    return sorted({c["channel"] for c in CHANNELS["channels"] if c["from"] != ["any"]
                   and (driver in c["from"] or "any" in c["from"]) and (driven in c["to"] or "any" in c["to"])})


def _names(value) -> list[str]:
    return [v for v in value if isinstance(v, str) and v] if isinstance(value, list) else []


def _support_targets(obj: dict) -> list[str]:
    support = obj.get("initial_support")
    return [t.strip() for t in support.split(SUPPORT_SEPARATOR)] if isinstance(support, str) else []


def has_field_strength(edge: dict) -> bool:
    settings = edge.get("settings")
    return isinstance(settings, dict) and any(
        (k == "field.flow" or k.endswith(".field.flow")) and number(v) for k, v in settings.items())


def solver_graph(manifest: dict, error=None) -> dict | None:
    """Read topology.solver_graph and check it (error(check, path, message) per problem).

    Returns None when the manifest has no graph; otherwise {"nodes": {id: node}, "edges": [edge],
    "claims": [claim]} with each node's derived `system` (input kind for input nodes) and each
    edge's and claim's table lookup, for coupling_summary() and the checks below.
    """
    error = error or (lambda check, path, message: None)
    objects = [o for o in manifest.get("object_contracts") or [] if isinstance(o, dict)] \
        if isinstance(manifest.get("object_contracts"), list) else []
    contracts = {o["name"]: o for o in objects if isinstance(o.get("name"), str)}
    index = {o["name"]: i for i, o in enumerate(manifest.get("object_contracts") or [])
             if isinstance(o, dict) and isinstance(o.get("name"), str)}
    contract = manifest.get("contract") if isinstance(manifest.get("contract"), dict) else {}
    story = contract.get("story") if isinstance(contract.get("story"), dict) else {}
    beats = story.get("beats") if isinstance(story.get("beats"), list) else []
    case_classes = contract.get("simulation_class") if isinstance(contract.get("simulation_class"), list) else []
    simulated = {name: o.get("blender_system") for name, o in contracts.items()
                 if o.get("simulation_class") in PREFERRED_SYSTEMS and o.get("simulation_class") not in UNSIMULATED_CLASSES
                 and o.get("blender_system") in SYSTEMS and o.get("blender_system") != "none"}
    topology = manifest.get("topology") if isinstance(manifest.get("topology"), dict) else {}
    graph = topology.get("solver_graph")
    root = "topology.solver_graph"

    # solver_graph_required
    systems = sorted(set(simulated.values()))
    if graph is None:
        reasons = []
        if len(systems) >= 2:
            reasons.append(f"objects are simulated by {len(systems)} Blender systems ({', '.join(systems)})")
        if COUPLED in case_classes:
            reasons.append("contract.simulation_class lists coupled")
        if reasons:
            error("solver_graph_required", root, "; ".join(reasons) + "; write topology.solver_graph (one node "
                  "per solver bake, one edge per one-way transfer, bake_order)")
        return None
    if not isinstance(graph, dict):
        error("malformed", root, "not an object")
        return None

    inputs_listed = {name for owner in [*objects, *(b for b in beats if isinstance(b, dict))]
                     for name in _names(owner.get("allowed_inputs"))}

    # solver_node
    nodes: dict[str, dict] = {}
    for i, node in enumerate(graph.get("nodes") if isinstance(graph.get("nodes"), list) else []):
        path = f"{root}.nodes[{i}]"
        if not (isinstance(node, dict) and isinstance(node.get("id"), str) and node["id"]):
            error("malformed", path, "node without a string id")
            continue
        nid = node["id"]
        path = f"{path}({nid})"
        if nid in nodes:
            error("solver_node", path, f"duplicate node id {nid!r}")
            continue
        members = _names(node.get("objects"))
        if not members:
            error("solver_node", path, "objects is missing or empty")
        kind = node.get("input")
        info = {"id": nid, "input": kind, "objects": members, "system": None,
                "domain": node.get("domain") if isinstance(node.get("domain"), str) else None, "path": path}
        if kind is not None:
            if kind not in INPUT_KINDS:
                error("solver_node", path, f"unknown input {kind!r}; one of {', '.join(INPUT_KINDS)}")
            else:
                info["system"] = kind
            for name in members:
                c = contracts.get(name)
                if not ((c and c.get("simulation_class") == "kinematic") or name in inputs_listed):
                    error("solver_node", path, f"input {name!r} is not a kinematic object contract and is not "
                          "listed in any allowed_inputs")
        else:
            found: dict[str, list[str]] = {}
            for name in members:
                c = contracts.get(name)
                if c is None:
                    error("solver_node", path, f"{name!r} is not in object_contracts")
                elif name not in simulated:
                    error("solver_node", path, "objects of one solver node must share one simulating blender_system; "
                          f"{name} is {c.get('blender_system')}/{c.get('simulation_class')}")
                else:
                    found.setdefault(simulated[name], []).append(name)
            if len(found) > 1:
                error("solver_node", path, "objects of one solver node must share one simulating blender_system; "
                      + "; ".join(f"{', '.join(n)} {'is' if len(n) == 1 else 'are'} {s}" for s, n in found.items()))
            elif found:
                info["system"] = next(iter(found))
            if info["system"] in FLUID_SYSTEMS and not info["domain"] and len(members) > 1:
                error("solver_node", path, "fluid node needs domain (the domain object's name) when objects are not "
                      "the domain")
        nodes[nid] = info

    owner: dict[str, list[str]] = {}
    for nid, info in nodes.items():
        if info["input"] is None:
            for name in info["objects"]:
                owner.setdefault(name, [])
                if nid not in owner[name]:
                    owner[name].append(nid)
    for name, system in simulated.items():
        path = f"object_contracts[{index.get(name)}]({name})"
        if not owner.get(name):
            error("solver_node", path, f"{name} ({system}) is in no solver node")
        elif len(owner[name]) > 1:
            error("solver_node", path, f"{name} is owned by nodes {' and '.join(owner[name])}; one solver owns an "
                  "output and bakes it once")
    solver_of = {name: nids[0] for name, nids in owner.items() if len(nids) == 1}

    def in_node(name, info) -> bool:
        return name in info["objects"] or name == info["domain"]

    # solver_edge, coupling_disclosure
    edges: list[dict] = []
    edge_ids: set[str] = set()
    for i, edge in enumerate(graph.get("edges") if isinstance(graph.get("edges"), list) else []):
        path = f"{root}.edges[{i}]"
        if not (isinstance(edge, dict) and isinstance(edge.get("id"), str) and edge["id"]):
            error("malformed", path, "edge without a string id")
            continue
        eid = edge["id"]
        path = f"{path}({eid})"
        if eid in edge_ids:
            error("solver_edge", path, f"duplicate edge id {eid!r}")
            continue
        edge_ids.add(eid)
        a, b, channel = edge.get("from"), edge.get("to"), edge.get("channel")
        unknown = [end for end in (a, b) if end not in nodes]
        for end in unknown:
            error("solver_edge", path, f"unknown node {end!r}")
        if unknown:
            continue
        if a == b:
            error("solver_edge", path, "from and to are the same node")
            continue
        fa, fb = nodes[a], nodes[b]
        for name in _names(edge.get("objects")):
            if not in_node(name, fa):
                error("solver_edge", path, f"{name!r} is not an object of node {a}")
        for name in _names(edge.get("targets")):
            if not in_node(name, fb):
                error("solver_edge", path, f"{name!r} is not an object of node {b}")
        entries = channel_entries(channel, fa["system"], fb["system"])
        if fa["system"] and fb["system"] and not entries:
            listed = channels_between(fa["system"], fb["system"])
            error("solver_edge", path, f"Blender provides no {channel} from {fa['system']} to {fb['system']} "
                  f"(schemas/coupling_channels.json; listed: {', '.join(listed) or 'none'}); use a listed channel, "
                  "declare a scripted_transfer with method and assumptions, or mark the claims achievable: false")
        approximated = any(e["approximation"] for e in entries)
        if approximated and blank(edge.get("approximation")):
            error("coupling_disclosure", path, f"{channel} is an approximation; state what it approximates and how "
                  "in approximation")
        if channel == SCRIPTED_TRANSFER and (blank(edge.get("method")) or not _names(edge.get("assumptions"))):
            error("coupling_disclosure", path, "scripted_transfer needs method (script path and what it computes) "
                  "and assumptions")
        omitted = [r for r in _names(edge.get("omitted_reactions")) if r.strip()]
        if fa["input"] is None and channel not in UNORDERED_CHANNELS and not omitted:
            error("coupling_disclosure", path, f"list what {b} does not push back on {a} in omitted_reactions")
        if channel == FIELD_FLOW and not (_names(edge.get("via")) and has_field_strength(edge)):
            error("coupling_disclosure", path, "field_flow needs the Fluid Flow field object in via and its strength "
                  "in settings (field.flow)")
        edges.append({"id": eid, "from": a, "to": b, "channel": channel, "path": path,
                      "provides": sorted({k for e in entries for k in e["provides"]}),
                      "approximated": approximated, "targets": _names(edge.get("targets")),
                      "driver_first": channel not in UNORDERED_CHANNELS, "input_driver": fa["input"] is not None,
                      "approximation": edge.get("approximation"), "method": edge.get("method"),
                      "assumptions": _names(edge.get("assumptions")), "omitted_reactions": omitted})

    # solver_graph_required: a transfer between two solvers is a coupled case.
    if COUPLED not in case_classes:
        for e in edges:
            if nodes[e["from"]]["input"] is None and nodes[e["to"]]["input"] is None:
                error("solver_graph_required", e["path"], f"solver_graph couples {e['from']} and {e['to']}; add "
                      "coupled to contract.simulation_class")
                break

    # bake_order
    order = graph.get("bake_order")
    position: dict[str, int] = {}
    if isinstance(order, list):
        listed = [n for n in order if isinstance(n, str)]
        missing = [n for n in nodes if n not in listed]
        extra = sorted({n for n in listed if n not in nodes})
        repeated = sorted({n for n in listed if listed.count(n) > 1})
        if missing or extra or repeated:
            parts = [f"{label} {', '.join(names)}" for label, names in
                     (("missing", missing), ("extra", extra), ("repeated", repeated)) if names]
            error("bake_order", f"{root}.bake_order", "bake_order must list every node once: " + "; ".join(parts))
        for n in listed:
            position.setdefault(n, len(position))
    else:
        error("malformed", f"{root}.bake_order", "not a list of node ids")
    for e in edges:
        if e["driver_first"] and e["from"] in position and e["to"] in position \
                and position[e["to"]] < position[e["from"]]:
            error("bake_order", e["path"], f"{e['to']} is baked before its driver {e['from']} ({e['channel']} reads "
                  "the driver's baked output)")
    cycle = find_cycle({nid: [e["to"] for e in edges if e["from"] == nid and e["driver_first"]] for nid in nodes})
    if cycle:
        error("bake_order", f"{root}.edges", f"cycle {' -> '.join(cycle)}: solvers bake one after the other, so "
              "two cannot drive each other; keep one direction and list the other in omitted_reactions")

    # coupling_support
    def joined(x, y) -> bool:
        return any({e["from"], e["to"]} == {x, y} for e in edges)

    for name, obj in contracts.items():
        for target in _support_targets(obj):
            na, nb = solver_of.get(name), solver_of.get(target)
            if na and nb and na != nb and not joined(na, nb):
                error("coupling_support", f"object_contracts[{index.get(name)}]({name}).initial_support",
                      f"{name} ({simulated[name]}) rests on {target} ({simulated[target]}) with no edge between "
                      f"their nodes {na} and {nb}")

    # coupling_claim
    claims: list[dict] = []
    for i, beat in enumerate(beats):
        if not isinstance(beat, dict) or not isinstance(beat.get("interactions"), list):
            continue
        for j, claim in enumerate(beat["interactions"]):
            path = f"contract.story.beats[{i}]({beat.get('id')}).interactions[{j}]"
            if not isinstance(claim, dict):
                error("malformed", path, "not {cause, effect, kind}")
                continue
            cause, effect, kind = claim.get("cause"), claim.get("effect"), claim.get("kind")
            if cause not in nodes:
                error("coupling_claim", path, f"cause {cause!r} is not a node")
                continue
            target = solver_of.get(effect)
            if target is None or target == cause:
                error("coupling_claim", path, f"effect {effect!r} is in no node other than {cause}")
                continue
            reaching = [e for e in edges if e["from"] == cause and e["to"] == target
                        and (not e["targets"] or effect in e["targets"] or nodes[target]["domain"] in e["targets"])]
            providing = [e for e in reaching if kind in e["provides"]]
            record = {"beat": beat.get("id"), "cause": cause, "effect": effect, "kind": kind,
                      "edges": [e["id"] for e in providing],
                      "approximated": bool(providing) and all(e["approximated"] for e in providing)}
            if claim.get("achievable") is False:
                record.update(achievable=False, reason=claim.get("reason"))
                if blank(claim.get("reason")):
                    error("coupling_claim", path, "achievable false needs a reason")
                if providing:
                    error("coupling_claim", path, f"{providing[0]['id']} provides {kind}; remove achievable: false")
            elif not reaching:
                listed = channels_between(nodes[cause]["system"], nodes[target]["system"])
                error("coupling_claim", path, f"no edge from {cause} to {target}; Blender channels for "
                      f"{nodes[cause]['system']} -> {nodes[target]['system']}: {', '.join(listed) or 'none'}")
            elif not providing:
                kinds = sorted({k for e in reaching for k in e["provides"]})
                error("coupling_claim", path, f"{', '.join(e['id'] for e in reaching)} provide "
                      f"{', '.join(kinds) or 'nothing'}; {kind} needs another edge or achievable: false with reason")
            claims.append(record)
    return {"nodes": nodes, "edges": edges, "claims": claims}


def coupling_summary(manifest: dict) -> dict | None:
    """Approximations, approximated and not-achievable claims, and omitted reactions of the graph, for review.

    None when the manifest has no topology.solver_graph. Never changes a verdict.
    """
    graph = solver_graph(manifest)
    if graph is None:
        return None
    return {
        "approximations": [{"edge": e["id"], "channel": e["channel"], "from": e["from"], "to": e["to"],
                            "approximation": e["approximation"], "method": e["method"],
                            "assumptions": e["assumptions"]} for e in graph["edges"] if e["approximated"]],
        "approximated_claims": [{k: c[k] for k in ("beat", "cause", "effect", "kind", "edges")}
                                for c in graph["claims"] if c.get("achievable") is not False and c["approximated"]],
        "not_achievable": [{k: c[k] for k in ("beat", "cause", "effect", "kind", "reason")}
                           for c in graph["claims"] if c.get("achievable") is False],
        "omitted_reactions": [{"edge": e["id"], "from": e["from"], "to": e["to"], "omitted": e["omitted_reactions"]}
                              for e in graph["edges"] if e["omitted_reactions"] and not e["input_driver"]],
    }


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

    # Total duration [simulation-workflow.en.md 1.2]: a duration the request states takes
    # precedence over the long-form floor; minimum_duration_seconds always has to fit.
    if fps and frame_range and frame_range[0] <= frame_range[1]:
        seconds = (frame_range[1] - frame_range[0] + 1) / fps
        minimum = story.get("minimum_duration_seconds")
        if number(minimum) and seconds < minimum:
            error("duration", "contract.story.minimum_duration_seconds",
                  f"frame_range {list(frame_range)} is {seconds:.2f} s at {fps} fps "
                  f"< minimum_duration_seconds {minimum}")
        if (story.get("long_form") is True and not number(story.get("requested_duration_seconds"))
                and seconds < LONG_FORM_MINIMUM_SECONDS):
            error("duration", "contract.story.long_form",
                  f"frame_range {list(frame_range)} is {seconds:.2f} s at {fps} fps < the "
                  f"{LONG_FORM_MINIMUM_SECONDS} s long-form minimum; the request states no duration "
                  "(requested_duration_seconds)")

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

    # Coupling graph [paper A.1.2; material-playbooks.en.md section 7].
    solver_graph(manifest, error)

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
                      "state it under tolerances with its basis (basis outcome when the threshold comes "
                      "from an outcome the request states)")
    # Beat coverage [simulation-workflow.en.md 1.2 empty missing_beats], same rule as the gate.
    if isinstance(rows, list) and rows:
        beat_index = {b.get("id"): i for i, b in enumerate(beats) if isinstance(b, dict)}
        _, missing, unknown, _ = story_coverage(manifest, rows)
        for bid in missing:
            error("beat_coverage", f"contract.story.beats[{beat_index.get(bid)}]({bid})",
                  "no requirements_matrix row covers this beat; name it in a row's covers")
        row_index = {r.get("id"): i for i, r in enumerate(rows) if isinstance(r, dict)}
        for u in unknown:
            error("beat_coverage", f"requirements_matrix[{row_index.get(u['row'])}]({u['row']}).covers",
                  f"names beat {u['beat']!r}, which contract.story.beats does not have")
        for i, row in enumerate(rows):
            if isinstance(row, dict) and "covers" in row and not (
                    isinstance(row["covers"], list) and all(isinstance(c, str) and c for c in row["covers"])):
                error("malformed", f"requirements_matrix[{i}]({row.get('id')}).covers", "not a list of beat ids")

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


def method_errors(manifest: dict, case_dir: Path) -> list[dict]:
    """scripted_transfer methods whose script path is not an existing file inside the case."""
    errors = []
    topology = manifest.get("topology") if isinstance(manifest.get("topology"), dict) else {}
    graph = topology.get("solver_graph") if isinstance(topology.get("solver_graph"), dict) else {}
    for i, edge in enumerate(graph.get("edges") if isinstance(graph.get("edges"), list) else []):
        if not (isinstance(edge, dict) and edge.get("channel") == SCRIPTED_TRANSFER) or blank(edge.get("method")):
            continue
        script = edge["method"].split(":", 1)[0].strip()
        path = (case_dir / script).resolve()
        if not path.is_relative_to(case_dir) or not path.is_file():
            errors.append({"check": "coupling_disclosure",
                           "path": f"topology.solver_graph.edges[{i}]({edge.get('id')}).method",
                           "message": f"{script!r} is not an existing file inside the case; start method with the "
                                      "case-relative script path"})
    return errors


def frozen_rows(manifest: dict) -> list[dict]:
    """The part of each requirements row that is frozen with the spec."""
    rows = manifest.get("requirements_matrix")
    return [{k: row.get(k) for k in FROZEN_ROW_KEYS} for row in rows if isinstance(row, dict)] \
        if isinstance(rows, list) else []


def rows_diff(old: list, new: list) -> dict:
    """Row ids added, removed, or with a changed pass_predicate, tolerances or measures."""
    a = {r.get("id"): r for r in old if isinstance(r, dict)}
    b = {r.get("id"): r for r in new if isinstance(r, dict)}
    part = {"added": sorted(map(str, set(b) - set(a))), "removed": sorted(map(str, set(a) - set(b))),
            "changed": sorted(str(k) for k in set(a) & set(b) if a[k] != b[k])}
    return part if any(part.values()) else {}


def freeze(case_dir: Path, manifest: dict) -> dict:
    """Keep the first passing spec with its rows; return where it is and whether the current spec matches.

    Nothing is frozen while the manifest has no requirements_matrix rows.
    """
    path = case_dir / BASELINE
    digest = case_trace.spec_hash(manifest)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            matches = data.get("spec_hash") == digest
            if "rows" in data:  # baselines written before rows were frozen compare the spec part only
                matches = matches and not rows_diff(data["rows"], frozen_rows(manifest))
        except (OSError, ValueError, AttributeError, TypeError):
            matches = False
        return {"path": BASELINE.as_posix(), "created": False, "matches": matches}
    rows = manifest.get("requirements_matrix")
    if not (isinstance(rows, list) and rows):
        return {"path": None, "created": False, "matches": None,
                "problem": "not frozen: no requirements_matrix yet; the spec is frozen by the first "
                           "passing audit with the matrix"}
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"spec_hash": digest, "frozen_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "spec": case_trace.spec_part(manifest), "rows": frozen_rows(manifest)}
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
        errors += method_errors(manifest, args.manifest.resolve().parent)
    report = {"pass": not errors, "manifest": str(args.manifest.resolve()), "errors": errors,
              "coupling": coupling_summary(manifest)}
    if report["pass"] and args.manifest.name == "manifest.json":
        report["spec_baseline"] = freeze(args.manifest.resolve().parent, manifest)
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
