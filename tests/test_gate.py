"""gate tests: current runner evidence on one candidate passes; anything else does not.

No Blender: the case uses dummy files and hand-written runner reports
(tests/fixtures/gate/gate_case.py). Run from the repo root:
    python3 -m unittest worldagent-plugin/tests/test_gate.py
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "bin" / "gate.py"
_spec = importlib.util.spec_from_file_location("gate_case", ROOT / "tests" / "fixtures" / "gate" / "gate_case.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


class GateTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case = gc.make_case(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def gate(self, *extra):
        proc = subprocess.run([sys.executable, str(GATE), str(self.case), *extra],
                              capture_output=True, text=True, cwd=self.case)
        report = json.loads(proc.stdout) if proc.stdout.strip() else None
        return proc.returncode, report

    def verdicts(self, report):
        return {v["row"]: v["verdict"] for v in report["rows"]}

    def test_all_pass(self):
        code, report = self.gate()
        self.assertEqual(0, code, report)
        self.assertTrue(report["pass"])
        self.assertEqual(gc.CANDIDATE, report["candidate"])
        self.assertEqual(gc.run_verifier.sha256_file(self.case / gc.CANDIDATE), report["candidate_sha256"])
        self.assertEqual({"contact": "pass", "camera": "pass"}, self.verdicts(report))

    def test_changed_helper_library_is_stale(self):
        # Simulate a row measured with an older worldagent_measure: its recorded library hash differs.
        m = gc.read_manifest(self.case)
        r = next(r for r in m["requirements_matrix"] if r["id"] == "contact")
        r["input_hashes"][gc.run_verifier.LIB_HASH_KEY] = "0" * 64
        report_path = self.case / r["evidence"][0]
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report["input_hashes"] = r["input_hashes"]
        report_path.write_text(json.dumps(report), encoding="utf-8")
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual({"contact": "stale", "camera": "pass"}, self.verdicts(report))

    def test_explicit_candidate_pass(self):
        code, report = self.gate("--candidate", gc.CANDIDATE)
        self.assertEqual(0, code, report)

    def test_non_pass_status_fails(self):
        for status in ("fail", "unknown", "blocked"):
            with self.subTest(status=status):
                gc.record(self.case, "camera", status)
                code, report = self.gate()
                self.assertEqual(1, code)
                self.assertEqual({"contact": "pass", "camera": status}, self.verdicts(report))
                gc.record(self.case, "camera", "pass")

    def test_empty_matrix_fails(self):
        m = gc.read_manifest(self.case)
        m["requirements_matrix"] = []
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertIn("requirements_matrix is missing or empty", report["errors"])

    def test_spec_audit_failure_fails(self):
        m = gc.read_manifest(self.case)
        m["topology"]["edges"].append(["nowhere", "door", "nowhere"])
        gc.write_manifest(self.case, m)
        for rid in gc.ROWS:  # re-measure so only the audit can fail
            gc.record(self.case, rid, "pass")
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertTrue(report["spec_audit_errors"])
        self.assertEqual({"contact": "pass", "camera": "pass"}, self.verdicts(report))

    def test_changed_candidate_makes_rows_stale(self):
        (self.case / gc.CANDIDATE).write_bytes(b"edited candidate")
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual({"contact": "stale", "camera": "stale"}, self.verdicts(report))

    def test_mark_stale_touches_only_affected_rows(self):
        before = gc.row(self.case, "camera")
        old_hashes = gc.row(self.case, "contact")["input_hashes"]
        (self.case / gc.CACHE / "fluid_0001.bobj").write_bytes(b"rebaked cache")
        code, report = self.gate("--mark-stale")
        self.assertEqual(1, code)
        self.assertEqual(["contact"], report["marked_stale"])
        contact = gc.row(self.case, "contact")
        self.assertEqual("stale", contact["status"])
        self.assertEqual(old_hashes, contact["input_hashes"])
        self.assertIn(gc.CACHE, contact["reason"])
        self.assertEqual(before, gc.row(self.case, "camera"))

    def test_changed_verifier_script_makes_row_stale(self):
        (self.case / "src" / "verify_camera.py").write_text("# edited verifier\n", encoding="utf-8")
        code, report = self.gate("--mark-stale")
        self.assertEqual(1, code)
        self.assertEqual({"contact": "pass", "camera": "stale"}, self.verdicts(report))
        self.assertEqual("stale", gc.row(self.case, "camera")["status"])
        self.assertEqual("pass", gc.row(self.case, "contact")["status"])

    def test_spec_change_makes_rows_stale(self):
        m = gc.read_manifest(self.case)
        m["contract"]["fps"] = m["contract"]["fps"] + 1
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual({"contact": "stale", "camera": "stale"}, self.verdicts(report))
        for v in report["rows"]:
            self.assertEqual(["manifest.json#spec"], v["changed_inputs"])

    def test_non_spec_manifest_change_keeps_pass(self):
        m = gc.read_manifest(self.case)
        m["limitations"] = ["note added after measurement"]
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertEqual(0, code, report)

    def test_missing_report_is_unverified(self):
        (self.case / gc.row(self.case, "camera")["evidence"][0]).unlink()
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual("unverified", self.verdicts(report)["camera"])

    def test_report_mismatch_is_unverified(self):
        path = self.case / gc.row(self.case, "camera")["evidence"][0]
        report_data = json.loads(path.read_text(encoding="utf-8"))
        report_data["status"] = "fail"
        path.write_text(json.dumps(report_data), encoding="utf-8")
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual("unverified", self.verdicts(report)["camera"])

    def test_hand_written_pass_is_unverified(self):
        m = gc.read_manifest(self.case)
        r = next(r for r in m["requirements_matrix"] if r["id"] == "camera")
        for key in ("verified_by", "input_hashes", "evidence"):
            r.pop(key)
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual("unverified", self.verdicts(report)["camera"])

    def test_rows_on_different_candidates_fail(self):
        other = ".work/candidates/c02/scene.blend"
        gc.add_candidate(self.case, other, b"BLENDER-candidate-c02")
        gc.record(self.case, "camera", "pass", candidate=other)
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertTrue(any("different candidates" in e for e in report["errors"]))
        code, report = self.gate("--candidate", gc.CANDIDATE)
        self.assertEqual(1, code)
        self.assertEqual({"contact": "pass", "camera": "unverified"}, self.verdicts(report))

    def set_covers(self, **covers):
        """Set (list) or drop (None) the covers field of the named rows; covers are not spec inputs."""
        m = gc.read_manifest(self.case)
        for r in m["requirements_matrix"]:
            if r["id"] in covers:
                if covers[r["id"]] is None:
                    r.pop("covers", None)
                else:
                    r["covers"] = covers[r["id"]]
        gc.write_manifest(self.case, m)

    def test_all_beats_covered(self):
        code, report = self.gate()
        self.assertEqual(0, code, report)
        self.assertEqual({"establish": ["camera"], "tip": ["contact"], "fall": ["contact"], "settle": ["contact"]},
                         report["coverage"])
        self.assertEqual([], report["missing_beats"])
        self.assertEqual([], report["unknown_covers"])

    def test_beat_without_row_fails(self):
        self.set_covers(contact=["tip", "fall"])
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertFalse(report["pass"])
        self.assertEqual(["settle"], report["missing_beats"])
        self.assertTrue(any("settle" in e for e in report["errors"]))
        self.assertEqual({"contact": "pass", "camera": "pass"}, self.verdicts(report))

    def test_deleted_row_leaves_beats_uncovered(self):
        m = gc.read_manifest(self.case)
        m["requirements_matrix"] = [r for r in m["requirements_matrix"] if r["id"] != "contact"]
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual(["tip", "fall", "settle"], report["missing_beats"])

    def test_one_row_covers_several_beats_and_beat_several_rows(self):
        self.set_covers(camera=["establish", "settle"])
        code, report = self.gate()
        self.assertEqual(0, code, report)
        self.assertEqual(["camera"], report["coverage"]["establish"])
        self.assertEqual(["contact", "camera"], report["coverage"]["settle"])

    def test_row_id_matching_beat_covers_it(self):
        # A row named after a beat covers that beat without a covers field.
        m = gc.read_manifest(self.case)
        r = next(r for r in m["requirements_matrix"] if r["id"] == "camera")
        r["id"] = "establish"
        r.pop("covers")
        report_path = self.case / r["evidence"][0]
        runner_report = json.loads(report_path.read_text(encoding="utf-8"))
        runner_report["row"] = "establish"
        report_path.write_text(json.dumps(runner_report), encoding="utf-8")
        gc.write_manifest(self.case, m)
        code, report = self.gate()
        self.assertEqual(0, code, report)
        self.assertEqual(["establish"], report["coverage"]["establish"])

    def test_covers_unknown_beat_fails(self):
        self.set_covers(contact=["tip", "fall", "settle", "setle"])
        code, report = self.gate()
        self.assertEqual(1, code)
        self.assertEqual([{"row": "contact", "beat": "setle"}], report["unknown_covers"])
        self.assertEqual([], report["missing_beats"])
        self.assertTrue(any("setle" in e for e in report["errors"]))

    def test_row_covering_no_beat_is_allowed(self):
        # contact matches no beat id and has no covers: it covers nothing, which is fine.
        self.set_covers(contact=None, camera=["establish", "tip", "fall", "settle"])
        code, report = self.gate()
        self.assertEqual(0, code, report)
        self.assertNotIn("contact", sum(report["coverage"].values(), []))

    def test_unreadable_manifest_exits_2(self):
        (self.case / "manifest.json").write_text("{not json", encoding="utf-8")
        code, _ = self.gate()
        self.assertEqual(2, code)


if __name__ == "__main__":
    unittest.main()
