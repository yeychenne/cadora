"""HITL MCP-surface hardening — fail-closed + fail-soft guarantees for the review tools.

The runner and the file/stdin review surfaces already degrade safely on bad or absent input; these
tests lock the MCP tool path to the same bar, covering its three highest-risk seams:

  W1  a gate with no timeout pins the run thread forever if a client starts a run and never submits
  W2  ``submit_review`` surfaced a raw ``ValueError`` on a bad decision / empty-comment request_changes
  W3  a double-submit (or a submit with nothing pending) surfaced a raw ``RuntimeError``
"""

import asyncio
import json
import time

import pytest

pytest.importorskip("mcp")  # the server needs the optional cadora[mcp] extra

from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from cadora.executors.base import ExecutionResult, NodeExecutor  # noqa: E402
from cadora.mcp.server import build_app  # noqa: E402
from cadora.mcp.session import RunSession  # noqa: E402
from cadora.review import REVIEW_APPROVE, ReviewResult  # noqa: E402
from cadora.topology import Node, Topology  # noqa: E402


class FakeExecutor(NodeExecutor):
    name = "fake"

    def run(self, node, prompt, *, cwd, env=None):
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}", cost_usd=0.0,
        )


def _fake_app():
    return build_app(executor_factory=lambda name: FakeExecutor())


def _result(call_tool_result) -> dict:
    """This SDK serializes a dict tool-return as JSON text (structuredContent is unset)."""
    return json.loads(call_tool_result.content[0].text)


def _review_topology(path) -> str:
    topo = path / "r.yaml"
    topo.write_text("name: r\nnodes:\n  - id: requirements\n    prompt: REQ\n    review: true\n")
    return str(topo)


def _one_review_node() -> Topology:
    return Topology(
        name="r",
        nodes=[Node(id="requirements", role="requirements", prompt="REQ", review=True)],
    )


async def _await_gate(client, run_id):
    for _ in range(500):
        gate = _result(await client.call_tool("review_gate", {"run_id": run_id}))
        if gate.get("pending"):
            return gate
        await asyncio.sleep(0.01)
    raise AssertionError("review gate never became pending")


async def _await_done(client, run_id, *, tries=500, interval=0.01):
    for _ in range(tries):
        st = _result(await client.call_tool("run_status", {"run_id": run_id}))
        if not st["running"]:
            return st
        await asyncio.sleep(interval)
    return {"error": "run did not finish in time", "running": True}


def test_submit_review_rejects_bad_input_without_consuming_the_gate(tmp_path):
    """W2: an invalid decision or an empty-comment ``request_changes`` returns ``{"error": ...}`` and
    LEAVES the gate pending, so the operator can still land a valid decision. No raw traceback."""

    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            _result(await client.call_tool("start_run", {
                "topology": _review_topology(tmp_path), "run_id": "w2",
                "cwd": str(tmp_path), "archive_dir": str(tmp_path / "runs"),
            }))
            await _await_gate(client, "w2")

            bad = _result(await client.call_tool(
                "submit_review", {"run_id": "w2", "decision": "maybe"}))
            assert "error" in bad and "maybe" in bad["error"]

            empty = _result(await client.call_tool(
                "submit_review",
                {"run_id": "w2", "decision": "request_changes", "comments": "   "}))
            assert "error" in empty and "comment" in empty["error"].lower()

            # both rejections were non-destructive — the gate is still awaiting a decision
            still = _result(await client.call_tool("review_gate", {"run_id": "w2"}))
            assert still["pending"] and still["node_id"] == "requirements"

            ok = _result(await client.call_tool(
                "submit_review",
                {"run_id": "w2", "decision": "approve", "comments": "lgtm"}))
            assert ok["submitted"] == "approve"
            return await _await_done(client, "w2")

    status = asyncio.run(_run())
    assert status["error"] is None
    manifest = json.loads((tmp_path / "runs" / "w2" / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert manifest["nodes"][0]["human_reviews"][-1]["decision"] == "approve"


def test_submit_review_when_nothing_pending_returns_error(tmp_path):
    """W3: a second submit once the gate is resolved (a double-submit) degrades to a graceful
    'no review is pending' error rather than a raw ``RuntimeError``."""

    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            _result(await client.call_tool("start_run", {
                "topology": _review_topology(tmp_path), "run_id": "w3",
                "cwd": str(tmp_path), "archive_dir": str(tmp_path / "runs"),
            }))
            await _await_gate(client, "w3")
            first = _result(await client.call_tool(
                "submit_review", {"run_id": "w3", "decision": "approve", "comments": "ok"}))
            assert first["submitted"] == "approve"
            await _await_done(client, "w3")  # single node → nothing pending afterwards
            return _result(await client.call_tool(
                "submit_review", {"run_id": "w3", "decision": "approve", "comments": "again"}))

    second = asyncio.run(_run())
    assert "error" in second
    assert "pending" in second["error"].lower()


def test_submit_review_unknown_run_returns_error():
    """W3 sibling: a submit against a run id that was never started degrades gracefully."""

    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            return _result(await client.call_tool(
                "submit_review", {"run_id": "ghost", "decision": "approve"}))

    out = asyncio.run(_run())
    assert "error" in out and "ghost" in out["error"]


def test_mcp_review_timeout_fails_closed(tmp_path):
    """W1: a run whose gate is never answered aborts on ``review_timeout`` instead of pinning the run
    thread forever, and records the timeout in the evidence pack."""

    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            _result(await client.call_tool("start_run", {
                "topology": _review_topology(tmp_path), "run_id": "w1",
                "cwd": str(tmp_path), "archive_dir": str(tmp_path / "runs"),
                "review_timeout": 0.3,
            }))
            # never submit — the gate must expire on its own
            return await _await_done(client, "w1", tries=200, interval=0.05)

    status = asyncio.run(_run())
    assert status["error"] is not None
    assert "abort" in status["error"].lower()
    manifest = json.loads((tmp_path / "runs" / "w1" / "manifest.json").read_text())
    assert manifest["ok"] is False
    review = manifest["nodes"][0]["human_reviews"][-1]
    assert review["decision"] == "abort"
    assert "timed out" in review["comments"].lower()


def test_review_timeout_zero_waits_indefinitely(tmp_path):
    """W1 guard: ``review_timeout=0`` must mean 'wait indefinitely', NOT 'abort immediately'. A
    literal 0-second wait would insta-abort every gate — the dangerous misreading of the knob."""
    session = RunSession(
        _one_review_node(), FakeExecutor(),
        run_id="w1zero", cwd=str(tmp_path), archive_root=str(tmp_path / "runs"),
        review_timeout=0,
    ).start()

    deadline = time.time() + 5
    while session.pending_review() is None and time.time() < deadline:
        time.sleep(0.01)
    assert session.pending_review() is not None

    # sit on the gate well past any sub-second timeout; indefinite means it stays open
    time.sleep(0.3)
    assert session.is_running()
    assert session.pending_review() is not None

    session.submit_review(ReviewResult(REVIEW_APPROVE, "ok"))
    session.join(3)
    assert session.error is None
