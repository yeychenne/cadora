"""Budget depth: the ceiling holds DURING a parked conversation, and no kill loses cost data.

Three behaviours, each answering a gap that survived the first review-spend fix:

1. **Mid-park enforcement** — the ledger was only consulted at node boundaries, so an unbounded
   Ask/Revise session inside one gate could sail past the ceiling and be noticed a node too late.
   Now the review surface consults a guard before every executor call and relays a refusal
   instead of spending; the decision itself stays free, so the gate is never bricked.
2. **Crash durability** — conversation spend lived only in memory until the node recorded, and
   the manifest itself was only written at finalize. A SIGKILL while parked lost both. Now every
   turn is journaled to the archive the moment it completes, the manifest is flushed after every
   node, and a resumed run recovers and charges the journaled turns.
3. **Reporting** — `review_cost_usd` at run level in the manifest and `cadora usage`, answering
   "what did human review cost?" — which nothing reported before.
"""

import json
import threading
import time

import pytest

from cadora.budget import BudgetLedger, BudgetPolicy, evaluate, load_baseline
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.review import (
    REVIEW_APPROVE,
    append_review_turn,
    file_review_fn,
    read_pending_review_spend,
    write_review_decision,
    write_review_message,
)
from cadora.runner import run_topology
from cadora.topology import Node, Topology
from cadora.usage import summarize_usage


class TalkativeExecutor(NodeExecutor):
    """$1 to run a node, $5 per review turn."""

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


def _reviewer(node_cwd, *, questions: int, replies: list | None = None):
    """Ask N questions once the gate parks, collecting replies, then approve."""

    def _await(name):
        for _ in range(1500):
            if (node_cwd / name).is_file():
                return True
            time.sleep(0.01)
        return False

    def drive():
        assert _await("cadora-review-request.json"), "gate never parked"
        for i in range(questions):
            assert write_review_message(node_cwd, "question", f"why {i}?", "doc.md") == {
                "sent": "question"
            }
            assert _await("cadora-review-reply.json"), f"question {i} unanswered"
            if replies is not None:
                replies.append(
                    json.loads((node_cwd / "cadora-review-reply.json").read_text())["reply"]
                )
            (node_cwd / "cadora-review-reply.json").unlink(missing_ok=True)
        write_review_decision(node_cwd, REVIEW_APPROVE, "ok")

    return threading.Thread(target=drive, daemon=True)


def _run(tmp_path, executor, review_fn, **kwargs):
    return run_topology(
        Topology(name="t", nodes=[Node(id="a", role="builder", prompt="p", review=True)]),
        executor,
        run_id="r",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=review_fn,
        **kwargs,
    )


def _journal(tmp_path):
    return tmp_path / "runs" / "r" / "review-spend.jsonl"


def _manifest(tmp_path):
    return json.loads((tmp_path / "runs" / "r" / "manifest.json").read_text())


# --- 1. the ceiling holds DURING the conversation ---------------------------------------------


