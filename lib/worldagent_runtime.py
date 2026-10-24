"""Host-independent settings, usable by ordinary Python and Blender alike.

This module has no Blender imports and does not change process environment variables.
Config keys match the Claude plugin's userConfig keys; neither host is required.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
import warnings
from pathlib import Path

SETTINGS = {
    "WORLDAGENT_BLENDER": "blender_path",
    "WORLDAGENT_FLIP_PATH": "flip_addon_path",
    "WORLDAGENT_FLIP_INSTALLER": "flip_installer",
    "WORLDAGENT_RENDER_DEVICE": "render_device",
    "WORLDAGENT_RENDER_RESOLUTION": "render_resolution",
    "WORLDAGENT_RENDER_SAMPLES": "render_samples",
    "WORLDAGENT_ASSET_LIBRARY": "asset_library_path",
    "WORLDAGENT_MEMORY_BUDGET_GB": "memory_budget_gb",
    "WORLDAGENT_TIME_BUDGET_HOURS": "time_budget_hours",
}
PLUGIN_ROOT = Path(__file__).resolve().parent.parent


def unset(value) -> bool:
    """Empty, missing, or an unsubstituted ${...} placeholder."""
    return value is None or str(value).strip() == "" or str(value).strip().startswith("${")


def config_path() -> Path:
    override = os.environ.get("WORLDAGENT_CONFIG_FILE")
    if override:
        return Path(override).expanduser()
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "worldagent" / "config.json"


def read_object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("expected a JSON object")
        return value
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        warnings.warn(f"WorldAgent: cannot read {path}: {exc}", RuntimeWarning, stacklevel=2)
        return {}


def session_path(session_id: str) -> Path:
    """A deterministic locator shared by hooks and their child processes."""
    cache = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    plugin = hashlib.sha256(str(PLUGIN_ROOT).encode()).hexdigest()
    session = hashlib.sha256(session_id.encode()).hexdigest()
    return cache / "worldagent" / plugin / "sessions" / f"{session}.json"


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as f:
            name = f.name
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def save_session(event: dict, environment: dict, started_at: float | None = None) -> Path:
    """Store one session's environment and start time, never a shared 'latest' file."""
    session_id = str(event.get("session_id") or uuid.uuid4().hex)
    locator = session_path(session_id)
    previous = read_object(locator)
    data = {"session_id": session_id, "plugin_root": str(PLUGIN_ROOT),
            "cwd": event.get("cwd") or os.getcwd(),
            "started_at": previous.get("started_at", started_at if started_at is not None else time.time()),
            "environment": environment}
    storage = os.environ.get("PLUGIN_DATA") or os.environ.get("CLAUDE_PLUGIN_DATA")
    destination = Path(storage) / "sessions" / locator.name if storage else locator
    atomic_json(destination, data)
    if destination != locator:
        atomic_json(locator, data)
    return destination


def session_environment() -> dict:
    explicit = os.environ.get("WORLDAGENT_ENV_FILE")
    if explicit:
        data = read_object(Path(explicit).expanduser())
    else:
        session_id = os.environ.get("CODEX_SESSION_ID") or os.environ.get("CODEX_THREAD_ID")
        data = read_object(session_path(session_id)) if session_id else {}
    if data.get("plugin_root") != str(PLUGIN_ROOT):
        return {}
    values = data.get("environment")
    return values if isinstance(values, dict) else {}


def resolve(variable: str, explicit=None, default=None, *, use_session=True) -> tuple:
    """Return (value, source), preserving the source for environment reports."""
    if not unset(explicit):
        return explicit, "configured"
    value = os.environ.get(variable)
    if not unset(value):
        return value, variable
    if use_session:
        value = session_environment().get(variable)
        if not unset(value) and isinstance(value, (str, int, float)) and not isinstance(value, bool):
            return value, "session"
    value = read_object(config_path()).get(SETTINGS[variable])
    if not unset(value):
        numeric = variable in {"WORLDAGENT_MEMORY_BUDGET_GB", "WORLDAGENT_TIME_BUDGET_HOURS", "WORLDAGENT_RENDER_SAMPLES"}
        if isinstance(value, str) or (numeric and isinstance(value, (int, float)) and not isinstance(value, bool)):
            return value, "config file"
        expected = "a string or number" if numeric else "a string"
        warnings.warn(f"WorldAgent: {SETTINGS[variable]} must be {expected}", RuntimeWarning, stacklevel=2)
    return default, "default" if default is not None else None


def value(variable: str, default=None):
    return resolve(variable, default=default)[0]
