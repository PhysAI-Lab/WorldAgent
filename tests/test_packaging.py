"""Both hosts resolve to the same distribution and user-only command skills."""
import importlib.util
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent


class PackagingTest(unittest.TestCase):
    def test_manifests_share_identity_and_skill_tree(self):
        claude = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
        codex = json.loads((ROOT / ".codex-plugin/plugin.json").read_text())
        self.assertEqual((claude["name"], claude["version"]), (codex["name"], codex["version"]))
        self.assertIsNone(codex["license"])
        self.assertEqual((ROOT / "skills").resolve(), (ROOT / codex["skills"]).resolve())

    def test_catalogs_resolve_to_same_plugin(self):
        for folder in (".claude-plugin", ".agents/plugins"):
            catalog = json.loads((REPO / folder / "marketplace.json").read_text())
            entry = next(p for p in catalog["plugins"] if p["name"] == "worldagent")
            source = entry["source"]
            if isinstance(source, dict):
                self.assertEqual("local", source["source"])
                source = source["path"]
            self.assertEqual(ROOT.resolve(), (REPO / source).resolve())

    def test_user_commands_are_explicit_in_both_hosts(self):
        for name in ("new", "bake", "verify", "render", "promote", "run"):
            with self.subTest(name=name):
                folder = ROOT / "skills" / name
                self.assertIn("disable-model-invocation: true", (folder / "SKILL.md").read_text())
                self.assertIn("allow_implicit_invocation: false", (folder / "agents/openai.yaml").read_text())

    def test_e2e_invokes_namespaced_skill_and_preserves_request(self):
        spec = importlib.util.spec_from_file_location("wa_e2e", ROOT / "tests/e2e/run_batch.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        prompt = module.PROMPTS["codex"].format(case="case", request='A scene with "quotes", $variables and spaces.')
        cmd = module.command_for(SimpleNamespace(agent="codex", agent_cmd=None, plugin_dir=None), prompt)
        self.assertTrue(prompt.startswith("$worldagent:run --case case "))
        self.assertEqual(prompt, cmd[-1])
        self.assertNotIn("--full-auto", cmd)
        self.assertIn("--approve-for-me", cmd)
        self.assertNotIn("never", cmd)


if __name__ == "__main__":
    unittest.main()
