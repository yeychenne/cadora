# Changelog

## v0.8.1 — 2026-07-08

An urgent gate-correctness fix (a Python packaging defect could pass the gate), plus run resumption
and a gate-design whitepaper. Ships on top of v0.8.0.

### Added
- **Run resumption — `cadora run --resume-from <node>` / `--skip <node,...>`.** When a run fails
  late, re-run only what's left: `--resume-from build` skips every node upstream of `build`
  (trusting their artifacts already in `--cwd`), then runs `build` and everything downstream;
  `--skip` names nodes directly. Skipped nodes are recorded `skipped` (never `completed`) in
  `status.json`, with run-level `resumed_from` / `skipped_nodes`; a resumed node's prompt points at
  the upstream artifacts on disk instead of an empty piped output. Unknown node names fail fast,
  before any agent runs. Saves the credits and the 15–20-min-per-node cost of redoing already-green
  inception phases just to reach a failed `BUILD`.
- **Verification-gates whitepaper (`docs/verification-gates.md`).** A from-the-code explanation of
  how gates decide "green" — the five-value status vocabulary, prerequisite / vacuous / packaging
  classification, out-of-workspace isolation, and the bounded remediation loop.

### Fixed
- **No more false-green on a package that does not build.** A workspace that *declares* an
  installable package (`[build-system]`/`[project]`) but has a flat layout with several top-level
  packages and no explicit `packages` config makes `pip install -e .` panic on setuptools
  auto-discovery. The gate used to fall back to installing dev tooling only and then **pass** on
  tests that import from the working directory — certifying a package that `pip install .` /
  `python -m build` (and CDK/Lambda bundling) cannot actually produce. Cadora now records this as a
  remediable **`packaging_failed`** gate status carrying a concrete fix hint (declare
  `[tool.setuptools.packages.find]`, `packages`/`py-modules`, or move the code under `src/`), so
  `--remediate` repairs the `pyproject.toml` instead of shipping a false pass. The provision cache
  is not written on a packaging defect, so a stale stamp can never resurrect the false-green. Other
  editable-install failures keep the tooling-only fallback (no new false-blocks in offline /
  wheelhouse mode).

## v0.8.0 — 2026-07-08

The run-detail & headless-ops release: see what a run was told, why it stopped, and what it cost —
and run review and verify-only steps in non-interactive contexts. The dashboard's run-detail view
gains the **prompt given at entry**, a **failure analysis**, **live per-node credits and duration**,
and **rendered markdown artifacts**; headless HITL (`cadora run --review-file`) and
`cadora gate-check` make Cadora operable without a TTY; and executor failures now name the exit
code, timeout, and stderr tail instead of a bare "executor failed".

