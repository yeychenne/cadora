# Cadora getting started

This guide takes you from a clean install to your first evidence pack. Use a throwaway
workspace for the first run: Cadora drives coding-agent CLIs autonomously, and the agent can
read, write, and run commands inside the `--cwd` you give it.

## 1. Install

Requirements:

- Python 3.10+
- At least one authenticated backend CLI: `claude`, `codex`, `kiro-cli`, or `ZAI_API_KEY` plus
  `claude` for the experimental GLM backend
- The normal toolchain for whatever you ask the agent to build

```bash
pip install cadora
cadora doctor
```

`cadora doctor` is offline. It checks Python, backend binaries, tested version ranges, and
required environment variables where relevant.

## 2. Pick a safe workspace

Start in a fresh directory, git worktree, or container. Do not point the first run at your home
directory, a repository with secrets, or a workspace you cannot throw away.

```bash
mkdir -p ./demo
```

Interactive runs print a blast-radius banner and ask once before launching the autonomous agent.
Automation can bypass that prompt with `--yes` or `CADORA_ASSUME_YES=1`.

## 3. Run the tiny example

The example files live in the Cadora repository. If you installed from PyPI and are not already
inside a checkout, clone the repo first:

```bash
git clone https://github.com/yeychenne/cadora.git
cd cadora
```

Then run:

```bash
cadora run examples/aidlc.topology.yaml \
  --vision examples/hackathon-hello.vision.md \
  --cwd ./demo
```

By default this uses Claude Code and your Claude subscription login. To use Codex instead:

```bash
cadora run examples/aidlc.topology.yaml \
  --vision examples/hackathon-hello.vision.md \
  --cwd ./demo-codex \
  --executor codex --model gpt-5.5
```

The command prints a run id. If you miss it, list recent runs:

```bash
cadora archive ls
```

## 4. Inspect the evidence

```bash
cadora archive show <run-id>
cadora report <run-id>
cadora eval <run-id>
```

The report command writes a portable evidence pack: `report.html`, `report.json`, and
`checksums.txt`. The pack is checksummed, not signed.

## 5. Compare two backends

Run the same topology twice - for example once on Claude and once on Codex - then compare the
archives:

```bash
cadora compare <claude-run-id> <codex-run-id>
cadora usage
```

`compare` shows outcome/model/cost differences node by node. `usage` summarizes tokens, dollars,
estimated costs, and Kiro credits from the archive.

## 6. Optional: human review over MCP

For human-in-the-loop review, mark topology nodes with `review: true` and run through either the
terminal (`cadora run ... --hitl`) or an MCP client:

```bash
pip install 'cadora[mcp]'
cadora mcp --transport stdio
```

The MCP server exposes `start_run`, `review_gate`, `submit_review`, `get_artifact`, and
`run_status`. For HTTP transport, Cadora binds localhost by default and refuses a non-loopback
host unless you explicitly pass `--i-understand-no-auth`.

## What to remember

- Cadora audits the agent's output; it does not sandbox the agent.
- A gate that runs zero tests is `vacuous` and blocks the run.
- Missing toolchains are reported as `blocked_prerequisite`, not fake test failures.
- `cadora integrity` can scan an existing workspace for substituted or counterfeit build/test
  tooling without launching an agent.
