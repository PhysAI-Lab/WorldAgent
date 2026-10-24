"""worldagent_measure.support tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_support.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "support_cases.py"


@requires_blender
class SupportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def m(self, key):
        return self.r[key]["measurements"]["support_chain"]

    def assert_fails(self, key, obj, supporter, reason, unsupported=1):
        r = self.r[key]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual((obj, supporter, reason), (f["object"], f["supporter"], f["reason"]))
        self.assertEqual(unsupported, self.m(key)["unsupported_floating_count"])
        return f

    def test_box_on_table_on_floor_passes(self):
        for key in ("on_table", "on_table_foundation_list"):
            self.assertEqual("pass", self.r[key]["status"], key)
            m = self.m(key)
            self.assertEqual(0, m["unsupported_floating_count"])
            self.assertEqual(2, m["objects_checked"])
            self.assertEqual("box -> table -> floor", m["per_object"]["box"]["chain"])
            self.assertLess(abs(m["per_object"]["box"]["max_gap_m"]), 1e-6)

    def test_slight_penetration_still_counts_as_contact(self):
        self.assertEqual("pass", self.r["slight_penetration"]["status"])
        self.assertAlmostEqual(-0.0005, self.m("slight_penetration")["per_object"]["box"]["max_gap_m"], places=5)

    def test_floating_5cm_fails_with_gap(self):
        f = self.assert_fails("floating_5cm", "box", "table", "no contact under footprint")
        self.assertAlmostEqual(0.05, f["gap_m"], places=5)
        self.assertEqual(1, f["frame"])

    def test_aabb_near_beside_table_is_not_support(self):
        self.assertTrue(self.r["beside_aabb_near"]["aabb_overlap_1cm"])
        f = self.assert_fails("beside_aabb_near", "box", "table", "no contact under footprint")
        self.assertEqual("inf", f["gap_m"])

    def test_touching_side_face_is_not_support(self):
        f = self.assert_fails("beside_on_floor", "box", "table", "no contact under footprint")
        self.assertEqual("inf", f["gap_m"])

    def test_missing_supporter_fails(self):
        self.assert_fails("missing_supporter", "box", "shelf", "declared supporter missing or not a mesh")

    def test_cycle_fails(self):
        f = self.assert_fails("cycle", "table", "box", "support cycle", unsupported=2)
        self.assertEqual(["table", "box", "table"], f["cycle"])

    def test_supporter_without_declaration_fails(self):
        self.assert_fails("undeclared_supporter", "table", None, "supporter has no declared support")

    def test_broken_lower_link_unsupports_everything_above(self):
        f = self.assert_fails("lower_link_broken", "table", "floor", "no contact under footprint", unsupported=2)
        self.assertAlmostEqual(0.02, f["gap_m"], places=5)
        box = self.m("lower_link_broken")["per_object"]["box"]
        self.assertEqual("unsupported", box["status"])
        self.assertEqual("table", box["first_failure"]["object"])

    def test_multiple_supporters_each_need_contact(self):
        self.assertEqual("pass", self.r["two_posts"]["status"])
        self.assertEqual("beam -> (post_a -> floor | post_b -> floor)",
                         self.m("two_posts")["per_object"]["beam"]["chain"])
        f = self.assert_fails("two_posts_one_short", "beam", "post_b", "no contact under footprint")
        self.assertAlmostEqual(0.01, f["gap_m"], places=5)

    def test_hinge_needs_attached_declaration(self):
        self.assertEqual("pass", self.r["hinge_attached"]["status"])
        self.assertEqual(["door"], self.m("hinge_attached")["attached"])
        self.assert_fails("hinge_as_resting", "door", "frame", "no contact under footprint")

    def test_support_lost_on_later_frame(self):
        f = self.assert_fails("lifted_later", "box", "table", "no contact under footprint")
        self.assertEqual(6, f["frame"])
        m = self.m("lifted_later")
        self.assertEqual(10, m["frames_checked"])
        self.assertAlmostEqual(0.2, m["per_object"]["box"]["max_gap_m"], places=5)
        self.assertEqual(10, m["per_object"]["box"]["max_gap_frame"])


if __name__ == "__main__":
    unittest.main()
