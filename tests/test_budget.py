"""Budget enforcement at node boundaries — warn, stop, or move the run.

The behaviour these lock down comes from two real failures: quota exhaustion killed a node
mid-flight (2026-07-17, $5.87 lost), and no CLI exposes remaining quota, so the threshold can
only be a declared budget measured against Cadora's own recorded spend.
"""

import json

import pytest

from cadora.budget import (
    BudgetLedger,
    BudgetPolicy,
    evaluate,
    load_baseline,
    parse_budgets,
)
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class SpendingExecutor(NodeExecutor):
    """Charges a fixed amount per node so a budget can be crossed deterministically."""

    def __init__(self, name: str, cost: float = 1.0):
        self.name = name
        self.cost = cost
        self.calls: list[str] = []

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append(node.id)
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"out-{node.id}",
            usage={"input_tokens": 10, "output_tokens": 5},
            cost_usd=self.cost,
        )


def _chain(*ids):
    nodes = []
    for i, node_id in enumerate(ids):
        nodes.append(
            Node(
                id=node_id,
                role="builder",
                prompt="do the thing",
                depends_on=[ids[i - 1]] if i else [],
            )
        )
    return Topology(name="t", nodes=nodes)


def _run(tmp_path, executor, **kwargs):
    return run_topology(
        _chain("a", "b", "c"),
        executor,
        run_id="r1",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        **kwargs,
    )


# --- policy + ledger arithmetic ---------------------------------------------------------------


def test_parse_budgets_is_loud_on_nonsense():
    assert parse_budgets(["claude=200", "codex=80.5"]) == {"claude": 200.0, "codex": 80.5}
    assert parse_budgets(None) == {}
    for bad in ["claude", "claude=", "=200", "claude=abc", "claude=-5", "claude=0"]:
        with pytest.raises(SystemExit, match="invalid --budget"):
            parse_budgets([bad])


def test_policy_rejects_incoherent_configuration():
    with pytest.raises(ValueError, match="invalid budget action"):
        BudgetPolicy(budgets={"claude": 10}, action="explode")
    with pytest.raises(ValueError, match="warn_at"):
        BudgetPolicy(budgets={"claude": 10}, warn_at=0)
    with pytest.raises(ValueError, match="must be > 0"):
        BudgetPolicy(budgets={"claude": 0.0})
    with pytest.raises(ValueError, match="requires failover_to"):
        BudgetPolicy(budgets={"claude": 10}, action="failover")


def test_baseline_and_live_spend_are_summed_not_double_counted():
    ledger = BudgetLedger(baseline={"claude": 5.0})
    policy = BudgetPolicy(budgets={"claude": 10.0}, warn_at=0.9)
    assert evaluate(ledger, policy, "claude").fraction == 0.5
    ledger.record("claude", 4.0)
    assert evaluate(ledger, policy, "claude").spent_usd == 9.0
    assert evaluate(ledger, policy, "claude").tripped  # 90% is AT the threshold, so it trips


def test_undeclared_budget_never_trips():
    ledger = BudgetLedger(baseline={"codex": 1_000.0})
    verdict = evaluate(ledger, BudgetPolicy(budgets={"claude": 10.0}), "codex")
    assert not verdict.tripped and verdict.budget_usd is None


def test_load_baseline_tolerates_a_missing_archive(tmp_path):
    assert load_baseline(str(tmp_path / "nope")) == {}


# --- the three actions, end to end through the runner -------------------------------------------


def test_warn_is_the_default_and_never_changes_what_a_run_does(tmp_path, capsys):
    """Adding a budget must not alter behaviour unless an action is asked for."""
    executor = SpendingExecutor("fake", cost=10.0)  # blows a $1 budget on the first node
    _run(tmp_path, executor, budget_policy=BudgetPolicy(budgets={"fake": 1.0}))
    assert executor.calls == ["a", "b", "c"]  # all three still ran
    err = capsys.readouterr().err  # the runner logs to stderr
    assert "budget" in err.lower()
    assert err.lower().count("action=warn") == 1  # said once per backend, not once per node


def test_stop_halts_at_the_boundary_before_the_offending_node_runs(tmp_path, capsys):
    executor = SpendingExecutor("fake", cost=1.0)
    policy = BudgetPolicy(budgets={"fake": 2.0}, warn_at=0.9, action="stop")
    with pytest.raises(SystemExit, match="budget threshold reached"):
        _run(tmp_path, executor, budget_policy=policy)

    # a ($1, 50%) and b ($2, 100%) ran; the check before c saw 100% and stopped it starting.
    assert executor.calls == ["a", "b"]
    err = capsys.readouterr().err
    assert "nothing was lost" in err
    assert "--resume-from c" in err  # the exact continuation is printed


