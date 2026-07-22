"""Conversational review spends real money — it must reach the ledger and the evidence.

Every reviewer Ask and Revise at a parked gate is a full executor call on the node's backend, and
messages are **unbounded**: request-changes decisions are capped at MAX_REVIEW_REVISIONS, but a
reviewer may send as many questions and revision instructions as they like while the gate waits.
Before this was wired, `responder` kept `result.text` and dropped `cost_usd`/`usage`, and the
ledger was charged before the review block ever ran — so that spend was invisible to the budget
ceiling and absent from the manifest.
"""

import json
import threading
import time

import pytest

from cadora.budget import BudgetPolicy
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.review import (
    REVIEW_APPROVE,
    file_review_fn,
    write_review_decision,
    write_review_message,
)
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class TalkativeExecutor(NodeExecutor):
    """Charges $1 to run a node and $5 for every review question or revision."""

    name = "fake"

    def __init__(self):
        self.node_calls: list[str] = []
        self.review_calls: list[str] = []

    def run(self, node, prompt, *, cwd, env=None):
        is_review = "[[cadora-review-" in prompt
        (self.review_calls if is_review else self.node_calls).append(node.id)
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text="answered" if is_review else f"out-{node.id}",
            usage={"input_tokens": 10, "output_tokens": 5},
            cost_usd=5.0 if is_review else 1.0,
        )


def _reviewed_topology():
    return Topology(
        name="t", nodes=[Node(id="a", role="builder", prompt="p", review=True)]
    )


def _reviewer(node_cwd, *, questions: int, then=REVIEW_APPROVE):
    """A reviewer that asks N questions at the parked gate, then decides."""

    def _await(name):
        for _ in range(1000):
            if (node_cwd / name).is_file():
                return True
            time.sleep(0.01)
        return False

    def drive():
        # Always wait for the gate to park first: review_fn clears stale decision/message files
        # when it opens, so anything written before that is deleted and the gate waits forever.
        assert _await("cadora-review-request.json"), "gate never parked"
        for i in range(questions):
            assert write_review_message(node_cwd, "question", f"why {i}?", "doc.md") == {
                "sent": "question"
            }
            assert _await("cadora-review-reply.json"), f"question {i} went unanswered"
            (node_cwd / "cadora-review-reply.json").unlink(missing_ok=True)
        write_review_decision(node_cwd, then, "looks good")

    return threading.Thread(target=drive, daemon=True)


def _run(tmp_path, executor, review_fn, **kwargs):
    return run_topology(
        _reviewed_topology(),
        executor,
        run_id="r",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=review_fn,
        **kwargs,
    )


def _manifest(tmp_path):
    return json.loads((tmp_path / "runs" / "r" / "manifest.json").read_text())


def test_review_questions_are_charged_to_the_archive(tmp_path):
    (tmp_path / "doc.md").write_text("# design\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(timeout=30, interval=0.02, executor=executor)

    reviewer = _reviewer(tmp_path, questions=3)
    reviewer.start()
    _run(tmp_path, executor, review_fn)
    reviewer.join(timeout=30)

    assert len(executor.review_calls) == 3  # the reviewer really did spend
    node = _manifest(tmp_path)["nodes"][0]
    # $1 to run the node + 3 × $5 of conversation.
    assert node["cost_usd"] == pytest.approx(16.0)
    assert node["review_conversation_cost_usd"] == pytest.approx(15.0)


def test_review_spend_trips_the_budget_ceiling(tmp_path):
    """The failure this closes: unbounded spend beneath a ceiling that could not see it."""
    (tmp_path / "doc.md").write_text("# design\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(timeout=30, interval=0.02, executor=executor)

    reviewer = _reviewer(tmp_path, questions=2)
    reviewer.start()
    _run(
        tmp_path,
        executor,
        review_fn,
        budget_policy=BudgetPolicy(budgets={"fake": 100.0}, action="warn"),
    )
    reviewer.join(timeout=30)

    # The ledger is internal to the run, so assert through what it writes: the archive is the
    # ledger's baseline on the next run, and it now carries the conversation.
    from cadora.budget import BudgetLedger, evaluate, load_baseline

    baseline = load_baseline(str(tmp_path / "runs"))
    assert baseline["fake"] == pytest.approx(11.0)  # $1 node + 2 × $5
    ledger = BudgetLedger(baseline=baseline)
    assert evaluate(ledger, BudgetPolicy(budgets={"fake": 11.0}), "fake").tripped


def test_a_silent_reviewer_costs_nothing_extra(tmp_path):
    """No conversation must mean no phantom charge, and no stray field in the evidence."""
    (tmp_path / "doc.md").write_text("# design\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(timeout=30, interval=0.02, executor=executor)

    reviewer = _reviewer(tmp_path, questions=0)
    reviewer.start()
    _run(tmp_path, executor, review_fn)
    reviewer.join(timeout=30)

    assert executor.review_calls == []
    node = _manifest(tmp_path)["nodes"][0]
    assert node["cost_usd"] == pytest.approx(1.0)
    assert "review_conversation_cost_usd" not in node


def test_a_review_surface_that_never_spends_is_unaffected(tmp_path):
    """stdin and MCP review functions have no executor and expose no counter — reading the spend
    off them must be a no-op, not an AttributeError."""
    from cadora.review import ReviewResult
    from cadora.runner import _review_spend

    def plain_review_fn(node, node_cwd, documents=None):
        return ReviewResult(REVIEW_APPROVE, "fine")

    assert _review_spend(plain_review_fn) == 0.0

    executor = TalkativeExecutor()
    _run(tmp_path, executor, plain_review_fn)
    assert _manifest(tmp_path)["nodes"][0]["cost_usd"] == pytest.approx(1.0)
