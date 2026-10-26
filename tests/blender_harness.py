"""Run a Blender-side test script and read back its results (shared by the helper tests).

Pattern: tests/blender/<topic>_cases.py builds scenes with bpy, calls the helpers,
and prints one line `WORLDAGENT_RESULT <json>`; tests/test_measure_<topic>.py
calls run_cases() and asserts on that JSON. Tests skip when no Blender is set.
"""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "lib"
BLENDER = os.environ.get("WORLDAGENT_BLENDER") or shutil.which("blender")
MARKER = "WORLDAGENT_RESULT "

requires_blender = unittest.skipUnless(BLENDER, "set WORLDAGENT_BLENDER to run Blender tests")


def run_blender_script(script: Path, *args: str, blend: Path | None = None, timeout: float = 900):
    cmd = [BLENDER, "--background", "--factory-startup"]
    if blend is not None:
        cmd.append(str(blend))
    cmd += ["--python-exit-code", "1", "--python-expr", f"import sys; sys.path.insert(0, {json.dumps(str(LIB))})",
            "--python", str(script), "--", *args]
    # Tests control FLIP Fluids themselves (the FLIP bake test passes its path as an argument);
    # a WORLDAGENT_FLIP_PATH exported by the session would make "FLIP missing" cases find it.
    env = {k: v for k, v in os.environ.items() if k != "WORLDAGENT_FLIP_PATH"}
    # Blender writes side files into its working folder (Mantaflow's waveletNoiseTile.bin, the FLIP
    # add-on's icons/); run it in a scratch folder so tests never leave them in the repository.
    with tempfile.TemporaryDirectory(prefix="worldagent_test_cwd_") as cwd:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, cwd=cwd)


def run_cases(script: Path, *args: str, blend: Path | None = None, timeout: float = 900) -> dict:
    proc = run_blender_script(script, *args, blend=blend, timeout=timeout)
    lines = [l for l in proc.stdout.splitlines() if l.startswith(MARKER)]
    if proc.returncode != 0 or len(lines) != 1:
        raise AssertionError(
            f"{script.name} exited {proc.returncode} with {len(lines)} result lines\n"
            f"--- stdout tail\n{chr(10).join(proc.stdout.splitlines()[-30:])}\n"
            f"--- stderr tail\n{chr(10).join(proc.stderr.splitlines()[-30:])}"
        )
    return json.loads(lines[0][len(MARKER):])


def emit(results: dict) -> None:
    """Call from the Blender-side script to report results."""
    print(MARKER + json.dumps(results, default=str))
