"""HITL Phase-1 coverage — file-path robustness, gate/review ordering, telemetry, concurrency.

Phase 1 of the HITL test campaign (strategy/hitl-test-campaign-2026-07-15.md), the non-MCP half:
these pin behaviours that were correct-but-unverified, so a future refactor can't silently regress
them. None of them needed a code change — they characterize guarantees the runner already makes.

  T1.6  file reviewer survives a corrupt/partial decision file, and fails closed on a permanent one
  T1.7  the deterministic gate runs BEFORE the human review gate (a red gate is never reviewed)
  T1.8  the telemetry event stream records the review lifecycle for abort and revision-limit exits
  T1.9  concurrent-wave review gates are presented strictly one at a time (serialization guarantee)
"""

import json
import threading
import time

import pytest

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import ShellGate
from cadora.review import (
    DECISION_FILE,
    REQUEST_FILE,
    REVIEW_ABORT,
    REVIEW_APPROVE,
    REVIEW_REQUEST_CHANGES,
    ReviewResult,
    file_review_fn,
)
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class _OkExecutor(NodeExecutor):
    name = "fake"

    def run(self, node, prompt, *, cwd, env=None):
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}", cost_usd=0.0,
        )


class _SleepExecutor(NodeExecutor):
    """Holds the agent step open briefly so a wave's initial executions genuinely overlap."""

    name = "sleep"

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def run(self, node, prompt, *, cwd, env=None):
        time.sleep(self.delay)
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}", cost_usd=0.0,
        )


def _event_types(run_dir) -> list[str]:
    lines = (run_dir / "run-events.jsonl").read_text().splitlines()
    return [json.loads(line)["type"] for line in lines if line.strip()]


# ---- T1.6  file-path decision robustness -------------------------------------------------------


def test_file_review_survives_a_corrupt_partial_decision_then_reads_the_valid_one(tmp_path):
    """W6: a decision file read mid-write (unparseable) must not crash the reviewer — it keeps
    polling and returns the decision once the file is complete."""

    def respond():
        for _ in range(500):
            if (tmp_path / REQUEST_FILE).is_file():
                (tmp_path / DECISION_FILE).write_text('{"decision": "appr')  # truncated mid-write
                time.sleep(0.08)  # reviewer reads the corrupt file across several polls
                (tmp_path / DECISION_FILE).write_text(
                    json.dumps({"decision": "approve", "comments": "ok"})
                )
                return
            time.sleep(0.01)

    t = threading.Thread(target=respond)
    t.start()
    result = file_review_fn(timeout=5, interval=0.02)(Node(id="n", review=True), str(tmp_path))
    t.join(timeout=3)
    assert result.decision == REVIEW_APPROVE
    assert result.comments == "ok"


def test_file_review_times_out_to_abort_on_permanently_malformed_json(tmp_path):
    """W6: a decision file that never becomes valid JSON must fail closed (abort on timeout) rather
    than hang forever or raise."""

    def respond():
        for _ in range(500):
            if (tmp_path / REQUEST_FILE).is_file():
                (tmp_path / DECISION_FILE).write_text("}{ not json at all")
                return
            time.sleep(0.01)

    t = threading.Thread(target=respond)
    t.start()
    result = file_review_fn(timeout=0.3, interval=0.03)(Node(id="n", review=True), str(tmp_path))
    t.join(timeout=2)
    assert result.decision == REVIEW_ABORT
    assert "timed out" in result.comments
    assert not (tmp_path / REQUEST_FILE).exists()  # cleaned up on timeout


# ---- T1.7  gate runs before review -------------------------------------------------------------


def test_red_gate_fails_before_the_review_gate_is_ever_reached(tmp_path):
    """W10: the deterministic gate runs before the human review gate. A red gate must stop the run
    (fail-closed) WITHOUT ever surfacing a review — a human can't be asked to approve work the gate
    already rejected."""
    reviewed: list[str] = []

    def review_fn(node, cwd, documents=None):
        reviewed.append(node.id)
        return ReviewResult(REVIEW_APPROVE)

    topo = Topology(name="g", nodes=[Node(id="build", prompt="B", gate="build-test", review=True)])
    with pytest.raises(SystemExit):
        run_topology(
            topo, _OkExecutor(), run_id="redgate", cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"), hitl=True, review_fn=review_fn,
            gates={"build-test": ShellGate("build-test", "exit 1", setup_mode="off")},
        )
    assert reviewed == []  # review never reached — the gate stopped the run first
    manifest = json.loads((tmp_path / "runs" / "redgate" / "manifest.json").read_text())
    assert manifest["ok"] is False


# ---- T1.8  telemetry event stream --------------------------------------------------------------


def test_telemetry_records_review_waiting_then_aborted_for_an_abort(tmp_path):
    """W10: an aborted HITL run emits review_waiting -> review_aborted, in that order."""
    topo = Topology(name="a", nodes=[Node(id="req", prompt="R", review=True)])
    with pytest.raises(SystemExit):
        run_topology(
            topo, _OkExecutor(), run_id="abt", cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"), hitl=True,
            review_fn=lambda n, c, d=None: ReviewResult(REVIEW_ABORT, "stop"),
        )
    types = _event_types(tmp_path / "runs" / "abt")
    assert "review_waiting" in types and "review_aborted" in types
    assert types.index("review_waiting") < types.index("review_aborted")


def test_telemetry_records_requested_changes_up_to_the_revision_limit(tmp_path):
    """W10: hitting the revision limit emits review_waiting + review_requested_changes exactly
    MAX_REVIEW_REVISIONS (3) times before the run fails closed."""
    topo = Topology(name="r", nodes=[Node(id="req", prompt="R", review=True)])
    with pytest.raises(SystemExit):
        run_topology(
            topo, _OkExecutor(), run_id="rl", cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"), hitl=True,
            review_fn=lambda n, c, d=None: ReviewResult(REVIEW_REQUEST_CHANGES, "again"),
        )
    types = _event_types(tmp_path / "runs" / "rl")
    assert types.count("review_requested_changes") == 3
    assert types.count("review_waiting") == 3


# ---- T1.9  concurrent-wave review serialization ------------------------------------------------


def test_concurrent_wave_serializes_review_gates(tmp_path):
    """W8: with two review:true nodes whose agents run concurrently (max_parallel=2), the review
    gates are still presented strictly one at a time. The runner hoists review out of the parallel
    section, so a single-slot review channel/front-end is never asked to hold two gates at once —
    this test fails loudly if a refactor ever moves review into the concurrent workers."""
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}
    reviewed: list[str] = []

    def review_fn(node, cwd, documents=None):
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.05)  # hold the gate 'open' so any overlap would be observed as peak == 2
        with lock:
            state["active"] -= 1
            reviewed.append(node.id)
        return ReviewResult(REVIEW_APPROVE)

    topo = Topology(
        name="par",
        nodes=[Node(id="a", prompt="A", review=True), Node(id="b", prompt="B", review=True)],
    )
    out = run_topology(
        topo, _SleepExecutor(0.05), run_id="parser", cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"), hitl=True, review_fn=review_fn, max_parallel=2,
    )
    assert sorted(reviewed) == ["a", "b"]  # both nodes reviewed
    assert state["peak"] == 1  # never two gates open at once — reviews are serialized
    assert json.loads((out / "manifest.json").read_text())["ok"] is True
