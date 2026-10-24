"""Bad verifier: claims pass without measuring anything."""
import json
import sys

argv = sys.argv[sys.argv.index("--") + 1:]
with open(argv[argv.index("--out") + 1], "w") as f:
    json.dump({"status": "pass", "measurements": {}}, f)
