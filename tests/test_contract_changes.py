"""Contract change records: a verifier, pass_predicate or world spec changed after a fail is recorded and listed for review.

No Blender: tests/fixtures/gate/fake_blender.py stands in for it (a measured pass, or
`<verifier>.result.json` when a test writes one). Run from the repo root:
    python3 -m unittest worldagent-plugin/tests/test_contract_changes.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "bin" / "run_verifier.py"
GATE = ROOT / "bin" / "gate.py"
FAKE_BLENDER = ROOT / "tests" / "fixtures" / "gate" / "fake_blender.py"
_spec = importlib.util.spec_from_file_location("gate_case", ROOT / "tests" / "fixtures" / "gate" / "gate_case.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

FAIL = {"status": "fail", "measurements": {"depth_m": 6.4e-4}, "first_failure": {"pair": ["a", "b"]}}


class ContractChangeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case = gc.make_case(Path(self._tmp.name))
        self.script = self.case / gc.ROWS["contact"][1]

    def tearDown(self):
        self._tmp.cleanup()

    def run_row(self, result=None, inputs=()):
        override = self.script.with_name(self.script.name + ".result.json")
        if result is None:
            override.unlink(missing_ok=True)
        else:
            override.write_text(json.dumps(result), encoding="utf-8")
        cmd = [sys.executable, str(RUNNER), str(self.case), "--row", "contact", "--script", gc.ROWS["contact"][1],
               "--candidate", gc.CANDIDATE, "--input", gc.CACHE]
        for item in inputs:
            cmd += ["--input", item]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=self.case,
                              env={**os.environ, "WORLDAGENT_BLENDER": str(FAKE_BLENDER)})
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        return json.loads(proc.stdout)

    def gate(self):
        proc = subprocess.run([sys.executable, str(GATE), str(self.case)], capture_output=True, text=True)
        return json.loads(proc.stdout)

    def trace(self, kind):
        lines = (self.case / "reports" / "trace.jsonl").read_text(encoding="utf-8").splitlines()
        return [e for e in map(json.loads, lines) if e.get("type") == kind]

    def test_rerun_with_same_verifier_records_no_change(self):
        self.run_row(FAIL)
        self.run_row()
        self.assertNotIn("contract_changes", gc.row(self.case, "contact"))
        self.assertEqual([], self.gate()["contract_changes"])

    def test_verifier_rewritten_after_fail_is_recorded_and_listed(self):
        self.run_row(FAIL)
        old_text = self.script.read_text(encoding="utf-8")
        self.script.write_text("# verifier\nEPS_PEN_M = 2e-3\n", encoding="utf-8")
        self.assertEqual("pass", self.run_row()["status"])

        changes = gc.row(self.case, "contact")["contract_changes"]
        self.assertEqual(1, len(changes))
        change = changes[0]
        self.assertEqual(("fail", "pass", True), (change["previous_status"], change["status"], change["verifier_changed"]))
        self.assertEqual(old_text, (self.case / change["from"]["copy"]).read_text(encoding="utf-8"))
        self.assertIn("EPS_PEN_M = 2e-3", (self.case / change["to"]["copy"]).read_text(encoding="utf-8"))
        self.assertEqual(["contact"], [e["row"] for e in self.trace("contract_change")])

        report = self.gate()
        self.assertTrue(report["pass"], report)  # review only: the verdict is unchanged
        self.assertEqual(["contact"], [c["row"] for c in report["contract_changes"]])

    def test_change_after_stale_uses_the_measured_status(self):
        self.run_row(FAIL)
        self.script.write_text("# verifier v2\n", encoding="utf-8")
        subprocess.run([sys.executable, str(GATE), str(self.case), "--mark-stale"], capture_output=True)
        self.assertEqual("stale", gc.row(self.case, "contact")["status"])
        self.run_row()
        self.assertEqual("fail", gc.row(self.case, "contact")["contract_changes"][0]["previous_status"])

    def test_change_after_pass_is_recorded_but_not_listed(self):
        self.run_row()
        self.script.write_text("# verifier v2\n", encoding="utf-8")
        self.run_row()
        self.assertEqual("pass", gc.row(self.case, "contact")["contract_changes"][0]["previous_status"])
        self.assertEqual([], self.gate()["contract_changes"])

    def test_pass_predicate_change_is_recorded(self):
        self.run_row(FAIL)
        m = gc.read_manifest(self.case)
        row = next(r for r in m["requirements_matrix"] if r["id"] == "contact")
        row["pass_predicate"] = "max penetration <= 2 mm"
        gc.write_manifest(self.case, m)
        self.run_row()
        change = gc.row(self.case, "contact")["contract_changes"][0]
        self.assertFalse(change["verifier_changed"])
        self.assertEqual({"from": "measured", "to": "max penetration <= 2 mm"}, change["pass_predicate_changed"])
        self.assertEqual(["contact"], [c["row"] for c in self.gate()["contract_changes"]])

    def test_tolerance_and_measure_changes_are_recorded(self):
        self.run_row(FAIL)  # the runner's report keeps the row's tolerances and measures
        m = gc.read_manifest(self.case)
        row = next(r for r in m["requirements_matrix"] if r["id"] == "contact")
        old_tol, old_measures = row.get("tolerances"), row.get("measures")
        row["tolerances"] = [dict(row["tolerances"][0], value_m=0.01)]  # loosened
        row["measures"] = [{"quantity": "crate speed, last second", "comparison": "<", "value": 0.5, "unit": "m/s"}]
        gc.write_manifest(self.case, m)
        self.run_row()
        change = gc.row(self.case, "contact")["contract_changes"][0]
        self.assertEqual(("fail", False, None), (change["previous_status"], change["verifier_changed"],
                                                change["pass_predicate_changed"]))
        self.assertEqual({"from": old_tol, "to": row["tolerances"]}, change["tolerances_changed"])
        self.assertEqual({"from": old_measures, "to": row["measures"]}, change["measures_changed"])
        self.assertEqual(["contact"], [c["row"] for c in self.gate()["contract_changes"]])

    def test_unchanged_tolerances_record_no_change(self):
        self.run_row(FAIL)
        self.run_row()
        self.assertNotIn("contract_changes", gc.row(self.case, "contact"))

    def test_spec_change_after_fail_is_recorded_and_listed(self):
        self.run_row(FAIL)
        m = gc.read_manifest(self.case)
        m["contract"]["story"]["beats"][1]["pass_predicate"] = "looser"  # loosen a beat's requirement
        gc.write_manifest(self.case, m)
        self.run_row()
        change = gc.row(self.case, "contact")["contract_changes"][0]
        self.assertEqual(("fail", False), (change["previous_status"], change["verifier_changed"]))
        self.assertEqual([m["contract"]["story"]["beats"][1]["id"]], change["spec_changed"]["beats"]["changed"])
        self.assertEqual(["contact"], [c["row"] for c in self.gate()["contract_changes"]])

    def test_spec_change_after_pass_is_not_listed(self):
        self.run_row()
        m = gc.read_manifest(self.case)
        m["contract"]["story"]["beats"][1]["pass_predicate"] = "looser"
        gc.write_manifest(self.case, m)
        self.run_row()
        self.assertTrue(gc.row(self.case, "contact")["contract_changes"][0]["spec_changed"])
        self.assertEqual([], self.gate()["contract_changes"])

    def test_module_input_changed_after_fail_is_recorded_and_listed(self):
        module = self.case / "src" / "verify_common.py"
        module.write_text("EPS_PEN_M = 5e-4\n", encoding="utf-8")
        self.run_row(FAIL, inputs=["src/verify_common.py"])
        module.write_text("EPS_PEN_M = 2e-3\n", encoding="utf-8")  # the verifier file itself is untouched
        self.assertEqual("pass", self.run_row(inputs=["src/verify_common.py"])["status"])

        # [0] is the module first passed as an input (after the fixture's recorded pass)
        change = gc.row(self.case, "contact")["contract_changes"][-1]
        self.assertEqual(("fail", "pass", False), (change["previous_status"], change["status"], change["verifier_changed"]))
        [item] = change["inputs_changed"]
        self.assertEqual("src/verify_common.py", item["path"])
        self.assertNotEqual(item["from"]["sha256"], item["to"]["sha256"])
        self.assertEqual("EPS_PEN_M = 5e-4\n", (self.case / item["from"]["copy"]).read_text(encoding="utf-8"))
        self.assertEqual("EPS_PEN_M = 2e-3\n", (self.case / item["to"]["copy"]).read_text(encoding="utf-8"))
        self.assertEqual(["contact"], [c["row"] for c in self.gate()["contract_changes"]])

    def test_unchanged_module_input_records_no_change(self):
        (self.case / "src" / "verify_common.py").write_text("EPS_PEN_M = 5e-4\n", encoding="utf-8")
        self.run_row(FAIL, inputs=["src/verify_common.py"])
        self.run_row(inputs=["src/verify_common.py"])
        [added] = gc.row(self.case, "contact")["contract_changes"]  # only the first run, which added the input
        self.assertEqual("pass", added["previous_status"])

    def test_module_input_added_or_dropped_is_recorded(self):
        (self.case / "src" / "verify_common.py").write_text("EPS_PEN_M = 5e-4\n", encoding="utf-8")
        self.run_row(FAIL)
        self.run_row(FAIL, inputs=["src/verify_common.py"])
        self.run_row()
        added, dropped = gc.row(self.case, "contact")["contract_changes"]
        self.assertEqual(("src/verify_common.py", None), (added["inputs_changed"][0]["path"], added["inputs_changed"][0]["from"]))
        self.assertIsNotNone(added["inputs_changed"][0]["to"]["copy"])
        self.assertEqual(("src/verify_common.py", None), (dropped["inputs_changed"][0]["path"], dropped["inputs_changed"][0]["to"]))
        self.assertIsNotNone(dropped["inputs_changed"][0]["from"]["copy"])

    def test_verifier_change_alone_has_no_input_changes(self):
        self.run_row(FAIL)
        self.script.write_text("# verifier v2\n", encoding="utf-8")
        self.run_row()
        self.assertIsNone(gc.row(self.case, "contact")["contract_changes"][0]["inputs_changed"])

    def test_promote_output_carries_the_review_fields(self):
        """promote reports contract_changes, spec_baseline and solver_deviations, on success and on refusal."""
        def promote():
            proc = subprocess.run([sys.executable, str(ROOT / "bin" / "promote.py"), str(self.case),
                                   "--candidate", gc.CANDIDATE], capture_output=True, text=True, cwd=self.case,
                                  env={**os.environ, "WORLDAGENT_BLENDER": str(FAKE_BLENDER)})
            return proc.returncode, json.loads(proc.stdout or proc.stderr)

        self.run_row(FAIL)
        code, out = promote()
        self.assertEqual(1, code, out)
        self.assertEqual(self.gate()["solver_deviations"], out["solver_deviations"])
        self.assertIn("spec_baseline", out)
        self.run_row()
        code, out = promote()
        self.assertEqual(0, code, out)
        for key in ("contract_changes", "spec_baseline", "solver_deviations", "final_relative_paths"):
            self.assertIn(key, out)
        self.assertEqual(self.gate()["solver_deviations"], out["solver_deviations"])


if __name__ == "__main__":
    unittest.main()
