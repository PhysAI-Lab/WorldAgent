"""spec_audit tests: the warehouse fixture passes; each single defect is reported by its check.

Run from the repo root (standard library only):
    python3 -m unittest worldagent-plugin/tests/test_spec_audit.py
"""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("spec_audit", ROOT / "bin" / "spec_audit.py")
spec_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_audit)

VALID = json.loads((ROOT / "tests" / "fixtures" / "specs" / "valid_warehouse_shelf.json").read_text(encoding="utf-8"))
ROWS = json.loads((ROOT / "tests" / "fixtures" / "requirements" / "valid_matrix.json").read_text(encoding="utf-8"))
AUDIT = ROOT / "bin" / "spec_audit.py"


def beat(m, beat_id):
    return next(b for b in m["contract"]["story"]["beats"] if b["id"] == beat_id)


def obj(m, name):
    return next(o for o in m["object_contracts"] if o["name"] == name)


class AuditCase(unittest.TestCase):
    def checks(self, mutate):
        m = copy.deepcopy(VALID)
        mutate(m)
        return {e["check"] for e in spec_audit.audit(m)}


class SpecAuditTest(AuditCase):
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


class SolverChoiceTest(AuditCase):
    def jelly(self, m, **fields):
        """crate_a as the jelly runs had it: an elastic body simulated by Cloth."""
        obj(m, "crate_a").update(simulation_class="soft_body", blender_system="cloth", **fields)
        m["contract"]["simulation_class"] = ["rigid_body", "soft_body"]

    def test_missing_fields(self):
        self.assertIn("solver_choice", self.checks(lambda m: obj(m, "crate_a").pop("blender_system")))
        self.assertIn("solver_choice", self.checks(lambda m: obj(m, "crate_a").pop("simulation_class")))

    def test_unknown_value(self):
        self.assertIn("solver_choice", self.checks(lambda m: obj(m, "crate_a").__setitem__("blender_system", "houdini")))

    def test_deviation_without_reason(self):
        self.assertEqual({"solver_choice"}, self.checks(lambda m: self.jelly(m)))

    def test_deviation_with_reason_passes(self):
        reason = {"reason": "Soft Body probe lost 30% volume on impact", "evidence": ["reports/probe.json"]}
        self.assertEqual(set(), self.checks(lambda m: self.jelly(m, solver_choice_reason=reason)))

    def test_blank_reason_or_no_evidence(self):
        for reason in ({"reason": "  ", "evidence": ["reports/probe.json"]}, {"reason": "easier"},
                       {"reason": "easier", "evidence": []}, None):
            with self.subTest(reason=reason):
                self.assertIn("solver_choice", self.checks(lambda m: self.jelly(m, solver_choice_reason=reason)))

    def test_fixed_and_kinematic_need_no_reason(self):
        self.assertEqual(set(), self.checks(lambda m: obj(m, "floor").__setitem__("blender_system", "bullet_rigid_body")))

    def test_case_class_mismatch(self):
        def cloth_case(m):
            self.jelly(m, solver_choice_reason={"reason": "r", "evidence": ["reports/p.json"]})
            m["contract"]["simulation_class"] = ["rigid_body", "cloth"]
        self.assertIn("simulation_class", self.checks(cloth_case))
        self.assertIn("simulation_class", self.checks(
            lambda m: m["contract"].__setitem__("simulation_class", ["rigid_body", "gas"])))

    def test_coupled_needs_no_object(self):
        self.assertEqual(set(), self.checks(
            lambda m: m["contract"].__setitem__("simulation_class", ["coupled", "rigid_body"])))

    def test_deviations_listed(self):
        m = copy.deepcopy(VALID)
        self.jelly(m, solver_choice_reason={"reason": "r", "evidence": ["reports/p.json"]})
        self.assertEqual([{"object": "crate_a", "simulation_class": "soft_body", "blender_system": "cloth",
                           "preferred": ["soft_body"], "reason": "r", "evidence": ["reports/p.json"]}],
                         spec_audit.solver_deviations(m))
        self.assertEqual([], spec_audit.solver_deviations(VALID))

    def test_evidence_paths_checked_for_case_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            (case / "reports").mkdir(parents=True)
            for evidence, code in ((["reports/probe.json"], 1), (["../outside.json"], 1), (["reports/probe.json"], 0)):
                with self.subTest(evidence=evidence, code=code):
                    if code == 0:
                        (case / "reports" / "probe.json").write_text("{}", encoding="utf-8")
                    m = copy.deepcopy(VALID)
                    self.jelly(m, solver_choice_reason={"reason": "r", "evidence": evidence})
                    (case / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
                    proc = subprocess.run([sys.executable, str(AUDIT), str(case / "manifest.json")],
                                          capture_output=True, text=True)
                    self.assertEqual(code, proc.returncode, proc.stdout)
                    if code:
                        self.assertEqual(["solver_choice"], [e["check"] for e in json.loads(proc.stdout)["errors"]])


class RequirementRowTest(AuditCase):
    def checks(self, mutate):
        def with_rows(m):
            m["requirements_matrix"] = copy.deepcopy(ROWS["requirements_matrix"])
            mutate(m["requirements_matrix"])
        return super().checks(with_rows)

    @staticmethod
    def tol(rows, **fields):
        t = rows[1]["tolerances"][0]
        t.update(fields)
        for key in [k for k, v in fields.items() if v is None]:
            t.pop(key)
        return t

    def test_valid_rows_pass(self):
        self.assertEqual(set(), self.checks(lambda rows: None))

    def test_no_matrix_is_not_checked(self):
        self.assertEqual([], spec_audit.audit(copy.deepcopy(VALID)))

    def test_judgement_only_row(self):
        def review(rows):
            rows[2].pop("measures")
            rows[2]["pass_predicate"] = "native asset review"
        self.assertEqual({"measurable"}, self.checks(review))
        self.assertEqual({"measurable"}, self.checks(lambda rows: rows[2].__setitem__("measures", [])))

    def test_tolerance_without_basis(self):
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, basis=None)))
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, basis="taste")))

    def test_tolerance_without_derivation(self):
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, derivation=" ")))

    def test_value_and_ratio(self):
        self.assertIn("tolerance_basis", self.checks(lambda rows: self.tol(rows, ratio=0.01, of="crate_a size")))
        self.assertIn("tolerance_basis", self.checks(lambda rows: self.tol(rows, value_m=None)))
        self.assertIn("tolerance_basis", self.checks(lambda rows: self.tol(rows, value_m=0)))

    def test_numeric_needs_ratio_of_size(self):
        absolute = {"quantity": "centre of mass error", "value_m": 1e-6, "basis": "numeric", "derivation": "1 um"}
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: rows[1].__setitem__("tolerances", [absolute])))
        ratio = {"quantity": "centre of mass error", "ratio": 0.01, "of": "crate_a bbox diagonal", "basis": "numeric",
                 "derivation": "1% of the crate size"}
        self.assertEqual(set(), self.checks(lambda rows: rows[1].__setitem__("tolerances", [ratio])))
        ratio_no_of = dict(ratio, of=None)
        ratio_no_of.pop("of")
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: rows[1].__setitem__("tolerances", [ratio_no_of])))

    def test_default_rule(self):
        # 0.001 mm while the screen allows 3 mm: smaller than the default needs basis request
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, value_m=1e-6)))
        self.assertEqual(set(), self.checks(lambda rows: self.tol(rows, value_m=1e-6, basis="request",
                                                                  derivation="request: 'flush to the micron'")))
        # solver resolution larger than the screen footprint: the solver sets the tolerance
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, solver_m=0.015)))
        self.assertEqual(set(), self.checks(lambda rows: self.tol(rows, solver_m=0.015, value_m=0.015, basis="solver")))
        self.assertEqual(set(), self.checks(lambda rows: self.tol(rows, value_m=0.00305)))  # rounding
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, value_m=0.05)))  # looser

    def test_basis_needs_its_component(self):
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, screen_m=None)))
        self.assertEqual({"tolerance_basis"}, self.checks(lambda rows: self.tol(rows, basis="solver", solver_m=None)))

    def test_length_bound_in_measures(self):
        def hidden(rows):
            rows[2]["measures"].append({"quantity": "collider offset", "comparison": "<=", "value": 1, "unit": "mm"})
        self.assertEqual({"tolerance_basis"}, self.checks(hidden))
        def lower(rows):
            rows[2]["measures"].append({"quantity": "shelf top travel", "comparison": ">=", "value": 0.3, "unit": "m"})
        self.assertEqual(set(), self.checks(lower))

    def test_malformed_measure(self):
        self.assertEqual({"malformed"}, self.checks(lambda rows: rows[2]["measures"][0].__setitem__("comparison", "about")))


if __name__ == "__main__":
    unittest.main()
