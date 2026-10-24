#!/usr/bin/env python3
"""SessionStart hook: find Blender, FLIP Fluids, the render device, the asset library and the memory budget, and export them.

Reads the plugin's user configuration (CLAUDE_PLUGIN_OPTION_BLENDER_PATH, _FLIP_ADDON_PATH,
_FLIP_INSTALLER, _RENDER_DEVICE, _ASSET_LIBRARY_PATH, _MEMORY_BUDGET_GB, _TIME_BUDGET_HOURS,
_RENDER_RESOLUTION, _RENDER_SAMPLES), fills anything unset by
auto-detection (bin/worldagent_env.py), and appends `export` lines to $CLAUDE_ENV_FILE so
every Bash command in the session sees WORLDAGENT_BLENDER, WORLDAGENT_FLIP_PATH,
WORLDAGENT_RENDER_DEVICE, WORLDAGENT_ASSET_LIBRARY, WORLDAGENT_MEMORY_BUDGET_GB, WORLDAGENT_TIME_BUDGET_HOURS, WORLDAGENT_RENDER_RESOLUTION and
WORLDAGENT_RENDER_SAMPLES (when set). The summary printed on stdout becomes session context, so
the agent knows up front whether FLIP rows can run or will be blocked.

Codex receives a per-session snapshot under PLUGIN_DATA plus a deterministic cache locator.
The report identifies the plugin root and snapshot; no hook environment is assumed to
propagate to later shell commands. Claude keeps its existing env-file exports.

Never blocks the session: on any error it prints what it could and exits 0.
Standard library only.
"""

from __future__ import annotations

import json
import os
import shlex
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))
import worldagent_env  # noqa: E402
from worldagent_runtime import save_session


def option(key: str) -> str | None:
    return os.environ.get(f"CLAUDE_PLUGIN_OPTION_{key.upper()}")


def main() -> None:
    started_at = time.time()
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            event = {}
    except (OSError, ValueError):
        event = {}
    try:
        report = worldagent_env.detect(option("blender_path"), option("flip_addon_path"),
                                       option("flip_installer"), option("memory_budget_gb"),
                                       option("asset_library_path"), option("render_device"),
                                       option("time_budget_hours"), option("render_resolution"),
                                       option("render_samples"), use_session=False)
    except Exception as exc:  # noqa: BLE001 - a setup probe must never stop the session
        print(f"WorldAgent environment check failed: {exc}")
        return
    environment = worldagent_env.exports(report)
    # Codex supplies PLUGIN_ROOT; Claude retains its original env-file handoff.
    if os.environ.get("PLUGIN_ROOT"):
        try:
            snapshot = save_session(event, environment, started_at)
            print(f"WorldAgent session environment: {snapshot}")
        except OSError as exc:
            print(f"WorldAgent: could not save the session environment: {exc}")
    env_file = os.environ.get("CLAUDE_ENV_FILE")
    if env_file:
        try:
            with open(env_file, "a", encoding="utf-8") as f:
                for key, value in environment.items():
                    f.write(f"export {key}={shlex.quote(value)}\n")
        except OSError as exc:
            print(f"WorldAgent: could not write the session environment: {exc}")
    root = os.environ.get("PLUGIN_ROOT") or os.environ.get("CLAUDE_PLUGIN_ROOT") or str(Path(__file__).resolve().parent.parent)
    print(f"WorldAgent plugin root: {root}")
    print(worldagent_env.summary(report))


if __name__ == "__main__":
    main()
