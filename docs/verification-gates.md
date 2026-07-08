# Verification Gates in Cadora

*How Cadora decides that agent-built code is green — and why "green" means proven, not claimed.*

---

## Abstract

Cadora is an audit-grade conductor for coding-agent CLIs. It drives a headless coding
agent through a declared, multi-step workflow and then refuses to take the agent's word for
the result. The mechanism that enforces that refusal is the **verification gate**: a
deterministic check that re-runs the real build, tests, or scan itself and decides an outcome
from exit codes and observed evidence, not from the agent's self-report. This document
describes how gates actually work in the current implementation — the status vocabulary, the
classification logic that turns a bare exit code into a meaningful verdict, the out-of-workspace
isolation that keeps a gate from false-failing on its own provisioned tooling, and the bounded
remediation loop that turns a failing gate into an engine of completion. Every claim is
grounded in `cadora/gates.py`, `cadora/remediation.py`, and `cadora/integrity.py`. Where a
capability is a deliberate stub, this document says so.

---

## 1. The problem: an agent will always claim success

Coding agents can build real software. The hard part of agentic delivery is not generation —
it is *verification*. Left to narrate its own outcome, an agent will report success: it will
say the tests pass, the package builds, the lint is clean. Sometimes that report is true.
Sometimes the agent ran a test suite that contained zero tests, wrote a `pyproject.toml` that
cannot actually build, stubbed the functions its tests skip over, or — under an offline
sandbox — quietly stood up a local script named `pytest` and "passed."

A vendor's tool verifying that vendor's agent is the fox auditing the henhouse. Cadora
verifies from the outside, and the load-bearing primitive is the gate. A gate is a
deterministic check that Cadora runs *itself*, after a node completes, to decide whether the
work is genuinely done. The contract is one sentence:

> **Green means proven, not claimed.**

The agent's own `ok` never decides an outcome. A gate re-runs the actual command and reads
the actual result. This is the audit-grade promise: at the end of a run, "green" is a fact
Cadora observed and archived, not a sentence the model emitted.

---

## 2. Design principles

Four principles govern every gate decision. They are opinions encoded as code, and they are
worth stating plainly because they explain the design's less obvious choices.

**Deterministic-first verification ordering.** Cadora follows Anthropic's own stated ranking
for verifying agent output: rules-based checks beat visual checks beat an LLM acting as
judge. The gate machinery lives entirely at the top of that ranking. A shell command that a
CI system would run — a linter, a test runner, a secret scan, a build — is deterministic,
reproducible, and cheap. That is what a gate is.

