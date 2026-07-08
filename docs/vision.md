# The Neutral Conductor

*A vision for agentic delivery without lock-in — free to run on any model and any subscription,
driven by a real method, and provable. **Green means proven, not claimed.***

---

## North star

Coding agents can now build real software — fast, and increasingly on their own. The open question
is no longer *can an agent build it?* It is *on whose terms?* Which model does the work. On whose
bill. Through what process. And with what proof.

A **neutral conductor** answers all four. It sits above the coding-agent CLIs and drives them
through a workflow *you* declare — so the *how* of delivery stays yours: any frontier model, on the
subscriptions you already pay for, through a real method, with a verdict you can actually trust. Not
one vendor's opinionated harness — the layer that spans them.

---

## Why now? The lock-in you don't see

Every major vendor now ships an excellent agentic harness. Each is also, quietly, a set of defaults
you didn't choose: it runs on *its* model, bills through *its* meter, follows *its* process, and —
most consequentially — grades its own output. Each choice is individually reasonable. Together, they
are lock-in.

As agentic delivery moves from novelty to core infrastructure, that lock-in becomes the real risk.
The scarce thing is no longer generation — it is **control**: over which model does each step, on
which subscription, through which method, and whether "done" is a fact or a claim. A conductor that
lives *above* the vendors is what returns that control.

---

## Three things only a neutral conductor gives you

**1. Freedom — any model, on your own subscriptions.** One declared workflow runs on Claude Code,
on Codex, on Kiro, on whatever comes next. A/B two models on identical work and compare on equal
terms; **phase-split** a run — design on one, build on another — to use the best model for each
step. And it runs on the seats you already pay for: subscription-funded by default, **one cost
ledger** across every vendor and funding source, no metered-API surprise. No single harness can
offer this, because each is *of* a vendor. A conductor above them can.

**2. Method — a real lifecycle, not a wall of prompts.** Ad-hoc prompting doesn't scale to real
delivery. A neutral conductor runs a *declared* method — aligned with a structured lifecycle like
**AI-DLC** (inception → construction → operations), phase by phase — so a run is something you can
reason about, repeat, and hand off. And because the method is declared rather than baked in, it's
yours to change: AI-DLC, BMAD, or your own.

**3. Proof — a verdict you can trust.** An agent will always report success. The hard, unsolved
part is *proving* it. A neutral conductor decides "green" itself — re-running the real build, the
real tests, the real scans — instead of taking the agent's word. And it does so from *outside*: a
vendor's tool grading that vendor's own agent is the fox auditing the henhouse. The record of a run
is a **signed, checksummed evidence pack**, tamper-evident and attributable, that verifies after it
leaves the machine that made it. **Green means proven, not claimed.**

Freedom decides *who* does the work. Method decides *how*. Proof decides whether to *believe* it —
and leaves the evidence.

---

## The tenets

1. **Sit above the vendors, not inside one.** Neutrality is the whole point — it's what makes the
   model choice free, the cost ledger cross-vendor, and the verdict trustworthy.
2. **The workflow is declared, and it's yours.** Any model, any method, any funding source — you
   decide; the conductor drives.
3. **Green means proven, not claimed.** A deterministic check decides the outcome; the agent's "it
   works" is input, never verdict.
4. **Deterministic-first.** Rules-based checks beat visual inspection beat an LLM as judge. The
   load-bearing gate is a real command — reproducible and impossible to sweet-talk.
5. **Fail closed.** A suite that ran zero tests didn't pass; a package that won't build isn't green;
   a missing human decision aborts.
6. **Proof is portable and attributable.** Signed, checksummed evidence is the unit of trust — it
   travels with the software.
7. **Cost is evidence too.** What each run spent — per model, per vendor — is part of the record.

---

## The three-layer model

Agentic delivery has three layers, and a neutral conductor spans all three:

| Layer | The question | Where it's heading | The conductor's role |
|-------|--------------|--------------------|----------------------|
| **Orchestration** | *drive the steps* | commoditized — harnesses self-orchestrate | run *your* declared DAG across *any* backend |
| **Method** | *how to build* | owned by the method communities (AI-DLC, BMAD) | drive the method you chose, method-agnostically |
| **Trust** | *prove it's done* | **open** | the deterministic, external verifier + signed evidence |

The first two are being commoditized and owned. The third — trust — is open, and by nature
method-agnostic and vendor-neutral. A conductor that spans all three is the one place they compose.

---

## Honest boundaries

A paper about proof should be honest about its own.

- **What is real today:** the model-neutral conductor, the cross-vendor cost ledger, the
  deterministic gates (with substance and prerequisite classification), the tamper/integrity scan,
  and the signed + checksummed evidence pack.
- **What is a deliberate stub:** an LLM acting as judge — the *least* trustworthy verifier, so it
  sits last and never gates on its own.
- **Where this is:** [Cadora](https://github.com/yeychenne/cadora) is a working reference
  implementation — shipped, tested, backend-neutral. It is offered as the capstone of an
  exploration, not a finished commercial platform. The vision stands on its own; the tool shows it
  holds.

---

## The aspiration

Agentic delivery that no single vendor owns end to end: **free** to run on any model and any
subscription, **driven** by a method you chose, and **provable** — where *"the AI built it"* is
followed, as a matter of course, by *"— and here is the proof."* Generation is solved. Control is
the frontier.