### Added
- **Headless HITL — `cadora run --review-file`.** For non-interactive runs (Quick Desktop, CI,
  background), a `review: true` gate no longer aborts on the missing TTY: Cadora writes
  `cadora-review-request.json` into the node workspace (listing the stage's documents) and polls
  for a `cadora-review-decision.json` — any tool or human drops
  `{"decision": "approve"|"request_changes"|"abort", "comments": "…"}`. Fails closed: an invalid
  or absent decision within `--review-timeout` (default 3600s) → abort. The stdin path's abort
  message now names the escape hatches (`--review-file`, the MCP review surface, `--yes`).
- **`cadora gate-check <topology> --cwd <workspace>`** — run a topology's gates against an
  existing workspace with **no executor and no LLM cost**: "I already have code, just verify it."
  Honors the per-gate `gates:` map, runs a shared gate once, and exits non-zero if any gate fails
  (CI-ready). Fixes the field pain where verifying existing code meant re-running the whole
  topology (which re-invoked the agent and could pile new code on top).
- **Dashboard run-detail: entry prompt + failure analysis.** The run-detail view now shows the
  **prompt given at entry** — the topology's root-node prompts plus the workspace `vision.md`,
  captured to `run-input.json` at run start — and a **failure-analysis** surface on a failed run: a
  banner naming the failing node and reason, plus a per-node block with the gate command's actual
  output and any integrity findings (using this release's descriptive executor reason). Builds on
  the existing DAG view, node inspector, and artifact browser; still dependency-free vanilla JS.
- **Dashboard renders markdown artifacts.** Clicking a `.md` artifact in the run-detail inspector
  now shows it **rendered** (headings, bold, inline/fenced code, lists, links) instead of raw text —
  the dominant AI-DLC artifact type. Safe by construction: content is HTML-escaped before any
  formatting, so only Cadora's own tags render (an artifact cannot inject markup). Non-markdown
  artifacts keep the monospace raw view.

### Changed
- **Live per-node credits & duration in `status.json`.** Each node's `status.json` entry now
  carries `credits` and `duration_seconds` the moment it completes — not only in the end-of-run
  `manifest.json` — so live-monitoring tools and the dashboard show credit spend and per-node
  timing *during* a run. The dashboard node inspector surfaces both, and `cadora dashboard` now
  prints the resolved archive path it is serving (so a "no runs visible" archive-dir mismatch is
  obvious at a glance).
- **Descriptive executor failures.** A failed executor node is no longer recorded as a bare
  "executor failed" — the reason now carries the exit code, the timeout (with its limit), and the
  last stderr line, e.g. `executor failed (exit 1: kiro: not authenticated)` or
  `executor failed (timed out after 600s)`. The Kiro backend captures a `stderr_tail` on failure so
  the *why* (auth / credits / crash) reaches the manifest instead of vanishing.

## v0.7.1 — 2026-07-07

The field-feedback release from a full day of real multi-project hackathon runs: gates and
provisioning that survive real-world layouts, genuinely parallel waves, and a clearer review
surface.

### Added
- **Per-gate-type commands in the topology (`gates:` map).** A topology can now declare a command
  per gate name — e.g. `build-test` runs `ruff check . && pytest -q` while an inception
  `artifact-check` runs a cheap `test -f aidlc-docs/design.md` with `setup: off` (no venv). Gates
  not named in the map fall back to the run-level `--gate-cmd` / `--gate-setup`. Fixes the #1 field
  pain — one global `--gate-cmd` (ruff+pytest) crashed on inception phases that produce only
  markdown, forcing users to strip gates off every inception node.
- **Wave concurrency — `cadora run --max-parallel N`.** Independent nodes in a dependency wave now
  run their agent execution **concurrently** (up to N at once) instead of strictly one at a time,
  cutting wall-clock on wide topologies toward the slowest node per wave. Only the executor call is
  parallelized; gates, integrity scanning, remediation, human review, and archiving stay
  **sequential and deterministic**, so manifest order and every fail-closed guarantee are
  unchanged. Dependencies are always respected. Opt-in: default `1` (sequential).

### Changed
- **HITL review gate surfaces the stage's own document(s).** When a `review: true` node pauses for
  human review, Cadora now shows exactly what *that stage* produced instead of only naming the doc
  directory: the CLI lists each new/modified file under `aidlc-docs/` with a short content preview,
  and the MCP `review_gate` scopes its `artifacts` to those documents rather than the whole tree.
  Backward compatible — existing 2-arg `review_fn(node, cwd)` callbacks are unaffected.

### Fixed
- **`--gate-setup auto` robustness (three fixes from a three-backend field test).** (1) `pip
  install -e .` is attempted only when the workspace actually declares an installable package
  (`[build-system]`/`[project]`, `setup.py`, or `setup.cfg`) — agents routinely write a tool-only
  `pyproject.toml`, which made setuptools flat-layout discovery abort the whole provision and
  block the gate on "missing tooling"; if an editable install fails anyway, provisioning retries
  with the dev requirements alone so the gate can still run. (2) The gate virtualenv moved out of
  the workspace (`~/.cache/cadora/gate-venvs/<hash>`, override with `CADORA_GATE_CACHE`) so
  `.`-globbing gates (`ruff check .`, `mypy .`) no longer scan Cadora's own provisioned
  third-party code and false-fail. (3) A gate failure caused by an unimportable package that
  lives *in the workspace* is now a remediable `GATE_FAILED` (fixable packaging bug —
  `--remediate` can drive it) instead of a terminal `blocked_prerequisite`; missing *external*
  dependencies remain terminal.

## v0.7.0 — 2026-07-05

Drive-to-completion release: the deterministic gate becomes the engine of completion — a failed
gate is fed back to a fresh constrained session and re-run, bounded, until it genuinely passes or
stops honestly.

### Added
- **Hollow-code detection (`stub-implementation` integrity finding).** `cadora integrity` now
  catches genuinely hollow code — a threshold of functions whose body is only `pass`, `...`, or
  `raise NotImplementedError` — the blind spot the build/test gate misses (stubbed code with weak
  tests still goes green). Abstract methods, `Protocol`s, `@overload`, and `.pyi` are excluded, so
  it fires on real hollowness, not interfaces. As a blocking finding it composes with the
  remediation loop: under `--integrity-mode enforce`/`repair` a hollow-but-passing build is driven
  to real code; under the default `audit` it's recorded, not blocking.
- **Drive to completion — `cadora run --remediate N`.** When the deterministic build/test gate
  fails, Cadora now feeds the *exact* gate failure into a fresh, constrained session and re-runs
  the **same** gate — up to N bounded attempts — terminating in **`completed-green`** or
  **`honest-blocked`** with the full per-attempt trail archived (prompt, output, gate re-run,
  cost). The gate is never weakened and "green" is never the agent's claim: it's the same gate
  genuinely passing, and a false-green guard test pins that. `vacuous` gates are remediable;
  `blocked_prerequisite` stays terminal. Opt-in (default off); bound spend with
  `--remediate-max-cost`. The loop lives in `cadora/remediation.py`; the runner wires it in.
  Proven on a real docs-not-code run that previously blocked with nothing runnable — remediation
  drove it to a gated green in one attempt.

## v0.6.0 — 2026-07-05

The hackathon-readiness release: trust-gated autonomous runs, one cost ledger across
`archive` / `report` / `eval` / `compare` / `usage` / dashboard, full advertised-backend coverage
in `cadora doctor`, and fail-closed localhost guards for the dashboard and MCP server — on top of
the portable evidence pack, deterministic `eval` (+ opt-in LLM judge), cross-backend `compare`,
the `deliverable` report, Kiro credits, the experimental GLM and AI-DLC v2 paths, and a
tester-ready onboarding kit.

### Added
- **Autonomous-run trust gate** — every autonomous run prints a blast-radius banner (backend,
  workspace, skip-permissions) and, interactively, asks once to proceed; CI/automation bypasses
  with `--yes` / `CADORA_ASSUME_YES=1`. Cadora audits the agent's *output*, not its *execution* —
  the README now has a **Security model** section stating this plainly.
- **`cadora doctor` covers all advertised backends** — kiro (kiro-cli ≥ 2.10.0) and glm (claude
  present + `ZAI_API_KEY`) join the checks, so the first command a new user runs tells the whole
  truth about what's usable.
- **Onboarding kit** — a `docs/hackathon-quickstart.md` (5-command flow), a
  `docs/demo-script-5min.md`, and a tiny `examples/hackathon-hello.vision.md` that builds in ~2
  minutes for a fast, reliable live demo.

### Changed
- **One cost source across every surface** — `cadora archive show` / `ls` now price nodes through
  the usage layer like `usage` / `report` / `eval` / `compare` / dashboard already do, so a
  Codex/GLM run no longer shows real dollars in one command and `$0.00` in another (estimates are
  flagged `est.`; Kiro credits shown alongside).

### Security
- **Unauthenticated surfaces fail closed on non-loopback binds** — `cadora mcp` and
  `cadora dashboard` refuse a non-loopback `--host` unless `--i-understand-no-auth` is passed.
- **MCP `get_artifact` path traversal fixed** — the artifact reader now resolves and fail-closes
  any path escaping the run workspace (a `../` path from any connected MCP client could
  previously read arbitrary files). Found by the internal security review board.
- **Codex `stderr_tail` is redacted** before landing in the archived (dashboard-served)
  manifest — credential-shaped strings (bearer/`sk-`/key=…) are masked in case the backend CLI
  ever logs a token on an error path.
- `scripts/refresh-aidlc-rules.sh` now defaults to a **pinned release tag**, never `main`.

### Fixed
- **Claude/GLM node timeouts are archived, not raised** — a hung `claude -p` (or GLM) node now
  returns an archivable failure (`exit_code=124`, `timed_out` meta, partial stream captured)
  instead of escaping the runner and losing the evidence for exactly the failure long agent runs
  hit most. Codex and Kiro already behaved this way.
- **GLM per-node `model:` overrides route correctly** — the `--model` flag is now stripped for
  the child call in every case (it previously leaked through for per-node overrides, handing GLM
  ids to the Claude CLI instead of the env aliases).
- **Evidence-pack checksums verify under `--out`** — the report checksum line references the
  actual output location (relative to the run dir when possible, absolute otherwise) instead of
  a hard-coded `report/` prefix.
- **Price-table prefix matching prefers the longest prefix** — dated ids like
  `gpt-5.4-mini-2026…` price as mini, never as `gpt-5.4` (was over-charging 3.3–10×).

### Added
- **EXPERIMENTAL GLM backend** — `--executor glm` drives Zhipu GLM (default `glm-5.2`) through
  Z.ai's Anthropic-compatible endpoint behind the existing `claude` CLI: same stream-json
  contract, same archive. Guarded by construction: ambient Anthropic credentials
  (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`) are stripped from the subprocess so a GLM run
  can neither bill Anthropic nor leak an Anthropic key to a third-party endpoint; auth comes
  only from `ZAI_API_KEY`. The CLI's client-side cost estimate (an Anthropic price table that
  doesn't know GLM) is discarded and dollars are computed from the public Z.ai rate table with
  Anthropic-wire cache semantics, flagged `est.`. Live smoke (`scripts/live-smoke.sh glm`)
  gates promotion out of experimental.
- **Kiro backend live-verified + credits in FinOps** — the `kiro` executor is verified against
  kiro-cli 2.10.0 (real captured output in the test fixtures; one full AI-DLC topology run =
  **3.68 credits**). Kiro reports **subscription credits**, not tokens/dollars, so `cadora usage`
  (totals + by model/executor/funding), `--json`, and `cadora archive show` now carry a
  `credits` dimension alongside dollars — one FinOps ledger across three billing models
  (Claude $, Codex tokens→$, Kiro credits).
- **`cadora deliverable <run-id>`** — generate a client-facing delivery report from an archived
  run (`md` now, `docx` as an optional extra — core stays pyyaml-only), with `--client` /
  `--project` header fields: the consulting hand-off document generated from the same evidence
  the archive already holds.
- **`cadora eval <run>` — deterministic run evaluation.** Six checks, no LLM cost: run
  completion, per-node success, gate verdicts, integrity findings (critical — gate the verdict
  and the exit code, so it drops into CI) plus cost attribution and artifact capture (warnings).
  Cost attribution counts price-table estimates; the integrity check explains when it is stricter
  than the run's own audit mode.
- **Opt-in LLM-as-judge on `eval`** — `cadora eval <run> --judge [--judge-executor …]` adds a
  rubric-scored advisory verdict from **any backend** (judge a Claude run with Codex or vice
  versa). Off by default, its cost is reported, and it **never overrides the deterministic verdict**.
- **`cadora compare <a> <b>` — diff two archived runs.** Per-node outcome/model/cost with
  ok-regression and node-presence flags, a run-level cost delta, and a same-topology guard — the
  cross-backend A/B (Claude vs Codex on one topology), measured instead of guessed.
- **`cadora report <run-id>` — the evidence pack.** Turns one archived run into a portable,
  self-contained proof pack: `report.html` (single file, no external assets — attach it to a
  deliverable), `report.json` (structured), and `checksums.txt` (SHA-256 of every archived run
  file + the report itself; verify with `shasum -a 256 -c`). Covers deterministic gate verdicts
  (incl. vacuous/blocked), integrity findings, the human-review trail, and per-node cost across
  backends with estimated costs explicitly flagged. States its claims honestly: checksummed,
  not signed (signing is on the roadmap).
- **EXPERIMENTAL aidlc-workflows 2.0 method pack** — `cadora aidlc-init --method aidlc-v2`
  installs upstream's v2 engine from a **pinned, commit-verified tag** (a moved tag aborts the
  install), **strips upstream's silent provider/cost pins by default** (`CLAUDE_CODE_USE_BEDROCK`,
  region, model aliases, `model: opus[1m]`, `effortLevel: xhigh`) and leaves the five remote MCP
  servers uninstalled unless opted in — recording exactly what was stripped in
  `.cadora-aidlc-v2.json`. New read-only **`cadora aidlc-audit`** summarizes a v2 workspace's
  state file and 68-event audit trail (gates, human turns, sensors; `--json` for the full event
  stream). `cadora doctor` now also checks for `bun` (v2's hook runtime). The full external
  driver for v2 is deferred while upstream's GA preview stabilizes its gate surface.
- **`cadora doctor`** — offline backend-CLI contract checks: Python floor, backend binary
  presence, and whether each CLI's version falls inside the range the adapter was last verified
  against (backend CLIs ship weekly with no machine-output stability guarantee — drift is the
  top operational risk). Outside-range is a warning (`untested`), missing/unparsable is the hard
  signal; `--json` for machines. Exits non-zero only when no live backend is usable.
- **Codex dollar cost** — nodes that report tokens but no dollars (Codex) are now priced from
  the public OpenAI rate table (gpt-5.5 / 5.4 / 5.4-mini / 5.4-nano / 5.3-codex; cached input
  billed at the cached rate). ChatGPT-plan credit-funded runs price identically (the credit rate
  card maps to API rates exactly). Estimated costs are **flagged**: `cadora usage` prints the
  estimated-node count, and `--json` exposes per-node `cost_estimated` plus the new
  `reasoning_output_tokens`. A backend-reported cost is always authoritative.

## v0.5.0 — 2026-07-03

Multi-backend + repositioning release: drive design and construction on different agent backends
from one conductor with per-backend cost attribution, and reposition Cadora as the **audit-grade
conductor** — with AI-DLC as the flagship method pack.

### Changed
- **Repositioning** — Cadora's identity is the **audit-grade conductor** for coding-agent CLIs:
  deterministic fail-closed gates, tamper detection, run evidence, and per-node cross-backend
  cost attribution. The AWS AI-DLC method remains the flagship built-in workflow — a **method
  pack, not the product identity**. README and architecture docs rewritten accordingly.

### Removed
- The **quick-desktop HITL review surface** (parked) — the terminal and MCP review surfaces
  remain the supported ways to run fail-closed human review.
- The **analyst-frontend example topology and doc** — moved out of the core distribution; it
  lives on as a sample application of the conductor rather than a core feature.

### Added
- **Multi-backend runs** — `cadora run` gains `--construction-executor` / `--construction-model`,
  routing construction-phase nodes to a second backend (e.g. **design on Claude Code, code on
  Codex**) while inception/operations nodes stay on `--executor`.
- **Per-node executor attribution** — each node records the backend that ran it, so `cadora usage`
  and the dashboard FinOps **"by executor"** panel split cost/tokens across backends (claude vs
  codex) instead of lumping everything under the run-level executor; the run-detail node panel now
  shows each node's **backend**. Codex `cached_input_tokens` are counted toward context.

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
