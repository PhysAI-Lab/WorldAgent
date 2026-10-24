"""Configuration and session handoff across host hooks, Python and Blender helpers."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))
sys.path.insert(0, str(ROOT / "bin"))
import worldagent_runtime as runtime
import worldagent_env
from blender_harness import BLENDER, requires_blender


class RuntimeTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(("WORLDAGENT_", "PLUGIN_", "CLAUDE_", "CODEX_THREAD_ID", "CODEX_SESSION_ID"))}
        clean.update(WORLDAGENT_CONFIG_FILE=str(self.tmp / "config.json"), XDG_CACHE_HOME=str(self.tmp / "cache"))
        patch = mock.patch.dict(os.environ, clean, clear=True)
        patch.start()
        self.addCleanup(patch.stop)

    def config(self, data):
        runtime.config_path().write_text(json.dumps(data), encoding="utf-8")

    def test_explicit_then_environment_then_config_then_default(self):
        key = "WORLDAGENT_RENDER_DEVICE"
        self.assertEqual(("OPTIX", "default"), runtime.resolve(key, default="OPTIX"))
        self.config({"render_device": "CPU"})
        self.assertEqual(("CPU", "config file"), runtime.resolve(key))
        os.environ[key] = "CUDA"
        self.assertEqual(("CUDA", key), runtime.resolve(key))
        self.assertEqual(("METAL", "configured"), runtime.resolve(key, "METAL"))
        self.assertEqual(("CUDA", key), runtime.resolve(key, "${user_config.render_device}"))

    def test_xdg_location(self):
        os.environ.pop("WORLDAGENT_CONFIG_FILE")
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp)
        self.assertEqual(self.tmp / "worldagent" / "config.json", runtime.config_path())

    def test_malformed_file_reports_problem(self):
        for bad in ("{", "[]"):
            runtime.config_path().write_text(bad)
            with self.assertWarns(RuntimeWarning):
                self.assertEqual(("CPU", "default"), runtime.resolve("WORLDAGENT_RENDER_DEVICE", default="CPU"))
        self.config({"memory_budget_gb": {"bad": "shape"}})
        with self.assertWarns(RuntimeWarning):
            self.assertIsNone(runtime.value("WORLDAGENT_MEMORY_BUDGET_GB"))

    def test_detector_uses_config_for_all_settings(self):
        exe = self.tmp / "blender"
        exe.write_text("#!/bin/sh\necho 'Blender 5.1.1'\n")
        exe.chmod(0o755)
        addon = self.tmp / "flip" / "flip_fluids_addon"
        addon.mkdir(parents=True)
        (addon / "blender_manifest.toml").write_text('version = "1.8.8"\n')
        assets = self.tmp / "assets"
        (assets / "models").mkdir(parents=True)
        self.config({"blender_path": str(exe), "flip_addon_path": str(addon.parent),
                     "asset_library_path": str(assets), "render_device": "CPU",
                     "memory_budget_gb": 4, "time_budget_hours": 2,
                     "render_resolution": "640x360", "render_samples": 16})
        report = worldagent_env.detect()
        self.assertEqual(str(exe), worldagent_env.find_blender()["path"])
        exported = worldagent_env.exports(report)
        self.assertEqual({"WORLDAGENT_BLENDER": str(exe), "WORLDAGENT_FLIP_PATH": str(addon.parent),
                          "WORLDAGENT_ASSET_LIBRARY": str(assets.resolve()), "WORLDAGENT_RENDER_DEVICE": "CPU",
                          "WORLDAGENT_MEMORY_BUDGET_GB": "4", "WORLDAGENT_TIME_BUDGET_HOURS": "2",
                          "WORLDAGENT_RENDER_RESOLUTION": "640x360", "WORLDAGENT_RENDER_SAMPLES": "16"}, exported)
        self.assertEqual("config file", report["memory"]["source"])

    def test_invalid_configured_path_does_not_fall_back_to_autodetection(self):
        self.config({"blender_path": str(self.tmp / "missing")})
        with mock.patch.object(worldagent_env.shutil, "which") as lookup:
            result = worldagent_env.find_blender()
        lookup.assert_not_called()
        self.assertIsNone(result["path"])
        self.assertIn("config file", result["problem"])

    def test_session_snapshot_reaches_a_fresh_process_without_hook_environment(self):
        os.environ["PLUGIN_DATA"] = str(self.tmp / "plugin data")
        path = runtime.save_session({"session_id": "one", "cwd": str(self.tmp)},
                                    {"WORLDAGENT_RENDER_DEVICE": "CPU"}, started_at=123)
        self.assertTrue(path.is_relative_to(self.tmp / "plugin data"))
        child_env = {k: v for k, v in os.environ.items() if k != "PLUGIN_DATA"}
        child_env["WORLDAGENT_ENV_FILE"] = str(path)
        code = "import worldagent_runtime as r; print(r.value('WORLDAGENT_RENDER_DEVICE'))"
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "lib", env=child_env,
                              text=True, capture_output=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertEqual("CPU", proc.stdout.strip())
        child_env.pop("WORLDAGENT_ENV_FILE")
        child_env["CODEX_THREAD_ID"] = "one"
        proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "lib", env=child_env,
                              text=True, capture_output=True)
        self.assertEqual("CPU", proc.stdout.strip())

    def test_sessions_are_isolated_and_resume_keeps_start(self):
        first = runtime.save_session({"session_id": "../one"}, {"WORLDAGENT_RENDER_DEVICE": "CPU"}, 123)
        runtime.save_session({"session_id": "two"}, {"WORLDAGENT_RENDER_DEVICE": "CUDA"}, 456)
        runtime.save_session({"session_id": "../one"}, {"WORLDAGENT_RENDER_DEVICE": "METAL"}, 789)
        self.assertEqual(123, runtime.read_object(first)["started_at"])
        os.environ["CODEX_THREAD_ID"] = "two"
        self.assertEqual("CUDA", runtime.value("WORLDAGENT_RENDER_DEVICE"))
        os.environ["WORLDAGENT_ENV_FILE"] = str(first)
        self.assertEqual("METAL", runtime.value("WORLDAGENT_RENDER_DEVICE"))
        os.environ["WORLDAGENT_RENDER_DEVICE"] = "OPTIX"
        self.assertEqual("OPTIX", runtime.value("WORLDAGENT_RENDER_DEVICE"))

    def test_session_start_detection_ignores_previous_snapshot(self):
        path = runtime.save_session({"session_id": "one"}, {"WORLDAGENT_RENDER_DEVICE": "CPU"})
        os.environ["WORLDAGENT_ENV_FILE"] = str(path)
        self.config({"render_device": "METAL"})
        self.assertEqual(("CPU", "session"), runtime.resolve("WORLDAGENT_RENDER_DEVICE"))
        self.assertEqual(("METAL", "config file"), runtime.resolve("WORLDAGENT_RENDER_DEVICE", use_session=False))

    def test_hook_persists_settings_and_reports_paths(self):
        os.environ.update(PLUGIN_ROOT=str(ROOT), PLUGIN_DATA=str(self.tmp / "data"),
                          CLAUDE_PLUGIN_OPTION_RENDER_DEVICE="CPU", CLAUDE_PLUGIN_OPTION_BLENDER_PATH="/missing")
        proc = subprocess.run([sys.executable, str(ROOT / "hooks" / "session_env.py")],
                              input=json.dumps({"session_id": "hook-session", "cwd": str(self.tmp)}),
                              capture_output=True, text=True)
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn(f"WorldAgent plugin root: {ROOT}", proc.stdout)
        self.assertIn("WorldAgent session environment:", proc.stdout)
        data = runtime.read_object(runtime.session_path("hook-session"))
        self.assertEqual("CPU", data["environment"]["WORLDAGENT_RENDER_DEVICE"])
        self.assertGreater(data["started_at"], 0)


@requires_blender
class BlenderSessionTest(unittest.TestCase):
    def test_helpers_read_snapshot_in_a_fresh_blender_process(self):
        with tempfile.TemporaryDirectory() as folder:
            configured = {"WORLDAGENT_RENDER_DEVICE": "CPU", "WORLDAGENT_RENDER_RESOLUTION": "80x45",
                          "WORLDAGENT_RENDER_SAMPLES": "2"}
            flip = os.environ.get("WORLDAGENT_FLIP_PATH")
            if flip:
                configured["WORLDAGENT_FLIP_PATH"] = flip
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": folder, "PLUGIN_DATA": folder}):
                snapshot = runtime.save_session({"session_id": "blender-handoff"}, configured)
            process_env = {k: v for k, v in os.environ.items() if not k.startswith("WORLDAGENT_")}
            process_env.update(WORLDAGENT_ENV_FILE=str(snapshot), WORLDAGENT_CONFIG_FILE=str(Path(folder) / "none.json"))
            code = (f"import sys,json; sys.path.insert(0,{str(ROOT / 'lib')!r}); "
                    "from worldagent_measure import render,liquid; "
                    "result={'device':render.setup_device()['device_type'],"
                    "'resolution':render.final_resolution(),'samples':render.default_samples()}; ")
            if flip:
                code += "liquid.enable_flip(); result['flip']=liquid.flip_available(); "
            code += "print('HANDOFF '+json.dumps(result))"
            proc = subprocess.run([BLENDER, "--background", "--factory-startup", "--python-exit-code", "1",
                                   "--python-expr", code], env=process_env, capture_output=True, text=True, timeout=90)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            result = json.loads(next(line[8:] for line in proc.stdout.splitlines() if line.startswith("HANDOFF ")))
            expected = {"device": "CPU", "resolution": [80, 45], "samples": 2}
            if flip:
                expected["flip"] = True
            self.assertEqual(expected, result)


if __name__ == "__main__":
    unittest.main()
