# Verification gates & toolchain integrity in Cadora — user manual

Cadora drives a coding agent through a workflow and then **refuses to take the agent's word** for
the result. After a node runs, a deterministic **gate** re-runs the real build/tests, and a
**toolchain-integrity** scan checks the tools weren't faked. This manual is for the person defining
those gates and reading their verdicts.

The contract is one sentence:

> **Green means proven, not claimed.**

The agent's own `ok` never decides an outcome. A gate re-runs the actual command and reads the
actual result; integrity scans the actual files. At the end of a run, "green" is a fact Cadora
observed and archived — not a sentence the model emitted.

---

## 1. What a gate is

A gate is a **shell command that must exit 0**, run by Cadora itself after a node completes — the
same command a CI job or a developer would run (`ruff check . && pytest -q`, a secret scan, a
build). Gates are:

- **Deterministic-first.** Rules-based checks (exit codes, test counts, static scans) beat visual
  checks beat an LLM-as-judge. A gate lives at the top of that ranking.
- **Fail-closed.** A non-zero exit blocks the run. Ambiguity resolves against the agent.
- **Substance over presence.** A command that *ran* but verified nothing does not pass.

The verdict is not a bare pass/fail. A gate result carries one of five statuses:

| Status | Meaning | Passes? |
|---|---|---|
| `passed` | Exited `0`, and (for a test gate) at least one test actually ran. | **Yes** |
| `failed` | Non-zero exit for a real, in-workspace reason (a genuine test/lint/config failure). | No |
| `vacuous` | Exited `0`, but a test runner it invoked executed **zero** tests. | No |
| `blocked_prerequisite` | Failed because **external** tooling is missing (an uninstallable dependency, a compiler not on `PATH`). Not the agent's to fix. | No |
| `packaging_failed` | The workspace *declares* an installable package that does not `pip install`. | No |

The last three exist because a bare exit code lies: it would fail the agent for missing tooling it
can never install, and pass it for suites that tested nothing or packages that don't build.

---

## 2. Defining gates

### 2.1 Per-topology `gates:` map

A single global command doesn't fit a multi-phase workflow — an inception phase that produces only
markdown has nothing to lint or test. So a topology declares a top-level **`gates:` map**: a command
(and optional `setup`/`wheelhouse`) per gate name. Each node names the gate it runs via `gate:`.

```yaml
name: aidlc-hitl

gates:
  build-test:
    cmd: "ruff check . && pytest -q"
    setup: auto          # provision an isolated gate venv from requirements-dev.txt
  artifact-check:
    cmd: "test -f aidlc-docs/design.md"
    setup: off           # a design stage produces markdown — no venv needed

nodes:
  - id: construction
    role: engineer
    gate: build-test     # this node's gate → the `build-test` entry above
```

A gate spec can be just a string (`build-test: "pytest -q"`), which sets only `cmd`. Any field left
unset falls back to the run-level defaults. A gate name **not** in the map falls back entirely to
`--gate-cmd` / `--gate-setup` / `--gate-wheelhouse`.

> Because YAML 1.1 parses a bare `off`/`on` as a boolean, `setup: off` is accepted and read as the
> `off` mode.

### 2.2 The run-level fallback

