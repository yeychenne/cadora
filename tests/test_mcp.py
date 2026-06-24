"""Tests for the MCP interface seam core — ReviewChannel + RunSession (no MCP SDK required)."""

import json
import threading
import time

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.mcp.channel import ReviewChannel, ReviewRequest
from cadora.mcp.session import RunSession
from cadora.review import REVIEW_APPROVE, ReviewResult
from cadora.topology import Node, Topology


class FakeExecutor(NodeExecutor):
    name = "fake"

    def run(self, node, prompt, *, cwd, env=None):
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}",
            cost_usd=0.01, meta={"funding_resolved": "subscription"},
        )


def test_channel_round_trip():
    channel = ReviewChannel()
    captured = {}

    def consumer():
        captured["result"] = channel.request_review(ReviewRequest("n1", "/tmp/d", ["a.md"]))

    t = threading.Thread(target=consumer)
    t.start()
    for _ in range(200):  # wait for the request to surface
        if channel.pending():
            break
        time.sleep(0.005)
    assert channel.pending() is not None
    assert channel.pending().node_id == "n1"
    channel.respond(ReviewResult(REVIEW_APPROVE, "looks good"))
    t.join(2)
    assert captured["result"].decision == REVIEW_APPROVE
    assert channel.pending() is None  # cleared after response


def test_channel_respond_without_pending_raises():
    channel = ReviewChannel()
    try:
        channel.respond(ReviewResult(REVIEW_APPROVE))
        raise AssertionError("expected RuntimeError")
    except RuntimeError:
        pass


def _chain() -> Topology:
    return Topology(
        name="t",
        nodes=[
            Node(id="requirements", prompt="REQ", review=True),
            Node(id="design", prompt="DESIGN", depends_on=["requirements"]),
        ],
    )


def test_run_session_drives_hitl_through_channel(tmp_path):
    session = RunSession(
        _chain(), FakeExecutor(),
        run_id="mcp1", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
    ).start()

    reviewed = []
    deadline = time.time() + 5
    while session.is_running() and time.time() < deadline:
        request = session.pending_review()
        if request is not None:
            reviewed.append(request.node_id)
            session.submit_review(ReviewResult(REVIEW_APPROVE, f"ok-{request.node_id}"))
        else:
            time.sleep(0.01)
    session.join(2)

    assert session.error is None
    assert reviewed == ["requirements"]  # the only review:true node was surfaced via the channel
    manifest = json.loads((tmp_path / "runs" / "mcp1" / "manifest.json").read_text())
    assert manifest["ok"] is True
    by_id = {n["node_id"]: n for n in manifest["nodes"]}
    assert by_id["requirements"]["human_reviews"][0]["decision"] == REVIEW_APPROVE
    assert "human_reviews" not in by_id["design"]
