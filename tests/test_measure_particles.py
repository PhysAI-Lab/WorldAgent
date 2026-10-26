"""worldagent_measure.particles tests against a real Blender (skipped when none is configured).

One Blender process builds every scene (tests/blender/particle_cases.py):
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest tests/test_measure_particles.py
WORLDAGENT_SLOW_TESTS=1 also bakes a default DDR system (solver behaviour, may change between versions).
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "particle_cases.py"
SLOW = os.environ.get("WORLDAGENT_SLOW_TESTS") == "1"


def m(result, name):
    return result["measurements"][name]


@requires_blender
class ParticlesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT, *(["slow"] if SLOW else []))

    def status(self, key):
        return self.r[key]["status"]

    def failure(self, key):
        self.assertEqual("fail", self.r[key]["status"], key)
        return self.r[key]["first_failure"]

    def test_state_reads_evaluated_world_space(self):
        g = self.r["grid_sphere"]
        self.assertEqual((616, 1000, 0), (g["evaluated"], g["count"], g["original"]))
        for got, want in zip(g["mean"], (10, 0, 3)):
            self.assertAlmostEqual(want, got, places=4)
        self.assertEqual(616, g["counts"]["alive"])
        self.assertTrue(g["status"]["exempt"])
        self.assertEqual([], g["status"]["problems"])
        self.assertEqual(5, self.r["frame_restored"])
        self.assertEqual(5, self.r["frame_after"])

    def test_memory_cache_ok(self):
        s = self.r["memory_status"]
        self.assertEqual(([], 36, "memory", True), (s["problems"], s["cached_frames"], s["storage"], s["is_baked"]))
        self.assertEqual("pass", self.status("coverage_ok"))
        self.assertEqual("cache frames 1-36 do not cover 1-48", self.failure("coverage_short")["reason"])

    def test_disk_cache_ok(self):
        d = self.r["disk_ok"]
        self.assertEqual(37, self.r["disk_files_on_disk"])  # frame 0 included
        self.assertEqual(("36 frames on disk.", 36, "disk", []),
                         (d["status"]["info"], d["status"]["disk_files"], d["status"]["storage"], d["status"]["problems"]))
        self.assertEqual("pass", d["coverage"]["status"])
        self.assertAlmostEqual(0.5101, d["z_min_24"], places=4)
        self.assertIsNone(d["raised"])

    def test_disk_cache_folder_missing(self):
        d = self.r["disk_missing"]
        self.assertTrue(d["status"]["is_baked"])
        self.assertEqual(0, d["status"]["cached_frames"])
        self.assertEqual("fail", d["coverage"]["status"])
        self.assertIn("cache folder missing", " ".join(d["status"]["problems"]))
        self.assertIn("0 cached frames", d["raised"])
        self.assertTrue(d["is_cache_missing"])
        rec = d["recorded"]
        self.assertEqual(("fail", "particle_cache", "cache missing", "rests/Particles"),
                         (rec["status"], rec["first_failure"]["check"], rec["first_failure"]["reason"],
                          rec["first_failure"]["system"]))
        self.assertTrue(rec["measurements"]["particle_cache"]["cache_missing"])

    def test_unbaked_and_out_of_range(self):
        u = self.r["unbaked"]
        self.assertEqual("not baked", u["coverage"]["first_failure"]["reason"])
        self.assertEqual("ParticleCacheMissing", u["state"])
        self.assertEqual("ParticleCacheMissing", self.r["out_of_range"])
        self.assertEqual({"hair": "ValueError", "missing": "ValueError", "wrong_name": "ValueError"},
                         self.r["ref_errors"])

    def test_counts_schedule(self):
        rows = self.r["staggered_rows"]
        # 100 particles born over frames 1..10, lifetime 5; the bake stops at 16 (all dead), later frames read as dead
        self.assertEqual({"unborn": 99, "alive": 1, "dying": 0, "dead": 0, "total": 100}, rows[0])
        self.assertEqual({"unborn": 44, "alive": 55, "dying": 0, "dead": 1, "total": 100}, rows[5])
        self.assertEqual(100, rows[-1]["dead"])
        emitted = self.r["staggered_emitted"]
        self.assertEqual([1, 12, 23, 34, 45, 56, 67, 78, 89, 100], [emitted[str(f)] for f in range(1, 11)])
        self.assertEqual("pass", self.status("counts_exact"))
        f = self.failure("counts_off0")
        self.assertEqual((1, "emitted count off", 1, 2), (f["frame"], f["reason"], f["value"], f["expected"]))
        self.assertEqual("pass", self.status("counts_off1"))
        self.assertEqual("ValueError", self.r["counts_none"])

    def test_setup(self):
        self.assertEqual("pass", self.status("setup_rests"))
        setup = m(self.r["setup_rests"], "particle_setup")
        self.assertAlmostEqual(0.01, setup["solver_length_m"], places=6)
        self.assertTrue(setup["colliders"]["box"]["acts"])
        cases = {"setup_passes": "collider box is not in collision_collection no_colliders",
                 "setup_plain": "collider plain_box has no Collision modifier",
                 "setup_hidden": "the emitter renders (hide_render off, show_instancer_for_render and visible_camera on)",
                 "setup_sph_on_newton": "physics type NEWTON, the contract's sph_particles needs FLUID"}
        for key, reason in cases.items():
            self.assertEqual(reason, self.failure(key)["reason"], key)
        self.assertEqual("pass", self.status("setup_hidden_off"))
        self.assertIn("2 FLUID systems on the emitter", self.failure("setup_two_fluid")["reason"])
        child = m(self.r["setup_child"], "particle_setup")
        self.assertEqual("pass", self.status("setup_child"))  # children are reported, never a failure
        self.assertEqual(("SIMPLE", ["only parent particles are simulated and measured"]),
                         (child["settings"]["child_type"], child["limitations"]))
        self.assertEqual({"sph_without_solver": "ValueError", "bad_system": "ValueError"}, self.r["setup_errors"])

    def test_penetration_size_radius(self):
        self.assertEqual("pass", self.status("pen_rests"))
        p = m(self.r["pen_rests"], "particle_penetration")
        self.assertAlmostEqual(1e-4, p["min_clearance_m"], delta=2e-6)
        self.assertAlmostEqual(0.0101, p["min_center_clearance_m"], delta=2e-6)
        self.assertEqual({"box": {"closed": True, "planar_fast_path": False}}, p["solids"])

    def test_penetration_float_radius_and_tolerance(self):
        f = self.failure("pen_rests_02")
        self.assertEqual((9, "box", 64, 1.0), (f["frame"], f["other_object"], f["count"], f["fraction"]))
        self.assertAlmostEqual(-0.0099, f["value"], delta=2e-6)
        self.assertEqual(10, len(f["first_offenders"]))
        self.assertEqual("fail", self.status("pen_rests_02_count63"))
        self.assertEqual("pass", self.status("pen_rests_02_count64"))
        self.assertEqual("fail", self.status("pen_rests_02_fraction"))
        self.assertEqual({"instancer_with_size": "ValueError", "bad_count": "ValueError",
                          "bad_fraction": "ValueError"}, self.r["pen_errors"])

    def test_penetration_without_size_deflect(self):
        self.assertEqual("box", self.failure("pen_no_deflect")["other_object"])
        p = m(self.r["pen_no_deflect_center"], "particle_penetration")
        self.assertAlmostEqual(1.1e-3, p["min_center_clearance_m"], delta=2e-6)

    def test_penetration_tunnelled(self):
        f = self.failure("pen_passes")
        self.assertEqual((9, "box"), (f["frame"], f["other_object"]))
        self.assertLess(f["first_offenders"][0]["center_clearance_m"], 0)

    def test_instances_particle(self):
        p = m(self.r["pen_inst"], "particle_penetration")
        self.assertEqual("pass", self.status("pen_inst"))
        # instance half-height = size 0.01 x grain z scale 0.5
        self.assertAlmostEqual(p["min_center_clearance_m"] - 0.005, p["min_clearance_m"], delta=2e-6)
        self.assertAlmostEqual(0.0051, p["min_clearance_m"], delta=2e-6)

    def test_instances_geometry_nodes(self):
        p = m(self.r["gn"], "particle_penetration")
        self.assertEqual("pass", self.status("gn"))
        # Object Info (Original) does not apply the grain's own scale: half-height 0.01, not 0.005
        self.assertAlmostEqual(0.04, p["min_clearance_m"], delta=1e-6)
        self.assertEqual({"ground": {"closed": False, "planar_fast_path": True}}, p["solids"])
        f = self.failure("gn_low")
        self.assertEqual((1000, "ground"), (f["count"], f["other_object"]))
        self.assertAlmostEqual(-0.005, f["value"], delta=1e-6)
        self.assertEqual("pass", self.status("gn_low_tolerated"))

    def test_crossings(self):
        f = self.failure("cross_passes")
        self.assertEqual((9, 8, 64, "box"), (f["frame"], f["previous_frame"], f["count"], f["other_object"]))
        c = m(self.r["cross_passes"], "particle_crossings")
        self.assertEqual((64, 1, []), (c["crossings"], c["frame_step_max"], c["limitations"]))
        wide = m(self.r["cross_passes_wide"], "particle_crossings")
        self.assertEqual((64, 23), (wide["crossings"], wide["frame_step_max"]))
        self.assertEqual(["a particle that crossed and came back between checked frames is not seen"],
                         wide["limitations"])
        self.assertEqual("pass", self.status("cross_passes_tolerated"))
        self.assertEqual("pass", self.status("cross_rests"))
        self.assertEqual(0, m(self.r["cross_rests"], "particle_crossings")["crossings"])

    def test_crossings_open_sheet(self):
        f = self.failure("cross_sheet")
        self.assertEqual(("sheet", 64), (f["other_object"], f["count"]))
        self.assertEqual({"sheet": {"closed": False}}, m(self.r["cross_sheet"], "particle_crossings")["solids"])
        self.assertTrue(self.r["fast_path"]["planar"])
        self.assertLess(self.r["fast_path"]["max_diff"], 1e-6)

    def test_numeric_safety(self):
        self.assertEqual("pass", self.status("safety_rests"))
        f = self.failure("safety_passes")
        self.assertEqual(("outside the bounds", 64), (f["reason"], f["count"]))
        self.assertEqual("speed above limit", self.failure("safety_speed")["reason"])

    def test_region_exit(self):
        f = self.failure("region_escape")
        # v0 = 5 m/s up from z = 1: z = 2 at t = (5 - sqrt(25 - 2 g)) / g = 0.2732 s, 6.56 frames after frame 1
        self.assertEqual(24.0, self.r["fps"])
        self.assertEqual((8, "+z", 16), (f["frame"], f["face"], f["value"]))
        self.assertEqual(16, m(self.r["region_escape"], "particle_region")["exits_by_face"]["+z"])
        self.assertEqual("pass", self.status("region_escape_tolerated"))

    def test_pre_release(self):
        self.assertEqual("pass", self.status("prerelease_ok"))
        self.assertEqual(0.0, m(self.r["prerelease_ok"], "particle_pre_release")["max_displacement_m"])
        f = self.failure("prerelease_late")
        self.assertEqual((10, "moved before release"), (f["frame"], f["reason"]))

    def test_settling(self):
        s = m(self.r["settle_rests"], "particle_settling")
        self.assertEqual("pass", self.status("settle_rests"))
        self.assertLessEqual(s["time_to_rest_frame"], 12)
        self.assertEqual(1.0, s["frames"][-1]["settled_fraction"])
        self.assertEqual("pass", self.status("settle_rests_disp"))
        self.assertEqual("settled too late", self.failure("settle_rests_early")["reason"])
        self.assertEqual("not settled at the last frame", self.failure("settle_passes")["reason"])
        self.assertEqual(0.0, self.r["rests_24"]["speed_max"])

    def test_rest_displacement(self):
        self.assertEqual("pass", self.status("rest_disp_rests"))
        s = m(self.r["rest_disp_rests"], "particle_rest_displacement")
        self.assertEqual(64, s["tracked"])
        self.assertEqual(1.0, s["settled_fraction"])
        self.assertLessEqual(s["max_window_displacement_m"], 1e-3)
        self.assertIn("max_solver_speed_m_s", s)
        f = self.failure("rest_disp_passes")
        self.assertEqual("moved during the rest window", f["reason"])
        self.assertGreater(f["displacement_m"], 1e-3)

    @unittest.skipUnless(SLOW, "set WORLDAGENT_SLOW_TESTS=1 to bake the default DDR system")
    def test_ddr_default_explodes(self):
        f = self.failure("ddr_safety")
        self.assertEqual("outside the bounds", f["reason"])
        self.assertIn("crossings", m(self.r["ddr_crossings"], "particle_crossings"))


if __name__ == "__main__":
    unittest.main()
