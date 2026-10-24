"""run_verifier tests against a real Blender (skipped when none is configured).

Run from the repo root (standard library only):
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_run_verifier.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "bin" / "run_verifier.py"
VERIFIERS = ROOT / "tests" / "fixtures" / "verifiers"
BLENDER = os.environ.get("WORLDAGENT_BLENDER") or shutil.which("blender")


def make_case(tmp: Path, box_bottom_z: float) -> Path:
    case = tmp / "case"
    candidate = case / ".work" / "candidates" / "c01" / "scene.blend"
    candidate.parent.mkdir(parents=True)
    (case / "src").mkdir()
    for script in VERIFIERS.glob("verify_*.py"):
        shutil.copy(script, case / "src" / script.name)
    subprocess.run(
        [BLENDER, "--background", "--factory-startup", "--python", str(VERIFIERS / "make_cube_on_plane.py"),
         "--", str(candidate), str(box_bottom_z)],
        check=True, capture_output=True,
    )
    manifest = {
        "contract": {"fps": 24, "frame_range": [1, 24], "story": {"beats": []}},
        "requirements_matrix": [
            {"id": "box_support", "text": "The box rests on the ground", "category": "physics",
             "pass_predicate": "box bottom within 1e-6 m of ground", "status": "unknown"},
            {"id": "other", "text": "Untouched row", "category": "scene", "pass_predicate": "n/a", "status": "unknown"},
        ],
    }
    (case / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return case


@unittest.skipUnless(BLENDER, "set WORLDAGENT_BLENDER to run Blender tests")
class RunVerifierTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def run_case(self, script: str, box_bottom_z: float = 0.0):
        case = make_case(self.tmp, box_bottom_z)
        proc = subprocess.run(
            [sys.executable, str(RUNNER), str(case), "--row", "box_support", "--script", f"src/{script}",
             "--candidate", ".work/candidates/c01/scene.blend"],
            capture_output=True, text=True, cwd=case, env={**os.environ, "WORLDAGENT_BLENDER": BLENDER},
        )
        manifest = json.loads((case / "manifest.json").read_text(encoding="utf-8"))
        rows = {r["id"]: r for r in manifest["requirements_matrix"]}
        return proc, rows, case

    def test_measured_pass(self):
        proc, rows, case = self.run_case("verify_box_on_ground.py")
        row = rows["box_support"]
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("pass", row["status"])
        self.assertEqual("src/verify_box_on_ground.py", row["verified_by"])
        self.assertIn(".work/candidates/c01/scene.blend", row["input_hashes"])
        self.assertIn("src/verify_box_on_ground.py", row["input_hashes"])
        self.assertIn("manifest.json#spec", row["input_hashes"])
        self.assertIn("plugin:worldagent_measure", row["input_hashes"])
        self.assertNotIn("first_failure", row)
        report = json.loads((case / row["evidence"][0]).read_text(encoding="utf-8"))
        self.assertTrue(report["blender"].startswith("Blender "))
        self.assertAlmostEqual(0.0, report["result"]["measurements"]["gap_m"], places=6)
        self.assertEqual("unknown", rows["other"]["status"])

    def test_measured_fail(self):
        proc, rows, _ = self.run_case("verify_box_on_ground.py", box_bottom_z=0.2)
        row = rows["box_support"]
        self.assertEqual(1, proc.returncode)
        self.assertEqual("fail", row["status"])
        self.assertEqual("box", row["first_failure"]["object"])
        self.assertAlmostEqual(0.2, row["first_failure"]["value"], places=5)

    def test_pass_without_measurements_is_unknown(self):
        _, rows, _ = self.run_case("verify_pass_without_measurements.py")
        self.assertEqual("unknown", rows["box_support"]["status"])
        self.assertIn("without measurements", rows["box_support"]["reason"])

    def test_crash_is_unknown(self):
        _, rows, case = self.run_case("verify_crash.py")
        row = rows["box_support"]
        self.assertEqual("unknown", row["status"])
        self.assertIn("exited with code", row["reason"])
        report = json.loads((case / row["evidence"][0]).read_text(encoding="utf-8"))
        self.assertTrue(any("verifier bug" in line for line in report["stderr_tail"] + report["stdout_tail"]))

    def test_changed_candidate_is_unknown(self):
        _, rows, _ = self.run_case("verify_touch_candidate.py")
        self.assertEqual("unknown", rows["box_support"]["status"])
        self.assertIn("candidate .blend changed", rows["box_support"]["reason"])


if __name__ == "__main__":
    unittest.main()
