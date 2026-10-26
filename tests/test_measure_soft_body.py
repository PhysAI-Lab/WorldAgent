"""worldagent_measure.deformable Soft Body helpers against a real Blender (skipped when none is configured).

soft_body_setup, free_fall, transfer_deviation, plus verify_soft_body_example.py end to end.

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_soft_body.py
"""
import functools
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import BLENDER, requires_blender, run_cases  # noqa: E402

TESTS = Path(__file__).resolve().parent
SCRIPT = TESTS / "blender" / "soft_body_cases.py"
RUNNER = TESTS.parent / "bin" / "run_verifier.py"
VERIFIER = TESTS / "fixtures" / "verifiers" / "verify_soft_body_example.py"
SCENE = TESTS / "fixtures" / "verifiers" / "make_soft_body_drop.py"


@functools.cache
def cases() -> dict:
    """One Blender launch shared by both test classes."""
    return run_cases(SCRIPT)


def sb(r):
    return r["measurements"]["soft_body_setup"]


def ff(r):
    return r["measurements"]["free_fall"]


def td(r):
    return r["measurements"]["transfer_deviation"]


@requires_blender
class SoftBodySetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = cases()

    def assert_problem(self, key, problem, **fields):
        r = self.r[key]
        self.assertEqual("fail", r["status"], key)
        self.assertEqual(problem, r["first_failure"]["problem"], key)
        for k, v in fields.items():
            self.assertEqual(v, r["first_failure"][k], key)

    def test_good_setup_passes(self):
        r = self.r["good"]
        self.assertEqual("pass", r["status"], r.get("first_failure"))
        m = sb(r)
        # Soft Body requires original data: Blender keeps it above both Subdivisions.
        self.assertEqual([], m["before_soft_body"])
        self.assertEqual(["SolverSubdivision", "RenderSubdivision"], m["after_soft_body"])
        mesh = m["simulation_mesh"]
        self.assertEqual((56, 108, 54), (mesh["vertices"], mesh["edges"], mesh["faces"]))
        self.assertEqual((0, 0, 1, True, 0), (mesh["boundary_edges"], mesh["non_manifold_edges"], mesh["components"],
                                              mesh["closed"], mesh["self_intersections"]))
        self.assertAlmostEqual(0.4 / 3, mesh["edge_min_m"], places=6)
        self.assertAlmostEqual(0.4 ** 3, mesh["signed_volume_m3"], places=6)
        self.assertEqual(0.6, round(m["springs"]["pull"], 6))
        self.assertEqual(12, m["steps"]["step_min"])
        self.assertTrue(m["collision"]["use_self_collision"])
        self.assertEqual(0.0, m["mass_and_media"]["friction"])
        self.assertEqual(1.0, m["mass_and_media"]["gravity_weight"])
        self.assertAlmostEqual(0.001, m["solver_distance_m"], places=7)
        self.assertEqual(["soft_body"], m["physics"]["systems"])

    def test_original_untouched(self):
        self.assertTrue(self.r["stack_unchanged"])
        self.assertTrue(self.r["objects_unchanged"])
        self.assertEqual({"is_baked": True, "is_outdated": False}, self.r["baked_after"])

    def test_clock_block(self):
        t = sb(self.r["good"])["time"]
        self.assertAlmostEqual(math.sqrt(1000) / 24, t["real_time_speed_5_1"], places=9)
        self.assertAlmostEqual(1.0, t["clock_ratio_5_1"], places=4)
        self.assertAlmostEqual(24 / math.sqrt(1000), sb(self.r["speed_one"])["time"]["clock_ratio_5_1"], places=4)
        self.assertEqual("pass", self.r["speed_one"]["status"])  # the clock is reported, never judged here

    def test_no_soft_body(self):
        self.assert_problem("no_soft_body", "no Soft Body modifier")

    def test_second_solver(self):
        self.assert_problem("with_cloth", "another solver on the simulation object", systems=["cloth"])
        self.assert_problem("with_rigid", "another solver on the simulation object", systems=["bullet_rigid_body"])
        self.assertEqual("pass", self.r["with_collision"]["status"])

    def test_goal_rule(self):
        # Measured in 5.1.1: free-fall cases end at the floor of the probe, held cases stay up.
        expected = {"new_defaults": ("fail", 8), "all_w0_min0": ("pass", 0), "none_assigned": ("fail", 8),
                    "missing_group": ("fail", 8), "two_pinned": ("fail", 2), "default0": ("pass", 0),
                    "all_w1_max0": ("pass", 0), "one_w0_min05": ("fail", 1), "default0_min05": ("fail", 8)}
        got = {k: (v["status"], v["pinned"]) for k, v in self.r["goal_cases"].items()}
        self.assertEqual(expected, got)
        for v in self.r["goal_cases"].values():
            if v["status"] == "fail":
                self.assertEqual("Goal pins a free-falling body", v["problem"])

    def test_anchored_goal_allowed(self):
        self.assertEqual("pass", self.r["anchored"]["status"])
        self.assertEqual(8, sb(self.r["anchored"])["goal"]["pinned_vertices"])

    def test_stack_rules(self):
        c = self.r["stack_cases"]
        self.assertTrue(c["subsurf_move_refused"])
        self.assertEqual(("pass", ["Displace"]), (c["deform_ok"]["status"], c["deform_ok"]["before"]))
        self.assertEqual("fail", c["visibility"]["status"])
        self.assertEqual("enabled differently in viewport and render before Soft Body",
                         c["visibility"]["first_failure"]["problem"])

    def test_open_mesh(self):
        self.assertEqual("pass", self.r["open_alone"]["status"])
        self.assertEqual(32, sb(self.r["open_alone"])["simulation_mesh"]["boundary_edges"])
        self.assert_problem("open_with_visible", "simulation mesh not closed", boundary_edges=32)

    def test_transfer_detection(self):
        self.assertEqual("pass", self.r["sd_bound"]["status"])
        self.assertTrue(sb(self.r["sd_bound"])["transfer"]["sd_bound"]["driving_modifiers"][0]["is_bound"])
        self.assert_problem("sd_unbound", "transfer modifier not bound", visible="sd_unbound", modifier="SD")
        self.assert_problem("not_driven", "visible object not driven by the simulation object", visible="not_driven")
        for key, visible, via in (("gn_nested", "gn_visible", "node tree"), ("gn_input", "gn_input", "input Target"),
                                  ("gn_collection", "gn_collection", "node tree")):
            self.assertEqual("pass", self.r[key]["status"], key)
            self.assertEqual(via, sb(self.r[key])["transfer"][visible]["driving_modifiers"][0]["via"])

    def test_cage_transfer_rest(self):
        self.assertEqual("pass", self.r["cage"]["status"])
        t = sb(self.r["cage"])["transfer"]["caged"]
        self.assertEqual(1.0, t["inside_fraction"])
        self.assertAlmostEqual(self.r["cage_expected_max_m"], t["max_m"], places=6)
        frames = td(self.r["cage_deviation"])["frames"]
        self.assertEqual("pass", self.r["cage_deviation"]["status"])
        self.assertLess(max(f["max_change_m"] for f in frames), 1e-5)

    def test_supports(self):
        self.assert_problem("support_rigid_only", "support has no Collision modifier", support="rigid_only",
                            rigid_body=True)
        self.assert_problem("support_disabled", "support Collision disabled", support="plate")
        self.assert_problem("support_outside", "support outside the Soft Body collision collection", support="plate",
                            collision_collection="Colliders")
        self.assertAlmostEqual(0.004, sb(self.r["support_outside"])["solver_distance_m"], places=7)

    def test_cache_range_and_bake(self):
        self.assert_problem("short_cache", "cache frames 1-12 do not cover 1-24")
        self.assert_problem("unbaked_required", "the cache is not baked")
        self.assertEqual("pass", self.r["baked"]["status"], self.r["baked"].get("first_failure"))


