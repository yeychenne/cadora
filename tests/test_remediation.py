"""Tests for the bounded gate-remediation loop (``--remediate N``).

Pins the loop contract from vision.md: a failing gate feeds its own output back to a fresh,
constrained session and re-runs, bounded by attempt count and (optional) cost — never a
fabricated pass. Follows the ``FakeExecutor`` conventions in ``tests/test_runner.py``.
"""

import json
import shlex
import sys

import pytest

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.gates import ShellGate
from cadora.remediation import RemediationPolicy
from cadora.review import REVIEW_APPROVE, REVIEW_REQUEST_CHANGES, ReviewResult
from cadora.runner import run_topology
from cadora.topology import Node, Topology

_PY = shlex.quote(sys.executable)
_MARKER_GATE = (
    f"{_PY} -c \"import pathlib,sys; sys.exit(0 if pathlib.Path('solution.py').is_file() "
    'else 1)"'
)


def _topo(*nodes):
    return Topology(name="t", nodes=list(nodes))


def _runs(tmp_path):
    return str(tmp_path / "runs")


class FixerExecutor(NodeExecutor):
    """Base run never fixes the gate; the Nth remediation attempt writes the missing file."""

    name = "fake"

    def __init__(self, workspace, fix_at_attempt: int = 1, cost: float = 0.01):
        self.workspace = workspace
        self.fix_at_attempt = fix_at_attempt
        self.cost = cost
        self.calls: list[str] = []

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append(node.id)
        if node.id.endswith(f"-remediate-{self.fix_at_attempt}"):
            (self.workspace / "solution.py").write_text("print('fixed')")
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"out-{node.id}",
            cost_usd=self.cost,
        )


class NeverFixesExecutor(NodeExecutor):
    """Claims success every time, but never actually fixes the gate — the false-green case."""

    name = "fake"

    def __init__(self, cost: float = 0.01):
        self.cost = cost
        self.calls: list[str] = []

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append(node.id)
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}", cost_usd=self.cost
        )


def test_remediation_fixes_failing_gate_to_completed_green(tmp_path):
    # 1. gate fails -> remediation session writes the missing code -> same gate re-runs green.
    ex = FixerExecutor(tmp_path, fix_at_attempt=1)
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", _MARKER_GATE)}
    out = run_topology(
        t, ex, run_id="r1", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates,
        remediation_policy=RemediationPolicy(max_attempts=2),
    )
    assert ex.calls == ["a", "a-remediate-1"]
    manifest = json.loads((out / "manifest.json").read_text())
    node = manifest["nodes"][0]
    assert manifest["ok"] is True
    assert node["gate"]["passed"] is True
    assert node["remediation"]["state"] == "completed-green"
    assert node["remediation"]["attempts"] == 1


def test_remediation_exhausts_attempts_to_honest_blocked(tmp_path):
    # 2. max_attempts exhausted -> honest-blocked, non-zero exit, full trail archived.
    ex = NeverFixesExecutor()
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", "false")}  # never passes

    with pytest.raises(SystemExit):
        run_topology(
            t, ex, run_id="r2", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates,
            remediation_policy=RemediationPolicy(max_attempts=2),
        )

    manifest = json.loads((tmp_path / "runs" / "r2" / "manifest.json").read_text())
    node = manifest["nodes"][0]
    assert manifest["ok"] is False
    assert node["remediation"]["state"] == "honest-blocked"
    assert node["remediation"]["blocked_reason"] == "max_attempts"
    assert node["remediation"]["attempts"] == 2
    assert ex.calls == ["a", "a-remediate-1", "a-remediate-2"]


def test_blocked_prerequisite_never_invokes_remediation(tmp_path):
    # 3. blocked_prerequisite -> remediation NEVER invoked (terminal).
    ex = NeverFixesExecutor()
    command = (
        f"{_PY} -c \"import sys; "
        "sys.stderr.write('error: unrecognized arguments: --cov=src'); sys.exit(4)\""
    )
    gates = {"build-test": ShellGate("build-test", command)}

    with pytest.raises(SystemExit, match=r"missing prerequisite\(s\): pytest-cov"):
        run_topology(
            _topo(Node(id="a", prompt="A", gate="build-test")),
            ex,
            run_id="r3",
            cwd=str(tmp_path),
            archive_root=_runs(tmp_path),
            gates=gates,
            remediation_policy=RemediationPolicy(max_attempts=3),
        )

    assert ex.calls == ["a"]  # no "-remediate-" sessions were ever spawned
    manifest = json.loads((tmp_path / "runs" / "r3" / "manifest.json").read_text())
    assert manifest["nodes"][0].get("remediation") is None


def test_cost_ceiling_exceeded_mid_loop_blocks_honestly(tmp_path):
    # 4. cost ceiling exceeded mid-loop -> honest-blocked with blocked_reason="cost_ceiling".
    ex = NeverFixesExecutor(cost=0.3)
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", "false")}

    with pytest.raises(SystemExit):
        run_topology(
            t, ex, run_id="r4", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates,
            remediation_policy=RemediationPolicy(max_attempts=5, max_cost_usd=0.5),
        )

    manifest = json.loads((tmp_path / "runs" / "r4" / "manifest.json").read_text())
    node = manifest["nodes"][0]
    assert node["remediation"]["blocked_reason"] == "cost_ceiling"
    assert node["remediation"]["attempts"] == 2  # stopped before a 3rd attempt would exceed $0.5
    assert node["remediation"]["cost_usd"] == pytest.approx(0.6)


