"""Schema tests: every valid_* fixture must validate, every invalid_* fixture must not.

Run from worldagent-plugin/:
    uv run --with jsonschema python -m unittest tests/test_schemas.py
"""
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"

CASES = {
    "world_spec.schema.json": FIXTURES / "specs",
    "requirements.schema.json": FIXTURES / "requirements",
}


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class SchemaFixtureTest(unittest.TestCase):
    def test_schemas_are_valid(self):
        for name in CASES:
            with self.subTest(schema=name):
                Draft202012Validator.check_schema(load(ROOT / "schemas" / name))

    def test_fixtures(self):
        for name, fixture_dir in CASES.items():
            validator = Draft202012Validator(load(ROOT / "schemas" / name))
            fixtures = sorted(fixture_dir.glob("*.json"))
            self.assertTrue(fixtures, f"no fixtures in {fixture_dir}")
            for path in fixtures:
                with self.subTest(fixture=path.name):
                    errors = list(validator.iter_errors(load(path)))
                    if path.name.startswith("valid_"):
                        self.assertEqual([], [e.message for e in errors])
                    elif path.name.startswith("invalid_"):
                        self.assertTrue(errors, f"{path.name} should fail validation")
                    else:
                        self.fail(f"fixture name must start with valid_ or invalid_: {path.name}")

    def test_tolerance_bases_match_audit(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("spec_audit", ROOT / "bin" / "spec_audit.py")
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        tol = load(ROOT / "schemas" / "requirements.schema.json")["$defs"]["tolerance"]["properties"]
        self.assertEqual(list(audit.TOLERANCE_BASES), tol["basis"]["enum"])

    def test_outcome_length_names_default_rule(self):
        schema = load(ROOT / "schemas" / "requirements.schema.json")
        validator = Draft202012Validator(dict(schema["$defs"]["tolerance"], **{"$defs": schema["$defs"]}))
        length = {"quantity": "gap", "basis": "outcome", "value_m": 0.01, "derivation": "request clause"}
        self.assertTrue(list(validator.iter_errors(length)))
        self.assertEqual([], list(validator.iter_errors(dict(length, screen_m=0.003, solver_m=0.001))))
        ratio = {"quantity": "height", "basis": "outcome", "ratio": 0.5, "of": "start height",
                 "derivation": "request clause"}
        self.assertEqual([], list(validator.iter_errors(ratio)))

    def test_free_fall_object_and_requested_duration(self):
        spec = load(FIXTURES / "specs" / "valid_warehouse_shelf.json")
        for obj in spec["object_contracts"]:
            obj.pop("initial_support", None)
        spec["contract"]["story"].update(long_form=True, requested_duration_seconds=3)
        validator = Draft202012Validator(load(ROOT / "schemas" / "world_spec.schema.json"))
        self.assertEqual([], [e.message for e in validator.iter_errors(spec)])

    def test_solver_class_names_match_table(self):
        table = load(ROOT / "schemas" / "solver_classes.json")
        obj = load(ROOT / "schemas" / "world_spec.schema.json")["$defs"]["object_contract"]["properties"]
        self.assertEqual(sorted(table["classes"]), sorted(obj["simulation_class"]["enum"]))
        self.assertEqual(sorted(table["systems"]), sorted(obj["blender_system"]["enum"]))
        for name, entry in table["classes"].items():
            with self.subTest(simulation_class=name):
                self.assertTrue(entry["preferred"])
                self.assertLessEqual(set(entry["preferred"]), set(table["systems"]))
        self.assertEqual(set(table["case_classes"]) - {"coupled"}, set(table["classes"]) - {"fixed", "kinematic"})

    def test_coupling_channels_match_schema_enums(self):
        table = load(ROOT / "schemas" / "coupling_channels.json")
        Draft202012Validator(load(ROOT / "schemas" / "coupling_channels.schema.json")).validate(table)
        defs = load(ROOT / "schemas" / "world_spec.schema.json")["$defs"]
        systems = set(load(ROOT / "schemas" / "solver_classes.json")["systems"]) - {"none"}
        self.assertEqual(sorted(table["kinds"]),
                         sorted(defs["beat"]["properties"]["interactions"]["items"]["properties"]["kind"]["enum"]))
        self.assertEqual(sorted(table["inputs"]), sorted(defs["solver_node"]["properties"]["input"]["enum"]))
        self.assertEqual(sorted({c["channel"] for c in table["channels"]}),
                         sorted(defs["solver_edge"]["properties"]["channel"]["enum"]))
        for entry in table["channels"]:
            with self.subTest(channel=entry["channel"], to=entry["to"]):
                for end in ("from", "to"):
                    self.assertLessEqual(set(entry[end]), systems | set(table["inputs"]) | {"any"})
                self.assertLessEqual(set(entry["provides"]), set(table["kinds"]))
                self.assertTrue(entry["evidence"])

    def test_coupling_channel_test_evidence_exists(self):
        """Every `test` evidence names a test file and, when given, a test method in it."""
        for entry in load(ROOT / "schemas" / "coupling_channels.json")["channels"]:
            for ev in (e for e in entry["evidence"] if e["kind"] == "test"):
                with self.subTest(ref=ev["ref"]):
                    path, _, rest = ev["ref"].partition(" ")
                    text = (ROOT / path).read_text(encoding="utf-8")
                    if rest.startswith("test_"):
                        self.assertIn(f"def {rest}(", text)


if __name__ == "__main__":
    unittest.main()
