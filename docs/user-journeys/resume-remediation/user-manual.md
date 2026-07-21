# Resume & remediation — user manual

An interrupted run should never cost you the whole run. A timeout, a walk-away, or a red gate four
stages in leaves real work on disk — Cadora lets you pick up from the break instead of starting
over. There are two recovery paths, and both are **honest by construction**:

- **Resume** — re-run from a chosen node, trusting the upstream artifacts already in your
  workspace. That trust is *verified*: Cadora checks the workspace against the run's own provenance
  and refuses to resume onto a drifted one unless you say so.
- **Remediate** — hand a failed gate to a **bounded, verified auto-repair loop**. "Green" is the
  same gate re-passing, never the agent's claim; on any bound hit the run stops *honest-blocked*
  with the full attempt trail.

This manual is for the operator recovering a run.

---

## 1. What these are, and why

When a gate fails, Cadora stops the run **at that node** — it never lets a failed gate flow
downstream. The workspace and the partial archive are left intact under `runs/<id>`. Everything the
earlier, green stages produced is still on disk, and the run's workspace fingerprint is recorded.

That preserved state is what recovery builds on:

| Path | Command surface | What it trusts / does |
|---|---|---|
| **Resume** | `--resume-from`, `--skip` | Skips nodes whose artifacts already exist in `--cwd`, re-runs the rest. |
| **Provenance** | `--allow-drift` | Verifies the workspace against the resumed run's fingerprint; refuses on drift by default. |
| **Remediate** | `--remediate`, `--remediate-max-cost` | Runs a bounded repair loop on a failed gate; green means the gate re-passes. |

> **Honesty contract.** Green is never the agent's say-so. A resumed run only trusts artifacts it
> has provenance-verified; a remediated gate is only "green" when the *same* `ShellGate.check` passes
> (and integrity passes, when enforced). On any bound hit — attempts exhausted, cost ceiling,
> executor failure — the run stops **honest-blocked**, keeping the full attempt trail rather than
> fabricating a pass.

---

## 2. Resume from the break — `--resume-from`

Point Cadora at the node to restart at. `--resume-from build` skips every node **upstream of**
`build` (trusting their artifacts already in `--cwd`), then runs `build` and everything downstream.
It **re-runs `build` itself**.

```bash
cadora run app.topology.yaml --resume-from build --cwd ./workspace
```

```
↩ resume: skipping plan, design · running from 'build'
  ↳ workspace verified against run-20260717-140205 — no drift (37 files)
cadora · executor=claude · run=run-20260717-163150
↩ skip 'plan' — artifacts trusted in the workspace
↩ skip 'design' — artifacts trusted in the workspace
▶ build · claude-sonnet-5 · running…
  ✓ build   $2.0700 est.   gate:tests ok
▶ test · claude-sonnet-5 · running…
  ✓ test   $0.4400 est.   gate:tests ok
✓ run complete -> runs/run-20260717-163150
```

The skipped nodes contribute **no new agent cost** — their outputs come from the earlier run. Only
the resume point and its downstream re-execute and re-gate.

### Finer-grained: `--skip`

When you want to name the trusted nodes directly rather than derive them from a resume point:

```bash
cadora run app.topology.yaml --skip plan,design --cwd ./workspace
```

`--skip` takes a comma-separated list of node ids to skip, trusting their existing workspace
artifacts. It's the fine-grained alternative to `--resume-from`. A typo fails fast: an unknown node
name is rejected up front, before any agent runs.

---

## 3. Provenance — trust, but verify

Resuming *trusts* the skipped nodes' artifacts. Cadora makes that trust **checked, not silent**: it
fingerprints the workspace on every run, and a resume verifies the current workspace against the most
recent prior run's fingerprint. If they match, the resume proceeds (`— no drift (N files)`, as in
§2). If the workspace **drifted** — a file edited, added, or cleaned since the run you're resuming —
Cadora **refuses**:

```
↩ resume: skipping plan, design · running from 'build'
✗ resume refused: workspace drifted since run-20260717-140205 (2 modified, 0 removed, 1 added).
    modified: aidlc-docs/inception/application-design/application-design.md
    modified: src/orders/service.py
       added: src/orders/scratch.py
  The skipped nodes' artifacts no longer match the run you are resuming, so the gates
  would certify source that never passed the earlier stages. Re-run from scratch, or pass
  --allow-drift to resume anyway (the drift is recorded in the evidence pack).
```

