"""B1 feasibility spike — Quick Desktop as MCP client against cadora-mcp (stdio).

Proves: a front-end (modelled here as a fake Quick Desktop client) can call all 5
MCP tools (start_run, review_gate, submit_review, get_artifact, run_status) and
render artifacts for human review — without touching the server contract.

Uses the same pattern as test_mcp.py (RunSession + ReviewChannel directly), since
Quick Desktop would connect over stdio MCP which wraps the same session layer.
"""

import json
import time
from pathlib import Path

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.mcp.session import RunSession
from cadora.review import REVIEW_APPROVE, REVIEW_REQUEST_CHANGES, ReviewResult
from cadora.topology import Node, Topology


class FakeInceptionExecutor(NodeExecutor):
    """Simulates an executor that writes AI-DLC docs (Inception phase output)."""

    name = "fake-inception"

    def run(self, node, prompt, *, cwd, env=None):
        # Write a fake artifact so get_artifact can read it
        docs = Path(cwd) / "aidlc-docs"
        docs.mkdir(parents=True, exist_ok=True)
        artifact = docs / f"{node.id}.md"
        artifact.write_text(f"# {node.id}\n\nGenerated content for {node.role}.\n")
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0,
            text=f"Completed {node.id}", cost_usd=0.02,
        )


def _hitl_topology() -> Topology:
    """A 3-node topology: two inception review gates + one construction node."""
    return Topology(
        name="quick-spike",
        nodes=[
            Node(id="requirements", role="requirements-analysis", prompt="Analyze.", review=True),
            Node(id="design", role="application-design", prompt="Design.",
                 depends_on=["requirements"], review=True),
            Node(id="build", role="code-generation", prompt="Build.",
                 depends_on=["design"], review=False),
        ],
    )


class QuickDesktopClient:
    """Simulates Quick Desktop acting as an MCP client over the cadora-mcp seam.

    In production, Quick Desktop would call these via MCP stdio JSON-RPC.
    Here we call RunSession directly (same contract the MCP server exposes).
    """

    def __init__(self, session: RunSession):
        self.session = session
        self.reviewed_nodes: list[str] = []
        self.rendered_artifacts: list[str] = []

    def poll_and_review(self, timeout: float = 5.0) -> None:
        """Simulate Quick Desktop's event loop: poll for gates, render, decide."""
        deadline = time.time() + timeout
        while self.session.is_running() and time.time() < deadline:
            # Tool: review_gate(run_id) — check for pending review
            request = self.session.pending_review()
            if request is not None:
                # Tool: get_artifact(run_id, path) — render each artifact
                for art_path in request.artifacts:
                    content = self._get_artifact(art_path)
                    self.rendered_artifacts.append(content)

                # Simulate decision card → user approves
                self.reviewed_nodes.append(request.node_id)
                # Tool: submit_review(run_id, decision, comments)
                self.session.submit_review(
                    ReviewResult(REVIEW_APPROVE, f"LGTM — {request.node_id}")
                )
            else:
                time.sleep(0.01)

    def get_status(self) -> dict:
        """Tool: run_status(run_id)"""
        return {
            "running": self.session.is_running(),
            "result_path": str(self.session.result_path) if self.session.result_path else None,
            "error": self.session.error,
        }

    def _get_artifact(self, rel_path: str) -> str:
        """Tool: get_artifact(run_id, path)"""
        cwd = self.session.run_kwargs.get("cwd", ".")
        return (Path(cwd) / rel_path).read_text()


def test_quick_client_calls_all_five_tools(tmp_path):
    """B1 acceptance: Quick Desktop can start_run, review_gate, submit_review, get_artifact, run_status."""
    # Tool 1: start_run — create and start the session
    session = RunSession(
        _hitl_topology(), FakeInceptionExecutor(),
        run_id="quick-b1", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    ).start()

    # Quick Desktop client drives the review loop (tools 2-4)
    client = QuickDesktopClient(session)
    client.poll_and_review(timeout=5.0)
    session.join(2)

    # Tool 5: run_status
    status = client.get_status()
    assert status["error"] is None
    assert status["running"] is False
    assert status["result_path"] is not None

    # Verify: both review gates were hit and artifacts rendered
    assert client.reviewed_nodes == ["requirements", "design"]
    assert len(client.rendered_artifacts) >= 2
    assert "# requirements" in client.rendered_artifacts[0]

    # Verify archive integrity
    manifest = json.loads((tmp_path / "runs" / "quick-b1" / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert len(manifest["nodes"]) == 3
    by_id = {n["node_id"]: n for n in manifest["nodes"]}
    assert by_id["requirements"]["human_reviews"][0]["decision"] == REVIEW_APPROVE
    assert by_id["design"]["human_reviews"][0]["decision"] == REVIEW_APPROVE
    assert "human_reviews" not in by_id["build"]


def test_quick_client_request_changes_triggers_revision(tmp_path):
    """Quick Desktop can request changes — the run re-executes the node and re-presents the gate."""
    session = RunSession(
        Topology(name="rev", nodes=[
            Node(id="req", role="requirements", prompt="Req.", review=True),
        ]),
        FakeInceptionExecutor(),
        run_id="quick-rev", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    ).start()

    decisions = []
    deadline = time.time() + 5
    while session.is_running() and time.time() < deadline:
        request = session.pending_review()
        if request is not None:
            decisions.append(request.node_id)
            if len(decisions) == 1:
                # First pass: request changes
                session.submit_review(
                    ReviewResult(REVIEW_REQUEST_CHANGES, "Add rate limiting requirement")
                )
            else:
                # Second pass: approve
                session.submit_review(ReviewResult(REVIEW_APPROVE, "Good now"))
                break
        else:
            time.sleep(0.01)
    session.join(2)

    assert session.error is None
    assert len(decisions) == 2  # gate fired twice (revision cycle)
    manifest = json.loads((tmp_path / "runs" / "quick-rev" / "manifest.json").read_text())
    assert manifest["ok"] is True
    reviews = manifest["nodes"][0]["human_reviews"]
    assert reviews[0]["decision"] == REVIEW_REQUEST_CHANGES
    assert reviews[1]["decision"] == REVIEW_APPROVE


def test_quick_client_abort_stops_run(tmp_path):
    """Quick Desktop abort decision stops the run immediately."""
    from cadora.review import REVIEW_ABORT

    session = RunSession(
        Topology(name="abt", nodes=[
            Node(id="req", role="requirements", prompt="Req.", review=True),
            Node(id="design", role="design", prompt="D.", depends_on=["req"], review=True),
        ]),
        FakeInceptionExecutor(),
        run_id="quick-abort", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    ).start()

    deadline = time.time() + 5
    while session.is_running() and time.time() < deadline:
        request = session.pending_review()
        if request is not None:
            session.submit_review(ReviewResult(REVIEW_ABORT, "Stopping — wrong direction"))
            break
        time.sleep(0.01)
    session.join(2)

    assert session.error is not None
    assert "abort" in session.error.lower()
