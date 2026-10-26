"""worldagent_measure.numerical tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_numerical.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "numerical_cases.py"


@requires_blender
class NumericalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def test_steady_motion_passes(self):
        r = self.r["steady_pass"]
        self.assertEqual("pass", r["status"])
        m = r["measurements"]["stability"]
        self.assertAlmostEqual(0.1, m["max_displacement_m"], places=5)
        self.assertAlmostEqual(2.4, m["max_speed_m_s"], places=4)
        self.assertEqual(0, m["non_finite_vertex_count"])
        self.assertEqual(25, m["frame_count"])

    def test_keyframed_jump_fails_on_speed(self):
        r = self.r["jump_fail"]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual(("cube", 11, 10, "speed"), (f["object"], f["frame"], f["previous_frame"], f["reason"]))
        self.assertAlmostEqual(240.0, f["value"], places=3)

    def test_nan_vertex_fails(self):
        r = self.r["nan_fail"]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual(("cube", 1, "non-finite coordinates"), (f["object"], f["frame"], f["reason"]))
        self.assertEqual(1, f["value"])
        self.assertEqual(25, r["measurements"]["stability"]["non_finite_vertex_count"])

    def test_extent_growth_fails(self):
        r = self.r["extent_fail"]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual("extent", f["reason"])
        self.assertGreater(f["value"], 5.0)
        self.assertAlmostEqual(10.0, r["measurements"]["stability"]["max_extent_m"], places=4)

    def test_rest_jitter_passes_on_window_displacement(self):
        r = self.r["rest_jitter"]
        self.assertEqual("pass", r["status"])
        m = r["measurements"]["rest_displacement"]
        self.assertAlmostEqual(0.001, m["max_window_displacement_m"], places=6)
        self.assertAlmostEqual(0.024, m["max_step_speed_m_s"], places=5)  # reported, not judged
        self.assertEqual([1, 25], m["window"])

    def test_rest_creep_fails(self):
        r = self.r["rest_creep"]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual(("cube", "moved during the rest window"), (f["object"], f["reason"]))
        self.assertGreater(f["value"], 0.003)
        self.assertAlmostEqual(0.024, r["measurements"]["rest_displacement"]["max_window_displacement_m"], places=5)


if __name__ == "__main__":
    unittest.main()
