"""worldagent_measure.systems tests against a real Blender (skipped when none is configured).

One Blender process builds every scene (tests/blender/systems_cases.py):
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_measure_systems.py
The FLIP domain mapping needs the add-on and is not covered here.
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import ROOT, requires_blender, run_cases  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "blender" / "systems_cases.py"


def short(listing):
    return [(e["object"], e["enabled"], e["active"], e["in_group"], e["acts"]) for e in listing]


@requires_blender
class SystemsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        r = run_cases(SCRIPT)
        cls.s, cls.e, cls.cache = r["systems"], r["effectors"], r["cache_missing"]

    def test_physics_systems(self):
        expected = {"plain": ["none"], "soft_body": ["soft_body"], "cloth_and_particles": ["cloth", "newton_particles"],
                    "rigid_active": ["bullet_rigid_body"], "rigid_passive_animated": ["bullet_rigid_body"],
                    "gas_domain": ["mantaflow_gas"], "liquid_domain": ["mantaflow_liquid"], "flow": ["none"],
                    "sph": ["sph_particles"], "hair": ["none"], "keyed": ["none"], "collider": ["none"],
                    "wind": ["none"]}
        self.assertEqual(expected, {k: self.s[k]["systems"] for k in expected})

    def test_names_come_from_the_schema(self):
        schema = json.loads((ROOT / "schemas" / "solver_classes.json").read_text(encoding="utf-8"))["systems"]
        self.assertEqual(schema, self.s["schema_systems"])

    def test_systems_that_do_not_run(self):
        for key, system in (("soft_body_off", "soft_body"), ("rigid_outside_world", "bullet_rigid_body")):
            r = self.s[key]
            self.assertEqual((["none"], [system], [system]), (r["systems"], r["all"], r["disabled"]), key)

    def test_roles(self):
        expected = {"flow": ["mantaflow_flow"], "collider": ["collision"], "wind": ["force_field"], "hair": ["hair"],
                    "keyed": ["keyed_particles"], "rigid_passive_animated": ["rigid_body_passive", "rigid_body_animated"],
                    "liquid_domain": [], "plain": []}
        self.assertEqual(expected, {k: self.s[k]["roles"] for k in expected})

    def test_solver_identity_pass(self):
        r = self.s["identity_pass"]
        self.assertEqual("pass", r["status"], r)
        m = r["measurements"]["solver_identity"]
        self.assertEqual([], m["mismatched"])
        self.assertEqual(("cloth", ["cloth", "newton_particles"]), (m["objects"]["flag"]["declared"],
                                                                    m["objects"]["flag"]["systems"]))

    def test_solver_identity_failures(self):
        cases = {"identity_mismatch": ("jelly", "declared system not in scene", "cloth", ["soft_body"]),
                 "identity_none_but_solver": ("flag", "declared none but a system runs", "none",
                                              ["cloth", "newton_particles"]),
                 "identity_disabled": ("jelly_off", "declared system disabled", "soft_body", ["none"]),
                 "identity_outside_world": ("loose", "declared system disabled", "bullet_rigid_body", ["none"]),
                 "identity_missing": ("ghost", "object missing", "cloth", None)}
        for key, (obj, reason, declared, found) in cases.items():
            r = self.s[key]
            f = r["first_failure"]
            self.assertEqual(("fail", "solver_identity", obj, reason, declared, found),
                             (r["status"], f["check"], f["object"], f["reason"], f["declared"], f["systems"]), key)
        self.assertEqual(["jelly"], self.s["identity_mismatch"]["measurements"]["solver_identity"]["mismatched"])

    def test_solver_identity_argument_errors(self):
        self.assertEqual({"empty", "bad_system", "twice"}, set(self.s["identity_errors"]))

    def test_mantaflow_effectors_whole_scene(self):
        r = self.e["all_scene"]
        self.assertEqual(("mantaflow_gas", None, None), (r["system"], r["group"], r["flow_group"]))
        self.assertEqual([("lid", True, True, True, True), ("wall", True, True, True, True),
                          ("outside", False, True, True, False), ("keyed_effector", True, True, True, True)],
                         short(r["effectors"]))
        self.assertEqual(["COLLISION", "GUIDE"], [e["settings"]["effector_type"] for e in r["effectors"][:2]])
        self.assertEqual([("source", True, True, True, True), ("stray_flow", True, True, True, True)],
                         short(r["flows"]))

    def test_mantaflow_groups_and_keyed_use_effector(self):
        one, six = self.e["grouped_frame1"], self.e["grouped_frame6"]
        self.assertEqual(("Effectors", "Flows"), (one["group"], one["flow_group"]))
        self.assertEqual('modifiers["Fluid"].domain_settings.effector_group', one["group_property"])
        self.assertEqual([("lid", True, True, True, True), ("wall", True, True, True, True),
                          ("outside", False, True, False, False), ("keyed_effector", True, True, True, True)],
                         short(one["effectors"]))
        self.assertEqual(("keyed_effector", False, False), (six["effectors"][3]["object"],
                                                            six["effectors"][3]["active"], six["effectors"][3]["acts"]))
        self.assertEqual([("source", True, True, True, True), ("stray_flow", True, True, False, False)],
                         short(one["flows"]))

    def test_collision_effectors(self):
        self.assertEqual([("near", True, True, True, True), ("far", True, False, True, False)],
                         short(self.e["cloth_all"]["effectors"]))
        grouped = self.e["cloth_group"]
        self.assertEqual(("Colliders", 'modifiers["Cloth"].collision_settings.collection'),
                         (grouped["group"], grouped["group_property"]))
        self.assertEqual([("near", True, True, True, True), ("far", True, False, False, False)],
                         short(grouped["effectors"]))
        self.assertAlmostEqual(0.03, grouped["effectors"][0]["settings"]["thickness_outer"], places=6)
        self.assertEqual([], grouped["flows"])
        p = self.e["particles"]
        self.assertEqual(("newton_particles", "P_NEWTON", 'particle_systems["P_NEWTON"].settings.collision_collection'),
                         (p["system"], p["particle_system"], p["group_property"]))
        sb = self.e["soft_body"]
        self.assertEqual(("soft_body", 'modifiers["Softbody"].settings.collision_collection'),
                         (sb["system"], sb["group_property"]))
        self.assertEqual(["near", "far"], [x["object"] for x in sb["effectors"]])

    def test_effectors_argument_errors(self):
        self.assertEqual({"ambiguous", "not_covered", "absent"}, set(self.e["errors"]))

    def test_cache_missing_subclasses(self):
        self.assertEqual({"gas": True, "liquid": True, "runtime_error": True}, self.cache)


if __name__ == "__main__":
    unittest.main()
