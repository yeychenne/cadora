"""``--resume-from`` verifies the workspace instead of trusting it blindly.

A resume skips upstream nodes and trusts their artifacts already in ``--cwd``. These tests pin the
honesty fix: a run records a workspace fingerprint, and a later resume refuses (or, with
``--allow-drift``, records) any drift against it — so a resumed run can never silently certify
gates over source that never matched the run it claims to continue.
"""

import json
from pathlib import Path

import pytest

from cadora.archive import RunArchive
from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.provenance import (
    diff_fingerprints,
    fingerprint_workspace,
    tree_sha256,
)
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class WritingExecutor(NodeExecutor):
    """A fake backend that writes one deterministic file per node into the workspace."""

    name = "writer"

    def __init__(self, files: dict[str, tuple[str, str]]):
        self.files = files  # node_id -> (relpath, content)

    def run(self, node, prompt, *, cwd, env=None):
        rel, content = self.files[node.id]
        path = Path(cwd) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"wrote {rel}",
            cost_usd=0.0,
            meta={"funding_resolved": "subscription"},
        )


def _chain() -> Topology:
    # a -> b: resuming from "b" skips "a" and trusts a's artifact already in the workspace.
    return Topology(
        name="chain",
        nodes=[Node(id="a", prompt="A"), Node(id="b", prompt="B", depends_on=["a"])],
    )


def _seed(ws: Path, runs: Path) -> None:
    ws.mkdir(parents=True, exist_ok=True)
    ex = WritingExecutor({"a": ("a.txt", "A1"), "b": ("b.txt", "B1")})
    run_topology(_chain(), ex, run_id="run-1", cwd=str(ws), archive_root=str(runs))


# --- unit: fingerprint + diff -------------------------------------------------------------------


