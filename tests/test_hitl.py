"""HITL review-gate tests using structured decisions and a fake executor."""

import json
import time

import pytest

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.review import (
    REVIEW_ABORT,
    REVIEW_APPROVE,
    REVIEW_REQUEST_CHANGES,
    ReviewResult,
)
from cadora.runner import (
    MAX_REVIEW_REVISIONS,
    _changed_docs,
    _doc_snapshot,
    _invoke_review,
    _stdin_review,
    run_topology,
)
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


# --- scoped document surfacing at the HITL gate ---------------------------------


def test_changed_docs_detects_new_and_modified(tmp_path):
    docs = tmp_path / "aidlc-docs"
    docs.mkdir()
    (docs / "requirements.md").write_text("v1")
    before = _doc_snapshot(str(tmp_path))
    (docs / "design.md").write_text("brand new")            # new
    (docs / "requirements.md").write_text("v2")             # modified
    changed = _changed_docs(str(tmp_path), before)
    assert ("aidlc-docs/design.md", "new") in changed
    assert ("aidlc-docs/requirements.md", "modified") in changed
    assert len(changed) == 2  # nothing else surfaced


def test_changed_docs_ignores_untouched_documents(tmp_path):
    docs = tmp_path / "aidlc-docs"
    docs.mkdir()
    (docs / "a.md").write_text("same")
    before = _doc_snapshot(str(tmp_path))
    assert _changed_docs(str(tmp_path), before) == []  # nothing written since the snapshot


def test_stdin_review_surfaces_stage_documents(tmp_path, capsys):
    docs = tmp_path / "aidlc-docs"
    docs.mkdir()
    (docs / "design.md").write_text("# Architecture\nThe design decision is X.")
    # stdin is not a TTY under pytest -> aborts, but must surface the docs first.
    _stdin_review(
        Node(id="architect", review=True),
        str(tmp_path),
        documents=[("aidlc-docs/design.md", "new")],
    )
    err = capsys.readouterr().err
    assert "1 document(s) to review" in err
    assert "aidlc-docs/design.md" in err
    assert "The design decision is X." in err  # content preview, not just the path


def test_invoke_review_is_backward_compatible():
    node = Node(id="n", review=True)
    two_arg_calls: list[tuple[str, str]] = []
    three_arg_docs: list = []

    _invoke_review(
        lambda n, c: two_arg_calls.append((n.id, c)) or ReviewResult(REVIEW_APPROVE),
        node,
        "/cwd",
        [("aidlc-docs/x.md", "new")],
    )

    def three_arg(n, c, documents):
        three_arg_docs.append(documents)
        return ReviewResult(REVIEW_APPROVE)

    _invoke_review(three_arg, node, "/cwd", [("aidlc-docs/x.md", "new")])

    assert two_arg_calls == [("n", "/cwd")]  # legacy 2-arg callback still works
    assert three_arg_docs == [[("aidlc-docs/x.md", "new")]]  # 3-arg callback gets scoped docs


def test_channel_scopes_artifacts_to_stage_documents(tmp_path):
    import threading

    from cadora.mcp.channel import ReviewChannel, channel_review_fn

    channel = ReviewChannel()
    review_fn = channel_review_fn(channel)
    documents = [("aidlc-docs/design.md", "new"), ("aidlc-docs/nfr.md", "modified")]
    holder: dict = {}

    def run():
        holder["result"] = review_fn(Node(id="architect", review=True), str(tmp_path), documents)

    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(200):
        if channel.pending() is not None:
            break
        time.sleep(0.01)
    request = channel.pending()
    assert request is not None
    # Scoped to THIS stage's documents, not the whole aidlc-docs tree.
    assert request.artifacts == ["aidlc-docs/design.md", "aidlc-docs/nfr.md"]
    channel.respond(ReviewResult(REVIEW_APPROVE))
    thread.join(timeout=2)
    assert holder["result"].decision == REVIEW_APPROVE
