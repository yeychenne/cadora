# The AI-DLC method pack — user manual

Cadora is a conductor, but a conductor needs a score. The **AI-DLC method pack** is that score: an
installable *method* — rules plus inputs — that structures software work into three phases
(**inception → construction → operations**). Cadora lays the method into a workspace, drives a
phased topology through it, puts deterministic gates and human-review gates *between* the phases,
and can read the phases back as an audit trail. This manual is for the person installing the method,
driving it, and auditing the result.

There are two packs: **`aidlc`** — the v1 rules, stable, the default — and **`aidlc-v2`**, an
**EXPERIMENTAL** pinned upstream distribution. Most of this manual is about the stable pack; §5 and
§6 cover v2.

---

## 1. What the pack is, and why

AI-DLC is not a flag that changes how Cadora runs. It is a body of *rules* — a workflow plus
per-stage detail — that ships vendored inside Cadora at `cadora/aidlc_rules/`:

| File / dir | What it is |
|---|---|
| `core-workflow.md` | The adaptive workflow: the phase order, the stage-selection principle, the mandatory loading rules. |
| `rule-details/` | The detailed per-stage rules, grouped by phase: `inception/`, `construction/`, `operations/`, plus `common/`. |
| `RULES_VERSION` | The vendored rule-set version (currently `1.0.0`) and its upstream provenance. |

`cadora aidlc-init` installs those rules into a workspace as **backend-native project memory** — the
executor reads them the way it reads any instruction file — and drops your **inputs** (`vision.md`,
optionally `tech-env.md`) beside them. From that point the method is "loaded": a coding agent pointed
at the workspace follows AI-DLC first.

> **Why a method pack, not a prompt.** The rules are the same across backends and across runs — they
> are versioned, provenance-stamped, and gated by Cadora rather than pasted into a prompt. That is
> what lets the same three-phase shape be *driven* and *audited* instead of merely suggested.

---

## 2. Set up a workspace — `cadora aidlc-init`

```bash
cadora aidlc-init myapp --vision vision.md --tech-env tech-env.md
```

```
AI-DLC workspace ready at myapp (rules 1.0.0)
  installed: CLAUDE.md, .aidlc-rule-details/, vision.md, tech-env.md
  next: cadora run examples/aidlc.topology.yaml --executor claude --cwd myapp
```

What lands in `myapp/`:

- **The instruction file** — `CLAUDE.md` for Claude Code, `AGENTS.md` for Codex. The AI-DLC core
  workflow is written into a managed `<!-- cadora:aidlc -->` block, so the rest of the file is yours.
- **`.aidlc-rule-details/`** — the per-stage rules, under `inception/`, `construction/`,
  `operations/`, and `common/`.
- **`vision.md`** — your product vision (the required input for a run).
- **`tech-env.md`** — the technical-environment input (optional).

`--vision` and `--tech-env` each take either a **path** or **inline text**, so
`--vision "A CLI that converts CSV to Parquet"` works as well as `--vision vision.md`.

Choose the backend with `--executor`:

```bash
cadora aidlc-init myapp --executor codex --vision vision.md   # writes AGENTS.md instead of CLAUDE.md
```

`--executor` accepts `claude` (default), `codex`, or `kiro`. It only changes which instruction file
the rules are written into — the method is identical across backends.

---

## 3. The three phases, and the gates between them

The method's spine is three phases, each a directory under `.aidlc-rule-details/`:

| Phase | What happens | Example stages (rule files) |
|---|---|---|
| **inception** | Understand and design | `workspace-detection`, `requirements-analysis`, `workflow-planning`, `application-design`, `units-generation` |
| **construction** | Build and prove | `functional-design`, `code-generation`, `build-and-test` |
| **operations** | Deploy and operate | `operations` |

Cadora's role is to sit *between* the phases with two kinds of checkpoint:

- **Review gates** — a node marked `review: true`. With `--hitl`, Cadora pauses there and waits for a
  human to approve, request a same-stage revision, or abort — **before** downstream work starts.
- **Deterministic gates** — a node with `gate: build-test`. The gate re-runs the real build and
  tests; a non-zero exit blocks the run. A pass is re-earned, not asserted.

A node declares its phase, so routing can act on it: `--construction-executor codex` sends only the
construction-phase nodes to a second backend, leaving inception and operations on `--executor`.

---

## 4. Drive a phased topology — `cadora run`

A topology is where the phases become a driven DAG. Each node carries a `phase`, a `role`, its
`depends_on` edges, and — where the method wants a checkpoint — a `gate` or `review: true`. Four
phased examples ship with Cadora:

| Topology | Shape |
|---|---|
| `examples/aidlc.topology.yaml` | One node, the whole lifecycle in a single session, `gate: build-test`. |
| `examples/aidlc-phased.topology.yaml` | Four nodes with explicit `phase:` labels and executor routing. |
| `examples/aidlc-hitl.topology.yaml` | Three coarse stages with `review: true` on `requirements` and `design`. |
| `examples/aidlc-stages.topology.yaml` | The full per-stage DAG — seven inception + six construction stages, conditional self-skips. |

