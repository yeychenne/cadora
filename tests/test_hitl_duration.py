"""W5 — a HITL node's duration_seconds must be agent WORK, not wall-clock that includes the human.

Phase 4 of the HITL campaign. Human deliberation time was flowing into duration_seconds and thus into
the *signed* evidence pack (a gate held open 20 min signed a 20-min node). The fix subtracts every
review-wait interval overlapping a node's span; review_wait_seconds records the deliberation
separately. This is the same honesty standard as the v0.10.1 concurrent-wave duration fix.
"""

import json
import time

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.review import REVIEW_APPROVE, REVIEW_REQUEST_CHANGES, ReviewResult
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class _FastExecutor(NodeExecutor):
    name = "fast"

    def run(self, node, prompt, *, cwd, env=None):
        return ExecutionResult(node_id=node.id, ok=True, exit_code=0, text="x", cost_usd=0.0)


class _SlowExecutor(NodeExecutor):
    name = "slow"

    def __init__(self, delay):
        self.delay = delay

    def run(self, node, prompt, *, cwd, env=None):
        time.sleep(self.delay)
        return ExecutionResult(node_id=node.id, ok=True, exit_code=0, text="x", cost_usd=0.0)


def _nodes(run_dir):
    return json.loads((run_dir / "status.json").read_text())["nodes"]


def test_hitl_duration_excludes_human_review_wait(tmp_path):
    """A slow human review must NOT inflate the node's work duration; it lands in review_wait_seconds."""
    wait = 0.4

    def slow_review(node, cwd, documents=None):
        time.sleep(wait)
        return ReviewResult(REVIEW_APPROVE)

    run_topology(
        Topology(name="d", nodes=[Node(id="req", prompt="R", review=True)]),
        _FastExecutor(), run_id="d1", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
        hitl=True, review_fn=slow_review,
    )
    node = _nodes(tmp_path / "runs" / "d1")["req"]
    assert node["review_wait_seconds"] >= wait * 0.8       # the deliberation was captured…
    assert node["duration_seconds"] < node["review_wait_seconds"]  # …and excluded from work time
    assert node["duration_seconds"] < wait * 0.5


def test_hitl_duration_excludes_wait_across_revisions(tmp_path):
    """request_changes reruns: every review wait accumulates, and all are excluded from duration."""
    wait = 0.2
    calls = []

    def slow_review(node, cwd, documents=None):
        time.sleep(wait)
        calls.append(1)
        return (ReviewResult(REVIEW_REQUEST_CHANGES, "again") if len(calls) < 3
                else ReviewResult(REVIEW_APPROVE))

    run_topology(
        Topology(name="d", nodes=[Node(id="req", prompt="R", review=True)]),
        _FastExecutor(), run_id="d2", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
        hitl=True, review_fn=slow_review,
    )
    node = _nodes(tmp_path / "runs" / "d2")["req"]
    assert len(calls) == 3
    assert node["review_wait_seconds"] >= 3 * wait * 0.8   # three waits accumulated
    assert node["duration_seconds"] < node["review_wait_seconds"]


def test_non_review_node_keeps_full_duration_and_zero_wait(tmp_path):
    """A node with no review gate is untouched: review_wait_seconds is 0 and duration is its work."""
    run_topology(
        Topology(name="d", nodes=[Node(id="n", prompt="P")]),
        _SlowExecutor(0.25), run_id="d3", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    )
    node = _nodes(tmp_path / "runs" / "d3")["n"]
    assert node["review_wait_seconds"] == 0.0
    assert node["duration_seconds"] >= 0.2                 # ≈ the executor's work, not reduced


def test_concurrent_wave_excludes_a_siblings_review_from_duration(tmp_path):
    """The concurrent-wave edge: node B's agent ran in parallel with A's, but B is recorded only
    after B's own review — which is after A's review (reviews are serialized). So B's span covers
    A's review too. The overlap correction must debit B for BOTH reviews, not just its own."""
    wait = 0.3

    def slow_review(node, cwd, documents=None):
        time.sleep(wait)
        return ReviewResult(REVIEW_APPROVE)

    run_topology(
        Topology(name="par", nodes=[
            Node(id="a", prompt="A", review=True),
            Node(id="b", prompt="B", review=True),
        ]),
        _FastExecutor(), run_id="d4", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
        hitl=True, review_fn=slow_review, max_parallel=2,
    )
    nodes = _nodes(tmp_path / "runs" / "d4")
    # both nodes did ~no work; without the sibling-overlap correction B's duration would be ≈ `wait`
    assert nodes["a"]["duration_seconds"] < wait * 0.6
    assert nodes["b"]["duration_seconds"] < wait * 0.6     # the key assertion (sibling wait excluded)
    assert nodes["a"]["review_wait_seconds"] >= wait * 0.8
    assert nodes["b"]["review_wait_seconds"] >= wait * 0.8