def test_stop_records_the_run_incomplete_not_passed(tmp_path):
    executor = SpendingExecutor("fake", cost=1.0)
    policy = BudgetPolicy(budgets={"fake": 2.0}, action="stop")
    with pytest.raises(SystemExit):
        _run(tmp_path, executor, budget_policy=policy)

    status = json.loads((tmp_path / "runs" / "r1" / "status.json").read_text())
    assert status["status"] != "completed"
    assert "budget" in (status["error"] or "").lower()
    # The nodes that DID finish are still recorded — a budget stop is not a failure of the work.
    assert status["nodes"]["a"]["status"] == "completed"
    assert status["nodes"]["c"]["status"] != "completed"


def test_failover_moves_the_remaining_nodes_to_the_other_backend(tmp_path, capsys):
    primary = SpendingExecutor("primary", cost=1.0)
    secondary = SpendingExecutor("secondary", cost=1.0)
    policy = BudgetPolicy(
        budgets={"primary": 2.0, "secondary": 50.0}, action="failover", failover_to="secondary"
    )
    _run(tmp_path, primary, budget_policy=policy, failover_executor=secondary)

    assert primary.calls == ["a", "b"]
    assert secondary.calls == ["c"]  # the run continued rather than dying
    assert "moving" in capsys.readouterr().err


def test_failover_declines_to_a_stop_when_the_target_is_also_exhausted(tmp_path, capsys):
    """Moving a run onto a second exhausted account helps nobody — stop honestly instead."""
    secondary = SpendingExecutor("secondary", cost=1.0)
    # Spend the secondary's whole budget in an EARLIER run, so it is genuinely exhausted on disk
    # rather than merely declared small.
    run_topology(
        Topology(name="t", nodes=[Node(id="x", role="builder", prompt="p")]),
        secondary,
        run_id="r0",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
    )
    secondary.calls.clear()

    primary = SpendingExecutor("primary", cost=1.0)
    policy = BudgetPolicy(
        budgets={"primary": 2.0, "secondary": 1.0}, action="failover", failover_to="secondary"
    )
    with pytest.raises(SystemExit, match="budget threshold reached"):
        _run(tmp_path, primary, budget_policy=policy, failover_executor=secondary)

    assert secondary.calls == []  # never handed work it could not afford
    assert "also at its ceiling" in capsys.readouterr().err


def test_no_policy_leaves_the_runner_exactly_as_it_was(tmp_path):
    """The additive guarantee: 53 existing callers pass no budget and must be unaffected."""
    executor = SpendingExecutor("fake", cost=1_000.0)
    _run(tmp_path, executor)
    assert executor.calls == ["a", "b", "c"]


# --- CLI wiring -------------------------------------------------------------------------------


def test_cli_wires_budget_flags_end_to_end(tmp_path, monkeypatch, capsys):
    import cadora.cli as cli

    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n    prompt: hi\n  - id: b\n    prompt: hi\n")
    executor = SpendingExecutor("fake", cost=1.0)
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: executor)

    with pytest.raises(SystemExit, match="budget threshold reached"):
        cli.main(
            [
                "run", str(topo),
                "--cwd", str(tmp_path),
                "--archive-dir", str(tmp_path / "runs"),
                "--run-id", "cli-budget",
                "--budget", "fake=1.0",
                "--on-budget", "stop",
                "--yes",
            ]
        )
    assert executor.calls == ["a"]  # stopped at the b boundary, having spent the whole $1


def test_cli_refuses_an_action_with_nothing_to_measure(tmp_path):
    """--on-budget/--failover-to without a --budget would silently never trip. Say so instead."""
    import cadora.cli as cli

    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n    prompt: hi\n")
    with pytest.raises(SystemExit, match="need at least one --budget"):
        cli.main(
            ["run", str(topo), "--cwd", str(tmp_path), "--on-budget", "stop", "--yes"]
        )


def test_a_resumed_run_counts_its_earlier_nodes_from_the_archive(tmp_path):
    """Baseline is read once from disk, so resumed spend is counted — and only once."""
    executor = SpendingExecutor("fake", cost=1.0)
    _run(tmp_path, executor)  # burns $3 across a, b, c into the archive

    baseline = load_baseline(str(tmp_path / "runs"))
    assert baseline["fake"] == pytest.approx(3.0)

    ledger = BudgetLedger(baseline=baseline)
    policy = BudgetPolicy(budgets={"fake": 3.0}, action="stop")
    assert evaluate(ledger, policy, "fake").tripped  # already spent the whole budget
