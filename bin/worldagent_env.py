#!/usr/bin/env python3
"""Find the machine-specific pieces a WorldAgent case needs: Blender, FLIP Fluids, render device, assets, memory.

Used by the runner (Blender lookup) and by the SessionStart hook, which exports the result
to the session. Every value can be set explicitly; auto-detection only fills what is unset.
After explicit values and environment variables, worldagent_runtime reads the selected
session snapshot and optional user config file. SessionStart bypasses older snapshots.

Blender: the explicit path, else $WORLDAGENT_BLENDER, else `blender` on PATH, else the
usual install locations for this OS. The version is read with `blender --version`; the
skill targets Blender 5.1, so another version is reported as a warning, not refused.

FLIP Fluids: the explicit folder, else $WORLDAGENT_FLIP_PATH, else the Blender user add-on
and extension folders for that Blender version. The result is the folder that contains
`flip_fluids_addon`; verifiers put it on sys.path and enable the add-on from there (see
worldagent_measure.liquid.enable_flip). The installed version is read from the add-on's
`blender_manifest.toml` or `bl_info`. An installer zip, when given, is checked against
scripts/FLIP/SHA256SUMS.txt; installed files cannot be checked, because the published
checksums cover the installer only.

Render device: the explicit setting, else $WORLDAGENT_RENDER_DEVICE, else OPTIX (the skill's
final render device). CPU, ANY_GPU or a Cycles GPU type (OPTIX, CUDA, HIP, METAL, ONEAPI).
Blender is started once to list the GPU types it can use on this machine; ANY_GPU takes the
first in that order. The final render sets the device with worldagent_measure.render.

Asset library: the explicit folder, else $WORLDAGENT_ASSET_LIBRARY. Not searched for: there
is no standard location. The report lists its top-level folders (the original toolkit uses
terrain_textures/, road_textures/ and vegetation_3d/) so the agent knows what is there.

Memory: total physical memory is reported; the budget is only what the user configured
(the skill's own memory rules apply otherwise). Nothing here invents a budget.

Usage: worldagent_env.py [--blender PATH] [--flip DIR] [--flip-installer ZIP]
                         [--render-device TYPE] [--asset-library DIR] [--memory-budget-gb N] [--json]
Standard library only.
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PLUGIN_ROOT / "lib"))
from worldagent_runtime import resolve, unset

FLIP_SUMS = PLUGIN_ROOT / "scripts" / "FLIP" / "SHA256SUMS.txt"
FLIP_MODULE = "flip_fluids_addon"
TARGET_BLENDER = "5.1"
FLIP_VERSION = "1.8.8"


# --- Blender ---------------------------------------------------------------------------


def blender_candidates(system: str | None = None, home: Path | None = None) -> list[Path]:
    """Usual install locations, newest-looking first within each pattern."""
    system = system or platform.system()
    home = home or Path.home()
    if system == "Darwin":
        patterns = ["/Applications/Blender*.app/Contents/MacOS/Blender",
                    str(home / "Applications/Blender*.app/Contents/MacOS/Blender")]
    elif system == "Windows":
        roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("LOCALAPPDATA", "")]
        patterns = [str(Path(r) / "Blender Foundation" / "Blender*" / "blender.exe") for r in roots if r]
    else:
        patterns = ["/usr/bin/blender", "/usr/local/bin/blender", "/snap/bin/blender",
                    "/opt/blender*/blender", str(home / "blender*/blender"), str(home / "*/blender*/blender")]
    found = []
    for pattern in patterns:
        found += sorted((Path(p) for p in glob.glob(pattern)), reverse=True)
    return [p for p in found if p.is_file()]


def blender_version(blender: str, timeout: float = 30) -> str | None:
    """'5.1.1' from `blender --version`, or None if it does not run."""
    try:
        out = subprocess.run([blender, "--version"], capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Blender\s+(\d+\.\d+(?:\.\d+)?)", out)
    return m.group(1) if m else None


def find_blender(explicit: str | None = None, *, use_session=True) -> dict:
    """{'path', 'source', 'version', 'problem'}; path is None when nothing usable was found."""
    tried = []
    value, source = resolve("WORLDAGENT_BLENDER", explicit, use_session=use_session)
    if not unset(value):
        if not Path(value).is_file():
            return {"path": None, "source": source, "version": None,
                    "problem": f"{source} does not point to a file: {value}"}
        return _with_version(str(value), source)
    on_path = shutil.which("blender")
    if on_path:
        return _with_version(on_path, "PATH")
    for candidate in blender_candidates():
        tried.append(str(candidate))
        info = _with_version(str(candidate), "auto-detected")
        if info["version"]:
            return info
    return {"path": None, "source": None, "version": None,
            "problem": "Blender not found: set the plugin's Blender path or WORLDAGENT_BLENDER"
                       + (f" (tried {', '.join(tried)})" if tried else "")}


def _with_version(path: str, source: str) -> dict:
    version = blender_version(path)
    problem = None
    if version is None:
        problem = f"{path} did not report a Blender version"
    elif not version.startswith(TARGET_BLENDER):
        problem = f"Blender {version} found; the skill targets Blender {TARGET_BLENDER}"
    return {"path": path, "source": source, "version": version, "problem": problem}


# --- FLIP Fluids -----------------------------------------------------------------------


def blender_user_dirs(version: str | None, system: str | None = None, home: Path | None = None) -> list[Path]:
    """Folders where Blender looks for user add-ons and extensions for this version."""
    system = system or platform.system()
    home = home or Path.home()
    major_minor = ".".join((version or TARGET_BLENDER).split(".")[:2])
    dirs = []
    if os.environ.get("BLENDER_USER_SCRIPTS"):
        dirs.append(Path(os.environ["BLENDER_USER_SCRIPTS"]) / "addons")
    if os.environ.get("BLENDER_USER_EXTENSIONS"):
        dirs += sorted(Path(os.environ["BLENDER_USER_EXTENSIONS"]).glob("*"))
    if os.environ.get("BLENDER_USER_RESOURCES"):
        res = Path(os.environ["BLENDER_USER_RESOURCES"])
        dirs += [res / "scripts" / "addons"] + sorted((res / "extensions").glob("*"))
    if system == "Darwin":
        base = home / "Library" / "Application Support" / "Blender" / major_minor
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Blender Foundation" / "Blender" / major_minor
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config")) / "blender" / major_minor
    dirs += [base / "scripts" / "addons"] + sorted((base / "extensions").glob("*"))
    return dirs


def flip_version(addon_dir: Path) -> str | None:
    """Version from blender_manifest.toml (`version = "1.8.8"`) or bl_info (`"version": (1, 8, 8)`)."""
    manifest = addon_dir / "blender_manifest.toml"
    if manifest.is_file():
        m = re.search(r'^\s*version\s*=\s*"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"), re.M)
        if m:
            return m.group(1)
    init = addon_dir / "__init__.py"
    if init.is_file():
        m = re.search(r'"version"\s*:\s*\(([\d,\s]+)\)', init.read_text(encoding="utf-8", errors="replace"))
        if m:
            return ".".join(p.strip() for p in m.group(1).split(",") if p.strip())
    return None


def find_flip(explicit: str | None = None, version: str | None = None, *, use_session=True) -> dict:
    """{'path' (folder containing flip_fluids_addon), 'source', 'version', 'problem'}."""
    value, source = resolve("WORLDAGENT_FLIP_PATH", explicit, use_session=use_session)
    if not unset(value):
        folder = Path(value)
        if folder.name == FLIP_MODULE:  # accept the add-on folder itself
            folder = folder.parent
        if not (folder / FLIP_MODULE).is_dir():
            return {"path": None, "source": source, "version": None,
                    "problem": f"{source} has no {FLIP_MODULE} folder: {value}"}
        return _flip_info(folder, source)
    for folder in blender_user_dirs(version):
        if (folder / FLIP_MODULE).is_dir():
            return _flip_info(folder, "auto-detected")
    return {"path": None, "source": None, "version": None,
            "problem": "FLIP Fluids not found; FLIP requirements will be blocked"}


def _flip_info(folder: Path, source: str) -> dict:
    found = flip_version(folder / FLIP_MODULE)
    problem = None
    if found is None:
        problem = "FLIP Fluids version could not be read"
    elif found != FLIP_VERSION:
        problem = f"FLIP Fluids {found} found; the references are written for {FLIP_VERSION}"
    return {"path": str(folder), "source": source, "version": found, "problem": problem}


def known_installer_sums(sums: Path = FLIP_SUMS) -> dict[str, str]:
    """file name -> sha256 from SHA256SUMS.txt."""
    out = {}
    if sums.is_file():
        for line in sums.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                out[parts[1].lstrip("*")] = parts[0].lower()
    return out


def check_installer(zip_path: str | None, sums: Path = FLIP_SUMS) -> dict | None:
    """{'path', 'sha256', 'matches', 'problem'} for an installer zip, or None if none was given."""
    if unset(zip_path):
        return None
    path = Path(zip_path)
    if not path.is_file():
        return {"path": str(path), "sha256": None, "matches": False, "problem": f"installer not found: {path}"}
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    sha = digest.hexdigest()
    matches = sha in known_installer_sums(sums).values()
    return {"path": str(path), "sha256": sha, "matches": matches,
            "problem": None if matches else "installer does not match scripts/FLIP/SHA256SUMS.txt; do not install it"}


# --- render device -------------------------------------------------------------------

GPU_ORDER = ("OPTIX", "CUDA", "HIP", "METAL", "ONEAPI")
DEVICE_MARKER = "WORLDAGENT_DEVICES "
_DEVICE_PROBE = f"""
import bpy, json
prefs = bpy.context.preferences.addons["cycles"].preferences
found = {{}}
for item in prefs.get_device_types(bpy.context):
    if item[0] in {GPU_ORDER!r}:
        prefs.compute_device_type = item[0]
        prefs.refresh_devices()
        names = [d.name for d in prefs.devices if d.type == item[0]]
        if names:
            found[item[0]] = names
