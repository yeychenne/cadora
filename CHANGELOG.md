# Changelog

## Unreleased

## v0.4.0 — 2026-07-01

Gate-quality and observability release: the construction gate refuses hollow passes and understands
cross-stack toolchains, and the local dashboard becomes a topology + FinOps cockpit.

### Changed
- **Gate substance check** — a construction gate that invokes a test runner but executes **zero
  tests** (`go test` / `cargo test` / `jest --passWithNoTests` all exit 0 with no tests) is now
  reported as `vacuous` and **blocks the run** instead of passing — it verified nothing. Any test
  that actually ran exempts the gate, so mixed multi-package runs aren't penalized.
- **Cross-stack prerequisite detection** — the "toolchain missing vs. tests failed" classifier now
  also recognizes Node (`Cannot find module`), Go (`no required module provides package`), and Rust
  (`can't find crate for`) missing-dependency errors, so a missing toolchain on those stacks is a
  `blocked_prerequisite`, not a `failed` gate.

### Added
- **Topology + FinOps dashboard** — the `cadora dashboard` overview gains a **FinOps** panel (a token
  split, a cost-by-day trend, and cost by model / executor / funding, with an all/30d/7d window), and
  the run-detail **DAG becomes a cost-and-quality map**: every node box shows its cost and context
  tokens plus badges for its gate, integrity, and review outcomes. `summarize_usage` (and
  `cadora usage --json`) now expose `by_funding` and a per-day `by_day` cost series. See
  [docs/dashboard.md](docs/dashboard.md).

## v0.3.0 — 2026-06-27

Observability and local-iteration release: see what a run is doing and what it cost, iterate
offline, and productize the analyst front-end.

### Added
- **Run dashboard** — `cadora dashboard` serves a local web dashboard: active and recent runs,
  token usage and cost by model, and a per-run **detail view** (DAG progress, node inspector,
  activity timeline, output preview, and artifact list + text preview). Localhost-only with no
  authentication — keep it on loopback or front it with TLS + auth before exposing. `cadora usage`
  summarizes tokens and cost by model from the run archive. See [docs/dashboard.md](docs/dashboard.md).
- **Live stage progress** — `cadora run` announces each node as it starts and emits an elapsed-time
  heartbeat, so long autonomous runs show progress instead of going silent.
- **Local fixture executor** — `--executor fixture` writes deterministic `aidlc-docs` with **no
  external model call**, for private/offline HITL demos and CI.
- **HITL quick-desktop front-end (Track B)** — a phase-aware desktop review surface for the
  fail-closed HITL gates.
- **Reusable analyst-frontend (FE-builder) topology** — `examples/analyst-frontend.topology.yaml`:
  a domain-agnostic node that turns any deterministic case-scoring engine into engine + FastAPI
  analyst API + Vite/React GUI + WeasyPrint PDF + a deterministic audit/explainability panel. An
  optional `frontend.manifest.yaml` steers it (contract-first); discovery fills the rest. See
  [docs/analyst-frontend.md](docs/analyst-frontend.md).

### Fixed
- **Gate cwd resolution** — prerequisite provisioning no longer doubles the path when `--cwd` is set.
- **Version alignment** — `cadora.__version__` is back in sync with the packaged version (was `0.1.0`).
- Dropped a private-doc reference from a shipped module docstring.

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
