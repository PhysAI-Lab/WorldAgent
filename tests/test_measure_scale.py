"""worldagent_measure.scale tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_scale.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "scale_cases.py"


@requires_blender
class ScaleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def test_dimensions_pass(self):
        r = self.r["dims_pass"]
        self.assertEqual("pass", r["status"])
        m = r["measurements"]["dimensions"]
        self.assertLess(m["max_rel_error"], 1e-6)
        self.assertEqual(0, m["unapplied_transform_count"])

    def test_wrong_dimension_fails(self):
        r = self.r["dims_wrong"]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual(("table", "z"), (f["object"], f["axis"]))
        self.assertAlmostEqual(0.75, f["value"], places=5)
        self.assertAlmostEqual(0.25, f["rel_error"], places=5)

    def test_rotated_object_uses_own_axes(self):
        self.assertEqual("pass", self.r["rotated_object_axes"]["status"])
        world = self.r["rotated_world_axes"]
        self.assertEqual("fail", world["status"])
        self.assertEqual("suitcase", world["first_failure"]["object"])

    def test_unapplied_scale_reported_not_failed(self):
        r = self.r["unapplied_reported"]
        self.assertEqual("pass", r["status"])
        m = r["measurements"]["dimensions"]
        self.assertEqual(1, m["unapplied_transform_count"])
        self.assertEqual(["crate"], m["unapplied_transform_objects"])

    def test_unapplied_scale_fails_on_request(self):
        r = self.r["unapplied_fail"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("crate", r["first_failure"]["object"])
        self.assertAlmostEqual(1.5, r["first_failure"]["scale"][2], places=5)

    def test_ratio_in_bounds(self):
        r = self.r["ratio_pass"]
        self.assertEqual("pass", r["status"])
        pair = r["measurements"]["size_ratios"]["pairs"][0]
        self.assertAlmostEqual((0.45**2 + 0.5**2 + 0.9**2) ** 0.5 / (1.6**2 + 0.9**2 + 0.75**2) ** 0.5, pair["rho"], places=5)

    def test_ratio_out_of_bounds(self):
        r = self.r["ratio_fail"]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual(["chair", "table"], f["pair"])
        self.assertLess(f["value"], f["rho_min"])


if __name__ == "__main__":
    unittest.main()
