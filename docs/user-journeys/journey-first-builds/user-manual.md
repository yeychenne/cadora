# Journey-first builds — user manual

When Cadora builds an application, the AI-DLC method drives the engineering artifacts hard:
requirements with full FR/BR/API/NFR/AC coverage, a traceability matrix, an application design,
a tested build. A **user journey is not among its default artifacts** — so an app can arrive
engineering-complete with its human half unexamined. **Journey-first building** closes that gap:
the operator's vision names a user-journey document as a requirements-stage deliverable, a human
reviews it at the requirements gate, and the design must trace to it. This manual is for the
operator writing the vision and the reviewer holding the gate.

> **Method, not feature.** Nothing below is a new Cadora capability. Journey-first composes two
> things that already exist — a vision file and `review: true` gates under `--hitl` — into a
> discipline. The gate mechanics are documented in the *human review* manual and the method pack
> in the *AI-DLC* manual; this one is about using them so the built app's human story is
> examined, not assumed.

---

## 1. The dead end that motivates it

Cadora rebuilt an insurance claims adjudication engine: four specialist agents (document, policy
coverage, medical, financial), a supervisor synthesizing a final decision of
`APPROVED | MANUAL_REVIEW | DENIED`, a FastAPI surface, and a deterministic `USE_MOCK` mode so
the whole pipeline runs and tests offline.

The generated requirements were **excellent on engineering** — full FR/BR/API/NFR/AC coverage
and a traceability matrix. But they contained **no user journey**, and one grep exposed the cost:

```
$ grep -n "MANUAL_REVIEW" aidlc-docs/inception/requirements/requirements.md
74:   FR-09  The supervisor emits a final decision: APPROVED | MANUAL_REVIEW | DENIED
118:  BR-04  Medical-necessity score 40–69 → route to MANUAL_REVIEW
121:  BR-07  Billing anomaly detected → route to MANUAL_REVIEW
203:  AC-12  A borderline claim yields decision == "MANUAL_REVIEW"

$ grep -rinE "review queue|reviewer|resolve" aidlc-docs/inception/requirements/
(no matches)
```

`MANUAL_REVIEW` was a decision value with routing conditions and a test case — and a **dead
end**. No queue, no reviewer view, no resolve step. The human step of the claims process was
named but never designed, and every downstream stage would have inherited the hole.

The operator caught it at the requirements review gate by asking two questions: *where is the
user journey?* and *what does the human decide?* The rest of this manual is that catch, turned
into a repeatable method.

---

## 2. The method in three moves

| Move | Where it lives | Who acts |
|---|---|---|
| **Demand the journey** — name `user-journey.md` as a requirements-stage deliverable | `vision.md` | operator |
| **Review it with a human** — the requirements gate holds until someone reads the journey and decides | the `--hitl` review gate | reviewer |
| **Trace the design to it** — every journey step maps to a component or endpoint | the design gate | reviewer |

---

## 3. Move 1 — put the journey in the vision

Add a section to `vision.md` that makes the journey a *named deliverable with a review
contract*. This is the addendum the claims rebuild actually used:

> **## User journey first (reviewed at the first gate)**
>
> Before designing, write `aidlc-docs/inception/user-journey.md` describing the end-to-end
> journey for the two personas — the **submitter** (documents in → decision + rationale back)
> and the **claims reviewer** (queue → inspect audit trail → resolve). Cover the three claim
> fates (clean APPROVED, MANUAL_REVIEW → human resolution, DENIED on exclusion) step by step,
> naming at each step what the person sees and what the system records. This document is a
> first-class deliverable of the requirements stage — it will be human-reviewed at the
> requirements gate, and the design must trace to it.

Two properties make the section enforceable at a gate:

- it names the **exact file** (`aidlc-docs/inception/user-journey.md`), so its absence is a
  fact, not a taste;
