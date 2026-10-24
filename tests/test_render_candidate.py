"""render_candidate and render.check_render_report against a real Blender (skipped when none is configured).

Renders a two-frame cube scene on the CPU with 1 sample. Run from the repo root:
    WORLDAGENT_BLENDER=/path/to/blender python3 -m unittest worldagent-plugin/tests/test_render_candidate.py
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
RENDER = ROOT / "bin" / "render_candidate.py"
RUNNER = ROOT / "bin" / "run_verifier.py"
LAYOUT = ROOT / "scripts" / "check_case_layout.py"
VERIFIERS = ROOT / "tests" / "fixtures" / "verifiers"
BLENDER = os.environ.get("WORLDAGENT_BLENDER") or shutil.which("blender")
CANDIDATE = ".work/candidates/c01/scene.blend"


@unittest.skipUnless(BLENDER, "set WORLDAGENT_BLENDER to run Blender tests")
class RenderCandidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.case = Path(cls._tmp.name) / "case"
        (cls.case / "src").mkdir(parents=True)
        (cls.case / "reports").mkdir()
        (cls.case / Path(CANDIDATE).parent).mkdir(parents=True)
        (cls.case / "README.md").write_text("# Render test\n", encoding="utf-8")
        shutil.copy(VERIFIERS / "verify_final_render.py", cls.case / "src")
        subprocess.run([BLENDER, "--background", "--factory-startup", "--python", str(VERIFIERS / "make_render_scene.py"),
                        "--", str(cls.case / CANDIDATE)], check=True, capture_output=True)
        manifest = {"status": "baked", "requirements_matrix": [
            {"id": "req_render", "text": "Final 1920x1080 Cycles render", "category": "media",
             "pass_predicate": "every frame, 1920x1080, Cycles, this candidate", "status": "unknown"}]}
        (cls.case / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def render(self, *extra, environ=None):
        proc = subprocess.run([sys.executable, str(RENDER), str(self.case), "--candidate", CANDIDATE, *extra],
                              capture_output=True, text=True, timeout=900, cwd=self.case,
                              env=self.environ(environ))
        return proc.returncode, json.loads(proc.stdout or proc.stderr)

    @staticmethod
    def environ(extra=None):
        # No user config file or session snapshot: only what the test sets reaches the render.
        base = {k: v for k, v in os.environ.items()
                if k not in ("WORLDAGENT_RENDER_RESOLUTION", "WORLDAGENT_RENDER_SAMPLES", "EXPECTED_DEVICE",
                             "WORLDAGENT_ENV_FILE", "CODEX_SESSION_ID", "CODEX_THREAD_ID")}
        return {**base, "WORLDAGENT_BLENDER": BLENDER, "WORLDAGENT_CONFIG_FILE": "/nonexistent/worldagent/config.json", **(extra or {})}

    def verify(self, expected_device=None, environ=None):
        env = self.environ(environ)
        if expected_device:
            env["EXPECTED_DEVICE"] = expected_device
        proc = subprocess.run([sys.executable, str(RUNNER), str(self.case), "--row", "req_render",
                               "--script", "src/verify_final_render.py", "--candidate", CANDIDATE],
                              capture_output=True, text=True, timeout=600, cwd=self.case, env=env)
        out = json.loads(proc.stdout)
        report = json.loads((self.case / out["report"]).read_text(encoding="utf-8"))
        return out["status"], report["result"]

    def test_1_final_render_passes_the_check(self):
        code, out = self.render("--device", "CPU", "--samples", "1")
        self.assertEqual(0, code, out)
        self.assertEqual(("final", 2), (out["mode"], out["frames"]))
        self.assertTrue(out["output_dir"].startswith("media/final_"))
        report = json.loads((self.case / out["report"]).read_text(encoding="utf-8"))
        self.assertEqual(([1920, 1080], "CYCLES", 1, "CPU"),
                         (report["resolution"], report["engine"], report["samples"], report["device"]["device_type"]))
        self.assertEqual([[1920, 1080]] * 2, [f["size"] for f in report["files"]])
        status, result = self.verify()
        self.assertEqual("pass", status, result)
        self.assertEqual("fail", self.verify(expected_device="OPTIX")[0])
        proc = subprocess.run([sys.executable, str(LAYOUT), str(self.case)], capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stdout)

    def test_2_preview_does_not_pass_the_final_check(self):
        code, out = self.render("--preview", "--frames", "2", "--resolution", "64x36", "--device", "CPU",
                                "--samples", "1")
        self.assertEqual(0, code, out)
        self.assertTrue(out["output_dir"].startswith("previews/preview_"))
        status, result = self.verify()
        self.assertEqual("fail", status)
        self.assertEqual("not a final render", result["first_failure"]["problem"])

    def test_3_changed_output_frame_fails_the_check(self):
        code, out = self.render("--device", "CPU", "--samples", "1")
        self.assertEqual(0, code, out)
        frame = self.case / out["output_dir"] / "frame_0001.png"
        frame.write_bytes(frame.read_bytes() + b"\0")
        status, result = self.verify()
        self.assertEqual("fail", status)
        self.assertEqual("output files do not match the report", result["first_failure"]["problem"])

    def test_4_configured_resolution_and_samples(self):
        small = {"WORLDAGENT_RENDER_RESOLUTION": "320x180", "WORLDAGENT_RENDER_SAMPLES": "2"}
        code, out = self.render("--device", "CPU", environ=small)
        self.assertEqual(0, code, out)
        report = json.loads((self.case / out["report"]).read_text(encoding="utf-8"))
        self.assertEqual(([320, 180], 2), (report["resolution"], report["samples"]))
        self.assertEqual([[320, 180]] * 2, [f["size"] for f in report["files"]])
        self.assertEqual("pass", self.verify(environ=small)[0])
        # The same render checked against the default 1920x1080 fails.
        status, result = self.verify()
        self.assertEqual("fail", status)
        self.assertIn("1920x1080", result["first_failure"]["problem"])

    def test_final_render_refuses_frame_subset(self):
        code, out = self.render("--frames", "1")
        self.assertEqual(2, code)
        self.assertIn("--preview", out["error"])

    def test_unknown_device_is_blocked_and_keeps_layout(self):
        code, out = self.render("--device", "VULKAN", "--samples", "1")
        self.assertEqual(1, code)
        self.assertEqual("blocked", out["status"])
        self.assertIn("unknown render device", out["problem"])
        self.assertIsNone(out["output_dir"])


if __name__ == "__main__":
    unittest.main()
