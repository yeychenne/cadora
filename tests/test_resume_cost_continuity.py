"""A run id identifies the whole run, not one invocation of it.

`--resume-from` opens a second invocation against the same run id. Before this was fixed, that
invocation wrote a manifest containing only the nodes it ran — deleting the earlier nodes' cost,
usage, and gate records from the evidence. Found live: a four-node run recorded $4.17, resumed at
its last node, and kept $1.72. Everything that reads the archive — `cadora usage`, `cadora
accounts`, and the budget ledger that decides when a backend is running dry — under-reported by
59%, which is worst precisely where it matters: a budget guard believes it has headroom it does
not have.
"""

import json

import pytest

from cadora.archive import list_runs
from cadora.budget import BudgetLedger, BudgetPolicy, evaluate, load_baseline
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import ShellGate
from cadora.runner import run_topology
from cadora.topology import Node, Topology
from cadora.usage import summarize_usage


class CostedExecutor(NodeExecutor):
    name = "fake"

    def __init__(self, cost: float = 1.0):
        self.cost = cost
        self.calls: list[str] = []

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append(node.id)
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"out-{node.id}",
            usage={"input_tokens": 100, "output_tokens": 50},
            cost_usd=self.cost,
        )


def _chain(*ids):
    return Topology(
        name="t",
        nodes=[
            Node(id=n, role="builder", prompt="p", depends_on=[ids[i - 1]] if i else [])
            for i, n in enumerate(ids)
        ],
    )


def _run(tmp_path, executor, **kwargs):
    return run_topology(
        _chain("a", "b", "c"),
        executor,
        run_id="same-id",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        **kwargs,
    )


def _manifest(tmp_path):
    return json.loads((tmp_path / "runs" / "same-id" / "manifest.json").read_text())


def _status(tmp_path):
    return json.loads((tmp_path / "runs" / "same-id" / "status.json").read_text())


def test_resume_keeps_the_earlier_invocations_cost_in_the_manifest(tmp_path):
    executor = CostedExecutor(cost=1.0)
    _run(tmp_path, executor)  # a, b, c = $3.00
    assert [n["node_id"] for n in _manifest(tmp_path)["nodes"]] == ["a", "b", "c"]

    # Resume the last node only. The first two are skipped, not re-run.
    resumed = CostedExecutor(cost=1.0)
    _run(tmp_path, resumed, resume_from="c")
    assert resumed.calls == ["c"]

    nodes = _manifest(tmp_path)["nodes"]
    assert [n["node_id"] for n in nodes] == ["a", "b", "c"]  # all three survive, in order
    # $3 for the first pass plus $1 for c's second run — the money actually spent, not $3.
    assert sum(n["cost_usd"] for n in nodes) == pytest.approx(4.0)


def test_every_archive_reader_sees_the_whole_run_after_a_resume(tmp_path):
    """usage / accounts / the budget ledger all read the manifest — none may under-report."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    _run(tmp_path, CostedExecutor(cost=1.0), resume_from="c")

    archive = str(tmp_path / "runs")
    assert summarize_usage(archive).cost_usd == pytest.approx(4.0)  # 3 + c's re-run
    assert load_baseline(archive)["fake"] == pytest.approx(4.0)

    # The failure that motivated this: a budget guard trusting a truncated baseline.
    ledger = BudgetLedger(baseline=load_baseline(archive))
    assert evaluate(ledger, BudgetPolicy(budgets={"fake": 3.0}), "fake").tripped


def test_a_re_run_node_accumulates_its_invocations_rather_than_replacing_them(tmp_path):
    """One entry per node, but its cost is the node's total across the whole run.

    Replacing would drop the first attempt's money — the same under-reporting the carry-forward
    exists to prevent, just one level down. Measured before this was fixed: $7 spent, $5 reported.
    """
    _run(tmp_path, CostedExecutor(cost=1.0))  # a, b, c at $1 = $3
    _run(tmp_path, CostedExecutor(cost=2.0), resume_from="b")  # b, c again at $2 = $4

    nodes = _manifest(tmp_path)["nodes"]
    assert [n["node_id"] for n in nodes] == ["a", "b", "c"]  # no duplicates
    by_id = {n["node_id"]: n for n in nodes}
    assert by_id["a"]["cost_usd"] == pytest.approx(1.0)  # ran once, untouched
    assert by_id["b"]["cost_usd"] == pytest.approx(3.0)  # $1 + $2, not $2
    assert summarize_usage(str(tmp_path / "runs")).cost_usd == pytest.approx(7.0)


def test_accumulating_keeps_the_earlier_invocations_detail(tmp_path):
    """Summing must not destroy what it summed — the trail is the evidence."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    _run(tmp_path, CostedExecutor(cost=2.0), resume_from="b")

    b = next(n for n in _manifest(tmp_path)["nodes"] if n["node_id"] == "b")
    assert b["invocations"] == 2
    assert [p["cost_usd"] for p in b["prior_invocations"]] == [pytest.approx(1.0)]
    # Tokens accumulate the same way, so usage reporting matches the dollars.
    assert b["usage"]["input_tokens"] == 200  # 100 per invocation
    a = next(n for n in _manifest(tmp_path)["nodes"] if n["node_id"] == "a")
    assert "prior_invocations" not in a  # a single-invocation node stays clean


