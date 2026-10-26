"""spec_audit coupling graph tests: the lobby fixture passes; each single defect is reported by its check.

Standard library only. Run from the repo root:
    python3 -m unittest tests/test_coupling_graph.py
"""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("spec_audit", ROOT / "bin" / "spec_audit.py")
spec_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(spec_audit)

SPECS = ROOT / "tests" / "fixtures" / "specs"
LOBBY = json.loads((SPECS / "valid_lobby_coupled.json").read_text(encoding="utf-8"))
WAREHOUSE = json.loads((SPECS / "valid_warehouse_shelf.json").read_text(encoding="utf-8"))
AUDIT = ROOT / "bin" / "spec_audit.py"


def graph(m):
    return m["topology"]["solver_graph"]


def node(m, nid):
    return next(n for n in graph(m)["nodes"] if n["id"] == nid)


def edge(m, eid):
    return next(e for e in graph(m)["edges"] if e["id"] == eid)


def beat(m, bid):
    return next(b for b in m["contract"]["story"]["beats"] if b["id"] == bid)


def obj(m, name):
    return next(o for o in m["object_contracts"] if o["name"] == name)


def cloth_flag(m, support="plastic_crate_orange"):
    """A cloth tarp in its own node, resting on the crate."""
    tarp = copy.deepcopy(obj(m, "plastic_crate_orange"))
    tarp.update(name="tarp", role="tarp", simulation_class="cloth", blender_system="cloth", initial_support=support,
                dimensions_m=[0.6, 0.4, 0.01])
    m["object_contracts"].append(tarp)
    m["contract"]["simulation_class"].append("cloth")
    graph(m)["nodes"].append({"id": "cloth", "objects": ["tarp"]})
    graph(m)["bake_order"].append("cloth")


def mantaflow(m):
    """The water as a Mantaflow liquid (a justified deviation from FLIP), so field_flow back to Bullet exists."""
    obj(m, "water_initial").update(blender_system="mantaflow_liquid", solver_choice_reason={
        "reason": "FLIP Fluids is not installed on this machine", "evidence": ["reports/flip_probe.json"]})
    edge(m, "rigid_to_water")["channel"] = "effector"


def field_flow_push(m, **fields):
    """A Mantaflow water -> Bullet push through a Fluid Flow field (second water pass after the rigid one)."""
    mantaflow(m)
    m["object_contracts"].append(dict(copy.deepcopy(obj(m, "plastic_crate_orange")), name="raft", initial_support="pool"))
    graph(m)["nodes"].append({"id": "raft_rigid", "objects": ["raft"]})
    e = {"id": "water_to_raft", "from": "water", "to": "raft_rigid", "channel": "field_flow", "objects": ["water_initial"],
         "via": ["flow_field"], "settings": {"field.flow": 1.0}, "exchanged": ["grid velocity at the raft centre"],
         "omitted_reactions": ["the raft's displacement of the water"],
         "approximation": "drag from the grid velocity at the centre of mass; no torque, no buoyancy"}
    e.update(fields)
    for key in [k for k, v in fields.items() if v is None]:
        e.pop(key)
    graph(m)["edges"].append(e)
    graph(m)["bake_order"].append("raft_rigid")
    beat(m, "settle")["interactions"] = [{"cause": "water", "effect": "raft", "kind": "translate"}]


class GraphCase(unittest.TestCase):
    base = LOBBY

    def errors(self, mutate):
        m = copy.deepcopy(self.base)
        mutate(m)
        return spec_audit.audit(m)

    def checks(self, mutate):
        return {e["check"] for e in self.errors(mutate)}

    def messages(self, mutate, check):
        return [e["message"] for e in self.errors(mutate) if e["check"] == check]


