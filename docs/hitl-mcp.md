# Human-in-the-loop review over MCP

Cadora drives the AWS AI-DLC method on a headless coding agent. At any node marked `review: true`,
the run **pauses for a human** to read the generated artifacts and approve, request changes, or
abort. The `cadora mcp` server exposes that review gate — and run control — over the
[Model Context Protocol](https://modelcontextprotocol.io), so **any MCP client can be the review
surface**: Claude Code, Claude Desktop, the Codex CLI, or the terminal.

This mirrors Cadora's backend design: just as `NodeExecutor` makes the *coding agent* pluggable, the
MCP server makes the *human review surface* pluggable.

## Install

```bash
pip install 'cadora[mcp]'
```

## Connect Claude Code

Register the server once, from your project directory:

```bash
claude mcp add cadora -- cadora mcp --transport stdio
```

…or commit a project-scoped [`.mcp.json`](../examples/claude-code.mcp.json):

```json
{
  "mcpServers": {
    "cadora": { "command": "cadora", "args": ["mcp", "--transport", "stdio"] }
  }
}
```

## Connect Claude Desktop

Claude Desktop reads the same `mcpServers` block — see
[`examples/claude-desktop.config.json`](../examples/claude-desktop.config.json). Add Cadora to its
config file and restart the app:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "cadora": { "command": "cadora", "args": ["mcp", "--transport", "stdio"] }
  }
}
```

The Codex CLI uses the same stdio command in its own MCP configuration.

## Connect any local MCP client

Local MCP clients can use the same stdio command:

```json
{
  "mcpServers": {
    "cadora": { "command": "cadora", "args": ["mcp", "--transport", "stdio"] }
  }
}
```

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `start_run` | `topology`, `run_id`, `cwd="."`, `executor="claude"`, `archive_dir="runs"`, `review_timeout=3600` | `{run_id, status}` — starts an AI-DLC run with HITL gates active |
| `review_gate` | `run_id` | the pending review (`node_id`, `docs_dir`, `artifacts`) or `{pending: false, running}` |
| `get_artifact` | `run_id`, `path` | the text of a generated artifact, relative to the run workspace |
| `submit_review` | `run_id`, `decision`, `comments=""` | submits `approve` \| `request_changes` \| `abort` |
| `run_status` | `run_id` | `{running, result_path, error}` |

`request_changes` requires `comments`; the node re-runs with your feedback, for up to three
revisions — exceeding that limit stops the run.

`submit_review` **fails soft**: an unknown decision, a `request_changes` with no comments, or a
submit when no gate is pending returns `{error: …}` — the gate stays open for a valid retry — rather
than raising. Each gate waits up to `review_timeout` seconds (default 1 h) for a decision, then
**fails closed to `abort`** so a client that walks away can't pin the run forever; pass
`review_timeout=0` to wait indefinitely.

## A review session

1. **Start** — ask the client to call `start_run` with your topology and a `run_id`.
2. **Watch** — poll `review_gate`. When a `review: true` node finishes, it returns the node and the
   list of generated artifacts (for example `aidlc-docs/requirements.md`).
3. **Read** — call `get_artifact` for each path, or open the files in your workspace.
4. **Decide** — call `submit_review` with `approve`, or `request_changes` plus comments, or `abort`.
   On approval the run proceeds to the downstream nodes; on changes the same node re-runs.
5. **Finish** — poll `run_status` until `running` is false; `result_path` points at the run manifest.

## Remote clients (HTTP transport)

For clients that connect over the network rather than spawning a local process — a hosted chat
surface, or a teammate's machine — run the server with the streamable-HTTP transport:

```bash
cadora mcp --transport http --host 127.0.0.1 --port 8000
```

The MCP endpoint is then `http://<host>:<port>/mcp`. The server **binds localhost by default** — keep
it that way for local use. To reach it from another host, do not expose `0.0.0.0` straight to the
internet: put it behind a reverse proxy that terminates TLS and authenticates callers.

## Trust boundary & failure modes

The review gate is fail-closed on every surface (a non-TTY terminal, an invalid or empty decision, a
timeout, and an unauthenticated HTTP request all resolve to `abort`). Three properties are worth
understanding before you rely on it:

- **Reviewer comments are trusted input.** Your `comments` are inserted **verbatim** into the coding
  agent's prompt — appended to the same node on `request_changes`, and to downstream nodes on
  `approve` — framed as instructions the agent must address. A careless or malicious review comment
  is therefore an instruction-injection vector into the agent. Treat the comment box as you would any
  prompt: only let people you trust drive the review surface, and require authentication on any
  endpoint that is not a local process (see below).
- **Sessions are in-memory.** A pending run lives inside the `cadora mcp` server process. If that
  process restarts or crashes, the run is gone and `review_gate` / `run_status` report
  `unknown run <id>` — an **explicit** loss, never a silent or corrupted state. The artifacts and
  telemetry captured up to the pause remain under `runs/<run_id>/`; re-drive from the terminal with
  `cadora run … --resume-from <node>` rather than expecting the server to recover the session.
- **A hard kill leaves no resume baseline.** Cadora records a workspace fingerprint when a run
  *finalizes* — on success **and** on a graceful stop (abort, revision-limit, gate failure) — and
  `--resume-from` verifies the workspace against it, refusing on drift unless `--allow-drift`. A run
  ended by `SIGKILL`, a crash, or power loss never finalizes, so it writes no fingerprint and a later
  `--resume-from` proceeds *on trust* with no drift check. Prefer aborting a run you intend to resume
  over killing it.

For a network endpoint, always run with authentication:

```bash
cadora mcp --transport http --host 127.0.0.1 --port 8000 --auth-token "$CADORA_MCP_TOKEN"
```

Every HTTP request must then carry `Authorization: Bearer <token>`; unauthenticated or wrong-token
requests are rejected with `401` before reaching any tool.

## See also

- The captured run lives under `runs/<run_id>/` — inspect it with `cadora archive show <run_id>`.
- To gate a run from the terminal instead of an MCP client, use `cadora run … --hitl`.
