"""Integration tests for the cadora-mcp server over a real in-memory MCP client.

Verifies the FastMCP transport binding (cadora/mcp/server.py) that the unit-level channel/session
tests cannot reach: tool registration + schemas, and a full HITL run driven entirely through the
MCP tools. Skipped when the optional `mcp` extra is not installed.
"""

import asyncio
import json
import logging
import socket
import subprocess
import sys
import time

import pytest

pytest.importorskip("mcp")  # the server needs the optional cadora[mcp] extra

from mcp.shared.memory import create_connected_server_and_client_session as connect  # noqa: E402

from cadora.executors.base import ExecutionResult, NodeExecutor  # noqa: E402
from cadora.mcp.server import _TRANSPORTS, build_app  # noqa: E402

logging.getLogger("mcp").setLevel(logging.WARNING)  # quiet the per-request INFO logs


class FakeExecutor(NodeExecutor):
    name = "fake"

    def run(self, node, prompt, *, cwd, env=None):
        return ExecutionResult(
            node_id=node.id, ok=True, exit_code=0, text=f"out-{node.id}",
            cost_usd=0.0, meta={"funding_resolved": "subscription"},
        )


def _fake_app():
    return build_app(executor_factory=lambda name: FakeExecutor())


def _result(call_tool_result) -> dict:
    """This SDK serializes a dict tool-return as JSON text (structuredContent is unset)."""
    return json.loads(call_tool_result.content[0].text)


def _write_topology(path) -> str:
    topo = path / "t.yaml"
    topo.write_text(
        "name: t\n"
        "nodes:\n"
        "  - id: requirements\n    prompt: REQ\n    review: true\n"
        "  - id: design\n    prompt: DESIGN\n    depends_on: [requirements]\n"
    )
    return str(topo)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_transport_names_map_http_to_streamable_http():
    # FastMCP.run() accepts only stdio | sse | streamable-http; the CLI exposes the friendly "http".
    assert _TRANSPORTS["stdio"] == "stdio"
    assert _TRANSPORTS["http"] == "streamable-http"


def test_build_app_carries_host_and_port():
    app = build_app(host="0.0.0.0", port=9123)
    assert app.settings.host == "0.0.0.0"
    assert app.settings.port == 9123


def test_http_transport_serves_over_the_wire():
    """Launch `cadora mcp --transport http` and drive it with a real streamable-HTTP client."""
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "cadora.cli", "mcp", "--transport", "http", "--port", str(port)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 15
        ready = False
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                    ready = True
                    break
            except OSError:
                time.sleep(0.2)
        if not ready:
            pytest.skip("http server did not become ready in time")
        time.sleep(0.5)  # let uvicorn finish binding the ASGI routes

        async def _drive():
            from mcp import ClientSession
            from mcp.client.streamable_http import streamable_http_client

            async with streamable_http_client(f"http://127.0.0.1:{port}/mcp") as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    return sorted(t.name for t in tools.tools)

        assert asyncio.run(_drive()) == [
            "get_artifact", "review_gate", "run_status", "start_run", "submit_review",
        ]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_build_app_registers_all_tools():
    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            tools = await client.list_tools()
            return sorted(t.name for t in tools.tools)

    assert asyncio.run(_run()) == [
        "get_artifact", "review_gate", "run_status", "start_run", "submit_review",
    ]


def test_unknown_run_returns_error():
    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            return _result(await client.call_tool("review_gate", {"run_id": "nope"}))

    assert "error" in asyncio.run(_run())


def test_start_run_registers_topology_gates(tmp_path):
    # Regression: start_run must register the gates a topology references (mirrors `cadora run`),
    # otherwise run_topology fails the run as "unregistered gate(s)".
    topo = tmp_path / "g.yaml"
    topo.write_text("name: g\nnodes:\n  - id: build\n    prompt: B\n    gate: build-test\n")

    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            started = _result(await client.call_tool("start_run", {
                "topology": str(topo), "run_id": "g1", "cwd": str(tmp_path),
                "archive_dir": str(tmp_path / "runs"),
                "gate_cmd": "true", "gate_setup": "off",
            }))
            assert started["status"] == "started"
            for _ in range(500):
                st = _result(await client.call_tool("run_status", {"run_id": "g1"}))
                if not st["running"]:
                    return st
                await asyncio.sleep(0.01)
            return {"error": "timeout"}

    status = asyncio.run(_run())
    assert status["error"] is None  # gate registered + passed ("true"), not "unregistered gate"


def test_mcp_drives_full_hitl_run(tmp_path):
    async def _run():
        topo = _write_topology(tmp_path)
        async with connect(_fake_app()) as client:
            await client.initialize()
            started = _result(await client.call_tool(
                "start_run",
                {"topology": topo, "run_id": "r1", "cwd": str(tmp_path),
                 "archive_dir": str(tmp_path / "runs")},
            ))
            assert started["status"] == "started"

            reviewed = None
            for _ in range(500):
                gate = _result(await client.call_tool("review_gate", {"run_id": "r1"}))
                if gate.get("pending"):
                    reviewed = gate["node_id"]
                    submitted = _result(await client.call_tool(
                        "submit_review",
                        {"run_id": "r1", "decision": "approve", "comments": "ok"},
                    ))
                    assert submitted["submitted"] == "approve"
                    break
                await asyncio.sleep(0.01)

            status = {"running": True}
            for _ in range(500):
                status = _result(await client.call_tool("run_status", {"run_id": "r1"}))
                if not status["running"]:
                    break
                await asyncio.sleep(0.01)
            return reviewed, status

    reviewed, status = asyncio.run(_run())
    assert reviewed == "requirements"  # the review:true gate was surfaced over MCP
    assert status["error"] is None
    manifest = json.loads((tmp_path / "runs" / "r1" / "manifest.json").read_text())
    assert manifest["ok"] is True
    by_id = {n["node_id"]: n for n in manifest["nodes"]}
    assert by_id["requirements"]["human_reviews"][0]["decision"] == "approve"