class RequiredTest(GraphCase):
    def test_valid_coupled_fixture_passes(self):
        self.assertEqual([], spec_audit.audit(copy.deepcopy(LOBBY)))
        summary = spec_audit.coupling_summary(LOBBY)
        self.assertEqual(["rigid_to_water"], [o["edge"] for o in summary["omitted_reactions"]])
        self.assertEqual([], summary["approximations"])

    def test_warehouse_needs_no_graph(self):
        self.assertEqual([], spec_audit.audit(copy.deepcopy(WAREHOUSE)))
        self.assertIsNone(spec_audit.coupling_summary(WAREHOUSE))

    def test_two_systems_need_graph(self):
        msgs = self.messages(lambda m: m["topology"].pop("solver_graph"), "solver_graph_required")
        self.assertEqual(1, len(msgs))
        self.assertIn("bullet_rigid_body, flip_fluids", msgs[0])
        self.assertIn("lists coupled", msgs[0])

    def test_coupled_class_needs_graph(self):
        m = copy.deepcopy(WAREHOUSE)
        m["contract"]["simulation_class"].append("coupled")
        self.assertEqual(["solver_graph_required"], [e["check"] for e in spec_audit.audit(m)])

    def test_coupling_edge_needs_coupled_class(self):
        msgs = self.messages(lambda m: m["contract"]["simulation_class"].remove("coupled"), "solver_graph_required")
        self.assertEqual(["solver_graph couples rigid and water; add coupled to contract.simulation_class"], msgs)

    def test_input_edges_alone_need_no_coupled_class(self):
        fixture = json.loads((SPECS / "valid_justified_solver_deviation.json").read_text(encoding="utf-8"))
        self.assertNotIn("coupled", fixture["contract"]["simulation_class"])
        self.assertEqual([], spec_audit.audit(fixture))


class NodeTest(GraphCase):
    def test_unknown_node_object(self):
        msgs = self.messages(lambda m: node(m, "rigid")["objects"].append("ghost"), "solver_node")
        self.assertEqual(["'ghost' is not in object_contracts"], msgs)

    def test_duplicate_node_id(self):
        self.assertIn("duplicate node id 'rigid'", self.messages(
            lambda m: graph(m)["nodes"].append({"id": "rigid", "objects": ["plastic_crate_orange"]}), "solver_node"))

    def test_mixed_systems_in_node(self):
        def mutate(m):
            cloth_flag(m)
            graph(m)["nodes"].pop()
            graph(m)["bake_order"].pop()
            node(m, "rigid")["objects"].append("tarp")
        msgs = self.messages(mutate, "solver_node")
        self.assertTrue(any("share one simulating blender_system" in t and "tarp is cloth" in t for t in msgs), msgs)

    def test_fixed_object_in_solver_node(self):
        msgs = self.messages(lambda m: node(m, "rigid")["objects"].append("pool"), "solver_node")
        self.assertEqual(["objects of one solver node must share one simulating blender_system; pool is none/fixed"], msgs)

    def test_input_not_declared(self):
        def mutate(m):
            obj(m, "suitcase_cognac_leather")["allowed_inputs"] = []
            beat(m, "slide")["allowed_inputs"] = []
        self.assertEqual(["input 'INPUT_slide_force' is not a kinematic object contract and is not listed in any "
                          "allowed_inputs"], self.messages(mutate, "solver_node"))

    def test_kinematic_contract_is_an_input(self):
        def mutate(m):
            m["object_contracts"].append(dict(copy.deepcopy(obj(m, "pool")), name="ramp", simulation_class="kinematic"))
            node(m, "slide_motor")["objects"].append("ramp")
        self.assertEqual(set(), self.checks(mutate))

    def test_simulated_object_in_no_node(self):
        msgs = self.messages(lambda m: node(m, "rigid")["objects"].remove("suitcase_linen_canvas"), "solver_node")
        self.assertIn("suitcase_linen_canvas (bullet_rigid_body) is in no solver node", msgs)

    def test_object_in_two_solver_nodes(self):
        def mutate(m):
            graph(m)["nodes"].append({"id": "rigid_2", "objects": ["plastic_crate_orange"]})
            graph(m)["bake_order"].append("rigid_2")
        self.assertIn("plastic_crate_orange is owned by nodes rigid and rigid_2; one solver owns an output and bakes "
                      "it once", self.messages(mutate, "solver_node"))

    def test_input_node_may_share_object(self):
        def mutate(m):
            obj(m, "plastic_crate_orange")["allowed_inputs"] = ["plastic_crate_orange"]
            node(m, "slide_motor")["objects"].append("plastic_crate_orange")
        self.assertEqual(set(), self.checks(mutate))

    def test_fluid_node_needs_domain(self):
        def mutate(m):
            m["object_contracts"].append(dict(copy.deepcopy(obj(m, "water_initial")), name="water_inflow"))
            node(m, "water")["objects"].append("water_inflow")
            node(m, "water").pop("domain")
        self.assertEqual(["fluid node needs domain (the domain object's name) when objects are not the domain"],
                         self.messages(mutate, "solver_node"))


