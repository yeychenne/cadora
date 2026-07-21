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
    assert sum(n["cost_usd"] for n in nodes) == pytest.approx(3.0)


def test_every_archive_reader_sees_the_whole_run_after_a_resume(tmp_path):
    """usage / accounts / the budget ledger all read the manifest — none may under-report."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    _run(tmp_path, CostedExecutor(cost=1.0), resume_from="c")

    archive = str(tmp_path / "runs")
    assert summarize_usage(archive).cost_usd == pytest.approx(3.0)
    assert load_baseline(archive)["fake"] == pytest.approx(3.0)

    # The failure that motivated this: a budget guard trusting a truncated baseline.
    ledger = BudgetLedger(baseline=load_baseline(archive))
    assert evaluate(ledger, BudgetPolicy(budgets={"fake": 3.0}), "fake").tripped


def test_a_resumed_node_replaces_its_entry_rather_than_duplicating_it(tmp_path):
    """Re-running a node under the same id must leave one entry showing the latest outcome."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    _run(tmp_path, CostedExecutor(cost=2.0), resume_from="b")  # b and c run again

    nodes = _manifest(tmp_path)["nodes"]
    assert [n["node_id"] for n in nodes] == ["a", "b", "c"]  # no duplicates
    by_id = {n["node_id"]: n for n in nodes}
    assert by_id["a"]["cost_usd"] == pytest.approx(1.0)  # untouched first invocation
    assert by_id["b"]["cost_usd"] == pytest.approx(2.0)  # replaced, not appended
    assert summarize_usage(str(tmp_path / "runs")).cost_usd == pytest.approx(5.0)


def test_status_json_reports_a_skipped_nodes_real_cost(tmp_path):
    """The dashboard reads status.json; a skipped node showing $0 misreports the run."""
    _run(tmp_path, CostedExecutor(cost=1.0))
    _run(tmp_path, CostedExecutor(cost=1.0), resume_from="c")

    nodes = _status(tmp_path)["nodes"]
    assert nodes["a"]["status"] == "skipped"
    assert nodes["a"]["cost_usd"] == pytest.approx(1.0)  # not 0, not None
    assert sum(n["cost_usd"] or 0 for n in nodes.values()) == pytest.approx(3.0)


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
    assert sum(n["cost_usd"] for n in nodes) == pytest.approx(2.0)  # a kept + b's retry
