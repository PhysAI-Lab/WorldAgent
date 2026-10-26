"""tests/e2e/run_batch.py picks the final render of the selected candidate for the summary."""
import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wa_run_batch", ROOT / "tests/e2e/run_batch.py")
run_batch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(run_batch)

A = ".work/candidates/c01/scene.blend"
B = ".work/candidates/c02/scene.blend"


class RunBatchRenderTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workspace = Path(self._tmp.name)
        self.case = self.workspace / "case"
        for rel, data in ((A, b"candidate A"), (B, b"candidate B")):
            (self.case / rel).parent.mkdir(parents=True)
            (self.case / rel).write_bytes(data)
        (self.case / "reports").mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def manifest(self, **extra):
        rows = [{"id": "req_a", "candidate": A, "status": "pass"}, {"id": "req_b", "candidate": B, "status": "fail"}]
        (self.case / "manifest.json").write_text(json.dumps({"status": "baked", "requirements_matrix": rows, **extra}))

    def report(self, stamp, mode, candidate, status="ok"):
        sha = hashlib.sha256((self.case / candidate).read_bytes()).hexdigest()
        frames = 3 if mode == "final" else 1
        data = {"mode": mode, "status": status, "candidate": candidate, "candidate_sha256": sha,
                "resolution": [1920, 1080] if mode == "final" else [64, 36], "samples": 64,
                "device": {"device_type": "OPTIX"}, "files": [{"path": f"f{i}.png"} for i in range(frames)]}
        (self.case / "reports" / f"render_{stamp}.json").write_text(json.dumps(data))

    def test_final_render_of_selected_candidate_not_later_preview(self):
        self.manifest(validation={"candidate": A, "passed": True})
        self.report("20260101T000000Z", "final", A)
        self.report("20260101T010000Z", "preview", B)
        self.report("20260101T020000Z", "preview", A)
        result = run_batch.evaluate(self.workspace, probe=False)
        self.assertEqual(A, result["candidate"])
        render = result["render"]
        self.assertEqual(("final", "ok", 3, "reports/render_20260101T000000Z.json", A),
                         (render["mode"], render["status"], render["frames"], render["report"], render["candidate"]))
        self.assertEqual(2, render["skipped_reports"])
        results = [{"id": "x", **result}]
        run_batch.write_summary(self.workspace, results)
        self.assertIn("| final ok 3f 1920x1080 |", (self.workspace / "summary.md").read_text())

    def test_limitation_candidate_and_successful_render_preferred(self):
        self.manifest(status="limited", limitation={"candidate": B, "report": "reports/limit_x.json"})
        self.report("20260101T000000Z", "final", B)
        self.report("20260101T010000Z", "final", B, status="error")
        self.report("20260101T020000Z", "final", A)
        render = run_batch.evaluate(self.workspace, probe=False)["render"]
        self.assertEqual(("ok", "reports/render_20260101T000000Z.json"), (render["status"], render["report"]))

    def test_no_final_render_of_selected_candidate_is_reported(self):
        self.manifest(validation={"candidate": A})
        self.report("20260101T000000Z", "final", B)
        self.report("20260101T010000Z", "preview", A)
        result = run_batch.evaluate(self.workspace, probe=False)
        self.assertEqual({"problem": f"no final render of {A}", "skipped_reports": 2}, result["render"])
        run_batch.write_summary(self.workspace, [{"id": "x", **result}])
        self.assertIn(f"| no final render of {A} |", (self.workspace / "summary.md").read_text())

    def test_candidate_hash_must_match(self):
        self.manifest(validation={"candidate": A})
        self.report("20260101T000000Z", "final", A)
        (self.case / A).write_bytes(b"candidate A, changed after the render")
        self.assertIn("problem", run_batch.evaluate(self.workspace, probe=False)["render"])


if __name__ == "__main__":
    unittest.main()
