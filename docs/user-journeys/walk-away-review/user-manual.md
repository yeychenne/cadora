# Walk-away review — user manual

Review gates run on the reviewer's schedule, not the laptop's. A run **parks and exits** at its
human gates, decisions can be made **while no process is alive** (from the dashboard, including a
phone through a tunnel), and **`cadora resume`** applies them headless — with the evidence
recording who decided, through which surface, over exactly which bytes.

Companion documents:
[user journey](https://yeychenne.github.io/cadora/docs/user-journeys/walk-away-review/user-journey.html) ·
[design spec](https://yeychenne.github.io/cadora/docs/user-journeys/walk-away-review/design-spec.html) ·
related: [Human review](../human-review/user-manual.md) · [Cost & FinOps](../cost-finops/user-manual.md)

---

## 1. Park a run at its review gates

```bash
cadora run pipeline.yaml --executor claude --cwd ws \
  --hitl --on-review park \
  --reviewers "yves" --notify-url https://ntfy.sh/my-runs --yes
```

| Flag | Effect |
|---|---|
| `--on-review park` | at a `--hitl` review gate, the wave **drains** (siblings finish and record), one park record collects every pending gate, and the run **exits cleanly with code 75** |
| `--on-review wait` | (default) the classic behavior: the process blocks until a decision arrives |
| `--reviewer NAME` | your declared identity, recorded on every decision you make (default: `$CADORA_REVIEWER`) |
| `--reviewers a,b` | authorization allowlist, **enforced at decision time** and recorded in the manifest (`review_policy`) |
| `--notify-url URL` | webhook POSTed when a gate starts waiting and when the run parks (default: `$CADORA_NOTIFY_URL`) |

What exit code 75 means: *waiting for a human*, not failure. Wrappers and schedulers can
distinguish a parked run (75) from a broken one (1) and a finished one (0).

What the park record holds (`runs/<run_id>/park.json`): the topology, the resolved gate
specs, the execution contract (backend, model, funding, budget policy, notify URL, reviewer
policy), each pending node's completed agent result, the per-document SHA-256 snapshot, and the
workspace fingerprint. A resume depends on nothing outside the archive. A finished run deletes
its park record.

## 2. Decide while parked

Serve the dashboard (it stays bound to loopback; reach it from a phone through a tunnel such as
`tailscale serve 8765` or an SSH tunnel):

```bash
cadora dashboard --archive-dir runs
```

A parked run shows a `parked` pill and a **⏸ Parked** triage panel: each pending gate with its
cost so far and changed documents, a name field ("recorded in the evidence with this decision"),
and **Approve / Request changes / Abort**. The decision is stored in the archive
(`runs/<run_id>/parked-decisions.json`), bound three ways:

1. to **that one node**;
2. to the **SHA-256 of the exact bytes being reviewed**, hashed at decision time;
3. to the **declared identity**, which faces the same `--reviewers` allowlist as a live decision.

Mobile is **triage + decide** — see a gate, read enough, decide, type a short change request.
Full-screen reading and annotation remain the desktop dashboard's job.

## 3. Resume — the decisions apply, the agents don't re-run

```bash
cadora resume runs/pipeline-1 --yes
```

```
▸ applying the decision made while parked: approve by yves via dashboard
  ✓ design   gate:design-gate ok   integrity:ok
▶ build · running…
✓ run complete -> runs/pipeline-1
```

| On resume | Guarantee |
|---|---|
| parked node's agent work | **not re-run, not re-paid** — the serialized result is injected; only downstream work executes |
| deterministic gate | **re-checked** against the workspace |
| workspace | fingerprint-verified against the state the run parked over; drift is **refused** (override: `--allow-drift`, recorded either way) |
| downstream prompts | render **byte-identical** to a never-parked run |
| parked downtime | lands in `review_wait_seconds`, never in a node's signed `duration_seconds` |
| stored decision | honored **only if all three bindings still hold**; a drifted document discards it loudly (`parked_decision_discarded` in the event log) and the gate re-asks |
| stored identity | allowlist-checked exactly like a live decision — an unlisted decision, **including an abort**, is rejected |
| every stored decision | **consume-once**, honored or not |
| decision timestamp | the moment the **human** decided, not the moment the resume applied it |

Resume flags: `--review-file` + `--review-timeout N` (0 = wait indefinitely) collect any
*undecided* gates via the file/dashboard surface; `--reviewer NAME` declares your identity for
live decisions; `--on-review wait|park` chooses what happens to gates that are still undecided.
The `--reviewers` allowlist is **not** overridable at resume — the policy recorded at park time
governs.

**The triage sweep:** `cadora resume runs/<id> --on-review park` applies every stored decision
and re-parks whatever remains — headless, exit 75 again, `park.json` now holding only the
undecided gates.

## 4. Notifications

`--notify-url` (or `$CADORA_NOTIFY_URL`) receives one POST per event, ntfy-style — the request
body **is** the message:

| Event | Message |
|---|---|
| a gate starts waiting | `<run_id> — <node_id> awaits your review` |
| the run parks | `<run_id> parked — awaiting review: <node ids>` |

Fire-and-forget by design: a daemon thread with a short timeout, every failure swallowed. A dead
endpoint never delays a node, corrupts telemetry, or fails a run. The URL rides the park
contract, so resumes keep notifying.

## 5. What the evidence records

Per decision, in the manifest's `human_reviews` and in each node's `human-review.md`:

```json
{
  "decision": "approve",
  "reviewer": "yves",
  "method": "dashboard",
  "timestamp": "2026-07-22T11:35:26+00:00",
  "documents": [{ "path": "aidlc-docs/.../design.md", "sha256": "ff7055f6…" }]
}
```

`method` is honestly self-asserted — `local-shell`, `dashboard`, `file-drop`, or `mcp` — which is
what lets an auditor weigh a laptop approval differently from a stronger method later. With an
allowlist declared, the manifest also records `review_policy`, so *"was this approver permitted
at the time?"* is answerable from the pack alone.

## 6. Troubleshooting

| Symptom | Meaning | Do |
|---|---|---|
| exit code 75 | the run parked — it is waiting, not broken | decide (dashboard or at resume), then `cadora resume runs/<id>` |
| `no park record at …` | the run never parked, or already resumed to completion | check `cadora archive ls`; a finished run deletes its park record |
| `✗ discarding the decision made while parked … changed after the decision` | a reviewed document was edited after the decision | re-review the current bytes; the gate re-asks at resume |
| `✗ decision by 'X' … rejected — not in the --reviewers allowlist` | identity not permitted by the recorded policy | decide as a listed identity; the gate stays open |
| `refusing a mismatched resume` | the directory name and the park record's run id disagree | pass the run's own archive directory |
| resume refuses with drift itemized | the workspace changed while parked | inspect the diff; `--allow-drift` proceeds deliberately and records it |
| notification never arrives | the webhook endpoint is down or unreachable | the run is unaffected by design; check the URL with `curl -d test <url>` |
| dashboard shows no triage panel | the run is not parked (live gates use the review panel instead) | parked runs carry the `parked` pill; live waiting gates the `Review required` panel |
