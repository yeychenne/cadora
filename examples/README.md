# Cadora topology examples

A Cadora topology is a small YAML DAG. Each node drives a headless coding agent through one step,
declares what it `depends_on`, and names a deterministic `gate` that must pass before the run moves
on. This folder is two things: real method topologies, and a gallery of the DAG **shapes** you'll
reuse when writing your own.

## Method topologies

| File | What it is |
|------|------------|
| [`aidlc.topology.yaml`](aidlc.topology.yaml) | The AWS AI-DLC method as ONE autonomous session (the v0.1.0 shape). |
| [`aidlc-stages.topology.yaml`](aidlc-stages.topology.yaml) | The same method as a per-stage DAG (the destination shape). |
| [`aidlc-phased.topology.yaml`](aidlc-phased.topology.yaml) | AI-DLC split across inception / construction phases. |
| [`aidlc-hitl.topology.yaml`](aidlc-hitl.topology.yaml) | AI-DLC with human-review points (`--hitl`). |

## Prep phase (run before a build)

Amplify a thin vision into a build-ready brief *before* construction: research the project's own
domain, enrich the vision, then assess it through a **Senior PM and a Senior DE lens in parallel**,
and consolidate into decision records + revised complexity signals. It's an inception phase, so its
gates are artifact checks — never code.

| File | What it is |
|------|------------|
| [`mission-prep.topology.yaml`](mission-prep.topology.yaml) | `research → enrich → (PM ∥ DE assess) → decide`. Run it, then point a build topology at the enriched `vision.md` it leaves behind. |

## Shape gallery

Three self-contained, runnable demos — one per canonical DAG shape. Each builds a small, neutral
Python service and lets **deterministic gates decide "green"**: an invariants test suite, a
rule-based decision, an aggregation identity — never the agent's own say-so. Every gate command is
declared inline in the topology's `gates:` map, so each file stands alone.

| Shape | File | Reach for it when… |
|-------|------|--------------------|
| **Sequential pipeline** &nbsp;`a → b → c` | [`sequential-pipeline.topology.yaml`](sequential-pipeline.topology.yaml) | every step needs the previous step's output (spec → build → harden). |
| **Fan-out → synthesize** &nbsp;`1 → N → 1` | [`parallel-fanout.topology.yaml`](parallel-fanout.topology.yaml) | work splits into independent parts you build concurrently (`--max-parallel`) and join at the end. |
| **Multi-signal fan-in** &nbsp;`N → 1` | [`fan-in-aggregation.topology.yaml`](fan-in-aggregation.topology.yaml) | independent signals are combined by one aggregator under a deterministic invariant. |

Run any of them against a fresh, throwaway workspace:

```bash
mkdir -p /tmp/seq-demo
cadora run examples/sequential-pipeline.topology.yaml --executor claude --cwd /tmp/seq-demo --yes

# the two parallel shapes are best with concurrency:
cadora run examples/parallel-fanout.topology.yaml    --executor claude --cwd /tmp/fanout-demo --max-parallel 3 --yes
cadora run examples/fan-in-aggregation.topology.yaml --executor claude --cwd /tmp/fanin-demo  --max-parallel 3 --yes
```

## Operations — deploy targets

Produce a real target's deployment artifacts and let **deterministic gates decide whether they're
actually deployable and safe**. The security gate is the point: it fails on a wildcard IAM
resource, a missing confused-deputy guard, or any long-term static credential — so "green" means
"provably deployable to the documented contract," not "the agent said so."

| File | What it is |
|------|------------|
| [`agentcore-deploy.topology.yaml`](agentcore-deploy.topology.yaml) | An operations phase for Amazon Bedrock AgentCore Runtime: `plan → (containerize ∥ runtime-spec ∥ least-priv IAM) → verify`. Gates enforce arm64 + `/health`, a typed request/response contract, and a least-privilege, **SigV4-only, confused-deputy-guarded** IAM role. Pairs naturally with `cadora gate-check` to audit an existing `deploy/` folder with no agent. |

Inspect a finished run — the DAG view, each node's gate output, the entry prompt — with
`cadora dashboard`. Verify an existing workspace against a topology's gates, no agent, with
`cadora gate-check <topology> --cwd <workspace>`. Re-run only what a late failure left with
`--resume-from <node>`.
