"""Blender-side cases for worldagent_measure.render (run by tests/test_render.py)."""
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from blender_harness import emit  # noqa: E402

from worldagent_measure import render  # noqa: E402


def attempt(device_type):
    try:
        return render.setup_device(bpy.context.scene, device_type)
    except render.RenderDeviceError as exc:
        return {"error": str(exc)}


def tiny_render(info):
    if "error" in info:
        return None
    s = bpy.context.scene
    s.render.resolution_x, s.render.resolution_y, s.cycles.samples = 32, 18, 2
    s.render.filepath = str(Path(tempfile.mkdtemp()) / "frame.png")
    bpy.ops.render.render(write_still=True)
    return Path(s.render.filepath).is_file()


def main():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.mesh.primitive_cube_add()
    bpy.ops.object.camera_add(location=(0, -6, 0), rotation=(1.5708, 0, 0))
    bpy.context.scene.camera = bpy.context.object
    out = {"available": render.available_types()}
    out["cpu"] = attempt("CPU")
    out["cpu_rendered"] = tiny_render(out["cpu"])
    out["cpu_device"] = bpy.context.scene.cycles.device
    out["any_gpu"] = attempt("ANY_GPU")
    out["any_gpu_device"] = bpy.context.scene.cycles.device
    out["any_gpu_rendered"] = tiny_render(out["any_gpu"])
    missing = next((k for k in render.GPU_ORDER if k not in out["available"]), None)
    out["missing"] = missing and attempt(missing)
    out["unknown"] = attempt("VULKAN")
    emit(out)


main()
