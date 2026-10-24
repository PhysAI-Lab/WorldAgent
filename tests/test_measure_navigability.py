"""worldagent_measure.navigability tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_navigability.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "navigability_cases.py"


@requires_blender
class NavigabilityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def m(self, key):
        return self.r[key]["measurements"]["route"]

    def assert_passes(self, key):
        self.assertEqual("pass", self.r[key]["status"], self.r[key].get("first_failure"))
        self.assertIsNone(self.m(key)["first_blocking_sample"])
        return self.m(key)

    def assert_fails(self, key, check, obj):
        r = self.r[key]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual((check, obj), (f["check"], f["object"]))
        self.assertEqual(f["sample"], self.m(key)["first_blocking_sample"])
        return f

    def test_corridor_with_doorway_passes(self):
        m = self.assert_passes("doorway_clear")
        self.assertEqual(161, m["samples"])
        self.assertAlmostEqual(1.0, m["min_width_m"], places=5)
        self.assertAlmostEqual(2.1, m["min_headroom_m"], places=5)
        self.assertEqual(0.0, m["max_slope_deg"])
        self.assertEqual(0.0, m["max_step_m"])

    def test_crate_in_doorway_blocks(self):
        f = self.assert_fails("doorway_blocked", "headroom", "crate")
        self.assertEqual(0.0, f["value"])
        self.assertAlmostEqual(-0.25, f["point"][0], places=5)

    def test_crate_narrowing_doorway_fails_width(self):
        f = self.assert_fails("doorway_narrowed", "width", "crate")
        self.assertAlmostEqual(0.6, f["value"], places=5)

    def test_low_lintel_fails_headroom(self):
        f = self.assert_fails("low_lintel", "headroom", "lintel")
        self.assertAlmostEqual(1.8, f["value"], places=5)

    def test_step(self):
        self.assertAlmostEqual(0.15, self.assert_passes("step_ok")["max_step_m"], places=5)
        f = self.assert_fails("step_high", "step", "platform")
        self.assertAlmostEqual(0.3, f["value"], places=5)

    def test_ramp(self):
        self.assertLess(self.assert_passes("ramp_gentle")["max_slope_deg"], 10)
        f = self.assert_fails("ramp_steep", "slope", "ramp")
        self.assertAlmostEqual(30.964, f["value"], places=2)

    def test_floor_gap_breaks_continuity(self):
        f = self.assert_fails("floor_gap", "ground", None)
        self.assertGreaterEqual(f["point"][0], 1.0)
        self.assertLessEqual(f["point"][0], 1.5)


if __name__ == "__main__":
    unittest.main()
