"""Tests for AI-DLC workspace setup (rule vendoring + install)."""

from cadora.workspace import (
    rules_version,
    setup_aidlc_workspace,
    workspace_instruction_file,
)


def test_installs_rules_and_inline_vision(tmp_path):
    ws = setup_aidlc_workspace(tmp_path / "proj", vision="Build a todo API.")
    assert (ws / "CLAUDE.md").is_file()
    assert (ws / ".aidlc-rule-details").is_dir()
    # a known rule-detail file lands at the path the core workflow expects
    assert (ws / ".aidlc-rule-details" / "common" / "process-overview.md").is_file()
    assert (ws / "vision.md").read_text() == "Build a todo API."


def test_claude_md_is_the_core_workflow(tmp_path):
    ws = setup_aidlc_workspace(tmp_path / "proj")
    text = (ws / "CLAUDE.md").read_text()
    assert "INCEPTION PHASE" in text and "CONSTRUCTION PHASE" in text
    assert "AllowAdminCreateUserOnly=True" in text
    assert "CreateServiceSpecificCredential" in text


def test_codex_installs_agents_md_with_security_baseline(tmp_path):
    ws = setup_aidlc_workspace(tmp_path / "proj", executor="codex")
    instructions = (ws / "AGENTS.md").read_text()
    assert "INCEPTION PHASE" in instructions
    assert "AllowAdminCreateUserOnly=True" in instructions
    assert "long-term Amazon Bedrock API keys" in instructions
    assert not (ws / "CLAUDE.md").exists()
    assert workspace_instruction_file("codex") == "AGENTS.md"


def test_codex_preserves_existing_agents_instructions_on_refresh(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "AGENTS.md").write_text("# Team rules\n\nKeep this line.\n")
    setup_aidlc_workspace(ws, executor="codex")
    setup_aidlc_workspace(ws, executor="codex")
    instructions = (ws / "AGENTS.md").read_text()
    assert instructions.count("<!-- cadora:aidlc:start -->") == 1
    assert instructions.count("Keep this line.") == 1


def test_vision_from_file_is_copied(tmp_path):
    vf = tmp_path / "v.md"
    vf.write_text("# Vision\nA calculator.")
    ws = setup_aidlc_workspace(tmp_path / "proj", vision=vf)
    assert (ws / "vision.md").read_text() == "# Vision\nA calculator."


def test_rerun_replaces_rule_details(tmp_path):
    ws = setup_aidlc_workspace(tmp_path / "proj")
    stray = ws / ".aidlc-rule-details" / "stray.md"
    stray.write_text("x")
    setup_aidlc_workspace(tmp_path / "proj")  # rerun
    assert not stray.exists()  # the rule-details tree is fully replaced


def test_tech_env_is_optional(tmp_path):
    ws = setup_aidlc_workspace(tmp_path / "proj", vision="v")
    assert not (ws / "tech-env.md").exists()
    ws2 = setup_aidlc_workspace(tmp_path / "proj2", vision="v", tech_env="Python 3.12")
    assert (ws2 / "tech-env.md").read_text() == "Python 3.12"


def test_rules_version_is_pinned():
    assert rules_version() != "unknown"
