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

`examples/aidlc.topology.yaml` is one node — fastest (~2 min), and it gate-passes reliably on
Claude and Codex. On some backends (Kiro in particular) a single-node run can trip the gate on a
lint nit the agent left behind (an unused import) — **that's the gate working, not a bug**, and
it's a fine thing to show an audience. For a guaranteed-green live demo, prefer the three-stage
`examples/aidlc-hitl.topology.yaml` (run it autonomously — without `--hitl`): the design and
requirements stages clean the spec up before construction, so it passes more consistently and
produces a richer artifact (working code + a full test suite + AI-DLC docs).

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