def test_over_ceiling_ask_is_refused_and_the_gate_stays_decidable(tmp_path):
    (tmp_path / "doc.md").write_text("# d\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(timeout=30, interval=0.02, executor=executor)
    replies: list = []
    reviewer = _reviewer(tmp_path, questions=3, replies=replies)
    reviewer.start()
    # Node costs $1; first ask (+$5) is under the $10/90% threshold, so it runs; from then on
    # spend ($6) has crossed $9×… wait — threshold is 0.9×10 = $9: after ask1 spend is $6 < $9,
    # ask2 brings the CHECK to $6 → still under, runs (+$5 = $11); ask3's check sees $11 ≥ $9 →
    # refused. The arithmetic is the test: the guard sees each turn BEFORE it spends.
    _run(
        tmp_path,
        executor,
        review_fn,
        budget_policy=BudgetPolicy(budgets={"fake": 10.0}, action="stop"),
    )
    reviewer.join(timeout=30)

    assert len(executor.review_calls) == 2  # third ask never reached the executor
    assert len(replies) == 3  # but the reviewer got three REPLIES — the last is the refusal
    assert "budget ceiling" in replies[2]
    assert "NOT executed" in replies[2]
    # The decision still landed and the node recorded the true spend: $1 + 2×$5.
    node = _manifest(tmp_path)["nodes"][0]
    assert node["cost_usd"] == pytest.approx(11.0)
    assert node["review_conversation_cost_usd"] == pytest.approx(10.0)


def test_warn_policy_never_refuses(tmp_path):
    """warn's contract is 'never change what a run does' — that includes the conversation."""
    (tmp_path / "doc.md").write_text("# d\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(timeout=30, interval=0.02, executor=executor)
    reviewer = _reviewer(tmp_path, questions=3)
    reviewer.start()
    _run(
        tmp_path,
        executor,
        review_fn,
        budget_policy=BudgetPolicy(budgets={"fake": 2.0}, action="warn"),  # hopelessly over
    )
    reviewer.join(timeout=30)
    assert len(executor.review_calls) == 3  # every ask executed


# --- 2. crash durability ------------------------------------------------------------------------


def test_every_turn_is_journaled_the_moment_it_completes(tmp_path):
    """The journal is what a SIGKILL cannot take: assert it exists BETWEEN turns, not after."""
    (tmp_path / "doc.md").write_text("# d\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(
        timeout=30, interval=0.02, executor=executor, spend_journal=_journal(tmp_path)
    )
    observed: list = []

    def _await(name):
        for _ in range(1500):
            if (tmp_path / name).is_file():
                return True
            time.sleep(0.01)
        return False

    def drive():
        assert _await("cadora-review-request.json")
        write_review_message(tmp_path, "question", "why?", "doc.md")
        assert _await("cadora-review-reply.json")
        # The moment the reply exists, the turn must already be on disk — this is the instant a
        # kill would strike.
        observed.append(read_pending_review_spend(_journal(tmp_path), "a")["cost_usd"])
        write_review_decision(tmp_path, REVIEW_APPROVE, "ok")

    thread = threading.Thread(target=drive, daemon=True)
    thread.start()
    _run(tmp_path, executor, review_fn)
    thread.join(timeout=30)

    assert observed == [pytest.approx(5.0)]
    # After the node records, the journal is committed and cleared — no double-charge ever.
    assert read_pending_review_spend(_journal(tmp_path), "a")["cost_usd"] == 0.0


def test_a_killed_parks_journal_is_recovered_charged_and_cleared(tmp_path):
    """Simulate the kill: turns in the journal, nothing in the manifest. The next invocation
    must charge them to the node, the baseline, and the ceiling — then clear the journal."""
    journal = _journal(tmp_path)
    append_review_turn(journal, node_id="a", cost_usd=5.0, usage={"input_tokens": 10})
    append_review_turn(journal, node_id="a", cost_usd=5.0, usage={"input_tokens": 10})
    append_review_turn(journal, node_id="other-node", cost_usd=99.0, usage={})  # not ours

    (tmp_path / "doc.md").write_text("# d\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(timeout=30, interval=0.02, executor=executor, spend_journal=journal)
    reviewer = _reviewer(tmp_path, questions=0)  # silent this time — only the carried spend
    reviewer.start()
    _run(tmp_path, executor, review_fn)
    reviewer.join(timeout=30)

    node = _manifest(tmp_path)["nodes"][0]
    assert node["cost_usd"] == pytest.approx(11.0)  # $1 exec + $10 recovered conversation
    assert node["review_conversation_cost_usd"] == pytest.approx(10.0)
    # Recovered money reaches the budget baseline like any other spend.
    assert load_baseline(str(tmp_path / "runs"))["fake"] == pytest.approx(11.0)
    # Our turns cleared; the other node's pending turns are untouched.
    assert read_pending_review_spend(journal, "a")["cost_usd"] == 0.0
    assert read_pending_review_spend(journal, "other-node")["cost_usd"] == pytest.approx(99.0)


def test_the_manifest_is_on_disk_after_every_node_not_only_at_finalize(tmp_path):
    """A SIGKILL between nodes must not erase completed nodes from the accounting chain."""

    class Prober(NodeExecutor):
        name = "fake"

        def __init__(self):
            self.seen_mid_run: dict | None = None

        def run(self, node, prompt, *, cwd, env=None):
            if node.id == "b":
                # Node a recorded; the run is still in flight. What is on disk RIGHT NOW is what
                # a kill here would leave behind.
                self.seen_mid_run = json.loads(
                    (tmp_path / "runs" / "r2" / "manifest.json").read_text()
                )
            return ExecutionResult(
                node_id=node.id, ok=True, exit_code=0, text="x",
                usage={"input_tokens": 1, "output_tokens": 1}, cost_usd=2.0,
            )

    executor = Prober()
    run_topology(
        Topology(
            name="t",
            nodes=[
                Node(id="a", role="builder", prompt="p"),
                Node(id="b", role="builder", prompt="p", depends_on=["a"]),
            ],
        ),
        executor,
        run_id="r2",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
    )

    assert executor.seen_mid_run is not None
    assert executor.seen_mid_run["ok"] is None  # honestly marked in flight
    mid = {n["node_id"]: n for n in executor.seen_mid_run["nodes"]}
    assert mid["a"]["cost_usd"] == pytest.approx(2.0)  # a's money was already safe
    assert "b" not in mid
    final = json.loads((tmp_path / "runs" / "r2" / "manifest.json").read_text())
    assert final["ok"] is True  # finalize still lands


# --- 3. run-level reporting ----------------------------------------------------------------------


def test_run_level_review_cost_in_manifest_and_usage(tmp_path):
    (tmp_path / "doc.md").write_text("# d\n")
    executor = TalkativeExecutor()
    review_fn = file_review_fn(
        timeout=30, interval=0.02, executor=executor, spend_journal=_journal(tmp_path)
    )
    reviewer = _reviewer(tmp_path, questions=2)
    reviewer.start()
    _run(tmp_path, executor, review_fn)
    reviewer.join(timeout=30)

    manifest = _manifest(tmp_path)
    assert manifest["review_cost_usd"] == pytest.approx(10.0)
    summary = summarize_usage(str(tmp_path / "runs"))
    assert summary.review_cost_usd == pytest.approx(10.0)
    assert summary.cost_usd == pytest.approx(11.0)  # review included, not double-counted


def test_no_review_no_rollup_field(tmp_path):
    """A non-HITL run must not carry review_cost_usd: 0.0 — absence is the honest value."""

    class Plain(NodeExecutor):
        name = "fake"

        def run(self, node, prompt, *, cwd, env=None):
            return ExecutionResult(node_id=node.id, ok=True, exit_code=0, text="x", cost_usd=1.0)

    run_topology(
        Topology(name="t", nodes=[Node(id="a", role="builder", prompt="p")]),
        Plain(),
        run_id="r3",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
    )
    manifest = json.loads((tmp_path / "runs" / "r3" / "manifest.json").read_text())
    assert "review_cost_usd" not in manifest


# --- the ledger view the guard uses --------------------------------------------------------------


def test_guard_arithmetic_matches_the_boundary_check(tmp_path):
    """The mid-park guard and the node-boundary check must agree on what 'over' means."""
    ledger = BudgetLedger(baseline={"fake": 8.0})
    policy = BudgetPolicy(budgets={"fake": 10.0}, action="stop")
    from cadora.runner import _make_review_guard

    guard = _make_review_guard(ledger, policy, "fake", carried=0.0)
    assert guard(0.5) is None  # 8.5 < 9.0 threshold
    refusal = guard(1.0)  # 9.0 ≥ 9.0
    assert refusal and "budget ceiling" in refusal
    # And the boundary evaluator agrees at the same numbers.
    ledger.record("fake", 1.0)
    assert evaluate(ledger, policy, "fake").tripped
