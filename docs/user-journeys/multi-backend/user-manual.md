# Multi-backend executors — user manual

Cadora is a conductor, not an agent. It does **not** implement an agent loop; each topology
node is driven by a swappable backend that runs an external headless coding-agent CLI. The same
topology can run on Claude Code, Codex, Kiro, GLM, or Antigravity — and because the gates,
integrity checks, evidence pack, and cost attribution all sit in the conductor, **every engine
produces the same audit-grade proof**. This manual is for the operator choosing an engine (or
two) for a run.

---

## 1. What it is, and why

Every node is executed by a `NodeExecutor` backend. That contract is deliberately tiny — one
method:

```python
class NodeExecutor(abc.ABC):
    name: str = "base"
    def run(self, node, prompt, *, cwd, env=None) -> ExecutionResult: ...
```

Everything a run needs downstream comes back in a normalized `ExecutionResult`: `ok` (a real
success signal, not just exit 0), `cost_usd` when the backend reports it, `model`, and — set by
the runner — `executor`, the engine that ran the node. Because the result shape is identical
across backends, **nothing downstream knows or cares which engine ran**: the gate re-runs the
same real build, the integrity check inspects the same workspace, the evidence pack seals the
same files, and cost attributes per node the same way.

That is the differentiator: you can run the same topology through different engines (or across
time) and diff outcome and cost per node — proof that a swap didn't change the answer. No single
vendor ships that, because it requires being neutral about the engine.

| Engine | Module | Tier |
|---|---|---|
| `claude` | `cadora/executors/claude_code.py` — default; structured stream-json | verified |
| `codex` | `cadora/executors/codex.py` — OpenAI; structured JSONL | verified |
| `kiro` | `cadora/executors/kiro.py` — AWS; credit-based (`kiro-cli`) | verified |
| `glm` | `cadora/executors/glm.py` — Z.ai, driven behind the `claude` CLI | experimental |
| `antigravity` | `cadora/executors/antigravity.py` — Google (`agy`) | experimental |
| `fixture` | `cadora/executors/fixture.py` — local deterministic backend for demos and tests | test-only |

> **Verified vs experimental.** *Verified* engines are live-smoke-checked each release and carry
> a tested version range (see §2). *Experimental* engines work but aren't in the release smoke —
> use them knowingly. The `fixture` engine has no external contract; it's for deterministic tests
> and demos.

---

## 2. Check your backends — `cadora doctor`

Backend CLIs ship weekly (Codex publishes near-daily) with no machine-output stability
guarantee, so the riskiest failure mode is silent contract drift on your machine. `cadora doctor`
verifies — deterministically and **offline, with no model calls** — the Python floor, each
backend binary's presence, and whether its version falls inside the range the adapter contract
was last verified against.

```bash
cadora doctor
```

```
cadora doctor — backend CLI contract checks
  ok         python                     3.12.7
  ok         claude (verified)          2.1.128
  ok         codex (verified)           0.142.3
  ok         kiro (verified)            2.10.4
  ok         glm (experimental)
  missing    antigravity (experimental)   ('agy' not on PATH)
  ok         bun                        1.1.34  (runtime for the aidlc-v2 method pack's hooks (optional otherwise))
  support: 3 verified (claude, codex, kiro) · 2 experimental (antigravity, glm)
  (fixture needs no check — offline, no external contract)
```

Read the status column:

- **`ok`** — present and inside the tested range.
- **`untested`** — present but the version is outside the tested range (`below tested minimum
  <v>` / `above tested maximum <v>`). A **warning**: it usually still works.
- **`missing`** — the binary isn't on `PATH`. The hard signal.
- **`unparsable`** — the binary is there but `--version` failed or printed no version.

The command **exits `0` while at least one live backend is usable**, and non-zero only when none
is — so it drops straight into CI as a pre-run guard. Add `--json` for the structured report:

```bash
cadora doctor --json
```

> **GLM is a special case.** GLM runs *through* the `claude` CLI against Z.ai, so its check looks
> for `claude` on `PATH` **and** `ZAI_API_KEY` in the environment — no separate binary. Without
> the key you'll see `missing … ZAI_API_KEY not set; glm needs it for Z.ai`.

---

## 3. Run on a specific backend — `cadora run --executor`

`--executor` selects the engine for the whole run (default `claude`):

```bash
cadora run api.topology.yaml --executor codex --model gpt-5.5
```

```
cadora · executor=codex · run=api-codex-0717
▶ requirements · gpt-5.5 · running… (generating documents; this can take a few minutes)
  ✓ requirements   $0.1180   integrity:ok
▶ design · gpt-5.5 · running…
  ✓ design   $0.1640   integrity:ok
▶ construction · gpt-5.5 · running…
  ✓ construction   $0.3210   gate:build-test ok   integrity:ok
✓ run complete -> runs/api-codex-0717
```

The run header names the engine (`executor=codex`). Each node line shows its per-node cost, then
any gate and integrity verdicts — the same line shape on every backend. (Dollar figures above are
illustrative; real per-node cost is recorded when the backend reports it, otherwise a price-table
estimate marked *est.*)

Two related flags:

- **`--model <name>`** — an optional backend model override for `--executor`.
- **`--funding subscription|api`** — the **Claude** funding source (default `subscription`, the
  subscription token rather than a metered API call). It reaches Claude Code and is silently
  ignored by engines that don't accept it, which is why `funding=` shows in the run header only
  for Claude runs.

Under the hood the name resolves through a small registry; `get_executor(name)` forwards only the
kwargs a backend's constructor accepts, so one CLI surface drives every engine. An unknown name
fails loud against the allow-list:

