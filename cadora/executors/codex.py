"""OpenAI Codex CLI backend.

Drives ``codex exec --json`` headlessly and normalizes its JSONL event stream.
Codex has no per-node tool allowlist: filesystem/process scope is enforced by
its sandbox, while ``node.tools`` remains advisory metadata.

Authentication is delegated to the Codex CLI (ChatGPT login or its supported
API-key flow). Cadora never creates or persists credentials.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node


class CodexExecutor(NodeExecutor):
    name = "codex"

    def __init__(
        self,
        binary: str = "codex",
        sandbox: str = "workspace-write",  # read-only | workspace-write | danger-full-access
        timeout: int = 1800,
        model: str | None = None,
        ephemeral: bool = True,
        ignore_user_config: bool = True,
    ):
        self.binary = binary
        self.sandbox = sandbox
        self.timeout = timeout
        self.model = model
        self.ephemeral = ephemeral
        self.ignore_user_config = ignore_user_config

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        cmd = [
            self.binary,
            "exec",
            "--json",
            "--sandbox",
            self.sandbox,
            "--skip-git-repo-check",
            "-c",
            'approval_policy="never"',
        ]
        if self.ephemeral:
            cmd.append("--ephemeral")
        if self.ignore_user_config:
            cmd.append("--ignore-user-config")
        resolved_model = node.model or self.model
        if resolved_model:
            cmd += ["--model", resolved_model]
        # NOTE: Codex has no per-tool allowlist; node.tools is not enforced here.
        cmd.append(prompt)
        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env={**os.environ, **(env or {})},
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            parsed = _parse_jsonl(_as_text(exc.stdout))
            return ExecutionResult(
                node_id=node.id,
                ok=False,
                exit_code=124,
                text=parsed.text,
                events=parsed.events,
                usage=parsed.usage,
                model=resolved_model,
                meta={
                    "timed_out": True,
                    "timeout_seconds": self.timeout,
                    "thread_id": parsed.thread_id,
                    "error": parsed.error,
                    "sandbox": self.sandbox,
                },
            )

        parsed = _parse_jsonl(proc.stdout)
        ok = proc.returncode == 0 and parsed.completed and not parsed.failed
        meta = {
            "thread_id": parsed.thread_id,
            "error": parsed.error,
            "sandbox": self.sandbox,
            "approval_policy": "never",
            "ephemeral": self.ephemeral,
        }
        if proc.stderr:
            meta["stderr_tail"] = proc.stderr[-2000:]
        return ExecutionResult(
            node_id=node.id,
            ok=ok,
            exit_code=proc.returncode,
            text=parsed.text,
            events=parsed.events,
            usage=parsed.usage,
            model=resolved_model,
            meta={k: v for k, v in meta.items() if v not in (None, "")},
        )


@dataclass
class _CodexResult:
    events: list[dict] = field(default_factory=list)
    text: str = ""
    usage: dict = field(default_factory=dict)
    thread_id: str | None = None
    completed: bool = False
    failed: bool = False
    error: str | None = None


def _parse_jsonl(stdout: str) -> _CodexResult:
    """Parse Codex ``exec --json`` events into normalized terminal state."""
    result = _CodexResult()
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        result.events.append(ev)
        event_type = ev.get("type")
        if event_type == "thread.started":
            result.thread_id = ev.get("thread_id", result.thread_id)
        elif event_type == "turn.completed":
            result.completed = True
            result.usage = ev.get("usage", result.usage) or result.usage
        elif event_type == "turn.failed":
            result.failed = True
            result.error = (ev.get("error") or {}).get("message", result.error)
        elif event_type == "error":
            result.failed = True
            result.error = ev.get("message", result.error)
        item = ev.get("item") or {}
        if item.get("type") in ("agent_message", "assistant_message"):
            result.text = item.get("text", result.text) or result.text
    return result


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""