Here is the phased example, prompts elided:

```yaml
name: aidlc-phased

nodes:
  - id: requirements
    phase: inception
    role: requirements-analysis
    review: true

  - id: design
    phase: inception
    role: application-design
    depends_on: [requirements]
    review: true

  - id: code-generation
    phase: construction
    role: code-generation
    depends_on: [design]
    gate: build-test

  - id: build-test
    phase: construction
    role: build-and-test
    depends_on: [code-generation]
    gate: build-test
```

Drive it. `--vision` on `run` **installs the AI-DLC workspace into `--cwd`** before driving, so you
can go from an empty directory to a driven run in one command:

```bash
cadora run examples/aidlc-hitl.topology.yaml --vision vision.md --hitl
```

`--hitl` activates the `review: true` gates. When `requirements` finishes, Cadora pauses before
`design` starts:

- **Approve** — downstream work proceeds.
- **Request changes** — the *same* stage re-runs, with your comments prepended, before anything
  downstream starts.
- **Abort** — the run stops.

Every decision is recorded in the node's `human-review.md`. Without `--hitl`, the same topology runs
autonomously end to end.

**Routing construction to a second backend:**

```bash
cadora run examples/aidlc-phased.topology.yaml --vision vision.md \
  --executor claude --construction-executor codex --construction-model gpt-5.5
```

Inception and operations nodes run on `--executor` (`claude`); construction-phase nodes route to
`--construction-executor` (`codex`).

**Headless review (no TTY):** on a server there is no stdin to prompt, so `--hitl` alone would abort.
Use `--review-file` instead — Cadora writes `cadora-review-request.json` into the node workspace and
polls for a `cadora-review-decision.json`; any tool or human can drop the decision. It fails closed
on `--review-timeout` (default `3600` seconds).

> **Cost is marked.** Per-node cost is real where the backend reports dollars, and a price-table
> *estimate* (shown as `est.`) for token-only backends. Cadora never implies an estimate is a metered
> charge.

---

## 5. The two packs — `aidlc` vs `aidlc-v2`

`aidlc` (this manual so far) is the **stable default**: v1 rules, vendored, no network fetch, runs on
the funding source you chose, works on all three backends.

`aidlc-v2` is a **pinned upstream distribution** and is honestly labelled **EXPERIMENTAL**. Install
it with `--method aidlc-v2`:

```bash
cadora aidlc-init myapp-v2 --method aidlc-v2 --vision vision.md
```

```
aidlc-v2 pack (EXPERIMENTAL) installed at myapp-v2
  upstream: https://github.com/awslabs/aidlc-workflows@v2.1.7 (fde1e1af7aae)
  provider/cost pins stripped (recorded): model, effortLevel, CLAUDE_CODE_USE_BEDROCK, AWS_REGION
    (upstream default silently switches sessions to Bedrock at opus[1m]/xhigh;
     funding stays yours — restore with --keep-provider-pins)
  remote MCP servers NOT installed (opt in with --keep-mcp): 5 upstream servers
  install record: myapp-v2/.cadora-aidlc-v2.json
  next: cd myapp-v2 && claude   then run /aidlc — inspect any time with:
        cadora aidlc-audit myapp-v2
```

Three things make v2's installer *guarded* rather than a bare copy:

1. **It is pinned.** The default ref is a tag (`v2.1.7`) whose commit hash Cadora verifies after
   fetch. A moved tag **fails the install** rather than silently shipping different code. Override the
   ref with `--ref`.
2. **It strips provider/cost pins by default — and records what it stripped.** Upstream's shipped
   `.claude/settings.json` silently re-points every session at metered **AWS Bedrock** on
   `opus[1m]` at `effortLevel: xhigh` — the most expensive configuration. Cadora removes those pins
   (`model`, `effortLevel`, `CLAUDE_CODE_USE_BEDROCK`, `AWS_REGION`) so the run stays on **your**
   funding source, and writes exactly what it took out to `.cadora-aidlc-v2.json`.
   `--keep-provider-pins` opts back in.
3. **Its five remote MCP servers are opt-in.** Remote tooling is not installed unless you pass
   `--keep-mcp`.

Other v2-only flags: `--force` overwrites existing pack files. v2 currently supports
`--executor claude` only.

> **v2 is a GA preview with weekly churn.** The stripping is Cadora's *protective* behaviour — it
> keeps the run predictable and on the funding source you picked — but treat v2 itself as moving
> ground. For anything you need to be stable, use the default `aidlc` pack.

---

## 6. Read the trail back — `cadora aidlc-audit`

The v2 workflow writes its own state as it runs: a per-intent state file (`aidlc-state.md`, a
six-state checklist) and an append-only audit trail (`audit/<host>-<clone>.md` shards, a 68-event
taxonomy with ISO timestamps). `cadora aidlc-audit` is **read-only** — it parses both into one
summary:

