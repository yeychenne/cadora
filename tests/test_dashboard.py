"""Tests for the lightweight local dashboard server."""

from __future__ import annotations
from importlib import resources

from cadora.archive import RunArchive
from cadora.dashboard.server import (
    _node_artifacts,
    _run_payload,
    _runs_payload,
    _safe_artifact_path,
    _safe_segment,
)
from cadora.executors.base import ExecutionResult


def _archive(root):
    ar = RunArchive(root, "run-20260626-091500", "claude", "aidlc")
    ar.record(
        ExecutionResult(
            node_id="requirements",
            ok=True,
            exit_code=0,
            text="done",
            usage={"input_tokens": 1, "output_tokens": 2},
            cost_usd=0.03,
            model="sonnet",
        )
    )
    ar.finalize(True)


def test_dashboard_static_index_is_packaged():
    body = resources.files("cadora.dashboard.static").joinpath("index.html").read_text()

    assert "Cadora" in body
    assert "Conductor runs" in body


def test_dashboard_run_payloads(tmp_path):
    _archive(tmp_path)
    runs = _runs_payload(tmp_path)
    run = _run_payload(tmp_path, "run-20260626-091500")
    output = (tmp_path / "run-20260626-091500" / "requirements" / "output.txt").read_text()

    assert runs["runs"][0]["run_id"] == "run-20260626-091500"
    assert run["manifest"]["nodes"][0]["cost_usd"] == 0.03
    assert output == "done"


def test_safe_segment_blocks_path_traversal():
    import pytest

    for bad in ["..", "../../etc", "a/b", "."]:
        with pytest.raises(ValueError):
            _safe_segment(bad)
    assert _safe_segment("run-20260626-091500") == "run-20260626-091500"


def test_node_artifacts_lists_and_safely_loads_files(tmp_path):
    import pytest

    _archive(tmp_path)
    node_dir = tmp_path / "run-20260626-091500" / "requirements"
    artifact_dir = node_dir / "aidlc-docs" / "inception"
    artifact_dir.mkdir(parents=True)
    (artifact_dir / "requirements.md").write_text("# Requirements")

    payload = _node_artifacts(node_dir)
    assert "aidlc-docs/inception/requirements.md" in {
        artifact["path"] for artifact in payload["artifacts"]
    }

    artifact = _safe_artifact_path(node_dir, "aidlc-docs/inception/requirements.md")
    assert artifact.read_text() == "# Requirements"
    with pytest.raises(ValueError):
        _safe_artifact_path(node_dir, "../manifest.json")
