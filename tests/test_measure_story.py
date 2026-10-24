"""worldagent_measure.causal and .shot tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_story.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "story_cases.py"


@requires_blender
class CausalTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def failure(self, key):
        self.assertEqual("fail", self.r[key]["status"], key)
        return self.r[key]["first_failure"]

    def test_event_onsets(self):
        self.assertEqual({"contact": 9, "motion": 12, "entry": 16, "never": None}, self.r["onsets"])

    def test_order_and_windows_pass(self):
        r = self.r["order_pass"]
        self.assertEqual("pass", r["status"])
        lags = [p["lag_frames"] for p in r["measurements"]["causal_order"]["pairs"]]
        self.assertEqual([3, 4], lags)

    def test_order_failures(self):
        self.assertEqual(("effect before cause", -7), tuple(self.failure("order_reversed")[k] for k in ("problem", "lag_frames")))
        self.assertEqual("effect too soon after cause", self.failure("order_same_frame")["problem"])
        self.assertEqual("effect too late", self.failure("order_too_late")["problem"])
        self.assertEqual(("flood", "never happened"), tuple(self.failure("order_missing")[k] for k in ("event", "problem")))
        self.assertEqual("outside its frame interval", self.failure("order_window")["problem"])

    def test_response_to_contact(self):
        m = self.r["response_pass"]["measurements"]["response_to_contact"]
        self.assertEqual((9, 12, 3), (m["contact_frame"], m["response_frame"], m["lag_frames"]))
        self.assertEqual("no response within the allowed lag", self.failure("response_slow")["problem"])
        f = self.failure("response_early")
        self.assertEqual(("effect moved before contact", 3), (f["problem"], f["frame"]))

    def test_closest_approach_explains_missing_contact(self):
        self.assertIsNone(self.r["short_contact"])
        near = self.r["short_closest"]
        self.assertAlmostEqual(0.003, near["min_gap_m"], places=5)
        self.assertEqual((9, 25), (near["frame"], near["n"]))
        f = self.failure("response_no_contact")
        self.assertEqual("no contact on the checked frames", f["problem"])
        self.assertAlmostEqual(0.003, f["min_gap_m"], places=5)

    def test_story_dependencies(self):
        self.assertEqual({"pairs": [["tip", "fall"]], "windows": {"tip": [1, 10], "fall": [11, 20]}},
                         self.r["story_dependencies"])


@requires_blender
class ShotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def targets(self, key):
        return self.r[key]["measurements"]["shot_coverage"]["targets"]

    def test_visible_target_passes(self):
        self.assertEqual("pass", self.r["shot_pass"]["status"])
        t = self.targets("shot_pass")[0]
        self.assertEqual((5, 5, 1.0), (t["frames_checked"], t["frames_covered"], t["worst_seen_fraction"]))
        self.assertGreater(t["max_image_span"], 0)

    def test_occluded_and_off_frame_targets_fail(self):
        f = self.r["shot_hidden"]["first_failure"]
        self.assertEqual(("hidden", 0.0, "not seen on enough frames"), (f["object"], f["covered_fraction"], f["problem"]))
        self.assertEqual(1.0, self.targets("shot_hidden")[0]["covered_fraction"])
        self.assertEqual("outside", self.r["shot_outside"]["first_failure"]["object"])

    def test_missing_camera_fails(self):
        self.assertEqual("no scene camera", self.r["shot_no_camera"]["first_failure"]["problem"])

    def test_explicit_camera_and_step(self):
        self.assertEqual("pass", self.r["shot_explicit_camera"]["status"])
        self.assertEqual(3, self.targets("shot_explicit_camera")[0]["frames_checked"])  # 1, 3 and the last frame 4


if __name__ == "__main__":
    unittest.main()
