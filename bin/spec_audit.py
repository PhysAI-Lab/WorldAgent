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

Shape is checked by schemas/world_spec.schema.json; this script only reports
malformed parts it cannot read. Standard library only.

Usage: spec_audit.py <manifest.json> [--out report.json]
Exit: 0 pass, 1 audit errors, 2 unreadable manifest.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support targets that are not objects [non-penetration-contract.en.md support chain].
SUPPORT_KEYWORDS = {"foundation", "ground"}
# Several supports may be listed in one string, e.g. "wall_a|wall_b".
SUPPORT_SEPARATOR = "|"


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

    return errors


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
    report = {"pass": not errors, "manifest": str(args.manifest.resolve()), "errors": errors}
    text = json.dumps(report, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    raise SystemExit(0 if report["pass"] else 1)


if __name__ == "__main__":
    main()
