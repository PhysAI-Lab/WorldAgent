"""worldagent_measure.gas gas_setup, cache_coverage and boundary_clearance tests against a real Blender
(skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest tests/test_measure_gas_setup.py
"""
import math
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "gas_setup_cases.py"

# Mirrors the case script: 2 m cube domain, resolution_max 16 (voxel 0.125 m); Flow sphere r 0.2 at z 0.4.
VOXEL_M = 0.125


@requires_blender
class GasSetupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT, timeout=300)

    def m(self, key, check="gas_setup"):
        r = self.r[key]
        return (r["result"] if "result" in r else r)["measurements"][check]

    def status(self, key):
        r = self.r[key]
        return (r["result"] if "result" in r else r)["status"]

    def failure(self, key):
        r = self.r[key]
        return (r["result"] if "result" in r else r)["first_failure"]

    def assertPass(self, key):
        self.assertEqual("pass", self.status(key), self.r[key].get("first_failure"))

    def assertFails(self, key, obj=None, problem=None):
        self.assertEqual("fail", self.status(key), key)
        f = self.failure(key)
        if obj is not None:
            self.assertEqual(obj, f.get("object"), f)
        if problem is not None:
            self.assertIn(problem, f["problem"])
        return f

    # --- gas_setup ---------------------------------------------------------------------------------

    def test_good_scene(self):
        self.assertPass("good")
        m = self.m("good")
        d = m["domain"]
        for v in d["voxel_m"]:
            self.assertAlmostEqual(VOXEL_M, v, places=6)
        self.assertEqual([16, 16, 16], d["domain_resolution"])
        self.assertEqual([16, 16, 16], d["domain_resolution_formula"])
        self.assertEqual({"flag": "front", "closed": False}, d["borders"]["y_min"])
        flow = m["flows"][0]
        self.assertAlmostEqual(3.2, flow["thinnest_voxels"], places=3)
        self.assertAlmostEqual(VOXEL_M, flow["surface_emission_m"], places=6)
        self.assertEqual([[1, 12]], flow["active_intervals"])
        self.assertAlmostEqual(1.6, flow["clearance_voxels"]["z_min"], places=4)
        self.assertAlmostEqual(6.4, flow["clearance_voxels"]["x_min"], places=4)
        self.assertEqual(1.0, flow["inside_fraction"])
        self.assertAlmostEqual(1 / 24, m["time"]["simulated_seconds_per_frame"], places=9)
        self.assertAlmostEqual(0.5, m["time"]["story_seconds"], places=9)
        self.assertTrue(m["obstacles"]["lid"]["effector"])
        c = m["cache"]
        self.assertEqual((False, False, True, True, False),
                         (c["relative"], c["rebased"], c["inside_cache_root"], c["writable"], c["has_cache_baked_data"]))
        self.assertEqual([], m["undeclared_effectors"])
        self.assertIsNone(m["thresholds"]["max_flow_step_voxels"])  # written out as None, copied

    def test_scaled_domain(self):
        self.assertPass("scaled_domain")
        d = self.m("scaled_domain")["domain"]
        for v in d["voxel_m"]:
            self.assertAlmostEqual(0.25, v, places=6)
        self.assertEqual([16, 8, 8], d["domain_resolution"])
        self.assertTrue(d["unapplied_scale"])
        self.assertAlmostEqual(1.6, self.m("scaled_domain")["flows"][0]["thinnest_voxels"], places=3)

    def test_time(self):
        self.assertPass("fps48")
        t = self.m("fps48")["time"]
        self.assertAlmostEqual(1 / 48, t["simulated_seconds_per_frame"], places=9)
        self.assertAlmostEqual(0.25, t["story_seconds"], places=9)
        f = self.assertFails("time_scale_half", "Domain")
        self.assertEqual("time_scale 0.5 differs from the spec's 1.0", f["problem"])
        self.assertAlmostEqual(1 / 48, self.m("time_scale_half")["time"]["simulated_seconds_per_frame"], places=9)
        self.assertPass("time_scale_half_spec")

    def test_flow_position(self):
        self.assertFails("flow_outside", "Flow", "Flow outside the Domain")
        self.assertEqual(0.0, self.m("flow_outside")["flows"][0]["inside_fraction"])
        self.assertPass("flow_partly_outside")
        flow = self.m("flow_partly_outside")["flows"][0]
        self.assertTrue(0 < flow["inside_fraction"] < 1, flow["inside_fraction"])
        self.assertLess(flow["clearance_voxels"]["z_min"], 0)
        self.assertEqual("z_min", self.assertFails("flow_partly_outside_clear", "Flow")["face"])

    def test_flow_structural(self):
        for key, problem in (("flow_not_in_group", "Flow collection"), ("liquid_flow", "LIQUID"),
                             ("no_emission", "surface_distance and volume_density are 0"),
                             ("zero_density", "density 0"), ("flow_modifier_off", "off in the viewport"),
                             ("inflow_never", "never emits")):
            with self.subTest(key):
                self.assertFails(key, "Flow", problem)

    def test_flow_active_frames(self):
        f = self.assertFails("pre_event", "Flow", "before its allowed frame")
        self.assertEqual((1, 6), (f["first_active"], f["allowed_from"]))
        self.assertEqual([[1, 8]], self.m("pre_event")["flows"][0]["active_intervals"])
        self.assertPass("keyed_ok")
        self.assertEqual([[6, 12]], self.m("keyed_ok")["flows"][0]["active_intervals"])
        self.assertFails("geometry_late", "Flow", "before its allowed frame")
        self.assertEqual([[1, 1]], self.m("geometry_late")["flows"][0]["active_intervals"])
        self.assertFails("geometry_keyed_on6", "Flow", "never emits")
        self.assertEqual([], self.m("geometry_keyed_on6")["flows"][0]["active_intervals"])

    def test_flow_naming(self):
        self.assertFails("not_a_flow", "lid", "no Fluid FLOW modifier")
        self.assertFails("no_flow_named", "Domain", "no emitting Flow")
        self.assertPass("all_flows")
        self.assertEqual(["Flow"], [f["name"] for f in self.m("all_flows")["flows"]])

    def test_obstacles(self):
        for key, problem in (("obstacle_missing_effector", "no Fluid Effector modifier"),
                             ("effector_modifier_off", "off in the viewport"),
                             ("effector_disabled", "use_effector off"), ("effector_guide", "GUIDE"),
                             ("effector_outside_group", "Effector collection"),
                             ("obstacle_outside_domain", "outside the Domain"),
                             ("effector_off_midway", "use_effector off"),
                             ("obstacle_hidden", "declared obstacle is not rendered")):
            with self.subTest(key):
                self.assertFails(key, "lid", problem)
        self.assertEqual([[7, 12]], self.m("effector_off_midway")["effectors"][0]["use_effector_frames_off"])

    def test_hidden_effectors_and_proxies(self):
        self.assertFails("hidden_undeclared_effector", "proxy", "air wall")
        self.assertPass("proxy_ok")
        proxy = next(e for e in self.m("proxy_ok")["effectors"] if e["name"] == "proxy")
        self.assertEqual(("lid", False, "hide_render"), (proxy["proxy_for"], proxy["rendered"],
                                                         proxy["not_rendered_reason"]))
        self.assertFails("proxy_for_undeclared", "proxy", "not a declared obstacle")
        self.assertPass("visible_undeclared_effector")
        self.assertEqual(["wall"], self.m("visible_undeclared_effector")["undeclared_effectors"])
        self.assertPass("effector_thickened")
        lid = self.m("effector_thickened")["effectors"][0]
        self.assertTrue(lid["thickened"])
        self.assertAlmostEqual(0.25, lid["surface_thickness_m"], places=6)

    def test_cache(self):
        self.assertPass("cache_outside_root")  # a layout convention: reported, not failed
        self.assertFalse(self.m("cache_outside_root")["cache"]["inside_cache_root"])
        self.assertIsNone(self.m("cache_root_none")["cache"]["inside_cache_root"])
        self.assertFails("cache_range_short", "Domain", "cache frames 1-6 do not cover 1-12")
        self.assertFails("cache_replay", "Domain", "REPLAY")
        self.assertFails("cache_not_writable", "Domain", "not writable")

    def test_motion(self):
        f = self.assertFails("moving_flow", "Flow", "max_flow_step_voxels")
        self.assertAlmostEqual(4.0, self.m("moving_flow")["flows"][0]["max_step_voxels"], places=3)
        self.assertEqual(2, f["frame"])
        self.assertPass("moving_flow_subframes")
        self.assertAlmostEqual(1.0, self.m("moving_flow_subframes")["flows"][0]["step_per_substep_voxels"], places=3)

    def test_thresholds(self):
        self.assertFails("source_4", "Flow", "min_source_voxels")
        self.assertPass("source_3")
        f = self.assertFails("feature_3", problem="min_feature_voxels")
        self.assertEqual("root_width", f["feature"])
        self.assertAlmostEqual(2.4, self.m("feature_3")["features_voxels"]["root_width"], places=6)
        self.assertPass("feature_2")
        self.assertEqual("ValueError", self.r["feature_no_min"])
        self.assertEqual("TypeError", self.r["no_min_source"])  # thresholds have no default
        self.assertEqual("ValueError", self.r["not_gas"])

    def test_adaptive_reported(self):
        self.assertPass("adaptive_reported")
        d = self.m("adaptive_reported")["domain"]
        self.assertTrue(d["use_adaptive_domain"])
        self.assertEqual(("formula", [16, 16, 16]), (d["domain_resolution_used"], d["domain_resolution_formula"]))

    def test_replay_not_simulated(self):
        r = self.r["replay_untouched"]
        self.assertFails("replay_untouched", "Domain", "REPLAY")
        self.assertEqual(([], []), (r["files_before"], r["files_after"]))
        self.assertEqual(([True, True], 5), (r["show"], r["frame"]))

    def test_baked_cache_untouched(self):
        self.assertEqual(["FINISHED"], self.r["baked_bake"])
        self.assertPass("baked_untouched")
        self.assertTrue(self.r["baked_untouched"]["same_hash"])
        c = self.m("baked_untouched")["cache"]
        self.assertEqual((True, 12), (c["has_cache_baked_data"], c["data_files_in_range"]))

    def test_rebased_relative_cache(self):
        self.assertPass("rebased")
        c = self.m("rebased")["cache"]
        self.assertEqual(("//../../cache/c01", True, True, True),
                         (c["cache_directory_saved"], c["relative"], c["rebased"], c["inside_cache_root"]))
        self.assertEqual(self.r["rebased_cache_dir"], c["cache_directory_resolved"])

    # --- cache_coverage ----------------------------------------------------------------------------

    def test_coverage(self):
        self.assertEqual(["FINISHED"], self.r["coverage_bake"])
        self.assertPass("coverage_baked")
        m = self.m("coverage_baked", "gas_cache_coverage")
        self.assertEqual([], m["cache"]["missing_data_frames"])
        self.assertEqual([(1, 4096), (6, 4096), (12, 4096)], [(g["frame"], g["length"]) for g in m["grids"]])
        self.assertTrue(all(g["total"] > 0 for g in m["grids"]))

    def test_coverage_failures(self):
        f = self.assertFails("coverage_unbaked", problem="Data not baked")
        self.assertEqual([], self.m("coverage_unbaked", "gas_cache_coverage")["grids"])  # nothing stepped
        self.assertIsNotNone(f)
        self.assertFails("coverage_changed", problem="Data not baked")
        self.assertFails("coverage_short", problem="cache frames 1-12 do not cover 1-14")
        self.assertEqual([7], self.assertFails("coverage_file_missing", problem="data files missing")["frames"])
        self.assertFails("coverage_replay", problem="REPLAY")

    def test_coverage_noise(self):
        self.assertPass("coverage_noise")
        grids = self.m("coverage_noise", "gas_cache_coverage")["grids"]
        self.assertTrue(all(g["length"] == 32768 for g in grids), grids)

    # --- boundary_clearance ------------------------------------------------------------------------

    def test_open_top(self):
        self.assertEqual(["FINISHED"], self.r["jet_bake"])
        self.assertPass("clearance_open_top")
        frames = {f["frame"]: f for f in self.m("clearance_open_top", "gas_boundary_clearance")["frames"]}
        self.assertGreater(frames[6]["faces"]["z_max"]["clearance_voxels"], 0)
        self.assertEqual(0, frames[24]["faces"]["z_max"]["clearance_voxels"])
        self.assertIsNone(frames[6]["front_speed_m_s"])
        self.assertTrue(0 < frames[12]["front_speed_m_s"]["z_max"] < 6, frames[12]["front_speed_m_s"])
        self.assertAlmostEqual(VOXEL_M * frames[6]["faces"]["z_max"]["clearance_voxels"],
                               frames[6]["faces"]["z_max"]["clearance_m"], places=6)
        self.assertEqual("z_max", self.assertFails("clearance_min", problem="min_clearance_voxels")["face"])
        self.assertEqual("z_max", self.assertFails("clearance_guard", problem="guard layers")["face"])
        self.assertEqual("ValueError", self.r["clearance_bad_frames"])

    def test_world_mapping(self):
        self.assertPass("clearance_mapping")
        frame = self.m("clearance_mapping", "gas_boundary_clearance")["frames"][0]
        centre = self.r["mapping_flow_centre"]
        self.assertLess(math.dist(frame["centroid_world_m"], centre), 0.5 * 0.25)  # half a 0.25 m voxel
        lo, hi = frame["aabb_world_m"]
        self.assertTrue(all(lo[i] <= centre[i] <= hi[i] for i in range(3)), (lo, hi, centre))

    def test_noise_mapping(self):
        noise = self.m("clearance_noise", "gas_boundary_clearance")
        base = self.m("clearance_base", "gas_boundary_clearance")
        self.assertEqual(("noise", [32, 32, 32]), (noise["cells"], noise["frames"][0]["grid"]))
        self.assertEqual(("base", [16, 16, 16]), (base["cells"], base["frames"][0]["grid"]))
        self.assertLess(math.dist(noise["frames"][0]["centroid_world_m"], base["frames"][0]["centroid_world_m"]),
                        VOXEL_M)

    def test_front_border(self):
        faces = self.m("clearance_front_closed", "gas_boundary_clearance")["frames"][0]["faces"]
        self.assertEqual(("front", True), (faces["y_min"]["border"], faces["y_min"]["closed"]))
        self.assertEqual([], [f for f in faces if f != "y_min" and faces[f]["closed"]])

    def test_empty_frames(self):
        self.assertPass("clearance_empty")
        rows = self.m("clearance_empty", "gas_boundary_clearance")["frames"]
        self.assertEqual([True, True, False], [r["empty"] for r in rows])
        self.assertIsNone(rows[2]["front_speed_m_s"])  # no earlier non-empty frame

    def test_adaptive_unverified(self):
        self.assertEqual(["FINISHED"], self.r["adaptive_bake"])
        f = self.assertFails("adaptive_clearance")
        self.assertEqual("UNVERIFIED", f["verdict"])
        self.assertTrue(self.m("adaptive_clearance", "gas_boundary_clearance")["adaptive"])
        self.assertPass("adaptive_density_none")
        self.assertTrue(self.m("adaptive_density_none", "gas_density")["adaptive"])
        self.assertEqual("UNVERIFIED", self.assertFails("adaptive_density_wall")["verdict"])


if __name__ == "__main__":
    unittest.main()
