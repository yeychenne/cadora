"""OpenAI Codex CLI backend.

Drives ``codex exec --json`` headless. Structured output: Codex emits a JSONL
event stream (``turn.completed`` carries usage), so capture is clean — on par
with Claude Code. Key difference: Codex has **no per-tool allowlist** (no
``--allowedTools``); tool scope is governed by ``--sandbox`` + ``--ask-for-approval``,
so a node's ``tools`` list is advisory here, not enforced. Auth: ``CODEX_API_KEY``
(exec-only) for metered/CI runs, or a ChatGPT subscription via ``codex login``.
(An official Codex SDK — TS ``@openai/codex-sdk`` / Python
``openai-codex`` — is a tighter integration point than shelling out; TODO.)
"""

from __future__ import annotations

import json
import os
import subprocess

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node


class CodexExecutor(NodeExecutor):
    name = "codex"

    def __init__(
        self,
        binary: str = "codex",
        sandbox: str = "workspace-write",  # read-only | workspace-write | danger-full-access
        timeout: int = 1800,
    ):
        self.binary = binary
        self.sandbox = sandbox
        self.timeout = timeout

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        cmd = [
            self.binary, "exec", "--json",
            "--sandbox", self.sandbox,
            "--ask-for-approval", "never",
            "--skip-git-repo-check",
        ]
        if node.model:
            cmd += ["--model", node.model]
        # NOTE: Codex has no per-tool allowlist; node.tools is not enforced here.
        cmd.append(prompt)
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        events, text, usage = _parse_jsonl(proc.stdout)
        return ExecutionResult(
            node_id=node.id,
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            text=text,
            events=events,
            usage=usage,
        )


def _parse_jsonl(stdout: str) -> tuple[list[dict], str, dict]:
    """Parse Codex's JSONL event stream (thread.* / turn.* / item.* / error).

    TODO: pin the exact item schema (agent message vs reasoning vs command).
    For now: collect events, take usage from turn.completed and the last
    assistant message text.
    """
    events: list[dict] = []
    text, usage = "", {}
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        events.append(ev)
        if ev.get("type") == "turn.completed":
            usage = ev.get("usage", usage) or usage
        item = ev.get("item") or {}
        if item.get("type") in ("agent_message", "assistant_message"):
            text = item.get("text", text) or text
    return events, text, usage
