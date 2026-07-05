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
| `start_run` | `topology`, `run_id`, `cwd="."`, `executor="claude"`, `archive_dir="runs"` | `{run_id, status}` — starts an AI-DLC run with HITL gates active |
| `review_gate` | `run_id` | the pending review (`node_id`, `docs_dir`, `artifacts`) or `{pending: false, running}` |
| `get_artifact` | `run_id`, `path` | the text of a generated artifact, relative to the run workspace |
| `submit_review` | `run_id`, `decision`, `comments=""` | submits `approve` \| `request_changes` \| `abort` |
| `run_status` | `run_id` | `{running, result_path, error}` |

`request_changes` requires `comments`; the node re-runs with your feedback, for up to three
revisions — exceeding that limit stops the run.

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

## See also

- The captured run lives under `runs/<run_id>/` — inspect it with `cadora archive show <run_id>`.
- To gate a run from the terminal instead of an MCP client, use `cadora run … --hitl`.
