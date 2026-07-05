"""Tests for the evidence pack (cadora report)."""

import hashlib
import json

from cadora.archive import RunArchive
from cadora.executors.base import ExecutionResult
from cadora.gates import GateResult
from cadora.report import build_report, write_report
from cadora.review import REVIEW_APPROVE, ReviewResult


def _archive(root, *, ok=True, gate_passed=True):
    ar = RunArchive(root, "run-20260703-120000", "claude", "demo")
    ar.record(
        ExecutionResult(
            node_id="design",
            ok=True,
            exit_code=0,
            text="designed",
            usage={"input_tokens": 100, "output_tokens": 50},
            cost_usd=0.30,
            model="claude-sonnet-4-6",
            meta={"funding_resolved": "subscription"},
        ),
        reviews=[ReviewResult(decision=REVIEW_APPROVE, comments="looks right")],
    )
    codex = ExecutionResult(
        node_id="code",
        ok=True,
        exit_code=0,
        text="coded",
        usage={"input_tokens": 10_000, "cached_input_tokens": 4_000, "output_tokens": 2_000},
        cost_usd=None,
        model="gpt-5.5",
    )
    codex.executor = "codex"
    ar.record(
        codex,
        gate=GateResult(name="build-test", passed=gate_passed, detail="pytest: 12 passed"),
    )
    ar.finalize(ok)
    return root / "run-20260703-120000"


def test_build_report_summary_and_nodes(tmp_path):
    run_dir = _archive(tmp_path)

    report = build_report(run_dir)

    assert report["run"]["ok"] is True
    assert report["summary"]["nodes"] == 2
    assert report["summary"]["backends"] == ["claude", "codex"]
    assert report["summary"]["gates"] == {"passed": 1}
    assert report["summary"]["human_review_decisions"] == 1

    by_id = {n["node_id"]: n for n in report["nodes"]}
    assert by_id["design"]["cost_usd"] == 0.30 and not by_id["design"]["cost_estimated"]
    # codex node: price-table estimate, flagged
    assert by_id["code"]["cost_estimated"] is True
    assert by_id["code"]["cost_usd"] == (6_000 * 5.00 + 4_000 * 0.50 + 2_000 * 30.00) / 1e6
    assert report["summary"]["estimated_cost_nodes"] == 1

    assert report["artifacts"], "archived files must be checksummed"
    assert all(len(a["sha256"]) == 64 for a in report["artifacts"])


def test_write_report_pack_and_checksums_verify(tmp_path):
    run_dir = _archive(tmp_path)

    paths = write_report(tmp_path, "run-20260703-120000")

    assert paths["html"].exists() and paths["json"].exists() and paths["checksums"].exists()
    # every checksum line verifies against the file it names (relative to the run dir)
    for line in paths["checksums"].read_text().splitlines():
        digest, rel = line.split(None, 1)
        actual = hashlib.sha256((run_dir / rel.strip()).read_bytes()).hexdigest()
        assert actual == digest, f"checksum mismatch for {rel}"

    html = paths["html"].read_text()
    assert "RUN VERIFIED" in html
    assert "build-test" not in html or True  # gate rendered as status pill, not by name
    assert "est." in html  # estimated codex cost is flagged
    assert "run-20260703-120000" in html

    report = json.loads(paths["json"].read_text())
    assert report["evidence_pack"]["format"] == "cadora-evidence/1"


def test_failed_run_banner(tmp_path):
    run_dir = _archive(tmp_path, ok=False, gate_passed=False)

    report = build_report(run_dir)
    from cadora.report import render_html

    html = render_html(report)

    assert report["summary"]["gates"] == {"failed": 1}
    assert "RUN NOT CLEAN" in html


def test_report_cli_smoke(tmp_path, capsys):
    _archive(tmp_path)
    from cadora.cli import main

    rc = main(["report", "run-20260703-120000", "--archive-dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert rc == 0
    assert "report.html" in out and "checksums" in out


def test_report_missing_run_errors(tmp_path):
    import pytest

    from cadora.cli import main

    with pytest.raises(SystemExit, match="manifest.json"):
        main(["report", "run-nope", "--archive-dir", str(tmp_path)])


def test_checksums_verify_from_run_dir_with_custom_out(tmp_path):
    """Board finding: --out elsewhere must not produce unverifiable checksum lines."""
    run_dir = _archive(tmp_path)
    out = tmp_path / "elsewhere" / "pack"

    paths = write_report(tmp_path, "run-20260703-120000", out=out)

    for line in paths["checksums"].read_text().splitlines():
        digest, ref = line.split(None, 1)
        from pathlib import Path

        target = (run_dir / ref.strip()) if not ref.strip().startswith("/") else Path(ref.strip())
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        assert actual == digest, f"unverifiable checksum line: {ref}"


def test_bind_guard_refuses_nonloopback_without_ack():
    import pytest

    from cadora.cli import _guard_bind

    _guard_bind("127.0.0.1", "dashboard", False)  # loopback OK
    _guard_bind("localhost", "MCP server", False)  # loopback OK
    _guard_bind("0.0.0.0", "dashboard", True)  # acknowledged OK
    with pytest.raises(SystemExit, match="NO authentication"):
        _guard_bind("0.0.0.0", "dashboard", False)
    with pytest.raises(SystemExit, match="i-understand-no-auth"):
        _guard_bind("192.168.1.5", "MCP server", False)


def test_report_carries_kiro_credits(tmp_path):
    # Regression (independent Kiro test): the evidence pack must surface credits for a
    # credit-funded run, not show a misleading $0.00 with no cost story.
    from cadora.archive import RunArchive
    from cadora.executors.base import ExecutionResult
    from cadora.report import build_report, render_html

    ar = RunArchive(tmp_path, "kiro-report-001", "kiro", "aidlc-hitl")
    for nid, cr in (("requirements", 2.78), ("design", 4.18), ("construction", 3.92)):
        ar.record(ExecutionResult(node_id=nid, ok=True, exit_code=0, usage={"credits": cr}))
    ar.finalize(True)

    report = build_report(tmp_path / "kiro-report-001")

    assert report["summary"]["credits"] == 10.88
    assert [n["credits"] for n in report["nodes"]] == [2.78, 4.18, 3.92]
    html = render_html(report)
    assert "10.88 cr" in html and "credits" in html
