"""B4 tests — phase-aware construction routing.

Proves: construction nodes route to a dedicated executor (e.g. kiro),
inception nodes stay on the default executor — no ACP hand-off, just
plain backend routing through Cadora's central orchestrator.
"""

import json
from pathlib import Path

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.review import REVIEW_APPROVE, ReviewResult
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class InceptionExecutor(NodeExecutor):
    """Executor that handles inception (document) nodes."""
    name = "inception-exec"
    called_nodes: list[str] = []

    def __init__(self):
        self.called_nodes = []

    def run(self, node, prompt, *, cwd, env=None):
        self.called_nodes.append(node.id)
        docs = Path(cwd) / "aidlc-docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / f"{node.id}.md").write_text(f"# {node.id}\n")
        return ExecutionResult(node_id=node.id, ok=True, exit_code=0, text=f"inception:{node.id}")


class ConstructionExecutor(NodeExecutor):
    """Executor that handles construction (code) nodes — simulates Kiro."""
    name = "construction-exec"
    called_nodes: list[str] = []

    def __init__(self):
        self.called_nodes = []

    def run(self, node, prompt, *, cwd, env=None):
        self.called_nodes.append(node.id)
        return ExecutionResult(node_id=node.id, ok=True, exit_code=0, text=f"construction:{node.id}")


def _phased_topology() -> Topology:
    return Topology(name="phased", nodes=[
        Node(id="requirements", phase="inception", prompt="Req.", review=True),
        Node(id="design", phase="inception", prompt="Design.", depends_on=["requirements"], review=True),
        Node(id="codegen", phase="construction", prompt="Code.", depends_on=["design"]),
        Node(id="build", phase="construction", prompt="Build.", depends_on=["codegen"]),
    ])


def _auto_approve(node, cwd):
    return ReviewResult(REVIEW_APPROVE, "ok")


def test_construction_nodes_route_to_construction_executor(tmp_path):
    """construction_executor handles phase=construction nodes; default handles inception."""
    inception = InceptionExecutor()
    construction = ConstructionExecutor()

    run_topology(
        _phased_topology(),
        inception,
        construction_executor=construction,
        run_id="b4-route",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=_auto_approve,
    )

    # Inception nodes went to inception executor
    assert inception.called_nodes == ["requirements", "design"]
    # Construction nodes went to construction executor
    assert construction.called_nodes == ["codegen", "build"]

    # Archive captures all nodes
    manifest = json.loads((tmp_path / "runs" / "b4-route" / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert len(manifest["nodes"]) == 4


def test_no_construction_executor_uses_default_for_all(tmp_path):
    """Without construction_executor, all nodes use the default (backward compat)."""
    default = InceptionExecutor()

    run_topology(
        _phased_topology(),
        default,
        run_id="b4-default",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=_auto_approve,
    )

    assert default.called_nodes == ["requirements", "design", "codegen", "build"]
