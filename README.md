# Cadora

**An AI-DLC workflow conductor — drive the AWS AI-DLC method on a coding agent, headlessly, and capture every run.**

Cadora implements the [AWS AI-DLC method](https://github.com/awslabs/aidlc-workflows) (AI-Driven Development Life Cycle) as a thin, **backend-agnostic conductor**. Point it at a product vision and it drives a coding agent through the full AI-DLC lifecycle — 🔵 Inception (requirements → planning → design) then 🟢 Construction (code generation → build & test) — **autonomously and headlessly**, applies a deterministic gate, and captures the whole run (artifacts, events, cost) for inspection and comparison.

Cadora is the **conductor**, not the agent: it doesn't implement the agent loop (the backend CLI does), and it isn't an in-session/in-IDE assistant. It installs the AI-DLC rules into a workspace, drives an external headless coding-agent CLI, gates the result, and archives it — so runs are **reproducible, scriptable, gate-able, and comparable**.

**v0.1.0 backend:** Claude Code (`claude -p`, subscription-funded by default). The `NodeExecutor` seam is backend-agnostic — `codex` / `kiro` / `antigravity` adapters sit behind it (roadmap); the same workflow is meant to run across them and be A/B-compared.

## Status

**v0.1.0 — released** ([PyPI](https://pypi.org/project/cadora/)). The Claude Code path is live-proven end-to-end — it has driven the full AI-DLC lifecycle on real projects, from a tiny CLI to a multi-stack AWS app (CDK + Lambda + Next.js, `cdk synth` + unit tests passing). `pytest` green, `ruff` clean, CI on Python 3.10–3.12.

## How it works

```
vision.md ─▶ install AI-DLC rules (CLAUDE.md + .aidlc-rule-details/) into a workspace
          ─▶ drive `claude -p` through the AI-DLC lifecycle (autonomous, subscription-funded)
          ─▶ deterministic build-test gate (blocks on failure)
          ─▶ run archive: aidlc-docs/ + events + cost   →   cadora archive show
```

## Install

Requires **Python 3.10+** and the [`claude`](https://docs.claude.com/claude-code) CLI on PATH.

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

The run lands in `runs/<run-id>/` — `manifest.json` (ok, cost, model, gate, funding) + a per-node `aidlc-docs/` snapshot + the event stream. The generated application code lands in your workspace.

### Funding (Claude Code)

Cadora **defaults to your Claude Code subscription** — it removes any ambient `ANTHROPIC_API_KEY` from the run so a stray key can't silently meter you. Metered API is explicit opt-in (`--funding api`). Set the subscription token once with `claude setup-token`, or just be logged in to Claude Code.

## The AI-DLC method

Cadora vendors the AWS AI-DLC rule-set ([`awslabs/aidlc-workflows`](https://github.com/awslabs/aidlc-workflows), MIT-0) under `cadora/aidlc_rules/` and installs it per run as `CLAUDE.md` (the core workflow, auto-loaded) + `.aidlc-rule-details/` (per-stage rules). v0.1.0 drives the lifecycle as a **single autonomous session** (`examples/aidlc.topology.yaml`); the **per-stage DAG** (`examples/aidlc-stages.topology.yaml`, one node per stage) is the roadmap.

## Architecture

- `cadora/topology.py` — the workflow DAG (schema + loader + dependency-ordered waves).
- `cadora/executors/` — the `NodeExecutor` seam + the Claude Code backend (codex/kiro/antigravity adapters behind it).
- `cadora/workspace.py` — install the AI-DLC rules + inputs into a run workspace.
- `cadora/gates.py` — deterministic-first gates (shell checks that block).
- `cadora/archive.py` — run capture (`runs/<id>/manifest.json` + artifacts) + `cadora archive ls/show`.
- `cadora/runner.py` — wires it together.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Roadmap

`cadora eval` / `compare` (deterministic AI-DLC compliance + LLM-judge over captured runs), the per-stage DAG + wave concurrency, `--hitl` human approval, more backends (codex/kiro/antigravity), and a consulting deliverable pack.

## License

MIT — see [LICENSE](LICENSE). The vendored AI-DLC rules are MIT-0 (`cadora/aidlc_rules/LICENSE`).
