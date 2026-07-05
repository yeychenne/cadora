# Cadora funding & billing (verified July 2026)

**Purpose:** how to pay for the Claude Code / headless-agent usage that powers Cadora,
grounded in the *current* (not the announced-then-paused) Anthropic billing mechanics.
Supersedes the assumption in earlier planning docs that the June-15 change had landed.

## The headline correction

The **June 15, 2026** change that would have moved headless usage (`claude -p`, the Claude
Agent SDK, GitHub Actions) **off** subscription limits onto a separate metered credit pool
**was paused on the day it was due to ship, before taking effect.** As of July 2026,
headless/SDK usage **still draws from the ordinary Pro/Max subscription limits**, exactly
as interactive Claude Code does. Anthropic's Help Center article now carries a banner:

> *"We're pausing the changes to Claude Agent SDK usage described below. For now, nothing
> has changed: Claude Agent SDK, `claude -p`, and third-party app usage still draw from
> your subscription's usage limits."* — [support.claude.com/…/15036540](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)

**But** Anthropic said it is *reworking* the plan and will *"give advance notice before any
future change."* So the subscription-covers-headless situation is real today but explicitly
flagged as temporary — a policy risk to price into a tool meant to run for months.

## Current mechanics (July 2026) — verified

| Question | Answer |
|---|---|
| Does `claude -p` / SDK draw on the subscription today? | **Yes** — same pool as interactive Claude Code (the paused split never took effect). |
| Interactive Claude Code limits | Subscription rolling **5-hour** window + **weekly** caps (unchanged). |
| When subscription limits are exhausted | Hard-stop until the window resets, **unless** you've opted into pay-as-you-go "usage credits" overflow → then it bills at **standard API rates**. |
| Bedrock / Vertex path | **Fully separate** — metered per-token on your AWS/GCP bill, immune to the subscription/credit question. Rates == first-party API list; feature-lags it. |
| The paused credit amounts (if it returns) | Pro $20 / Max 5x $100 / Max 20x $200 monthly, no rollover, metered at API rates. |

## Current API list prices (per MTok) — for cost modeling

| Model | Input | Output | Cache write 5m (1.25×) | Cache write 1h (2×) | Cache read (0.1×) |
|---|---|---|---|---|---|
| Opus 4.8 | $5.00 | $25.00 | $6.25 | $10.00 | $0.50 |
| Sonnet 5 (intro → 2026-08-31) | **$2.00** | **$10.00** | $2.50 | $4.00 | $0.20 |
| Sonnet 5 (from 2026-09-01) | $3.00 | $15.00 | $3.75 | $6.00 | $0.30 |
| Haiku 4.5 | $1.00 | $5.00 | $1.25 | $2.00 | $0.10 |

**Batch API = 50% off** in+out (stacks with cache multipliers). **Tokenizer caveat:** Opus
4.7+/Sonnet 5/Fable 5 use a newer tokenizer producing **~30% more tokens** for the same
text vs Sonnet 4.6 — add headroom to any estimate calibrated on older counts.

## Funding recommendation

**Meter Cadora's automated pipeline on a metered API key (or Bedrock); keep a personal
Pro/Max subscription only for interactive dev. Hybrid.**

Why, from the verified mechanics:

1. **Don't build unit economics on the subscription subsidy for headless.** Today a Max 20x
   ($200/mo) plan appears to cover ~$300–550/mo of headless spend — but Anthropic already
   tried to wall this off once and has said it will again. A product's cost model shouldn't
   depend on a subsidy the vendor is actively trying to remove.
2. **Even today, subscription caps hard-stop bursty fan-out.** A DAG of subagents is exactly
   the parallel, bursty workload the 5-hour + weekly windows throttle — mid-run stalls
   unless you enable API-credit overflow, which puts you on metered rates anyway. (Seen live:
   a Cadora run archived `"You're out of extra usage · resets 4:10pm"` and failed the node.)
3. **A metered key is predictable and policy-stable:** list price, no rate-window hard-stops,
   and the billing model won't shift under you. Lean on **prompt caching** (0.1× reads — the
   subagent system prompts + repo context are highly cacheable) and **model tiering** (Haiku
   4.5 / Sonnet-5-intro for cheap nodes, Opus 4.8 only for reasoning-critical nodes) to keep
   effective cost well under naive list math.
4. **Client work bills to the client's key** (per the runner's per-run-credentials rule) —
   never pooled through your personal subscription (ToS).

Cadora already has the right instrument for this: the **FinOps funding-split** view
(cost by model/executor/funding) + `cadora compare` (now shipped, WP-C1) to measure
Claude-vs-Codex cost per node on the same topology. Use them to keep the ~$300–550/mo
estimate honest against real runs.

## Sources
- [Anthropic — Agent SDK with your plan (pause banner)](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
- [Anthropic — Claude Code with Pro/Max](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan)
- [Anthropic pricing docs](https://platform.claude.com/docs/en/about-claude/pricing)
- [DevOps.com — pause](https://devops.com/anthropic-hits-pause-on-claude-agent-sdk-billing-change-for-now/)
- [CloudZero — Claude on AWS/Bedrock](https://www.cloudzero.com/blog/claude-on-aws-bedrock/)
