"""Plugin hooks: the final-scene write guard and the stop check for unvalidated cases.

No Blender. Run from the repo root:
    python3 -m unittest worldagent-plugin/tests/test_hooks.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
import worldagent_runtime as runtime
GUARD = ROOT / "hooks" / "guard_final_scene.py"
STOP = ROOT / "hooks" / "stop_unverified.py"


def run(script: Path, event) -> subprocess.CompletedProcess:
    data = event if isinstance(event, str) else json.dumps(event)
    return subprocess.run([sys.executable, str(script)], input=data, capture_output=True, text=True)


class HooksJsonTest(unittest.TestCase):
    def test_hooks_reference_existing_scripts(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        commands = [h["command"] for groups in hooks.values() for g in groups for h in g["hooks"]]
        self.assertEqual(3, len(commands))
        for command in commands:
            rel = command.split("${CLAUDE_PLUGIN_ROOT}/", 1)[1].rstrip('"')
            self.assertTrue((ROOT / rel).is_file(), rel)


class GuardTest(unittest.TestCase):
    def bash(self, command):
        return run(GUARD, {"tool_name": "Bash", "tool_input": {"command": command}}).returncode

    def write(self, path, content=""):
        return run(GUARD, {"tool_name": "Write", "tool_input": {"file_path": path, "content": content}}).returncode

    def test_blocks_file_tools_on_final_scene(self):
        self.assertEqual(2, self.write("/c/scene_final.blend"))
        proc = run(GUARD, {"tool_name": "Edit", "tool_input": {"file_path": "scene_final.blend",
                                                                "old_string": "a", "new_string": "b"}})
        self.assertEqual(2, proc.returncode)
        self.assertIn("promote", proc.stderr)

    def test_blocks_written_code_that_saves_final_scene(self):
        self.assertEqual(2, self.write("/c/src/finish.py",
                                       "bpy.ops.wm.save_as_mainfile(filepath='/c/scene_final.blend')\n"))
        self.assertEqual(2, self.write("/c/src/finish.py", "shutil.copy2(candidate, case / 'scene_final.blend')\n"))
        self.assertEqual(2, self.write("/c/src/touch.py",
                                       "bpy.ops.wm.open_mainfile(filepath='scene_final.blend')\n"
                                       "bpy.ops.wm.save_mainfile()\n"))

    def test_allows_code_that_reads_final_scene(self):
        self.assertEqual(0, self.write("/c/src/render.py",
                                       "bpy.ops.wm.open_mainfile(filepath='scene_final.blend')\n"
                                       "bpy.ops.wm.save_as_mainfile(filepath='.work/render/scene.blend')\n"))
        self.assertEqual(0, self.write("/c/src/verify_contact.py", "print('measure')\n"))

    def test_blocks_shell_writes(self):
        for command in [
            "cp .work/candidates/c1/scene.blend case/scene_final.blend",
            "cp -f a.blend scene_final.blend && echo ok",
            "mv scene_final.blend old.blend",
            "rm -f case/scene_final.blend",
            "echo x > case/scene_final.blend",
            "dd if=a.blend of=scene_final.blend",
            "ln -s a.blend scene_final.blend",
            "blender -b --python-expr \"import bpy; bpy.ops.wm.save_as_mainfile(filepath='scene_final.blend')\"",
        ]:
            self.assertEqual(2, self.bash(command), command)

    def test_promote_does_not_cover_the_rest_of_the_command(self):
        self.assertEqual(2, self.bash("python3 bin/promote.py c --candidate x; cp x c/scene_final.blend"))

    def test_python_checks_destination_only(self):
        self.assertEqual(0, self.write("/c/src/r.py", "shutil.copy2('c/scene_final.blend', '/tmp/r.blend')\n"))
        self.assertEqual(0, self.write("/c/src/r.py", "os.path.islink('c/scene_final.blend')\n"))
        self.assertEqual(2, self.write("/c/src/r.py", "shutil.copy2(pathlib.Path('x'), 'c/scene_final.blend')\n"))
        self.assertEqual(2, self.write("/c/src/r.py", "(case / 'scene_final.blend').write_bytes(data)\n"))

    def test_allows_reads_and_promote(self):
        for command in [
            "sha256sum case/scene_final.blend 2>/dev/null",
            "ls -la case/scene_final.blend",
            "cp case/scene_final.blend .work/render/scene.blend",
            "python3 /p/bin/promote.py case --candidate .work/candidates/c1/scene.blend  # writes scene_final.blend",
            "cp a.blend b.blend",
        ]:
            self.assertEqual(0, self.bash(command), command)

    def test_ignores_bad_input(self):
        self.assertEqual(0, run(GUARD, "not json").returncode)
        self.assertEqual(0, run(GUARD, {"tool_name": "Read", "tool_input": {"file_path": "scene_final.blend"}}).returncode)

    def patch(self, body):
        return run(GUARD, {"tool_name": "apply_patch", "tool_input": {
            "command": "*** Begin Patch\n" + body + "\n*** End Patch"}})

    def test_patch_blocks_all_final_destinations(self):
        for operation in ("Add File", "Update File", "Delete File", "Move to"):
            with self.subTest(operation=operation):
                body = f"*** {operation}: case dir/scene_final.blend\n+x"
                if operation == "Move to":
                    body = "*** Update File: candidate.blend\n" + body
                result = self.patch(body)
                self.assertEqual(2, result.returncode, result.stderr)
                self.assertIn("promote", result.stderr)

    def test_patch_blocks_added_save_code_including_multiline(self):
        for code in ("shutil.copy2(src, 'scene_final.blend')",
                     "bpy.ops.wm.save_as_mainfile(\n    filepath='scene_final.blend')"):
            result = self.patch("*** Update File: build.py\n@@\n" +
                                "\n".join("+" + line for line in code.splitlines()))
            self.assertEqual(2, result.returncode, result.stderr)

    def test_patch_allows_reads_and_removing_a_save(self):
        result = self.patch("*** Update File: build.py\n@@\n"
                            "-bpy.ops.wm.save_as_mainfile(filepath='scene_final.blend')\n"
                            "+bpy.ops.wm.open_mainfile(filepath='scene_final.blend')\n"
                            "+shutil.copy2('scene_final.blend', 'preview.blend')")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("", result.stdout)

    def test_patch_checks_each_file(self):
        result = self.patch("*** Add File: safe.txt\n+safe\n*** Delete File: scene_final.blend")
        self.assertEqual(2, result.returncode)

    def test_patch_does_not_combine_code_from_unrelated_files(self):
        result = self.patch("*** Add File: read.py\n"
                            "+bpy.ops.wm.open_mainfile(filepath='scene_final.blend')\n"
                            "*** Add File: build.py\n+bpy.ops.wm.save_mainfile()")
        self.assertEqual(0, result.returncode, result.stderr)


class StopTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.root / "cache")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.transcript = self.root / ".transcript.jsonl"
        self.say("Working on it.")

    def tearDown(self):
        self._tmp.cleanup()

    def say(self, text):
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        entry = {"type": "assistant", "timestamp": stamp, "message": {"content": [{"type": "text", "text": text}]}}
        with self.transcript.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def case(self, name, status="baked", rows=(("contact", "fail"), ("camera", "pass")), old=False):
        case = self.root / name
        case.mkdir(parents=True)
        manifest = {"status": status, "contract": {},
                    "requirements_matrix": [{"id": rid, "status": s} for rid, s in rows]}
        path = case / "manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        if old:
            past = time.time() - 30 * 24 * 3600
            os.utime(path, (past, past))
        return case

    def stop(self, **extra):
        return run(STOP, {"transcript_path": str(self.transcript), "cwd": str(self.root),
                          "stop_hook_active": False, **extra})

    def test_blocks_done_claim_on_unvalidated_case(self):
        self.case("cases/c1")
        self.say("The scene is done and all checks passed.")
        proc = self.stop()
        self.assertEqual(2, proc.returncode)
        self.assertIn("contact: fail", proc.stderr)
        self.assertNotIn("camera", proc.stderr)

    def test_reads_last_assistant_message_field(self):
        self.case("c")
        self.assertEqual(2, self.stop(last_assistant_message="최종 장면 완료했어요").returncode)
        self.assertEqual(0, self.stop(last_assistant_message="Contact still fails at frame 3.").returncode)

    def test_allows_when_validated_or_no_claim(self):
        self.case("good", status="validated", rows=(("contact", "pass"),))
        self.say("Done: promoted.")
        self.assertEqual(0, self.stop().returncode)
        self.case("bad")
        self.say("Contact fails at frame 37; I'll fix the support next.")
        self.assertEqual(0, self.stop().returncode)

    def test_negated_claims_do_not_count(self):
        self.case("c")
        for text in ["I have not finished the bake.", "Contact is not complete yet.", "아직 완료 안 됐어요."]:
            self.assertEqual(0, self.stop(last_assistant_message=text).returncode, text)

    def test_session_start_comes_from_transcript_not_file_times(self):
        # A transcript whose first entry is older than the manifest: the case counts as touched,
        # whatever the filesystem reports for the transcript's own times (Linux has no birth time).
        self.transcript.write_text(json.dumps({"type": "user", "timestamp": "2020-01-01T00:00:00Z"}) + "\n",
                                   encoding="utf-8")
        self.case("c")
        self.say("Done.")
        self.assertEqual(2, self.stop().returncode)

    def test_does_not_descend_into_case_folders(self):
        self.case("c")
        nested = self.case("c/src/inner")
        self.say("Done.")
        proc = self.stop()
        self.assertIn("/c (status", proc.stderr)
        self.assertNotIn(str(nested), proc.stderr)

    def test_allows_second_stop(self):
        self.case("c")
        self.say("Done.")
        self.assertEqual(0, self.stop(stop_hook_active=True).returncode)

    def test_ignores_cases_not_touched_this_session(self):
        self.case("old_case", old=True)
        self.say("Done.")
        self.assertEqual(0, self.stop().returncode)

    def test_codex_session_marker_without_transcript(self):
        runtime.save_session({"session_id": "codex-one", "cwd": str(self.root)}, {}, time.time() - 1)
        self.case("old", old=True)
        proc = self.stop(session_id="codex-one", transcript_path=None, last_assistant_message="Done.")
        self.assertEqual((0, "", ""), (proc.returncode, proc.stdout, proc.stderr))
        self.case("new")
        proc = self.stop(session_id="codex-one", transcript_path=None, last_assistant_message="Done.")
        self.assertEqual(2, proc.returncode)
        self.assertIn("/new (status", proc.stderr)
        self.assertNotIn("/old (status", proc.stderr)
        proc = self.stop(session_id="codex-one", transcript_path=None,
                         last_assistant_message="Done.", stop_hook_active=True)
        self.assertEqual((0, "", ""), (proc.returncode, proc.stdout, proc.stderr))

    def test_marker_takes_precedence_over_legacy_transcript(self):
        self.transcript.write_text('{"timestamp":"2020-01-01T00:00:00Z"}\n')
        os.environ["PLUGIN_ROOT"] = str(ROOT)
        runtime.save_session({"session_id": "fresh"}, {}, time.time())
        self.case("old", old=True)
        proc = self.stop(session_id="fresh", last_assistant_message="Done.")
        self.assertEqual(0, proc.returncode, proc.stderr)

    def test_claude_keeps_transcript_timing_even_if_a_marker_exists(self):
        os.environ.pop("PLUGIN_ROOT", None)
        self.transcript.write_text('{"timestamp":"2020-01-01T00:00:00Z"}\n')
        runtime.save_session({"session_id": "claude-session"}, {}, time.time())
        self.case("old", old=True)
        proc = self.stop(session_id="claude-session", last_assistant_message="Done.")
        self.assertEqual(2, proc.returncode)

    def test_skips_work_folders_and_other_json(self):
        work = self.root / "c" / ".work" / "candidates" / "x"
        work.mkdir(parents=True)
        (work / "manifest.json").write_text(json.dumps({"status": "baked", "contract": {}}), encoding="utf-8")
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "manifest.json").write_text(json.dumps({"name": "web"}), encoding="utf-8")
        self.say("Done.")
        self.assertEqual(0, self.stop().returncode)


if __name__ == "__main__":
    unittest.main()
