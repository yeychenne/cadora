# Reading the money in Cadora — user manual

Cadora prices **every node it runs** — tokens and dollars — and rolls the total up four ways: by
model, by executor (backend), by funding source, and by day. This manual is for the person reading
those numbers: in the dashboard's FinOps panel, on the `cadora usage` CLI, and in the signed
evidence pack.

---

## 1. What Cadora attributes

When a node finishes, Cadora records what it cost and how many tokens it moved, against that node —
never pooled into an unattributable run total. Two token totals are kept side by side, so cache
volume never hides inside a single ambiguous number:

| Total | Arithmetic |
|---|---|
| **Generation tokens** | `input + output` — read fresh and written |
| **Context tokens** | `input + output + cache creation + cache read` — the full volume moved |

Every node also carries a **model**, an **executor** (the backend that ran it), a **funding**
source, and a **cost** in dollars. The usage layer then groups the whole archive four ways:

- **by model** — e.g. `claude-opus-4-7[1m]`
- **by executor** — the backend: `claude`, `codex`, `glm`, …
- **by funding** — `subscription` or `metered`
- **by day** — cost per calendar day

> **Where cost comes from.** Claude Code reports dollars directly, and a backend-reported dollar
> figure is always authoritative. A backend that reports only tokens (Codex) is priced from the
> public rate table and **flagged as estimated** — an estimate never masquerades as a billed figure.

---

## 2. Reading the FinOps panel

Start the dashboard from the directory that holds your `runs/` archive:

```bash
cadora dashboard --archive-dir runs
```

Open **http://127.0.0.1:8765/**. The home page opens with four **metric tiles** across the top —
a muted label over one large number — totalling the whole archive:

| Tile | Shows |
|---|---|
| **Runs** | archived runs in the current window |
| **Generation Tokens** | input + output, abbreviated (`26.0k`) |
| **Context Tokens** | the full volume including cache (`990.8k`) |
| **Cost** | summed per-node dollars, always four decimals (`$1.9895`) |

Below the tiles sits the **FinOps panel**:

1. **Token split** — `in`, `out`, and `cache` (cache creation + read combined). For a cached
   backend the cache number dwarfs the rest; that is the cache doing its job, not an error.
2. **Cost by day** — one bar per day in the window, scaled to the costliest day.
3. **By model / By executor / By funding** — three columns of **breakdown rows**. Each row is a
   label, a blue-to-green cost bar, and `tokens / $cost` (e.g. `claude-opus-4-7[1m] · 990.8k /
   $1.9895`). Rows sort by context, heaviest first.

One run on one backend shows the *same* dollar figure under all three breakdowns — the same money,
read three ways. Fan a run across backends (route construction to Codex) and each column splits into
its own priced rows.

Each node also carries its own cost on the **run detail** page: the DAG canvas shows every node box
stamped with its dollars and context tokens, so you can see which stage spent what.

---

## 3. The `since` window

A toggle above the panel — **all / 30d / 7d** — narrows every block at once. The active window has a
blue outline. The filter is applied **server-side over the run archive**, and it is exactly the same
filter as `cadora usage --since` on the CLI, so the browser and the terminal always agree.

---

## 4. The same numbers, on the CLI

`cadora usage` prints the identical aggregation in the terminal — no browser needed:

```bash
# Whole archive.
cadora usage --archive-dir runs

# Last seven days (also accepts an ISO timestamp, or Nh for hours).
cadora usage --archive-dir runs --since 7d

# Structured summary for another tool.
cadora usage --archive-dir runs --json
```

Text output is the totals, the token split spelled out, and the by-model breakdown:

```text
usage since 2026-07-10T09:00:00+00:00: 7 run(s), 12 node(s)
  tokens: input=6.4k  output=19.6k  cache_create=118.4k  cache_read=846.4k
  totals: generation=26.0k  context=990.8k  cost=$1.9895
  by model:
    claude-opus-4-7[1m]         990.8k context  $1.9895
```

When any node's cost was estimated, each such line is marked `est.` and a note counts the estimated
nodes — so billed and computed dollars stay legible. `--json` emits every group
(`by_model`, `by_executor`, `by_funding`, `by_day`) plus per-node rows, each with a `cost_estimated`
flag.

---

## 5. Funding — subscription vs metered, and why it matters

