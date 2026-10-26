"""FLIP obstacle coupling for worldagent_measure.coupling: what FLIP exported against the Bullet cache.

Skipped unless WORLDAGENT_FLIP_PATH names the directory that contains flip_fluids_addon:
    WORLDAGENT_BLENDER=... WORLDAGENT_FLIP_PATH=... python3 -m unittest tests/test_measure_coupling_flip.py
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


@unittest.skipUnless(BLENDER and FLIP, "set WORLDAGENT_BLENDER and WORLDAGENT_FLIP_PATH to run the FLIP coupling test")
class CouplingFlipTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.r = run_cases(HERE / "blender" / "coupling_flip_cases.py", FLIP, cls._tmp.name, timeout=1800)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_obstacle_exported_per_frame(self):
        self.assertEqual(["FINISHED"], self.r["bake_result"])
        self.assertTrue(self.r["db_exists"])
        self.assertEqual("ANIMATED", self.r["motion_type"])
        self.assertEqual(list(range(1, 11)), self.r["exported_frames"])
        self.assertEqual(8, self.r["exported_vertices"])  # the cube's vertices, decoded from the blob

    def test_obstacle_link_declared(self):
        self.assertEqual("pass", self.r["links"]["status"], self.r["links"])
        self.assertIn(["crate", "flip_domain", "obstacle"], self.r["links"]["links"])

    def test_export_matches_bullet_cache(self):
        fresh = self.r["fresh"]
        self.assertEqual(("pass", "flip_export"), (fresh["status"], fresh["evidence"]), fresh)
        self.assertLess(fresh["worst"]["distance_m"], 1e-4)

    def test_bullet_rebaked_after_flip_fails(self):
        stale = self.r["stale"]
        self.assertEqual(("fail", "flip_export"), (stale["status"], stale["evidence"]), stale)
        self.assertGreater(stale["worst"]["distance_m"], 0.05)
        self.assertIn("rebake water", stale["problem"])

    def test_obstacle_without_export_animated_mesh(self):
        r = self.r["no_export_animated"]
        self.assertEqual("fail", r["status"])
        self.assertIn("crate is a moving FLIP obstacle without export_animated_mesh; FLIP sees only its first pose",
                      r["wrong_carrier"])


if __name__ == "__main__":
    unittest.main()