def test_status_json_reports_a_skipped_nodes_real_cost(tmp_path):
    """The dashboard reads status.json; a skipped node showing $0 misreports the run."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    _run(tmp_path, CostedExecutor(cost=1.0), resume_from="c")

    nodes = _status(tmp_path)["nodes"]
    assert nodes["a"]["status"] == "skipped"
    assert nodes["a"]["cost_usd"] == pytest.approx(1.0)  # not 0, not None
    assert sum(n["cost_usd"] or 0 for n in nodes.values()) == pytest.approx(4.0)


def test_the_dashboard_and_the_manifest_never_disagree_on_cost(tmp_path):
    """status.json feeds the dashboard; the manifest feeds usage/accounts/budget. A run whose two
    records tell different stories is worse than one that is merely wrong."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    _run(tmp_path, CostedExecutor(cost=2.0), resume_from="b")

    manifest_total = sum(n["cost_usd"] or 0 for n in _manifest(tmp_path)["nodes"])
    status_total = sum(n["cost_usd"] or 0 for n in _status(tmp_path)["nodes"].values())
    assert manifest_total == pytest.approx(7.0)
    assert status_total == pytest.approx(manifest_total)


def test_a_fresh_run_id_is_unaffected(tmp_path):
    """Carry-forward must not invent history for a run id with no prior manifest."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    assert len(_manifest(tmp_path)["nodes"]) == 3
    assert len(list_runs(str(tmp_path / "runs"))) == 1


def test_a_corrupt_prior_manifest_does_not_block_the_run(tmp_path):
    """Refusing to start because a PREVIOUS invocation left bad JSON would be worse."""
    run_dir = tmp_path / "runs" / "same-id"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text("{not json at all")

    executor = CostedExecutor(cost=1.0)
    _run(tmp_path, executor)
    assert executor.calls == ["a", "b", "c"]
    assert [n["node_id"] for n in _manifest(tmp_path)["nodes"]] == ["a", "b", "c"]


def test_a_failed_run_resumed_to_green_keeps_the_failed_attempts_cost(tmp_path):
    """The real shape of the bug: runs are usually resumed *because* they failed."""
    gate = {"g": ShellGate(name="g", command="test -f marker")}
    topology = Topology(
        name="t",
        nodes=[
            Node(id="a", role="builder", prompt="p"),
            Node(id="b", role="builder", prompt="p", depends_on=["a"], gate="g"),
        ],
    )
    archive = str(tmp_path / "runs")
    with pytest.raises(SystemExit):
        run_topology(
            topology, CostedExecutor(cost=1.0), run_id="same-id",
            cwd=str(tmp_path), archive_root=archive, gates=gate,
        )
    assert summarize_usage(archive).cost_usd == pytest.approx(2.0)  # a + failed b

    (tmp_path / "marker").write_text("ok")  # make the gate pass, then resume at b
    run_topology(
        topology, CostedExecutor(cost=1.0), run_id="same-id",
        cwd=str(tmp_path), archive_root=archive, gates=gate, resume_from="b", allow_drift=True,
    )
    nodes = _manifest(tmp_path)["nodes"]
    assert [n["node_id"] for n in nodes] == ["a", "b"]
    # a ($1) + b's failed attempt ($1) + b's successful retry ($1). The failed attempt cost real
    # money and stays counted — that is the whole point of resuming a failed run honestly.
    assert sum(n["cost_usd"] for n in nodes) == pytest.approx(3.0)
