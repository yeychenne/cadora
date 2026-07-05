"""Tests for `cadora deliverable` — the client-facing consulting pack (WP-C2).

Renders a narrative delivery document from a run, reusing report.py's structured
evidence (extend, don't duplicate). Markdown is the dependency-free core; .docx is
an optional extra. No LLM, no network.
"""

import pytest

from cadora.archive import RunArchive
from cadora.deliverable import build_deliverable, render_deliverable, write_deliverable
from cadora.executors.base import ExecutionResult
from cadora.gates import GateResult


def _run(root, run_id, nodes, ok=True):
    ar = RunArchive(root, run_id, "claude", "aidlc")
    for n in nodes:
        gate = n.pop("_gate", None)
        ar.record(ExecutionResult(**n), gate=gate)
    return ar.finalize(ok)


def _sample_report():
    return {
        "evidence_pack": {"cadora_version": "9.9.9", "generated_at": "2026-07-04T00:00:00+00:00"},
        "run": {"run_id": "R1", "topology": "aidlc", "executor": "claude", "ok": True},
        "summary": {
            "nodes": 2, "backends": ["claude", "codex"], "gates": {"passed": 2},
            "integrity_findings": 0, "integrity_failed_nodes": 0,
            "human_review_decisions": 1, "cost_usd": 0.42, "estimated_cost_nodes": 1,
        },
        "nodes": [
            {"node_id": "po", "executor": "claude", "model": "opus", "ok": True,
             "gate": {"passed": True, "status": "passed"}, "cost_usd": 0.30, "cost_estimated": False},
            {"node_id": "fse", "executor": "codex", "model": "gpt-5.5", "ok": True,
             "gate": {"passed": True, "status": "passed"}, "cost_usd": 0.12, "cost_estimated": True},
        ],
        "human_reviews": [{"node_id": "po", "decision": "approve"}],
        "artifacts": [{"path": "po/output.txt"}, {"path": "manifest.json"}],
    }


def test_render_deliverable_has_key_sections_and_data():
    md = render_deliverable(_sample_report())
    for heading in ("Executive summary", "What was delivered", "Quality", "Cost", "Evidence"):
        assert heading.lower() in md.lower()
    assert "aidlc" in md              # topology
    assert "claude" in md and "codex" in md  # both backends surfaced
    assert "0.42" in md               # total cost
    assert "cadora report R1" in md   # points at the verifiable evidence pack, doesn't duplicate it
    assert "po" in md and "fse" in md  # per-node


def test_render_flags_estimated_cost_and_review():
    md = render_deliverable(_sample_report())
    assert "estimated" in md.lower()          # 1 node priced from the table
    assert "review" in md.lower()             # human-review decision surfaced


def test_build_deliverable_end_to_end(tmp_path):
    _run(tmp_path, "R2", [
        dict(node_id="po", ok=True, exit_code=0, cost_usd=0.1, model="opus",
             _gate=GateResult(name="tests", passed=True)),
    ])
    md = build_deliverable(tmp_path / "R2")
    assert "R2" in md and "Executive summary".lower() in md.lower()


def test_cli_deliverable_writes_md(tmp_path, capsys):
    import cadora.cli as cli
    _run(tmp_path, "R3", [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.1)])
    out_dir = tmp_path / "pack"
    rc = cli.main(["deliverable", "R3", "--archive-dir", str(tmp_path), "--out", str(out_dir)])
    assert rc == 0
    md = out_dir / "deliverable.md"
    assert md.is_file() and "R3" in md.read_text()


def test_docx_is_optional_extra(tmp_path):
    """The .docx path is a soft extra: skipped cleanly when python-docx is absent."""
    pytest.importorskip("docx")
    _run(tmp_path, "R4", [dict(node_id="po", ok=True, exit_code=0, cost_usd=0.1)])
    paths = write_deliverable(tmp_path / "R4", out=tmp_path / "out", docx=True)
    assert paths["md"].is_file() and paths["docx"].is_file()
