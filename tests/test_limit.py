"""limit and spec-baseline tests: a case is `limited` only with runner results on every
non-render row, and spec changes after the first passing audit are listed for review.

No Blender: the case uses dummy files and hand-written runner reports
(tests/fixtures/gate/gate_case.py). Run from the repo root:
    python3 -m unittest worldagent-plugin/tests/test_limit.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIMIT = ROOT / "bin" / "limit.py"
GATE = ROOT / "bin" / "gate.py"
AUDIT = ROOT / "bin" / "spec_audit.py"
_spec = importlib.util.spec_from_file_location("gate_case", ROOT / "tests" / "fixtures" / "gate" / "gate_case.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


def run(script, *args, cwd=None):
    proc = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True, cwd=cwd)
    out = proc.stdout if proc.stdout.strip() else proc.stderr
    return proc.returncode, (json.loads(out) if out.strip().startswith("{") else out)


class CaseTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case = gc.make_case(Path(self._tmp.name))
        # A render row, as every case has one; limit does not require it to be measured.
        m = gc.read_manifest(self.case)
        m["requirements_matrix"].append({"id": "render", "text": "final render", "category": "media",
                                         "pass_predicate": "measured", "status": "unknown", "covers": [],
                                         "measures": [{"quantity": "rendered frames", "comparison": "==",
                                                       "value": 240}]})
        gc.write_manifest(self.case, m)

    def tearDown(self):
        self._tmp.cleanup()

    def limit(self, reason="no measured progress after three causal groups"):
        return run(LIMIT, self.case, "--candidate", self.case / gc.CANDIDATE, "--reason", reason)

    def gate(self):
        return run(GATE, self.case, cwd=self.case)


class LimitTest(CaseTest):
    def test_refuses_unmeasured_rows(self):
        m = gc.read_manifest(self.case)
        m["requirements_matrix"][0].update(status="unknown")
        gc.write_manifest(self.case, m)
        code, out = self.limit()
        self.assertEqual(1, code, out)
        self.assertEqual(["contact"], [r["row"] for r in out["rows"]])
        self.assertNotEqual("limited", gc.read_manifest(self.case).get("status"))

    def test_refuses_when_every_row_passes(self):
        code, out = self.limit()
        self.assertEqual(1, code, out)
        self.assertIn("promote", out["reason"])

    def test_refuses_rows_measured_on_another_candidate(self):
        gc.add_candidate(self.case, ".work/candidates/c02/scene.blend", b"BLENDER-candidate-c02")
        gc.record(self.case, "contact", "fail", candidate=".work/candidates/c02/scene.blend")
        code, out = self.limit()
        self.assertEqual(1, code, out)
        self.assertEqual("contact", out["rows"][0]["row"])

    def test_refuses_stale_rows(self):
        gc.record(self.case, "contact", "fail")
        (self.case / "src" / "verify_contact.py").write_text("# rewritten\n", encoding="utf-8")
        code, out = self.limit()
        self.assertEqual(1, code, out)
        self.assertIn("inputs changed", out["rows"][0]["problem"])

    def test_refuses_hand_written_fail(self):
        m = gc.read_manifest(self.case)
        m["requirements_matrix"][0].update(status="fail")  # report on disk still says pass
        gc.write_manifest(self.case, m)
        code, out = self.limit()
        self.assertEqual(1, code, out)
        self.assertIn("does not match", out["rows"][0]["problem"])

    def test_records_measured_failure(self):
        gc.record(self.case, "contact", "fail")
        code, out = self.limit()
        self.assertEqual(0, code, out)
        self.assertEqual(["contact"], [r["row"] for r in out["failing_rows"]])
        m = gc.read_manifest(self.case)
        self.assertEqual("limited", m["status"])
        self.assertTrue((self.case / m["limitation"]["report"]).is_file())
        report = json.loads((self.case / m["limitation"]["report"]).read_text(encoding="utf-8"))
        self.assertEqual({"frame": 3}, report["failing_rows"][0]["first_failure"])
        events = [json.loads(line) for line in (self.case / "reports" / "trace.jsonl").read_text().splitlines()]
        self.assertEqual("limit", events[-1]["type"])
        code, gate_report = self.gate()
        self.assertTrue(gate_report["limited"]["recorded"])

    def test_gate_flags_hand_written_limited(self):
        m = gc.read_manifest(self.case)
        m["status"] = "limited"
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertFalse(report["limited"]["recorded"])
        self.assertIn("limit.py", report["limited"]["problem"])

    def test_gate_reports_no_limit_for_other_status(self):
        code, report = self.gate()
        self.assertIsNone(report["limited"])


class SpecBaselineTest(CaseTest):
    def audit(self):
        return run(AUDIT, self.case / "manifest.json")

    def test_first_passing_audit_freezes_the_spec(self):
        code, out = self.audit()
        self.assertEqual(0, code, out)
        self.assertTrue(out["spec_baseline"]["created"])
        baseline = json.loads((self.case / "reports" / "spec_baseline.json").read_text(encoding="utf-8"))
        self.assertEqual(gc.run_verifier.spec_hash(gc.read_manifest(self.case)), baseline["spec_hash"])

    def test_later_audit_keeps_the_first_spec(self):
        self.audit()
        first = (self.case / "reports" / "spec_baseline.json").read_text(encoding="utf-8")
        m = gc.read_manifest(self.case)
        m["object_contracts"][0]["dimensions_m"] = [9.0, 9.0, 9.0]
        gc.write_manifest(self.case, m)
        code, out = self.audit()
        self.assertEqual(0, code, out)
        self.assertEqual((False, False), (out["spec_baseline"]["created"], out["spec_baseline"]["matches"]))
        self.assertEqual(first, (self.case / "reports" / "spec_baseline.json").read_text(encoding="utf-8"))

    def test_failing_audit_does_not_freeze(self):
        m = gc.read_manifest(self.case)
        m["contract"]["story"]["beats"][0]["id"] = m["contract"]["story"]["beats"][1]["id"]
        gc.write_manifest(self.case, m)
        code, out = self.audit()
        self.assertEqual(1, code)
        self.assertFalse((self.case / "reports" / "spec_baseline.json").exists())

    def test_gate_lists_spec_changes_made_without_any_measurement(self):
        self.audit()
        m = gc.read_manifest(self.case)
        name = m["object_contracts"][0]["name"]
        m["object_contracts"][0]["dimensions_m"] = [9.0, 9.0, 9.0]
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertTrue(report["spec_baseline"]["changed"])
        self.assertEqual([name], report["spec_baseline"]["diff"]["objects"]["changed"])

    def test_gate_unchanged_spec(self):
        self.audit()
        code, report = self.gate()
        self.assertFalse(report["spec_baseline"]["changed"])

    def test_gate_without_baseline_says_so(self):
        code, report = self.gate()
        self.assertIsNone(report["spec_baseline"]["baseline"])


if __name__ == "__main__":
    unittest.main()