This is the whole point of provenance: without it, a resumed run would re-run gates over source that
never passed the earlier stages and certify it green — exactly the wrong failure mode for an
audit-grade tool.

### Override deliberately — `--allow-drift`

If the drift is expected and you want to resume anyway:

```bash
cadora run app.topology.yaml --resume-from build --cwd ./workspace --allow-drift
```

```
↩ resume: skipping plan, design · running from 'build'
  ⚠ workspace DRIFTED since run-20260717-140205 (2 modified, 0 removed, 1 added) — proceeding under --allow-drift
      modified: aidlc-docs/inception/application-design/application-design.md
      modified: src/orders/service.py
         added: src/orders/scratch.py
    this run's evidence will record that it resumed against a drifted workspace
cadora · executor=claude · run=run-20260717-164410
```

`--allow-drift` proceeds **even if the workspace has drifted since the run being resumed** (default:
refuse). **The drift is recorded in the evidence pack either way** — refused or allowed — so the
resume stays honest about the ground it stood on. Drift is classified `modified` / `removed` /
`added`, most-actionable first.

> If there's no prior run to check against, the resume proceeds on trust and says so:
> `↳ no prior workspace manifest to verify against — resuming on trust`.

---

## 4. Remediate a failed gate — `--remediate`

Instead of stopping at a red gate, hand it to a bounded repair loop. `--remediate N` runs **up to N
remediation attempts** on a failed/vacuous gate (or a blocking integrity finding), each in a **fresh,
constrained session**, before giving up. Default is `0` — off.

```bash
cadora run app.topology.yaml --cwd ./workspace --remediate 2
```

```
▶ build · claude-sonnet-5 · running…
  ✓ build   $3.4100 est.   gate:tests ok   remediate:completed-green x2
```

Here attempt 1's fix didn't hold, attempt 2 cleared it, and the node completed green after two
attempts. The archived **attempt trail** (under `build/remediation/`) records each one:

```
attempt 1   fresh session · re-ran gate:tests   FAILED
attempt 2   fresh session · re-ran gate:tests   PASSED
```

### How "green" is decided

