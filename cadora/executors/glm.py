"""GLM backend (EXPERIMENTAL) — Zhipu GLM behind the Claude Code CLI.

Z.ai serves an Anthropic-compatible endpoint that officially drives Claude Code, so GLM rides
the existing `claude -p` stream-json contract: same parser, same archive, zero new wire format.
This executor is the guarded env plumbing around that fact:

- **Credential safety**: the ambient `ANTHROPIC_API_KEY` and `CLAUDE_CODE_OAUTH_TOKEN` are
  dropped from the subprocess env — a GLM run must never be able to bill Anthropic, and an
  Anthropic credential must never be sent to a third-party endpoint. Auth comes exclusively
  from the Z.ai key (`ZAI_API_KEY` by default), passed as `ANTHROPIC_AUTH_TOKEN`.
- **Honest cost**: Claude Code's `total_cost_usd` is a client-side estimate from a bundled
  Anthropic price table that does not know GLM — it is discarded (`cost_usd=None`,
  `meta.cost_source="computed"`) so the usage layer prices the run from the public Z.ai rate
  table instead (flagged as estimated, Anthropic-wire cache semantics).
- **Funding label**: GLM is metered Z.ai (or a GLM Coding Plan); either way it is not the
  Claude subscription, so the funding label is explicit.

Live-verification status: unit-tested against the stream-json contract; the live smoke
(scripts/live-smoke.sh glm) requires a `ZAI_API_KEY` and gates promotion out of EXPERIMENTAL.
"""

from __future__ import annotations

import dataclasses
import os

from cadora.executors.base import ExecutionResult
from cadora.executors.claude_code import ClaudeCodeExecutor
from cadora.topology import Node

ZAI_ANTHROPIC_BASE_URL = "https://api.z.ai/api/anthropic"
DEFAULT_GLM_MODEL = "glm-5.2"


class GlmExecutor(ClaudeCodeExecutor):
    name = "glm"
    funding = "zai"  # metered Z.ai key or GLM Coding Plan — never the Claude subscription

    def __init__(
        self,
        binary: str = "claude",
        timeout: int = 3600,  # GLM is output-verbose; give nodes more headroom than claude's 1800
        autonomous: bool = True,
        model: str | None = None,
        base_url: str = ZAI_ANTHROPIC_BASE_URL,
        api_key_env: str = "ZAI_API_KEY",
    ):
        super().__init__(
            binary=binary,
            timeout=timeout,
            funding="subscription",  # placates the parent's validator; overridden just below
            autonomous=autonomous,
            model=None,  # never pass --model: GLM ids route via env aliases, not the CLI flag
        )
        # The real funding label. The parent's subscription-mode key-drop no longer applies,
        # but our _resolve_env override drops Anthropic credentials unconditionally anyway.
        self.funding = "zai"
        self.glm_model = model or DEFAULT_GLM_MODEL
        self.base_url = base_url
        self.api_key_env = api_key_env

    def run(self, node: Node, prompt: str, *, cwd: str, env=None) -> ExecutionResult:
        api_key = (env or {}).get(self.api_key_env) or os.environ.get(self.api_key_env)
        if not api_key:
            raise SystemExit(
                f"glm executor: {self.api_key_env} is not set — export your Z.ai key "
                f"(metered API key or GLM Coding Plan token) as {self.api_key_env}"
            )
        model = node.model or self.glm_model
        overlay = {
            **(env or {}),
            "ANTHROPIC_BASE_URL": self.base_url,
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "API_TIMEOUT_MS": str(self.timeout * 1000),
        }
        # GLM ids route via env aliases ONLY — the parent adds --model whenever node.model is
        # set, which would hand a GLM id to the Claude CLI's model handling. Strip it.
        child_node = dataclasses.replace(node, model=None) if node.model else node
        result = super().run(child_node, prompt, cwd=cwd, env=overlay)

        # The CLI's cost estimate comes from its bundled Anthropic price table — meaningless
        # for GLM. Drop it so the usage layer computes from the Z.ai rate table (flagged est.).
        result.cost_usd = None
        result.model = model  # the stream may echo an alias; record what we routed to
        result.meta["provider"] = "zai"
        result.meta["cost_source"] = "computed"
        result.meta["funding_requested"] = self.funding
        result.meta["funding_resolved"] = self.funding
        return result

    def _resolve_env(self, env: dict | None) -> dict:
        proc_env = super()._resolve_env(env)
        # Belt over braces: no Anthropic credential may coexist with a third-party base URL —
        # ANTHROPIC_API_KEY outranks AUTH_TOKEN in the CLI's chain and would be SENT to Z.ai.
        proc_env.pop("ANTHROPIC_API_KEY", None)
        proc_env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
        return proc_env
