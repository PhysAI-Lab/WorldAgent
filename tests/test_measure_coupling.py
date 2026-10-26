"""worldagent_measure.coupling: graph reading and the FLIP export reader without Blender; links and
bake evidence against a real Blender (skipped when none is configured).

Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest tests/test_measure_coupling.py
"""
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent / "lib"))
from blender_harness import requires_blender, run_cases  # noqa: E402
from worldagent_measure import coupling  # noqa: E402  (node_map and read_flip_export need no bpy)

SCRIPT = HERE / "blender" / "coupling_cases.py"
LOBBY = json.loads((HERE / "fixtures" / "specs" / "valid_lobby_coupled.json").read_text(encoding="utf-8"))


def bobj(vertices, triangles):
    flat = [c for v in vertices for c in v]
    tri = [i for t in triangles for i in t]
    return struct.pack(f"<i{len(flat)}fi{len(tri)}i", len(vertices), *flat, len(triangles), *tri)


def flip_db(path):
    """A database with FLIP's export tables (objects/flip_fluid_geometry_database.py, FLIP 1.8.8)."""
    con = sqlite3.connect(path)
    m = ", ".join(f"m{r}{c} REAL" for r in range(4) for c in range(4))
    con.executescript(f"""
        CREATE TABLE object (object_id INTEGER PRIMARY KEY, object_name TEXT, object_slug TEXT,
                             object_motion_type TEXT, export_mesh INTEGER);
        CREATE TABLE mesh_static (mesh_static_id INTEGER PRIMARY KEY, object_id INTEGER, mesh_static_data BLOB);
        CREATE TABLE mesh_keyframed (mesh_keyframed_id INTEGER PRIMARY KEY, object_id INTEGER, frame_id INTEGER, {m});
        CREATE TABLE mesh_animated (mesh_animated_id INTEGER PRIMARY KEY, object_id INTEGER, frame_id INTEGER,
                                    mesh_animated_data BLOB, reference INTEGER);
    """)
    tri = [(0, 1, 2)]
    con.execute("INSERT INTO object VALUES (1, 'crate', 'crate1', 'ANIMATED', 1)")
    for frame in (1, 2):
        con.execute("INSERT INTO mesh_animated (object_id, frame_id, mesh_animated_data) VALUES (1, ?, ?)",
                    (frame, bobj([(0, 0, frame), (1, 0, frame), (0, 1, frame)], tri)))
    con.execute("INSERT INTO object VALUES (2, 'shelf', 'shelf2', 'KEYFRAMED', 1)")
    con.execute("INSERT INTO mesh_static (object_id, mesh_static_data) VALUES (2, ?)",
                (bobj([(0, 0, 0), (1, 0, 0), (0, 1, 0)], tri),))
    translate_x = [1, 0, 0, 5, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    con.execute(f"INSERT INTO mesh_keyframed (object_id, frame_id, {m.replace(' REAL', '')}) VALUES (2, 3, "
                + ", ".join("?" * 16) + ")", translate_x)
    con.execute("INSERT INTO object VALUES (3, 'floor', 'floor3', 'STATIC', 1)")
    con.execute("INSERT INTO mesh_static (object_id, mesh_static_data) VALUES (3, ?)",
                (bobj([(0, 0, -1), (1, 0, -1), (0, 1, -1)], tri),))
    con.commit()
    con.close()


class PureTest(unittest.TestCase):
    def test_node_map(self):
        gm = coupling.node_map(LOBBY)
        self.assertEqual("bullet_rigid_body", gm["nodes"]["rigid"]["system"])
        self.assertEqual(("keyed", "keyed"), (gm["nodes"]["slide_motor"]["input"], gm["nodes"]["slide_motor"]["system"]))
        self.assertEqual("flip_domain", gm["nodes"]["water"]["domain"])
        self.assertTrue(all(e["driver_first"] for e in gm["edges"]))
        self.assertEqual(["slide_motor", "rigid", "water"], gm["bake_order"])
        with self.assertRaises(ValueError):
            coupling.node_map({"topology": {}})

    def test_read_flip_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "export_data.sqlite3"
            flip_db(db)
            animated = coupling.read_flip_export(db, "crate", [1, 2, 3])
            self.assertEqual("ANIMATED", animated["motion_type"])
            self.assertEqual([1, 2], sorted(animated["frames"]))
            self.assertEqual((1.0, 0.0, 2.0), animated["frames"][2][1])
            keyed = coupling.read_flip_export(db, "shelf", [3])
            self.assertEqual([(5.0, 0.0, 0.0), (6.0, 0.0, 0.0), (5.0, 1.0, 0.0)], keyed["frames"][3])
            static = coupling.read_flip_export(db, "floor", [7, 8])
            self.assertEqual([7, 8], sorted(static["frames"]))
            with self.assertRaises(KeyError):
                coupling.read_flip_export(db, "ghost", [1])


@requires_blender
class LinkTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_cases(SCRIPT, "links")

    def test_effector_link(self):
        self.assertEqual("pass", self.r["effector_declared"]["status"])
        self.assertEqual([["box", "water", "effector"]], self.r["effector_declared"]["links"])
        self.assertEqual(["effector from box to water (water) is not a solver_graph edge"],
                         self.r["effector_undeclared"]["undeclared"])
        self.assertEqual(["box is not in water's group others"], self.r["effector_outside_group"]["wrong_carrier"])
        self.assertEqual(["no effector from rigid to water in the scene"], self.r["effector_missing"]["missing"])

    def test_static_collider_is_no_link(self):
        self.assertEqual([], self.r["static_effector_links"])

    def test_flow_source_link(self):
        self.assertEqual("pass", self.r["flow_source_declared"]["status"])
        self.assertEqual([["burning_crate", "smoke", "flow_source"]], self.r["flow_source_declared"]["links"])

    def test_collision_link(self):
        self.assertEqual("pass", self.r["collision_declared"]["status"])
        self.assertEqual("undeclared", self.r["collision_undeclared"]["first_failure"]["kind"])

    def test_hook_link(self):
        self.assertEqual(["Hook modifier Hook is below the solver modifier on patch"],
                         self.r["hook_below"]["wrong_carrier"])
        self.assertEqual("pass", self.r["hook_above"]["status"])

    def test_force_field_link(self):
        self.assertEqual("pass", self.r["force_field_declared"]["status"])
        self.assertEqual([["wind", "box", "force_field"], ["wind", "patch", "force_field"]],
                         self.r["force_field_declared"]["links"])
        # A zero weight in the rigid body world's effector weights removes the link.
        self.assertEqual(["no force_field from wind_input to rigid in the scene"],
                         self.r["force_field_weight_zero"]["missing"])

    def test_force_field_moves_rigid_body(self):
        self.assertGreater(self.r["force_field_moved_x_m"], 0.01)

    def test_keyed_motor(self):
        link = self.r["motor_link"]
        self.assertEqual(("motor_x", "raft", "kinematic_boundary"), (link["driver"], link["driven"], link["channel"]))
        self.assertEqual({"rigid_body_constraint.motor_lin_max_impulse": [0.0, 5.0]}, link["detail"]["keyed_values"])
        self.assertIn("motor_x is in no node", self.r["motor_undeclared"]["undeclared"][0])
        self.assertEqual("pass", self.r["motor_scripted"]["status"], self.r["motor_scripted"])


@requires_blender
class BakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        work = Path(cls._tmp.name)
        cls.b = run_cases(SCRIPT, "bake", str(work))
        cls.o = run_cases(SCRIPT, "reopen", str(work), blend=work / "case" / "scene.blend")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_field_flow_translates_without_torque(self):
        # A bar with one end in a 6 m/s gas jet and the other outside moves but does not turn:
        # the Fluid Flow field gives Bullet a central force only, so the channel provides no rotate.
        self.assertGreater(self.b["bar_moved_m"], 0.005)
        self.assertEqual(0.0, self.b["bar_rotation_change_rad"])

    def test_field_flow_deforms_cloth(self):
        self.assertGreater(self.b["patch_mean_x_moved_m"], 0.0005)

    def test_field_flow_links(self):
        self.assertEqual("pass", self.b["field_flow_declared"]["status"], self.b["field_flow_declared"])
        self.assertEqual(2, len(self.b["field_flow_undeclared"]["undeclared"]))
        self.assertEqual(["flow_field.field.flow is 1.0, the spec says 2.0"],
                         self.b["field_flow_strength_differs"]["wrong_carrier"])
        self.assertIn("flow_field reads other_domain, not the gas domain",
                      self.b["field_flow_wrong_source"]["wrong_carrier"])

    def test_records_written_per_node(self):
        records = self.b["records"]
        self.assertEqual({"gas", "rigid", "cloth"}, set(records))
        self.assertEqual(("disk", "cache_gas"), (records["gas"]["digest_source"], records["gas"]["cache"]))
        self.assertEqual(("state", "in .blend"), (records["rigid"]["digest_source"], records["rigid"]["cache"]))
        for node in ("rigid", "cloth"):
            driver = records[node]["drivers"]["gas"]
            self.assertEqual(records["gas"]["digest"], driver["digest_at_bake"])
            self.assertFalse(driver["driver_unrecorded"])

    def test_bake_order_from_records(self):
        for result in (self.b["evidence_same_process"], self.o["from_records"]):
            self.assertEqual("pass", result["status"], result)
            self.assertEqual({"bake_record"}, {e["evidence"] for e in result["edges"].values()})

    def test_digests_stable_after_reopen(self):
        self.assertEqual(self.b["gas_digest"], self.o["gas_digest"])

    def test_without_records(self):
        fail, report = self.o["no_records_fail"], self.o["no_records_report"]
        self.assertEqual(["gas_pushes_bar"], fail["unverified_edges"])  # Bullet keeps its cache in the .blend
        self.assertEqual("file_times", fail["edges"]["gas_moves_patch"]["evidence"])
        self.assertEqual(("fail", "pass"), (fail["status"], report["status"]))

    def test_file_times_contradict(self):
        r = self.o["gas_newer_than_cloth"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("gas cache files are newer than cloth's: rebake cloth", r["first_failure"]["problem"])

    def test_driver_changed_after_driven_bake(self):
        self.assertNotEqual(self.o["gas_digest"]["digest"], self.o["gas_digest_after_change"]["digest"])
        r = self.o["driver_changed"]
        self.assertEqual("fail", r["status"])
        self.assertEqual("gas changed after rigid was baked: rebake rigid", r["first_failure"]["problem"])


if __name__ == "__main__":
    unittest.main()
