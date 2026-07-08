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
- **Drive to completion, honestly** — a failed gate needn't be the end. With `--remediate N`,
  Cadora feeds the exact gate failure to a fresh, constrained session and re-runs the *same* gate,
  up to N bounded attempts, until it genuinely passes (**`completed-green`**) or it stops with the
  full attempt trail (**`honest-blocked`**). It never weakens the gate or accepts the agent's
  claim of success — the gate becomes the engine of completion, not just a tripwire. Opt-in; bound
  spend with `--remediate-max-cost`.
- **Tamper detection** — `cadora integrity` detects generated packages and scripts that
  impersonate real tools, unrecognized build substitutions, tests run against another project's
  environment, and **hollow code** — a threshold of stub function bodies (`pass` / `...` /
  `raise NotImplementedError`) that the passing gate misses. Modes: `audit` (record), `enforce`
  (block), `repair` (one constrained fix session, then re-verify).
- **Fail-closed human review** — mark nodes `review: true` and run `--hitl`: the operator must
  approve, request bounded revisions, or abort; closed stdin aborts rather than silently
  approving. Every decision, comment, and revision cost is archived. The review surface is
  pluggable over **MCP** — run `cadora mcp` and drive `start_run` / `review_gate` /
  `submit_review` / `get_artifact` / `run_status` from any MCP client (Claude Code, Claude
  Desktop, Codex CLI, or a networked client). See [docs/hitl-mcp.md](docs/hitl-mcp.md).
- **Per-node cost attribution, cross-vendor** — every node records its backend, model, tokens,
  and dollars, split by funding source (subscription vs metered API). `cadora usage` and the
  local dashboard's FinOps panel aggregate by model / backend / funding / day — one ledger even
  when design runs on Claude and code runs on Codex.

The evidence of a run *is* the archive: `runs/<id>/manifest.json` + per-stage artifacts + the
event stream + gate/integrity/review outcomes + cost. Inspect with `cadora archive ls / show`
or the dashboard — and hand it to someone with **`cadora report <run-id>`**: a portable
**evidence pack** (self-contained `report.html` + `report.json` + a SHA-256 `checksums.txt`
covering every archived file), verifiable after it leaves your machine
(`shasum -a 256 -c`). The pack states exactly what it claims — and that it's checksummed,
not signed.

## Backends

| Backend | Drive | Notes |
|---|---|---|
| `claude` (default) | `claude -p`, structured stream-json | **subscription-funded by default**; metered API is explicit opt-in (`--funding api`) |
| `codex` | `codex exec --json`, structured JSONL | uses your Codex login/plan |
| `kiro` | `kiro-cli chat --no-interactive` | bills **subscription credits** (shown in FinOps); live-verified on 2.10.0 |
| `glm` *(experimental)* | Z.ai's Anthropic-compatible endpoint behind the `claude` CLI | `ZAI_API_KEY`; Anthropic credentials are stripped from the env; dollars computed from the public Z.ai rate table (flagged `est.`) |
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

**EXPERIMENTAL — aidlc-workflows 2.0 pack.** `cadora aidlc-init <ws> --method aidlc-v2` installs
upstream's v2 engine (pinned tag, **commit-verified** — a moved tag fails the install) with a
guarded twist: upstream's defaults silently re-point every session at **Bedrock us-east-1 with
`opus[1m]` at `xhigh` effort** and wire five remote MCP servers; the installer **strips those
pins by default and records exactly what it stripped** in `.cadora-aidlc-v2.json` (restore with
`--keep-provider-pins` / `--keep-mcp`). Then drive `/aidlc` yourself in Claude Code, and inspect
the run any time — read-only — with:

```bash
cadora aidlc-audit ./my-project          # state + 68-event audit trail summary
cadora aidlc-audit ./my-project --json   # full structured events (gates, sensors, human turns)
```

