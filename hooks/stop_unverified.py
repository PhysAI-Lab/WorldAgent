#!/usr/bin/env python3
"""Stop hook: do not let the agent report a case as done before promote validated it.

When the agent's last message claims completion (a completion word that is not negated) and a case touched in this session
(its manifest.json changed since the session started) is not `validated`, the stop
is blocked once (exit 2) with the case's non-passing rows, so the agent either runs
promote or tells the user plainly what is still unverified. A second stop in a row
is always allowed (stop_hook_active), so this never loops.

Cases are found under the session's working directory (depth <= 4; hidden folders and
.work/ skipped) as manifest.json files with a requirements_matrix or contract.
The check reads manifests only; it does not hash inputs or run Blender. Standard library only.
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from worldagent_runtime import read_object, session_path

MAX_DEPTH = 4
SKIP = {"node_modules", "__pycache__", "venv", "site-packages", "cache", "caches"}
# Words that claim the work is finished (English and Korean).
CLAIM = re.compile(
    r"\b(done|complete[ds]?|finished|validated|delivered|succeeded|"
    r"all (checks|rows|requirements|tests) pass(ed|es)?)\b|완료|완성|끝났|마쳤|통과했",
    re.IGNORECASE,
)
# A claim word right after these (English) or right before these (Korean) is negated.
NEGATED_BEFORE = re.compile(r"\b(not|n't|never|no longer|yet to be|isn't|aren't|wasn't)\W+(\w+\W+){0,2}$", re.IGNORECASE)
NEGATED_AFTER = re.compile(r"^\S{0,3}\s*(안|못|않|아니|되지)")


def claims_done(text: str) -> bool:
    """True when some completion word in `text` is not negated."""
    for m in CLAIM.finditer(text):
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 8]
        if NEGATED_BEFORE.search(before) or NEGATED_AFTER.search(after):
            continue
        return True
    return False


def last_assistant_text(event: dict) -> str:
    text = event.get("last_assistant_message")
    if isinstance(text, str):
        return text
    path = event.get("transcript_path")
    if not path or not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if entry.get("type") != "assistant":
            continue
        content = (entry.get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        texts = [c.get("text", "") for c in content or [] if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return "\n".join(texts)
    return ""


def session_start(event: dict) -> float:
    """SessionStart marker; legacy transcript fallback; 0 if both are unavailable.

    File times are not used: Linux has no birth time, and ctime changes on every append.
    """
    session_id = event.get("session_id")
    if (os.environ.get("PLUGIN_ROOT") or not event.get("transcript_path")) and isinstance(session_id, str) and session_id:
        stamp = read_object(session_path(session_id)).get("started_at")
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp) and stamp > 0:
            return float(stamp)
    path = event.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 0.0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                stamp = json.loads(line).get("timestamp")
            except (ValueError, AttributeError):
                continue
            if isinstance(stamp, str):
                try:
                    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
    return 0.0


def cases(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        if "manifest.json" in filenames:
            yield Path(dirpath) / "manifest.json"
            dirnames[:] = []  # a case folder holds no further cases
            continue
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP and depth < MAX_DEPTH]


def open_rows(manifest: dict) -> list[str]:
    rows = manifest.get("requirements_matrix")
    if not isinstance(rows, list) or not rows:
        return ["no requirements_matrix rows"]
    return [f"{r.get('id')}: {r.get('status')}" for r in rows if isinstance(r, dict) and r.get("status") != "pass"]


def main() -> None:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)
    if not isinstance(event, dict):
        sys.exit(0)
    if event.get("stop_hook_active"):
        sys.exit(0)
    if not claims_done(last_assistant_text(event)):
        sys.exit(0)
    root = Path(event.get("cwd") or os.getcwd())
    since = session_start(event)
    problems = []
    for path in cases(root):
        try:
            if path.stat().st_mtime < since:
                continue
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(manifest, dict) or not ("requirements_matrix" in manifest or "contract" in manifest):
            continue
        if manifest.get("status") == "validated":
            continue
        rows = open_rows(manifest)
        detail = "; ".join(rows[:10]) + (f"; and {len(rows) - 10} more" if len(rows) > 10 else "")
        problems.append(f"- {path.parent} (status {manifest.get('status')!r}): {detail or 'not promoted'}")
    if not problems:
        sys.exit(0)
    print("worldagent: these cases have not been validated by promote:\n" + "\n".join(problems) +
          "\nDo not describe them as done, complete, or final. Either run promote "
          "(/worldagent:promote or bin/promote.py), or tell the user plainly that the case is "
          "incomplete and which rows are not passing.", file=sys.stderr)
    sys.exit(2)


if __name__ == "__main__":
    main()
