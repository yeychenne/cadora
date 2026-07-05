"""Tests for `cadora compare` — diff two archived runs (cross-backend A/B).

The differentiator no vendor ships: run the SAME topology on Claude vs Codex
and diff cost / outcome per node.
"""

from cadora.archive import RunArchive, read_manifest
from cadora.compare import compare_runs, format_comparison
from cadora.executors.base import ExecutionResult


def _run(root, run_id, executor, topology, nodes):
    ar = RunArchive(root, run_id, executor, topology)
    for n in nodes:
        ar.record(ExecutionResult(**n))
    return ar.finalize(all(n["ok"] for n in nodes))


def test_compare_same_topology_across_backends(tmp_path):
    _run(tmp_path, "A", "claude", "aidlc",
         [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.10,
               model="claude-opus-4-8", usage={"output_tokens": 100})])
    _run(tmp_path, "B", "codex", "aidlc",
         [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.04,
               model="gpt-5.5", usage={"output_tokens": 120})])
    diff = compare_runs(read_manifest(tmp_path, "A"), read_manifest(tmp_path, "B"))
    assert diff["same_topology"] is True
    assert diff["summary_a"]["executor"] == "claude"
    assert diff["summary_b"]["executor"] == "codex"
    assert diff["cost_delta"] < 0  # codex cheaper here
    po = next(n for n in diff["nodes"] if n["node_id"] == "po")
    assert po["in_a"] and po["in_b"]
    assert po["model_a"] == "claude-opus-4-8" and po["model_b"] == "gpt-5.5"
    assert po["ok_changed"] is False
    text = format_comparison(diff)
    assert "po" in text and "claude" in text and "codex" in text


def test_compare_flags_node_only_in_one_and_ok_regression(tmp_path):
    _run(tmp_path, "A", "claude", "aidlc",
         [dict(node_id="po", ok=True, exit_code=0),
          dict(node_id="fse", ok=True, exit_code=0)])
    _run(tmp_path, "B", "claude", "aidlc",
         [dict(node_id="po", ok=False, exit_code=1)])  # fse missing, po regressed
    diff = compare_runs(read_manifest(tmp_path, "A"), read_manifest(tmp_path, "B"))
    po = next(n for n in diff["nodes"] if n["node_id"] == "po")
    fse = next(n for n in diff["nodes"] if n["node_id"] == "fse")
    assert po["ok_changed"] is True
    assert fse["in_a"] is True and fse["in_b"] is False


def test_compare_flags_different_topology(tmp_path):
    _run(tmp_path, "A", "claude", "aidlc", [dict(node_id="x", ok=True, exit_code=0)])
    _run(tmp_path, "B", "claude", "secure", [dict(node_id="x", ok=True, exit_code=0)])
    diff = compare_runs(read_manifest(tmp_path, "A"), read_manifest(tmp_path, "B"))
    assert diff["same_topology"] is False


def test_cli_compare(tmp_path, capsys):
    import cadora.cli as cli
    _run(tmp_path, "A", "claude", "aidlc",
         [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.10)])
    _run(tmp_path, "B", "codex", "aidlc",
         [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.05)])
    rc = cli.main(["compare", "A", "B", "--archive-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "A" in out and "B" in out and "po" in out
