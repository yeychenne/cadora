# Running a gated workflow in Cadora — user manual

Cadora is an audit-grade conductor for coding-agent CLIs. It drives a headless agent (Claude Code,
Codex, …) through a **topology** — a graph of steps — and then refuses to take the agent's word for
the result: after every step it re-runs the real build and tests **itself**, and archives the whole
run as evidence. The promise is one sentence: **green means proven, not claimed.**

This manual takes you from a YAML file to a sealed evidence pack.

> **Use a throwaway workspace for your first run.** Cadora drives the agent autonomously with
> `--dangerously-skip-permissions` inside the `--cwd` you give it — it audits the output, it does
> **not** sandbox the execution. Point it at a fresh directory, a git worktree, or a container.

---

## 1. What a topology is

A topology is a DAG of **nodes**. Each node names a **role** and a **prompt** for the agent;
`depends_on` wires the order; and `gate` names a deterministic check that must pass before the run
moves downstream. Cadora topo-sorts the graph into dependency **waves** and runs them in order.

```yaml
# build-a-service.topology.yaml
name: build-a-service

# Deterministic gates, declared once and named by nodes. `setup: auto` provisions an isolated
# gate venv from requirements-dev.txt; `setup: off` needs none.
gates:
  spec-check:
    cmd: "test -f aidlc-docs/requirements.md"
    setup: off
  build-test:
    cmd: "ruff check . && pytest -q"
    setup: auto

nodes:
  - id: requirements          # wave 1
    role: analyst
    phase: inception
    prompt: "Read vision.md and write aidlc-docs/requirements.md. Do not write code yet."
    tools: [Read, Write]
    gate: spec-check

  - id: design                # wave 2 — depends on requirements
    role: architect
    phase: inception
    prompt: "Turn the requirements into a component design. Still no application code."
    tools: [Read, Write]
    depends_on: [requirements]
    gate: spec-check

  - id: construction          # wave 3 — depends on design
    role: engineer
    phase: construction
    prompt: "Generate the code and a real test suite, then run it until green."
    tools: [Read, Write, Edit, Bash]
    depends_on: [design]
    gate: build-test
```

Three nodes, three waves, two gates. `requirements` and `design` share the cheap `spec-check`
(a design stage produces markdown — nothing to lint or test); `construction` carries the real
`build-test` gate.

---

## 2. Write your first topology

Each node field:

| Field | Meaning |
|---|---|
| `id` | Unique node name — appears in the terminal, the DAG, and the archive. |
| `role` | The hat the agent wears: `analyst`, `architect`, `engineer`, … (descriptive). |
| `phase` | Optional — `inception` / `construction` / `operations`, used for routing and reporting. |
| `prompt` | What the agent should do at this node. Upstream nodes' output is appended automatically. |
| `tools` | The tools the agent may use (e.g. `[Read, Write, Edit, Bash]`). |
| `depends_on` | The nodes that must finish first. This is what makes it a DAG. |
| `gate` | The deterministic gate to run after the node (a key from the `gates:` map). |
| `review` | `true` marks a **human** gate — see the human-in-the-loop manual. |

The `gates:` map declares each gate's `cmd` (the shell command Cadora re-runs), an optional `setup`
(`auto` provisions an isolated venv from `requirements-dev.txt`, `off` uses the ambient one), and an
optional `wheelhouse` for offline installs. Any field left unset falls back to the run-level
`--gate-cmd` / `--gate-setup` / `--gate-wheelhouse`.

Ready-made shapes ship in the repo's `examples/`: `sequential-pipeline` (a → b → c),
`parallel-fanout` (a diamond that runs concurrently), `aidlc.topology.yaml` (the full AI-DLC method
in one node), and `aidlc-hitl` (with human review gates).

---

## 3. Run it

Point Cadora at the topology, an **executor**, and a **vision** file:

```bash
cadora run build-a-service.topology.yaml \
  --executor claude --model claude-opus-4-8 \
  --vision vision.md \
  --cwd ./demo \
  --archive-dir runs
```

- `--executor` — `claude` (default), `codex`, `kiro`, `glm`, or `antigravity`. Add
  `--model` to pin a model (e.g. `--executor codex --model gpt-5.5`).
