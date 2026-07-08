"""Run-input capture — the entry prompt(s) + vision land in the archive for the dashboard."""

import json

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class _Fixture(NodeExecutor):
    name = "fixture"

    def run(self, node, prompt, *, cwd, env=None):
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text="ok",
            meta={"funding_resolved": "subscription"},
        )


def test_run_input_captures_root_prompts_and_vision(tmp_path):
    (tmp_path / "vision.md").write_text("Build a thing")
    topo = Topology(
        name="demo",
        nodes=[
            Node(id="requirements", prompt="Gather requirements"),
            Node(id="build", phase="construction", prompt="Build it", depends_on=["requirements"]),
        ],
    )
    run_topology(topo, _Fixture(), run_id="ri", cwd=str(tmp_path),
                 archive_root=str(tmp_path / "runs"))
    data = json.loads((tmp_path / "runs" / "ri" / "run-input.json").read_text())
    assert data["topology"] == "demo"
    assert data["vision"] == "Build a thing"
    # only the root node (no depends_on) is an entry point
    assert [root["node_id"] for root in data["roots"]] == ["requirements"]
    assert data["roots"][0]["prompt"] == "Gather requirements"


def test_run_input_without_vision(tmp_path):
    topo = Topology(name="t", nodes=[Node(id="a", prompt="do a")])
    run_topology(topo, _Fixture(), run_id="nov", cwd=str(tmp_path),
                 archive_root=str(tmp_path / "runs"))
    data = json.loads((tmp_path / "runs" / "nov" / "run-input.json").read_text())
    assert data["vision"] is None
    assert data["roots"][0]["prompt"] == "do a"