print({DEVICE_MARKER!r} + json.dumps(found))
"""


def gpu_devices(blender: str, timeout: float = 60) -> dict[str, list[str]] | None:
    """{gpu type: [device names]} that Cycles can use here, or None if Blender could not be asked."""
    try:
        out = subprocess.run([blender, "--background", "--factory-startup", "--python-expr", _DEVICE_PROBE],
                             capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if line.startswith(DEVICE_MARKER):
            try:
                return json.loads(line[len(DEVICE_MARKER):])
            except ValueError:
                return None
    return None


def render_device(blender: str | None, configured=None, *, use_session=True) -> dict:
    """{'requested', 'source', 'type', 'devices', 'problem'}; type is None when the request cannot be met."""
    requested, source = resolve("WORLDAGENT_RENDER_DEVICE", configured, "OPTIX", use_session=use_session)
    requested = str(requested)
    requested = requested.strip().upper()
    info = {"requested": requested, "source": source, "type": None, "devices": [], "problem": None}
    if requested == "CPU":
        return info | {"type": "CPU", "devices": ["CPU"]}
    if requested != "ANY_GPU" and requested not in GPU_ORDER:
        return info | {"problem": f"unknown render device {requested}; use CPU, ANY_GPU or one of {', '.join(GPU_ORDER)}"}
    found = gpu_devices(blender) if blender else None
    if found is None:
        return info | {"problem": "could not list render devices" + ("" if blender else " without Blender")}
    kind = next((k for k in GPU_ORDER if k in found), None) if requested == "ANY_GPU" else requested
    if kind not in found:
        return info | {"problem": f"render device {requested} not available (found: {', '.join(found) or 'no GPU'})"}
    return info | {"type": kind, "devices": found[kind]}


# --- asset library -------------------------------------------------------------------


def find_asset_library(explicit=None, *, use_session=True) -> dict:
    """{'path', 'source', 'folders': {name: entry count}, 'problem'}; path is None when unset."""
    value, source = resolve("WORLDAGENT_ASSET_LIBRARY", explicit, use_session=use_session)
    if unset(value):
        return {"path": None, "source": None, "folders": {}, "problem": None}
    path = Path(value).expanduser()
    if not path.is_dir():
        return {"path": None, "source": source, "folders": {}, "problem": f"asset library not found: {path}"}
    folders = {d.name: sum(1 for _ in d.iterdir()) for d in sorted(path.iterdir())
               if d.is_dir() and not d.name.startswith(".")}
    return {"path": str(path.resolve()), "source": source, "folders": folders,
            "problem": None if folders else f"asset library is empty: {path}"}


# --- memory ----------------------------------------------------------------------------


def total_memory_gb() -> float | None:
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return round(int(out.strip()) / 2**30, 1)
        if hasattr(os, "sysconf") and "SC_PHYS_PAGES" in os.sysconf_names:
            return round(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30, 1)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


def memory_budget(configured, *, use_session=True) -> dict:
    """GB per Blender process from the plugin setting, else $WORLDAGENT_MEMORY_BUDGET_GB, else none."""
    total = total_memory_gb()
    budget, problem = None, None
    configured, source = resolve("WORLDAGENT_MEMORY_BUDGET_GB", configured, use_session=use_session)
    source = {"configured": "setting", "WORLDAGENT_MEMORY_BUDGET_GB": "environment"}.get(source, source)
    if not unset(configured):
        try:
            budget = float(configured)
        except ValueError:
            problem = f"memory budget is not a number: {configured}"
        else:
            if budget <= 0:
                budget, problem = None, "memory budget must be positive"
            elif total and budget > total:
                problem = f"memory budget {budget:g} GB exceeds total memory {total:g} GB"
    return {"total_gb": total, "budget_gb": budget, "source": source if budget else None, "problem": problem}


# --- time budget -----------------------------------------------------------------------

# Wall-clock hours a /worldagent:run case may use when the user sets none. A full laptop run of
# a small liquid scene (spec to final render) took about 2.5 h in testing.
DEFAULT_TIME_BUDGET_HOURS = 3.0


def time_budget(configured, *, use_session=True) -> dict:
    """Hours from the plugin setting, else $WORLDAGENT_TIME_BUDGET_HOURS, else the default."""
    value, source = resolve("WORLDAGENT_TIME_BUDGET_HOURS", configured, use_session=use_session)
    source = {"configured": "setting", "WORLDAGENT_TIME_BUDGET_HOURS": "environment"}.get(source, source)
    if unset(value):
        return {"hours": DEFAULT_TIME_BUDGET_HOURS, "source": "default", "problem": None}
    try:
        hours = float(value)
    except ValueError:
        return {"hours": DEFAULT_TIME_BUDGET_HOURS, "source": "default",
                "problem": f"time budget is not a number: {value}; using {DEFAULT_TIME_BUDGET_HOURS:g} h"}
    if hours <= 0:
        return {"hours": DEFAULT_TIME_BUDGET_HOURS, "source": "default",
                "problem": f"time budget must be positive; using {DEFAULT_TIME_BUDGET_HOURS:g} h"}
    return {"hours": hours, "source": source, "problem": None}


# --- render output ---------------------------------------------------------------------

DEFAULT_RENDER_RESOLUTION = "1920x1080"  # the skill's final delivery size


def render_output(resolution=None, samples=None, *, use_session=True) -> dict:
    """Final render resolution (setting, else $WORLDAGENT_RENDER_RESOLUTION, else 1920x1080) and samples."""
    out = {"resolution": DEFAULT_RENDER_RESOLUTION, "resolution_source": "default", "samples": None,
           "samples_source": "scene", "problem": None}
    problems = []
    for key, value, variable in (("resolution", resolution, "WORLDAGENT_RENDER_RESOLUTION"),
                                 ("samples", samples, "WORLDAGENT_RENDER_SAMPLES")):
        value, source = resolve(variable, value, use_session=use_session)
        source = {"configured": "setting", variable: "environment"}.get(source, source)
        if unset(value):
            continue
        text = str(value).strip().lower()
        if key == "resolution":
            parts = text.split("x")
            ok = len(parts) == 2 and all(p.isdigit() and int(p) > 0 for p in parts)
            if ok:
                out.update(resolution=f"{int(parts[0])}x{int(parts[1])}", resolution_source=source)
            else:
                problems.append(f"render resolution {value!r} is not WIDTHxHEIGHT; using {DEFAULT_RENDER_RESOLUTION}")
        else:
            try:
                n = int(float(text))
            except ValueError:
                n = 0
            if n > 0:
                out.update(samples=n, samples_source=source)
            else:
                problems.append(f"render samples {value!r} is not a positive number; using the scene's own")
    out["problem"] = "; ".join(problems) or None
    return out


# --- report ----------------------------------------------------------------------------


def detect(blender=None, flip=None, flip_installer=None, memory_budget_gb=None, asset_library=None,
           device=None, time_budget_hours=None, render_resolution=None, render_samples=None, *, use_session=True) -> dict:
    b = find_blender(blender, use_session=use_session)
    installer, _ = resolve("WORLDAGENT_FLIP_INSTALLER", flip_installer, use_session=use_session)
    return {"blender": b, "flip": find_flip(flip, b["version"], use_session=use_session),
            "flip_installer": check_installer(installer),
            "render": render_device(b["path"], device, use_session=use_session),
            "assets": find_asset_library(asset_library, use_session=use_session),
            "memory": memory_budget(memory_budget_gb, use_session=use_session),
            "time": time_budget(time_budget_hours, use_session=use_session),
            "output": render_output(render_resolution, render_samples, use_session=use_session)}


def exports(report: dict) -> dict[str, str]:
    """Environment variables for the session."""
    env = {}
    if report["blender"]["path"]:
        env["WORLDAGENT_BLENDER"] = report["blender"]["path"]
    if report["flip"]["path"]:
        env["WORLDAGENT_FLIP_PATH"] = report["flip"]["path"]
    render = report["render"]
    if render["type"] or render["source"] != "default":
        env["WORLDAGENT_RENDER_DEVICE"] = render["type"] or render["requested"]
    if report["assets"]["path"]:
        env["WORLDAGENT_ASSET_LIBRARY"] = report["assets"]["path"]
    if report["memory"]["budget_gb"]:
        env["WORLDAGENT_MEMORY_BUDGET_GB"] = f"{report['memory']['budget_gb']:g}"
    env["WORLDAGENT_TIME_BUDGET_HOURS"] = f"{report['time']['hours']:g}"
    env["WORLDAGENT_RENDER_RESOLUTION"] = report["output"]["resolution"]
    if report["output"]["samples"]:
        env["WORLDAGENT_RENDER_SAMPLES"] = str(report["output"]["samples"])
    return env


def summary(report: dict) -> str:
    b, f, i, a, m = report["blender"], report["flip"], report["flip_installer"], report["assets"], report["memory"]
    r = report["render"]
    lines = ["WorldAgent environment:"]
    lines.append(f"- Blender: {b['version']} at {b['path']} ({b['source']})" if b["path"] else "- Blender: not found")
    if f["path"]:
        lines.append(f"- FLIP Fluids: {f['version']} in {f['path']} ({f['source']})")
    else:
        lines.append("- FLIP Fluids: not found; FLIP requirement rows will be blocked")
    if i:
        lines.append(f"- FLIP installer {i['path']}: " + ("matches SHA256SUMS.txt" if i["matches"] else "does NOT match SHA256SUMS.txt"))
    if r["type"]:
        lines.append(f"- Render device: {r['type']} ({', '.join(r['devices'])})")
    else:
        lines.append(f"- Render device: {r['problem'].removeprefix('render device ')}")
    if a["path"]:
        listing = ", ".join(f"{name}/ ({n})" for name, n in a["folders"].items())
        lines.append(f"- Asset library: {a['path']} ({a['source']}): {listing}")
    else:
        lines.append("- Asset library: none; build assets procedurally and record the substitution")
    total = f"{m['total_gb']:g} GB" if m["total_gb"] else "unknown"
    if m["budget_gb"]:
        lines.append(f"- Memory: {total} total; budget {m['budget_gb']:g} GB per Blender process ({m['source']})")
    else:
        lines.append(f"- Memory: {total} total; no budget configured, so the skill's memory-safety rules apply as written")
    o = report["output"]
    lines.append(f"- Final render: {o['resolution']} ({o['resolution_source']}), samples "
                 + (f"{o['samples']} ({o['samples_source']})" if o["samples"] else "from the scene"))
    t = report["time"]
    lines.append(f"- Time budget for /worldagent:run: {t['hours']:g} h ({t['source']})")
    for part in (b, f, i, a, m, t, o):
        if part and part.get("problem"):
            lines.append(f"- Warning: {part['problem']}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--blender")
    parser.add_argument("--flip")
    parser.add_argument("--flip-installer")
    parser.add_argument("--render-device")
    parser.add_argument("--asset-library")
    parser.add_argument("--memory-budget-gb")
    parser.add_argument("--time-budget-hours")
    parser.add_argument("--render-resolution")
    parser.add_argument("--render-samples")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = detect(args.blender, args.flip, args.flip_installer, args.memory_budget_gb, args.asset_library,
                    args.render_device, args.time_budget_hours, args.render_resolution, args.render_samples)
    print(json.dumps(report, indent=2) if args.json else summary(report))
    sys.exit(0 if report["blender"]["path"] else 1)


if __name__ == "__main__":
    main()
