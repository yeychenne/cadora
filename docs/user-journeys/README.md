# Cadora by capability — user journeys

A guided tour of everything Cadora does, one capability at a time. Each capability comes with three
companion documents:

- **User journey** — a narrated, screen-by-screen walkthrough from the operator's chair (HTML).
- **User manual** — the command reference: every flag, worked examples, and a troubleshooting table (Markdown).
- **Design spec** — the design system behind the screens, for rebuilding or extending the UI in Figma or a design tool (HTML).

> The **manuals render right here on GitHub**; the **journeys** and **design specs** open on the
> [docs site](https://yeychenne.github.io/cadora/docs/index.html). New to Cadora? Start with
> [Run a gated workflow](https://yeychenne.github.io/cadora/docs/user-journeys/run-a-gated-workflow/user-journey.html), then follow the run's own evidence
> through [Gates & integrity](https://yeychenne.github.io/cadora/docs/user-journeys/gates-and-integrity/user-journey.html) and the
> [Evidence pack](https://yeychenne.github.io/cadora/docs/user-journeys/evidence-pack/user-journey.html).

---

## The audit-grade core

The loop that turns *"an AI wrote it"* into a run you can repeat, gate, cost, and prove.

### ⚙️ Run a gated workflow
Declare a small YAML DAG, drive it on a backend, and watch deterministic gates decide *green* from real exit codes.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/run-a-gated-workflow/user-journey.html) · [Manual](run-a-gated-workflow/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/run-a-gated-workflow/design-spec.html)

### 🔒 Gates & integrity
How a gate re-runs the real build and tests (a suite that ran zero tests is blocked), and how toolchain-integrity catches impersonated tools and hollow, stubbed-out code.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/gates-and-integrity/user-journey.html) · [Manual](gates-and-integrity/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/gates-and-integrity/design-spec.html)

### 👤 Human review
Fail-closed human-in-the-loop gates — approve, request a same-stage revision, or abort — on three surfaces (stdin, file-drop, MCP), with a conversational review dashboard.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/human-review/user-journey.html) · [Manual](human-review/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/human-review/design-spec.html)

### 🔏 Evidence pack
Turn a run into a portable, tamper-evident bundle — report + SHA-256 checksums + an optional signature — that anyone can verify offline, with no Cadora installed.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/evidence-pack/user-journey.html) · [Manual](evidence-pack/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/evidence-pack/design-spec.html)

### 💰 Cost & FinOps
One cross-vendor cost ledger — per-node dollars and credits by model, backend, funding source, and day, with price-table estimates flagged.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/cost-finops/user-journey.html) · [Manual](cost-finops/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/cost-finops/design-spec.html)

---

## Backends, recovery & evaluation

Swap the engine underneath, recover a broken run, and weigh two runs against each other.

### 🔀 Multi-backend executors
One topology, swappable engines (Claude Code · Codex · Kiro · GLM · Antigravity) — including phase-split runs — and the same audit-grade proof on every engine.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/multi-backend/user-journey.html) · [Manual](multi-backend/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/multi-backend/design-spec.html)

### 🔁 Resume & remediation
Resume an interrupted run on provenance-verified trust, or let a bounded auto-repair loop clear a red gate — where *green* is only ever the real gate re-passing.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/resume-remediation/user-journey.html) · [Manual](resume-remediation/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/resume-remediation/design-spec.html)

### ⚖️ Compare & evaluate
Diff two runs (cross-backend or over time) and score one against deterministic invariants — pure functions over the run manifests: no LLM, no network, reproducible and free.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/compare-evaluate/user-journey.html) · [Manual](compare-evaluate/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/compare-evaluate/design-spec.html)

---

## Method, delivery & integration

Install the method, hand off the result, and drive the conductor from another agent.

### 🧭 AI-DLC method pack
Install the AI-DLC method as rules + inputs and drive it as a phased, gated topology (inception → construction → operations); read the phases back as an audit trail.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/aidlc-method/user-journey.html) · [Manual](aidlc-method/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/aidlc-method/design-spec.html)

### 📦 Deliverable pack
Turn one archived run into a client-facing delivery report — the readable story that sits on top of the checksummed evidence, under the same honesty contract.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/deliverable-pack/user-journey.html) · [Manual](deliverable-pack/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/deliverable-pack/design-spec.html)

### 🔌 MCP server
Run Cadora as an MCP server so another agent — Claude Desktop, Claude Code, the Codex CLI — can start gated runs, watch them, pull artifacts, and service the human-review gate.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/mcp-server/user-journey.html) · [Manual](mcp-server/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/mcp-server/design-spec.html)

### 🧑‍🤝‍🧑 Journey-first builds
Make the built app's user journey a gated artifact: the vision demands `user-journey.md`, a human reviews it at the requirements gate, and the design must trace to it.
[Journey](https://yeychenne.github.io/cadora/docs/user-journeys/journey-first-builds/user-journey.html) · [Manual](journey-first-builds/user-manual.md) · [Design](https://yeychenne.github.io/cadora/docs/user-journeys/journey-first-builds/design-spec.html)

---

*Looking for the bigger picture first? Read [the vision](../vision.md), see [how gates decide *green*](../verification-gates.md),
or skim the [one-page overview](https://yeychenne.github.io/cadora/docs/index.html).*
