#!/usr/bin/env python3
"""Stand-in for Blender in promote tests: answers --version and writes a verifier result.

The result is `<verifier script>.result.json` if that file exists, else a measured pass.
"""
import json
import sys
from pathlib import Path

argv = sys.argv[1:]
if "--version" in argv:
    print("Blender 0.0 (test stand-in)")
    raise SystemExit(0)
script = Path(argv[argv.index("--python") + 1])
rest = argv[argv.index("--") + 1:]
out = Path(rest[rest.index("--out") + 1])
override = script.with_name(script.name + ".result.json")
result = json.loads(override.read_text()) if override.is_file() else {"status": "pass", "measurements": {"value": 1.0}}
out.write_text(json.dumps(result))