```
unknown executor 'gpt'; choose from ['antigravity', 'claude', 'codex', 'fixture', 'glm', 'kiro']
```

---

## 4. Split one run across two backends — `--construction-executor`

You can route by **phase**: design on one engine, build on another, in a single run. Every node
has a `phase` (`inception` | `construction` | `operations`). `--construction-executor` sends
construction-phase nodes to a second backend while inception and operations nodes stay on
`--executor`.

```bash
cadora run multi-backend.topology.yaml \
  --executor claude \
  --construction-executor codex --construction-model gpt-5.5
```

For a topology whose DAG is
`requirements → architecture → interface-design → { implement-engine, implement-api } → integration-tests`,
this runs the three design nodes on Claude Code and the three code nodes on Codex:

```
inception   · --executor               construction · --construction-executor
  requirements      [claude]             implement-engine   [codex]
  architecture      [claude]             implement-api      [codex]
  interface-design  [claude]             integration-tests  [codex]  gate:build-test
```

- **`--construction-executor <name>`** — the engine for construction-phase nodes. Real help text:
  *"route construction-phase nodes to this executor (e.g. codex); inception/operations nodes stay
  on --executor"*.
- **`--construction-model <name>`** — an optional model for that second engine (e.g. `gpt-5.5`).

The runner stamps each result with the engine that ran it (`result.executor`), so a two-engine
run is archived, gated, and costed exactly like a single-engine run — one manifest, one verdict.

---

## 5. Compare across backends — `cadora compare`

Once you've run the same topology on two engines, diff them per node:

```bash
cadora compare api-claude-0716 api-codex-0717
```

```
compare  A=api-claude-0716  B=api-codex-0717
  A: executor=claude topology=api.topology.yaml ok=True pass=3/3 cost=$0.9120 out_tok=47210
  B: executor=codex topology=api.topology.yaml ok=True pass=3/3 cost=$0.6030 out_tok=51840
  Δcost (B−A): $-0.3090
  nodes:
    · requirements: A[✓ claude-opus-4-8 $0.2040] B[✓ gpt-5.5 $0.1180]
    · design: A[✓ claude-opus-4-8 $0.2790] B[✓ gpt-5.5 $0.1640]
    · construction: A[✓ claude-opus-4-8 $0.4290] B[✓ gpt-5.5 $0.3210]
```

Same topology, same gates, both green — the diff is purely engine economics (figures
illustrative, *est.*). `cadora compare` is its own capability (it also diffs a backend against
itself over time); this section is just the multi-backend tie-in. It reads two archived manifests
and computes pure functions over them — no model, no network.

---

## 6. Why the proof is identical across backends

The audit surface lives in the conductor, not the engine, so **swapping engines never changes the
shape of the proof**:

| Surface | What it does | Backend-agnostic because… |
|---|---|---|
| **Gates** | Re-run the real build / tests / scan per node; non-zero blocks the run | the gate is a shell command Cadora runs, not something the agent self-reports |
| **Integrity** | Toolchain-integrity checks over the produced workspace | it inspects files on disk, not the transcript |
| **Evidence pack** | Tamper-evident `report.html` / `report.json` / `checksums.txt` (+ signature) | it hashes the archived files, whatever engine wrote them |
| **Cost** | Per-node attribution, priced to the engine that ran it | `ExecutionResult.cost_usd` / `.executor` are normalized across backends |

Choosing an engine changes the bill and maybe the speed. It does not change the gates you pass,
the integrity findings, or the format of the evidence anyone verifies afterward.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `missing … '<bin>' not on PATH` | The backend CLI isn't installed / not on `PATH` | Install it, or choose a different `--executor`; `cadora doctor` lists what's usable |
| `missing … ZAI_API_KEY not set; glm needs it for Z.ai` | GLM needs `claude` **and** `ZAI_API_KEY` | Export `ZAI_API_KEY`; GLM runs through the `claude` CLI |
| `untested … below tested minimum <v>` | The CLI version is outside the tested contract range | Usually still works (warning). Pin to a tested version if a run misbehaves |
| `unparsable … --version exited <n>` | The binary is broken (e.g. a stack trace instead of a version) | Reinstall the backend CLI; don't trust a run on it |
| `unknown executor '<x>'; choose from [...]` | `--executor`/`--construction-executor` name isn't in the registry | Use one of `claude · codex · kiro · glm · antigravity · fixture` |
| `funding=` missing from the run header | Not a Claude run — only Claude Code has a funding source | Expected; `--funding` applies to `claude` only |
| Construction nodes ran on the wrong engine | Node `phase` isn't `construction`, or `--construction-executor` was omitted | Check each node's `phase`; only `construction`-phase nodes reroute |

---

## 8. Reference

**`cadora run <topology>`** — drive a topology on one or two backends.
`--executor claude|codex|kiro|glm|antigravity` (default `claude`) ·
`--model <name>` (backend model override) ·
`--construction-executor <name>` (route construction-phase nodes to a second engine) ·
`--construction-model <name>` (model for that engine) ·
`--funding subscription|api` (Claude funding source; default `subscription`)

**`cadora doctor`** — validate backend CLIs against the tested contract ranges (offline, no model
calls). `--json` (structured report). Exit `0` while ≥1 live backend is usable.

**`cadora compare <run-a> <run-b>`** — diff outcome + cost per node across engines or across time.
`--archive-dir <dir>` · `--json`

**The registry** (`cadora/executors/`): `claude` · `codex` · `kiro` · `glm` · `antigravity` ·
`fixture`. Each is one `NodeExecutor` subclass with a `name` and a `run()` method; resolved by
`get_executor(name)`, which forwards only the kwargs a backend accepts.
