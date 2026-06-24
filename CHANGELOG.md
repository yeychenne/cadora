# Changelog

## Unreleased

## v0.2.0 — 2026-06-24

### Added
- **Explicit fail-closed HITL gates** — topology nodes opt in with `review: true`; operators can
  approve, request a bounded same-stage revision, or abort. Non-interactive input aborts, and
  structured review history is captured in the run archive.
- **Pluggable human-review surface over MCP** — `cadora mcp` exposes the HITL review gate and run
  control (`start_run`, `review_gate`, `submit_review`, `get_artifact`, `run_status`) over the Model
  Context Protocol, so the reviewer can be any MCP client — Claude Code, Claude Desktop, the Codex
  CLI (local stdio), or a networked client over streamable HTTP — not just the terminal. The runner,
  topology, and archive are unchanged; the review surface becomes pluggable the way the
  `NodeExecutor` backend is. Optional extra: `pip install 'cadora[mcp]'`. See
  [docs/hitl-mcp.md](docs/hitl-mcp.md).
- **Gate prerequisite provisioning and classification** — Python projects can prepare a cached
  isolated gate environment from their dev requirements, optionally from an offline wheelhouse.
  Missing tools/plugins are archived as `blocked_prerequisite`, separately from executed test or
  build failures; the original gate command and quality thresholds remain unchanged.
- **Toolchain integrity evaluation** — `cadora integrity <workspace>` detects generated packages
  and scripts that impersonate real tools, unrecognized TypeScript build substitutions, and tests
  run with another temporary project's environment.
- **Run-integrated integrity modes** — `audit` records structured findings, `enforce` blocks, and
  `repair` permits one fresh constrained agent session before rerunning the deterministic scan and
  external gate. Findings and repair events are captured in the run archive.
- **OpenAI Codex backend** — `--executor codex` (`codex exec --json`) drives the same AI-DLC
  topology, gates, integrity evaluation, and run archive as Claude Code, so the two A/B-compare.
  Promoted from the development line to a supported backend after live verification.

### Fixed
- **MCP `start_run` now registers the topology's gates** — gated topologies run over the MCP review
  surface instead of failing as "unregistered gate(s)".
- **`cadora run --model` is honored by the Claude backend** — the executor-level model is no longer
  dropped (a per-node `model:` still overrides it).

## v0.1.0 — 2026-06-22

First public release of **Cadora** — an AI-DLC workflow conductor that drives the AWS AI-DLC
method on a headless coding agent (Claude Code), gates the result, and captures every run.

### Added
- **AI-DLC conductor** — vendors the AWS AI-DLC rule-set (`awslabs/aidlc-workflows`, MIT-0) and
  installs it per run (`CLAUDE.md` + `.aidlc-rule-details/`); drives the full lifecycle
  (Inception → Construction → Build & Test) from a `vision.md`.
- **`cadora aidlc-init`** — set up an AI-DLC workspace (rules + inputs).
- **`cadora run`** — drive the workflow on Claude Code (`claude -p`), autonomous and
  **subscription-funded by default** (metered API opt-in via `--funding api`); `--cwd`,
  `--gate-cmd`, `--timeout`.
- **Deterministic `build-test` gate** — blocks the run on non-zero exit.
- **Run archive** — `runs/<id>/manifest.json` + per-node `aidlc-docs/` snapshot + event stream
  + cost; inspect with `cadora archive ls` / `cadora archive show`.
- **`NodeExecutor` seam** — backend-agnostic; Claude Code shipping, with codex / kiro /
  antigravity adapters behind it.
- Examples: `aidlc.topology.yaml` (single-session) and `aidlc-stages.topology.yaml`
  (per-stage DAG); CI (ruff + pytest on Python 3.10–3.12).

### Roadmap
- `cadora eval` / `compare` (AI-DLC compliance + LLM-judge over captured runs), the per-stage
  DAG + wave concurrency, `--hitl` human approval, more backends, a consulting deliverable pack.
