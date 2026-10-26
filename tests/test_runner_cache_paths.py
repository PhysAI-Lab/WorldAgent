"""The runner reads caches stored relative to the candidate (`//` paths), not the temp copy's folder.

Real Blender (skipped when none is configured); the FLIP test also needs WORLDAGENT_FLIP_PATH.
Builds one tiny case (Mantaflow gas cache in //cache_gas, particle disk cache in
//blendcache_scene) and runs bin/run_verifier.py on copies of it:
    WORLDAGENT_BLENDER=... python3 -m unittest worldagent-plugin/tests/test_runner_cache_paths.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import BLENDER, MARKER, run_blender_script  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RUNNER = ROOT / "bin" / "run_verifier.py"
FLIP = os.environ.get("WORLDAGENT_FLIP_PATH")
CANDIDATES = Path(".work") / "candidates"

BUILD = r'''
import bpy, os, sys
case = sys.argv[sys.argv.index("--") + 1]
cand = lambda c: os.path.join(case, ".work", "candidates", c, "scene.blend")
for c in ("c01", "c02", "c03"):
    os.makedirs(os.path.dirname(cand(c)), exist_ok=True)
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 5
bpy.ops.wm.save_as_mainfile(filepath=cand("c01"))  # so // resolves next to the candidate
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
domain = bpy.context.object
domain.name = "Domain"
mod = domain.modifiers.new("Fluid", "FLUID")
mod.fluid_type = "DOMAIN"
d = mod.domain_settings
d.domain_type, d.resolution_max, d.cache_type = "GAS", 10, "ALL"
d.cache_frame_start, d.cache_frame_end = 1, 5
d.cache_directory = "//cache_gas"
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0, 0, 0.5))
flow = bpy.context.object.modifiers.new("Fluid", "FLUID")
flow.fluid_type = "FLOW"
flow.flow_settings.flow_type, flow.flow_settings.flow_behavior = "SMOKE", "INFLOW"
bpy.ops.mesh.primitive_plane_add(location=(3, 0, 0))
emitter = bpy.context.object
emitter.name = "Emitter"
emitter.modifiers.new("Particles", "PARTICLE_SYSTEM")
psys = emitter.particle_systems[0]
psys.settings.count, psys.settings.frame_start, psys.settings.frame_end, psys.settings.lifetime = 50, 1, 2, 100
pc = psys.point_cache
pc.use_disk_cache, pc.frame_start, pc.frame_end = True, 1, 5
bpy.ops.wm.save_as_mainfile(filepath=cand("c01"))
with bpy.context.temp_override(object=domain, active_object=domain):
    assert bpy.ops.fluid.bake_all() == {"FINISHED"}
with bpy.context.temp_override(scene=scene, point_cache=pc):
    assert bpy.ops.ptcache.bake(bake=True) == {"FINISHED"}
bpy.ops.wm.save_as_mainfile(filepath=cand("c01"))
d.cache_directory = os.path.join(os.path.dirname(cand("c01")), "cache_gas")  # absolute
bpy.ops.wm.save_as_mainfile(filepath=cand("c02"), copy=True)
d.cache_directory = "//missing_cache"
bpy.ops.wm.save_as_mainfile(filepath=cand("c03"), copy=True, relative_remap=False)
'''

VERIFIERS = {
    # density at the last frame; also where Blender looked for the cache
    "verify_gas.py": r'''
import bpy, json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
domain = bpy.data.objects["Domain"]
cache_dir = bpy.path.abspath(domain.modifiers["Fluid"].domain_settings.cache_directory)
bpy.context.scene.frame_set(5)
graph = bpy.context.evaluated_depsgraph_get()
total = float(sum(domain.evaluated_get(graph).modifiers["Fluid"].domain_settings.density_grid))
result = {"measurements": {"density_total": total, "cache_dir": cache_dir}}
if total > 0:
    result["status"] = "pass"
else:
    result.update(status="fail", first_failure={"frame": 5, "density_total": total})
json.dump(result, open(out, "w"))
''',
    "verify_particles.py": r'''
import bpy, json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
emitter = bpy.data.objects["Emitter"]
bpy.context.scene.frame_set(5)
graph = bpy.context.evaluated_depsgraph_get()
alive = sum(p.alive_state == "ALIVE" for p in emitter.evaluated_get(graph).particle_systems[0].particles)
result = {"measurements": {"alive": alive}}
if alive == 50:
    result["status"] = "pass"
else:
    result.update(status="fail", first_failure={"frame": 5, "alive": alive})
json.dump(result, open(out, "w"))
''',
    # toggling a point cache setting frees the disk cache (deletes the files next to the copy)
    "verify_free_particles.py": r'''
import bpy, json, os, sys
argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
folder = os.path.join(os.path.dirname(bpy.data.filepath), "blendcache_scene")
before = len(os.listdir(folder))
bpy.data.objects["Emitter"].particle_systems[0].point_cache.use_disk_cache = True
after = len(os.listdir(folder)) if os.path.isdir(folder) else 0
json.dump({"status": "pass", "measurements": {"files_before": before, "files_after": after}}, open(out, "w"))
''',
    # a write into a disk cache file next to the copy (a hard link to the candidate's file)
    "verify_overwrite_particles.py": r'''
import bpy, json, os, sys
argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
folder = os.path.join(os.path.dirname(bpy.data.filepath), "blendcache_scene")
first = sorted(os.listdir(folder))[0]
with open(os.path.join(folder, first), "ab") as f:
    f.write(b"stray")
json.dump({"status": "pass", "measurements": {"written": first}}, open(out, "w"))
''',
    # the runtime's record of the rebase, as a measure helper reads it
    "verify_rebase_record.py": r'''
import json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
runtime = sys.modules.get("worldagent_runtime")
json.dump({"status": "pass", "measurements": {"applied": runtime.applied_rebases()}}, open(out, "w"))
''',
    # a stray bake writes into the cache the measurement reads
    "verify_rebake.py": r'''
import bpy, json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
domain = bpy.data.objects["Domain"]
domain.modifiers["Fluid"].domain_settings.resolution_max = 12
with bpy.context.temp_override(object=domain, active_object=domain):
    bpy.ops.fluid.bake_all()
json.dump({"status": "pass", "measurements": {"rebaked": 1}}, open(out, "w"))
''',
}

FLIP_BUILD = r'''
import bpy, os, sys
sys.path.insert(0, os.environ["WORLDAGENT_LIB"])
from worldagent_measure import liquid
case = sys.argv[sys.argv.index("--") + 1]
path = os.path.join(case, ".work", "candidates", "c01", "scene.blend")
os.makedirs(os.path.dirname(path), exist_ok=True)
liquid.enable_flip()
for obj in list(bpy.data.objects):  # not read_factory_settings, which would unload the add-on
    bpy.data.objects.remove(obj, do_unlink=True)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 3
bpy.ops.wm.save_as_mainfile(filepath=path)  # so // resolves next to the candidate
bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1.0))
domain = bpy.context.object
domain.name = "flip_domain"
domain.flip_fluid.is_active = True
domain.flip_fluid.object_type = "TYPE_DOMAIN"
domain.flip_fluid.domain.simulation.resolution = 16
domain.flip_fluid.domain.cache.cache_directory = "//flip_cache"
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.4, location=(0, 0, 1.2))
ball = bpy.context.object
ball.flip_fluid.is_active = True
ball.flip_fluid.object_type = "TYPE_FLUID"
bpy.ops.wm.save_as_mainfile(filepath=path)
bpy.ops.object.select_all(action="DESELECT")
domain.select_set(True)
bpy.context.view_layer.objects.active = domain
assert bpy.ops.flip_fluid_operators.bake_fluid_simulation_cmd() == {"FINISHED"}
bpy.ops.wm.save_as_mainfile(filepath=path)
'''

FLIP_VERIFIER = r'''
import bpy, json, sys
from worldagent_measure import liquid
argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
liquid.enable_flip()
domain = bpy.data.objects["flip_domain"]
surface = liquid.surface_object(domain, 2)
json.dump({"status": "pass",
           "measurements": {"cache_dir": bpy.path.abspath(domain.flip_fluid.domain.cache.cache_directory),
                            "vertices": len(surface.vertices)}}, open(out, "w"))
'''


def manifest(row_ids) -> dict:
    return {"contract": {"fps": 24, "frame_range": [1, 5], "story": {"beats": []}},
            "requirements_matrix": [{"id": r, "text": r, "category": "physics", "pass_predicate": "measured",
                                     "status": "unknown"} for r in row_ids]}


def tree_hash(folder: Path) -> dict:
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


@unittest.skipUnless(BLENDER, "set WORLDAGENT_BLENDER to run Blender tests")
class RunnerCachePathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._base = tempfile.TemporaryDirectory()
        cls.base = Path(cls._base.name) / "case"
        build = Path(cls._base.name) / "build.py"
        build.write_text(BUILD, encoding="utf-8")
        proc = run_blender_script(build, str(cls.base))
        if proc.returncode != 0:
            raise AssertionError(proc.stdout[-3000:] + proc.stderr[-3000:])
        (cls.base / "src").mkdir()
        for name, text in VERIFIERS.items():
            (cls.base / "src" / name).write_text(text, encoding="utf-8")
        (cls.base / "manifest.json").write_text(json.dumps(manifest(["gas"])), encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        cls._base.cleanup()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.case = Path(tmp.name).resolve() / "case"
        shutil.copytree(self.base, self.case)

    def run_row(self, script: str, candidate: str = "c01"):
        proc = subprocess.run(
            [sys.executable, str(RUNNER), str(self.case), "--row", "gas", "--script", f"src/{script}",
             "--candidate", str(CANDIDATES / candidate / "scene.blend")],
            capture_output=True, text=True, cwd=self.case, env={**os.environ, "WORLDAGENT_BLENDER": BLENDER})
        self.assertIn(proc.returncode, (0, 1), proc.stderr)
        row = next(r for r in json.loads((self.case / "manifest.json").read_text())["requirements_matrix"]
                   if r["id"] == "gas")
        report = json.loads((self.case / row["evidence"][0]).read_text(encoding="utf-8"))
        return row, report

    def entry(self, report, prop_suffix):
        return [e for e in report["rebased_paths"] if e["property"].endswith(prop_suffix)]

    def test_gas_cache_read_from_candidate_folder(self):
        row, report = self.run_row("verify_gas.py")
        self.assertEqual("pass", row["status"], report)
        self.assertGreater(report["result"]["measurements"]["density_total"], 0)
        real = str(self.case / CANDIDATES / "c01" / "cache_gas")
        [entry] = self.entry(report, "domain_settings.cache_directory")
        self.assertEqual(('Object["Domain"]', "//cache_gas", real, True),
                         (entry["datablock"], entry["from"], entry["to"], entry["exists"]))
        self.assertEqual(real, report["result"]["measurements"]["cache_dir"])
        disk = str(self.case / CANDIDATES / "c01" / "blendcache_scene")
        self.assertEqual(([disk, real], [], "fingerprint"), (report["cache_check"]["paths"],
                                                             report["cache_check"]["changed"],
                                                             report["cache_check"]["method"]))
        self.assertGreaterEqual(report["cache_check"]["seconds"], 0)

    def test_rebase_record(self):
        row, report = self.run_row("verify_rebase_record.py")
        self.assertEqual("pass", row["status"], report)
        applied = report["result"]["measurements"]["applied"]
        self.assertEqual(report["rebased_paths"], applied)
        [entry] = [e for e in applied if e["property"].endswith("domain_settings.cache_directory")]
        self.assertEqual("//cache_gas", entry["from"])

    def test_copy_without_rebase_reads_all_zero_grid(self):
        """Regression guard: the reason for the rebase. A copy opened elsewhere reads no density."""
        elsewhere = self.case / "elsewhere"
        elsewhere.mkdir()
        shutil.copy2(self.case / CANDIDATES / "c01" / "scene.blend", elsewhere / "scene.blend")
        probe = self.case / "probe.py"
        probe.write_text(
            "import bpy, json\nd = bpy.data.objects['Domain']\nbpy.context.scene.frame_set(5)\n"
            "g = bpy.context.evaluated_depsgraph_get()\n"
            "s = d.evaluated_get(g).modifiers['Fluid'].domain_settings\n"
            f"print({MARKER!r} + json.dumps([float(sum(s.density_grid)), s.has_cache_baked_data]))\n",
            encoding="utf-8")
        proc = run_blender_script(probe, blend=elsewhere / "scene.blend")
        line = next(l for l in proc.stdout.splitlines() if l.startswith(MARKER))
        self.assertEqual([0.0, True], json.loads(line[len(MARKER):]))  # baked, yet empty

    def test_particle_disk_cache_read(self):
        row, report = self.run_row("verify_particles.py")
        self.assertEqual("pass", row["status"], report)
        [entry] = self.entry(report, "particle_systems[\"Particles\"].point_cache")
        self.assertEqual(("//blendcache_scene", str(self.case / CANDIDATES / "c01" / "blendcache_scene"), True, True),
                         (entry["from"], entry["to"], entry["exists"], entry["read_from_copy"]))

    def test_freed_point_cache_leaves_the_original(self):
        folder = self.case / CANDIDATES / "c01" / "blendcache_scene"
        before = tree_hash(folder)
        row, report = self.run_row("verify_free_particles.py")
        self.assertEqual("pass", row["status"], report)
        m = report["result"]["measurements"]
        self.assertGreater(m["files_before"], m["files_after"])  # Blender deleted the files next to the copy
        self.assertEqual(before, tree_hash(folder))
        self.assertEqual({"linked": len(before), "copied": 0}, report["disk_cache_copy"])

    def test_write_through_a_linked_disk_cache_is_unknown(self):
        folder = self.case / CANDIDATES / "c01" / "blendcache_scene"
        row, report = self.run_row("verify_overwrite_particles.py")
        if report["disk_cache_copy"]["copied"]:
            self.skipTest("temporary folder on another filesystem: the cache was copied, not linked")
        self.assertEqual("unknown", row["status"])
        self.assertEqual(f"verifier changed a cache it read: {folder}", row["reason"])
        self.assertEqual([str(folder)], report["cache_check"]["changed"])

    def test_verifier_that_changes_a_cache_is_unknown(self):
        row, report = self.run_row("verify_rebake.py")
        real = str(self.case / CANDIDATES / "c01" / "cache_gas")
        self.assertEqual("unknown", row["status"])
        self.assertEqual(f"verifier changed a cache it read: {real}", row["reason"])
        self.assertEqual([real], report["cache_check"]["changed"])

    def test_absolute_path_is_left_alone(self):
        row, report = self.run_row("verify_gas.py", candidate="c02")
        self.assertEqual("pass", row["status"], report)
        self.assertEqual([], self.entry(report, "domain_settings.cache_directory"))
        # the absolute path still names the folder the case was built in
        self.assertEqual(str(self.base / CANDIDATES / "c01" / "cache_gas"),
                         report["result"]["measurements"]["cache_dir"])

    def test_missing_cache_names_the_real_path(self):
        row, report = self.run_row("verify_gas.py", candidate="c03")
        real = str(self.case / CANDIDATES / "c03" / "missing_cache")
        [entry] = self.entry(report, "domain_settings.cache_directory")
        self.assertEqual((real, False), (entry["to"], entry["exists"]))
        self.assertEqual("fail", row["status"])
        self.assertEqual(real, report["result"]["measurements"]["cache_dir"])
        self.assertNotIn(real, report["cache_check"]["paths"])  # nothing there to fingerprint


@unittest.skipUnless(BLENDER and FLIP, "set WORLDAGENT_BLENDER and WORLDAGENT_FLIP_PATH to run the FLIP test")
class RunnerFlipCachePathTest(unittest.TestCase):
    def test_flip_cache_read_from_candidate_folder(self):
        """The copy is opened without the add-on; the stored cache_directory is rebased before a verifier enables it."""
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp).resolve() / "case"
            build = Path(tmp) / "build_flip.py"
            build.write_text(FLIP_BUILD, encoding="utf-8")
            env = {**os.environ, "WORLDAGENT_LIB": str(ROOT / "lib")}
            proc = subprocess.run([BLENDER, "--background", "--factory-startup", "--python-exit-code", "1",
                                   "--python", str(build), "--", str(case)], capture_output=True, text=True, env=env)
            self.assertEqual(0, proc.returncode, proc.stdout[-3000:] + proc.stderr[-3000:])
            (case / "src").mkdir()
            (case / "src" / "verify_flip.py").write_text(FLIP_VERIFIER, encoding="utf-8")
            (case / "manifest.json").write_text(json.dumps(manifest(["liquid"])), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(RUNNER), str(case), "--row", "liquid", "--script", "src/verify_flip.py",
                 "--candidate", str(CANDIDATES / "c01" / "scene.blend")], capture_output=True, text=True, cwd=case)
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            report = json.loads((case / json.loads(proc.stdout)["report"]).read_text(encoding="utf-8"))
            real = str(case / CANDIDATES / "c01" / "flip_cache")
            self.assertEqual(real, report["result"]["measurements"]["cache_dir"])
            self.assertGreater(report["result"]["measurements"]["vertices"], 0)
            self.assertEqual(([real], []), (report["cache_check"]["paths"], report["cache_check"]["changed"]))
            [entry] = [e for e in report["rebased_paths"] if e["property"].startswith("flip_fluid")]
            self.assertEqual(("//flip_cache", real, True), (entry["from"], entry["to"], entry["exists"]))


class FinalRelativePathsTest(unittest.TestCase):
    """promote lists rebased // paths that scene_final.blend in the case root would resolve elsewhere."""

    def test_listing(self):
        sys.path.insert(0, str(ROOT / "bin"))
        import promote
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp).resolve()
            cand = case / CANDIDATES / "c01"
            entries = [
                {"datablock": 'Object["Domain"]', "property": "cache", "from": "//../../cache/gas",
                 "to": str(case / ".work" / "cache" / "gas"), "exists": True},
                {"datablock": 'Object["Emitter"]', "property": "point_cache", "from": "//blendcache_scene",
                 "to": str(cand / "blendcache_scene"), "exists": True, "read_from_copy": True},
                {"datablock": 'Image["wood"]', "property": "filepath", "from": "//tex/wood.png",
                 "to": str(case / "tex" / "wood.png"), "exists": True},  # same place from the case root
            ]
            (case / "reports").mkdir()
            (case / "reports" / "verify_a.json").write_text(json.dumps({"rebased_paths": entries}), encoding="utf-8")
            listed = promote.final_relative_paths(case, [{"row": "a", "report": "reports/verify_a.json"},
                                                         {"row": "b", "report": "reports/missing.json"}])
        self.assertEqual([("//../../cache/gas", os.path.normpath(str(case.parent.parent / "cache" / "gas"))),
                          ("//blendcache_scene", str(case / "blendcache_scene_final"))],
                         [(e["from"], e["final_resolves_to"]) for e in listed])


if __name__ == "__main__":
    unittest.main()
