"""promote tests: only a re-verified, gated candidate becomes scene_final.blend.

No Blender: tests/fixtures/gate/fake_blender.py stands in for it during re-verification
(a measured pass, or `<verifier>.result.json` when a test writes one). Run from the repo root:
    python3 -m unittest worldagent-plugin/tests/test_promote.py
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
PROMOTE = ROOT / "bin" / "promote.py"
LAYOUT = ROOT / "scripts" / "check_case_layout.py"
FAKE_BLENDER = ROOT / "tests" / "fixtures" / "gate" / "fake_blender.py"
_spec = importlib.util.spec_from_file_location("gate_case", ROOT / "tests" / "fixtures" / "gate" / "gate_case.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


def snapshot(case: Path) -> dict:
    """Every case file except the trace log, which records refusals too and never affects a verdict."""
    return {p.relative_to(case).as_posix(): p.read_bytes() for p in sorted(case.rglob("*"))
            if p.is_file() and not p.relative_to(case).as_posix().startswith("reports/trace")}


class PromoteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case = gc.make_case(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def promote(self, candidate=gc.CANDIDATE, *extra):
        proc = subprocess.run([sys.executable, str(PROMOTE), str(self.case), "--candidate", candidate, *extra],
                              capture_output=True, text=True, cwd=self.case,
                              env={**os.environ, "WORLDAGENT_BLENDER": str(FAKE_BLENDER)})
        return proc.returncode, json.loads(proc.stdout or proc.stderr)

    def verifier_result(self, rid, result):
        """Make the stand-in Blender return `result` for this row's verifier."""
        script = self.case / gc.ROWS[rid][1]
        script.with_name(script.name + ".result.json").write_text(json.dumps(result), encoding="utf-8")

    def assert_not_promoted(self):
        self.assertFalse((self.case / "scene_final.blend").exists())
        self.assertNotEqual("validated", gc.read_manifest(self.case)["status"])

    def assert_layout_passes(self):
        proc = subprocess.run([sys.executable, str(LAYOUT), str(self.case)], capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stdout)

    def test_promotes_passing_candidate(self):
        code, out = self.promote()
        self.assertEqual(0, code, out)
        final = self.case / "scene_final.blend"
        self.assertEqual((self.case / gc.CANDIDATE).read_bytes(), final.read_bytes())
        m = gc.read_manifest(self.case)
        self.assertEqual("validated", m["status"])
        self.assertTrue(m["validation"]["passed"])
        self.assertEqual("scene_final.blend", m["artifacts"]["final_scene"])
        self.assertEqual(out["gate_report"], m["validation"]["gate_report"])
        self.assertIn(out["gate_report"], m["artifacts"]["reports"])
        self.assertTrue(out["gate_report"].startswith("reports/"))
        gate_report = json.loads((self.case / out["gate_report"]).read_text(encoding="utf-8"))
        self.assertTrue(gate_report["pass"])
        self.assertEqual(gc.run_verifier.sha256_file(final), gate_report["candidate_sha256"])
        self.assertTrue((self.case / gc.CANDIDATE).is_file())
        self.assertEqual({"contact", "camera"}, {r["row"] for r in gate_report["reverified"]})
        self.assertIsNone(out["coupling"])  # no solver graph in this case
        # no // path resolves elsewhere from the case root, so no delivery copy is written
        self.assertEqual(([], None), (out["final_relative_paths"], out["portable_scene"]))
        self.assertIsNone(gate_report["portable_scene"])
        self.assertFalse((self.case / "scene_final_portable.blend").exists())
        self.assert_layout_passes()

    def test_refuses_when_reverification_fails(self):
        self.verifier_result("contact", {"status": "fail", "measurements": {"value": 2.0}, "first_failure": {"frame": 3}})
        code, out = self.promote()
        self.assertEqual(1, code)
        self.assertEqual("gate did not pass", out["reason"])
        self.assertEqual(["contact"], [v["row"] for v in out["failing_rows"]])
        self.assert_not_promoted()
        self.assertEqual("fail", gc.row(self.case, "contact")["status"])

    def test_hand_written_pass_does_not_promote(self):
        # The record says pass, but the verifier fails when promote runs it again.
        self.verifier_result("camera", {"status": "fail", "measurements": {"value": 0.0}, "first_failure": {"frame": 1}})
        self.assertEqual("pass", gc.row(self.case, "camera")["status"])
        code, out = self.promote()
        self.assertEqual(1, code)
        self.assertEqual(["camera"], [v["row"] for v in out["failing_rows"]])
        self.assert_not_promoted()

    def test_earlier_fail_record_is_remeasured(self):
        gc.record(self.case, "contact", "fail")
        code, out = self.promote()
        self.assertEqual(0, code, out)

    def test_refuses_row_never_measured(self):
        m = gc.read_manifest(self.case)
        m["requirements_matrix"].append({"id": "extra", "text": "never run", "category": "physics",
                                         "pass_predicate": "measured", "status": "unknown"})
        gc.write_manifest(self.case, m)
        before = snapshot(self.case)
        code, out = self.promote()
        self.assertEqual(1, code)
        self.assertEqual(["extra"], out["rows"])
        self.assertEqual(before, snapshot(self.case))

    def test_reverifies_and_promotes_another_candidate(self):
        other = ".work/candidates/c02/scene.blend"
        gc.add_candidate(self.case, other, b"BLENDER-candidate-c02")
        code, out = self.promote(other)
        self.assertEqual(0, code, out)
        self.assertEqual(b"BLENDER-candidate-c02", (self.case / "scene_final.blend").read_bytes())
        self.assertEqual({other}, {r["candidate"] for r in gc.read_manifest(self.case)["requirements_matrix"]})

    def test_refuses_existing_final_without_replace(self):
        self.assertEqual(0, self.promote()[0])
        new = ".work/candidates/c02/scene.blend"
        gc.add_candidate(self.case, new, b"BLENDER-candidate-c02")
        for rid in gc.ROWS:
            gc.record(self.case, rid, "pass", candidate=new)
        before = snapshot(self.case)
        code, out = self.promote(new)
        self.assertEqual(1, code)
        self.assertIn("--replace", out["reason"])
        self.assertEqual(before, snapshot(self.case))

    def test_replace_supersedes_old_final(self):
        self.assertEqual(0, self.promote()[0])
        old_bytes = (self.case / "scene_final.blend").read_bytes()
        new = ".work/candidates/c02/scene.blend"
        gc.add_candidate(self.case, new, b"BLENDER-candidate-c02")
        for rid in gc.ROWS:
            gc.record(self.case, rid, "pass", candidate=new)
        code, out = self.promote(new, "--replace")
        self.assertEqual(0, code, out)
        self.assertEqual(b"BLENDER-candidate-c02", (self.case / "scene_final.blend").read_bytes())
        self.assertTrue(out["superseded"].startswith(".work/superseded/scene_final_"))
        self.assertEqual(old_bytes, (self.case / out["superseded"]).read_bytes())
        self.assert_layout_passes()

    def test_replace_supersedes_old_portable_copy(self):
        """A portable copy belongs to the final scene it was made from; replacing that scene moves it aside."""
        self.assertEqual(0, self.promote()[0])
        (self.case / "scene_final_portable.blend").write_bytes(b"BLENDER-portable-c01")
        self.assert_layout_passes()
        new = ".work/candidates/c02/scene.blend"
        gc.add_candidate(self.case, new, b"BLENDER-candidate-c02")
        for rid in gc.ROWS:
            gc.record(self.case, rid, "pass", candidate=new)
        code, out = self.promote(new, "--replace")
        self.assertEqual(0, code, out)
        self.assertIsNone(out["portable_scene"])
        self.assertFalse((self.case / "scene_final_portable.blend").exists())
        [kept] = (self.case / ".work" / "superseded").glob("scene_final_portable_*.blend")
        self.assertEqual(b"BLENDER-portable-c01", kept.read_bytes())

    def test_portable_copy_failure_is_reported_not_raised(self):
        """The stand-in Blender cannot rewrite a file: the record says why and no copy is left behind."""
        sys.path.insert(0, str(ROOT / "bin"))
        import promote
        self.assertEqual(0, self.promote()[0])
        listed = [{"datablock": 'Object["Domain"]', "property": "cache", "from": "//../../cache/gas",
                   "final_resolves_to": "elsewhere"}]
        old = os.environ.get("WORLDAGENT_BLENDER")
        os.environ["WORLDAGENT_BLENDER"] = str(FAKE_BLENDER)
        try:
            record = promote.portable_copy(self.case, self.case / gc.CANDIDATE, listed, "t0")
        finally:
            if old is None:
                os.environ.pop("WORLDAGENT_BLENDER")
            else:
                os.environ["WORLDAGENT_BLENDER"] = old
        self.assertEqual((None, False), (record["path"], record["verified"]))
        self.assertIn("Blender exited", record["problem"])
        self.assertFalse((self.case / "scene_final_portable.blend").exists())
        self.assertEqual([], list((self.case / ".work").glob("portable_*")))
        self.assertIsNone(promote.portable_copy(self.case, self.case / gc.CANDIDATE, [], "t1"))

    def test_layout_refuses_portable_copy_without_final(self):
        (self.case / "scene_final_portable.blend").write_bytes(b"BLENDER-portable")
        proc = subprocess.run([sys.executable, str(LAYOUT), str(self.case)], capture_output=True, text=True)
        self.assertEqual(1, proc.returncode)
        self.assertIn("scene_final_portable.blend exists without scene_final.blend", proc.stdout)

    def test_replace_refuses_identical_final(self):
        self.assertEqual(0, self.promote()[0])
        before = snapshot(self.case)
        code, out = self.promote(gc.CANDIDATE, "--replace")
        self.assertEqual(1, code)
        self.assertIn("already equals", out["reason"])
        self.assertEqual(before, snapshot(self.case))

    def test_refuses_when_layout_already_fails(self):
        (self.case / "stray.txt").write_text("junk", encoding="utf-8")
        before = snapshot(self.case)
        code, out = self.promote()
        self.assertEqual(1, code)
        self.assertIn("layout", out["reason"])
        self.assertEqual(before, snapshot(self.case))


if __name__ == "__main__":
    unittest.main()
