"""The shipped example topologies stay valid: every one loads and topo-sorts, and the shape
gallery is self-contained (each gate a node references is declared inline, so `cadora run <file>`
works with no run-level --gate-cmd)."""

from pathlib import Path

import pytest

from cadora.topology import load_topology, topo_sort

EXAMPLES = Path(__file__).parent.parent / "examples"
TOPOLOGIES = sorted(EXAMPLES.glob("*.topology.yaml"))
SHAPE_GALLERY = {"sequential-pipeline", "parallel-fanout", "fan-in-aggregation"}


def test_example_topologies_are_present():
    assert TOPOLOGIES, "no example topologies found under examples/"


@pytest.mark.parametrize("path", TOPOLOGIES, ids=lambda p: p.name)
def test_example_loads_and_topo_sorts(path):
    topo = load_topology(path)
    waves = topo_sort(topo)  # raises on a cycle / dangling dependency
    assert waves and all(wave for wave in waves)
    ids = {n.id for n in topo.nodes}
    for node in topo.nodes:
        assert set(node.depends_on) <= ids, f"{path.name}: {node.id} depends on an unknown node"


def test_shape_gallery_is_self_contained():
    by_name = {load_topology(p).name: load_topology(p) for p in TOPOLOGIES}
    missing = SHAPE_GALLERY - set(by_name)
    assert not missing, f"missing shape-gallery examples: {sorted(missing)}"
    for name in SHAPE_GALLERY:
        topo = by_name[name]
        referenced = {n.gate for n in topo.nodes if n.gate}
        assert referenced, f"{name} references no gates"
        undeclared = referenced - set(topo.gates)
        assert not undeclared, f"{name} references gates not declared inline: {sorted(undeclared)}"


def test_mission_prep_is_inception_only_with_parallel_lenses():
    topo = load_topology(EXAMPLES / "mission-prep.topology.yaml")
    referenced = {n.gate for n in topo.nodes if n.gate}
    # self-contained (gates declared inline) ...
    assert referenced and referenced <= set(topo.gates)
    # ... and every gate is an artifact check (setup: off) — never a code gate on a design phase
    assert all(topo.gates[g].setup == "off" for g in referenced)
    # the Senior PM and Senior DE assessments run in the SAME wave (in parallel)
    waves = topo_sort(topo)
    assert any({n.id for n in w} >= {"assess-pm", "assess-de"} for w in waves)


def test_agentcore_deploy_is_operations_only_with_a_real_security_gate(tmp_path):
    from cadora.gates import GATE_PASSED, ShellGate

    topo = load_topology(EXAMPLES / "agentcore-deploy.topology.yaml")
    referenced = {n.gate for n in topo.nodes if n.gate}
    assert referenced <= set(topo.gates)  # self-contained
    assert all(topo.gates[g].setup == "off" for g in referenced)  # artifact checks, no venv
    assert all(n.phase == "operations" for n in topo.nodes)  # the operations phase

    # The IAM gate is a real deterministic security check, not a rubber stamp.
    iam = ShellGate("iam-bounded", topo.gates["iam-bounded"].cmd, setup_mode="off")
    dep = tmp_path / "deploy"
    dep.mkdir()
    ok = '{"Statement":[{"Resource":"arn:aws:s3:::bucket/prefix"}],"trust":"aws:SourceAccount"}'
    (dep / "iam-policy.json").write_text(ok)
    assert iam.check(str(tmp_path)).status == GATE_PASSED  # bounded + guard + no static key
    (dep / "iam-policy.json").write_text('{"Statement":[{"Resource":"*"}],"trust":"aws:SourceAccount"}')
    assert iam.check(str(tmp_path)).status != GATE_PASSED  # a wildcard Resource fails the gate


def test_shape_gallery_covers_the_three_canonical_shapes():
    shapes = {load_topology(p).name: topo_sort(load_topology(p)) for p in TOPOLOGIES}
    # sequential: every wave is a single node
    assert all(len(w) == 1 for w in shapes["sequential-pipeline"])
    # fan-out -> synthesize: a middle wave runs several nodes in parallel, then a single join
    assert max(len(w) for w in shapes["parallel-fanout"]) >= 3
    assert len(shapes["parallel-fanout"][-1]) == 1
    # fan-in: several independent roots in the first wave, a single aggregator last
    assert len(shapes["fan-in-aggregation"][0]) >= 3
    assert len(shapes["fan-in-aggregation"][-1]) == 1
