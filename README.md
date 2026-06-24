# Cadora

**An AI-DLC workflow conductor — drive the AWS AI-DLC method on a coding agent, headlessly, and capture every run.**

Cadora implements the [AWS AI-DLC method](https://github.com/awslabs/aidlc-workflows) (AI-Driven Development Life Cycle) as a thin, **backend-agnostic conductor**. Point it at a product vision and it drives a coding agent through the full AI-DLC lifecycle — 🔵 Inception (requirements → planning → design) then 🟢 Construction (code generation → build & test) — **autonomously and headlessly**, applies a deterministic gate, and captures the whole run (artifacts, events, cost) for inspection and comparison.

Cadora is the **conductor**, not the agent: it doesn't implement the agent loop (the backend CLI does), and it isn't an in-session/in-IDE assistant. It installs the AI-DLC rules into a workspace, drives an external headless coding-agent CLI, gates the result, and archives it — so runs are **reproducible, scriptable, gate-able, and comparable**.

**Backends:** Cadora supports both **Claude Code** (`claude -p`, subscription-funded by default)
and **OpenAI Codex** (`codex exec --json`, using your Codex login). Select either with
`--executor claude` or `--executor codex`; both use the same AI-DLC topology, deterministic gates,
toolchain-integrity evaluation, and run archive, so their results can be A/B-compared directly.

Both backends ship in **v0.2.0** — Claude Code since v0.1.0, and OpenAI Codex promoted to a
supported backend after live verification.

## Status

**v0.2.0 — released** ([PyPI](https://pypi.org/project/cadora/)). Adds the pluggable **HITL review surface over MCP** (`cadora mcp` — Claude Code/Desktop, Codex, remote HTTP), the **OpenAI Codex** backend, gate-prerequisite provisioning, and toolchain integrity on top of the v0.1.0 conductor. Live-proven end-to-end — from a tiny CLI to a multi-stack AWS app (CDK + Lambda + Next.js, `cdk synth` + tests passing). `pytest` green, `ruff` clean, CI on Python 3.10–3.12.

## How it works

```
vision.md ─▶ install AI-DLC rules (agent memory + .aidlc-rule-details/) into a workspace
          ─▶ drive the selected backend through the AI-DLC lifecycle
          ─▶ deterministic build-test gate (blocks on failure)
          ─▶ run archive: aidlc-docs/ + events + cost   →   cadora archive show
```

## Install

Requires **Python 3.10+** and at least one authenticated backend CLI:

- [`claude`](https://docs.claude.com/claude-code) for Claude Code.
- [`codex`](https://developers.openai.com/codex/cli/) for OpenAI Codex.

```bash
pip install cadora
```

From source (for development):

```bash
git clone https://github.com/yeychenne/cadora.git && cd cadora
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Quickstart

```bash
# 1. Set up an AI-DLC workspace from your product vision.
cadora aidlc-init ./my-project --vision vision.md

# 2. Drive the full AI-DLC lifecycle on Claude Code (autonomous, subscription-funded).
cadora run examples/aidlc.topology.yaml --vision vision.md --cwd ./my-project

# 3. Inspect what happened.
cadora archive ls
cadora archive show <run-id>
```

Codex development instance:

```bash
cadora run examples/aidlc.topology.yaml \
  --executor codex \
  --model gpt-5.4 \
  --integrity-mode repair \
  --vision vision.md \
  --cwd ./my-project
```

Cadora installs the workflow into the backend-native project memory file: `CLAUDE.md` for Claude
Code or `AGENTS.md` for Codex. Existing project instructions are preserved outside a managed block.
Toolchain integrity defaults to non-blocking `audit`. Use `--integrity-mode enforce` to block local
packages/scripts that impersonate declared tools, or `repair` to allow one fresh, constrained agent
session to remove the substitution and rerun the external gate.

The deterministic gate also distinguishes a real test/build failure from an unavailable
prerequisite. By default, Python workspaces that declare `requirements-dev.txt`,
`dev-requirements.txt`, or `requirements/dev.txt` get a cached isolated environment under
`.cadora/gate-venv`; the original `--gate-cmd` is then executed unchanged inside it. If dependency
provisioning is unavailable, the archive records `blocked_prerequisite` and the missing packages
instead of misreporting the application as failing. For disconnected environments, provide a local
wheel cache with `--gate-wheelhouse /path/to/wheels`; use `--gate-setup off` to manage the gate
environment yourself.

Human review is explicit and fail-closed. In a multi-stage topology, mark selected nodes with
`review: true` and run with `--hitl`. At each declared point the operator must approve, request
changes, or abort. Requested changes rerun the same stage—with a maximum of three revision
cycles—before any downstream node starts. Closed/non-interactive stdin aborts rather than silently
approving. Decisions, comments, individual attempt outputs, and aggregate revision cost are stored
in the archive. See `examples/aidlc-hitl.topology.yaml`. The review surface is **pluggable**:
besides the terminal, `cadora mcp` serves the review gate over the Model Context Protocol to any MCP
client — Claude Code, Claude Desktop, or the Codex CLI (local stdio), or a networked client over
streamable HTTP. See [docs/hitl-mcp.md](docs/hitl-mcp.md).

Scan any existing workspace without running an agent:

```bash
cadora integrity ./my-project
cadora integrity ./my-project --json
```

The run lands in `runs/<run-id>/` — `manifest.json` (ok, cost, model, structured gate status,
funding) + a per-node `aidlc-docs/` snapshot + the event stream. The generated application code
lands in your workspace.

### Funding (Claude Code)

Cadora **defaults to your Claude Code subscription** — it removes any ambient `ANTHROPIC_API_KEY` from the run so a stray key can't silently meter you. Metered API is explicit opt-in (`--funding api`). Set the subscription token once with `claude setup-token`, or just be logged in to Claude Code.

## The AI-DLC method

Cadora vendors the AWS AI-DLC rule-set ([`awslabs/aidlc-workflows`](https://github.com/awslabs/aidlc-workflows), MIT-0) under `cadora/aidlc_rules/` and installs it per run as backend-native project memory + `.aidlc-rule-details/` (per-stage rules). v0.1.0 drives the lifecycle as a **single autonomous session** (`examples/aidlc.topology.yaml`); the **per-stage DAG** (`examples/aidlc-stages.topology.yaml`, one node per stage) is the roadmap.

## Architecture

- `cadora/topology.py` — the workflow DAG (schema + loader + dependency-ordered waves).
- `cadora/executors/` — the `NodeExecutor` seam + structured Claude Code and Codex CLI backends.
- `cadora/mcp/` — the MCP interface seam: serves the HITL review gate + run control to any MCP client (Claude Code/Desktop, Codex, or remote HTTP).
- `cadora/workspace.py` — install the AI-DLC rules + inputs into a run workspace.
- `cadora/gates.py` — deterministic-first gates (shell checks that block).
- `cadora/archive.py` — run capture (`runs/<id>/manifest.json` + artifacts) + `cadora archive ls/show`.
- `cadora/runner.py` — wires it together.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Roadmap

`cadora eval` / `compare` (deterministic AI-DLC compliance + LLM-judge over captured runs),
per-stage wave concurrency, hardening for Kiro/Antigravity, and a consulting deliverable pack.

## License

MIT — see [LICENSE](LICENSE). The vendored AI-DLC rules are MIT-0 (`cadora/aidlc_rules/LICENSE`).
