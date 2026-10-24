"""worldagent_measure.deformable.cloth_setup tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_cloth_setup.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "cloth_setup_cases.py"

# Mirrors the case script: an 8 x 8 grid of side 1 m at object scale 2, then one level of
# simple subdivision before Cloth, so the solver mesh is a 16 x 16 grid of side 2 m.
CELLS = 16
SIDE_M = 2.0
EDGE_M = SIDE_M / CELLS


def m(r):
    return r["measurements"]["cloth_setup"]


@requires_blender
class ClothSetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def test_good_stack_passes(self):
        r = self.r["good"]
        self.assertEqual("pass", r["status"], r.get("first_failure"))
        self.assertEqual(["SolverSubdivision"], m(r)["before_cloth"])
        self.assertEqual(["Solidify", "RenderSubdivision"], m(r)["after_cloth"])
        self.assertTrue(m(r)["colliders"]["wall"]["collision_modifier"])

    def test_solver_mesh_counts_and_edges(self):
        s = m(self.r["good"])["solver"]
        self.assertEqual((CELLS + 1) ** 2, s["vertices"])
        self.assertEqual(2 * CELLS * (CELLS + 1), s["edges"])
        self.assertEqual(CELLS ** 2, s["faces"])
        self.assertAlmostEqual(SIDE_M ** 2, s["area_m2"], places=5)
        self.assertAlmostEqual(SIDE_M, s["extent_m"][0], places=6)
        for key in ("edge_median_m", "edge_p95_m", "edge_min_m", "edge_max_m"):
            self.assertAlmostEqual(EDGE_M, s[key], places=6)
        self.assertAlmostEqual((CELLS + 1) ** 2 / SIDE_M ** 2, s["vertices_per_m2"], places=4)

    def test_render_topology(self):
        # Solidify doubles the 16 x 16 sheet into a closed shell (2 sheets + 4 * 16 rim quads),
        # then render level 2 splits every quad into 16.
        shell_faces = 2 * CELLS ** 2 + 4 * CELLS
        self.assertEqual(shell_faces * 16, m(self.r["good"])["render"]["faces"])

    def test_cloth_settings(self):
        c = m(self.r["good"])["cloth_settings"]
        self.assertEqual(7, c["quality"])
        self.assertEqual(3, c["collision_quality"])
        self.assertTrue(c["use_self_collision"])
        self.assertAlmostEqual(0.01, c["distance_min_m"], places=6)
        self.assertAlmostEqual(0.004, c["self_distance_min_m"], places=6)
        self.assertAlmostEqual(0.2, c["mass_kg"], places=6)
        self.assertEqual("pin", c["pin_group"])

    def test_original_stack_untouched(self):
        self.assertTrue(self.r["visibility_restored"])
        self.assertTrue(self.r["objects_unchanged"])

    def test_collider_without_collision_fails(self):
        r = self.r["collider_without_collision"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("rigid_only", r["first_failure"]["collider"])
        self.assertTrue(r["first_failure"]["rigid_body"])

    def test_solidify_before_cloth_fails(self):
        r = self.r["solidify_before_cloth"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("Solidify before Cloth", r["first_failure"]["problem"])

    def test_no_cloth_fails(self):
        r = self.r["no_cloth"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("no Cloth modifier", r["first_failure"]["problem"])
        self.assertIsNone(m(r)["cloth_modifier"])

    def test_levels_differ_fails(self):
        r = self.r["levels_differ"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("SolverSubdivision", r["first_failure"]["modifier"])
        self.assertEqual(2, r["first_failure"]["render_levels"])

    def test_adaptive_fails(self):
        r = self.r["adaptive"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("adaptive subdivision before Cloth", r["first_failure"]["problem"])


if __name__ == "__main__":
    unittest.main()
