---
name: verify-with-cadora
description: Prove a change is genuinely done, not just claimed. Hand the workspace to Cadora for deterministic external gates plus a signed, checksummed evidence pack. Use before shipping or handing off agent-written code, or whenever a reviewer or CI needs proof the build and tests actually pass — not the agent's word.
---

# Verify with Cadora

You (the agent) just made a change and believe it works. *"It works"* is a claim. This skill turns
it into **evidence**: Cadora re-runs the real gates from *outside* the session and produces a
signed, checksummed evidence pack you can hand to a reviewer or a CI system.

Prefer this over self-attested TDD when the result needs to be *provable*, not merely tested. TDD
discipline inside the session is persuasion — the model following instructions. An external gate
re-running the real build and tests is proof. This skill supplies the second.

## When to use

- Before handing off or shipping agent-written code.
- When a reviewer or CI needs proof the build/tests genuinely pass (not the agent's say-so).
- To produce a portable, verifiable record of a change.

## How

**1. Verify existing code — no re-run, no LLM cost.** Point Cadora's gates at the workspace:

```bash
cadora gate-check <topology.yaml> --cwd .
```

If you don't have a topology yet, a one-node file is enough — `verify.topology.yaml`:

```yaml
name: verify
gates:
  build-test:
    cmd: "ruff check . && pytest -q"   # your real gate command
    setup: auto
nodes:
  - id: verify
    role: check
    gate: build-test
```

`gate-check` exits non-zero if any gate fails. A test runner that executed **zero** tests is blocked
(`vacuous`); a declared package that won't build fails (`packaging_failed`). That is the
deterministic verdict — the agent's claim never enters into it.

**2. Produce and sign the evidence pack** (from a full `cadora run`, or any archived run):

```bash
cadora report <run-id>                          # report.html + report.json + checksums.txt
cadora sign   <run-id> --key ~/.ssh/id_ed25519  # detached signature over the checksums manifest
cadora verify <run-id>                           # recompute hashes + check the signature (exit != 0 on tamper)
```

`cadora verify` re-hashes every archived file and checks the signature, so the pack is
tamper-evident **and** attributable — it verifies after it leaves your machine.

## The stacking

Run your best in-session discipline — skills, TDD, whatever produces the strongest change — to
*build* it. Then let Cadora *adjudicate* it deterministically and leave a signed record. Best input,
independent sign-off: the two compose cleanly, because they answer different questions.
