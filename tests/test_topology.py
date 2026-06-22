"""Topology scheduling tests — the one piece of real logic in the scaffold."""

import pytest

from cadora.topology import Node, Topology, topo_sort


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
