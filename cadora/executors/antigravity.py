"""Google Antigravity CLI (``agy``) backend — EXPERIMENTAL.

Drives ``agy -p`` headless. Two current defects make this rougher than the other
backends (researched 2026-06-19):

  1. **No JSON output** — ``--output-format`` is not implemented on ``agy``.
  2. **``agy -p`` stdout is broken** — it writes the response to the controlling
     terminal, NOT stdout, so piping/capture yields nothing. The community
     workaround is to read agy's own transcript JSONL under
     ``~/.gemini/antigravity-cli/brain/<conv-id>/.system_generated/logs/transcript.jsonl``
     and extract the final ``PLANNER_RESPONSE`` entry.

This stub runs the command and best-effort captures stdout; wire the transcript
parser (``_read_transcript_fallback``) before relying on it. Auth: Google
sign-in (free tier incl. Gemini 3) or an API key. NOTE: the legacy ``gemini``
CLI reached EOL for free tiers on 2026-06-18 — target ``agy``, not ``gemini``.
"""

from __future__ import annotations

import os
import subprocess

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node


class AntigravityExecutor(NodeExecutor):
    name = "antigravity"

    def __init__(self, binary: str = "agy", timeout: int = 1800):
        self.binary = binary
        self.timeout = timeout

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        cmd = [self.binary, "-p", prompt, "--dangerously-skip-permissions"]
        if node.model:
            cmd += ["--model", node.model]
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            timeout=self.timeout,
        )
        text = proc.stdout or _read_transcript_fallback(cwd)
        return ExecutionResult(
            node_id=node.id,
            ok=proc.returncode == 0,
            exit_code=proc.returncode,
            text=text,  # plain text; no structured events on agy today
        )


def _read_transcript_fallback(cwd: str) -> str:
    """TODO: locate the latest ``agy`` transcript JSONL and extract the final
    ``PLANNER_RESPONSE`` (the ``agy -p`` stdout-capture workaround). Returns ''
    until implemented."""
    return ""