@requires_blender
class FreeFallAndTransferTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = cases()

    def test_free_fall_keyed_exact(self):
        k = self.r["keyed"]
        for key in ("24_1.0_1.0_1.0", "48_2.0_1.0_1.0"):
            self.assertEqual("pass", k[key]["status"], key)
            self.assertAlmostEqual(1.0, ff(k[key])["a_over_expected"], places=5)
            self.assertAlmostEqual(0.0, ff(k[key])["v0_m_s"], places=5)
        self.assertEqual("fall acceleration off scene gravity", k["24_1.0_0.5_1.0"]["first_failure"]["problem"])
        self.assertEqual("pass", k["24_1.0_0.5_1.414"]["status"])

    def test_free_fall_soft_body_relational(self):
        f = self.r["sb_fall"]
        self.assertAlmostEqual(0.25, ff(f["speed05_24"])["a_m_s2"] / ff(f["speed1_24"])["a_m_s2"], places=3)
        # The same speed simulates the same time per frame at any fps.
        for a, b in zip(ff(f["speed1_24"])["frames"], ff(f["speed1_48"])["frames"]):
            self.assertAlmostEqual(a["mean_m"], b["mean_m"], places=6)
        self.assertAlmostEqual(0.7590, ff(f["speed1_24"])["measured_clock_ratio"], places=3)
        self.assertAlmostEqual(1.3176, ff(f["speed1_24"])["speed_for_real_time"], places=3)

    def test_free_fall_documented_constant(self):
        # Pins the playbook number: real time is speed = sqrt(1000) x fps_base / fps in 5.1.1.
        for key in ("real_24", "real_48", "real_60"):
            self.assertAlmostEqual(1.0, ff(self.r["sb_fall"][key])["a_over_expected"], delta=1e-4, msg=key)

    def test_free_fall_rigid(self):
        m = ff(self.r["rigid_fall"])
        self.assertEqual("pass", self.r["rigid_fall"]["status"])
        self.assertAlmostEqual(0.992, m["a_over_expected"], delta=0.002)
        self.assertAlmostEqual(-0.027, m["v0_m_s"], delta=0.005)

    def test_free_fall_unbaked_stepping(self):
        full = [r["mean_m"] for r in ff(self.r["stepping"]["full"])["frames"]][4:]
        late = [r["mean_m"] for r in ff(self.r["stepping"]["late"])["frames"]]
        self.assertEqual(len(full), len(late))
        for a, b in zip(full, late):
            self.assertAlmostEqual(a, b, places=7)

    def test_transfer_deviation_offset(self):
        rows = td(self.r["offset_pass"])["frames"]
        self.assertEqual([0.0, 0.0], [round(r["max_m"], 9) for r in rows[:2]])
        self.assertAlmostEqual(0.003, rows[2]["max_m"], places=6)
        self.assertEqual("pass", self.r["offset_pass"]["status"])
        f = self.r["offset_fail"]["first_failure"]
        self.assertEqual((3, "visible mesh left the simulation surface"), (f["frame"], f["problem"]))

    def test_errors(self):
        expected = {"ff_few_frames": "ValueError", "ff_gap": "ValueError", "ff_negative": "ValueError",
                    "ff_slow_motion_zero": "ValueError", "ff_no_threshold": "TypeError",
                    "td_non_increasing": "ValueError", "td_negative": "ValueError", "td_same": "ValueError",
                    "setup_non_mesh": "ValueError", "setup_bad_range": "ValueError", "ff_gravity_off": "ValueError"}
        self.assertEqual(expected, self.r["errors"])


