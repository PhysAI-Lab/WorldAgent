"""worldagent_measure.render against a real Blender (skipped when none is configured).

Sets the Cycles device and renders a 32x18 frame on the CPU and, when this machine has one,
on the first available GPU. Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_render.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "render_cases.py"


@requires_blender
class RenderDeviceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT, timeout=600)

    def test_cpu(self):
        self.assertEqual(("CPU", "CPU", True), (self.r["cpu"]["device_type"], self.r["cpu_device"], self.r["cpu_rendered"]))

    def test_any_gpu(self):
        if not self.r["available"]:
            self.assertIn("no GPU render device available", self.r["any_gpu"]["error"])
            return
        info = self.r["any_gpu"]
        self.assertEqual(self.r["available"][0], info["device_type"])
        self.assertTrue(info["devices"])
        self.assertEqual(("GPU", True), (self.r["any_gpu_device"], self.r["any_gpu_rendered"]))

    def test_unavailable_or_unknown_device_raises(self):
        if self.r["missing"]:
            self.assertIn("not available", self.r["missing"]["error"])
        self.assertIn("unknown render device", self.r["unknown"]["error"])


if __name__ == "__main__":
    unittest.main()
