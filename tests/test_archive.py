"""Tests for the archive reader + `cadora archive ls/show`."""

import pytest

from cadora.archive import RunArchive, list_runs, read_manifest
from cadora.executors.base import ExecutionResult
from cadora.gates import GateResult
from cadora.review import REVIEW_APPROVE, ReviewResult


def _archive(root, run_id, **node):
    ar = RunArchive(root, run_id, "claude", "aidlc")
    ar.record(ExecutionResult(node_id="n1", ok=True, exit_code=0, **node))
    return ar.finalize(True)


def test_list_runs_and_read_manifest(tmp_path):
    _archive(tmp_path, "runA", cost_usd=0.02, model="m")
    runs = list_runs(tmp_path)
    assert [m["run_id"] for m in runs] == ["runA"]
    assert read_manifest(tmp_path, "runA")["nodes"][0]["cost_usd"] == 0.02


def test_list_runs_missing_dir(tmp_path):
    assert list_runs(tmp_path / "nope") == []


def test_read_manifest_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        read_manifest(tmp_path, "ghost")


def test_cli_archive_ls(tmp_path, capsys):
    import cadora.cli as cli

    _archive(tmp_path, "runX", cost_usd=0.03)
    cli.main(["archive", "ls", "--archive-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "runX" in out and "claude" in out and "0.03" in out


def test_cli_archive_show(tmp_path, capsys):
    import cadora.cli as cli

    _archive(
        tmp_path, "runY", cost_usd=0.05, model="sonnet",
        meta={"funding_resolved": "subscription", "num_turns": 5},
    )
    cli.main(["archive", "show", "runY", "--archive-dir", str(tmp_path)])
    out = capsys.readouterr().out
    assert "runY" in out and "sonnet" in out and "funding=subscription" in out and "n1" in out


def test_cli_archive_show_distinguishes_missing_prerequisite(tmp_path, capsys):
    ar = RunArchive(tmp_path, "blocked", "codex", "aidlc")
    ar.record(
        ExecutionResult(node_id="n1", ok=True, exit_code=0),
        GateResult(
            name="build-test",
            passed=False,
            status="blocked_prerequisite",
            missing_prerequisites=["pytest-cov"],
        ),
    )
    ar.finalize(False)

    import cadora.cli as cli

    cli.main(["archive", "show", "blocked", "--archive-dir", str(tmp_path)])
    assert "gate:build-test BLOCKED_PREREQUISITE" in capsys.readouterr().out


def test_cli_archive_show_includes_structured_review(tmp_path, capsys):
    ar = RunArchive(tmp_path, "reviewed", "codex", "aidlc")
    ar.record(
        ExecutionResult(node_id="n1", ok=True, exit_code=0),
        reviews=[ReviewResult(REVIEW_APPROVE, timestamp="2026-06-23T00:00:00+00:00")],
    )
    ar.finalize(True)

    import cadora.cli as cli

    cli.main(["archive", "show", "reviewed", "--archive-dir", str(tmp_path)])
    output = capsys.readouterr().out
    assert "review:approve" in output
    assert "human-review.md" in output


def test_cli_archive_show_missing(tmp_path):
    import cadora.cli as cli

    with pytest.raises(SystemExit):
        cli.main(["archive", "show", "ghost", "--archive-dir", str(tmp_path)])


def test_cli_archive_ls_empty(tmp_path, capsys):
    import cadora.cli as cli

    cli.main(["archive", "ls", "--archive-dir", str(tmp_path / "empty")])
    assert "no runs" in capsys.readouterr().out
