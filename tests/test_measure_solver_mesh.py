"""worldagent_measure.solver_mesh tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_solver_mesh.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "solver_mesh_cases.py"


def obj(r, name):
    return r["measurements"]["solver_mesh_validity"]["objects"][name]


@requires_blender
class SolverMeshTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def test_clean_cube_passes(self):
        r = self.r["clean"]
        self.assertEqual("pass", r["status"])
        o = obj(r, "solid")
        self.assertEqual(0, o["open_edges"])
        self.assertEqual(0, o["self_intersecting_triangle_pairs"])
        self.assertAlmostEqual(1.0, o["signed_volume_m3"], places=6)
        self.assertAlmostEqual(10.0, o["narrowest_dimension_cells"], places=4)
        self.assertAlmostEqual(1.0, o["min_local_thickness_m"], places=4)

    def test_topology_only_has_no_thickness(self):
        self.assertEqual("pass", self.r["topology_only"]["status"])
        self.assertNotIn("narrowest_dimension_cells", obj(self.r["topology_only"], "solid"))

    def test_collinear_ngon_is_not_zero_area(self):
        r = self.r["collinear_ngon"]
        self.assertEqual("pass", r["status"], r.get("first_failure"))
        self.assertEqual(0, obj(r, "ngon")["zero_area_polygons"])

    def test_open_mesh_fails(self):
        r = self.r["open"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("open_edges", r["first_failure"]["metric"])
        self.assertEqual(4, obj(r, "holed")["open_edges"])

    def test_self_intersecting_mesh_fails(self):
        r = self.r["self_intersecting"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("self_intersecting_triangle_pairs", r["first_failure"]["metric"])
        self.assertEqual(2, obj(r, "slab")["components"])

    def test_sub_cell_wall_fails(self):
        r = self.r["thin"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("narrowest_dimension_cells", r["first_failure"]["metric"])
        self.assertAlmostEqual(0.5, r["first_failure"]["value"], places=4)
        self.assertEqual(["door_leaf"], r["measurements"]["solver_mesh_validity"]["thin_objects"])

    def test_inverted_normals_fail(self):
        r = self.r["inverted"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("inverted_normals", r["first_failure"]["metric"])
        self.assertLess(obj(r, "flipped")["signed_volume_m3"], 0)

    def test_thin_carpet_alone_fails(self):
        r = self.r["carpet_alone"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("carpet", r["first_failure"]["object"])
        self.assertEqual("narrowest_dimension_cells", r["first_failure"]["metric"])

    def test_carpet_grouped_with_floor_passes(self):
        r = self.r["carpet_grouped"]
        self.assertEqual("pass", r["status"], r.get("first_failure"))
        g = r["measurements"]["solver_mesh_validity"]["groups"]["floor"]
        self.assertAlmostEqual(0.503 / 0.1, g["narrowest_dimension_cells"], places=4)
        self.assertEqual(0.0, g["member_gap_m"]["carpet"])
        self.assertEqual("floor", obj(r, "carpet")["thickness_judged_by_group"])

    def test_floating_member_breaks_group(self):
        r = self.r["carpet_floating_grouped"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("group_member_gap", r["first_failure"]["metric"])
        self.assertAlmostEqual(0.05, r["first_failure"]["value"], places=4)

    def test_group_arguments(self):
        self.assertEqual({"member_not_checked": "ValueError", "no_tolerance": "ValueError"},
                         self.r["group_errors"])

    def test_mantaflow_cell_size(self):
        self.assertAlmostEqual(4.0 / 32, float(self.r["cell_size"]), places=6)

    def test_bad_arguments(self):
        self.assertEqual({"no_objects": "ValueError", "cell_without_min": "ValueError",
                          "not_a_domain": "ValueError"}, self.r["errors"])


if __name__ == "__main__":
    unittest.main()
