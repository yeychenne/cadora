"""Run resumption: `--resume-from` / `--skip` trust workspace artifacts and skip upstream nodes."""

import json

import pytest

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.runner import _compute_skip_set, run_topology
from cadora.topology import Node, Topology


class RecordingExecutor(NodeExecutor):
    """Records which nodes actually executed (and the prompt each one received)."""

    name = "rec"

    def __init__(self):
        self.ran: list[str] = []
        self.prompts: dict[str, str] = {}

    def run(self, node, prompt, *, cwd, env=None):
        self.ran.append(node.id)
        self.prompts[node.id] = prompt
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"out-{node.id}",
            cost_usd=0.0,
            meta={"funding_resolved": "subscription"},
        )


def _linear() -> Topology:
    return Topology(
        name="lin",
        nodes=[
            Node(id="a", prompt="A"),
            Node(id="b", prompt="B", depends_on=["a"]),
            Node(id="c", prompt="C", depends_on=["b"]),
            Node(id="d", prompt="D", depends_on=["c"]),
        ],
    )


# --- _compute_skip_set unit tests -------------------------------------------------------------


def test_resume_from_skips_upstream_only():
    skip_ids, ordered = _compute_skip_set(_linear(), "c", None)
    assert skip_ids == {"a", "b"}  # c and d run
    assert ordered == ["a", "b"]


def test_resume_from_root_skips_nothing():
    skip_ids, _ = _compute_skip_set(_linear(), "a", None)
    assert skip_ids == set()


def test_explicit_skip_names_those_nodes():
    skip_ids, _ = _compute_skip_set(_linear(), None, ["b", "a"])
    assert skip_ids == {"a", "b"}


def test_resume_from_skips_upstream_and_unrelated_branches():
    # a -> b -> c ; d is independent. Resuming from b runs {b, c}; skips {a, d}.
    topo = Topology(
        name="dag",
        nodes=[
            Node(id="a", prompt="A"),
            Node(id="b", prompt="B", depends_on=["a"]),
            Node(id="c", prompt="C", depends_on=["b"]),
            Node(id="d", prompt="D"),
        ],
    )
    skip_ids, _ = _compute_skip_set(topo, "b", None)
    assert skip_ids == {"a", "d"}


def test_unknown_node_name_fails_fast():
    with pytest.raises(SystemExit):
        _compute_skip_set(_linear(), "nope", None)
    with pytest.raises(SystemExit):
        _compute_skip_set(_linear(), None, ["ghost"])


# --- end-to-end run behavior ------------------------------------------------------------------


def test_resume_from_runs_target_and_downstream_records_skips(tmp_path):
    ex = RecordingExecutor()
    run_topology(
        _linear(), ex, run_id="r", cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"), resume_from="c",
    )
    assert ex.ran == ["c", "d"]  # a and b were skipped, not executed

    status = json.loads((tmp_path / "runs" / "r" / "status.json").read_text())
    assert status["resumed_from"] == "c"
    assert status["skipped_nodes"] == ["a", "b"]
    assert status["nodes"]["a"]["status"] == "skipped"
    assert status["nodes"]["b"]["status"] == "skipped"
    assert status["nodes"]["c"]["status"] == "completed"
    assert status["nodes"]["d"]["status"] == "completed"


def test_skipped_dependency_is_pointed_at_workspace_not_empty_output(tmp_path):
    ex = RecordingExecutor()
    topo = Topology(
        name="lin",
        nodes=[Node(id="a", prompt="A"), Node(id="b", prompt="B", depends_on=["a"])],
    )
    run_topology(
        topo, ex, run_id="r", cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"), skip=["a"],
    )
    assert ex.ran == ["b"]
    # b's prompt should point at a's workspace artifacts, not inject an empty piped output section
    assert "resumed" in ex.prompts["b"].lower()
    assert "Output of upstream node `a`" not in ex.prompts["b"]

    status = json.loads((tmp_path / "runs" / "r" / "status.json").read_text())
    assert status["resumed_from"] is None
    assert status["skipped_nodes"] == ["a"]
    assert status["nodes"]["a"]["status"] == "skipped"
    assert status["nodes"]["a"]["skipped_reason"] == "explicitly skipped"
