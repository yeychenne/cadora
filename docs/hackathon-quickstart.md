# Cadora — hackathon quickstart

Cadora is the **audit-grade conductor** for coding agents: it drives headless coding-agent CLIs
(Claude Code, OpenAI Codex, and more) through a gated workflow and proves — with deterministic
gates, tamper checks, and a per-node cost ledger — what they actually built and what it cost, not
just what they claim.

## The 5-command flow

Run these commands from a clone of the Cadora repository so the `examples/` files are present.
If you installed from PyPI elsewhere, first run
`git clone https://github.com/yeychenne/cadora.git && cd cadora`.

```bash
# 0. Install.
pip install cadora

# 1. Check your backend CLIs are on a tested contract version (offline, no model calls).
cadora doctor

# 2. Drive a tiny build, autonomously, on Claude Code (subscription-funded by default).
cadora run examples/aidlc.topology.yaml --vision examples/hackathon-hello.vision.md --cwd ./demo

# 3. Turn the run into a portable evidence pack (note the run-id printed by step 2, or
#    `cadora archive ls`).
cadora report <run-id>

# 4. Run it again on a different backend, then diff cost + outcome node-by-node.
cadora run examples/aidlc.topology.yaml --vision examples/hackathon-hello.vision.md \
  --cwd ./demo2 --executor codex --model gpt-5.5
cadora compare <run-id-a> <run-id-b>
```

## Picking a topology for a live demo

The single-node `examples/aidlc.topology.yaml` is the **verified demo path**: its construction
step emits complete runnable code, and it gate-passes on Claude and Codex (fastest, ~2 min). On
Kiro it occasionally trips the gate on a lint nit the agent left behind (an unused import) —
**that's the gate working, not a bug**, and it's a fine thing to show an audience.

Larger topologies like `examples/aidlc-hitl.topology.yaml` (three stages) can produce richer
artifacts, but their gate outcome **varies by backend**: some models lean into writing AI-DLC
*documentation* rather than runnable code, and the gate then correctly blocks a run with no tests.
In our testing, `aidlc-hitl` gate-passed on Kiro (a full app with 23 tests) but produced
docs-without-code on Claude. So there's one reliable rule:

**Rehearse your exact backend + topology once before you present.** And remember: a gate failure
isn't a stumble to hide — it's Cadora catching a real issue (no runnable tests, a lint nit), which
is itself worth showing.

## What you'll see

- **The evidence pack** (`cadora report`) — a self-contained `report.html` you can open in a
  browser, plus `report.json` and a SHA-256 `checksums.txt` covering every archived file. It
  states exactly what the gate and integrity checks found — passed, failed, `vacuous` (zero
  tests actually ran), or `blocked_prerequisite` — never just the agent's word for it.
- **A Claude-vs-Codex cost compare** (`cadora compare`) — the same spec, two backends, one
  ledger: per-node cost and pass/fail side by side, plus the total cost delta between the two
  runs.

## Before you run it

Cadora runs coding agents **autonomously** in the workspace you point `--cwd` at — it writes
files and executes commands there with no human in the loop. Point it only at a throwaway
directory (like the `./demo` above), never a workspace with anything you care about or any
credentials in its environment.