- it states the **review contract** ("human-reviewed at the requirements gate … the design must
  trace to it"), so a change request cites the vision, not the reviewer's mood.

And close the dead end **in scope**, not in commentary. The claims vision added item 5:

> **Human adjudication of MANUAL_REVIEW claims** — `MANUAL_REVIEW` is a queue, not a dead end.
> A claims reviewer must be able to: list the claims awaiting review (with each claim's audit
> trail and the reasons it was routed), and resolve one with an explicit decision
> (`approve` | `deny` + a mandatory reason). Resolution produces the final decision, appends a
> resolution entry to the audit trail (who/when/why), and is idempotent-safe (a claim can be
> resolved once; a second attempt is a clean error).

---

## 4. Run with the gates on

```bash
cadora run examples/aidlc-hitl.topology.yaml --vision vision.md --hitl
```

With `--hitl`, Cadora pauses at every node marked `review: true` — in this topology,
`requirements` and `design` — and waits for an explicit human decision **before any downstream
work starts**. `construction` doesn't pause for a human; it answers to the deterministic
`build-test` gate instead. Without `--hitl` the same topology runs autonomously end to end.

If the reviewer may step away, review asynchronously — the default review timeout is finite and
fail-closes to abort:

```bash
cadora run examples/aidlc-hitl.topology.yaml --vision vision.md --hitl \
  --review-file --review-timeout 0    # 0 = wait indefinitely
```

If a gate ever did time out and abort, the work up to the gate is archived — resume from the
gated node with `--resume-from` rather than re-running from scratch.

---

## 5. Move 2 — review the journey at the requirements gate

Open the dashboard (`cadora dashboard`). The pending gate lists the stage's documents, one click
each — **read `user-journey.md` first**. You can also ask questions and request an on-the-spot
revision conversationally, straight from the gate card.

**What to check** — walk each persona through each fate, end to end:

- **Every terminal state has a human story.** For each fate, can you say what the person sees at
  the end and how they got there?
- **Every decision value has an owner.** A value that is routed and tested but never resolved is
  a dead end — the claims case exactly.
- **Every queue has a consumer.** If something "lands in a queue", the journey must show who
  works it, with what information, and to what outcome.
- **Every step pairs "sees" with "records".** A step that only names what the system does is a
  feature list wearing a journey's clothes.

**Then decide** — three outcomes, each recorded in `human-review.md`:

| Decision | Effect |
|---|---|
| **Approve** | the stage completes; design starts |
| **Request changes** | your comment is prepended to the stage prompt and the **same stage reruns** — up to 3 revisions — while downstream waits |
| **Abort** | the run stops; nothing downstream is built |

The claims run's actual change request, verbatim in spirit:

> The reviewer persona has no resolve step — add the queue → inspect → resolve loop: approve|deny
> with a mandatory reason, the resolution recorded in the audit trail, and a clean error on a
> second attempt.

No dashboard at hand? The gate writes `cadora-review-request.json`; drop a
`cadora-review-decision.json` next to it and the run proceeds.

---

## 6. Move 3 — hold the design to the journey

At the **design** gate (also `review: true`), run a short traceability check: each journey step
against the requirement, component, or endpoint that serves it. The claims journey's
MANUAL_REVIEW fate traced like this:

| Journey step | Served by (the claims app's design) |
|---|---|
| reviewer lists waiting claims | `GET /claims/review-queue` |
| reviewer inspects trail + routing reasons | audit trail in the claim detail |
| resolve `approve\|deny` + mandatory reason | `POST /claims/{id}/resolution` |
| resolution recorded (who/when/why) | audit-trail resolution entry |
| second attempt fails cleanly | clean-error path + its test |

Those endpoints are the **claims app's** design outcome — the journey drew them out of the
design stage; Cadora's part was holding the gate until a human asked. An untraced step is a
request-changes at the design gate, citing the vision's own line: "the design must trace to it."

The payoff outlives the run: the journey is archived with the stage that produced it and the
review decision that examined it — **reviewed, versioned, and traced like any other gated
artifact**. It is part of the run's evidence, not a slide that drifted.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `user-journey.md` never appears in the requirements output | The vision doesn't name it as a deliverable — the method won't produce it by default | Add the "User journey first" section with the exact path; at the gate, request changes citing it |
| The journey reads like a feature list | Steps name what the system does, not what the person sees | Request a revision: "each step must name what the person sees and what the system records" |
| A decision value dead-ends (routed, tested, never resolved) | Scope stops at emitting the value; nobody owns the human step | Add an in-scope item making it "a queue, not a dead end" — list + resolve with a mandatory reason — and request changes |
| The gate aborted while you were away | The finite default review timeout fail-closes | Review with `--review-timeout 0` (indefinite); resume an aborted run from the gated node with `--resume-from` |
| Three revisions spent, journey still wrong | The gap is in the vision, not the stage's execution | Abort, fix `vision.md`, re-run — revisions repair a stage against a good vision; they can't repair the vision |
| The design doesn't trace to the journey | The design stage treated the journey as background reading | Request changes at the **design** gate naming the untraced steps; the vision's "the design must trace to it" is the citation |
| Tempted to fix a gap by hand-editing the generated docs | Hand-edits bypass the gate and leave no review trail | Request changes instead — the comment, the revision, and the decision are all recorded in `human-review.md` |

---

## 8. Reference

**The vision addendum skeleton** — adapt the names, keep the two enforceable properties (exact
file, review contract):

```markdown
## User journey first (reviewed at the first gate)

Before designing, write `aidlc-docs/inception/user-journey.md` describing the end-to-end
journey for the personas — <persona A> (<in → out>) and <persona B> (<in → out>). Cover
<the terminal states> step by step, naming at each step what the person sees and what the
system records. This document is a first-class deliverable of the requirements stage — it
will be human-reviewed at the requirements gate, and the design must trace to it.
```

**Run gated** — `cadora run examples/aidlc-hitl.topology.yaml --vision vision.md --hitl`
`--hitl` pauses at `review: true` nodes (`requirements`, `design`) · `construction` answers to
the deterministic `build-test` gate · async review: `--review-file` +
`--review-timeout 0` (indefinite).

**Review** — `cadora dashboard` → open the pending gate → read `user-journey.md` first →
Ask / Revise conversationally → **Approve** · **Request changes** (comment prepended, same stage
reruns, up to 3 revisions) · **Abort**. Decisions recorded in `human-review.md`. Headless:
answer `cadora-review-request.json` with a `cadora-review-decision.json` beside it.

**Trace** — at the design gate: one row per journey step ↔ the requirement / component /
endpoint that serves it. Untraced ⇒ request changes, citing the vision.

**Cross-references** — gate mechanics: the *human review* capability doc · the method pack: the
*AI-DLC* doc · where the reviewed journey ends up (archived, checksummed, portable): the
*evidence pack* doc.
