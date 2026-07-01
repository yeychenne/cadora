"""End-to-end runner tests using a FakeExecutor (no real agent CLI)."""

import json
import shlex
import sys

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
            usage={"input_tokens": 10, "output_tokens": 5},
            cost_usd=0.01,
            meta={"funding_resolved": "subscription"},
        )


class RepairExecutor(FakeExecutor):
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace

    def run(self, node, prompt, *, cwd, env=None):
        result = super().run(node, prompt, cwd=cwd, env=env)
        if node.id.endswith("-integrity-repair"):
            fake = self.workspace / "pytest"
            for child in fake.iterdir():
                child.unlink()
            fake.rmdir()
        return result


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
    status = json.loads((out / "status.json").read_text())
    assert status["status"] == "completed"
    assert status["nodes"]["a"]["status"] == "completed"
    assert status["nodes"]["a"]["generation_tokens"] == 15
    events = [json.loads(line) for line in (out / "run-events.jsonl").read_text().splitlines()]
    assert [event["type"] for event in events] == [
        "run_started",
        "node_started",
        "node_completed",
        "node_started",
        "node_completed",
        "run_completed",
    ]


def test_run_announces_each_stage(tmp_path, capsys):
    # progress visibility: each stage prints a "▶ <node>" announce before it runs
    ex = FakeExecutor()
    t = _topo(Node(id="a"), Node(id="b", depends_on=["a"]))
    run_topology(t, ex, run_id="r", cwd=str(tmp_path), archive_root=_runs(tmp_path))
    err = capsys.readouterr().err
    assert "▶ a · " in err
    assert "▶ b · " in err


def test_failing_gate_blocks_run(tmp_path):
    ex = FakeExecutor()
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", "false")}  # non-zero exit -> block
    with pytest.raises(SystemExit):
        run_topology(t, ex, run_id="r2", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates)
    manifest = json.loads((tmp_path / "runs" / "r2" / "manifest.json").read_text())
    assert manifest["ok"] is False
    assert manifest["nodes"][0]["gate"]["passed"] is False
    status = json.loads((tmp_path / "runs" / "r2" / "status.json").read_text())
    assert status["status"] == "failed"
    assert status["nodes"]["a"]["status"] == "failed"


def test_missing_gate_prerequisite_has_structured_block_reason(tmp_path):
    ex = FakeExecutor()
    python = shlex.quote(sys.executable)
    command = (
        f"{python} -c \"import sys; "
        "sys.stderr.write('error: unrecognized arguments: --cov=src'); sys.exit(4)\""
    )
    gates = {"build-test": ShellGate("build-test", command)}

    with pytest.raises(SystemExit, match=r"missing prerequisite\(s\): pytest-cov"):
        run_topology(
            _topo(Node(id="a", prompt="A", gate="build-test")),
            ex,
            run_id="missing-prerequisite",
            cwd=str(tmp_path),
            archive_root=_runs(tmp_path),
            gates=gates,
            integrity_mode="repair",
        )

    manifest = json.loads(
        (tmp_path / "runs" / "missing-prerequisite" / "manifest.json").read_text()
    )
    gate = manifest["nodes"][0]["gate"]
    assert gate["status"] == "blocked_prerequisite"
    assert gate["missing_prerequisites"] == ["pytest-cov"]
    assert [call[0] for call in ex.calls] == ["a"]  # an LLM cannot repair missing infrastructure


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


def test_cli_run_installs_codex_project_memory(tmp_path, monkeypatch):
    import cadora.cli as cli

    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n    prompt: hi\n")
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: FakeExecutor())
    rc = cli.main(
        [
            "run",
            str(topo),
            "--executor",
            "codex",
            "--cwd",
            str(tmp_path),
            "--archive-dir",
            _runs(tmp_path),
            "--run-id",
            "codex-cli",
            "--vision",
            "Build X.",
        ]
    )
    assert rc == 0
    assert (tmp_path / "AGENTS.md").is_file()
    assert not (tmp_path / "CLAUDE.md").exists()


def test_integrity_audit_records_but_does_not_block(tmp_path):
    (tmp_path / "pytest").mkdir()
    (tmp_path / "pytest" / "__init__.py").write_text("")
    out = run_topology(
        _topo(Node(id="a", prompt="A")),
        FakeExecutor(),
        run_id="integrity-audit",
        cwd=str(tmp_path),
        archive_root=_runs(tmp_path),
        integrity_mode="audit",
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert manifest["nodes"][0]["integrity"]["passed"] is False
    assert (out / "a" / "integrity.json").is_file()


def test_integrity_enforce_blocks(tmp_path):
    (tmp_path / "pytest").mkdir()
    (tmp_path / "pytest" / "__init__.py").write_text("")
    with pytest.raises(SystemExit, match="toolchain integrity blocked"):
        run_topology(
            _topo(Node(id="a", prompt="A")),
            FakeExecutor(),
            run_id="integrity-enforce",
            cwd=str(tmp_path),
            archive_root=_runs(tmp_path),
            integrity_mode="enforce",
        )


def test_integrity_repair_runs_once_and_rescans(tmp_path):
    (tmp_path / "pytest").mkdir()
    (tmp_path / "pytest" / "__init__.py").write_text("")
    executor = RepairExecutor(tmp_path)
    out = run_topology(
        _topo(Node(id="a", prompt="A")),
        executor,
        run_id="integrity-repair",
        cwd=str(tmp_path),
        archive_root=_runs(tmp_path),
        integrity_mode="repair",
    )
    manifest = json.loads((out / "manifest.json").read_text())
    assert [call[0] for call in executor.calls] == ["a", "a-integrity-repair"]
    assert manifest["ok"] is True
    assert manifest["nodes"][0]["integrity"]["passed"] is True
    assert manifest["nodes"][0]["repair"]["ok"] is True