**Funding** records *how* a node was paid for, and the default is the **Claude Code subscription
token — not a metered API call**:

| Funding | Meaning |
|---|---|
| **subscription** | Drawn against a Claude Code subscription seat. The default. |
| **metered** | A billed, pay-per-token API call. |
| **kiro/credits** | Kiro subscription credits (reported as credits, not dollars). |
| **unknown** | The node's record carried no funding tag. |

Why keep them apart: a `subscription` dollar and a `metered` dollar are not the same money. One is
a fixed seat you have already paid for; the other is marginal spend that lands on an invoice. The
**by funding** breakdown lets you see, at a glance, how much of a run rode the subscription you
already own versus how much it billed — the number a FinOps owner actually needs.

---

## 6. How cost lands in the signed pack

Per-node cost is not only a dashboard convenience. Each node's `cost_usd` and `duration_seconds` are
written into the run's `manifest.json` and `status.json`. The evidence pack then hashes and signs
those files, so the cost and duration you read are the **same numbers a verifier recomputes**:

```bash
# 1. Write the portable pack (report.html + report.json + checksums.txt).
cadora report pr7-claims --archive-dir runs

# 2. Sign it — a detached signature over the checksums (attributable).
cadora sign pr7-claims --archive-dir runs

# 3. Verify — recompute every hash, then check the signature.
cadora verify pr7-claims --archive-dir runs
```

`cadora verify` recomputes every file hash and checks the signature, so per-node cost and duration
are **audit-grade** — provable after the pack leaves your machine.

> **Honest duration.** A reviewed node signs only its real work time: the human's deliberation at
> the gate is recorded separately as `review_wait_seconds` and **excluded** from the signed
> `duration_seconds`. So a node that shows `41.2s` of duration with `18m 40s` of review wait is
> being honest, not slow. Non-review nodes carry their full span.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Cost shows `$0.0000` | Fixture/mock executor (reports no tokens or dollars), or an empty archive | Expected for fixture runs; run a real backend to see real spend |
| A node has tokens but `$0` cost | Backend reported tokens but no dollars, and its **model id isn't in the price table** — so no estimate could be made | Check the model id matches a known rate-table prefix (`gpt-5.5`, `glm-5.2`, …) |
| Codex cost looks approximate | It **is** estimated — Codex reports tokens only; Cadora prices it from the public rate table (`cost_estimated`) | Expected; the CLI marks it `est.` and counts estimated nodes |
| Context tokens ≫ generation tokens | Context includes cache creation + cache read; a cached backend moves far more context than it generates | Not a bug — read the token split to see the cache share |
| `by funding` shows `unknown` | The node's record carried no `funding_resolved` / `funding` tag | Cosmetic; newer runs resolve funding — the dollars are still correct |
| Panel or `usage` is empty | Wrong archive directory, or no runs recorded | Point `--archive-dir` at the folder that contains `runs/`; confirm with `cadora archive ls` |
| A reviewed node's duration looks too short | Human-review wait is excluded from the signed duration | Expected — see `review_wait_seconds` for the deliberation time |

---

## 8. Reference

**Usage CLI:** `cadora usage [--archive-dir <dir>] [--since <when>] [--json]` — `--since` accepts an
ISO timestamp, `Nd` (days), or `Nh` (hours).

**Dashboard:** `cadora dashboard --archive-dir <dir> --host 127.0.0.1 --port 8765` — read-only cost
and run visibility; keep it on loopback (it is unauthenticated).

**Two token totals:** `generation_tokens = input + output` · `context_tokens = input + output +
cache creation + cache read`.

**Breakdowns (in `--json`):** `by_model` · `by_executor` · `by_funding` · `by_day` — each group
carries `context_tokens`, `cost_usd`, and `node_count`; per-node rows carry `cost_estimated`.

**Funding values:** `subscription` (default — Claude Code subscription token) · `metered` (billed
API) · `kiro/credits` · `unknown`.

**Evidence pack:** `cadora report <run-id>` (`report.html` + `report.json` + `checksums.txt`) ·
`cadora sign <run-id>` (detached signature over the checksums) · `cadora verify <run-id>`
(recompute hashes, then check the signature). Per-node `cost_usd` and `duration_seconds` are under
the signed checksums; `review_wait_seconds` holds the human deliberation excluded from the signed
duration.
