"""Real FLIP Fluids bake test for worldagent_measure.liquid.

Skipped unless WORLDAGENT_FLIP_PATH names the directory that contains flip_fluids_addon:
    WORLDAGENT_BLENDER=... WORLDAGENT_FLIP_PATH=... python3 -m unittest worldagent-plugin/tests/test_measure_liquid_flip.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import BLENDER, run_cases  # noqa: E402

HERE = Path(__file__).resolve().parent
FLIP = os.environ.get("WORLDAGENT_FLIP_PATH")


@unittest.skipUnless(BLENDER and FLIP, "set WORLDAGENT_BLENDER and WORLDAGENT_FLIP_PATH to run the FLIP bake test")
class LiquidFlipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.work = Path(cls._tmp.name)
        cls.baked = run_cases(HERE / "blender" / "liquid_flip_cases.py", FLIP, str(cls.work), "bake", timeout=1800)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_bake_finished(self):
        self.assertEqual(["FINISHED"], self.baked["bake_result"])
        self.assertTrue(self.baked["is_flip_domain"])

    def test_surface_loaded_every_frame(self):
        for frame, s in self.baked["surfaces"].items():
            self.assertGreater(s["vertices"], 0, frame)
            self.assertGreater(s["triangles"], 0, frame)

    def test_first_frame_volume_near_source(self):
        first = self.baked["surfaces"]["1"]
        self.assertIsNotNone(first["volume_m3"], "frame 1 surface is not closed")
        rel = abs(first["volume_m3"] - self.baked["sphere_volume_m3"]) / self.baked["sphere_volume_m3"]
        self.assertLess(rel, 0.5, first)

    def test_checks_return_measurements(self):
        self.assertTrue(self.baked["quality"]["measurements_keys"])
        self.assertIn("frames_checked", self.baked["inside_obstacle"]["measurements"])

    def test_reopened_without_flip_is_blocked(self):
        result = run_cases(HERE / "blender" / "liquid_flip_cases.py", FLIP, str(self.work), "noflip",
                           blend=self.work / "flip_scene.blend")
        self.assertTrue(result["is_flip_domain"])
        self.assertTrue(result["blocked"], result)


if __name__ == "__main__":
    unittest.main()
