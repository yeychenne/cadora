"""HITL review-gate tests using structured decisions and a fake executor."""

import json

import pytest

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.review import (
    REVIEW_ABORT,
    REVIEW_APPROVE,
    REVIEW_REQUEST_CHANGES,
    ReviewResult,
)
from cadora.runner import MAX_REVIEW_REVISIONS, _stdin_review, run_topology
from cadora.topology import Node, Topology


class FakeExecutor(NodeExecutor):
    name = "fake"

    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append((node.id, prompt))
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"out-{node.id}-{len(self.calls)}",
            cost_usd=0.01,
            meta={"funding_resolved": "subscription"},
        )


def _chain() -> Topology:
    return Topology(
        name="t",
        nodes=[
            Node(id="requirements", prompt="REQ", review=True),
            Node(id="design", prompt="DESIGN", depends_on=["requirements"]),
            Node(id="construction", prompt="BUILD", depends_on=["design"], review=True),
        ],
    )


def test_hitl_reviews_only_explicit_nodes(tmp_path):
    ex = FakeExecutor()
    reviewed: list[str] = []

    def review_fn(node, node_cwd):
        reviewed.append(node.id)
        return ReviewResult(REVIEW_APPROVE, f"approved-{node.id}")

    out = run_topology(
        _chain(),
        ex,
        run_id="h1",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=review_fn,
    )

    assert reviewed == ["requirements", "construction"]
    assert "approved-requirements" in dict(ex.calls)["design"]
    manifest = json.loads((out / "manifest.json").read_text())
    by_id = {node["node_id"]: node for node in manifest["nodes"]}
    assert by_id["requirements"]["human_reviews"][0]["decision"] == REVIEW_APPROVE
    assert "human_reviews" not in by_id["design"]
    assert "Decision: `approve`" in (
        out / "requirements" / "human-review.md"
    ).read_text()


def test_request_changes_reruns_same_stage_before_downstream(tmp_path):
    ex = FakeExecutor()
    decisions = iter(
        [
            ReviewResult(REVIEW_REQUEST_CHANGES, "Add acceptance criteria."),
            ReviewResult(REVIEW_APPROVE),
            ReviewResult(REVIEW_APPROVE),
        ]
    )

    out = run_topology(
        _chain(),
        ex,
        run_id="revise",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=lambda node, cwd: next(decisions),
    )

    assert [node_id for node_id, _ in ex.calls] == [
        "requirements",
        "requirements",
        "design",
        "construction",
    ]
    assert "Add acceptance criteria." in ex.calls[1][1]
    manifest = json.loads((out / "manifest.json").read_text())
    reviews = manifest["nodes"][0]["human_reviews"]
    assert [review["decision"] for review in reviews] == [
        REVIEW_REQUEST_CHANGES,
        REVIEW_APPROVE,
    ]
    assert manifest["nodes"][0]["cost_usd"] == 0.02
    assert len(manifest["nodes"][0]["attempts"]) == 2
    assert (out / "requirements" / "attempts" / "1-output.txt").is_file()


def test_abort_stops_before_downstream_and_is_archived(tmp_path):
    with pytest.raises(SystemExit, match="human review aborted"):
        run_topology(
            _chain(),
            FakeExecutor(),
            run_id="abort",
            cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"),
            hitl=True,
            review_fn=lambda node, cwd: ReviewResult(REVIEW_ABORT, "Not ready."),
        )

    manifest = json.loads(
        (tmp_path / "runs" / "abort" / "manifest.json").read_text()
    )
    assert manifest["ok"] is False
    assert manifest["nodes"][0]["human_reviews"][0]["decision"] == REVIEW_ABORT


def test_revision_limit_fails_closed(tmp_path):
    with pytest.raises(SystemExit, match="revision limit exceeded"):
        run_topology(
            _chain(),
            FakeExecutor(),
            run_id="limit",
            cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"),
            hitl=True,
            review_fn=lambda node, cwd: ReviewResult(
                REVIEW_REQUEST_CHANGES, "Revise again."
            ),
        )

    manifest = json.loads(
        (tmp_path / "runs" / "limit" / "manifest.json").read_text()
    )
    assert len(manifest["nodes"][0]["human_reviews"]) == MAX_REVIEW_REVISIONS


def test_no_hitl_skips_declared_reviews(tmp_path):
    called: list[str] = []
    out = run_topology(
        _chain(),
        FakeExecutor(),
        run_id="off",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=False,
        review_fn=lambda node, cwd: called.append(node.id),
    )
    assert called == []
    manifest = json.loads((out / "manifest.json").read_text())
    assert all("human_reviews" not in node for node in manifest["nodes"])


def test_noninteractive_stdin_aborts_instead_of_approving(tmp_path):
    result = _stdin_review(Node(id="requirements", review=True), str(tmp_path))
    assert result.decision == REVIEW_ABORT
    assert "not a TTY" in result.comments
