"""B2 tests — Quick MCP front-end: decision cards, artifact tabs, conversational feedback."""

import json
from pathlib import Path

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.frontends.quick_review import (
    ArtifactTab,
    QuickReviewFrontEnd,
    ReviewCard,
)
from cadora.mcp.session import RunSession
from cadora.review import REVIEW_APPROVE, REVIEW_REQUEST_CHANGES
from cadora.topology import Node, Topology


class DocWritingExecutor(NodeExecutor):
    name = "doc-writer"

    def run(self, node, prompt, *, cwd, env=None):
        docs = Path(cwd) / "aidlc-docs"
        docs.mkdir(parents=True, exist_ok=True)
        (docs / f"{node.id}.md").write_text(
            f"# {node.id}\n\n{prompt}\n\nGenerated for review.\n"
        )
        return ExecutionResult(node_id=node.id, ok=True, exit_code=0, text=f"Done: {node.id}")


def _topo() -> Topology:
    return Topology(name="b2", nodes=[
        Node(id="requirements", prompt="Generate requirements.", review=True),
        Node(id="design", prompt="Generate design.", depends_on=["requirements"], review=True),
    ])


def test_frontend_presents_decision_cards(tmp_path):
    """The front-end surfaces a ReviewCard with correct options at each gate."""
    cards_seen: list[ReviewCard] = []

    def auto_approve(card, tabs):
        cards_seen.append(card)
        return REVIEW_APPROVE, "Approved"

    session = RunSession(
        _topo(), DocWritingExecutor(),
        run_id="b2-cards", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    ).start()

    fe = QuickReviewFrontEnd(session, decision_fn=auto_approve)
    result = fe.drive_review_loop(timeout=5)

    assert result["error"] is None
    assert len(cards_seen) == 2
    assert cards_seen[0].node_id == "requirements"
    assert cards_seen[1].node_id == "design"
    assert "✅ Approve" in cards_seen[0].options[0]
    assert "✏️ Request Changes" in cards_seen[0].options[1]
    assert "🛑 Abort" in cards_seen[0].options[2]


def test_frontend_renders_artifact_tabs(tmp_path):
    """The front-end builds ArtifactTab objects with content from generated docs."""
    tabs_seen: list[list[ArtifactTab]] = []

    def approve_with_tabs(card, tabs):
        tabs_seen.append(tabs)
        return REVIEW_APPROVE, ""

    session = RunSession(
        _topo(), DocWritingExecutor(),
        run_id="b2-tabs", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    ).start()

    fe = QuickReviewFrontEnd(session, decision_fn=approve_with_tabs)
    fe.drive_review_loop(timeout=5)

    # First gate should render the requirements artifact
    assert len(tabs_seen[0]) >= 1
    assert "# requirements" in tabs_seen[0][0].content
    assert tabs_seen[0][0].title  # has a readable title


def test_frontend_conversational_feedback(tmp_path):
    """Request changes feeds comments back; the gate re-fires for the same node."""
    call_count = [0]

    def first_revise_then_approve(card, tabs):
        call_count[0] += 1
        if call_count[0] == 1:
            return REVIEW_REQUEST_CHANGES, "Add security requirements"
        return REVIEW_APPROVE, "Good now"

    session = RunSession(
        Topology(name="fb", nodes=[
            Node(id="req", prompt="Requirements.", review=True),
        ]),
        DocWritingExecutor(),
        run_id="b2-feedback", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    ).start()

    fe = QuickReviewFrontEnd(session, decision_fn=first_revise_then_approve)
    result = fe.drive_review_loop(timeout=5)

    assert result["error"] is None
    assert len(fe.history) == 2
    assert fe.history[0]["decision"] == REVIEW_REQUEST_CHANGES
    assert fe.history[0]["comments"] == "Add security requirements"
    assert fe.history[1]["decision"] == REVIEW_APPROVE

    # Archive records the revision cycle
    manifest = json.loads((tmp_path / "runs" / "b2-feedback" / "manifest.json").read_text())
    reviews = manifest["nodes"][0]["human_reviews"]
    assert reviews[0]["decision"] == REVIEW_REQUEST_CHANGES
    assert reviews[1]["decision"] == REVIEW_APPROVE
