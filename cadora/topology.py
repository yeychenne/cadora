"""Topology schema + loader + dependency-ordered scheduling.

A topology is a DAG of agent task-nodes. Each node carries its prompt, tool
allowlist, and upstream dependencies. ``topo_sort`` groups independent nodes
into "waves" (Kahn's algorithm) — nodes in the same wave have no dependency
between them and may run concurrently; waves run in order.

This is Cadora's differentiated layer; the agent runtime itself is delegated to
a NodeExecutor (see ``cadora.executors``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


VALID_PHASES = ("inception", "construction", "operations")


@dataclass
class Node:
    id: str
    role: str = ""
    phase: str = "inception"  # "inception" | "construction" | "operations"
    prompt: str = ""
    tools: list[str] = field(default_factory=list)  # per-node tool allowlist
    depends_on: list[str] = field(default_factory=list)
    model: str | None = None  # optional per-node model pin
    cwd: str | None = None  # working dir for this node (defaults to run cwd)
    gate: str | None = None  # name of a post-step gate to run after this node
    review: bool = False  # explicit human review point, activated by --hitl

    def __post_init__(self) -> None:
        if self.phase not in VALID_PHASES:
            raise ValueError(
                f"node {self.id!r}: invalid phase {self.phase!r}; expected one of {VALID_PHASES}"
            )


@dataclass
class Topology:
    name: str
    nodes: list[Node] = field(default_factory=list)

    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}


def load_topology(path: str | Path) -> Topology:
    import yaml  # lazy import keeps the core (schema + scheduler) dependency-free

    data = yaml.safe_load(Path(path).read_text())
    nodes = [Node(**n) for n in data.get("nodes", [])]
    return Topology(name=data.get("name", Path(path).stem), nodes=nodes)


def topo_sort(topology: Topology) -> list[list[Node]]:
    """Return dependency-ordered waves of nodes.

    Raises ``ValueError`` on an unknown dependency or a cycle.
    """
    by_id = topology.by_id()
    indeg = {n.id: 0 for n in topology.nodes}
    children: dict[str, list[str]] = {n.id: [] for n in topology.nodes}
    for n in topology.nodes:
        for dep in n.depends_on:
            if dep not in by_id:
                raise ValueError(f"node {n.id!r} depends on unknown node {dep!r}")
            indeg[n.id] += 1
            children[dep].append(n.id)

    ready = sorted(nid for nid, deg in indeg.items() if deg == 0)
    waves: list[list[Node]] = []
    scheduled = 0
    while ready:
        waves.append([by_id[nid] for nid in ready])
        scheduled += len(ready)
        nxt: list[str] = []
        for nid in ready:
            for child in children[nid]:
                indeg[child] -= 1
                if indeg[child] == 0:
                    nxt.append(child)
        ready = sorted(nxt)

    if scheduled != len(topology.nodes):
        raise ValueError("topology contains a cycle")
    return waves
