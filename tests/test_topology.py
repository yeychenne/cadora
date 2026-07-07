"""Topology scheduling tests — the one piece of real logic in the scaffold."""

import pytest

from cadora.cli import _build_gates
from cadora.topology import GateSpec, Node, Topology, load_topology, topo_sort


def _topo(*nodes: Node) -> Topology:
    return Topology(name="t", nodes=list(nodes))


def test_waves_group_independent_nodes():
    t = _topo(
        Node(id="a"),
        Node(id="b"),
        Node(id="c", depends_on=["a", "b"]),
    )
    waves = topo_sort(t)
    assert [sorted(n.id for n in w) for w in waves] == [["a", "b"], ["c"]]


def test_linear_chain():
    t = _topo(
        Node(id="a"),
        Node(id="b", depends_on=["a"]),
        Node(id="c", depends_on=["b"]),
    )
    assert [[n.id for n in w] for w in topo_sort(t)] == [["a"], ["b"], ["c"]]


def test_cycle_raises():
    t = _topo(Node(id="a", depends_on=["b"]), Node(id="b", depends_on=["a"]))
    with pytest.raises(ValueError, match="cycle"):
        topo_sort(t)


def test_unknown_dependency_raises():
    t = _topo(Node(id="a", depends_on=["ghost"]))
    with pytest.raises(ValueError, match="unknown"):
        topo_sort(t)


def test_loads_explicit_review_flag(tmp_path):
    topology = tmp_path / "review.yaml"
    topology.write_text(
        "name: review\nnodes:\n  - id: requirements\n    review: true\n"
    )
    loaded = load_topology(topology)
    assert loaded.nodes[0].review is True


def test_node_phase_defaults_to_inception():
    n = Node(id="x")
    assert n.phase == "inception"


def test_invalid_phase_rejected():
    with pytest.raises(ValueError, match="invalid phase"):
        Node(id="x", phase="bogus")


def test_loads_phase_from_yaml(tmp_path):
    topology = tmp_path / "phased.yaml"
    topology.write_text(
        "name: phased\nnodes:\n"
        "  - id: req\n    phase: inception\n    review: true\n"
        "  - id: build\n    phase: construction\n    depends_on: [req]\n"
    )
    loaded = load_topology(topology)
    assert loaded.nodes[0].phase == "inception"
    assert loaded.nodes[1].phase == "construction"


def test_phased_topology_example_loads():
    from pathlib import Path
    example = Path(__file__).parent.parent / "examples" / "aidlc-phased.topology.yaml"
    if example.exists():
        loaded = load_topology(example)
        assert len(loaded.nodes) == 4
        phases = [n.phase for n in loaded.nodes]
        assert phases == ["inception", "inception", "construction", "construction"]


# --- per-gate-type commands -----------------------------------------------------


def test_load_topology_parses_per_gate_commands(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text(
        "name: demo\n"
        "gates:\n"
        '  build-test: "ruff check . && pytest -q"\n'
        "  artifact-check:\n"
        '    cmd: "test -f aidlc-docs/design.md"\n'
        "    setup: off\n"  # bare YAML `off` -> boolean; must coerce to the "off" mode
        "nodes:\n"
        "  - id: design\n    gate: artifact-check\n"
        "  - id: build\n    phase: construction\n    gate: build-test\n    depends_on: [design]\n"
    )
    t = load_topology(p)
    assert t.gates["build-test"].cmd == "ruff check . && pytest -q"
    assert t.gates["build-test"].setup is None  # falls back to the run-level --gate-setup
    assert t.gates["artifact-check"].cmd == "test -f aidlc-docs/design.md"
    assert t.gates["artifact-check"].setup == "off"


def test_topology_without_gates_map_is_empty(tmp_path):
    p = tmp_path / "t.yaml"
    p.write_text("name: d\nnodes:\n  - id: a\n")
    assert load_topology(p).gates == {}


def test_gate_spec_rejects_invalid_setup():
    with pytest.raises(ValueError, match="setup"):
        GateSpec(cmd="x", setup="sometimes")


def test_build_gates_uses_per_gate_override_and_falls_back():
    topo = Topology(
        name="t",
        nodes=[
            Node(id="design", gate="artifact-check"),
            Node(id="build", phase="construction", gate="build-test", depends_on=["design"]),
        ],
        gates={"artifact-check": GateSpec(cmd="test -f design.md", setup="off")},
    )
    gates = _build_gates(topo, default_cmd="ruff && pytest", default_setup="auto",
                         default_wheelhouse=None)
    # the inception gate runs its own cheap command with no venv setup…
    assert gates["artifact-check"].command == "test -f design.md"
    assert gates["artifact-check"].setup_mode == "off"
    # …while an un-overridden gate falls back to the run-level command/setup.
    assert gates["build-test"].command == "ruff && pytest"
    assert gates["build-test"].setup_mode == "auto"