Each attempt gets its own fresh session and a prompt built from the **current gate/integrity
detail** — the exact failure fed back verbatim — never the agent's own claim of success. After each
attempt, Cadora **re-runs the same gate** (and re-scans integrity when it's enforced). The node is
green only when that deterministic check passes:

> A false claim of success from the executor never substitutes for the gate: green is decided by
> re-running the same check, not by the session reporting done. A gate that passes having run zero
> tests is a **vacuous** pass, not a fix — and it doesn't count.

The remediation session is told, in no uncertain terms: do not weaken, delete, skip, or bypass the
gate or its tests; do not create local packages that impersonate the real toolchain; write real code
and tests; and re-run the gate yourself before finishing.

### When the attempts run out

If none of the N attempts clears the gate, the run stops **honest-blocked** — the gate still red, the
full trail preserved:

```
▶ build · claude-sonnet-5 · running…
  ✗ build   $5.8700 est.   gate:tests FAILED   remediate:honest-blocked x2
✗ stopped at node 'build': gate 'tests' blocked — remediation exhausted after 2 attempt(s) (max_attempts)  ->  runs/run-20260717-171904
```

`honest-blocked` is a real terminal state, not a soft warning: the run exits non-zero, and the
evidence pack carries every attempt that was tried and why it was blocked (`max_attempts`).

---

## 5. Bound the spend — `--remediate-max-cost`

Repair attempts cost tokens. `--remediate-max-cost USD` stops remediation **honestly
(honest-blocked)** if its attempts' summed cost would exceed the ceiling — before starting an attempt
that would cross it, rather than over-spending toward a fix that may never come.

```bash
cadora run app.topology.yaml --cwd ./workspace --remediate 3 --remediate-max-cost 5.00
```

```
▶ build · claude-sonnet-5 · running…
  ✗ build   $7.1000 est.   gate:tests FAILED   remediate:honest-blocked x2
✗ stopped at node 'build': gate 'tests' blocked — remediation exhausted after 2 attempt(s) (cost_ceiling)  ->  runs/run-20260717-172530
```

`--remediate 3` permitted a third attempt, but the two attempts already run summed past `$5.00`, so
Cadora stopped `honest-blocked (cost_ceiling)` rather than start a third. The ceiling is checked
before each attempt: it stops you *before* the overspend, not after.

---

## 6. When remediation won't engage

Not every failure is agent-repairable, and Cadora won't pretend otherwise. A gate blocked by
**missing prerequisites** — the gate tooling isn't installed in the environment — never enters the
loop, regardless of how many attempts remain:

```
✗ build    gate:tests BLOCKED_PREREQUISITE
✗ stopped at node 'build': gate 'tests' blocked by missing prerequisite(s): pytest
```

Fixing that means installing the tool, not asking an agent to try again. The gate statuses and how
the loop treats each:

| Status | Means | Remediable? |
|---|---|---|
| `failed` | Gate command ran and did not pass | Yes (default) |
| `vacuous` | "Passed" having run zero real tests | Yes (default) |
| `packaging_failed` | The package won't build / install | Yes (default) |
| `blocked_prerequisite` | Gate tooling missing from the environment | **No** — not agent-repairable |
| `passed` | The deterministic check holds | — nothing to repair |

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `resume refused: workspace drifted since <run> …` | The workspace changed since the run you're resuming | Get an untampered workspace and retry; or, if the change was intentional, pass `--allow-drift` (it's recorded) |
| `--resume-from/--skip references unknown node(s): …` | A node id was mistyped | Use an id from the list the error prints (`topology has: …`) |
| `no prior workspace manifest to verify against — resuming on trust` | No earlier run recorded a fingerprint | Expected on a first-ever run in this archive dir; the resume proceeds unchecked — confirm the workspace yourself |
| `remediate:honest-blocked x<n> (max_attempts)` | The repair loop ran out of attempts | Raise `--remediate N`, or fix the real blocker by hand; the attempt trail is under `<node>/remediation/` |
| `remediate:honest-blocked x<n> (cost_ceiling)` | Attempts summed past `--remediate-max-cost` | Raise the ceiling, or accept the block — it stopped you before overspending |
| `gate '<name>' blocked by missing prerequisite(s): …` | Gate tooling isn't installed | Install the prerequisite; remediation can't repair a missing tool |
| Remediation never runs on a failure | `--remediate` left at its default `0` | Pass `--remediate N` with `N ≥ 1` |
| Resume re-ran a node you expected skipped | `--resume-from NODE` always re-runs NODE itself | Use `--skip` to name only the nodes you want trusted |

---

## 8. Reference

**`cadora run <topology>`** — resume / remediation flags:

- `--resume-from NODE` — resume an interrupted run: skip every node upstream of NODE (trust their
  artifacts already in `--cwd`), then run NODE and everything downstream. Re-runs NODE itself.
- `--skip NODE[,NODE...]` — comma-separated node ids to skip, trusting their existing workspace
  artifacts (fine-grained alternative to `--resume-from`).
- `--allow-drift` — on `--resume-from`/`--skip`, proceed even if the workspace has drifted since the
  run being resumed (default: refuse). The drift is recorded in the evidence pack either way.
- `--remediate N` — on a failed/vacuous gate (or a blocking integrity finding), run up to N
  remediation attempts in a fresh constrained session before giving up (default: `0` = off).
- `--remediate-max-cost USD` — stop remediation honestly (honest-blocked) if its attempts' summed
  cost would exceed this ceiling.

**Terminal states** — `completed-green` (the same gate re-passed) or `honest-blocked` (a bound was
hit). Blocked reasons recorded in the evidence pack: `max_attempts`, `cost_ceiling`,
`executor_failed`, `integrity_blocked`.

**Provenance** — every run records a `workspace-manifest.json` fingerprint; a resume verifies the
current workspace against the most recent prior run's fingerprint and refuses on drift unless
`--allow-drift` is set. The drift (`modified` / `removed` / `added`) is recorded in the run manifest
either way.
