"""worldagent_measure.rigid_setup against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_rigid_setup.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "rigid_setup_cases.py"


@requires_blender
class RigidSetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def test_origin_at_center_passes(self):
        self.assertEqual("pass", self.r["origin_good_only"]["status"])

    def test_applied_location_moves_origin_off_center(self):
        r = self.r["origin_all"]
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual(("moved", "volume"), (f["object"], f["method"]))
        self.assertAlmostEqual((2 ** 2 + 1) ** 0.5, f["offset_m"], places=5)
        com, method = self.r["moved_com"]
        self.assertEqual("volume", method)
        for got, want in zip(com, (2, 0, 1)):
            self.assertAlmostEqual(want, got, places=5)  # single-precision vertices

    def test_constraint_outside_world_collection_is_reported(self):
        ok = self.r["constraints_ok"]
        self.assertEqual(("pass", 1), (ok["status"], ok["measurements"]["constraints_in_world"]["constraint_count"]))
        bad = self.r["constraints_ignored"]
        self.assertEqual("fail", bad["status"])
        self.assertIn("Bullet ignores it", bad["first_failure"]["problems"][0])

    def test_body_outside_world_collection_is_reported(self):
        self.assertEqual("pass", self.r["bodies_ok"]["status"])
        bad = self.r["bodies_outside"]
        self.assertEqual(("fail", "moved"), (bad["status"], bad["first_failure"]["object"]))
        self.assertIn("does not simulate it", bad["first_failure"]["problems"][0])

    def test_dynamic_off_and_missing_rigid_body_are_reported(self):
        r = self.r["bodies_disabled"]
        self.assertEqual("fail", r["status"])
        failing = {f["object"]: f["problems"] for f in r["measurements"]["bodies_in_world"]["failing"]}
        self.assertIn("Dynamic", failing["moved"][0])
        self.assertEqual(["has no rigid body"], failing["plain"])
        self.assertEqual("fail", self.r["bodies_pinned_off"]["status"])  # one key: never switched on
        animated = self.r["bodies_animated_dynamic"]
        self.assertEqual("pass", animated["status"])
        self.assertEqual(["moved"], animated["measurements"]["bodies_in_world"]["animated_dynamic"])

    def test_keyed_active_body_fails_unless_kinematic(self):
        self.assertEqual("pass", self.r["keyed_none"]["status"])
        bad = self.r["keyed_active"]
        self.assertEqual("fail", bad["status"])
        self.assertEqual(("good", ["location"]),
                         (bad["first_failure"]["object"], bad["first_failure"]["transform_paths"]))
        for key in ("keyed_declared", "keyed_kinematic"):
            r = self.r[key]
            self.assertEqual("pass", r["status"], key)
            self.assertEqual(["good"], r["measurements"]["keyed_active_bodies"]["keyed_kinematic"])

    def test_animated_release_is_information(self):
        r = self.r["keyed_release"]
        self.assertEqual("pass", r["status"])
        self.assertEqual([{"object": "good", "transform_paths": ["location"]}],
                         r["measurements"]["keyed_active_bodies"]["animated_release"])

    def test_cache_coverage(self):
        unbaked = self.r["cache_unbaked"]
        self.assertEqual(("fail", ["the cache is not baked"]),
                         (unbaked["status"], unbaked["first_failure"]["problems"]))
        baked = self.r["cache_baked"]
        self.assertEqual("pass", baked["status"], baked)
        self.assertTrue(baked["measurements"]["cache_coverage"]["is_baked"])
        short = self.r["cache_short"]
        self.assertEqual("fail", short["status"])
        self.assertIn("do not cover 1-10", short["first_failure"]["problems"][0])
        none = self.r["cache_no_world"]
        self.assertEqual(("fail", "the scene has no rigid body world"),
                         (none["status"], none["first_failure"]["problem"]))

    def fit(self, key):
        r = self.r[key]
        return r, r["measurements"]["collision_shape_fit"]["bodies"][0]

    def test_cylinder_shape_on_mesh_along_x_is_an_air_wall(self):
        r, body = self.fit("fit_cylinder_x")
        self.assertEqual("fail", r["status"])
        self.assertEqual({"radius": 0.2, "half_height": 0.05},
                         {k: round(v, 6) for k, v in body["size_m"].items()})
        self.assertGreater(body["protrusion_m"], 0.1)
        # The end-cap rims at x = +-0.2 reach past the radius-0.2 disc.
        self.assertAlmostEqual((0.2 ** 2 + 0.05 ** 2) ** 0.5 - 0.2, body["uncovered_m"], places=5)

    def test_matching_primitives_fit(self):
        for key in ("fit_cylinder_z", "fit_box"):
            r, body = self.fit(key)
            self.assertEqual("pass", r["status"], key)
            self.assertLess(body["protrusion_m"], 5e-4, key)  # 32-sided cylinder: 0.24 mm sagitta
            self.assertLess(body["uncovered_m"], 1e-5, key)

    def test_sphere_on_bar_protrudes_and_leaves_corners_uncovered(self):
        r, body = self.fit("fit_sphere_on_bar")
        self.assertEqual("fail", r["status"])
        self.assertAlmostEqual(0.2, body["size_m"]["radius"], places=5)
        self.assertGreater(body["protrusion_m"], 0.1)
        self.assertAlmostEqual((0.2 ** 2 + 2 * 0.05 ** 2) ** 0.5 - 0.2, body["uncovered_m"], places=4)
        self.assertEqual(2, len(r["first_failure"]["problems"]))

    def test_default_margin_larger_than_thin_box(self):
        r, body = self.fit("fit_thin_default_margin")
        self.assertEqual("fail", r["status"])
        self.assertAlmostEqual(0.04, body["margin_m"], places=6)
        self.assertIn("exceeds a half extent", r["first_failure"]["problems"][0])

    def test_convex_hull_fills_torus_hole(self):
        r, body = self.fit("fit_torus_hull")
        self.assertEqual(("fail", "added outside"), (r["status"], body["margin_mode"]))
        self.assertAlmostEqual((0.1 ** 2 + 0.02 ** 2) ** 0.5 - 0.02, body["protrusion_m"], delta=3e-3)  # 12-sided tube
        x, y, _ = body["protrusion_at"]
        self.assertLess(((x - 5) ** 2 + y ** 2) ** 0.5, 0.01)  # over the hole centre
        self.assertLess(body["uncovered_m"], 1e-6)

    def test_convex_hull_on_box_fits(self):
        r, body = self.fit("fit_box_hull")
        self.assertEqual("pass", r["status"])
        self.assertAlmostEqual(0.0005, body["protrusion_m"], places=5)  # the margin, added outside
        self.assertLess(body["uncovered_m"], 1e-6)

    def test_mesh_shapes_are_not_checked(self):
        m = self.r["fit_all"]["measurements"]["collision_shape_fit"]
        self.assertEqual([{"object": "trimesh", "shape": "MESH"}], m["not_checked"])
        self.assertEqual(7, m["bodies_checked"])


if __name__ == "__main__":
    unittest.main()