def test_fingerprint_excludes_caches_symlinks_and_archive(tmp_path):
    (tmp_path / "keep.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "also.py").write_text("y")
    for junk_dir in (".venv", ".git", ".pytest_cache", "node_modules"):
        (tmp_path / junk_dir).mkdir()
        (tmp_path / junk_dir / "junk").write_text("nope")
    # An archive nested under the workspace must not fingerprint itself.
    runs = tmp_path / "runs"
    (runs / "run-x").mkdir(parents=True)
    (runs / "run-x" / "manifest.json").write_text("{}")
    # A symlink is skipped (could point outside the tree / loop).
    (tmp_path / "link.py").symlink_to(tmp_path / "keep.py")

    fp = fingerprint_workspace(tmp_path, archive_root=runs)

    assert set(fp) == {"keep.py", "sub/also.py"}
    assert all("junk" not in v for v in fp.values())  # cache contents never read


def test_fingerprint_includes_node_artifacts_but_not_pycache(tmp_path):
    # aidlc-docs holds AI-DLC node artifacts — a resume trusts them, so they MUST be fingerprinted
    # (unlike the integrity scan, which skips that dir).
    (tmp_path / "aidlc-docs" / "inception").mkdir(parents=True)
    (tmp_path / "aidlc-docs" / "inception" / "requirements.md").write_text("reqs")
    # __pycache__ .pyc files are regenerated on every run — excluding them prevents false drift.
    (tmp_path / "pkg" / "__pycache__").mkdir(parents=True)
    (tmp_path / "pkg" / "__pycache__" / "m.cpython-312.pyc").write_bytes(b"\x00\x01")
    (tmp_path / "pkg" / "m.py").write_text("x")

    fp = fingerprint_workspace(tmp_path)

    assert "aidlc-docs/inception/requirements.md" in fp
    assert "pkg/m.py" in fp
    assert not any("__pycache__" in k for k in fp)


def test_tree_sha_is_order_independent(tmp_path):
    a = {"x": "1", "y": "2"}
    b = {"y": "2", "x": "1"}
    assert tree_sha256(a) == tree_sha256(b)
    assert tree_sha256({"x": "1"}) != tree_sha256({"x": "2"})


def test_diff_detects_added_removed_modified():
    base = {"a": "1", "b": "2", "c": "3"}
    cur = {"a": "1", "b": "9", "d": "4"}  # b modified, c removed, d added
    drift = diff_fingerprints(base, cur, baseline_run="run-1")
    assert drift.modified == ["b"]
    assert drift.removed == ["c"]
    assert drift.added == ["d"]
    assert drift.has_drift and drift.count == 3
    assert drift.baseline_run == "run-1"
    assert drift.as_dict()["drift"] is True


def test_diff_clean_has_no_drift():
    fp = {"a": "1", "b": "2"}
    drift = diff_fingerprints(fp, dict(fp), baseline_run="run-1")
    assert not drift.has_drift
    assert drift.as_dict()["drift"] is False


# --- integration: run records provenance, resume verifies it ------------------------------------


def test_failed_run_still_records_baseline(tmp_path):
    # The run you resume is usually a FAILED one, so finalize(False) must snapshot the workspace
    # too — otherwise a resume has no baseline for the exact run it continues.
    ws, runs = tmp_path / "ws", tmp_path / "runs"
    ws.mkdir()
    (ws / "f.txt").write_text("x")
    archive = RunArchive(str(runs), "run-1", "writer", "chain")
    archive.track_workspace(str(ws), str(runs))
    archive.finalize(False)  # a blocking-failure exit
    manifest = json.loads((runs / "run-1" / "workspace-manifest.json").read_text())
    assert "f.txt" in manifest["files"]


def test_run_writes_workspace_manifest(tmp_path):
    ws, runs = tmp_path / "ws", tmp_path / "runs"
    _seed(ws, runs)
    manifest = json.loads((runs / "run-1" / "workspace-manifest.json").read_text())
    assert set(manifest["files"]) == {"a.txt", "b.txt"}
    assert manifest["file_count"] == 2
    assert manifest["tree_sha256"]


def test_resume_refuses_on_drift(tmp_path):
    ws, runs = tmp_path / "ws", tmp_path / "runs"
    _seed(ws, runs)
    (ws / "a.txt").write_text("A-DRIFTED")  # the skipped node's artifact changed underneath us

    with pytest.raises(SystemExit) as ei:
        run_topology(
            _chain(),
            WritingExecutor({"a": ("a.txt", "A1"), "b": ("b.txt", "B1")}),
            run_id="run-2",
            cwd=str(ws),
            archive_root=str(runs),
            resume_from="b",
        )
    msg = str(ei.value)
    assert "drift" in msg.lower()
    assert "a.txt" in msg
    # It failed CLOSED: the resumed node never ran, no manifest was finalized.
    assert not (runs / "run-2" / "manifest.json").is_file()


def test_resume_allows_drift_and_records_it(tmp_path):
    ws, runs = tmp_path / "ws", tmp_path / "runs"
    _seed(ws, runs)
    (ws / "a.txt").write_text("A-DRIFTED")

    out = run_topology(
        _chain(),
        WritingExecutor({"a": ("a.txt", "A1"), "b": ("b.txt", "B1")}),
        run_id="run-2",
        cwd=str(ws),
        archive_root=str(runs),
        resume_from="b",
        allow_drift=True,
    )
    resume = json.loads((out / "manifest.json").read_text())["resume"]
    assert resume["allow_drift"] is True
    assert resume["workspace_drift"]["drift"] is True
    assert "a.txt" in resume["workspace_drift"]["modified"]


def test_resume_clean_passes_and_records_no_drift(tmp_path):
    ws, runs = tmp_path / "ws", tmp_path / "runs"
    _seed(ws, runs)  # workspace untouched between runs

    out = run_topology(
        _chain(),
        WritingExecutor({"a": ("a.txt", "A1"), "b": ("b.txt", "B1")}),
        run_id="run-2",
        cwd=str(ws),
        archive_root=str(runs),
        resume_from="b",
    )
    resume = json.loads((out / "manifest.json").read_text())["resume"]
    assert resume["workspace_drift"]["drift"] is False


def test_resume_without_prior_manifest_trusts(tmp_path):
    # No prior run to verify against -> proceed on trust (preserve behavior, but say so).
    ws, runs = tmp_path / "ws", tmp_path / "runs"
    ws.mkdir(parents=True)
    (ws / "a.txt").write_text("A1")  # pretend a's artifact is already here
    out = run_topology(
        _chain(),
        WritingExecutor({"a": ("a.txt", "A1"), "b": ("b.txt", "B1")}),
        run_id="run-1",
        cwd=str(ws),
        archive_root=str(runs),
        resume_from="b",
    )
    assert (out / "manifest.json").is_file()
    assert json.loads((out / "manifest.json").read_text())["resume"]["workspace_drift"] is None
