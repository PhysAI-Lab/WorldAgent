"""promote writes scene_final_portable.blend when the candidate's // cache paths resolve elsewhere from the case root.

Real Blender (skipped when none is configured). One tiny case: a Mantaflow gas domain whose cache is
`//../../cache/c01` from .work/candidates/c01/scene.blend (so .work/cache/c01), and a particle system
with a disk point cache. Run from the repo root:
    WORLDAGENT_BLENDER=... python3 -m unittest worldagent-plugin/tests/test_promote_portable.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_harness import BLENDER, MARKER, run_blender_script  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
PROMOTE = ROOT / "bin" / "promote.py"
LAYOUT = ROOT / "scripts" / "check_case_layout.py"
_spec = importlib.util.spec_from_file_location("gate_case", ROOT / "tests" / "fixtures" / "gate" / "gate_case.py")
gc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gc)

BUILD = r'''
import bpy, os, sys
path = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 3
bpy.ops.wm.save_as_mainfile(filepath=path)  # so // resolves next to the candidate
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
domain = bpy.context.object
domain.name = "Domain"
d = domain.modifiers.new("Fluid", "FLUID")
d.fluid_type = "DOMAIN"
s = d.domain_settings
s.domain_type, s.resolution_max, s.cache_type = "GAS", 8, "ALL"
s.cache_frame_start, s.cache_frame_end = 1, 3
s.cache_directory = "//../../cache/c01"
bpy.ops.mesh.primitive_cylinder_add(radius=0.3, depth=0.1, location=(0, 0, 0.3))
emitter = bpy.context.object
emitter.name = "Emitter"
flow = emitter.modifiers.new("Fluid", "FLUID")
flow.fluid_type = "FLOW"
flow.flow_settings.flow_type, flow.flow_settings.flow_behavior = "SMOKE", "INFLOW"
bpy.ops.mesh.primitive_plane_add(location=(3, 0, 0))
sprayer = bpy.context.object
sprayer.name = "Sprayer"
sprayer.modifiers.new("Particles", "PARTICLE_SYSTEM")
psys = sprayer.particle_systems[0]
psys.settings.count, psys.settings.frame_start, psys.settings.frame_end = 20, 1, 2
psys.point_cache.use_disk_cache = True
bpy.ops.wm.save_as_mainfile(filepath=path)
with bpy.context.temp_override(object=domain, active_object=domain):
    assert bpy.ops.fluid.bake_all() == {"FINISHED"}
with bpy.context.temp_override(scene=scene, point_cache=psys.point_cache):
    assert bpy.ops.ptcache.bake(bake=True) == {"FINISHED"}
bpy.ops.wm.save_as_mainfile(filepath=path)
'''

# A verifier that reads nothing: the rows pass, and the runner still records the rebased paths.
VERIFIER = r'''
import json, sys
argv = sys.argv[sys.argv.index("--") + 1:]
json.dump({"status": "pass", "measurements": {"value": 1.0}}, open(argv[argv.index("--out") + 1], "w"))
'''

# Density at the last frame and where the cache resolves, from the file Blender opened.
PROBE = r'''
import bpy, json
d = bpy.data.objects["Domain"]
s = d.modifiers["Fluid"].domain_settings
cache = bpy.path.abspath(s.cache_directory)
bpy.context.scene.frame_set(3)
g = bpy.context.evaluated_depsgraph_get()
print(%r + json.dumps([float(sum(d.evaluated_get(g).modifiers["Fluid"].domain_settings.density_grid)), cache]))
''' % MARKER


@unittest.skipUnless(BLENDER, "set WORLDAGENT_BLENDER to run Blender tests")
class PortableCopyTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.case = gc.make_case(self.tmp)
        build = self.tmp / "build.py"
        build.write_text(BUILD, encoding="utf-8")
        proc = run_blender_script(build, str(self.case / gc.CANDIDATE))
        self.assertEqual(0, proc.returncode, proc.stdout[-3000:] + proc.stderr[-3000:])
        for _, script, _ in gc.ROWS.values():
            (self.case / script).write_text(VERIFIER, encoding="utf-8")
        for rid in gc.ROWS:
            gc.record(self.case, rid, "pass")

    def probe(self, blend: Path):
        script = self.tmp / "probe.py"
        script.write_text(PROBE, encoding="utf-8")
        proc = run_blender_script(script, blend=blend)
        line = next(l for l in proc.stdout.splitlines() if l.startswith(MARKER))
        return json.loads(line[len(MARKER):])

    def test_portable_copy_resolves_from_case_root(self):
        proc = subprocess.run([sys.executable, str(PROMOTE), str(self.case), "--candidate", gc.CANDIDATE],
                              capture_output=True, text=True, cwd=self.case,
                              env={**os.environ, "WORLDAGENT_BLENDER": BLENDER})
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        out = json.loads(proc.stdout)
        final, portable = self.case / "scene_final.blend", self.case / "scene_final_portable.blend"
        # the final scene is still the measured file, byte for byte
        self.assertEqual((self.case / gc.CANDIDATE).read_bytes(), final.read_bytes())
        cache = str(self.case / ".work" / "cache" / "c01")
        scene = out["portable_scene"]
        self.assertTrue(scene["verified"], scene)
        self.assertEqual("scene_final_portable.blend", scene["path"])
        self.assertEqual(gc.run_verifier.sha256_file(portable), scene["sha256"])
        self.assertEqual([{"datablock": 'Object["Domain"]',
                           "property": 'modifiers["Fluid"].domain_settings.cache_directory',
                           "from": "//../../cache/c01", "to": "//.work/cache/c01", "resolves_to": cache}],
                         scene["rewritten"])
        # the disk point cache is listed, not rewritten
        self.assertEqual(['particle_systems["Particles"].point_cache'],
                         [e["property"] for e in scene["not_rewritten"]])
        gate_report = json.loads((self.case / out["gate_report"]).read_text(encoding="utf-8"))
        self.assertEqual(scene, gate_report["portable_scene"])
        # opened directly, the portable copy reads the baked smoke; the final scene looks outside the case
        density, resolved = self.probe(portable)
        self.assertEqual(cache, os.path.normpath(resolved))
        self.assertGreater(density, 0)
        self.assertEqual(0.0, self.probe(final)[0])
        self.assertEqual(0, subprocess.run([sys.executable, str(LAYOUT), str(self.case)],
                                           capture_output=True).returncode)
        self.assertEqual([], [p.name for p in ROOT.iterdir() if p.name in ("waveletNoiseTile.bin", "icons")])


if __name__ == "__main__":
    unittest.main()
