# Cadora

**Ship agent-built software you can prove — deterministic gates, tamper detection, run evidence,
and per-node cost attribution, across coding-agent CLIs.**

Coding agents can build real software. Cadora is the **audit-grade conductor** that proves what
they built: it drives headless coding-agent CLIs (**Claude Code**, **OpenAI Codex**) through a
declared multi-step workflow, refuses to take the agent's word for the result, and captures the
whole run — artifacts, events, human decisions, and **to-the-node cost** — as inspectable
evidence.

Cadora is the **conductor, not the agent**: it doesn't implement the agent loop (the backend CLI
does), and it isn't an in-IDE assistant. It sits *above* the vendors, which is exactly what makes
its verdicts neutral and its cost ledger cross-vendor.

## Why it exists

A vendor's tool verifying that vendor's agent is the fox auditing the henhouse. Cadora verifies
from the outside:

- **Deterministic, fail-closed gates** — Cadora re-runs your build and tests itself and reads
  exit codes and test counts, never the agent's claims. A test runner that executes **zero tests**
  is reported `vacuous` and **blocks the run**. A missing toolchain is `blocked_prerequisite`,
  not a fake failure — classified for Python, Node, Go, and Rust.
- **Tamper detection** — `cadora integrity` detects generated packages and scripts that
  impersonate real tools, unrecognized build substitutions, and tests run against another
  project's environment. Modes: `audit` (record), `enforce` (block), `repair` (one constrained
  fix session, then re-verify).
- **Fail-closed human review** — mark nodes `review: true` and run `--hitl`: the operator must
  approve, request bounded revisions, or abort; closed stdin aborts rather than silently
  approving. Every decision, comment, and revision cost is archived. The review surface is
  pluggable over **MCP** (Claude Code, Claude Desktop, Codex CLI, or any MCP client).
- **Per-node cost attribution, cross-vendor** — every node records its backend, model, tokens,
  and dollars, split by funding source (subscription vs metered API). `cadora usage` and the
  local dashboard's FinOps panel aggregate by model / backend / funding / day — one ledger even
  when design runs on Claude and code runs on Codex.

The evidence of a run *is* the archive: `runs/<id>/manifest.json` + per-stage artifacts + the
event stream + gate/integrity/review outcomes + cost. Inspect with `cadora archive ls / show`
or the dashboard.

## Backends

| Backend | Drive | Notes |
|---|---|---|
| `claude` (default) | `claude -p`, structured stream-json | **subscription-funded by default**; metered API is explicit opt-in (`--funding api`) |
| `codex` | `codex exec --json`, structured JSONL | uses your Codex login/plan |
| `fixture` | local, deterministic, offline | demos, CI smoke, policy-safe HITL walkthroughs — no model call |

Both live backends run the **same topology, gates, integrity evaluation, and archive**, so their
results A/B-compare directly — including phase-split runs (`--executor claude
--construction-executor codex`). The `NodeExecutor` seam makes a new backend one class.

## Methods are packs — AI-DLC is the flagship

Cadora ships the [AWS AI-DLC method](https://github.com/awslabs/aidlc-workflows) (AI-Driven
Development Life Cycle, MIT-0) as its built-in flagship workflow: `cadora aidlc-init` installs
the rule-set into your workspace (`CLAUDE.md` for Claude Code, `AGENTS.md` for Codex — existing
project instructions are preserved outside a managed block), and the example topologies drive
🔵 Inception → 🟢 Construction → Build & Test from a `vision.md`. The method is a **pack, not the
product**: any workflow you can express as a topology of gated nodes conducts the same way.

## Install

Requires **Python 3.10+** and at least one authenticated backend CLI
([`claude`](https://docs.claude.com/claude-code) or
[`codex`](https://developers.openai.com/codex/cli/)):

```bash
pip install cadora
```

From source: `git clone https://github.com/yeychenne/cadora.git && cd cadora && python3 -m venv
.venv && source .venv/bin/activate && pip install -e ".[dev]"`.

## Quickstart

```bash
# 1. Set up a workspace from your product vision (installs the AI-DLC method pack).
cadora aidlc-init ./my-project --vision vision.md

# 2. Drive the workflow on Claude Code — autonomous, gated, subscription-funded.
cadora run examples/aidlc.topology.yaml --vision vision.md --cwd ./my-project

# 3. Read the evidence.
cadora archive ls
cadora archive show <run-id>
cadora usage            # tokens + dollars by model / backend / funding
cadora dashboard        # local cockpit: DAG cost/quality map + FinOps panel
```

A/B the same spec on Codex:

```bash
cadora run examples/aidlc.topology.yaml \
  --executor codex --model gpt-5.5 \
  --integrity-mode repair \
  --vision vision.md --cwd ./my-project
```

Split phases across vendors (design on Claude, code on Codex):

```bash
cadora run examples/aidlc-phased.topology.yaml \
  --executor claude \
  --construction-executor codex --construction-model gpt-5.5 \
  --vision vision.md --cwd ./my-project
```

Scan any existing workspace for toolchain tampering — no agent run required:

```bash
cadora integrity ./my-project [--json]
```

## Gate mechanics worth knowing

The gate distinguishes a real failure from an unavailable prerequisite. Python workspaces that
declare dev requirements get a cached isolated gate environment (`.cadora/gate-venv`); your
`--gate-cmd` runs unchanged inside it. If provisioning is impossible, the archive records
`blocked_prerequisite` + the missing packages instead of misreporting the application as broken.
Offline: `--gate-wheelhouse /path/to/wheels`; opt out with `--gate-setup off`.

Autonomous runs pass `--dangerously-skip-permissions` to the backend (an agentic workflow edits
files and runs commands) — point Cadora only at workspaces you trust, prefer a dedicated
worktree/container, and keep credentials out of the workspace environment.

The dashboard binds **localhost only, no authentication** — keep it on loopback or front it with
TLS + auth. See [docs/dashboard.md](docs/dashboard.md).

## Status

**v0.5.0** — the multi-backend + repositioning release: **multi-backend phase routing**
(`--construction-executor`), **per-node executor cost attribution** in `cadora usage` and the
dashboard FinOps panel, and the audit-grade repositioning (AI-DLC becomes the flagship method
pack). On top of v0.4.0's gate substance checks (vacuous-pass blocking), cross-stack prerequisite
classification, and the topology/FinOps dashboard. 120+ tests, `ruff` clean, CI on Python
3.10–3.12.

**Roadmap:** `cadora report` — a portable, self-contained **evidence pack** per run (gates,
integrity findings, review trail, per-node cost); `cadora compare` — side-by-side measured
verdicts across backends/models; additional backend and method packs as they earn verification.

## License

MIT. The vendored AI-DLC rule-set is MIT-0 (`awslabs/aidlc-workflows`).
