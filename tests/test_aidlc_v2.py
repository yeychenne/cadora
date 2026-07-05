"""Tests for the EXPERIMENTAL aidlc-v2 method pack: guarded install + ingestion."""

import json

import pytest

from cadora.aidlc_v2 import (
    INSTALL_RECORD,
    InstallError,
    find_intents,
    ingest_intent,
    install_v2,
)

_UPSTREAM_SETTINGS = {
    "model": "opus[1m]",
    "effortLevel": "xhigh",
    "env": {
        "AWS_AIDLC_DEFAULT_SCOPE": "workshop",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "AWS_REGION": "us-east-1",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "global.anthropic.claude-opus-4-8[1m]",
    },
    "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "bun x aidlc-stop.ts"}]}]},
    "permissions": {"allow": ["Bash(bun:*)"]},
}

_UPSTREAM_MCP = {
    "mcpServers": {
        "context7": {"type": "http", "url": "https://example.test/mcp"},
        "aws-pricing": {"command": "uvx", "args": ["x@latest"]},
    }
}


@pytest.fixture
def upstream(tmp_path):
    """A minimal fake aidlc-workflows checkout (dist/claude shape)."""
    dist = tmp_path / "upstream" / "dist" / "claude"
    (dist / ".claude" / "skills").mkdir(parents=True)
    (dist / ".claude" / "skills" / "aidlc.md").write_text("# aidlc skill")
    (dist / ".claude" / "settings.json").write_text(json.dumps(_UPSTREAM_SETTINGS))
    (dist / "aidlc" / "spaces" / "default").mkdir(parents=True)
    (dist / ".mcp.json").write_text(json.dumps(_UPSTREAM_MCP))
    return tmp_path / "upstream"


def test_install_strips_and_records(upstream, tmp_path):
    ws = tmp_path / "ws"

    record = install_v2(ws, source=upstream)

    settings = json.loads((ws / ".claude" / "settings.json").read_text())
    assert "model" not in settings and "effortLevel" not in settings
    assert "CLAUDE_CODE_USE_BEDROCK" not in settings.get("env", {})
    assert settings["env"] == {"AWS_AIDLC_DEFAULT_SCOPE": "workshop"}  # method key survives
    assert settings["hooks"]  # the engine itself is untouched
    assert not (ws / ".mcp.json").exists()  # remote MCP wiring is opt-in

    assert record["provider_pins_stripped"]["model"] == "opus[1m]"
    assert record["provider_pins_stripped"]["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert record["mcp_installed"] is False
    assert record["mcp_servers_available"] == ["aws-pricing", "context7"]
    on_disk = json.loads((ws / INSTALL_RECORD).read_text())
    assert on_disk["pack"] == "aidlc-v2" and on_disk["ref"] == record["ref"]


def test_install_keep_flags(upstream, tmp_path):
    ws = tmp_path / "ws"

    record = install_v2(ws, source=upstream, keep_provider_pins=True, keep_mcp=True)

    settings = json.loads((ws / ".claude" / "settings.json").read_text())
    assert settings["model"] == "opus[1m]"
    assert settings["env"]["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert (ws / ".mcp.json").exists()
    assert record["provider_pins_stripped"] == {}
    assert record["mcp_installed"] is True


def test_install_refuses_existing_without_force(upstream, tmp_path):
    ws = tmp_path / "ws"
    (ws / ".claude").mkdir(parents=True)

    with pytest.raises(InstallError, match="--force"):
        install_v2(ws, source=upstream)

    install_v2(ws, source=upstream, force=True)  # force path works
    assert (ws / ".claude" / "skills" / "aidlc.md").exists()


_STATE = """\
## Current Status
- **Lifecycle Phase**: INCEPTION
- **Current Stage**: practices-discovery
- **Next Stage**: requirements-analysis
- **Status**: Running

## Stage Progress
- [x] workspace-scaffold — EXECUTE
- [-] practices-discovery — EXECUTE
- [?] made-up-gate — EXECUTE
- [S] market-research — SKIP
- [ ] code-generation — EXECUTE
"""

_SHARD = """\
## Workflow Started
**Timestamp**: 2026-07-03T13:00:00Z
**Event**: WORKFLOW_STARTED
**Scope**: workshop

---

## Gate Approved
**Timestamp**: 2026-07-03T13:04:57Z
**Event**: GATE_APPROVED
**Stage**: practices-discovery

---

## Human Turn
**Timestamp**: 2026-07-03T13:04:56Z
**Event**: HUMAN_TURN

---

## Sensor Fired
**Timestamp**: 2026-07-03T13:01:00Z
**Event**: SENSOR_FIRED
**Sensor**: aidlc-linter

## Not An Event
Just prose — no Event field, must be ignored.
"""


@pytest.fixture
def intent(tmp_path):
    intent_dir = tmp_path / "ws" / "aidlc" / "spaces" / "default" / "intents" / "260703-demo"
    (intent_dir / "audit").mkdir(parents=True)
    (intent_dir / "aidlc-state.md").write_text(_STATE)
    (intent_dir / "audit" / "host-abc.md").write_text(_SHARD)
    return intent_dir


def test_ingest_intent_state_and_events(intent):
    report = ingest_intent(intent)

    assert report["intent"] == "260703-demo" and report["space"] == "default"
    assert report["state"]["phase"] == "INCEPTION"
    assert report["state"]["stages"]["made-up-gate"] == "?"
    assert report["state"]["stage_rollup"] == {"x": 1, "-": 1, "?": 1, "S": 1, " ": 1}

    assert report["event_count"] == 4  # prose block without an Event field is ignored
    assert [e["event"] for e in report["events"]] == [
        "WORKFLOW_STARTED",  # sorted by timestamp, cross-checked below
        "SENSOR_FIRED",
        "HUMAN_TURN",
        "GATE_APPROVED",
    ]
    assert report["gates"][0]["stage"] == "practices-discovery"
    assert report["human_turns"] == 1
    assert report["sensors"] == {"fired": 1, "passed": 0, "failed": 0}


def test_find_intents_sorted(intent, tmp_path):
    ws = tmp_path / "ws"
    older = ws / "aidlc" / "spaces" / "default" / "intents" / "260101-old"
    (older / "audit").mkdir(parents=True)

    intents = find_intents(ws)

    assert [p.name for p in intents] == ["260101-old", "260703-demo"]


def test_aidlc_audit_cli_smoke(intent, tmp_path, capsys):
    from cadora.cli import main

    rc = main(["aidlc-audit", str(tmp_path / "ws")])

    out = capsys.readouterr().out
    assert rc == 0
    assert "260703-demo" in out
    assert "GATE_APPROVED" in out
    assert "awaiting-approval=1" in out
