"""Deterministic toolchain-integrity evaluator tests."""

import json

from cadora.integrity import scan_toolchain_integrity


def _rules(report):
    return {finding.rule for finding in report.findings}


def test_detects_repository_root_pytest_shadow(tmp_path):
    (tmp_path / "pytest").mkdir()
    (tmp_path / "pytest" / "__main__.py").write_text("print('fake')")
    report = scan_toolchain_integrity(tmp_path)
    assert report.passed is False
    assert "shadowed-toolchain" in _rules(report)


def test_detects_custom_typescript_build_substitution(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const x: number = 1")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "node scripts/build.mjs"}})
    )
    report = scan_toolchain_integrity(tmp_path)
    assert report.passed is False
    assert "typescript-build-substitution" in _rules(report)


def test_accepts_recognized_typescript_compiler(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const x: number = 1")
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"build": "tsc -p tsconfig.json"}})
    )
    assert scan_toolchain_integrity(tmp_path).passed is True


def test_detects_test_tool_from_another_temp_workspace(tmp_path):
    docs = tmp_path / "aidlc-docs" / "construction" / "build-and-test"
    docs.mkdir(parents=True)
    (docs / "build-and-test-summary.md").write_text(
        "Verified with `/tmp/other-project/.venv/bin/python -m pytest`."
    )
    report = scan_toolchain_integrity(tmp_path)
    assert report.passed is False
    assert "external-workspace-toolchain" in _rules(report)


def test_ignores_normal_virtualenv_contents(tmp_path):
    fake = tmp_path / ".venv" / "lib" / "python" / "site-packages" / "pytest"
    fake.mkdir(parents=True)
    assert scan_toolchain_integrity(tmp_path).passed is True


def test_cli_integrity_json_reports_blocking_finding(tmp_path, capsys):
    import cadora.cli as cli

    (tmp_path / "pytest").mkdir()
    rc = cli.main(["integrity", str(tmp_path), "--json"])
    report = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert report["passed"] is False
    assert report["findings"][0]["rule"] == "shadowed-toolchain"


def test_cli_integrity_clean_workspace(tmp_path, capsys):
    import cadora.cli as cli

    assert cli.main(["integrity", str(tmp_path)]) == 0
    assert "toolchain integrity ok" in capsys.readouterr().out