Requires `bun` (v2's hooks; `cadora doctor` checks it). The full external driver for v2 is
deliberately deferred while upstream's GA preview stabilizes its gate surface.

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

Run these commands from a clone of this repository so the `examples/` files are present.

```bash
# 0. Check your backend CLIs against the tested contract ranges (offline, no model calls).
cadora doctor

# 1. Set up a workspace from your product vision (installs the AI-DLC method pack).
cadora aidlc-init ./my-project --vision vision.md

# 2. Drive the workflow on Claude Code — autonomous, gated, subscription-funded.
cadora run examples/aidlc.topology.yaml --vision vision.md --cwd ./my-project

# 3. Read the evidence — package it, judge it, compare it.
cadora archive ls
cadora archive show <run-id>
cadora report <run-id>         # portable evidence pack: html + json + sha-256 checksums
cadora eval <run-id>           # deterministic verdict + CI-friendly exit code
cadora eval <run-id> --judge   # + opt-in LLM rubric (advisory; any backend; never overrides)
cadora compare <run-a> <run-b> # per-node outcome/model/cost diff — the measured A/B
cadora deliverable <run-id>    # client-facing delivery report (markdown; --docx / --pptx optional)
cadora usage                   # tokens + dollars/credits by model / backend / funding
cadora dashboard               # local cockpit: DAG cost/quality map + FinOps panel
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

## Security model — read before pointing it at your code

Cadora **audits the agent's output** (deterministic gates, integrity checks, evidence) — it does
**not sandbox the agent's execution**. An autonomous run drives the backend with
`--dangerously-skip-permissions` inside your `--cwd`: the agent reads, writes, and runs commands
there with your user's permissions, and gates may install dependencies (executing agent-authored
build hooks). So:

- **Point Cadora only at a trusted or throwaway workspace** — a fresh directory, a git worktree,
  or a container. Not your home directory, not a repo with secrets in it.
- **Keep credentials out of that environment.** Executors drop ambient provider keys where they
  can (e.g. the `claude` backend strips a stray `ANTHROPIC_API_KEY` in subscription mode), but the
  workspace itself is the agent's to touch.
- Every autonomous run prints a blast-radius banner and, interactively, asks once to proceed;
  CI/automation bypasses with `--yes` or `CADORA_ASSUME_YES=1`.
- The **dashboard and MCP server are localhost-only with no authentication.** Cadora refuses to
  bind either to a non-loopback host unless you pass `--i-understand-no-auth` — front them with
  TLS + auth before exposing them. See [docs/dashboard.md](docs/dashboard.md).
- The pre-publish **leak scan is a codename denylist, not a general secrets scanner** — it guards
  *our* release hygiene, it is not a substitute for your own secret-scanning on generated code.

New to Cadora, or bringing it to a hackathon? Start with the
[getting started guide](docs/getting-started.md), the
[hackathon quickstart](docs/hackathon-quickstart.md) (5 commands), and the
[5-minute demo script](docs/demo-script-5min.md). Curious how gates decide *green*? Read the
[verification-gates whitepaper](docs/verification-gates.md).

## Status

**v0.8.1** — an urgent gate-correctness fix: a project that *declares* an installable package but
cannot `pip install .` (flat layout, no `packages` config) no longer false-greens — it fails as a
remediable `packaging_failed` gate that `--remediate` repairs. Adds **run resumption**
(`--resume-from` / `--skip`) to re-run only what a late failure left, and a
[verification-gates whitepaper](docs/verification-gates.md). On the **v0.8.0** run-detail &
headless-ops base: the dashboard shows the **prompt given at entry**, a **failure analysis** of why
a run stopped, **live per-node credits and duration**, and **rendered markdown artifacts** — see
what a run was told, why it failed, and what it cost without leaving the browser. Headless HITL
(`cadora run --review-file`) and `cadora gate-check` (verify existing code, no executor) make review
and verify-only runs work without a TTY, and executor failures carry the exit code, timeout, and
stderr tail. Built on **v0.7.1**'s field-hardened per-gate commands (a topology `gates:` map),
parallel waves (`--max-parallel N`), and scoped review;
**v0.7.0**'s drive-to-completion loop (`--remediate N` re-runs a failed gate against a fresh session
until it genuinely passes); and v0.6.0's evidence pack, `eval` (+ judge), `compare`, `deliverable`,
`doctor` (all backends), Kiro credits, and trust gate. 220+ tests, `ruff` clean, CI on
Python 3.10–3.12.

**Roadmap:** signed evidence packs, full MCP auth, CI secrets-scanner + lockfile hardening, a
backend contract matrix, a container sandbox wrapper, and additional backend/method packs as they
earn verification.

## License

MIT. The vendored AI-DLC rule-set is MIT-0 (`awslabs/aidlc-workflows`).
