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


if __name__ == "__main__":
    unittest.main()
