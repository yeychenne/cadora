"""Claude Code backend — the default executor.

Drives ``claude -p`` in headless mode with structured (``stream-json``) output,
which makes per-node capture clean.

Funding (subscription by default, never silently metered): Cadora prefers the
Claude Code subscription token (``CLAUDE_CODE_OAUTH_TOKEN`` / the stored login).
The catch is that a stray ``ANTHROPIC_API_KEY`` in the environment WINS the
credential chain and silently meters the run — so in the default
``funding="subscription"`` mode this executor drops an *ambient*
``ANTHROPIC_API_KEY`` from the subprocess environment. Metered API is explicit
opt-in: ``funding="api"``, or passing the key in the per-run ``env`` overlay.

Autonomous mode: AI-DLC runs edit files and run build/test commands, so a
headless session must not stall on permission prompts. ``autonomous=True``
(default) passes ``--dangerously-skip-permissions``. This is the intended mode
for driving the AI-DLC workflow; it auto-approves tool use, so only point it at
workspaces you trust.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.topology import Node


class ClaudeCodeExecutor(NodeExecutor):
    name = "claude"

    def __init__(
        self,
        binary: str = "claude",
        timeout: int = 1800,
        funding: str = "subscription",  # "subscription" (preferred) | "api"
        autonomous: bool = True,  # --dangerously-skip-permissions; headless AI-DLC needs it
        model: str | None = None,  # default model for every node; a node's own `model` overrides it
    ):
        if funding not in ("subscription", "api"):
            raise ValueError(f"funding must be 'subscription' or 'api', got {funding!r}")
        self.binary = binary
        self.timeout = timeout
        self.funding = funding
        self.autonomous = autonomous
        self.model = model

    def _build_cmd(self, node: Node, prompt: str) -> list[str]:
        # stream-json REQUIRES --verbose under --print, else the CLI errors out.
        cmd = [self.binary, "-p", prompt, "--output-format", "stream-json", "--verbose"]
        if self.autonomous:
            cmd.append("--dangerously-skip-permissions")
        if node.tools:
            cmd += ["--allowedTools", ",".join(node.tools)]
        model = node.model or self.model  # a node's own model overrides the executor default
        if model:
            cmd += ["--model", model]
        return cmd

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        cmd = self._build_cmd(node, prompt)

        try:
            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=self._resolve_env(env),
                stdin=subprocess.DEVNULL,  # headless: never block waiting on stdin
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            # A hung node is the failure mode long agent runs hit most — it must land in
            # the archive as evidence, not escape the runner as an exception.
            partial = _parse_stream_json(_as_text(exc.stdout))
            meta = dict(partial.meta)
            meta.update(
                {
                    "timed_out": True,
                    "timeout_seconds": self.timeout,
                    "funding_requested": self.funding,
                    "funding_resolved": _funding_source(partial.api_key_source),
                }
            )
            return ExecutionResult(
                node_id=node.id,
                ok=False,
                exit_code=124,
                text=partial.text,
                events=partial.events,
                usage=partial.usage,
                cost_usd=partial.cost_usd,
                model=partial.model,
                meta=meta,
            )

        r = _parse_stream_json(proc.stdout)
        # Normalized success: the process exited 0 AND the run reported no error.
        # A node can exit 0 yet set is_error (e.g. max-turns), so exit code alone is not enough.
        ok = proc.returncode == 0 and not r.is_error
        meta = dict(r.meta)
        meta["funding_requested"] = self.funding
        meta["funding_resolved"] = _funding_source(r.api_key_source)
        return ExecutionResult(
            node_id=node.id,
            ok=ok,
            exit_code=proc.returncode,
            text=r.text,
            events=r.events,
            usage=r.usage,
            cost_usd=r.cost_usd,
            model=r.model,
            meta=meta,
        )

    def _resolve_env(self, env: dict | None) -> dict:
        """Build the subprocess environment, enforcing the funding preference.

        In ``subscription`` mode, drop any *ambient* ``ANTHROPIC_API_KEY`` so the
        subscription token pays — unless the caller explicitly opted into metering
        by passing the key in the per-run ``env`` overlay.
        """
        overlay = dict(env or {})
        proc_env = {**os.environ, **overlay}
        if self.funding == "subscription" and "ANTHROPIC_API_KEY" not in overlay:
            proc_env.pop("ANTHROPIC_API_KEY", None)
        return proc_env


def _as_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value or ""


def _funding_source(api_key_source: str | None) -> str:
    """Map Claude Code's ``apiKeySource`` to a funding label.

    ``"none"`` (no API key) means the run drew on the subscription / login token;
    any key name (e.g. ``"ANTHROPIC_API_KEY"``) means metered API billing.
    """
    if api_key_source in (None, "", "none"):
        return "subscription"
    return "metered"


@dataclass
class _Result:
    events: list[dict] = field(default_factory=list)
    text: str = ""
    usage: dict = field(default_factory=dict)
    cost_usd: float | None = None
    model: str | None = None
    is_error: bool = False
    api_key_source: str | None = None
    meta: dict = field(default_factory=dict)


def _parse_stream_json(stdout: str) -> _Result:
    """Parse Claude Code's newline-delimited JSON event stream.

    The authoritative final state is the ``result`` event (final text, usage,
    ``total_cost_usd``, ``is_error``). The ``system/init`` event carries the
    resolved model and ``apiKeySource`` (the funding signal). Non-JSON lines are
    skipped so partial or garbled output never crashes a run.
    """
    r = _Result()
    session_id = num_turns = stop_reason = subtype = None
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        r.events.append(ev)
        etype = ev.get("type")
        if etype == "system" and ev.get("subtype") == "init":
            r.api_key_source = ev.get("apiKeySource", r.api_key_source)
            r.model = ev.get("model", r.model)
        elif etype == "result":
            subtype = ev.get("subtype", subtype)
            r.text = ev.get("result", r.text) or r.text
            r.usage = ev.get("usage", r.usage) or r.usage
            if ev.get("total_cost_usd") is not None:
                r.cost_usd = ev.get("total_cost_usd")
            r.is_error = bool(ev.get("is_error", r.is_error))
            session_id = ev.get("session_id", session_id)
            num_turns = ev.get("num_turns", num_turns)
            stop_reason = ev.get("stop_reason", stop_reason)
            model_usage = ev.get("modelUsage") or {}
            if model_usage:  # primary model = the one that did the most work (by cost)
                r.model = max(
                    model_usage,
                    key=lambda k: (
                        model_usage[k].get("costUSD", 0),
                        model_usage[k].get("outputTokens", 0),
                    ),
                )
    r.meta = {
        k: v
        for k, v in {
            "session_id": session_id,
            "num_turns": num_turns,
            "stop_reason": stop_reason,
            "subtype": subtype,
            "apiKeySource": r.api_key_source,
        }.items()
        if v is not None
    }
    return r