@requires_blender
class ExampleVerifierTest(unittest.TestCase):
    """verify_soft_body_example.py end to end through bin/run_verifier.py."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case = Path(self._tmp.name) / "case"

    def tearDown(self):
        self._tmp.cleanup()

    def run_case(self, *scene_args: str):
        candidate = self.case / ".work" / "candidates" / "c01" / "scene.blend"
        candidate.parent.mkdir(parents=True)
        (self.case / "src").mkdir()
        (self.case / "src" / VERIFIER.name).write_bytes(VERIFIER.read_bytes())
        subprocess.run([BLENDER, "--background", "--factory-startup", "--python", str(SCENE), "--",
                        str(candidate), *scene_args], check=True, capture_output=True)
        manifest = {
            "contract": {"fps": 24, "frame_range": [1, 24], "story": {"beats": []}},
            "requirements_matrix": [
                {"id": "jelly_falls_freely", "text": "The jelly falls freely onto the plate", "category": "physics",
                 "pass_predicate": "Soft Body setup structural checks pass; fall acceleration within 2% of g; "
                                   "visible mesh within 4 mm of the simulation surface",
                 "status": "unknown"},
            ],
        }
        (self.case / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(RUNNER), str(self.case), "--row", "jelly_falls_freely", "--script",
             f"src/{VERIFIER.name}", "--candidate", ".work/candidates/c01/scene.blend"],
            capture_output=True, text=True, cwd=self.case, env={**os.environ, "WORLDAGENT_BLENDER": BLENDER},
        )
        row = json.loads((self.case / "manifest.json").read_text(encoding="utf-8"))["requirements_matrix"][0]
        report = json.loads((self.case / row["evidence"][0]).read_text(encoding="utf-8"))
        return proc, row, report

    def test_pass(self):
        proc, row, report = self.run_case()
        self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
        self.assertEqual("pass", row["status"], row.get("first_failure"))
        mm = report["result"]["measurements"]
        self.assertEqual({"soft_body_setup", "free_fall", "transfer_deviation"}, set(mm))
        self.assertTrue(mm["soft_body_setup"]["cache"]["is_baked"])
        self.assertAlmostEqual(1.0, mm["free_fall"]["a_over_expected"], delta=0.02)
        self.assertEqual(24, mm["transfer_deviation"]["frames_checked"])

    def test_fail_goal_left_on(self):
        proc, row, _ = self.run_case("goal")
        self.assertEqual(1, proc.returncode)
        self.assertEqual("fail", row["status"])
        self.assertEqual("soft_body_setup", row["first_failure"]["check"])
        self.assertEqual("Goal pins a free-falling body", row["first_failure"]["problem"])


if __name__ == "__main__":
    unittest.main()
