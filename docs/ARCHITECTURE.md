# Cadora architecture

Cadora implements the [AWS AI-DLC method](https://github.com/awslabs/aidlc-workflows) as a thin, **headless, backend-agnostic conductor** over external coding-agent CLIs. The differentiated value — the workflow DAG, the AI-DLC rules + workspace setup, the deterministic gates, and the run archive / eval layer — lives here; the agent loop does not (the backend CLI owns it).

## The AI-DLC workflow

Cadora vendors the AWS AI-DLC rule-set (`awslabs/aidlc-workflows`, MIT-0) under
`cadora/aidlc_rules/` and installs it into each run's workspace using the backend-native project
memory file: `CLAUDE.md` for Claude Code or `AGENTS.md` for Codex. The managed workflow block
preserves pre-existing project instructions. Per-stage rules live in `.aidlc-rule-details/`.
A run drives the agent through the method's three adaptive phases — 🔵 Inception →
🟢 Construction → 🟡 Operations — from a `vision.md`.

- **v0.1.0** runs the lifecycle as a **single autonomous session** (`examples/aidlc.topology.yaml`).
- The **per-stage DAG** (`examples/aidlc-stages.topology.yaml`, one node per AI-DLC stage) is the destination — it makes the stage graph explicit, gate-able, and resumable.

## The one decision: a runner-agnostic execution boundary

`cadora.executors.NodeExecutor` is the seam. A node's `(prompt, tools, cwd)` goes in; a normalized `ExecutionResult` `(text, events, exit code, usage, cost, model)` comes out. Adding a backend is one class — so Cadora isn't vendor-locked and the same workflow can A/B across backends.

| Backend | Command | Output | v0.2.0 |
|---|---|---|---|
| `claude` (default) | `claude -p --output-format stream-json --verbose` | structured events + cost | ✅ shipping |
| `codex` | `codex exec --json` | structured JSONL + usage | ✅ shipping |
| `kiro` | `kiro-cli chat --no-interactive` | text + exit code | seam / roadmap |
| `antigravity` | `agy -p` | text (transcript) | seam / roadmap (experimental) |

v0.2.0 ships **Claude Code** and **Codex** publicly. Codex runs are ephemeral, ignore ambient user
configuration for reproducibility, use `workspace-write`, and normalize `turn.completed` /
`turn.failed` rather than trusting process exit status alone.

## A second seam: a pluggable human-review surface (MCP)

The AI-DLC method favors human review of the generated documents at key steps. Cadora makes that
review point its own seam — the way `NodeExecutor` makes the *backend* pluggable, `cadora/mcp/`
makes the *human-review surface* pluggable.

- `cadora/mcp/channel.py` — `ReviewChannel`, a thread-safe rendezvous between the run thread and a
  review front-end; `channel_review_fn()` adapts it to the runner's existing `review_fn(node, cwd)`
  hook. No change to the runner, topology, gates, or archive.
- `cadora/mcp/session.py` — `RunSession` drives `run_topology(hitl=True)` in a background thread so a
  front-end can poll for gates and submit decisions while the run is in flight.
- `cadora/mcp/server.py` — a [Model Context Protocol](https://modelcontextprotocol.io) server
  (`cadora mcp`) exposing five tools: `start_run`, `review_gate`, `submit_review`, `get_artifact`,
  `run_status`.

Any MCP client can then be the reviewer: Claude Code, Claude Desktop, or the Codex CLI over **local
stdio**, or a networked/hosted client over **streamable HTTP** (`--transport http`, localhost by
default). The server is an optional extra (`pip install 'cadora[mcp]'`); the core has no MCP
dependency. See [hitl-mcp.md](hitl-mcp.md).

## Funding (Claude Code)

Subscription by default: the executor removes an ambient `ANTHROPIC_API_KEY` from the run so the Claude Code subscription token pays; metered API is explicit opt-in (`--funding api`). The resolved source is recorded per run.

## Scheduling

`topo_sort` groups independent nodes into dependency "waves" (Kahn's algorithm); waves run in order. The AI-DLC backbone is sequential; wave concurrency (for the per-stage DAG's per-unit fan-out) is on the roadmap.

## Gates (deterministic-first)

Per Anthropic's verification ranking (rules-based > visual > LLM-judge), gates are shell commands that **block** the run on non-zero exit. v0.1.0 ships a `build-test` gate; AI-DLC compliance + LLM-judge gates are on the roadmap.

The private development line also has a deterministic **toolchain-integrity evaluator**. It detects
repository-local packages or scripts impersonating tools such as `pytest`/`tsc`, unrecognized
TypeScript build substitutions, and verification performed with another temporary project's
environment. Modes are additive to the ordinary shell gate:

- `audit` (default) records structured findings without blocking.
- `enforce` blocks on integrity findings.
- `repair` gives one fresh agent session the findings plus exact gate output, then reruns both the
  deterministic scan and shell gate. If the toolchain is unavailable, the repair prompt requires a
  truthful BLOCKED result rather than a counterfeit substitute.

## Archive (the knowledge layer)

Every run writes `runs/<run-id>/manifest.json` (ok, cost, model, gate, funding) + a per-node `aidlc-docs/` snapshot + the event stream, in a stable, tool-readable shape. Inspect with `cadora archive ls` / `cadora archive show <run-id>`; run comparison + eval read it directly (roadmap).

## Scope (deliberately thin)

- **In Cadora:** the workflow DAG, AI-DLC rules + workspace setup, gates, the run archive, evals.
- **Not in Cadora:** the agent loop, model/provider clients, and any server/queue/VM infrastructure — those belong to the backend CLI.

## References

- AI-DLC method: [awslabs/aidlc-workflows](https://github.com/awslabs/aidlc-workflows).
