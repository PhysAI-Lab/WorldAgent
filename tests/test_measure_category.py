"""worldagent_measure.gas and .deformable tests against a real Blender (skipped when none is configured).

Bakes a small Mantaflow smoke domain and steps a small cloth. Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_category.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "category_cases.py"


@requires_blender
class CategoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT, timeout=1200)

    def m(self, key, check):
        return self.r[key]["measurements"][check]

    def failure(self, key):
        self.assertEqual("fail", self.r[key]["status"], key)
        return self.r[key]["first_failure"]

    def test_gas_density_series(self):
        self.assertEqual(["FINISHED"], self.r["gas_bake"])
        self.assertEqual("pass", self.r["gas_pass"]["status"])
        m = self.m("gas_pass", "gas_density")
        totals = [f["total"] for f in m["frames"]]
        self.assertEqual(12, len(totals))
        self.assertTrue(all(b >= a for a, b in zip(totals, totals[1:])), totals)  # the emitter keeps adding gas
        self.assertEqual([16, 16, 16], m["grid"])
        # the source's emission shell reaches the guard layer above the floor, and only that face
        self.assertTrue(all(f["guard_fraction_by_face"]["z_min"] > 0 for f in m["frames"]))
        self.assertTrue(all(f["guard_fraction_by_face"]["z_max"] == 0 for f in m["frames"]))
        self.assertEqual([0.125] * 3, m["cell_size_m"])

    def test_gas_reaching_the_border_fails(self):
        self.assertEqual(["FINISHED"], self.r["jet_bake"])
        self.assertEqual("pass", self.r["jet_early"]["status"])
        f = self.failure("jet_walls")
        self.assertEqual(("gas on the domain walls", "z_max"), (f["problem"], f["face"]))
        frames = self.m("jet_walls", "gas_density")["frames"]
        last = frames[-1]
        self.assertEqual(24, last["frame"])
        self.assertGreater(last["guard_fraction_by_face"]["z_max"], 0.05)
        # Mantaflow's outermost layer reads zero even where gas leaves (the old measure, which could not fail)
        self.assertTrue(all(fr["boundary_layer_fraction"] == 0.0 for fr in frames))

    def test_gas_cell_size_in_world_units(self):
        self.assertEqual(["FINISHED"], self.r["scaled_bake"])
        s = self.r["scaled"]
        self.assertEqual([16, 8, 8], s["grid"])
        for got in s["cell_size_m"]:
            self.assertAlmostEqual(0.25, got, places=6)
        self.assertGreater(s["raw_sum"], 0)
        self.assertAlmostEqual(s["raw_sum"] * 0.25 ** 3, s["total"], places=6)

    def test_gas_failures(self):
        self.assertEqual("sudden density jump", self.failure("gas_jump")["problem"])
        self.assertEqual(("total density below minimum", 1),
                         tuple(self.failure("gas_min_total")[k] for k in ("problem", "frame")))
        self.assertEqual(0.0, self.r["gas_unbaked_total"])
        self.assertEqual("ValueError", self.r["gas_not_domain"])
        self.assertEqual("TypeError", self.r["gas_no_guard"])  # guard_layers is required

    def test_shape_key_strain_and_step(self):
        m = self.m("deform_pass", "deformation")
        self.assertAlmostEqual(0.5, m["largest_stretch_rel"], places=5)
        self.assertAlmostEqual(0.05, m["largest_step_m"], places=5)
        self.assertAlmostEqual(2.0, m["shortest_rest_edge_m"], places=5)
        self.assertEqual(0, m["largest_self_intersections"])
        self.assertIsNone(m["collision_distance_m"])
        f = self.failure("deform_stretch")
        self.assertEqual(("overstretched", 4), (f["problem"], f["frame"]))
        self.assertEqual("moved too far in one step", self.failure("deform_step")["problem"])

    def test_recovery(self):
        self.assertEqual("pass", self.r["recovered"]["status"])
        f = self.failure("not_recovered")
        self.assertAlmostEqual(0.5, f["deviation_rel"], places=5)

    def test_cloth_reports_collision_scale(self):
        m = self.m("cloth", "deformation")
        self.assertGreater(m["collision_distance_m"], 0)
        self.assertGreater(m["largest_step_m"], 0)
        self.assertAlmostEqual(m["largest_step_m"] / m["collision_distance_m"], m["largest_step_over_collision_distance"])


if __name__ == "__main__":
    unittest.main()
