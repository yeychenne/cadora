"""End-to-end runner tests using a FakeExecutor (no real agent CLI)."""

import json

import pytest

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import ShellGate
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class FakeExecutor(NodeExecutor):
    name = "fake"

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append((node.id, prompt))
        return ExecutionResult(
            node_id=node.id,
            ok=self.ok,
            exit_code=0 if self.ok else 1,
            text=f"out-{node.id}",
            cost_usd=0.01,
            meta={"funding_resolved": "subscription"},
        )


def _topo(*nodes):
    return Topology(name="t", nodes=list(nodes))


def _runs(tmp_path):
    return str(tmp_path / "runs")


def test_runs_in_dependency_order_and_threads_outputs(tmp_path):
    ex = FakeExecutor()
    t = _topo(Node(id="a", prompt="A"), Node(id="b", prompt="B", depends_on=["a"]))
    out = run_topology(t, ex, run_id="r1", cwd=str(tmp_path), archive_root=_runs(tmp_path))
    assert [c[0] for c in ex.calls] == ["a", "b"]
    assert "out-a" in dict(ex.calls)["b"]  # upstream output threaded into b's prompt
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert [n["node_id"] for n in manifest["nodes"]] == ["a", "b"]
    assert manifest["nodes"][0]["cost_usd"] == 0.01


def test_failing_gate_blocks_run(tmp_path):
    ex = FakeExecutor()
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", "false")}  # non-zero exit -> block
    with pytest.raises(SystemExit):
        run_topology(t, ex, run_id="r2", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates)
    manifest = json.loads((tmp_path / "runs" / "r2" / "manifest.json").read_text())
    assert manifest["ok"] is False
    assert manifest["nodes"][0]["gate"]["passed"] is False


def test_passing_gate_allows_run(tmp_path):
    ex = FakeExecutor()
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", "true")}
    out = run_topology(t, ex, run_id="r3", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates)
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert manifest["nodes"][0]["gate"]["passed"] is True


def test_unknown_gate_is_preflight_error(tmp_path):
    ex = FakeExecutor()
    t = _topo(Node(id="a", prompt="A", gate="nope"))
    with pytest.raises(SystemExit, match="unregistered gate"):
        run_topology(t, ex, run_id="r4", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates={})
    assert ex.calls == []  # pre-flight: nothing ran


def test_executor_failure_stops_run(tmp_path):
    ex = FakeExecutor(ok=False)
    with pytest.raises(SystemExit):
        run_topology(_topo(Node(id="a", prompt="A")), ex, run_id="r5", cwd=str(tmp_path),
                     archive_root=_runs(tmp_path))
    manifest = json.loads((tmp_path / "runs" / "r5" / "manifest.json").read_text())
    assert manifest["ok"] is False


def test_aidlc_docs_snapshotted_into_archive(tmp_path):
    ws = tmp_path / "ws"
    (ws / "aidlc-docs").mkdir(parents=True)
    (ws / "aidlc-docs" / "requirements.md").write_text("# reqs")
    out = run_topology(_topo(Node(id="a", prompt="A")), FakeExecutor(), run_id="r6",
                       cwd=str(ws), archive_root=_runs(tmp_path))
    assert (out / "a" / "aidlc-docs" / "requirements.md").read_text() == "# reqs"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["nodes"][0]["aidlc_docs"] == "a/aidlc-docs"


def test_cli_run_wires_workspace_gate_and_executor(tmp_path, monkeypatch):
    import cadora.cli as cli

    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n    prompt: hi\n    gate: build-test\n")
    # swap in a fake executor so no real agent CLI runs
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: FakeExecutor())
    rc = cli.main(
        [
            "run", str(topo),
            "--cwd", str(tmp_path),
            "--archive-dir", _runs(tmp_path),
            "--run-id", "cli1",
            "--gate-cmd", "true",  # passing gate
            "--vision", "Build X.",
        ]
    )
    assert rc == 0
    assert (tmp_path / "CLAUDE.md").is_file()  # workspace was set up from --vision
    assert (tmp_path / "vision.md").read_text() == "Build X."
    manifest = json.loads((tmp_path / "runs" / "cli1" / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert manifest["nodes"][0]["gate"]["passed"] is True
