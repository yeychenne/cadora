# Cadora — 5-minute demo script

For a presenter running the hackathon demo live. Pair with
[docs/hackathon-quickstart.md](hackathon-quickstart.md) for the tester handout.

## Before you start (off-clock)

- `pip install cadora`, then `cadora doctor` once — confirm at least one backend CLI (`claude`
  and/or `codex`) shows `ok` so the live build doesn't stall on an auth prompt.
- Run the demo from a clone of the Cadora repository so `examples/aidlc.topology.yaml` and
  `examples/hackathon-hello.vision.md` are present.
- Have `examples/aidlc.topology.yaml` and `examples/hackathon-hello.vision.md` open in a second
  window so the audience can see the spec being built from.
- `mkdir -p ./demo ./demo2` (two throwaway dirs — Claude Code run and Codex run).
- **Topology choice:** the single-node `aidlc.topology.yaml` is fastest and passes reliably on
  Claude/Codex. Demoing on Kiro (or want a guaranteed-green run)? Use the three-stage
  `examples/aidlc-hitl.topology.yaml` autonomously instead — it cleans the spec up before
  construction. A single-node gate failure isn't a stumble to hide: it's Cadora catching a real
  lint issue, which is exactly the point — pivot to `cadora archive show` and show the caught gate.

## 0:00 — Setup (30s)

Say: "Cadora is the audit-grade conductor for coding agents — it proves what they built and what
it cost, from *outside* the vendor." Show `examples/hackathon-hello.vision.md`: one screen, a
tiny `quotes` CLI with add/list/random subcommands.

## 0:30 — Live build (2:00)

Run:

```bash
cadora run examples/aidlc.topology.yaml --vision examples/hackathon-hello.vision.md --cwd ./demo
```

While it runs, narrate: Cadora installed the AI-DLC workflow into `./demo`, and Claude Code is
now working the full lifecycle — requirements, design, code, build & test — autonomously, no
human in the loop. This spec is deliberately tiny so it gate-passes inside the demo window.

## 2:30 — Open the evidence pack (45s)

```bash
cadora report <run-id>
open <archive-dir>/<run-id>/report/report.html
```

Walk the page: the gate verdict (passed, not just "the agent said so"), the cost for this run,
and the checksums file that lets anyone verify the pack after it leaves your machine
(`shasum -a 256 -c checksums.txt`).

## 3:15 — The compare (1:00)

Kick off the second run ahead of time so it's ready (or narrate over a pre-baked second run if
time is short):

```bash
cadora run examples/aidlc.topology.yaml --vision examples/hackathon-hello.vision.md \
  --cwd ./demo2 --executor codex --model gpt-5.5
cadora compare <run-id-a> <run-id-b>
```

Point at the cost delta: same spec, two backends, one ledger, node-by-node.

## 4:15 — Gates + integrity, closing line (45s)

Say: "The gate re-runs the build and tests itself and reads exit codes — a test run that
executes zero tests is `vacuous` and blocks the run. `cadora integrity` catches tooling that's
been substituted or counterfeited. Cadora never takes the agent's word for the result."

## 5:00 — Done

Point testers at `docs/hackathon-quickstart.md` to try it themselves.
