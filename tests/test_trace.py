"""Case trace (bin/case_trace.py): events from the tools, spec snapshots and diffs, refinement notes.

No Blender: promote re-verification uses tests/fixtures/gate/fake_blender.py. Run from the repo root:
    python3 -m unittest worldagent-plugin/tests/test_trace.py
"""
import copy
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRACE = ROOT / "bin" / "case_trace.py"
FAKE_BLENDER = ROOT / "tests" / "fixtures" / "gate" / "fake_blender.py"
sys.path.insert(0, str(ROOT / "bin"))
import case_trace  # noqa: E402

_spec = importlib.util.spec_from_file_location("gate_case", ROOT / "tests" / "fixtures" / "gate" / "gate_case.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)


class TraceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.case = gc.make_case(Path(self._tmp.name))
        (self.case / "reports" / "trace.jsonl").unlink(missing_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def events(self):
        return case_trace.read(self.case)

    def run_tool(self, *argv):
        env = {**os.environ, "WORLDAGENT_BLENDER": str(FAKE_BLENDER)}
        return subprocess.run([sys.executable, *map(str, argv)], capture_output=True, text=True, cwd=self.case, env=env)

    def test_first_event_snapshots_the_spec(self):
        case_trace.record(self.case, {"type": "gate", "pass": False})
        kinds = [e["type"] for e in self.events()]
        self.assertEqual(["spec_change", "gate"], kinds)
        snapshot = self.case / self.events()[0]["snapshot"]
        self.assertTrue(snapshot.is_file())
        self.assertIn("contract", json.loads(snapshot.read_text(encoding="utf-8")))
        case_trace.record(self.case, {"type": "gate", "pass": True})
        self.assertEqual(["spec_change", "gate", "gate"], [e["type"] for e in self.events()])

    def test_spec_change_lists_what_changed(self):
        case_trace.record(self.case, {"type": "gate", "pass": False})
        m = gc.read_manifest(self.case)
        beats = m["contract"]["story"]["beats"]
        beats.append(dict(beats[-1], id="aftermath"))
        m["object_contracts"] = [o for o in m["object_contracts"] if o["name"] != "crate_b"]
        m["contract"]["fps"] = 30
        gc.write_manifest(self.case, m)
        case_trace.record(self.case, {"type": "gate", "pass": False})
        change = [e for e in self.events() if e["type"] == "spec_change"][-1]
        self.assertEqual(["aftermath"], change["diff"]["beats"]["added"])
        self.assertEqual(["crate_b"], change["diff"]["objects"]["removed"])
        self.assertIn("fps", change["diff"]["contract_fields"])

    def test_implementation_fields_listed_apart(self):
        old = gc.read_manifest(self.case)
        new = copy.deepcopy(old)
        obj, beat = new["object_contracts"][0], new["contract"]["story"]["beats"][0]
        obj.update(solver_owner="motor constraint", allowed_inputs=["motor"], solver_owned_outputs=["pose"])
        beat["owner"] = "Bullet with a motor constraint"
        part = lambda m: case_trace.spec_part(m)  # noqa: E731
        diff = case_trace.spec_diff(part(old), part(new))
        self.assertEqual({"implementation"}, set(diff))
        self.assertEqual({obj["name"]: ["solver_owner", "allowed_inputs", "solver_owned_outputs"]},
                         diff["implementation"]["objects"])
        self.assertEqual({beat["id"]: ["owner"]}, diff["implementation"]["beats"])
        # A requirement change next to it is still listed as changed.
        beat["frame_interval"] = [beat["frame_interval"][0] + 1, beat["frame_interval"][1] + 1]
        diff = case_trace.spec_diff(part(old), part(new))
        self.assertEqual([beat["id"]], diff["beats"]["changed"])
        self.assertNotIn("objects", diff)
        self.assertIn("beats", diff["implementation"])

    def test_note_and_show(self):
        proc = self.run_tool(TRACE, "note", self.case, "--hypothesis", "box sinks because collision margin is 0",
                             "--change", "collision margin 0 -> 0.002 m", "--result", "accepted",
                             "--reason", "max depth 0.004 -> 0", "--row", "contact", "--metric", "max_depth_m=0")
        self.assertEqual(0, proc.returncode, proc.stderr)
        note = self.events()[-1]
        self.assertEqual(("note", "accepted", ["contact"], {"max_depth_m": "0"}),
                         (note["type"], note["result"], note["rows"], note["metrics"]))
        shown = self.run_tool(TRACE, "show", self.case).stdout
        self.assertIn("note [accepted] box sinks because collision margin is 0", shown)

    def test_tools_record_their_runs(self):
        gate = self.run_tool(ROOT / "bin" / "gate.py", self.case, "--candidate", gc.CANDIDATE)
        self.assertEqual(0, gate.returncode, gate.stderr)
        promote = self.run_tool(ROOT / "bin" / "promote.py", self.case, "--candidate", gc.CANDIDATE)
        self.assertEqual(0, promote.returncode, promote.stderr)
        kinds = [e["type"] for e in self.events()]
        self.assertIn("gate", kinds)
        self.assertEqual(2, kinds.count("verify"))  # promote re-ran both rows through the runner
        last = self.events()[-1]
        self.assertEqual(("promote", True), (last["type"], last["promoted"]))
        history = case_trace.history(self.events(), "contact")
        self.assertEqual(["pass"], [s["status"] for s in history["rows"]["contact"]])

    def test_refused_promote_is_recorded(self):
        promote = ROOT / "bin" / "promote.py"
        self.assertEqual(0, self.run_tool(promote, self.case, "--candidate", gc.CANDIDATE).returncode)
        self.assertEqual(1, self.run_tool(promote, self.case, "--candidate", gc.CANDIDATE).returncode)
        last = self.events()[-1]
        self.assertEqual(("promote", False), (last["type"], last["promoted"]))
        self.assertIn("--replace", last["reason"])

    def test_trace_problems_never_raise(self):
        (self.case / "reports" / "trace.jsonl").mkdir()  # cannot append to a directory
        case_trace.record(self.case, {"type": "gate", "pass": False})



class NoteOutcomeTest(unittest.TestCase):
    EVENTS = [
        {"type": "verify", "row": "contact", "status": "fail", "candidate": "c1", "time": "t1"},
        {"type": "note", "rows": ["contact"], "result": "pending", "time": "t2"},
        {"type": "verify", "row": "camera", "status": "pass", "candidate": "c2", "time": "t3"},
        {"type": "verify", "row": "contact", "status": "pass", "candidate": "c2", "time": "t4"},
        {"type": "note", "rows": [], "result": "pending", "time": "t5"},
        {"type": "note", "rows": ["camera"], "result": "pending", "time": "t6"},
        {"type": "gate", "pass": False, "failing_rows": ["contact"], "candidate": "c2", "time": "t7"},
    ]

    def test_pending_note_shows_the_next_measurement(self):
        notes = case_trace.history(self.EVENTS)["notes"]
        self.assertEqual([{"row": "contact", "status": "pass", "candidate": "c2", "time": "t4"}],
                         notes[0]["measured_after"])
        self.assertEqual("fail", notes[1]["measured_after"][0]["gate"])  # no rows: the next gate run
        self.assertEqual([{"row": "camera", "status": None, "candidate": None, "time": None}],
                         notes[2]["measured_after"])  # never measured again

    def test_row_filter_keeps_outcomes(self):
        notes = case_trace.history(self.EVENTS, row="contact")["notes"]
        self.assertEqual(["t2", "t5"], [n["time"] for n in notes])
        self.assertEqual("pass", notes[0]["measured_after"][0]["status"])


if __name__ == "__main__":
    unittest.main()