class EdgeTest(GraphCase):
    def test_unsupported_channel(self):
        msgs = self.messages(lambda m: edge(m, "rigid_to_water").__setitem__("channel", "collision"), "solver_edge")
        self.assertEqual(1, len(msgs))
        self.assertIn("Blender provides no collision from bullet_rigid_body to flip_fluids "
                      "(schemas/coupling_channels.json; listed: obstacle)", msgs[0])

    def test_flip_drives_nothing(self):
        def mutate(m):
            graph(m)["edges"].append({"id": "water_to_rigid", "from": "water", "to": "rigid", "channel": "field_flow",
                                      "objects": ["water_initial"], "via": ["f"], "settings": {"field.flow": 1},
                                      "approximation": "a", "omitted_reactions": ["x"]})
        msgs = self.messages(mutate, "solver_edge")
        self.assertEqual(1, len(msgs))
        self.assertIn("no field_flow from flip_fluids to bullet_rigid_body", msgs[0])
        self.assertIn("listed: none", msgs[0])

    def test_unknown_or_same_node(self):
        self.assertIn("unknown node 'ocean'", self.messages(
            lambda m: edge(m, "rigid_to_water").__setitem__("to", "ocean"), "solver_edge"))
        self.assertIn("from and to are the same node", self.messages(
            lambda m: edge(m, "rigid_to_water").__setitem__("to", "rigid"), "solver_edge"))

    def test_edge_object_not_in_driver(self):
        self.assertEqual(["'water_initial' is not an object of node rigid"], self.messages(
            lambda m: edge(m, "rigid_to_water").__setitem__("objects", ["water_initial"]), "solver_edge"))

    def test_target_not_in_driven(self):
        self.assertEqual(["'water_initial' is not an object of node rigid"], self.messages(
            lambda m: edge(m, "motor_to_rigid").__setitem__("targets", ["water_initial"]), "solver_edge"))

    def test_domain_may_be_named(self):
        self.assertEqual(set(), self.checks(
            lambda m: edge(m, "rigid_to_water").__setitem__("targets", ["flip_domain"])))

    def test_missing_omitted_reactions(self):
        self.assertEqual(["list what water does not push back on rigid in omitted_reactions"], self.messages(
            lambda m: edge(m, "rigid_to_water").__setitem__("omitted_reactions", []), "coupling_disclosure"))

    def test_input_driver_needs_no_omitted_reactions(self):
        self.assertEqual(set(), self.checks(lambda m: edge(m, "motor_to_rigid").__setitem__("omitted_reactions", [])))

    def test_approximation_text_required(self):
        self.assertEqual(set(), self.checks(field_flow_push))
        self.assertEqual(["field_flow is an approximation; state what it approximates and how in approximation"],
                         self.messages(lambda m: field_flow_push(m, approximation=None), "coupling_disclosure"))

    def test_field_flow_needs_carrier_and_strength(self):
        expected = ["field_flow needs the Fluid Flow field object in via and its strength in settings (field.flow)"]
        self.assertEqual(expected, self.messages(lambda m: field_flow_push(m, via=None), "coupling_disclosure"))
        self.assertEqual(expected, self.messages(lambda m: field_flow_push(m, settings={}), "coupling_disclosure"))
        self.assertEqual(set(), self.checks(lambda m: field_flow_push(m, settings={"flow_field.field.flow": 2.0})))

    def test_scripted_needs_method_and_assumptions(self):
        scripted = {"channel": "scripted_transfer", "approximation": "drag from sampled grid velocity",
                    "method": "src/couple.py: drag from sampled grid velocity drives motor targets",
                    "assumptions": ["rho 1000 kg/m3", "Cd 1"], "via": ["motor_x"], "settings": None}
        self.assertEqual(set(), self.checks(lambda m: field_flow_push(m, **scripted)))
        msgs = self.messages(lambda m: field_flow_push(m, **dict(scripted, method=None)), "coupling_disclosure")
        self.assertEqual(["scripted_transfer needs method (script path and what it computes) and assumptions"], msgs)
        msgs = self.messages(lambda m: field_flow_push(m, **dict(scripted, assumptions=[])), "coupling_disclosure")
        self.assertEqual(["scripted_transfer needs method (script path and what it computes) and assumptions"], msgs)

    def test_scripted_method_path_checked(self):
        scripted = {"channel": "scripted_transfer", "approximation": "drag", "settings": None, "via": ["motor_x"],
                    "method": "src/couple.py: drag drives motor targets", "assumptions": ["Cd 1"]}
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            (case / "reports").mkdir(parents=True)
            (case / "reports" / "flip_probe.json").write_text("{}", encoding="utf-8")
            m = copy.deepcopy(LOBBY)
            field_flow_push(m, **scripted)
            (case / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            for present, code in ((False, 1), (True, 0)):
                with self.subTest(script_present=present):
                    if present:
                        (case / "src").mkdir()
                        (case / "src" / "couple.py").write_text("# transfer\n", encoding="utf-8")
                    proc = subprocess.run([sys.executable, str(AUDIT), str(case / "manifest.json")],
                                          capture_output=True, text=True)
                    self.assertEqual(code, proc.returncode, proc.stdout)
                    report = json.loads(proc.stdout)
                    if code:
                        self.assertEqual(["coupling_disclosure"], [e["check"] for e in report["errors"]])
                        self.assertIn("'src/couple.py' is not an existing file", report["errors"][0]["message"])
                    else:
                        self.assertEqual(["water_to_raft"], [a["edge"] for a in report["coupling"]["approximations"]])


class OrderTest(GraphCase):
    def test_bake_order_violation(self):
        msgs = self.messages(lambda m: graph(m).__setitem__("bake_order", ["slide_motor", "water", "rigid"]),
                             "bake_order")
        self.assertEqual(["water is baked before its driver rigid (obstacle reads the driver's baked output)"], msgs)

    def test_bake_order_incomplete_or_repeated(self):
        self.assertEqual(["bake_order must list every node once: missing slide_motor"], self.messages(
            lambda m: graph(m)["bake_order"].remove("slide_motor"), "bake_order"))
        self.assertEqual(["bake_order must list every node once: extra sea; repeated rigid"], self.messages(
            lambda m: graph(m)["bake_order"].extend(["rigid", "sea"]), "bake_order"))

    def test_cycle_is_reported(self):
        def mutate(m):
            mantaflow(m)
            graph(m)["edges"].append({"id": "water_to_rigid", "from": "water", "to": "rigid",
                                      "channel": "scripted_transfer", "objects": ["water_initial"],
                                      "approximation": "drag", "method": "src/couple.py: drag",
                                      "assumptions": ["Cd 1"], "omitted_reactions": ["x"]})
        msgs = self.messages(mutate, "bake_order")
        self.assertIn("rigid is baked before its driver water (scripted_transfer reads the driver's baked output)", msgs)
        self.assertTrue(any(t.startswith("cycle rigid -> water -> rigid") for t in msgs), msgs)

    def test_event_timing_not_ordered(self):
        def mutate(m):
            mantaflow(m)
            graph(m)["edges"].append({"id": "water_cue", "from": "water", "to": "rigid", "channel": "event_timing",
                                      "objects": ["water_initial"], "omitted_reactions": []})
        self.assertEqual(set(), self.checks(mutate))


class SupportTest(GraphCase):
    def test_cross_node_support_needs_edge(self):
        self.assertEqual(["tarp (cloth) rests on plastic_crate_orange (bullet_rigid_body) with no edge between their "
                          "nodes cloth and rigid"], self.messages(cloth_flag, "coupling_support"))

    def test_support_with_edge_passes(self):
        def mutate(m):
            cloth_flag(m)
            graph(m)["edges"].append({"id": "crate_to_tarp", "from": "rigid", "to": "cloth", "channel": "collision",
                                      "objects": ["plastic_crate_orange"],
                                      "omitted_reactions": ["tarp weight on the crate"]})
        self.assertEqual(set(), self.checks(mutate))


class ClaimTest(GraphCase):
    def test_claim_without_edge(self):
        msgs = self.messages(lambda m: beat(m, "settle").__setitem__(
            "interactions", [{"cause": "water", "effect": "suitcase_linen_canvas", "kind": "translate"}]), "coupling_claim")
        self.assertEqual(["no edge from water to rigid; Blender channels for flip_fluids -> bullet_rigid_body: none"], msgs)

    def test_claim_names_unknown_parts(self):
        self.assertEqual(["cause 'sea' is not a node"], self.messages(lambda m: beat(m, "splash").__setitem__(
            "interactions", [{"cause": "sea", "effect": "water_initial", "kind": "displace"}]), "coupling_claim"))
        self.assertEqual(["effect 'water_initial' is in no node other than water"], self.messages(
            lambda m: beat(m, "splash").__setitem__(
                "interactions", [{"cause": "water", "effect": "water_initial", "kind": "displace"}]), "coupling_claim"))

    def test_claim_respects_targets(self):
        msgs = self.messages(lambda m: beat(m, "slide")["interactions"][0].__setitem__("effect", "plastic_crate_orange"),
                             "coupling_claim")
        self.assertEqual(["no edge from slide_motor to rigid; Blender channels for keyed -> bullet_rigid_body: "
                          "kinematic_boundary"], msgs)

    def test_claim_kind_not_provided(self):
        def mutate(m):
            field_flow_push(m)
            beat(m, "settle")["interactions"].append({"cause": "water", "effect": "raft", "kind": "rotate"})
        self.assertEqual(["water_to_raft provide translate; rotate needs another edge or achievable: false with reason"],
                         self.messages(mutate, "coupling_claim"))

    def test_claim_marked_not_achievable(self):
        def mutate(m):
            field_flow_push(m)
            beat(m, "settle")["interactions"].append({"cause": "water", "effect": "raft", "kind": "rotate",
                                                      "achievable": False, "reason": "no channel gives torque; agreed"})
        self.assertEqual(set(), self.checks(mutate))
        m = copy.deepcopy(LOBBY)
        mutate(m)
        summary = spec_audit.coupling_summary(m)
        self.assertEqual([{"beat": "settle", "cause": "water", "effect": "raft", "kind": "rotate",
                           "reason": "no channel gives torque; agreed"}], summary["not_achievable"])
        self.assertEqual([{"beat": "settle", "cause": "water", "effect": "raft", "kind": "translate",
                           "edges": ["water_to_raft"]}], summary["approximated_claims"])

    def test_not_achievable_needs_reason(self):
        def mutate(m):
            field_flow_push(m)
            beat(m, "settle")["interactions"].append({"cause": "water", "effect": "raft", "kind": "rotate",
                                                      "achievable": False, "reason": " "})
        self.assertEqual(["achievable false needs a reason"], self.messages(mutate, "coupling_claim"))

    def test_not_achievable_but_provided(self):
        self.assertEqual(["rigid_to_water provides displace; remove achievable: false"], self.messages(
            lambda m: beat(m, "splash")["interactions"][0].update(achievable=False, reason="r"), "coupling_claim"))

    def test_scripted_claim_is_approximated(self):
        def mutate(m):
            field_flow_push(m, channel="scripted_transfer", method="src/couple.py: drag and torque from the grid",
                            assumptions=["Cd 1"], settings=None, via=["motor_rot"])
            beat(m, "settle")["interactions"].append({"cause": "water", "effect": "raft", "kind": "rotate"})
        self.assertEqual(set(), self.checks(mutate))
        m = copy.deepcopy(LOBBY)
        mutate(m)
        self.assertEqual(["translate", "rotate"],
                         [c["kind"] for c in spec_audit.coupling_summary(m)["approximated_claims"]])

    def test_native_claim_is_not_approximated(self):
        self.assertEqual([], spec_audit.coupling_summary(LOBBY)["approximated_claims"])


class FrozenGraphTest(unittest.TestCase):
    def test_graph_change_listed_by_gate(self):
        gate_spec = importlib.util.spec_from_file_location("gate", ROOT / "bin" / "gate.py")
        gate = importlib.util.module_from_spec(gate_spec)
        gate_spec.loader.exec_module(gate)
        rows = json.loads((ROOT / "tests" / "fixtures" / "requirements" / "valid_matrix.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            case = Path(tmp) / "case"
            case.mkdir()
            m = copy.deepcopy(LOBBY)
            m["requirements_matrix"] = [{"id": "story", "text": "t", "category": "physics", "pass_predicate": "p",
                                         "status": "unknown", "covers": ["establish", "slide", "splash", "settle"],
                                         "measures": rows["requirements_matrix"][0].get("measures") or
                                         [{"quantity": "q", "comparison": ">=", "value": 1}]}]
            (case / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(AUDIT), str(case / "manifest.json")], capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stdout)
            edge(m, "rigid_to_water")["omitted_reactions"] = ["buoyancy only"]
            (case / "manifest.json").write_text(json.dumps(m), encoding="utf-8")
            baseline = gate.evaluate(case)["spec_baseline"]
            self.assertTrue(baseline["changed"])
            self.assertEqual({"added": [], "removed": [], "changed": ["rigid_to_water"]},
                             baseline["diff"]["solver_graph_edges"])


if __name__ == "__main__":
    unittest.main()
