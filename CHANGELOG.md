# Changelog

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
