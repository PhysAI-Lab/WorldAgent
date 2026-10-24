"""spec_audit tests: the warehouse fixture passes; each single defect is reported by its check.

Run from the repo root (standard library only):
    python3 -m unittest worldagent-plugin/tests/test_spec_audit.py
"""
import copy
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("spec_audit", ROOT / "bin" / "spec_audit.py")
spec_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_audit)

VALID = json.loads((ROOT / "tests" / "fixtures" / "specs" / "valid_warehouse_shelf.json").read_text(encoding="utf-8"))


def beat(m, beat_id):
    return next(b for b in m["contract"]["story"]["beats"] if b["id"] == beat_id)


def obj(m, name):
    return next(o for o in m["object_contracts"] if o["name"] == name)


class SpecAuditTest(unittest.TestCase):
    def checks(self, mutate):
        m = copy.deepcopy(VALID)
        mutate(m)
        return {e["check"] for e in spec_audit.audit(m)}

    def test_valid_fixture_passes(self):
        self.assertEqual([], spec_audit.audit(copy.deepcopy(VALID)))

    def test_duplicate_beat_id(self):
        self.assertIn("unique_beat_id", self.checks(lambda m: beat(m, "fall").__setitem__("id", "tip")))

    def test_frame_order(self):
        self.assertIn("frame_order", self.checks(lambda m: beat(m, "fall").__setitem__("frame_interval", [168, 97])))

    def test_outside_frame_range(self):
        self.assertIn("frame_range", self.checks(lambda m: beat(m, "settle").__setitem__("frame_interval", [169, 300])))

    def test_readable_duration(self):
        # tip: 49-96 is 2.0 s at 24 fps
        self.assertIn("readable_duration", self.checks(lambda m: beat(m, "tip").__setitem__("minimum_readable_seconds", 5)))

    def test_unknown_dependency(self):
        self.assertIn("dependency", self.checks(lambda m: beat(m, "fall").__setitem__("depends_on", ["flood"])))

    def test_dependency_cycle(self):
        self.assertIn("dependency", self.checks(lambda m: beat(m, "establish").__setitem__("depends_on", ["settle"])))

    def test_dependency_order(self):
        self.assertIn("dependency_order", self.checks(lambda m: beat(m, "tip").__setitem__("depends_on", ["fall"])))

    def test_beat_count(self):
        self.assertIn("beat_count", self.checks(lambda m: m["contract"]["story"].__setitem__("required_beat_count", 5)))

    def test_duplicate_object_name(self):
        self.assertIn("unique_object_name", self.checks(lambda m: obj(m, "crate_a").__setitem__("name", "floor")))

    def test_unresolved_support(self):
        self.assertIn("support", self.checks(lambda m: obj(m, "crate_a").__setitem__("initial_support", "ceiling")))

    def test_unknown_topology_region(self):
        self.assertIn("topology", self.checks(lambda m: m["topology"]["edges"][0].__setitem__(2, "roof")))

    def test_missing_sections_are_malformed(self):
        self.assertIn("malformed", self.checks(lambda m: m.pop("object_contracts")))


if __name__ == "__main__":
    unittest.main()
