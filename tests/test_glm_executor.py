"""Tests for the EXPERIMENTAL GLM backend (Z.ai behind the Claude Code CLI).

All offline: subprocess is mocked with claude-shaped stream-json. The two properties that
matter most are credential safety (no Anthropic credential may reach the Z.ai endpoint) and
honest cost (the CLI's Anthropic-table estimate is discarded; the usage layer prices GLM).
"""

import json
from unittest import mock

import pytest

from cadora.executors.glm import DEFAULT_GLM_MODEL, ZAI_ANTHROPIC_BASE_URL, GlmExecutor
from cadora.topology import Node

_STREAM = "\n".join(
    [
        json.dumps(
            {"type": "system", "subtype": "init", "apiKeySource": "ANTHROPIC_AUTH_TOKEN",
             "model": "glm-5.2"}
        ),
        json.dumps(
            {
                "type": "result",
                "result": "done",
                "is_error": False,
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cache_read_input_tokens": 500,
                },
                "total_cost_usd": 0.42,  # the CLI's bogus Anthropic-table estimate for GLM
            }
        ),
    ]
)


def _proc(stdout=_STREAM, returncode=0):
    proc = mock.Mock()
    proc.stdout = stdout
    proc.stderr = ""
    proc.returncode = returncode
    return proc


def _run(monkeypatch, *, env_overlay=None, node=None):
    monkeypatch.setenv("ZAI_API_KEY", "zai-test-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-ambient")  # must never reach the subprocess
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-ambient")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs["env"]
        return _proc()

    with mock.patch("cadora.executors.claude_code.subprocess.run", side_effect=fake_run):
        result = GlmExecutor().run(node or Node(id="n1"), "build it", cwd=".", env=env_overlay)
    return result, captured


def test_env_routes_to_zai_and_drops_anthropic_credentials(monkeypatch):
    result, captured = _run(monkeypatch)

    env = captured["env"]
    assert env["ANTHROPIC_BASE_URL"] == ZAI_ANTHROPIC_BASE_URL
    assert env["ANTHROPIC_AUTH_TOKEN"] == "zai-test-key"
    assert env["ANTHROPIC_MODEL"] == DEFAULT_GLM_MODEL
    assert "ANTHROPIC_API_KEY" not in env  # ambient Anthropic key must not reach Z.ai
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert result.ok is True


def test_no_model_flag_in_command(monkeypatch):
    _, captured = _run(monkeypatch)

    assert "--model" not in captured["cmd"]  # GLM routes via env aliases, not the CLI flag


def test_node_model_overrides_default(monkeypatch):
    result, captured = _run(monkeypatch, node=Node(id="n1", model="glm-5"))

    assert captured["env"]["ANTHROPIC_MODEL"] == "glm-5"
    assert result.model == "glm-5"


def test_bogus_cli_cost_is_discarded(monkeypatch):
    result, _ = _run(monkeypatch)

    assert result.cost_usd is None  # 0.42 from the stream must not survive
    assert result.meta["cost_source"] == "computed"
    assert result.meta["provider"] == "zai"
    assert result.meta["funding_resolved"] == "zai"
    assert result.usage["input_tokens"] == 1000  # tokens pass through for the price table


def test_missing_key_fails_fast(monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    with pytest.raises(SystemExit, match="ZAI_API_KEY"):
        GlmExecutor().run(Node(id="n1"), "x", cwd=".")


def test_glm_pricing_uses_anthropic_wire_semantics():
    from cadora.usage import estimate_cost_usd

    # Anthropic wire: input EXCLUDES cache reads — both bill additively.
    additive = estimate_cost_usd(
        "glm-5.2",
        input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
        output_tokens=0,
        cached_included_in_input=False,
    )
    assert additive == 1.40 + 0.26
    # Suffixed ids (1M-context alias) still price.
    assert estimate_cost_usd("glm-5.2[1m]", input_tokens=1_000_000) == 1.40


def test_glm_manifest_node_gets_estimated_cost(tmp_path):
    from cadora.archive import RunArchive
    from cadora.executors.base import ExecutionResult
    from cadora.usage import normalize_manifest_usage

    ar = RunArchive(tmp_path, "glm-run-001", "glm", "aidlc")
    node = ExecutionResult(
        node_id="code",
        ok=True,
        exit_code=0,
        usage={"input_tokens": 10_000, "output_tokens": 2_000,
               "cache_read_input_tokens": 4_000},
        cost_usd=None,
        model="glm-5.2",
        meta={"provider": "zai", "funding_resolved": "zai"},
    )
    node.executor = "glm"
    ar.record(node)
    ar.finalize(True)
    manifest = json.loads((tmp_path / "glm-run-001" / "manifest.json").read_text())

    normalized = normalize_manifest_usage(manifest)[0]

    # additive wire: 10k in @1.40 + 4k cached @0.26 + 2k out @4.40 (per MTok)
    expected = (10_000 * 1.40 + 4_000 * 0.26 + 2_000 * 4.40) / 1_000_000
    assert normalized.cost_usd == pytest.approx(expected)
    assert normalized.cost_estimated is True
    assert normalized.funding == "zai"


def test_model_override_still_never_passes_model_flag(monkeypatch):
    """Board finding: node.model must route via env aliases, never the --model flag."""
    _, captured = _run(monkeypatch, node=Node(id="n1", model="glm-5"))

    assert "--model" not in captured["cmd"]
    assert captured["env"]["ANTHROPIC_MODEL"] == "glm-5"