def test_archive_and_manifest_carry_full_attempt_trail_and_summed_cost(tmp_path):
    # 5. archive/manifest carries every attempt (prompt+output+gate), final state, summed cost.
    ex = FixerExecutor(tmp_path, fix_at_attempt=2, cost=0.02)
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", _MARKER_GATE)}
    out = run_topology(
        t, ex, run_id="r5", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates,
        remediation_policy=RemediationPolicy(max_attempts=3),
    )
    manifest = json.loads((out / "manifest.json").read_text())
    node = manifest["nodes"][0]
    remediation = node["remediation"]
    assert remediation["state"] == "completed-green"
    assert remediation["attempts"] == 2
    assert remediation["cost_usd"] == pytest.approx(0.04)
    assert node["cost_usd"] == pytest.approx(0.02 + 0.04)  # base run + both remediation attempts

    trail = remediation["trail"]
    assert [t["number"] for t in trail] == [1, 2]
    for attempt in trail:
        assert attempt["prompt"]
        assert attempt["execution"]["text"]
        assert attempt["gate"] is not None
    assert trail[0]["gate"]["passed"] is False  # attempt 1 didn't fix it yet
    assert trail[1]["gate"]["passed"] is True  # attempt 2 did

    node_dir = out / "a" / "remediation"
    assert (node_dir / "1-prompt.txt").is_file()
    assert (node_dir / "1-output.txt").is_file()
    assert (node_dir / "2-prompt.txt").is_file()
    assert (node_dir / "2-output.txt").is_file()


def test_green_via_remediation_composes_with_hitl_and_integrity(tmp_path):
    # 6. green-via-remediation still composes with HITL request-changes + integrity re-check.
    class ComposeExecutor(NodeExecutor):
        name = "fake"

        def __init__(self, workspace):
            self.workspace = workspace
            self.calls: list[str] = []

        def run(self, node, prompt, *, cwd, env=None):
            self.calls.append(node.id)
            marker = self.workspace / "solution.py"
            if "-remediate-" in node.id:
                marker.write_text("print('ok')")
            elif marker.exists():
                marker.unlink()  # a fresh base session; remediation must fix it again
            return ExecutionResult(
                node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}", cost_usd=0.01
            )

    decisions = iter(
        [
            ReviewResult(REVIEW_REQUEST_CHANGES, "polish it"),
            ReviewResult(REVIEW_APPROVE),
        ]
    )

    def review_fn(node, node_cwd):
        return next(decisions)

    ex = ComposeExecutor(tmp_path)
    t = _topo(Node(id="a", prompt="A", gate="build-test", review=True))
    gates = {"build-test": ShellGate("build-test", _MARKER_GATE)}
    out = run_topology(
        t, ex, run_id="r6", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates,
        integrity_mode="enforce", hitl=True, review_fn=review_fn,
        remediation_policy=RemediationPolicy(max_attempts=2),
    )

    manifest = json.loads((out / "manifest.json").read_text())
    node = manifest["nodes"][0]
    assert manifest["ok"] is True
    assert node["remediation"]["state"] == "completed-green"
    assert [r["decision"] for r in node["human_reviews"]] == [
        "request_changes",
        "approve",
    ]
    assert node["integrity"]["passed"] is True
    # base session ran twice (initial + post-request-changes revision), each needing a fix.
    assert ex.calls == ["a", "a-remediate-1", "a", "a-remediate-1"]


def test_false_green_guard_execution_ok_does_not_fake_a_pass(tmp_path):
    # 7. a false-green guard: an executor that claims success but the gate still fails -> NOT green.
    ex = NeverFixesExecutor()  # every attempt reports ok=True yet never touches the workspace
    t = _topo(Node(id="a", prompt="A", gate="build-test"))
    gates = {"build-test": ShellGate("build-test", "false")}

    with pytest.raises(SystemExit):
        run_topology(
            t, ex, run_id="r7", cwd=str(tmp_path), archive_root=_runs(tmp_path), gates=gates,
            remediation_policy=RemediationPolicy(max_attempts=1),
        )

    manifest = json.loads((tmp_path / "runs" / "r7" / "manifest.json").read_text())
    node = manifest["nodes"][0]
    assert all(a["execution"]["ok"] is True for a in node["remediation"]["trail"])
    assert node["remediation"]["state"] != "completed-green"
    assert node["remediation"]["state"] == "honest-blocked"


def test_cli_wires_remediate_flags_end_to_end(tmp_path, monkeypatch):
    # --remediate / --remediate-max-cost actually drive the loop through the CLI.
    import cadora.cli as cli

    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: a\n    prompt: hi\n    gate: build-test\n")
    ex = FixerExecutor(tmp_path, fix_at_attempt=1)
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: ex)

    rc = cli.main(
        [
            "run", str(topo),
            "--cwd", str(tmp_path),
            "--archive-dir", _runs(tmp_path),
            "--run-id", "cli-remediate",
            "--gate-cmd", _MARKER_GATE,
            "--remediate", "2",
            "--remediate-max-cost", "5.0",
        ]
    )
    assert rc == 0
    manifest = json.loads((tmp_path / "runs" / "cli-remediate" / "manifest.json").read_text())
    assert manifest["nodes"][0]["remediation"]["state"] == "completed-green"
