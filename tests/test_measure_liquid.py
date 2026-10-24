"""worldagent_measure.liquid tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_liquid.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "liquid_cases.py"


@requires_blender
class LiquidTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def failure(self, key):
        r = self.r[key]
        self.assertEqual("fail", r["status"], r)
        return r["first_failure"]

    def test_clean_closed_blob_passes(self):
        r = self.r["clean_pass"]
        self.assertEqual("pass", r["status"])
        m = r["measurements"]["liquid_surface"]
        self.assertEqual(3, m["frames_checked"])
        row = m["frames"][0]
        self.assertTrue(row["closed"])
        self.assertEqual((1, 0), (row["components"], row["zero_area_faces"]))
        self.assertLess(row["edge_spike_ratio"], 1.2)
        self.assertEqual(0.0, m["frames"][1]["volume_jump_rel"])

    def test_spike_vertex_fails_spike_ratio(self):
        f = self.failure("spike_fail")
        self.assertEqual(("water", 1, "edge_spike_ratio"), (f["object"], f["frame"], f["metric"]))
        self.assertGreater(f["value"], 4.0)

    def test_collapsed_vertex_fails_zero_area(self):
        f = self.failure("zero_area_fail")
        self.assertEqual("zero_area_faces", f["metric"])
        self.assertGreaterEqual(f["value"], 2)

    def test_detached_chunk_increases_components(self):
        f = self.failure("chunk_fail")
        self.assertEqual(("components", 2, 1), (f["metric"], f["value"], f["limit"]))

    def test_appearing_chunk_is_a_component_pop(self):
        f = self.failure("component_pop_fail")
        self.assertEqual(("component_jump", 2, 1, 1), (f["metric"], f["frame"], f["previous_frame"], f["value"]))

    def test_volume_jump_is_a_pop(self):
        f = self.failure("volume_pop_fail")
        self.assertEqual(("volume_jump_rel", 2), (f["metric"], f["frame"]))
        self.assertAlmostEqual(0.5, f["value"], places=5)

    def test_volume_matches_expected(self):
        r = self.r["volume_pass"]
        self.assertEqual("pass", r["status"])
        m = r["measurements"]["liquid_volume"]
        self.assertAlmostEqual(1.0, m["frames"][0]["signed_volume_m3"], places=6)
        self.assertLess(m["max_rel_error"], 1e-6)

    def test_volume_drop_beyond_tolerance_fails(self):
        f = self.failure("volume_drop_fail")
        self.assertEqual(("volume_rel_error", 3), (f["metric"], f["frame"]))
        self.assertAlmostEqual(0.8, f["volume_m3"], places=5)

    def test_open_surface_volume_is_not_passed(self):
        f = self.failure("volume_open_fail")
        self.assertEqual(("not_closed", 4), (f["metric"], f["value"]))
        row = self.r["volume_open_fail"]["measurements"]["liquid_volume"]["frames"][0]
        self.assertFalse(row["closed"])

    def test_touching_solid_passes(self):
        r = self.r["solid_touch_pass"]
        self.assertEqual("pass", r["status"])
        m = r["measurements"]["liquid_in_solids"]
        self.assertEqual(0, m["bad_vertices_total"])
        self.assertEqual(1, m["frames"][0]["pairs_tested"])
        self.assertTrue(m["solid_closed"]["box_near"])

    def test_vertices_inside_closed_solid_fail(self):
        f = self.failure("solid_inside_fail")
        self.assertEqual(("water", "box_near", 1), (f["object"], f["other_object"], f["frame"]))
        self.assertLess(f["value"], -1e-6)

    def test_inside_count_does_not_depend_on_distance_from_origin(self):
        near = self.r["solid_inside_fail"]["measurements"]["liquid_in_solids"]["bad_vertices_total"]
        far = self.r["solid_inside_far"]["measurements"]["liquid_in_solids"]["bad_vertices_total"]
        self.assertGreater(near, 0)
        self.assertEqual(near, far)

    def test_vertices_below_open_ground_fail(self):
        f = self.failure("ground_inside_fail")
        self.assertEqual("ground", f["other_object"])
        m = self.r["ground_inside_fail"]["measurements"]["liquid_in_solids"]
        self.assertFalse(m["solid_closed"]["ground"])

    def test_flip_not_installed_is_blocked(self):
        for key in ("flip_enable_blocked", "flip_forced_blocked", "flip_auto_blocked"):
            r = self.r[key]
            self.assertEqual("blocked", r["status"], key)
            self.assertIn("FLIP", r["reason"])
        self.assertTrue(self.r["flip_stored_domain_detected"])

    def test_real_mantaflow_bake_is_measured(self):
        r = self.r["mantaflow"]
        self.assertIn("no baked liquid mesh", r["missing_cache"])
        self.assertIn("use_mesh=False", r["mesh_disabled"])
        self.assertEqual("pass", r["quality"]["status"], r["quality"].get("first_failure"))
        rows = r["quality"]["measurements"]["liquid_surface"]["frames"]
        self.assertEqual(10, len(rows))
        self.assertTrue(all(x["triangles"] > 0 and x["closed"] for x in rows))
        self.assertEqual("pass", r["volume"]["status"])
        self.assertTrue(all(x["signed_volume_m3"] > 0 for x in r["volume"]["measurements"]["liquid_volume"]["frames"]))


if __name__ == "__main__":
    unittest.main()
