"""Build a small case for gate/promote tests: dummy candidate, cache, verifiers, runner-style reports.

No Blender: reports are written by hand in the shape bin/run_verifier.py writes.
"""
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BIN = ROOT / "bin"
sys.path.insert(0, str(BIN))


def load(name):
    spec = importlib.util.spec_from_file_location(name, BIN / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run_verifier = load("run_verifier")
SPEC = json.loads((ROOT / "tests" / "fixtures" / "specs" / "valid_warehouse_shelf.json").read_text(encoding="utf-8"))
CANDIDATE = ".work/candidates/c01/scene.blend"
CACHE = ".work/cache/fluid"
# row id -> (category, verifier script, extra inputs)
ROWS = {
    "contact": ("physics", "src/verify_contact.py", [CACHE]),
    "camera": ("camera", "src/verify_camera.py", []),
}
# row id -> story beats it covers; together they cover every beat of SPEC
COVERS = {
    "contact": ["tip", "fall", "settle"],
    "camera": ["establish"],
}


def make_case(tmp: Path) -> Path:
    case = tmp / "case"
    for d in ("src", "reports", CACHE, Path(CANDIDATE).parent):
        (case / d).mkdir(parents=True, exist_ok=True)
    (case / "README.md").write_text("# Test case\n", encoding="utf-8")
    (case / CANDIDATE).write_bytes(b"BLENDER-candidate-c01")
    (case / CACHE / "fluid_0001.bobj").write_bytes(b"cache frame 1")
    for _, script, _ in ROWS.values():
        (case / script).write_text("# verifier\nprint('measure')\n", encoding="utf-8")
    manifest = dict(SPEC)
    manifest.update(
        status="baked",
        artifacts={"final_scene": None, "sources": [], "reports": []},
        validation={"passed": False, "summary": {}},
        requirements_matrix=[
            {"id": rid, "text": f"{rid} requirement", "category": cat, "pass_predicate": "measured", "status": "unknown",
             "covers": COVERS[rid]}
            for rid, (cat, _, _) in ROWS.items()
        ],
    )
    write_manifest(case, manifest)
    for rid in ROWS:
        record(case, rid, "pass")
    return case


def read_manifest(case: Path) -> dict:
    return json.loads((case / "manifest.json").read_text(encoding="utf-8"))


def write_manifest(case: Path, manifest: dict) -> None:
    (case / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def row(case: Path, rid: str) -> dict:
    return next(r for r in read_manifest(case)["requirements_matrix"] if r["id"] == rid)


def record(case: Path, rid: str, status: str, candidate: str = CANDIDATE) -> None:
    """Record a measured result for one row the way run_verifier.py does."""
    _, script, inputs = ROWS[rid]
    manifest = read_manifest(case)
    hashes = {candidate: run_verifier.sha256_file(case / candidate), script: run_verifier.sha256_file(case / script)}
    for path in inputs:
        hashes[path] = run_verifier.sha256_path(case / path)
    hashes[run_verifier.SPEC_HASH_KEY] = run_verifier.spec_hash(manifest)
    hashes[run_verifier.LIB_HASH_KEY] = run_verifier.lib_hash()
    result = {"status": status, "measurements": {"value": 0.0}}
    if status == "fail":
        result["first_failure"] = {"frame": 3}
    if status == "blocked":
        result["reason"] = "FLIP Fluids not installed"
    report_path = f"reports/verify_{rid}_{len(list((case / 'reports').iterdir())):04d}.json"
    report = {"row": rid, "status": status, "problem": None, "candidate": candidate,
              "input_hashes": hashes, "result": result}
    (case / report_path).write_text(json.dumps(report, indent=2), encoding="utf-8")
    r = next(r for r in manifest["requirements_matrix"] if r["id"] == rid)
    r.update(status=status, evidence=[report_path], verified_by=script, input_hashes=hashes, candidate=candidate)
    r.pop("first_failure", None)
    r.pop("reason", None)
    if status == "fail":
        r["first_failure"] = result["first_failure"]
    if status in ("blocked", "unknown"):
        r["reason"] = result.get("reason", "verifier crashed")
    write_manifest(case, manifest)


def add_candidate(case: Path, rel: str, data: bytes) -> None:
    (case / rel).parent.mkdir(parents=True, exist_ok=True)
    (case / rel).write_bytes(data)