- `--vision` — your product vision; it installs the AI-DLC workspace into `--cwd`.
- `--cwd` — the workspace the agent works in. Use a throwaway.
- `--archive-dir` — where the run is recorded (defaults to `runs/`).

Cadora prints a run header, then one block per node:

```text
cadora · executor=claude · funding=subscription · run=build-a-service-20260717-0904
▶ requirements · claude-opus-4-8 · running… (generating documents; this can take a few minutes)
  ✓ requirements   $0.3100   gate:spec-check ok   integrity:ok
▶ design · claude-opus-4-8 · running…
  ✓ design   $0.4600   gate:spec-check ok   integrity:ok
▶ construction · claude-opus-4-8 · running…
  ✓ construction   $1.2700   gate:build-test ok   integrity:ok
✓ run complete -> runs/build-a-service-20260717-0904
```

The `✓`/`✗` glyph is the **gate's** verdict, not the agent's — Cadora re-ran the command itself.

**Useful run options:**

```bash
# Run independent nodes in a wave concurrently (only the agent sessions overlap).
cadora run <topology> --executor claude --max-parallel 3

# Set a run-level gate for nodes whose gate spec leaves cmd unset, or for the whole run.
cadora run <topology> --executor claude --gate-cmd "ruff check . && pytest -q"

# Enforce toolchain integrity (block on tool impersonation / hollow stub code).
cadora run <topology> --executor claude --integrity-mode enforce

# Turn a failing-but-fixable gate into a bounded repair loop (fresh, constrained sessions).
cadora run <topology> --executor claude --remediate 2 --remediate-max-cost 5.00

# Pause at review: true nodes for a human (see the human-in-the-loop manual).
cadora run <topology> --executor claude --hitl --review-file --review-timeout 0
```

**Already have the code and just want to verify it?** `cadora gate-check <topology> --cwd <workspace>`
runs the topology's gates against an existing workspace with **no executor and no model cost** —
CI-ready, exits non-zero if any gate fails.

---

## 4. The preflight trust gate

Because the agent runs autonomously with your permissions, an interactive run prints a
**blast-radius banner** and asks once before it starts:

```text
  ┌─ cadora · autonomous run ─────────────────────────────────────────
  │  backend    : claude (--dangerously-skip-permissions)
  │  workspace  : /Users/you/demo
  │  Cadora audits the agent's OUTPUT (gates · integrity · evidence),
  │  it does NOT sandbox EXECUTION. The agent can read/write/run there
  │  with your permissions. Point it only at a trusted or throwaway
  │  workspace (a fresh dir, worktree, or container). Keep credentials
  │  out of that environment.
  └───────────────────────────────────────────────────────────────────
  Proceed with an autonomous run in /Users/you/demo? [y/N]
```

Answer `y` to proceed; anything else prints `aborted — no run started.`

For automation, skip the prompt with **`--yes`** (or `-y`), or set **`CADORA_ASSUME_YES=1`**. The
banner still prints — it is the audit record — but the run proceeds without asking. A piped,
TTY-less run (CI) does the same automatically, so pipelines are never blocked.

---

## 5. Watch it live

In another terminal, serve the same archive:

```bash
cadora dashboard --archive-dir runs
```

Open **http://127.0.0.1:8765/**, then click your run. The run detail page is the operator view:

- a **DAG progress canvas** — each node is a light chip whose fill is its state (green completed,
  blue running, pink failed, grey idle), carrying its **cost**, **context tokens**, and **badges**
  for its gate, toolchain-integrity, and human-review outcomes. A `gate vacuous` or
  `gate blocked_prerequisite` badge surfaces a gate that did not truly pass, right on the node.
- selected-node facts (model, backend, cost, context tokens, review state), an activity timeline,
  node output, produced artifacts, and raw metadata.
- a **FinOps** panel: a token split and cost broken down by day, model, executor, and funding.

The dashboard is a read-only cockpit over the archive: no login, no database, loopback by default.
If you bind it off `127.0.0.1`, front it with TLS and auth first — it serves run metadata and
artifacts unauthenticated.

The same usage is available in the terminal:

```bash
cadora usage --archive-dir runs --since 7d
```

---

## 6. Read the result

Every run is archived under `runs/<run-id>/`. Inspect it without re-running anything:

```bash
cadora archive ls                       # list recent runs (mark, id, executor, topology, cost)
cadora archive show <run-id>            # per-node: cost, gate, integrity, review, remediation
```

`archive show` prints one line per node — the same fields as the live run, read back from the
manifest:

```text
run build-a-service-20260717-0904  ·  executor=claude  ·  topology=build-a-service  ·  ok=True
  ✓ requirements   claude-opus-4-8   $0.3100   funding=subscription   gate:spec-check ok   integrity:ok
  ✓ design         claude-opus-4-8   $0.4600   gate:spec-check ok   integrity:ok
  ✓ construction   claude-opus-4-8   $1.2700   gate:build-test ok    integrity:ok
```

Then produce a portable, tamper-evident **evidence pack**:

```bash
cadora report <run-id>                  # writes report.html + report.json + checksums.txt
cadora eval <run-id>                    # deterministic AI-DLC checks; exits non-zero on fail
cadora verify <run-id>                  # recompute every hash, then check any signature
cadora compare <run-a> <run-b>          # diff the same topology across backends or over time
```

`cadora report` writes a self-contained pack checksummed with SHA-256. It is checksummed, not
signed, by default — `cadora sign <run-id>` adds a detached, attributable signature over the
checksums, which `cadora verify` then checks. After the fact, anyone can open the archive and see
exactly which command ran on each node, what it printed, why a status was assigned, and what it cost.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `aborted — no run started` | You answered `N` at the preflight prompt | Re-run and answer `y`, or pass `--yes` for automation |
| Run stops with `gate:… VACUOUS` | The suite exited 0 but ran **zero** tests | Write real, substantive tests; or add `--remediate N` to drive a fix |
| Run stops with `blocked_prerequisite` | External tooling is missing (not in the workspace) | Install the toolchain and re-run — it is not the agent's to author, so it never remediates |
| Run stops with `packaging_failed` | The workspace declares a package that will not build (flat-layout panic) | Declare packages explicitly (`[tool.setuptools.packages.find]`) or move code under `src/`; then `--remediate` |
| Run stopped at a node | That node's gate did not pass | Read `cadora archive show <run-id>` (or the dashboard badge); fix, then `--resume-from <node>` |
| Dashboard page is empty | Wrong `--archive-dir`, or started outside the runs dir | Start from the directory containing `runs/`, or pass `--archive-dir`; confirm with `cadora archive ls` |
| A gate false-fails on its own tooling | (Handled) the gate venv lives **outside** the workspace | Update Cadora; the isolated venv keeps the scan on the agent's code only |
| `cadora doctor` flags a backend | The backend CLI is missing or out of the tested range | Install/authenticate the CLI; `doctor` is offline and lists the exact gap |

---

## 8. Reference — key `run` flags

**Executor & workspace:** `--executor <claude|codex|kiro|glm|antigravity>` · `--model <id>` ·
`--construction-executor <exec>` / `--construction-model <id>` (route construction nodes elsewhere) ·
`--cwd <dir>` · `--vision <path>` · `--tech-env <path>` · `--funding <subscription|api>` ·
`--archive-dir <dir>` · `--run-id <id>`.

**Gates:** `--gate-cmd <command>` (run-level fallback) · `--gate-setup <off|auto>` ·
`--gate-wheelhouse <dir>` (offline wheels) · `--timeout <seconds>` (per-node executor, default 1800).

**Verification:** `--integrity-mode <off|audit|enforce|repair>` (default `audit`) ·
`--remediate <N>` (bounded repair, default 0) · `--remediate-max-cost <USD>`.

**Human review:** `--hitl` · `--review-file` · `--review-timeout <seconds>` (`0` = indefinite) —
see the human-in-the-loop manual.

**Concurrency & resume:** `--max-parallel <N>` (default 1) · `--resume-from <node>` ·
`--skip <node[,node…]>` · `--allow-drift`.

**Trust gate:** `--yes` / `-y` (also `CADORA_ASSUME_YES=1`).

**Related commands:** `cadora dashboard` · `cadora gate-check` · `cadora archive ls|show` ·
`cadora report` · `cadora eval` · `cadora verify` · `cadora sign` · `cadora compare` ·
`cadora usage` · `cadora doctor`.
