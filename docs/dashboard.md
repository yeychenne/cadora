# Local dashboard

Cadora includes a small local web dashboard for inspecting conductor runs from the run archive. It
is designed for operator visibility during local development, demos, and beta testing without
running a container, database, or frontend build.

**Scope:** the dashboard is a *cockpit over the run archive* — inspection and cost visibility —
and is deliberately kept small. Portable run evidence (`cadora report`, roadmap) is where export
and sharing will live; backend-native dashboards are consumed as data sources, not competed with.

**Cost sources:** Claude Code reports dollars directly; Codex reports tokens only, so Codex node
costs are computed from the public OpenAI rate table and flagged as estimated in
`cadora usage` / `--json` (`cost_estimated`).

## Start the dashboard

From the workspace where your `runs/` archive lives:

```bash
cadora dashboard
```

Then open:

```text
http://127.0.0.1:8765/
```

Common options:

```bash
# Serve a non-default archive directory.
cadora dashboard --archive-dir /path/to/runs

# Change the local port.
cadora dashboard --port 8787

# Bind explicitly to localhost.
cadora dashboard --host 127.0.0.1 --port 8765
```

For source checkouts:

```bash
source .venv/bin/activate
python -m cadora.cli dashboard --archive-dir runs
```

The server runs until interrupted with `Ctrl-C`.

## Security posture

The dashboard is intentionally local and lightweight:

- no login
- no TLS
- no database
- no container
- no external web service

By default it binds to `127.0.0.1`. Keep it on loopback. If you bind it to a non-loopback address,
put it behind TLS and authentication before exposing it beyond your machine because it serves run
metadata, outputs, and archived artifacts.

## What the dashboard shows

The home page at `/` shows:

- active runs, when live telemetry is present
- recent archived runs
- generation tokens
- context tokens, including cache read/create tokens
- reported cost
- a **FinOps** panel: a token split (input / output / cache), a **cost-by-day** trend, and cost
  broken down **by model, by executor, and by funding** — each with a cost bar. A time-window
  toggle (all / 30d / 7d) narrows every breakdown; the window is applied server-side over the run
  archive (the same filter as `cadora usage --since`).

Each run card links to a run detail page:

```text
http://127.0.0.1:8765/runs/<run-id>
```

## Run detail view

The run detail page is the operator view for one conductor run. It shows:

- run status, executor, topology, cost, and node count
- a DAG progress canvas with arrows between dependent nodes, where each node box is a
  cost-and-quality map: it carries the node's **cost and context tokens** and **badges for its gate,
  toolchain-integrity, and human-review outcomes** — so a `gate vacuous` or `gate blocked_prerequisite`
  badge surfaces a construction gate that did not truly pass, right on the node
- clickable node boxes
- selected-node facts: model, **backend** (the executor that ran the node), cost, context tokens,
  review state
- activity timeline
- node output
- produced artifacts
- raw node metadata

The artifact tab lists files captured under each node archive, including `aidlc-docs/`. Text-like
artifacts such as `.md`, `.txt`, `.json`, `.jsonl`, `.yaml`, and `.yml` can be previewed in the
browser.

## Reviewing a gate from the dashboard (HITL)

When a run started with `cadora run … --hitl --review-file` pauses at a `review: true` node, the run
detail page shows a **review panel** at the top: the node that is waiting, its changed documents as
**clickable links** (open the rendered doc, or `preview` it inline), a comments box, and
**Approve / Request changes / Abort**. Submitting a decision delivers it to the live run — the run
continues to the next node on approve, re-runs the stage with your comments on request-changes, or
stops on abort — and the panel advances to the next gate. The auto-refresh pauses while a review is
open so it never wipes an in-progress comment.

Before you decide, you can **talk to the work**. Pick a document and either **Ask** a question about
it or **Revise** it: the parked run drives the executor scoped to that document and replies in the
panel — a question is answered inline, a revision rewrites the document in place and shows you the new
draft, which you can then approve (or revise again). Each ask or revision is a real executor call, so
it costs a turn on a real backend; the fixture executor answers deterministically for demos.

For a genuinely asynchronous reviewer (you step away, or review from the dashboard on your own time),
start the run with **`--review-timeout 0`** so the gate waits indefinitely rather than failing closed.

How it works: the run records its workspace path in `run-input.json`, and the dashboard bridges to it
over the existing decision-file channel — it reads the pending `cadora-review-request.json` and writes
`cadora-review-decision.json` back into the run's workspace. This needs the dashboard to share the
run's filesystem (same host) and the run to use `--review-file`. The decision `POST` requires a JSON
content-type; keep the dashboard on loopback (it is unauthenticated).

## Token usage CLI

Use `cadora usage` when you want the same usage information in the terminal:

```bash
cadora usage --archive-dir runs
```

Examples:

```bash
# Last seven days.
cadora usage --archive-dir runs --since 7d

# Last 24 hours.
cadora usage --archive-dir runs --since 24h

# JSON for another tool.
cadora usage --archive-dir runs --json
```

Cadora reports two token totals:

- `generation_tokens`: input + output tokens
- `context_tokens`: input + output + cache creation + cache read tokens

This keeps Claude cache volume visible without collapsing every token category into a single
ambiguous number.

## Files written during a run

Completed runs still write the durable archive:

```text
runs/<run-id>/manifest.json
runs/<run-id>/<node-id>/output.txt
runs/<run-id>/<node-id>/events.jsonl
runs/<run-id>/<node-id>/aidlc-docs/
```

Runs also write lightweight live telemetry for the dashboard:

```text
runs/<run-id>/status.json
runs/<run-id>/run-events.jsonl
```

`status.json` is the current compact snapshot used by the dashboard. `run-events.jsonl` is the
append-only run activity stream.

## Troubleshooting

If the page is empty:

- confirm the archive directory contains runs: `cadora archive ls --archive-dir runs`
- start the dashboard from the directory that contains `runs/`, or pass `--archive-dir`
- refresh the browser after starting the server

If the port is already in use:

```bash
cadora dashboard --port 8787
```

If a run detail page has no live activity:

- old archives may not have `run-events.jsonl`
- node-level `events.jsonl`, `output.txt`, and artifacts should still be available once the run has
  been recorded

## Should this be a Codex skill?

Not yet. This is currently product documentation for Cadora users. A Codex skill would make sense
later if we want a reusable workflow such as:

- diagnose a broken Cadora dashboard archive
- generate screenshots or demos from a run
- compare two run dashboards and summarize cost/artifact differences

For now, keep the canonical instructions in this document.
