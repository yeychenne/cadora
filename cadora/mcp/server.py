"""MCP server — expose Cadora runs + HITL review over the interface seam.

Any MCP client (Claude Code, Claude Desktop, Codex CLI, or the terminal) can
start an AI-DLC run, fetch the non-code artifacts at each review gate, and approve / request changes
/ abort — the HITL surface becomes pluggable, mirroring how NodeExecutor makes the backend pluggable.

Requires the optional extra:  ``pip install 'cadora[mcp]'``

``build_app()`` constructs the FastMCP app (all tools registered) and is what the integration tests
drive via an in-memory client; ``serve()`` is the thin transport entrypoint behind ``cadora mcp``.
"""

from __future__ import annotations

from pathlib import Path

from cadora.executors import get_executor
from cadora.mcp.session import RunSession
from cadora.review import ReviewResult
from cadora.topology import load_topology

# CLI-friendly transport name -> FastMCP transport name.
_TRANSPORTS = {"stdio": "stdio", "http": "streamable-http", "sse": "sse"}


def build_app(executor_factory=None, *, host="127.0.0.1", port=8000):
    """Build the Cadora FastMCP app with all tools registered.

    ``executor_factory(name) -> NodeExecutor`` defaults to :func:`get_executor`; tests inject a fake
    so ``start_run`` does not spawn a real coding agent. ``host``/``port`` apply to the HTTP
    transport only. Lazily imports the optional ``mcp`` SDK.
    """
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:  # pragma: no cover - only without the extra
        raise SystemExit(
            "the MCP server needs the optional extra:  pip install 'cadora[mcp]'"
        ) from exc

    make_executor = executor_factory or get_executor
    app = FastMCP("cadora", host=host, port=port)
    sessions: dict[str, RunSession] = {}

    @app.tool()
    def start_run(
        topology: str,
        run_id: str,
        cwd: str = ".",
        executor: str = "claude",
        archive_dir: str = "runs",
        gate_cmd: str = "ruff check . && pytest -q",
        gate_setup: str = "auto",
        review_timeout: float = 3600.0,
    ) -> dict:
        """Start an AI-DLC run with HITL review gates; returns the run id.

        Every gate the topology references is registered with ``gate_cmd`` (mirroring ``cadora run``),
        so gated topologies run over MCP rather than failing as "unregistered gate(s)".

        ``review_timeout`` bounds how long each gate waits for ``submit_review`` before failing
        closed to an abort (default 1 h) — a client that starts a run and walks away cannot pin the
        run thread forever. Pass ``0`` to wait indefinitely.
        """
        from cadora.gates import ShellGate

        topo = load_topology(topology)
        gates = {
            name: ShellGate(name, gate_cmd, setup_mode=gate_setup)
            for name in {n.gate for n in topo.nodes if n.gate}
        }
        session = RunSession(
            topo,
            make_executor(executor),
            run_id=run_id,
            cwd=cwd,
            archive_root=archive_dir,
            gates=gates,
            review_timeout=review_timeout,
        ).start()
        sessions[run_id] = session
        return {"run_id": run_id, "status": "started"}

    @app.tool()
    def review_gate(run_id: str) -> dict:
        """Return the review awaiting a decision (node + artifact paths), or none."""
        session = sessions.get(run_id)
        if session is None:
            return {"error": f"unknown run {run_id!r}"}
        request = session.pending_review()
        if request is None:
            return {"pending": False, "running": session.is_running()}
        return {
            "pending": True,
            "node_id": request.node_id,
            "docs_dir": request.docs_dir,
            "artifacts": request.artifacts,
        }

    @app.tool()
    def submit_review(run_id: str, decision: str, comments: str = "", reviewer: str = "") -> dict:
        """Submit the pending review decision: ``approve`` | ``request_changes`` | ``abort``.

        Fails soft: an invalid decision, a ``request_changes`` with no comments, or a submit when no
        gate is pending (already resolved, or a double-submit) returns ``{"error": ...}`` rather than
        raising through the tool call. The file and stdin surfaces already degrade to a safe abort on
        bad input; the MCP tool must not be the one review path that surfaces a raw traceback.
        """
        session = sessions.get(run_id)
        if session is None:
            return {"error": f"unknown run {run_id!r}"}
        try:
            # The bearer token authenticates the CHANNEL, not a person — `reviewer` is the
            # person's self-asserted name, recorded with method=mcp so the evidence says both.
            result = ReviewResult(
                decision, comments, reviewer=reviewer.strip() or None, method="mcp"
            )
        except ValueError as exc:
            return {"error": str(exc)}
        try:
            session.submit_review(result)
        except RuntimeError as exc:
            return {"error": str(exc)}
        return {"submitted": decision}

    @app.tool()
    def get_artifact(run_id: str, path: str) -> str:
        """Read a generated artifact, relative to the run's workspace."""
        session = sessions.get(run_id)
        if session is None:
            return f"error: unknown run {run_id!r}"
        base = Path(session.run_kwargs.get("cwd", ".")).resolve()
        target = (base / path).resolve()
        # Fail closed on traversal: any reachable MCP client can call this tool, so a
        # `../`-shaped path must never read outside the run's workspace.
        if not target.is_relative_to(base):
            return f"error: path {path!r} escapes the run workspace"
        if not target.is_file():
            return f"error: no such artifact {path!r}"
        return target.read_text(errors="replace")

    @app.tool()
    def run_status(run_id: str) -> dict:
        """Report run progress."""
        session = sessions.get(run_id)
        if session is None:
            return {"error": f"unknown run {run_id!r}"}
        return {
            "running": session.is_running(),
            "result_path": str(session.result_path) if session.result_path else None,
            "error": session.error,
        }

    return app


def serve(
    transport: str = "stdio",
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    auth_token: str | None = None,
) -> None:
    """Run the Cadora MCP server over the given transport (``stdio`` | ``http``).

    For ``http`` (remote clients) the server listens on ``host:port`` at ``/mcp``. It binds
    localhost by default — front it with TLS before exposing it beyond the host. Pass
    ``auth_token`` to require ``Authorization: Bearer <token>`` on every HTTP request.
    """
    app = build_app(host=host, port=port)
    if transport == "http" and auth_token:
        import uvicorn

        from cadora.mcp.auth import bearer_auth

        uvicorn.run(bearer_auth(app.streamable_http_app(), auth_token), host=host, port=port)
        return
    app.run(transport=_TRANSPORTS.get(transport, transport))
