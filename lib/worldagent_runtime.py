"""Host-independent settings, usable by ordinary Python and Blender alike.

This module has no Blender imports at module level and does not change process environment
variables. Config keys match the Claude plugin's userConfig keys; neither host is required.
rebase_paths() is the one function that needs Blender: the runner calls it inside the
verifier's Blender process (bin/run_verifier.py).
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


# --- relative paths of a candidate opened from a copy ----------------------------------------

SKIP_DIRS = {"__pycache__"}


def fingerprint(path) -> str | None:
    """Cheap change marker of a file or directory: sha256 over sorted (relative path, size, mtime_ns).

    No file content is read, so a multi-GB cache costs one stat per file. A write that keeps both
    size and mtime_ns is not seen; a stray bake, free or overwrite is. None when path is missing.
    Bytecode caches are skipped.
    """
    path = os.path.abspath(str(path))
    if not os.path.exists(path):
        return None
    items = []
    if os.path.isfile(path):
        st = os.stat(path)
        items.append(("", st.st_size, st.st_mtime_ns))
    else:
        for top, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                full = os.path.join(top, name)
                try:
                    st = os.stat(full)
                except OSError:
                    continue  # removed while walking; the listing then differs anyway
                items.append((os.path.relpath(full, path).replace(os.sep, "/"), st.st_size, st.st_mtime_ns))
    digest = hashlib.sha256()
    for rel, size, mtime in sorted(items):
        digest.update(f"{rel}\0{size}\0{mtime}\n".encode())
    return digest.hexdigest()


def _quoted(name: str) -> str:
    return json.dumps(name, ensure_ascii=False)


def _point_caches(bpy):
    """(datablock, property path, point cache) for every point cache in the file."""
    for scene in bpy.data.scenes:
        world = scene.rigidbody_world
        if world is not None and world.point_cache is not None:
            yield scene, "rigidbody_world.point_cache", world.point_cache
    for obj in bpy.data.objects:
        for psys in obj.particle_systems:
            yield obj, f"particle_systems[{_quoted(psys.name)}].point_cache", psys.point_cache
        for mod in obj.modifiers:
            cache = getattr(mod, "point_cache", None)
            if cache is not None:
                yield obj, f"modifiers[{_quoted(mod.name)}].point_cache", cache
            canvas = getattr(mod, "canvas_settings", None) if mod.type == "DYNAMIC_PAINT" else None
            for surface in (canvas.canvas_surfaces if canvas is not None else []):
                yield obj, (f"modifiers[{_quoted(mod.name)}].canvas_settings.canvas_surfaces"
                            f"[{_quoted(surface.name)}].point_cache"), surface.point_cache


def _flip_cache(obj):
    """(owner, key) holding a FLIP Fluids domain's cache_directory, or None.

    With the add-on registered this is the RNA group; without it (the runner opens the copy
    with --factory-startup) the value is still stored in the object's system properties,
    which the add-on reads once a verifier enables it.
    """
    settings = getattr(obj, "flip_fluid", None)
    if settings is not None:
        if getattr(settings, "object_type", None) == "TYPE_DOMAIN":
            return settings.domain.cache, "cache_directory"
        return None
    try:
        group = obj.bl_system_properties_get()  # Blender 5.x: bpy.props data lives here
    except AttributeError:
        group = obj
    try:
        cache = group.get("flip_fluid", {}).get("domain", {}).get("cache", {})
    except (AttributeError, TypeError):
        return None
    if hasattr(cache, "get") and isinstance(cache.get("cache_directory"), str):
        return cache, "cache_directory"
    return None


_applied_rebases: list[dict] = []


def applied_rebases() -> list[dict]:
    """Copies of the entries the last rebase_paths() call returned; empty when none ran in this process.

    Measure helpers read it through sys.modules.get("worldagent_runtime") to report the path a
    datablock had saved (entry "from") when the runner has already rebased it.
    """
    return [dict(entry) for entry in _applied_rebases]


def rebase_paths(original_dir, report_path=None, relative_to=None, only=None) -> list[dict]:
    """Point `//` paths of the open (copied) .blend at the candidate's own folder.

    Blender resolves `//` against the folder of the file it opened; the runner opens a copy in
    a temporary folder, so caches stored next to the candidate would not be found (a Mantaflow
    grid then reads as all zeros while it still reports baked data). Every `//` path of a fluid
    domain cache (Mantaflow, FLIP Fluids), an external point cache, a Mesh Cache modifier, a
    cache file, a volume, an image or a movie clip is set to the same path under original_dir,
    in memory only (the file is never saved). Absolute paths are left as they are. Disk point
    caches (use_disk_cache) always live in `//blendcache_<file name>/`; the runner links that
    folder's files next to the copy before Blender starts, and they are listed here with
    read_from_copy true. Linked library paths are not rebased.

    Returns one entry per path: {datablock, property, from, to, exists[, read_from_copy]};
    applied_rebases() returns them again later in the process.
    With report_path, also writes {"rebased_paths": [...], "fingerprints": {path: fingerprint},
    "fingerprint_seconds": s} there, where fingerprints covers every existing rebased cache path
    the verifier reads in place (not single images) and the candidate's disk point cache folder
    (its files are hard links), so the runner can tell whether the verifier changed one.

    With relative_to (a folder), each path is instead set to a `//` path relative to relative_to
    that names the same place under original_dir: bin/promote.py saves the final scene this way
    as a copy that resolves from the case root. Disk point caches cannot be renamed and are
    listed with read_from_copy as before. With only (a set of (datablock, property) pairs, as
    in the entries), other paths are left as they are and not listed.
    """
    import bpy  # only available inside Blender

    original_dir = os.path.abspath(str(original_dir))
    stem = Path(bpy.data.filepath).stem
    entries, cache_paths = [], []

    def target(path: str) -> str:
        return os.path.normpath(os.path.join(original_dir, path[2:]))

    def rebase(datablock, prop: str, owner, key: str, cache: bool, folder: bool = False) -> None:
        value = owner[key] if not hasattr(owner, "bl_rna") else getattr(owner, key)
        if not (isinstance(value, str) and value.startswith("//")):
            return
        name = f"{datablock.bl_rna.identifier}[{_quoted(datablock.name)}]"
        if only is not None and (name, prop) not in only:
            return
        new = resolved = target(value)
        if relative_to is not None:
            new = "//" + os.path.relpath(resolved, os.path.abspath(str(relative_to))).replace(os.sep, "/")
            if value.endswith(("/", "\\")) and not new.endswith("/"):
                new += "/"
        if hasattr(owner, "bl_rna"):
            setattr(owner, key, new)
        else:
            owner[key] = new
        checked = os.path.dirname(resolved) if folder else resolved
        entries.append({"datablock": name, "property": prop, "from": value, "to": new,
                        "exists": os.path.exists(resolved)})
        if cache:
            cache_paths.append(checked)

    for obj in bpy.data.objects:
        if obj.library is not None:
            continue
        for mod in obj.modifiers:
            name = _quoted(mod.name)
            if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
                rebase(obj, f"modifiers[{name}].domain_settings.cache_directory", mod.domain_settings,
                       "cache_directory", cache=True)
            elif mod.type == "MESH_CACHE":
                rebase(obj, f"modifiers[{name}].filepath", mod, "filepath", cache=True)
        flip = _flip_cache(obj)
        if flip is not None:
            rebase(obj, "flip_fluid.domain.cache.cache_directory", *flip, cache=True)
    disk_cache = os.path.join(original_dir, f"blendcache_{stem}")
    for datablock, prop, cache in _point_caches(bpy):
        if datablock.library is not None:
            continue
        if cache.use_external:
            rebase(datablock, f"{prop}.filepath", cache, "filepath", cache=True)
        elif cache.use_disk_cache:
            if only is not None and (f"{datablock.bl_rna.identifier}[{_quoted(datablock.name)}]", prop) not in only:
                continue
            entries.append({"datablock": f"{datablock.bl_rna.identifier}[{_quoted(datablock.name)}]", "property": prop,
                            "from": f"//blendcache_{stem}", "to": disk_cache, "exists": os.path.isdir(disk_cache),
                            "read_from_copy": True})
            cache_paths.append(disk_cache)
    for collection in (bpy.data.cache_files, bpy.data.volumes, bpy.data.images, bpy.data.movieclips):
        for block in collection:
            if block.library is not None or getattr(block, "packed_file", None) is not None:
                continue
            source = getattr(block, "source", "FILE")
            if source not in ("FILE", "SEQUENCE", "MOVIE", "TILED"):
                continue  # generated or viewer images have no file
            single_image = isinstance(block, bpy.types.Image) and source in ("FILE", "TILED")
            sequence = source == "SEQUENCE" or getattr(block, "is_sequence", False)
            rebase(block, "filepath", block, "filepath", cache=not single_image, folder=sequence)

    if relative_to is None:
        _applied_rebases[:] = [dict(entry) for entry in entries]
    if report_path is not None:
        started = time.time()
        prints = {p: fingerprint(p) for p in dict.fromkeys(cache_paths) if os.path.exists(p)}
        atomic_json(Path(report_path), {"rebased_paths": entries, "fingerprints": prints,
                                        "fingerprint_seconds": round(time.time() - started, 4)})
    return entries


def scene_signature(ignore=()) -> dict:
    """Static fingerprint of the open .blend, for telling a re-saved copy from the file it came from.

    Counts every bpy.data collection; per object its type, data, parent, world matrix, modifier
    stack and particle systems; per mesh a hash of its vertex positions and faces; per scene its
    frame range, fps and camera; and every `//`-capable path rebase_paths() visits (absolute or
    relative), except the (datablock, property) pairs in ignore. No frame is stepped: evaluating
    a fluid domain whose cache is not marked baked would simulate and write into its cache folder.
    """
    import bpy

    def num(x) -> str:
        v = round(float(x), 6)
        return f"{(0.0 if v == 0 else v):.6f}"

    counts = {}
    for attr in dir(bpy.data):
        coll = getattr(bpy.data, attr, None)
        if isinstance(coll, bpy.types.bpy_prop_collection):
            counts[attr] = len(coll)
    objects = {}
    for obj in bpy.data.objects:
        objects[obj.name] = [obj.type, obj.data.name if obj.data is not None else None,
                             obj.parent.name if obj.parent is not None else None,
                             " ".join(num(c) for row in obj.matrix_world for c in row),
                             [[m.name, m.type] for m in obj.modifiers],
                             [[p.name, p.settings.name] for p in obj.particle_systems]]
    meshes = {}
    for mesh in bpy.data.meshes:
        digest = hashlib.sha256(" ".join(num(c) for v in mesh.vertices for c in v.co).encode())
        digest.update(" ".join(str(i) for poly in mesh.polygons for i in poly.vertices).encode())
        meshes[mesh.name] = digest.hexdigest()
    scenes = {scene.name: [scene.frame_start, scene.frame_end, scene.render.fps, scene.render.fps_base,
                           scene.camera.name if scene.camera is not None else None] for scene in bpy.data.scenes}
    ignore = {tuple(pair) for pair in ignore}
    paths = {}
    for obj in bpy.data.objects:
        for mod in obj.modifiers:
            if mod.type == "FLUID" and mod.fluid_type == "DOMAIN":
                paths[(f"Object[{_quoted(obj.name)}]",
                       f"modifiers[{_quoted(mod.name)}].domain_settings.cache_directory")] = \
                    mod.domain_settings.cache_directory
            elif mod.type == "MESH_CACHE":
                paths[(f"Object[{_quoted(obj.name)}]", f"modifiers[{_quoted(mod.name)}].filepath")] = mod.filepath
        flip = _flip_cache(obj)
        if flip is not None:
            owner, key = flip
            paths[(f"Object[{_quoted(obj.name)}]", "flip_fluid.domain.cache.cache_directory")] = \
                owner[key] if not hasattr(owner, "bl_rna") else getattr(owner, key)
    for datablock, prop, cache in _point_caches(bpy):
        paths[(f"{datablock.bl_rna.identifier}[{_quoted(datablock.name)}]", f"{prop}.filepath")] = \
            [cache.filepath, cache.use_external, cache.use_disk_cache]
    for collection in (bpy.data.cache_files, bpy.data.volumes, bpy.data.images, bpy.data.movieclips):
        for block in collection:
            paths[(f"{block.bl_rna.identifier}[{_quoted(block.name)}]", "filepath")] = block.filepath
    return {"counts": counts, "objects": objects, "meshes": meshes, "scenes": scenes,
            "paths": {f"{d} {p}": v for (d, p), v in sorted(paths.items()) if (d, p) not in ignore}}
