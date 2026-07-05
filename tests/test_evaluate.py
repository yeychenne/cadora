"""Tests for `cadora eval` — deterministic AI-DLC evaluation of a run.

Deterministic checks first (no LLM cost): completion, per-node success, gate
verdicts, integrity, cost attribution, artifact presence. LLM-as-judge graders
are a separate opt-in layer.
"""

from cadora.archive import RunArchive, read_manifest
from cadora.evaluate import evaluate_run, format_evaluation
from cadora.executors.base import ExecutionResult
from cadora.gates import GateResult


def _run(root, run_id, nodes, ok=True):
    ar = RunArchive(root, run_id, "claude", "aidlc")
    for n in nodes:
        gate = n.pop("_gate", None)
        ar.record(ExecutionResult(**n), gate=gate)
    return ar.finalize(ok)


def test_eval_clean_run_passes(tmp_path):
    _run(tmp_path, "A",
         [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.1,
               _gate=GateResult(name="tests", passed=True))],
         ok=True)
    r = evaluate_run(read_manifest(tmp_path, "A"), run_dir=tmp_path / "A")
    assert r["verdict"] == "pass"
    by = {c["name"]: c["passed"] for c in r["checks"]}
    assert by["run_ok"] and by["all_nodes_ok"] and by["gates_passed"]
    assert by["cost_attributed"] is True
    assert "PASS" in format_evaluation(r).upper()


def test_eval_failed_node_fails(tmp_path):
    _run(tmp_path, "B",
         [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.1),
          dict(node_id="fse", ok=False, exit_code=1, cost_usd=0.2)],
         ok=False)
    r = evaluate_run(read_manifest(tmp_path, "B"))
    assert r["verdict"] == "fail"
    by = {c["name"]: c["passed"] for c in r["checks"]}
    assert by["all_nodes_ok"] is False and by["run_ok"] is False


def test_eval_failing_gate_fails(tmp_path):
    _run(tmp_path, "C",
         [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.1,
               _gate=GateResult(name="tests", passed=False, status="failed"))],
         ok=True)
    r = evaluate_run(read_manifest(tmp_path, "C"))
    by = {c["name"]: c["passed"] for c in r["checks"]}
    assert by["gates_passed"] is False
    assert r["verdict"] == "fail"


def test_eval_missing_cost_is_noncritical(tmp_path):
    _run(tmp_path, "D", [dict(node_id="po", ok=True, exit_code=0)], ok=True)  # no cost_usd
    r = evaluate_run(read_manifest(tmp_path, "D"))
    by = {c["name"]: c["passed"] for c in r["checks"]}
    assert by["cost_attributed"] is False
    assert r["verdict"] == "pass"  # cost attribution is a warning, not a gate


def test_cli_eval(tmp_path, capsys):
    import cadora.cli as cli
    _run(tmp_path, "E", [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.1)], ok=True)
    rc = cli.main(["eval", "E", "--archive-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "eval E" in out and "PASS" in out.upper()
    assert rc == 0


def test_cost_attributed_counts_kiro_credits(tmp_path):
    # Regression (independent Kiro test, 2026-07-04): a credit-funded run must read as
    # cost-attributed — credits are cost, even though cost_usd is None.
    ar = RunArchive(tmp_path, "kiro-eval-001", "kiro", "aidlc-hitl")
    ar.record(ExecutionResult(node_id="construction", ok=True, exit_code=0,
                              usage={"credits": 3.92}, model=None))
    ar.finalize(True)
    manifest = read_manifest(tmp_path, "kiro-eval-001")

    by = {c["name"]: c for c in evaluate_run(manifest)["checks"]}

    assert by["cost_attributed"]["passed"] is True
    assert "credits" in by["cost_attributed"]["detail"]
