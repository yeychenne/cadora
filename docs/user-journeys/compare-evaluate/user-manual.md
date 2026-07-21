# Compare & evaluate — user manual

A Cadora run archives everything it did: per-node outcome, cost, duration, gate
verdicts, integrity findings, and the artifacts each node produced. Once two runs (or
one) are in the archive, two tools read that record back — **without calling a model**:

- **`cadora compare`** holds two runs side by side and diffs outcome + cost per node —
  the same topology through different backends (Claude vs Codex), or the same backend
  across time.
- **`cadora eval`** scores one run against the AI-DLC / quality invariants and returns a
  pass/fail verdict gated on the checks that matter.

Both are **pure functions over the run manifests: no LLM, no network**. That is the whole
point — a comparison or a score is reproducible, offline, and free, so anyone who doubts
the number can recompute it and land on the same answer.

---

## 1. What they are, and why

Runs finish. The interesting questions start after:

| Question | Tool |
|---|---|
| Same spec on Claude vs Codex — what's the price and outcome difference? | `cadora compare` |
| Did last night's run drift — a node that got pricier, or regressed? | `cadora compare` |
| Does this run hold the invariants (completion, gates, integrity) I care about? | `cadora eval` |
| Can I gate CI on that verdict? | `cadora eval` (exit code) |

Neither tool re-runs anything or asks a model to judge. They read the archived
`manifest.json` files and compute. Deterministic checks are the base layer — cheap and
reproducible; an LLM-as-judge grader is a possible *later, optional* layer, never the
floor.

> **Honesty contract.** Both tools report exactly what the archive recorded — real
> backend-reported cost where available, or price-table **estimates** explicitly marked
> *est.* Codex/GLM figures are priced from the rate table; Kiro reports credits. A
> comparison lines them up on one ruler but does not launder an estimate into a
> measurement.

---

## 2. Compare two runs — `cadora compare`

```bash
cadora compare <run_a> <run_b> --archive-dir runs
```

`compare` reads two manifests and diffs them: a per-run summary, the cost delta, and a
per-node line pairing outcome and cost, A against B.

### Cross-backend — same spec, two engines

```bash
cadora compare guardgoal-claude guardgoal-codex --archive-dir runs
```

```
compare  A=guardgoal-claude  B=guardgoal-codex
  A: executor=claude topology=guardgoal ok=False pass=4/4 cost=$14.1011 out_tok=140117
  B: executor=codex topology=guardgoal ok=False pass=4/4 cost=$4.9692 out_tok=50295
  Δcost (B−A): $-9.1319
  nodes:
    · requirements: A[✓ claude-sonnet-5 $1.0560] B[✓ gpt-5.5 $0.4552]
    · design: A[✓ claude-sonnet-5 $0.7142] B[✓ gpt-5.5 $0.4610]
    · implement: A[✓ claude-sonnet-5 $11.7842] B[✓ gpt-5.5 $2.3543]
    · build-test: A[✓ claude-sonnet-5 $0.5467] B[✓ gpt-5.5 $1.6986]
```

Same topology, every node `✓` on both — the outcome held identical while the price did
not, and Codex came out about **$9 cheaper** (its figures are price-table *est.*). Note
each run's `ok=False`: that's the `build-test` **gate**, not a failed node (`pass=4/4`).
Compare surfaces both, so you can see the outcome match while the cost diverges.

### Over time — same backend, two days

Point `compare` at the same backend on different days and it becomes a regression
detector:

```bash
cadora compare nightly-0706 nightly-0707 --archive-dir runs
```

```
compare  A=nightly-0706  B=nightly-0707
  A: executor=claude topology=aidlc-hitl ok=True pass=3/3 cost=$2.0139 out_tok=15840
  B: executor=claude topology=aidlc-hitl ok=False pass=2/3 cost=$2.4620 out_tok=17020
  Δcost (B−A): +$0.4481
  nodes:
    · requirements: A[✓ claude-sonnet-5 $0.9451] B[✓ claude-sonnet-5 $0.9612]
    · design: A[✓ claude-sonnet-5 $0.2791] B[✓ claude-sonnet-5 $0.2864]
    · construction: A[✓ claude-sonnet-5 $0.7897] B[✗ claude-sonnet-5 $1.2144]  ⚠ ok changed
```