```bash
cadora aidlc-audit myapp-v2
```

```
aidlc-v2 intent: 2026-07-16-checkout-service  (space: payments)
  phase=construction  current=code-generation  next=build-and-test  status=in-progress
  stages: done=6  skipped=2  revising=0  awaiting-approval=1  in-progress=1  not-started=2
  audit: 68 events  ·  human_turns=7  ·  sensors fired/passed/failed=19/18/1
    2026-07-16T14:22:07Z   stage.approved   requirements-analysis
    2026-07-16T15:03:44Z   gate.passed      application-design
    2026-07-16T16:41:12Z   stage.started    code-generation
```

Read it top to bottom:

- **`phase / current / next / status`** — where the intent is in the lifecycle.
- **`stages: …`** — the six-state roll-up, sorted most-advanced first. The glyphs in
  `aidlc-state.md` map to: `[x]` done · `[-]` in-progress · `[?]` awaiting-approval · `[R]` revising
  · `[S]` skipped · `[ ]` not-started.
- **`audit: N events`** — the total from the append-only trail, plus human turns and the
  sensor fired/passed/failed tally. The last lines are the gate/approval events.

Intents live under `aidlc/spaces/*/intents/*`. By default `aidlc-audit` reads the **newest** intent;
target a specific one with `--intent <dir-name>`. Pass `--json` for the full structured report.

Because ingestion is read-only and shape-normalized, it works whether the workflow was driven by
Cadora or by a human interactively in their IDE.

> `aidlc-audit` reads the **aidlc-v2** pack's own state files. For a stable `aidlc` run, the same
> evidence — nodes, gates, per-node cost, review decisions — lives in the run archive that
> `cadora report` packs.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no aidlc v2 intents under <ws> (aidlc/spaces/*/intents/*)` | `aidlc-audit` pointed at a non-v2 workspace, or the workflow hasn't created an intent yet | Point at a `--method aidlc-v2` workspace that has been driven at least once |
| `intent '<x>' not found; have: …` | `--intent` name doesn't match | Use one of the listed intent dir names (or omit `--intent` for the newest) |
| `the aidlc-v2 pack currently supports --executor claude only` | `--method aidlc-v2` with `--executor codex/kiro` | Use `--executor claude` for v2, or use the stable `aidlc` pack for other backends |
| `fetching …@<ref> failed` | Network, or a bad/moved ref | Check connectivity; pin a valid `--ref`. A moved tag failing is the pin *working* |
| v2 install shows nothing stripped, run goes to Bedrock | `--keep-provider-pins` was passed (or pins already absent) | Re-install without `--keep-provider-pins` to strip them and stay on your funding source |
| `WARNING: bun not found` after v2 install | v2's hooks (incl. the audit logger) need `bun` | `brew install bun` (or see bun.sh), then re-run |
| `--hitl` run aborts immediately on a server | No TTY to prompt on | Use `--review-file` (+ `--review-timeout`) so a decision file drives the gate |
| A `review` gate never pauses | `--hitl` was not passed | Add `--hitl`; without it, `review: true` nodes run through |

---

## 8. Reference

**`cadora aidlc-init <workspace>`** — set up an AI-DLC workspace (rules + inputs).
`--executor claude|codex|kiro` (default `claude`) ·
`--vision <path-or-inline>` · `--tech-env <path-or-inline>` ·
`--method aidlc|aidlc-v2` (default `aidlc`)
*aidlc-v2 only:* `--ref <ref>` · `--keep-provider-pins` · `--keep-mcp` · `--force`

**`cadora run <topology> --vision <path>`** — drive a topology (installs the workspace into `--cwd`).
`--executor <backend>` · `--cwd <dir>` · `--tech-env <path-or-inline>` ·
`--hitl` (activate `review: true` gates) ·
`--construction-executor <backend>` · `--construction-model <model>` ·
`--review-file` · `--review-timeout <seconds>` (default `3600`)

**`cadora aidlc-audit [<workspace>]`** — read-only summary of an aidlc-v2 workspace's state + the
68-event audit trail. `--intent <dir>` (default: newest) · `--json`

**Packs:** `aidlc` — v1 rules, vendored in `cadora/aidlc_rules/` (`core-workflow.md`,
`rule-details/`, `RULES_VERSION`), stable, the default. `aidlc-v2` — EXPERIMENTAL pinned upstream
dist; strips provider/cost pins by default and records them in `.cadora-aidlc-v2.json`.

**Phases:** `inception` · `construction` · `operations` — the three `.aidlc-rule-details/`
directories. A node's `phase` drives `--construction-executor` routing.

**Stage states** (`aidlc-state.md`): `[x]` done · `[-]` in-progress · `[?]` awaiting-approval ·
`[R]` revising · `[S]` skipped · `[ ]` not-started.
