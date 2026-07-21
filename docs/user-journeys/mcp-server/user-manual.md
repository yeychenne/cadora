# The MCP server — user manual

Cadora drives the AI-DLC method on a headless coding agent, pausing at each review gate for a human
to approve, request changes, or abort. The **MCP server** exposes that whole loop — run control *and*
the human-review gate — over the [Model Context Protocol](https://modelcontextprotocol.io), so
**another agent can be the operator**: Claude Desktop, Claude Code, the Codex CLI, or the terminal.
This manual is for the person wiring that agent to Cadora and driving a gated run through five tools.

It mirrors Cadora's backend design: just as `NodeExecutor` makes the *coding agent* pluggable, the
MCP server makes the *human-review surface* pluggable — the same fail-closed / fail-soft guarantees
Cadora enforces on the CLI, now reachable by an MCP client.

---

## 1. What the MCP server is, and why

`cadora mcp` runs Cadora as an MCP server. A connected client gets five tools — a programmatic
control plane over the audit-grade conductor:

| Tool | What it does |
|---|---|
| `start_run` | Start an AI-DLC run with HITL review gates active; returns the run id. |
| `run_status` | Report run progress — running, result path, error. |
| `review_gate` | Return the review awaiting a decision (node + artifact paths), or none. |
| `get_artifact` | Read a generated artifact, relative to the run's workspace. |
| `submit_review` | Submit the pending decision: `approve` \| `request_changes` \| `abort`. |

The point is that these are the **same** review controls Cadora enforces everywhere — not a weaker,
remote-only copy. The gate still fails closed on timeout, a bad decision still fails soft, and a
generated artifact still cannot be read from outside the run's workspace. The MCP server only changes
*who holds the controls*, not what the controls enforce.

The server needs the optional extra:

```bash
pip install 'cadora[mcp]'
```

> **Loopback by default.** The server speaks stdio to the one client that spawned it, or binds
> `127.0.0.1` over HTTP. Nothing is reachable from the network until you deliberately expose it — and
> that path requires authentication (§6).

---

## 2. Start the server — `cadora mcp`

```bash
cadora mcp --transport stdio
```

`cadora mcp` runs Cadora as an MCP server (HITL review + run control). Its flags:

| Flag | Default | Meaning |
|---|---|---|
| `--transport stdio\|http` | `stdio` | `stdio` (local: Claude Desktop/Code, Codex CLI) or `http` (remote). |
| `--host <host>` | `127.0.0.1` | Bind host for `--transport http` (default: localhost; expose remotely behind TLS+auth). |
| `--port <port>` | `8000` | Bind port for `--transport http`. |
| `--auth-token <tok>` | none | Require `Authorization: Bearer <token>` on every HTTP request (or set `CADORA_MCP_TOKEN`); enables safe `--transport http` exposure. Still front it with TLS. |
| `--i-understand-no-auth` | off | Allow binding this unauthenticated surface to a non-loopback host. |

For local use you rarely run this by hand — the client launches it for you (§3). The `stdio`
transport is the default and the safe one: no port, no listening socket, no network surface.

---

## 3. Connect a client

Every local client reads the same `mcpServers` block, wiring `cadora mcp` as a **stdio** server.

### Claude Code

Register it once from your project directory:

```bash
claude mcp add cadora -- cadora mcp --transport stdio
```

…or commit a project-scoped `.mcp.json` (see [`examples/claude-code.mcp.json`](../../../examples/claude-code.mcp.json)):

```json
{
  "mcpServers": {
    "cadora": {
      "command": "cadora",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

### Claude Desktop

Claude Desktop reads the same block (see
[`examples/claude-desktop.config.json`](../../../examples/claude-desktop.config.json)). Add Cadora to
its config file and restart the app:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "cadora": {
      "command": "cadora",
      "args": ["mcp", "--transport", "stdio"]
    }
  }
}
```

### Codex CLI, and any local client

The Codex CLI — and any local MCP client — uses the identical stdio command in its own MCP
configuration: `command` `cadora`, `args` `["mcp", "--transport", "stdio"]`.

---

## 4. The five tools

### `start_run`

```
start_run(topology, run_id, cwd=".", executor="claude", archive_dir="runs",
          gate_cmd="ruff check . && pytest -q", gate_setup="auto",
          review_timeout=3600.0) -> dict
```

Start an AI-DLC run with HITL review gates; returns the run id. Every gate the topology references is
registered with `gate_cmd` (mirroring `cadora run`), so gated topologies run over MCP rather than
failing as "unregistered gate(s)". Returns `{"run_id": <id>, "status": "started"}`.

`review_timeout` bounds how long **each** gate waits for `submit_review` before **failing closed to
an abort** (default `3600.0`, one hour) — a client that starts a run and walks away cannot pin the
run thread forever. **Pass `0` to wait indefinitely**, the opt-out for a genuinely interactive
reviewer who may sit on a gate for a long time.

### `run_status`

```
run_status(run_id) -> dict
```

Report run progress. Returns `{"running": <bool>, "result_path": <str|null>, "error": <str|null>}`.
While the run thread is alive `running` is `true` and `result_path` is `null`; when it finishes,
`result_path` points at the run manifest (or `error` carries the failure). Read-only.

### `review_gate`

```
review_gate(run_id) -> dict
```

Return the review awaiting a decision, or none. When a `review: true` node is paused, returns
`{"pending": true, "node_id": <id>, "docs_dir": <dir>, "artifacts": [<paths>]}`; otherwise
`{"pending": false, "running": <bool>}`. `artifacts` are the `.md` documents the node produced,
relative to the run workspace — read each with `get_artifact`. Read-only.

### `submit_review`

```
submit_review(run_id, decision, comments="") -> dict
```

Submit the pending review decision: `approve` | `request_changes` | `abort`. On success returns
`{"submitted": <decision>}`. **Fails soft** — an invalid decision, a `request_changes` with no
comments, or a submit when no gate is pending (already resolved, or a double-submit) returns
`{"error": <message>}` rather than raising through the tool call:

- invalid decision → `{"error": "invalid review decision: 'yep'"}`
- `request_changes`, empty comments → `{"error": "request_changes requires reviewer comments"}`
- nothing pending → `{"error": "no review is pending"}`

`approve` lets the run proceed to downstream nodes; `request_changes` re-runs the **same** node with
your comments (up to three revisions — exceeding that stops the run); `abort` stops the run.

### `get_artifact`

```
get_artifact(run_id, path) -> str
```

Read a generated artifact, relative to the run's workspace. Returns the file text. **Fails closed on
path traversal** — any reachable MCP client can call this, so a `../`-shaped path that escapes the
run workspace is refused, never read:

- traversal → `error: path '../secrets.env' escapes the run workspace`
- missing file → `error: no such artifact 'aidlc-docs/nope.md'`

(An unknown `run_id`, on any tool, returns `unknown run '<id>'`.)

---

## 5. A worked review session

Ask the client to drive the tools in order. A run of
[`examples/aidlc-hitl.topology.yaml`](../../../examples/aidlc-hitl.topology.yaml) — `requirements` and
`design` are `review: true`, `construction` has a `build-test` gate — looks like this:

**1. Start** (wait indefinitely at gates, since a human is in the loop):

```
start_run(topology="examples/aidlc-hitl.topology.yaml", run_id="checkout-api", review_timeout=0)
→ {"run_id": "checkout-api", "status": "started"}
```

**2. Watch** until a gate opens:

```
run_status(run_id="checkout-api")
→ {"running": true, "result_path": null, "error": null}

review_gate(run_id="checkout-api")
→ {"pending": true,
   "node_id": "requirements",
   "docs_dir": "aidlc-docs",
   "artifacts": ["aidlc-docs/inception/requirements/requirements.md"]}
```

**3. Read** the artifacts the node produced:

```
get_artifact(run_id="checkout-api", path="aidlc-docs/inception/requirements/requirements.md")
→ "# Requirements\n\n## FR-1 Place an order …"
```

**4. Decide** — approve to proceed, or request changes with comments:

```
submit_review(run_id="checkout-api", decision="approve")
→ {"submitted": "approve"}

# or, to send it back:
submit_review(run_id="checkout-api", decision="request_changes",
              comments="FR-3 is missing the refund path; add acceptance criteria.")
→ {"submitted": "request_changes"}
```

**5. Finish** — the run proceeds through `design` (another gate) and `construction` (the gate runs
`gate_cmd`), then stops:

```
run_status(run_id="checkout-api")
→ {"running": false, "result_path": "runs/checkout-api/manifest.json", "error": null}
```

The captured run lives under `runs/checkout-api/` — inspect it with `cadora archive show
checkout-api`, or seal it into a portable evidence pack with `cadora report checkout-api`.

---

## 6. Expose over HTTP, safely

For a client that connects over the network rather than spawning a local process — a hosted chat
surface, or a teammate's machine — use the streamable-HTTP transport. The endpoint is then
`http://<host>:<port>/mcp`.

The server **binds loopback by default and is unauthenticated** — so binding it to a routable host
is **refused** unless you either authenticate it or explicitly acknowledge the risk:

```
$ cadora mcp --transport http --host 0.0.0.0
refusing to bind the MCP server to '0.0.0.0': it has NO authentication and would be
reachable from the network. Front it with TLS + auth, or pass --i-understand-no-auth
to bind anyway (do this only on a trusted network).
```

The safe way to expose it is a **bearer token**. Set one (or `CADORA_MCP_TOKEN`); every HTTP request
must then carry `Authorization: Bearer <token>`, and a present token also lifts the non-loopback
refusal:

```bash
export CADORA_MCP_TOKEN=$(openssl rand -hex 32)
cadora mcp --transport http --host 127.0.0.1 --port 8000 --auth-token "$CADORA_MCP_TOKEN"
```

A missing or wrong token is rejected before any tool runs:

```
HTTP 401  {"error":"unauthorized"}
WWW-Authenticate: Bearer
```

> **Bearer auth is transport auth, not a substitute for TLS.** The token itself crosses the wire in
> the clear unless a TLS-terminating reverse proxy sits in front. Do not expose `0.0.0.0` straight to
> the internet — put it behind a proxy that terminates TLS and authenticates callers. The token
> comparison is constant-time; the guard lives in `cadora/mcp/auth.py`.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `the MCP server needs the optional extra: pip install 'cadora[mcp]'` | The `mcp` SDK isn't installed | `pip install 'cadora[mcp]'` |
| Client shows no `cadora` tools | The `mcpServers` block is wrong or the client wasn't restarted | Confirm `command: "cadora"`, `args: ["mcp", "--transport", "stdio"]`; restart the client |
| `start_run` fails with unregistered gate(s) | — (should not happen over MCP) | `start_run` registers every referenced gate with `gate_cmd`; override it via the `gate_cmd` argument |
| `{"error": "no review is pending"}` on `submit_review` | Already resolved, or a double-submit | Poll `review_gate` first; only submit when `pending` is `true` |
| `{"error": "request_changes requires reviewer comments"}` | `request_changes` with empty `comments` | Resubmit with a non-empty `comments` string |
| `{"error": "invalid review decision: '…'"}` | `decision` isn't `approve` / `request_changes` / `abort` | Use one of the three exact strings |
| `error: path '…' escapes the run workspace` | A `../`-shaped `path` to `get_artifact` | Pass a path **relative to and inside** the run workspace |
| `error: no such artifact '…'` | The file doesn't exist in the run | Take paths from `review_gate`'s `artifacts` list |
| A gate aborts the run on its own | `review_timeout` elapsed with no `submit_review` (fails closed) | Submit within the window, or start with `review_timeout=0` to wait indefinitely; recover a timed-out run with `cadora run --resume-from` |
| `refusing to bind the MCP server to '…'` | Non-loopback `--host` with no auth | Add `--auth-token` (or `CADORA_MCP_TOKEN`), or pass `--i-understand-no-auth` on a trusted network |
| `HTTP 401 {"error":"unauthorized"}` | Missing or wrong bearer token | Send `Authorization: Bearer <token>` matching `--auth-token` / `CADORA_MCP_TOKEN` |

---

## 8. Reference

**`cadora mcp`** — run Cadora as an MCP server (HITL review + run control).
`--transport stdio|http` (default `stdio`) · `--host <host>` (default `127.0.0.1`) ·
`--port <port>` (default `8000`) · `--auth-token <tok>` (or `CADORA_MCP_TOKEN`) ·
`--i-understand-no-auth`

**Tools** (all take `run_id`; an unknown run returns `unknown run '<id>'`):

| Tool | Signature | Returns | Fail-mode |
|---|---|---|---|
| `start_run` | `(topology, run_id, cwd=".", executor="claude", archive_dir="runs", gate_cmd="ruff check . && pytest -q", gate_setup="auto", review_timeout=3600.0)` | `{run_id, status:"started"}` | fail-closed timeout (`0` = wait) |
| `run_status` | `(run_id)` | `{running, result_path, error}` | read-only |
| `review_gate` | `(run_id)` | `{pending, node_id, docs_dir, artifacts}` or `{pending:false, running}` | read-only |
| `submit_review` | `(run_id, decision, comments="")` | `{submitted:decision}` or `{error}` | fail-soft |
| `get_artifact` | `(run_id, path)` | file text, or `error: …` | traversal-safe (fail-closed) |

**Decisions:** `approve` · `request_changes` (requires `comments`) · `abort`.

**HTTP endpoint:** `http://<host>:<port>/mcp` — bearer-authenticated when `--auth-token` /
`CADORA_MCP_TOKEN` is set; a bad token gets `401 {"error":"unauthorized"}`. Front with TLS.

**Client configs:** [`examples/claude-code.mcp.json`](../../../examples/claude-code.mcp.json) ·
[`examples/claude-desktop.config.json`](../../../examples/claude-desktop.config.json) — both wire
`cadora mcp --transport stdio`.

**Source:** the five tools + `serve()` in `cadora/mcp/server.py`; the bearer guard in
`cadora/mcp/auth.py`.