`construction` flipped from `✓` to `✗` **and** cost more doing it — flagged with
`⚠ ok changed` and a `+$0.4481` delta in one place.

### JSON

Add `--json` for the same diff as a structured object — `run_a`, `run_b`,
`same_topology`, `summary_a`/`summary_b`, `cost_delta`, and a `nodes` array
(`node_id`, `in_a`, `in_b`, `ok_a`, `ok_b`, `ok_changed`, `model_a`/`model_b`,
`cost_a`/`cost_b`, …). Pipe it into a script or a dashboard.

```bash
cadora compare guardgoal-claude guardgoal-codex --archive-dir runs --json
```

---

## 3. Reading a comparison

| Line | Meaning |
|---|---|
| `A:` / `B:` summary | `executor · topology · ok · pass=n/N · cost · out_tok` for each run. |
| `⚠ different topologies` | Opens the diff when A and B ran different topologies — you're comparing unlike things. |
| `Δcost (B−A)` | B minus A. `+` when B costs more; a negative number (`$-9.1319`) when B is cheaper. |
| `· <node>: A[…] B[…]` | Per-node outcome (`✓`/`✗`), model, and cost on each side. |
| `⚠ ok changed` | The node passed on one run and failed on the other — the regression signal. |
| `· <node>: A only` / `B only` | The node exists in only one of the two runs. |

`ok` is the **run** verdict (it can be `False` from a failed gate even when every node is
`✓`); the per-node `✓`/`✗` is that **node's** own outcome. Compare shows both on purpose.

---

## 4. Evaluate one run — `cadora eval`

```bash
cadora eval <run-id> --archive-dir runs
```

`eval` scores a run against the AI-DLC / quality invariants and prints a checklist, a
score, and a verdict:

```bash
cadora eval flags-verify --archive-dir runs
```

```
eval flags-verify  ·  executor=claude  ·  topology=multi-backend-feature-flags
  ✓ run_ok: manifest.ok=True
  ✓ all_nodes_ok: all nodes ok
  ✓ gates_passed: no failing gates
  ✓ integrity_clean: no integrity findings
  ✓ cost_attributed: all nodes have cost (3 estimated from price table)  (warn)
  ✗ aidlc_artifacts: no aidlc-docs artifacts found  (warn)
  score 5/6 (83%)  →  PASS
```

Read it top to bottom:

- The first four checks are **CRITICAL** — they gate the verdict.
- The last two carry `(warn)` — they're **non-critical**. Here artifacts are missing and
  three node costs are price-table estimates, but neither turns the verdict red.
- `score P/T` counts **all six** checks; `→ PASS` / `→ FAIL` is decided by the CRITICAL
  four only. This run is `5/6` and still **PASS** because every critical check holds.

The exit code follows the verdict: **`0` on PASS, `1` on FAIL** (see §6).

---

## 5. The six checks

| Check | Gates verdict | Passes when | Fails when |
|---|---|---|---|
| `run_ok` | **critical** | `manifest.ok=True` | `manifest.ok=<v>` |
| `all_nodes_ok` | **critical** | `all nodes ok` | `failed nodes: <ids>` |
| `gates_passed` | **critical** | `no failing gates` | `bad gates: <node:status>` |
| `integrity_clean` | **critical** | `no integrity findings` | `findings in: <ids>` |
| `cost_attributed` | warn | `all nodes have cost` | `missing cost: <ids>` |
| `aidlc_artifacts` | warn | `AI-DLC artifacts captured` | `no aidlc-docs artifacts found` |

**Verdict** is `pass` iff all four **critical** checks pass. Because `score` is
`passed/total` across all six, a run can score `5/6` and **PASS** (a warn missed), or
`4/6` and **FAIL** (a critical missed) — the score and the verdict are not the same
thing. `cost_attributed` may add detail such as `(N estimated from price table)` or
`(N in credits)`; it counts a node as attributed if it has *either* dollars or credits.

A failing critical run looks like this (two critical checks red, verdict `FAIL`,
exit `1`):

```
eval guardgoal-claude  ·  executor=claude  ·  topology=guardgoal
  ✗ run_ok: manifest.ok=False
  ✓ all_nodes_ok: all nodes ok
  ✗ gates_passed: bad gates: build-test:blocked_prerequisite
  ✓ integrity_clean: no integrity findings
  ✓ cost_attributed: all nodes have cost  (warn)
  ✓ aidlc_artifacts: AI-DLC artifacts captured  (warn)
  score 4/6 (67%)  →  FAIL
```

