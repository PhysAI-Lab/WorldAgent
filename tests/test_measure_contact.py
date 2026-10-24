"""worldagent_measure.contact tests against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_contact.py
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import BLENDER, requires_blender, run_cases  # noqa: E402

TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS / "blender" / "contact_cases.py"
RUNNER = TESTS.parent / "bin" / "run_verifier.py"
VERIFIER = TESTS / "fixtures" / "verifiers" / "verify_contact_example.py"
SCENES = TESTS / "fixtures" / "scenes" / "contact" / "contact_scenes.py"
EPS = 1e-6
# Depths are measured with float32 BVH queries.
PLACES = 5


def m(r):
    return r["measurements"]["interpenetration"]


@requires_blender
class PenetrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT)

    def assert_fails_at(self, r, pair, depth, frame=1, kind="surface_intersection"):
        self.assertEqual("fail", r["status"])
        f = r["first_failure"]
        self.assertEqual(set(pair), set(f["pair"]))
        self.assertEqual(frame, f["frame"])
        self.assertEqual(kind, f["kind"])
        self.assertAlmostEqual(depth, f["value"], places=PLACES)
        self.assertAlmostEqual(-depth, f["signed_clearance_m"], places=PLACES)
        self.assertGreaterEqual(m(r)["max_penetration_depth_m"], f["value"])

    def test_resting_contact_passes(self):
        r = self.r["resting"]
        self.assertEqual("pass", r["status"])
        mm = m(r)
        self.assertEqual(1, mm["broad_phase_candidates"])
        self.assertGreater(mm["triangle_overlap_pairs"], 0)
        self.assertGreater(mm["depth_points_tested"], 0)
        self.assertLessEqual(abs(mm["min_signed_clearance_m"]), EPS)
        self.assertLessEqual(mm["max_penetration_depth_m"], EPS)
        self.assertEqual(EPS, mm["eps_pen_m"])
        self.assertEqual(0, mm["penetrating_pair_count"])

    def test_sunk_box_fails_with_sink_depth(self):
        self.assert_fails_at(self.r["sunk"], ("box", "table"), 0.02)
        self.assertAlmostEqual(0.02, m(self.r["sunk"])["max_penetration_depth_m"], places=PLACES)
        self.assertEqual(1, m(self.r["sunk"])["penetrating_pair_count"])

    def test_explicit_pair(self):
        r = self.r["sunk_explicit_pair"]
        self.assert_fails_at(r, ("box", "table"), 0.02)
        self.assertEqual(1, m(r)["pairs_checked"])

    def test_shallow_sink_measured_against_given_eps(self):
        self.assert_fails_at(self.r["shallow_eps_small"], ("box", "table"), 1e-4)
        r = self.r["shallow_eps_large"]
        self.assertEqual("pass", r["status"])
        self.assertAlmostEqual(1e-4, m(r)["max_penetration_depth_m"], places=PLACES)

    def test_hidden_box_still_fails(self):
        self.assertFalse(self.r["hidden_box_visible"])
        self.assertTrue(self.r["hidden_wall_visible"])
        r = self.r["hidden"]
        self.assert_fails_at(r, ("box", "table"), 0.02)
        self.assertEqual(3, m(r)["pairs_checked"])
        self.assertEqual(1, m(r)["penetrating_pair_count"])

    def test_contained_mesh_fails(self):
        r = self.r["contained"]
        # Inner box vertices at x = 0.1 are 0.4 m from the outer faces.
        self.assert_fails_at(r, ("inner", "outer"), 0.4, kind="containment")
        self.assertEqual(0, m(r)["triangle_overlap_pairs"])
        self.assertEqual("inner", r["first_failure"]["object"])
        self.assertEqual("outer", r["first_failure"]["other_object"])

    def test_joined_overlapping_shells(self):
        r = self.r["joined_shells"]
        # The box bottom is 0.02 m under the first shell's top face, inside both shells, where
        # whole-mesh parity is even. It must still fail. Depth against internal faces is
        # ambiguous by nature, so the slab must be reported as self-intersecting.
        self.assertEqual("fail", r["status"])
        self.assertEqual({"box", "slab"}, set(r["first_failure"]["pair"]))
        self.assertGreaterEqual(r["first_failure"]["value"], 0.02 - 10 ** -PLACES)
        self.assertGreater(m(r)["self_intersecting_meshes"].get("slab", 0), 0)
        self.assertGreater(r["first_failure"]["self_intersecting"].get("slab", 0), 0)
        self.assertEqual({}, m(self.r["sunk"])["self_intersecting_meshes"])

    def test_first_failing_frame(self):
        r = self.r["animated"]
        self.assert_fails_at(r, ("box", "table"), 0.01, frame=5)
        mm = m(r)
        self.assertEqual(6, mm["frames_checked"])
        self.assertEqual(2, mm["penetrating_frame_count"])
        self.assertEqual(5, mm["first_penetrating_frame"])
        self.assertAlmostEqual(0.03, mm["max_penetration_depth_m"], places=PLACES)
        self.assertEqual(1, self.r["animated_frame_after"])

    def test_open_ground_plane(self):
        self.assertEqual("pass", self.r["ground_resting"]["status"])
        self.assertEqual(["ground"], m(self.r["ground_resting"])["open_meshes"])
        self.assert_fails_at(self.r["ground_sunk"], ("box", "ground"), 0.05)

    def test_separated_pair_reports_no_narrow_phase(self):
        r = self.r["apart"]
        self.assertEqual("pass", r["status"])
        self.assertEqual(0, m(r)["broad_phase_candidates"])
        self.assertIsNone(m(r)["min_signed_clearance_m"])
        self.assertIsNone(m(r)["max_penetration_depth_m"])

    def assert_same_as_per_frame(self, case):
        """Reusing unchanged surfaces and pairs must not change any result."""
        self.assertEqual(case["reference"], case["summary"])
        return m(case["result"])

    def test_static_table_reused_over_long_range(self):
        case = self.r["static_table"]
        mm = self.assert_same_as_per_frame(case)
        self.assert_fails_at(case["result"], ("box", "table"), 0.002, frame=11)
        self.assertEqual(20, mm["penetrating_frame_count"])
        self.assertEqual(["box"], mm["changed_objects"])
        # Table once; box on frame 1 and on each frame 11..20 where it moves.
        self.assertEqual(1 + 1 + 10, mm["surfaces_built"])
        self.assertEqual(2 * 30 - 12, mm["surfaces_reused"])
        self.assertEqual(11, mm["pairs_measured"])
        self.assertEqual(19, mm["pairs_reused"])

    def test_transform_change_rebuilds(self):
        case = self.r["transform_jump"]
        mm = self.assert_same_as_per_frame(case)
        self.assert_fails_at(case["result"], ("box", "table"), 0.02, frame=6)
        self.assertEqual(["box"], mm["changed_objects"])
        self.assertEqual(3, mm["surfaces_built"])

    def test_deformation_without_transform_rebuilds(self):
        for key, frame in (("shape_key", 5), ("modifier", 4)):
            with self.subTest(key):
                case = self.r[key]
                mm = self.assert_same_as_per_frame(case)
                self.assert_fails_at(case["result"], ("box", "table"), 0.02, frame=frame)
                self.assertEqual(["box"], mm["changed_objects"])
                self.assertEqual(3, mm["surfaces_built"])

    def test_rigid_body_cache(self):
        case = self.r["rigid_body"]
        mm = self.assert_same_as_per_frame(case)
        # Bullet moves the box on every frame; the passive table is built once.
        self.assertEqual(["box"], mm["changed_objects"])
        self.assertEqual(1 + 40, mm["surfaces_built"])
        self.assertEqual(40 - 1, mm["surfaces_reused"])

    def test_bad_arguments(self):
        self.assertEqual({"no_eps": "TypeError", "negative_eps": "ValueError", "no_frames": "ValueError",
                          "self_pair": "ValueError"}, self.r["errors"])


@requires_blender
class ExampleVerifierTest(unittest.TestCase):
    """verify_contact_example.py end to end through bin/run_verifier.py."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case = Path(self._tmp.name) / "case"

    def tearDown(self):
        self._tmp.cleanup()

    def run_case(self, sink: float):
        candidate = self.case / ".work" / "candidates" / "c01" / "scene.blend"
        candidate.parent.mkdir(parents=True)
        (self.case / "src").mkdir()
        (self.case / "src" / VERIFIER.name).write_bytes(VERIFIER.read_bytes())
        subprocess.run([BLENDER, "--background", "--factory-startup", "--python", str(SCENES), "--",
                        str(candidate), str(sink)], check=True, capture_output=True)
        manifest = {
            "contract": {"fps": 24, "frame_range": [1, 3], "story": {"beats": []}},
            "requirements_matrix": [
                {"id": "no_penetration", "text": "The box does not penetrate the table", "category": "physics",
                 "pass_predicate": "signed clearance >= -1e-6 m on every frame", "status": "unknown"},
            ],
        }
        (self.case / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(RUNNER), str(self.case), "--row", "no_penetration", "--script",
             f"src/{VERIFIER.name}", "--candidate", ".work/candidates/c01/scene.blend"],
            capture_output=True, text=True, cwd=self.case, env={**os.environ, "WORLDAGENT_BLENDER": BLENDER},
        )
        row = json.loads((self.case / "manifest.json").read_text(encoding="utf-8"))["requirements_matrix"][0]
        report = json.loads((self.case / row["evidence"][0]).read_text(encoding="utf-8"))
        return proc, row, report

    def test_pass(self):
        proc, row, report = self.run_case(0.0)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("pass", row["status"])
        mm = report["result"]["measurements"]["interpenetration"]
        self.assertEqual(3, mm["frames_checked"])
        self.assertEqual(EPS, mm["eps_pen_m"])
        self.assertEqual([1, 3], report["result"]["frames"])

    def test_fail(self):
        proc, row, _ = self.run_case(0.02)
        self.assertEqual(1, proc.returncode)
        self.assertEqual("fail", row["status"])
        self.assertEqual(1, row["first_failure"]["frame"])
        self.assertEqual({"box", "table"}, set(row["first_failure"]["pair"]))
        self.assertAlmostEqual(0.02, row["first_failure"]["value"], places=PLACES)


if __name__ == "__main__":
    unittest.main()