If a topology declares no `gates:` map (or a gate name isn't in it), the run-level flags apply to
every gate:

```bash
cadora run examples/aidlc-hitl.topology.yaml --vision vision.md \
  --gate-cmd "ruff check . && pytest -q" \
  --gate-setup auto
```

`--gate-cmd` defaults to `ruff check . && pytest -q`. Non-zero exit blocks the run.

---

## 3. Gate setup (`off` / `auto`)

`--gate-setup` (and a gate's `setup:`) controls whether Cadora provisions an isolated Python
environment before running the gate.

- **`off`** — run the gate in the ambient environment. Right for a cheap check like
  `test -f <deliverable>` on a markdown-only phase.
- **`auto`** (default) — for a Python gate, provision a **cached, isolated virtualenv** from
  `requirements-dev.txt` (or `dev-requirements.txt`, `requirements/dev.txt`) and run the gate inside
  it. If no dev-requirements file exists, there's nothing to provision and the gate runs against the
  ambient environment.

Two things worth knowing about `auto`:

- **The gate venv lives *outside* the workspace** (`~/.cache/cadora/gate-venvs/<hash>`, override with
  `$CADORA_GATE_CACHE`). If it lived inside `cwd`, a gate like `ruff check .` would descend into
  Cadora's own provisioned third-party code and false-fail on it.
- **Only a healthy provision is cached.** On a packaging defect the fingerprint stamp is deliberately
  *not* written, so a stale cache can never resurrect a false-green.

**Offline / wheelhouse.** Pass a wheel directory and the install switches to
`--no-index --find-links <dir>` — a gate provisions entirely offline:

```bash
cadora run <topology> --vision vision.md \
  --gate-setup auto --gate-wheelhouse ./wheels
```

---

## 4. Running just the gates (`cadora gate-check`)

For the "I already have code, just verify it" case, `cadora gate-check` runs a topology's gates
against an existing workspace with **no executor and no LLM cost** — a CI-ready, zero-model way to
apply the exact verification a full run would:

```bash
cadora gate-check examples/aidlc-hitl.topology.yaml --cwd ./my-workspace
```

It honors the per-gate `gates:` map, runs a shared gate once, prints a line per node, and exits
non-zero if any gate fails:

```text
✓ construction · gate:build-test passed
```

```text
✗ construction · gate:build-test failed (exit 5)
    no tests ran in 0.00s
```

It never re-invokes the agent, so it won't pile new code on top of what you meant to check. The same
flags apply: `--gate-cmd`, `--gate-setup {off,auto}`, `--gate-wheelhouse`.

---

## 5. Toolchain integrity

Gates verify the *result*. A separate deterministic check verifies the *toolchain*, because a build
can pass its tests and still be dishonest about how it passed — a local script named `pytest` that
always exits 0, TypeScript "built" by an unrecognized script, or **hollow code** (functions whose
whole body is `pass` / `...` / `raise NotImplementedError`) that weak tests skip over.

### 5.1 Scanning by hand

```bash
cadora integrity ./my-workspace          # human-readable
cadora integrity ./my-workspace --json   # structured report
```

A clean workspace prints `✓ toolchain integrity ok`. Findings look like:

```text
✗ blocking shadowed-toolchain: pytest
    repository-root directory shadows the real 'pytest' package/tool
✗ blocking stub-implementation: adjudicator.py
    3 function(s) have a stub body (pass / ... / raise NotImplementedError) …
    evidence: adjudicator.py:12 adjudicate(); adjudicator.py:20 split()
2 blocking, 0 warning finding(s) in ./my-workspace
```

`cadora integrity` exits non-zero when there's a blocking finding.

### 5.2 Integrity modes on a run

`--integrity-mode` decides how a run *reacts* to findings:

| Mode | Behavior |
|---|---|
| `off` | Not scanned. |
| `audit` (default) | Scanned and recorded in the evidence pack, but findings do not block. |
| `enforce` | A blocking finding fails the run (and engages remediation when configured). |
| `repair` | Like enforce, and a blocking finding drives one constrained repair session, then re-verifies. |

```bash
cadora run <topology> --vision vision.md \
  --integrity-mode enforce --remediate 3
```

Because hollow code passes the build/test gate but fails integrity, a build that is *green-but-hollow*
under `enforce`/`repair` is driven toward real code — a check the shell command alone can't express,
kept deterministic rather than delegated to a judge.

---

## 6. Reading a verdict

### A red gate (`failed`)

The gate ran a real command and it exited non-zero for a real reason. The failure panel shows the
reason, then the gate's own output verbatim:

```text
✗ gate 'build-test' blocked
gate failed output
  F401 [*] `typing.List` imported but unused
  Found 1 error. [*] 1 fixable with the `--fix` option.
```

**Fix it** (here, remove the unused import), or pass `--remediate N` to drive a bounded loop of
fresh, constrained sessions — whose success is decided by *re-running the same gate*, never by the
agent's claim.

### A vacuous gate (`vacuous`)

The command exited `0`, but the test runner executed **zero** tests. The command *ran*; it verified
nothing:

```text
✗ gate 'build-test' blocked
gate vacuous output
  no tests ran in 0.01s
```

**Fix it** by writing real, substantive tests. A suite that runs zero tests is not a fix — and
Cadora will not treat a green-on-nothing as a pass.

### A blocked prerequisite (`blocked_prerequisite`)

The gate failed because **external** tooling is genuinely missing — and Cadora verified the name
isn't a package living in your workspace:

```text
✗ gate 'build-test' blocked by missing prerequisite(s): quotes
```

This status is **terminal** — it never triggers remediation, because a fresh agent session can't
author a third-party library or provision a compiler the sandbox lacks. **You** provide the tooling
(or a wheelhouse), then re-run.

> If the "missing" module actually lives in your tree (e.g. `import mypkg` fails because there's no
> install/`pythonpath`), Cadora classifies it as a **remediable `failed`**, not a prerequisite — that
> *is* the agent's bug to fix.

### A packaging failure (`packaging_failed`)

Your workspace *declares* an installable package (a `[build-system]`/`[project]` table, or a
`setup.py`/`setup.cfg`), but `pip install -e .` hits the setuptools flat-layout auto-discovery panic:
several top-level packages, no explicit `packages` config. **The package genuinely does not build.**

**Fix it** by declaring the packages explicitly — `[tool.setuptools.packages.find]` (with
`where`/`include`/`exclude`), `[tool.setuptools] packages`/`py-modules` — or move the code under
`src/`.

### An integrity finding

Each finding names a `rule`, a `severity` (`blocking`/`warning`), a `path`, and often `evidence`.
The common ones:

- **`shadowed-toolchain` / `vendored-toolchain-shim`** — a file/dir impersonating a real tool
  (`pytest/`, `tsc`, a shim under `vendor/`). *Delete the shim; use the real installed tool.*
- **`stub-implementation`** — a threshold of hollow function bodies. *Fill in real code; don't leave
  `pass`/`raise NotImplementedError` behind a green suite.* (Abstract methods, `Protocol`s,
  `@overload`s, and `.pyi` stubs are excluded — this fires on genuine hollowness.)
- **`typescript-build-substitution`** — TypeScript built by an unrecognized local script instead of a
  declared compiler/bundler. *Build with `tsc`/`tsup`/`esbuild`/`vite`/etc.*
- **`external-workspace-toolchain`** — a build summary that references a tool from *another*
  temporary workspace. *Verify from your own tree.*

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Gate green, but the package doesn't ship | (Fixed) tooling-only fallback used to let tests import from `cwd` and green a non-building package | Current Cadora records `packaging_failed`; fix the `pyproject` packages config |
| `ruff check .` fails on code you didn't write | Gate venv was inside the workspace and got scanned | `--gate-setup auto` keeps the venv outside `cwd`; update Cadora |
| A "missing module" that's right there in the tree | Bare `pytest` can't import your package (no install/`pythonpath`) | It's a remediable `failed`, not a prerequisite — add the package config or install editable |
| `blocked_prerequisite` won't remediate | External tooling is genuinely missing — terminal by design | Provide the dependency/compiler (or a `--gate-wheelhouse`) yourself, then re-run |
| Vacuous keeps re-deriving after a "fix" | The suite still runs zero tests | Write real tests; a green on zero tests is never accepted |
| Integrity findings recorded but nothing blocks | `--integrity-mode audit` (the default) records only | Use `--integrity-mode enforce` (or `repair`) to block/repair |
| Offline install fails in the gate venv | No network and no wheelhouse | Pass `--gate-wheelhouse <dir>` with the needed wheels |

---

## 8. Reference

**Run flags (gates):** `--gate-cmd <cmd>` (default `ruff check . && pytest -q`) · `--gate-setup
{off,auto}` (default `auto`) · `--gate-wheelhouse <dir>`

**Run flags (integrity & remediation):** `--integrity-mode {off,audit,enforce,repair}` (default
`audit`) · `--remediate <N>` (default `0`) · `--remediate-max-cost <USD>`

**`cadora gate-check <topology>`** — run gates against a workspace, no executor / no LLM cost:
`--cwd <ws>` · `--gate-cmd` · `--gate-setup {off,auto}` · `--gate-wheelhouse`. Exits non-zero if any
gate fails.

**`cadora integrity [workspace]`** — scan for counterfeit/substituted tooling: `--json` for the
structured report. Exits non-zero on a blocking finding.

**Gate statuses:** `passed` · `failed` · `vacuous` · `blocked_prerequisite` · `packaging_failed`
(only `passed` proceeds). Remediable: `failed`, `vacuous`, `packaging_failed`. Terminal:
`blocked_prerequisite`.

**Where it's defined:** gate logic in `cadora/gates.py`, integrity in `cadora/integrity.py`, the
bounded remediation loop in `cadora/remediation.py`. Full design write-up:
`docs/verification-gates.md`. Every gate outcome — status, output, exit code, provisioning detail,
integrity findings, and any remediation trail — is archived in the run manifest, so "green" stays
inspectable after the fact.