| Rank | Verification kind | Status in Cadora |
|------|-------------------|------------------|
| 1 (preferred) | Rules-based / programmatic (exit codes, test counts, static scans) | The `ShellGate` — fully implemented |
| 2 | Visual / rendered-output inspection | Not a gate primitive; belongs to human review |
| 3 (last resort) | LLM-as-judge (reviewer subagent for semantics the shell can't express) | Deliberately a **stub** — see below |

**Fail-closed.** Ambiguity resolves against the agent. A non-zero exit blocks. A test runner
that verified nothing does not pass. A declared package that does not build does not green.
If a gate cannot reach a confident *pass*, it does not pass.

**Substance over presence.** A gate checks that something was genuinely accomplished, not
that a command was invoked. The clearest expression of this is vacuous-test detection
(Section 4): a test runner can exit `0` having executed zero tests. The command *ran*; it
*verified nothing*. Presence of a green exit is not substance, and the gate refuses to treat
it as such.

**The LLM-judge gate is the last resort, and today it is a stub.** `cadora/gates.py` ends
with an explicit `TODO`: a `ReviewerGate` that would spawn a reviewer subagent for semantic
checks the shell can't express, demoted *below* the deterministic gates. It is not
implemented. This is an honest statement of the codebase, and it is also the intended design:
an LLM judge is the least trustworthy verifier, so it sits last and does not gate on its own.
(An advisory LLM rubric exists as an opt-in on `cadora eval --judge`, but it is explicitly
advisory and never overrides a deterministic verdict — it is not a gate.)

---

## 3. Anatomy of a gate

The gate primitive is `ShellGate`. It carries a name, a shell `command`, an optional
`setup_mode` (`off` or `auto`), and an optional `wheelhouse` path. Its `check(cwd)` runs the
command in the node's workspace and returns a `GateResult`:

```python
ShellGate(name="build-test", command="ruff check . && pytest -q", setup_mode="auto")
```

The command is a real command — the same one a CI job or a developer would run. A non-zero
exit blocks the run. There is no separate "agent says it passed" path; there is only the
process return code and what Cadora reads out of the output.

The verdict is not a boolean. A `GateResult` carries a `status` drawn from a five-value
vocabulary, because a bare pass/fail cannot distinguish "your code is broken" from "the test
tool isn't installed" from "the suite ran nothing." Each status means something precise:

| Status | Meaning | Passes? | Remediable? |
|--------|---------|---------|-------------|
| `passed` | The command exited `0` and (for test gates) at least one test actually ran. | Yes | — |
| `failed` | The command exited non-zero for a real, in-workspace reason (a genuine test failure, a lint error, a fixable packaging/config bug). | No | Yes |
| `blocked_prerequisite` | The command failed because *external* tooling is missing (an uninstallable third-party dependency, a compiler not on `PATH`). Not the agent's fault, not the agent's to fix. | No | **No** |
| `vacuous` | The command exited `0`, but a test runner it invoked executed **zero** tests. It verified nothing. | No | Yes |
| `packaging_failed` | The workspace *declares* an installable package that does not build (`pip install -e .` hit the setuptools flat-layout auto-discovery panic). A green here would certify a deployable artifact that cannot be produced. | No | Yes |

Two of these — `blocked_prerequisite` and the pair (`vacuous`, `packaging_failed`) — exist
only because a raw exit code lies about what really happened. The next section explains the
classification logic that produces them.

---

## 4. Classification intelligence: why a bare exit code isn't enough

A gate that only read `returncode == 0` would be wrong in both directions. It would fail the
agent for missing tooling it could never install, and it would pass the agent for suites that
tested nothing and packages that don't build. Cadora inspects the command *and* its output to
classify the outcome correctly.

### 4.1 Prerequisite classification — external vs. in-workspace

When a command fails, Cadora scans its output for the fingerprints of a *missing dependency*
across ecosystems: `No module named 'x'` (Python), `command not found` (shell tools),
`Could not find a version that satisfies...` / `No matching distribution found` (pip),
`Cannot find module 'x'` (Node), `no required module provides package x` (Go modules),
`can't find crate for \`x\`` (Rust). It also recognizes the specific case of a coverage flag
(`--cov`) failing because `pytest-cov` isn't installed.

But a naive "missing module → blocked" rule would misclassify the most common agent bug of
all. Consider a workspace containing a package `mypkg/` where the agent forgot the install
step or a `pythonpath` setting, so the bare `pytest` console script cannot `import mypkg`.
The output says `No module named 'mypkg'` — which *looks* like a missing prerequisite, but
the module is right there in the tree. That is a **fixable packaging/config bug**, not a
terminal external blocker.

So Cadora filters the candidate list: any name that resolves to a directory or module living
in the workspace (`cwd/mypkg`, `cwd/mypkg.py`, or `cwd/src/mypkg`) is dropped from the
prerequisites set. Only genuinely-external names survive as prerequisites.

The consequence is the crux of the design:

- **External tooling missing → `blocked_prerequisite`.** This status is *terminal*. It never
  triggers remediation, regardless of how many attempts remain. Cadora will not burn a fresh
  agent session trying to author a third-party library or provision a compiler that the
  sandbox lacks — that is not an agent-repairable condition, and pretending otherwise wastes
  spend and produces nothing.
- **An in-workspace module that won't import → a remediable `failed`.** This *is* the agent's
  bug, and `--remediate` can drive a real fix (add the package config, fix the import path,
  install the local package editable).

The distinction is deliberate: don't ask the agent to fix what it cannot, and do ask it to
fix what it can.

### 4.2 Vacuous-pass detection — substance over a green exit

Several test runners exit `0` when they run *no tests at all*: `go test` on a package with no
test files, `cargo test` on an empty suite, `jest --passWithNoTests`, a `pytest` invocation
that collected zero items. The command succeeded; nothing was verified. Treating that as a
pass is exactly the false-green the audit-grade promise exists to prevent.

Cadora guards against it in two stages, and only for gates that actually invoke a test runner
(matched across pytest, `py.test`, jest, vitest, mocha, `go test`, `cargo test`,
`swift test`, `deno test`, rspec, and the `npm`/`yarn`/`pnpm` `test` scripts):

1. **Did at least one test run?** The output is checked for positive evidence of execution —
   a non-zero count like `N passed` / `N passing` / `N failed`, a Go `ok  <pkg>` line, a
   `--- PASS` / `--- FAIL` marker, or a `test ... ok` line. If any is present, the gate is a
   real pass. This positive check comes *first*, and it exists to prevent false positives:
   a multi-package `go test ./...` where only some packages carry tests still shows real
   execution, so it is not flagged.
2. **Only if nothing ran**, the output is checked for explicit "no tests" signals —
   `no tests ran`, `collected 0 items`, `no test files`, `running 0 tests`, `no tests
   found`, `tests: 0 total`, `0 passing`, `executed 0 of 0`.

A gate that invoked a test runner, exited `0`, showed no evidence of any test running, and
matched a zero-tests signal is reported `vacuous` and blocks. It is remediable: a fresh
session is told, in no uncertain terms, that a suite which runs zero tests is not a fix, and
is driven to write real, substantive tests.

### 4.3 Packaging validation — a worked example of the philosophy

This safeguard is the clearest illustration of "green means a *deployable* artifact, not a
lucky test run," and it is worth walking through because it was a real, recently-added fix.

The setuptools *flat layout* is a project with several top-level packages at the repository
root — say `pkg_a/` and `pkg_b/` — and no explicit `packages` configuration. When such a
project also *declares* itself installable (it has a `[build-system]` or `[project]` table,
or a `setup.py`/`setup.cfg`), `pip install -e .` triggers setuptools' auto-discovery, which
finds multiple top-level packages, cannot guess which one to ship, and aborts with the
"Multiple top-level packages discovered in a flat-layout" panic. **The package genuinely does
not build.** `pip install .` and `python -m build` fail the same way; a Lambda or container
bundling step would fail the same way.

Here is the trap the old behavior fell into. Cadora provisions an isolated gate environment
(Section 5). When the editable install panicked, it fell back to installing the dev tooling
*alone* so the gate could still run — and then the test command, run from the workspace
directory, would happily `import pkg_a` straight off the filesystem (no install needed to
import from `cwd`), the tests passed, and the gate went **green**. That green certified a
package `pip install .` cannot produce — a false pass of the most dangerous kind: it looks
shipped and isn't.

The current behavior distinguishes *why* the editable install failed. Cadora still provisions
the dev tooling via the fallback so the environment is usable — but if the failure output
matches the flat-layout auto-discovery panic specifically, it records `packaging_failed` and
the gate **fails** instead of running the command and greening on incidental imports. The
`GateResult` carries a concrete fix hint: declare the packages explicitly with
`[tool.setuptools.packages.find]` (with `where`/`include`/`exclude`), set `[tool.setuptools]
packages`/`py-modules`, or move the code under `src/`. Because `packaging_failed` is
remediable, `--remediate` repairs the `pyproject.toml` rather than shipping the false pass.

Two subtleties keep this correct:

- A `pyproject.toml` that carries *only* `[tool.*]` config (very common — agents write one
  just for pytest or ruff settings) is **not** treated as installable. Attempting an editable
  install on it would itself trip flat-layout discovery and take the whole gate environment
  down. Cadora attempts the editable install only when there is a genuine build declaration.
- Other, non-flat-layout editable-install failures keep the tooling-only fallback and do
  *not* become a packaging failure — so offline and wheelhouse installs don't sprout new
  false-blocks. Only the specific, provably-does-not-build case is escalated.

---

## 5. Isolation and provisioning

For Python gates run with `setup_mode="auto"`, Cadora provisions a cached, isolated
virtualenv and runs the gate command inside it. The provisioning logic (`_prepare_python_gate`)
embodies several decisions that exist because the naive version breaks in the field.

**The gate virtualenv lives OUTSIDE the workspace.** This is not incidental — it is the whole
point of where the venv lives. If the environment sat inside `cwd` (e.g.
`cwd/.cadora/gate-venv`), any gate that globs the tree — `ruff check .`, `mypy .`, a coverage
run — would descend into Cadora's *own provisioned third-party code* and false-fail on lint
or type errors that have nothing to do with the agent's work. So the venv is created under
`~/.cache/cadora/gate-venvs/<hash>`, keyed by a hash of the resolved workspace path (stable
across runs of the same workspace), overridable with `$CADORA_GATE_CACHE`. The workspace the
gate scans contains only the agent's code.

**Auto-provision from dev requirements.** Cadora looks for `requirements-dev.txt`,
`dev-requirements.txt`, or `requirements/dev.txt`. If none exists, there is nothing to
provision and the gate runs against the ambient environment. If one exists, Cadora creates the
venv and `pip install`s the dev requirements — plus an editable install of the project itself,
when (and only when) the project genuinely declares an installable package, per Section 4.3.

**Offline / wheelhouse support.** Passing a wheelhouse switches the install to
`--no-index --find-links <wheelhouse>`, so a gate can provision entirely from a local
directory of wheels with no network access — the offline-sandbox case.

**Fingerprint caching, healthy-only.** Provisioning is expensive, so it is cached behind a
fingerprint: a SHA-256 over the dev-requirements contents, the `pyproject.toml` contents, the
wheelhouse path, and the Python version. A stamp file records it; a matching stamp on the next
run means "provision: cached" and the install is skipped. Change any input and the environment
is rebuilt. Crucially, **only a healthy provision is cached** — on a packaging defect
(Section 4.3) the stamp is deliberately *not* written, so every run re-derives the failure
until the `pyproject.toml` is fixed (which changes its fingerprint). A cached stamp must never
be able to resurrect a false-green, so the cache refuses to remember a broken build.

If the venv itself cannot be created, or the install fails for a non-packaging reason that
leaves no usable tooling, the gate returns `blocked_prerequisite` with the provisioning output
and the missing package names — the archive records *why* provisioning was impossible rather
than misreporting the application as broken.

---

## 6. From gate to completion: bounded remediation

A failing gate need not be the end of a run. When `--remediate N` is set, a *remediable*
failure feeds a bounded loop of fresh, constrained sessions (`cadora/remediation.py`). The
loop's contract is the audit-grade promise applied recursively: **green is decided by
re-running the same gate, never by the executor's claim of success.**

The shape of one attempt:

1. Build a constrained prompt from the *current* gate detail — the exact gate command, the
   exact status, and the exact output, fed back **verbatim**. (Any integrity findings are
   attached too; see Section 7.)
2. Run a fresh session against that prompt. Each attempt gets its own synthetic node id
   (`<node>-remediate-<k>`), so it is a clean session, not a continuation.
3. **Re-run the same `ShellGate.check` on the workspace** and (when integrity is enforced)
   rescan integrity.
4. Decide green *from that re-run*: the attempt succeeds only if the gate now passes (and
   integrity holds). The executor's own `ok` does not decide this — a false claim of success
   from the agent never substitutes for the gate.

The prompt states the hard anti-cheating rules explicitly, because the cheapest way to make a
gate pass is to defeat it:

- **Do not weaken, delete, skip, or bypass** the gate command or the tests it runs.
- **Do not impersonate tools** — no local packages or scripts that stand in for pytest, pip,
  setuptools, TypeScript, `tsc`, `npm`, or any other declared tool.
- **No vacuous passes** — a gate that passes having run zero tests is not a fix; write real,
  substantive code and tests.
- **Stay truthfully blocked if genuinely stuck** — if a real blocker (missing tooling, an
  ambiguous spec) prevents a fix, leave the project truthfully blocked and document it; never
  claim success not achieved.
- **Preserve existing behavior and the security baseline.**

Which statuses are remediable is policy: by default `failed`, `vacuous`, and
`packaging_failed`. `blocked_prerequisite` is **never** remediable — the loop will not engage
for it, because missing external tooling is not something a fresh session can author.

The loop is bounded on two axes: attempt count (`N`) and optional cost
(`--remediate-max-cost`). On any bound hit — attempts exhausted, cost ceiling reached, the
executor itself failing, or an enforced integrity finding that never clears — the run stops
**`honest-blocked`** with the full per-attempt trail archived (each attempt's prompt, its
execution result, its gate re-run, and its cost). A genuine success terminates
**`completed-green`**. There is no third outcome. A fabricated pass is not reachable: the only
way to reach `completed-green` is for the same deterministic gate to genuinely pass.

```text
failed | vacuous | packaging_failed   ──►  remediate (fresh session, verbatim detail)
                                             └─► re-run SAME gate
                                                   ├─ passes ──► completed-green
                                                   └─ still failing ─┐
                                                                     ▼
                                          bound hit ──► honest-blocked (+ full trail)
```

---

## 7. Toolchain integrity: a parallel deterministic check

Gates verify the *result*. A separate deterministic check verifies the *toolchain*, because
a build can pass its tests and still be dishonest about how it passed. `cadora/integrity.py`
scans a workspace for tool-impersonation and hollow-toolchain patterns, producing
`blocking` or `warning` findings. It runs alongside gates and, under an enforced mode, feeds
the same remediation loop.

What it detects, all deterministically:

- **Shadowed toolchains** — a repository-root directory or file named after a real tool
  (`pytest/`, `setuptools/`, `pip/`, `typescript/`, `pytest.py`, `tsc`, ...) that would
  shadow the genuine package, and vendored shims under `vendor/` or `scripts/` that
  impersonate a standard build/test tool.
- **TypeScript build substitution** — TypeScript sources built by an unrecognized local
  script instead of a declared compiler/bundler (`tsc`, `tsup`, `esbuild`, `swc`, `vite`,
  `rollup`, `bun build`, `deno task`).
- **External-workspace toolchains** — a build/test summary that references a tool from
  *another* temporary project workspace (verification borrowed from a directory outside the
  run's own tree).
- **Hollow code (`stub-implementation`)** — a threshold of functions whose entire body is
  `pass`, `...`, or `raise NotImplementedError`. Such code *looks* implemented, tests that
  skip over it pass, and the build/test gate misses it entirely. Integrity catches it.
  Abstract methods, `Protocol`s, `@overload`s, and `.pyi` stubs are excluded, so it fires on
  genuine hollowness, not on legitimate interfaces.

Integrity runs in one of four modes:

| Mode | Behavior |
|------|----------|
| `off` | Not scanned. |
| `audit` | Scanned and recorded, but findings do not block. |
| `enforce` | A blocking finding fails the run (and engages remediation when configured). |
| `repair` | Like enforce, and a blocking finding drives a constrained repair session, then re-verifies. |

The composition matters: hollow code passes the build/test gate but fails integrity, so a
build that is green-but-hollow under `enforce`/`repair` is driven toward real code — a check
the shell command alone cannot express, kept deterministic rather than delegated to a judge.

---

## 8. Per-gate configuration

A single global gate command does not fit a multi-phase workflow. An inception phase that
produces only markdown design documents has nothing to lint or test, and forcing
`ruff check . && pytest -q` on it would fail it for the wrong reason. So a topology can
declare a **`gates:` map**: a command (and optional `setup`/`wheelhouse`) per gate name.

```yaml
gates:
  build-test:
    cmd: "ruff check . && pytest -q"
    setup: auto            # provision an isolated gate venv from requirements-dev.txt
  artifact-check:
    cmd: "test -f aidlc-docs/design.md"
    setup: off             # a design stage produces markdown — no venv needed
```

Each node names the gate it runs via its `gate:` field. Any field left unset on a gate spec
falls back to the run-level `--gate-cmd` / `--gate-setup` / `--gate-wheelhouse`. A gate name
not present in the map falls back entirely to the run-level defaults. (A convenience: because
YAML 1.1 parses a bare `off`/`on` as a boolean, `setup: off` is accepted and read as the
`off` mode.)

For the "I already have code, just verify it" case, **`cadora gate-check <topology> --cwd
<workspace>`** runs a topology's gates against an existing workspace with **no executor and no
LLM cost**. It honors the per-gate `gates:` map, runs a shared gate once, and exits non-zero
if any gate fails — a CI-ready, zero-model way to apply the exact same verification a full
run would apply, without re-invoking the agent (which would risk piling new code on top of
what you meant to check).

---

## 9. The audit trail: green is inspectable after the fact

A verdict you cannot inspect is not audit-grade. Every gate outcome is archived in full. The
`GateResult` — its `status`, the tail of the command's combined stdout/stderr, the
classified `missing_prerequisites`, the `exit_code`, and the provisioning `setup_detail`
(which venv, which requirements, which wheelhouse, whether the provision was cached) — is
serialized whole into the run manifest alongside any integrity findings. Under remediation,
every attempt's prompt, execution, gate re-run, and cost is captured too.

The consequence is that a run's "green" is not a claim you must trust — it is a record you can
read. After the fact, anyone can open the archive and see exactly which command ran, what it
printed, why a status was assigned, what tooling was provisioned to run it, and — if the run
went through remediation — the complete trail of what was tried and what finally held. The
gate does not just decide the outcome; it leaves the evidence for the decision.

---

## Summary

A gate is a deterministic check that Cadora runs itself to decide whether agent-built work is
genuinely done. It reads exit codes and real output, never the agent's self-report. Its
five-value status vocabulary distinguishes a real failure from missing external tooling, a
suite that tested nothing, and a package that cannot build — because a bare pass/fail cannot.
It runs isolated *outside* the workspace so it never false-fails on its own provisioned
tooling, and caches only healthy provisions so a stale stamp can never resurrect a false-green.
A remediable failure can drive a bounded loop of fresh, constrained sessions whose success is
decided by re-running the *same* gate — never by a claim. A parallel integrity scan catches the
dishonesty the shell can't express. The reviewer/LLM-judge gate, by design the last resort, is
an honest stub. And every outcome is archived, so "green" remains inspectable long after the
run ends. That is what audit-grade means here: not that the agent said it worked, but that
Cadora proved it did — and kept the proof.