def test_get_artifact_blocks_path_traversal(tmp_path):
    # Board finding (CISO): ../ must never read outside the run workspace.
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "ok.txt").write_text("fine")
    (tmp_path / "secret.txt").write_text("outside")
    topo = tmp_path / "t.yaml"
    topo.write_text("name: t\nnodes:\n  - id: one\n    prompt: X\n")

    async def _run():
        async with connect(_fake_app()) as client:
            await client.initialize()
            _result(await client.call_tool("start_run", {
                "topology": str(topo), "run_id": "trav1", "cwd": str(ws),
                "archive_dir": str(tmp_path / "runs"),
            }))
            for _ in range(500):
                st = _result(await client.call_tool("run_status", {"run_id": "trav1"}))
                if not st["running"]:
                    break
                await asyncio.sleep(0.01)
            ok = (await client.call_tool("get_artifact",
                                         {"run_id": "trav1", "path": "ok.txt"})).content[0].text
            escaped = (await client.call_tool(
                "get_artifact", {"run_id": "trav1", "path": "../secret.txt"})).content[0].text
            return ok, escaped

    ok, escaped = asyncio.run(_run())
    assert ok == "fine"
    assert "escapes the run workspace" in escaped
    assert "outside" not in escaped


def test_mcp_request_changes_then_approve_reruns_the_node(tmp_path):
    # T1.5/W7: the rejection path end-to-end over the MCP tools — request_changes re-executes the
    # node and re-presents the gate, and a follow-up approve lands the run green. The existing HITL
    # MCP test only covers approve.
    async def _run():
        topo = tmp_path / "one.yaml"
        topo.write_text("name: one\nnodes:\n  - id: requirements\n    prompt: REQ\n    review: true\n")
        async with connect(_fake_app()) as client:
            await client.initialize()
            _result(await client.call_tool("start_run", {
                "topology": str(topo), "run_id": "rc1", "cwd": str(tmp_path),
                "archive_dir": str(tmp_path / "runs")}))
            decisions = []
            for _ in range(500):
                gate = _result(await client.call_tool("review_gate", {"run_id": "rc1"}))
                if gate.get("pending"):
                    if not decisions:
                        _result(await client.call_tool("submit_review", {
                            "run_id": "rc1", "decision": "request_changes", "comments": "add X"}))
                        decisions.append("request_changes")
                    else:
                        _result(await client.call_tool("submit_review", {
                            "run_id": "rc1", "decision": "approve", "comments": "better"}))
                        decisions.append("approve")
                        break
                await asyncio.sleep(0.01)
            for _ in range(500):
                st = _result(await client.call_tool("run_status", {"run_id": "rc1"}))
                if not st["running"]:
                    return decisions, st
                await asyncio.sleep(0.01)
            return decisions, {"error": "timeout"}

    decisions, status = asyncio.run(_run())
    assert decisions == ["request_changes", "approve"]  # gate fired twice, second time approved
    assert status["error"] is None
    manifest = json.loads((tmp_path / "runs" / "rc1" / "manifest.json").read_text())
    assert manifest["ok"] is True
    reviews = manifest["nodes"][0]["human_reviews"]
    assert [r["decision"] for r in reviews] == ["request_changes", "approve"]


def test_mcp_abort_sets_run_status_error_and_stops_before_downstream(tmp_path):
    # T1.5/W7: an abort over MCP surfaces the failure via run_status.error and stops before the
    # downstream node — the whole rejection surface (not just approve) works over the tools.
    async def _run():
        topo = tmp_path / "two.yaml"
        topo.write_text(
            "name: two\nnodes:\n  - id: requirements\n    prompt: REQ\n    review: true\n"
            "  - id: design\n    prompt: DES\n    depends_on: [requirements]\n    review: true\n"
        )
        async with connect(_fake_app()) as client:
            await client.initialize()
            _result(await client.call_tool("start_run", {
                "topology": str(topo), "run_id": "ab1", "cwd": str(tmp_path),
                "archive_dir": str(tmp_path / "runs")}))
            aborted = False
            for _ in range(500):
                gate = _result(await client.call_tool("review_gate", {"run_id": "ab1"}))
                if gate.get("pending"):
                    _result(await client.call_tool("submit_review", {
                        "run_id": "ab1", "decision": "abort", "comments": "wrong direction"}))
                    aborted = True
                    break
                await asyncio.sleep(0.01)
            for _ in range(500):
                st = _result(await client.call_tool("run_status", {"run_id": "ab1"}))
                if not st["running"]:
                    return aborted, st
                await asyncio.sleep(0.01)
            return aborted, {"error": "timeout"}

    aborted, status = asyncio.run(_run())
    assert aborted
    assert status["error"] is not None and "abort" in status["error"].lower()
    manifest = json.loads((tmp_path / "runs" / "ab1" / "manifest.json").read_text())
    assert manifest["ok"] is False
    assert [n["node_id"] for n in manifest["nodes"]] == ["requirements"]  # design never ran
