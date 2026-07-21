# Reviewing agent work in Cadora — user manual

Cadora drives a coding agent through a gated workflow and **pauses for a human** at the nodes you
mark `review: true`. This manual is for the person doing that reviewing — from the browser, in the
dashboard.

---

## 1. What you're reviewing

At a review gate, the agent has finished a stage and produced documents (requirements, a design, a
plan). Nothing downstream runs until you decide. You have three moves:

| Decision | What happens |
|---|---|
| **Approve** | The node passes; the run continues to the next node. Your comment travels into the downstream node's prompt. |
| **Request changes** | The **same** stage re-runs with your comment folded into its prompt — up to three revisions, then it stops. |
| **Abort** | The run stops, recorded in the evidence pack. |

The gate is **fail-closed**: if it can't get a valid decision, it aborts rather than proceeding
unreviewed.

---

## 2. Set up (once)

You need two things running: a **gated run**, and the **dashboard** pointed at its archive.

```bash
# 1. Start a run that pauses at its review gates and takes decisions from a file.
#    --review-timeout 0 lets the gate wait indefinitely (see §5).
cadora run examples/aidlc-hitl.topology.yaml \
  --vision vision.md \
  --hitl --review-file --review-timeout 0 \
  --archive-dir runs

# 2. In another terminal, serve the dashboard over that same archive.
cadora dashboard --archive-dir runs --port 8768
```

Open **http://localhost:8768**, click your run, and you're at the operator view.

> The dashboard and the run must share the same machine (the dashboard writes your decision into the
> run's workspace). The run must be started with `--review-file`.

---

## 3. The review journey

1. **Watch.** The run detail page shows the DAG, each node's state and cost. While the agent works,
   there's nothing to do.
2. **A gate opens.** When a `review: true` node finishes, an **amber “Review required” panel** rises
   to the top of the page. It names the waiting node and lists the documents that stage produced.
3. **Read the work.** Click a document to open the rendered file in a new tab, or hit **preview** to
   render it inline in the panel. You see exactly the files this stage changed — not the whole tree.
4. **Ask or revise (optional).** Pick a document, then **Ask** a question about it or **Revise** it
   with an instruction. The parked run drives the agent scoped to that document and replies in the
   panel — a question is answered inline, a revision rewrites the document in place so you read the
   new draft before accepting it. Ask as many times as you like before deciding.
5. **Decide.** Type a comment if you have one (required for *Request changes*), then click
   **Approve**, **Request changes**, or **Abort**.
6. **The run continues.** Your decision reaches the live run. On approve, the node turns green and the
   panel advances to the next gate. On request-changes, the stage re-runs and the gate re-opens with
   the new draft.

The page **stops auto-refreshing while a review is open**, so it never wipes a half-typed comment or
moves a button out from under your cursor. Use **Refresh** if you want to force an update.

---

## 4. The controls

- **Document link** — opens the rendered document (served live from the run's workspace).
- **preview** — renders the document inline, below the list, without leaving the page.
- **Document picker + Ask / Revise** — the conversation row. Choose a document, type a question or a
  revision instruction, then **Ask** (answered inline) or **Revise** (the document is rewritten in
  place and shown). Each is a real turn on the agent, so it costs a call on a real backend; the run
  answers into the panel when it's done. Approving keeps whatever the document says at that moment.
- **Comments** — free text. Optional for approve/abort; **required** for request-changes. It's
  inserted verbatim into the agent's next prompt, so write it as an instruction to the agent.
- **Approve / Request changes / Abort** — the decision. After you click, a small status line reports
  `submitted: <decision>` or, if something's off, the reason (the gate stays open so you can retry).

> **Ask/Revise vs. Request changes.** Ask/Revise happen *in the panel, before you decide* — a quick
> question or an inline fix on one document. **Request changes** is the heavier move: it re-runs the
> whole stage with your comments. Reach for Ask/Revise to understand or nudge; Request changes to
> redo.

---

## 5. Reviewing on your own time

Human review is asynchronous — you might step away for an hour. Start the run with
**`--review-timeout 0`** and the gate waits **indefinitely**; nothing is lost while you're gone.

With a finite timeout (e.g. `--review-timeout 3600`), an unanswered gate **fails closed to abort**
after that many seconds. That's the right default for an unattended pipeline, the wrong one for a
human who walks away. When in doubt for interactive review, use `0`.

---

## 6. If a gate timed out (recovery)

A gate that aborted on timeout **did not lose the work** — the documents are preserved in the run's
workspace and archive. To pick up where you left off, re-run and **skip the completed node** so it
isn't regenerated:

```bash
cadora run <topology> --executor <same> \
  --cwd <same workspace> --archive-dir <same archive> \
  --run-id <new-id> --resume-from <next-node> \
  --hitl --review-file --review-timeout 0
```

`--resume-from <next-node>` trusts the reviewed artifacts already in the workspace and starts from the
next stage. A workspace-drift check verifies nothing changed underneath before it proceeds.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| No review panel appears | Run not started with `--review-file`, or the dashboard points at a different archive | Restart the run with `--review-file`; point `--archive-dir` at the same `runs/` |
| “run workspace unknown” on submit | The run predates `cwd` recording, or the workspace moved | Start a fresh run; the dashboard needs the run's live workspace path |
| Gate aborted while I was away | Finite `--review-timeout` expired | Recover with `--resume-from` (§6); relaunch with `--review-timeout 0` |
| Decision submit does nothing | Non-JSON request blocked (CSRF guard) | Use the dashboard's own buttons; the endpoint requires `Content-Type: application/json` |

---

## 8. Reference

**Run flags (HITL):** `--hitl` · `--review-file` · `--review-timeout <seconds>` (`0` = indefinite) ·
`--resume-from <node>` · `--vision <path>`

**Dashboard:** `cadora dashboard --archive-dir <dir> --host 127.0.0.1 --port 8768` — read-only + the
review write-path; keep it on loopback (it's unauthenticated).

**Decision file** (what the dashboard writes for you, if you prefer to drop it by hand):
`cadora-review-decision.json` in the run's workspace —
`{"decision": "approve" | "request_changes" | "abort", "comments": "…"}`.

**Conversation files** (drive Ask/Revise by hand): drop `cadora-review-message.json` —
`{"kind": "question" | "revision", "message": "…", "path": "<document>"}` — and the run writes its
answer to `cadora-review-reply.json`. Requires the run to have an executor (any `cadora run`).

**Also available:** the same review gate is reachable over the terminal (stdin) and over MCP
(`start_run` / `review_gate` / `submit_review`) for Claude Code, Claude Desktop, or Codex. See
`docs/hitl-mcp.md`.
