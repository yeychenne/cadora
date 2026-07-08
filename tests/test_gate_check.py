"""`cadora gate-check` — run a topology's gates against a workspace, no executor."""

import argparse

from cadora.cli import _build_gates, cmd_gate_check, run_gate_check
from cadora.topology import GateSpec, Node, Topology


def test_run_gate_check_reports_per_node_pass_and_fail(tmp_path):
    (tmp_path / "design.md").write_text("ok")
    topo = Topology(
        name="t",
        nodes=[
            Node(id="design", gate="artifact-check"),
            Node(id="build", phase="construction", gate="build-test", depends_on=["design"]),
        ],
        gates={
            "artifact-check": GateSpec(cmd="test -f design.md", setup="off"),
            "build-test": GateSpec(cmd="false", setup="off"),  # deliberately fails
        },
    )
    gates = _build_gates(topo, default_cmd="true", default_setup="off", default_wheelhouse=None)
    results = run_gate_check(topo, str(tmp_path), gates)
    by_id = {nid: result for nid, _gate, result in results}
    assert by_id["design"].passed is True
    assert by_id["build"].passed is False


def test_run_gate_check_caches_shared_gate(tmp_path):
    # two nodes share the same gate + cwd -> the gate runs once, both get the result.
    topo = Topology(
        name="t",
        nodes=[Node(id="a", gate="g"), Node(id="b", gate="g", depends_on=["a"])],
        gates={"g": GateSpec(cmd="true", setup="off")},
    )
    gates = _build_gates(topo, "true", "off", None)
    results = run_gate_check(topo, str(tmp_path), gates)
    assert [nid for nid, _g, _r in results] == ["a", "b"]
    assert results[0][2] is results[1][2]  # same cached GateResult object


def _args(topology, cwd):
    return argparse.Namespace(
        topology=str(topology), cwd=str(cwd),
        gate_cmd="true", gate_setup="off", gate_wheelhouse=None,
    )


def test_cmd_gate_check_exit_zero_on_pass(tmp_path):
    (tmp_path / "d.md").write_text("x")
    topo = tmp_path / "t.yaml"
    topo.write_text(
        'name: t\ngates:\n  artifact-check: {cmd: "test -f d.md", setup: off}\n'
        "nodes:\n  - id: design\n    gate: artifact-check\n"
    )
    assert cmd_gate_check(_args(topo, tmp_path)) == 0


def test_cmd_gate_check_exit_one_on_fail(tmp_path):
    topo = tmp_path / "t.yaml"
    topo.write_text(
        'name: t\ngates:\n  build-test: {cmd: "false", setup: off}\n'
        "nodes:\n  - id: build\n    phase: construction\n    gate: build-test\n"
    )
    assert cmd_gate_check(_args(topo, tmp_path)) == 1


def test_cmd_gate_check_no_gates_is_ok(tmp_path):
    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n")
    assert cmd_gate_check(_args(topo, tmp_path)) == 0
