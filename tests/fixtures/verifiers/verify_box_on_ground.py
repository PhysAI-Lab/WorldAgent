"""Example case verifier: the box's lowest vertex must sit on the ground (z = 0) within 1e-6 m."""
import json
import sys
import bpy

argv = sys.argv[sys.argv.index("--") + 1:]
out = argv[argv.index("--out") + 1]
box = bpy.data.objects["box"]
min_z = min((box.matrix_world @ v.co).z for v in box.data.vertices)
gap = min_z - 0.0
result = {"measurements": {"box_min_z_m": min_z, "gap_m": gap}}
if abs(gap) <= 1e-6:
    result["status"] = "pass"
else:
    result["status"] = "fail"
    result["first_failure"] = {"object": "box", "frame": bpy.context.scene.frame_current, "value": gap}
with open(out, "w") as f:
    json.dump(result, f)
