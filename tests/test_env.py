"""Environment detection (bin/worldagent_env.py) and the SessionStart hook that exports it.

No Blender: a stand-in executable answers `--version`. Run from the repo root:
    python3 -m unittest worldagent-plugin/tests/test_env.py
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import worldagent_env as env  # noqa: E402

HOOK = ROOT / "hooks" / "session_env.py"
CLEAN = {k: v for k, v in os.environ.items()
         if not k.startswith(("WORLDAGENT_", "BLENDER_USER_", "CLAUDE_PLUGIN_OPTION_", "CLAUDE_ENV_FILE"))}


def fake_blender(folder: Path, version: str) -> Path:
    exe = folder / f"blender-{version}"
    exe.write_text(f"#!/bin/sh\necho 'Blender {version}'\n", encoding="utf-8")
    exe.chmod(0o755)
    return exe


def fake_flip(parent: Path, version: str = "1.8.8", manifest: bool = True) -> Path:
    addon = parent / "flip_fluids_addon"
    addon.mkdir(parents=True)
    if manifest:
        (addon / "blender_manifest.toml").write_text(f'id = "flip_fluids_addon"\nversion = "{version}"\n', encoding="utf-8")
    else:
        major, minor, patch = version.split(".")
        (addon / "__init__.py").write_text(f'bl_info = {{\n    "version" : ({major}, {minor}, {patch}),\n}}\n',
                                           encoding="utf-8")
    return parent


class EnvTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        patcher = mock.patch.dict(os.environ, CLEAN, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ["WORLDAGENT_CONFIG_FILE"] = str(self.tmp / "config.json")
        os.environ["XDG_CACHE_HOME"] = str(self.tmp / "cache")

    def tearDown(self):
        self._tmp.cleanup()


class BlenderTest(EnvTestCase):
    def test_configured_path_wins_and_reports_version(self):
        exe = fake_blender(self.tmp, "5.1.1")
        os.environ["WORLDAGENT_BLENDER"] = str(fake_blender(self.tmp, "5.0.0"))
        info = env.find_blender(str(exe))
        self.assertEqual((str(exe), "configured", "5.1.1", None),
                         (info["path"], info["source"], info["version"], info["problem"]))

    def test_environment_variable_and_placeholder(self):
        exe = fake_blender(self.tmp, "5.1.1")
        os.environ["WORLDAGENT_BLENDER"] = str(exe)
        info = env.find_blender("${user_config.blender_path}")
        self.assertEqual(("WORLDAGENT_BLENDER", str(exe)), (info["source"], info["path"]))

    def test_other_version_is_a_warning(self):
        info = env.find_blender(str(fake_blender(self.tmp, "4.2.0")))
        self.assertEqual("4.2.0", info["version"])
        self.assertIn("targets Blender 5.1", info["problem"])

    def test_missing_file(self):
        info = env.find_blender(str(self.tmp / "nope"))
        self.assertIsNone(info["path"])
        self.assertIn("does not point to a file", info["problem"])

    def test_auto_detect_uses_install_locations(self):
        exe = fake_blender(self.tmp, "5.1.1")
        with mock.patch.object(env.shutil, "which", return_value=None), \
                mock.patch.object(env, "blender_candidates", return_value=[exe]):
            info = env.find_blender()
        self.assertEqual(("auto-detected", str(exe)), (info["source"], info["path"]))

    def test_nothing_found(self):
        with mock.patch.object(env.shutil, "which", return_value=None), \
                mock.patch.object(env, "blender_candidates", return_value=[]):
            info = env.find_blender()
        self.assertIsNone(info["path"])
        self.assertIn("Blender not found", info["problem"])


class FlipTest(EnvTestCase):
    def test_configured_folder_or_addon_folder(self):
        parent = fake_flip(self.tmp / "flip")
        for value in (parent, parent / "flip_fluids_addon"):
            info = env.find_flip(str(value))
            self.assertEqual((str(parent), "1.8.8", None), (info["path"], info["version"], info["problem"]))

    def test_version_from_bl_info(self):
        parent = fake_flip(self.tmp / "legacy", manifest=False)
        self.assertEqual("1.8.8", env.find_flip(str(parent))["version"])

    def test_other_version_is_a_warning(self):
        info = env.find_flip(str(fake_flip(self.tmp / "old", "1.8.4")))
        self.assertIn("written for 1.8.8", info["problem"])

    def test_auto_detect_in_user_addons_and_extensions(self):
        addons = fake_flip(self.tmp / "scripts" / "addons")
        os.environ["BLENDER_USER_SCRIPTS"] = str(self.tmp / "scripts")
        self.assertEqual(str(addons), env.find_flip(version="5.1.1")["path"])
        del os.environ["BLENDER_USER_SCRIPTS"]
        repo = fake_flip(self.tmp / "extensions" / "user_default")
        os.environ["BLENDER_USER_EXTENSIONS"] = str(self.tmp / "extensions")
        info = env.find_flip(version="5.1.1")
        self.assertEqual((str(repo), "auto-detected"), (info["path"], info["source"]))

    def test_missing_means_blocked(self):
        with mock.patch.object(env, "blender_user_dirs", return_value=[]):
            info = env.find_flip()
        self.assertIsNone(info["path"])
        self.assertIn("blocked", info["problem"])
        self.assertIn("folder", env.find_flip(str(self.tmp))["problem"])

    def test_installer_checksum(self):
        good = self.tmp / "FLIP_Fluids_addon.zip"
        good.write_bytes(b"installer")
        sums = self.tmp / "SHA256SUMS.txt"
        sums.write_text(f"{hashlib.sha256(b'installer').hexdigest()}  FLIP_Fluids_addon.zip\n", encoding="utf-8")
        self.assertTrue(env.check_installer(str(good), sums)["matches"])
        bad = self.tmp / "other.zip"
        bad.write_bytes(b"tampered")
        self.assertFalse(env.check_installer(str(bad), sums)["matches"])
        self.assertIn("not found", env.check_installer(str(self.tmp / "none.zip"), sums)["problem"])
        self.assertIsNone(env.check_installer(""))

    def test_plugin_checksum_file_is_readable(self):
        sums = env.known_installer_sums()
        self.assertTrue(sums)
        self.assertTrue(all(len(v) == 64 for v in sums.values()))


class RenderDeviceTest(EnvTestCase):
    def device(self, configured, found):
        with mock.patch.object(env, "gpu_devices", return_value=found):
            return env.render_device("/fake/blender", configured)

    def test_default_is_optix(self):
        r = self.device(None, {"OPTIX": ["A800"], "CUDA": ["A800"]})
        self.assertEqual(("default", "OPTIX", ["A800"]), (r["source"], r["type"], r["devices"]))
        r = self.device(None, {"METAL": ["Apple M5"]})
        self.assertIsNone(r["type"])
        self.assertIn("OPTIX not available (found: METAL)", r["problem"])

    def test_any_gpu_metal_and_cpu(self):
        self.assertEqual("METAL", self.device("any_gpu", {"METAL": ["Apple M5"]})["type"])
        self.assertEqual("OPTIX", self.device("ANY_GPU", {"CUDA": ["x"], "OPTIX": ["x"]})["type"])
        self.assertIn("ANY_GPU not available (found: no GPU)", self.device("ANY_GPU", {})["problem"])
        self.assertEqual("METAL", self.device("METAL", {"METAL": ["Apple M5"]})["type"])
        with mock.patch.object(env, "gpu_devices") as probe:
            self.assertEqual("CPU", env.render_device("/fake/blender", "cpu")["type"])
            probe.assert_not_called()  # CPU needs no Blender probe

    def test_unknown_unprobed_and_environment(self):
        self.assertIn("unknown render device", self.device("VULKAN", {})["problem"])
        self.assertIn("could not list", self.device("OPTIX", None)["problem"])
        os.environ["WORLDAGENT_RENDER_DEVICE"] = "metal"
        r = self.device(None, {"METAL": ["Apple M5"]})
        self.assertEqual(("WORLDAGENT_RENDER_DEVICE", "METAL"), (r["source"], r["type"]))


class AssetLibraryTest(EnvTestCase):
    def test_configured_or_environment_folder(self):
        lib = self.tmp / "assets"
        (lib / "vegetation_3d" / "trees").mkdir(parents=True)
        (lib / "terrain_textures").mkdir()
        found = env.find_asset_library(lib)
        self.assertEqual((str(lib.resolve()), "configured"), (found["path"], found["source"]))
        self.assertEqual({"terrain_textures": 0, "vegetation_3d": 1}, found["folders"])
        os.environ["WORLDAGENT_ASSET_LIBRARY"] = str(lib)
        self.assertEqual("WORLDAGENT_ASSET_LIBRARY", env.find_asset_library("${user_config.asset_library_path}")["source"])

    def test_unset_missing_or_empty(self):
        self.assertEqual((None, None), (env.find_asset_library(None)["path"], env.find_asset_library(None)["problem"]))
        self.assertIn("not found", env.find_asset_library(self.tmp / "nope")["problem"])
        self.assertIn("empty", env.find_asset_library(self.tmp)["problem"])


class MemoryTest(EnvTestCase):
    def test_budget(self):
        with mock.patch.object(env, "total_memory_gb", return_value=16.0):
            self.assertEqual(8.0, env.memory_budget("8")["budget_gb"])
            with mock.patch.dict(os.environ):
                os.environ.pop("WORLDAGENT_MEMORY_BUDGET_GB", None)
                self.assertIsNone(env.memory_budget(None)["budget_gb"])
            self.assertIn("not a number", env.memory_budget("lots")["problem"])
            self.assertIn("positive", env.memory_budget("0")["problem"])
            self.assertIn("exceeds", env.memory_budget("32")["problem"])

    def test_budget_from_environment(self):
        with mock.patch.object(env, "total_memory_gb", return_value=16.0), \
                mock.patch.dict(os.environ, {"WORLDAGENT_MEMORY_BUDGET_GB": "6"}):
            self.assertEqual((6.0, "environment"), tuple(env.memory_budget(None)[k] for k in ("budget_gb", "source")))
            self.assertEqual((8.0, "setting"), tuple(env.memory_budget("8")[k] for k in ("budget_gb", "source")))

    def test_total_memory_is_detected(self):
        self.assertGreater(env.total_memory_gb() or 1, 0)


class SessionHookTest(EnvTestCase):
    def run_hook(self, **options):
        env_file = self.tmp / "session.env"
        process_env = dict(os.environ, CLAUDE_ENV_FILE=str(env_file))
        process_env.update({f"CLAUDE_PLUGIN_OPTION_{k.upper()}": str(v) for k, v in options.items()})
        proc = subprocess.run([sys.executable, str(HOOK)], input="{}", capture_output=True, text=True, env=process_env)
        exported = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
        return proc, exported

    def test_exports_configured_values(self):
        exe = fake_blender(self.tmp, "5.1.1")
        flip = fake_flip(self.tmp / "flip dir")
        (self.tmp / "assets" / "vegetation_3d").mkdir(parents=True)
        proc, exported = self.run_hook(blender_path=exe, flip_addon_path=flip, memory_budget_gb=6,
                                       asset_library_path=self.tmp / "assets", render_device="CPU")
        self.assertEqual(0, proc.returncode, proc.stderr)
        self.assertIn(f"export WORLDAGENT_BLENDER={exe}", exported)
        self.assertIn(f"export WORLDAGENT_FLIP_PATH='{flip}'", exported)
        self.assertIn("export WORLDAGENT_MEMORY_BUDGET_GB=6", exported)
        self.assertIn(f"export WORLDAGENT_ASSET_LIBRARY={(self.tmp / 'assets').resolve()}", exported)
        self.assertIn("vegetation_3d/ (0)", proc.stdout)
        self.assertIn("export WORLDAGENT_RENDER_DEVICE=CPU", exported)
        self.assertIn("Render device: CPU (CPU)", proc.stdout)
        self.assertIn("FLIP Fluids: 1.8.8", proc.stdout)
        self.assertIn("budget 6 GB", proc.stdout)
        self.assertNotIn("WORLDAGENT_ENV_FILE", exported)  # Claude keeps its existing export contract
        self.assertFalse((self.tmp / "cache").exists())  # no Codex session state on the Claude path

    def test_reports_missing_flip_without_failing(self):
        exe = fake_blender(self.tmp, "5.1.1")
        os.environ["HOME"] = str(self.tmp)  # no user add-on folders
        os.environ["XDG_CONFIG_HOME"] = str(self.tmp / "config")
        proc, exported = self.run_hook(blender_path=exe)
        self.assertEqual(0, proc.returncode)
        self.assertNotIn("WORLDAGENT_FLIP_PATH", exported)
        self.assertIn("FLIP requirement rows will be blocked", proc.stdout)
        self.assertNotIn("WORLDAGENT_ASSET_LIBRARY", exported)
        self.assertIn("Asset library: none", proc.stdout)
        self.assertNotIn("WORLDAGENT_RENDER_DEVICE", exported)  # default OPTIX could not be confirmed
        self.assertIn("Render device: could not list render devices", proc.stdout)

    def test_hooks_json_registers_session_start(self):
        hooks = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]
        self.assertIn("session_env.py", hooks["SessionStart"][0]["hooks"][0]["command"])



class TimeBudgetTest(unittest.TestCase):
    def test_default_setting_and_environment(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WORLDAGENT_TIME_BUDGET_HOURS", None)
            self.assertEqual((env.DEFAULT_TIME_BUDGET_HOURS, "default"), tuple(env.time_budget(None)[k] for k in ("hours", "source")))
            self.assertEqual((1.5, "setting"), tuple(env.time_budget("1.5")[k] for k in ("hours", "source")))
        with mock.patch.dict(os.environ, {"WORLDAGENT_TIME_BUDGET_HOURS": "6"}):
            self.assertEqual((6.0, "environment"), tuple(env.time_budget("")[k] for k in ("hours", "source")))

    def test_bad_value_falls_back_with_a_warning(self):
        for bad in ("soon", "-1"):
            t = env.time_budget(bad)
            self.assertEqual(env.DEFAULT_TIME_BUDGET_HOURS, t["hours"])
            self.assertTrue(t["problem"])

    def test_always_exported(self):
        report = env.detect(blender="/nonexistent/blender")
        self.assertIn("WORLDAGENT_TIME_BUDGET_HOURS", env.exports(report))



class RenderOutputTest(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("WORLDAGENT_RENDER_RESOLUTION", None)
        os.environ.pop("WORLDAGENT_RENDER_SAMPLES", None)

    def test_default_is_1920x1080_and_scene_samples(self):
        out = env.render_output()
        self.assertEqual(("1920x1080", "default", None), (out["resolution"], out["resolution_source"], out["samples"]))
        exported = env.exports(env.detect(blender="/nonexistent/blender"))
        self.assertEqual("1920x1080", exported["WORLDAGENT_RENDER_RESOLUTION"])
        self.assertNotIn("WORLDAGENT_RENDER_SAMPLES", exported)

    def test_setting_and_environment(self):
        out = env.render_output("1280X720", "32")
        self.assertEqual(("1280x720", "setting", 32, "setting"),
                         (out["resolution"], out["resolution_source"], out["samples"], out["samples_source"]))
        os.environ.update(WORLDAGENT_RENDER_RESOLUTION="640x360", WORLDAGENT_RENDER_SAMPLES="16")
        out = env.render_output()
        self.assertEqual(("640x360", "environment", 16), (out["resolution"], out["resolution_source"], out["samples"]))

    def test_bad_values_fall_back_with_a_warning(self):
        out = env.render_output("wide", "-4")
        self.assertEqual(("1920x1080", None), (out["resolution"], out["samples"]))
        self.assertIn("WIDTHxHEIGHT", out["problem"])
        self.assertIn("samples", out["problem"])


if __name__ == "__main__":
    unittest.main()
