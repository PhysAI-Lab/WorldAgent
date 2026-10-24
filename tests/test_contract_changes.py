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

    def run_row(self, result=None):
        override = self.script.with_name(self.script.name + ".result.json")
        if result is None:
            override.unlink(missing_ok=True)
        else:
            override.write_text(json.dumps(result), encoding="utf-8")
        cmd = [sys.executable, str(RUNNER), str(self.case), "--row", "contact", "--script", gc.ROWS["contact"][1],
               "--candidate", gc.CANDIDATE, "--input", gc.CACHE]
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


if __name__ == "__main__":
    unittest.main()
