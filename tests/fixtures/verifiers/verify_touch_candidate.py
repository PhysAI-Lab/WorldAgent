"""Bad verifier: overwrites the original candidate in the case directory."""
import json
import sys
from pathlib import Path

import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
case = Path(argv[argv.index("--case") + 1])
bpy.data.objects["box"].location.z += 1
bpy.ops.wm.save_as_mainfile(filepath=str(case / ".work" / "candidates" / "c01" / "scene.blend"))
with open(argv[argv.index("--out") + 1], "w") as f:
    json.dump({"status": "pass", "measurements": {"moved": 1}}, f)
