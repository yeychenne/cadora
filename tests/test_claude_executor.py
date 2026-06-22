"""Tests for ClaudeCodeExecutor — parsing, normalized ``ok``, and funding handling.

``subprocess`` is mocked; no real ``claude`` CLI is invoked. A captured-shape
stream-json fixture (tests/fixtures/claude_stream_success.jsonl, real values)
anchors the parser to ground truth.
"""

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from cadora.executors.claude_code import ClaudeCodeExecutor, _funding_source
from cadora.topology import Node

FIXTURE = Path(__file__).parent / "fixtures" / "claude_stream_success.jsonl"


def _completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["claude"], returncode, stdout=stdout, stderr="")


def _run(stdout: str, *, returncode: int = 0, node: Node | None = None, env=None, **kw):
    """Run the executor with subprocess.run mocked to yield ``stdout``."""
    ex = ClaudeCodeExecutor(**kw)
    node = node or Node(id="n1", tools=["Read"])
    with mock.patch(
        "cadora.executors.claude_code.subprocess.run",
        return_value=_completed(stdout, returncode),
    ) as m:
        result = ex.run(node, "do it", cwd=".", env=env)
    return result, m


def test_parses_real_success_stream():
    result, _ = _run(FIXTURE.read_text())
    assert result.ok is True
    assert result.text == "OK"
    assert result.cost_usd and result.cost_usd > 0
    assert result.model and "haiku" in result.model
    assert result.usage["output_tokens"] == 49
    assert result.meta["apiKeySource"] == "none"
    assert result.meta["funding_resolved"] == "subscription"


def test_command_flags_and_stdin():
    _, m = _run('{"type":"result","subtype":"success","result":"x","is_error":false}')
    cmd = m.call_args.args[0]
    assert "--verbose" in cmd  # stream-json requires it
    assert "stream-json" in cmd
    assert "--dangerously-skip-permissions" in cmd  # autonomous default
    assert "--allowedTools" in cmd  # node.tools=["Read"]
    assert m.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_not_autonomous_omits_skip_permissions():
    _, m = _run('{"type":"result","is_error":false}', autonomous=False)
    assert "--dangerously-skip-permissions" not in m.call_args.args[0]


def test_ok_false_on_is_error():
    result, _ = _run(
        '{"type":"result","subtype":"error_max_turns","result":"partial","is_error":true}'
    )
    assert result.ok is False


def test_ok_false_on_nonzero_exit_even_if_result_success():
    result, _ = _run(
        '{"type":"result","subtype":"success","result":"ok","is_error":false}', returncode=1
    )
    assert result.ok is False
    assert result.exit_code == 1


def test_malformed_lines_skipped():
    stdout = "\n".join(
        [
            "not json",
            '{"type":"system","subtype":"init","apiKeySource":"none","model":"claude-x"}',
            "{bad",
            '{"type":"result","subtype":"success","result":"hi","is_error":false,"total_cost_usd":0.5}',
        ]
    )
    result, _ = _run(stdout)
    assert result.text == "hi"
    assert result.cost_usd == 0.5
    assert result.model == "claude-x"  # no modelUsage -> falls back to init model


def test_subscription_drops_ambient_api_key():
    with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-stray"}, clear=False):
        _, m = _run('{"type":"result","is_error":false}', funding="subscription")
    assert "ANTHROPIC_API_KEY" not in m.call_args.kwargs["env"]


def test_explicit_env_overlay_opts_into_metering():
    with mock.patch.dict("os.environ", {}, clear=False):
        _, m = _run(
            '{"type":"result","is_error":false}',
            funding="subscription",
            env={"ANTHROPIC_API_KEY": "sk-explicit"},
        )
    assert m.call_args.kwargs["env"]["ANTHROPIC_API_KEY"] == "sk-explicit"


def test_api_mode_keeps_ambient_key():
    with mock.patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-metered"}, clear=False):
        _, m = _run('{"type":"result","is_error":false}', funding="api")
    assert m.call_args.kwargs["env"]["ANTHROPIC_API_KEY"] == "sk-metered"


def test_funding_source_mapping():
    assert _funding_source("none") == "subscription"
    assert _funding_source(None) == "subscription"
    assert _funding_source("ANTHROPIC_API_KEY") == "metered"


def test_invalid_funding_rejected():
    with pytest.raises(ValueError):
        ClaudeCodeExecutor(funding="bogus")


def test_model_reports_primary_not_first_key():
    # a trivial haiku aux call alongside the real sonnet work -> report sonnet
    stream = (
        '{"type":"result","subtype":"success","result":"ok","is_error":false,'
        '"modelUsage":{"claude-haiku-4-5":{"costUSD":0.0007,"outputTokens":18},'
        '"claude-sonnet-4-6":{"costUSD":3.04,"outputTokens":68000}}}'
    )
    result, _ = _run(stream)
    assert result.model == "claude-sonnet-4-6"