---

## 6. `eval --json` and CI

`--json` emits the structured result — a `checks` array of `name` / `passed` / `detail`,
then `passed`, `total`, `score`, and `verdict`:

```bash
cadora eval flags-verify --archive-dir runs --json
```

```json
{
  "run_id": "flags-verify",
  "executor": "claude",
  "topology": "multi-backend-feature-flags",
  "checks": [
    { "name": "run_ok",          "passed": true,  "detail": "manifest.ok=True" },
    { "name": "all_nodes_ok",    "passed": true,  "detail": "all nodes ok" },
    { "name": "gates_passed",    "passed": true,  "detail": "no failing gates" },
    { "name": "integrity_clean", "passed": true,  "detail": "no integrity findings" },
    { "name": "cost_attributed", "passed": true,  "detail": "all nodes have cost (3 estimated from price table)" },
    { "name": "aidlc_artifacts", "passed": false, "detail": "no aidlc-docs artifacts found" }
  ],
  "passed": 5,
  "total": 6,
  "score": 0.833,
  "verdict": "pass"
}
```

There is **no `critical` field** on a check — the CRITICAL set (`run_ok`, `all_nodes_ok`,
`gates_passed`, `integrity_clean`) is defined by name inside the tool and is what
`verdict` gates on.

Gate CI on the **exit code**, which mirrors the verdict:

```bash
# fail the pipeline if any critical invariant broke
cadora eval "$RUN_ID" --archive-dir runs || exit 1
```

---

## 7. Deterministic by construction

Neither tool calls a model or touches the network — they read the archived manifests and
compute. So a comparison or a score is:

- **Reproducible** — same inputs produce the same bytes, every time:

  ```bash
  diff <(cadora eval flags-verify) <(cadora eval flags-verify) && echo reproducible
  ```

- **Offline** — nothing leaves the machine; run it on an air-gapped box.
- **Free** — no tokens, no model call; safe to run in CI on every push.

That's what makes the output admissible: whoever doubts a number can recompute it
themselves.

---

## 8. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no such run: <path>` (compare) | One of the two run ids isn't under `--archive-dir` | Check the id and `--archive-dir` (default `runs`); list the archive |
| `no such run '<id>' in runs/` (eval) | The run id isn't in the archive | Confirm the id; pass the right `--archive-dir` |
| `⚠ different topologies: A=… B=…` | The two runs ran different topologies | Expected — compare unlike runs with care; per-node lines only align where node ids match |
| `· <node>: A only` / `B only` | A node exists in just one run | The topologies diverged (added/removed/renamed node); not an error |
| `⚠ ok changed` on a node | The node passed on one run, failed on the other | A regression (or a fix, if B is the good one) — inspect that node's archived output |
| `eval … → FAIL` but score looks high | A **critical** check failed; non-critical passes still count toward score | Read which row is `✗`; only `run_ok/all_nodes_ok/gates_passed/integrity_clean` gate the verdict |
| `✗ aidlc_artifacts` on a PASS | Non-critical warn — no `aidlc-docs` captured for this topology | Fine to accept; it never fails the run. Capture artifacts if you want it green |
| `cost_attributed: (N estimated from price table)` | Backend reports tokens, not dollars (Codex/GLM) | Expected — costs are *est.* from the rate table; treat as estimates |

---

## 9. Reference

**`cadora compare <run_a> <run_b>`** — diff two runs (cross-backend / over time).
`--archive-dir <dir>` (default `runs`) · `--json` (emit the structured diff as JSON)

**`cadora eval <run-id>`** — evaluate a run (deterministic AI-DLC checks).
`--archive-dir <dir>` (default `runs`) · `--json` (emit the structured result as JSON)

**CRITICAL checks (gate the verdict):** `run_ok` · `all_nodes_ok` · `gates_passed` ·
`integrity_clean`.
**Non-critical (warn only):** `cost_attributed` · `aidlc_artifacts`.

**Exit codes:** `compare` → `0`. `eval` → `0` when `verdict` is `pass`, `1` otherwise.

**Guarantee:** both are pure functions over the run manifests — no LLM, no network —
so text and JSON output are byte-stable and free to recompute.
