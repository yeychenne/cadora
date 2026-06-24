"""Tests for the OpenAI Codex CLI executor and JSONL normalization."""

import subprocess
from unittest import mock

from cadora.executors.codex import CodexExecutor, _parse_jsonl
from cadora.topology import Node


SUCCESS_STREAM = "\n".join(
    [
        '{"type":"thread.started","thread_id":"thread-123"}',
        '{"type":"turn.started"}',
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}',
        (
            '{"type":"turn.completed","usage":{"input_tokens":20438,'
            '"cached_input_tokens":2432,"output_tokens":18,'
            '"reasoning_output_tokens":11}}'
        ),
    ]
)


def _completed(stdout: str, returncode: int = 0, stderr: str = ""):
    return subprocess.CompletedProcess(["codex"], returncode, stdout=stdout, stderr=stderr)


def test_parses_success_stream():
    result = _parse_jsonl(SUCCESS_STREAM)
    assert result.completed is True
    assert result.failed is False
    assert result.text == "OK"
    assert result.thread_id == "thread-123"
    assert result.usage["output_tokens"] == 18


def test_turn_failed_is_semantic_failure_even_on_zero_exit():
    stream = "\n".join(
        [
            '{"type":"thread.started","thread_id":"t"}',
            '{"type":"error","message":"model unavailable"}',
            '{"type":"turn.failed","error":{"message":"model unavailable"}}',
        ]
    )
    with mock.patch(
        "cadora.executors.codex.subprocess.run",
        return_value=_completed(stream, returncode=0),
    ):
        result = CodexExecutor().run(Node(id="n1"), "do it", cwd=".")
    assert result.ok is False
    assert result.meta["error"] == "model unavailable"


def test_command_matches_current_codex_exec_cli():
    with mock.patch(
        "cadora.executors.codex.subprocess.run",
        return_value=_completed(SUCCESS_STREAM),
    ) as run:
        result = CodexExecutor(model="gpt-5.4").run(Node(id="n1"), "do it", cwd=".")
    cmd = run.call_args.args[0]
    assert "--ask-for-approval" not in cmd
    assert 'approval_policy="never"' in cmd
    assert "--sandbox" in cmd and "workspace-write" in cmd
    assert "--ephemeral" in cmd
    assert "--ignore-user-config" in cmd
    assert "--model" in cmd and "gpt-5.4" in cmd
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL
    assert result.ok is True
    assert result.model == "gpt-5.4"


def test_node_model_overrides_executor_model():
    with mock.patch(
        "cadora.executors.codex.subprocess.run",
        return_value=_completed(SUCCESS_STREAM),
    ) as run:
        CodexExecutor(model="fallback").run(
            Node(id="n1", model="node-model"), "do it", cwd="."
        )
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("--model") + 1] == "node-model"


def test_timeout_is_captured_as_failed_result():
    timeout = subprocess.TimeoutExpired(
        ["codex"], 12, output='{"type":"thread.started","thread_id":"partial"}'
    )
    with mock.patch("cadora.executors.codex.subprocess.run", side_effect=timeout):
        result = CodexExecutor(timeout=12).run(Node(id="n1"), "do it", cwd=".")
    assert result.ok is False
    assert result.exit_code == 124
    assert result.meta["timed_out"] is True
    assert result.meta["thread_id"] == "partial"
