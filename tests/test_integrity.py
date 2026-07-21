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


# --- stub-implementation (hollow-code) detection ------------------------------------------

def _write(tmp_path, name, src):
    (tmp_path / name).write_text(src)


def test_stub_implementations_block_when_code_is_hollow(tmp_path):
    _write(tmp_path, "app.py",
           "def compute():\n    raise NotImplementedError\n\n"
           "def render():\n    pass\n\n"
           "def save():\n    ...\n")
    report = scan_toolchain_integrity(tmp_path)
    assert "stub-implementation" in _rules(report)
    assert report.passed is False  # blocking
    f = next(x for x in report.findings if x.rule == "stub-implementation")
    assert "3 function" in f.detail and "compute()" in f.evidence


def test_real_code_is_not_flagged(tmp_path):
    _write(tmp_path, "app.py",
           "def add(a, b):\n    return a + b\n\n"
           "def greet(name):\n    return f'hi {name}'\n")
    assert "stub-implementation" not in _rules(scan_toolchain_integrity(tmp_path))


def test_single_placeholder_is_below_threshold(tmp_path):
    _write(tmp_path, "app.py",
           "def real(a):\n    return a * 2\n\n"
           "def later():\n    pass  # one placeholder is normal\n")
    assert "stub-implementation" not in _rules(scan_toolchain_integrity(tmp_path))


def test_abstract_and_protocol_stubs_are_legitimate(tmp_path):
    _write(tmp_path, "iface.py",
           "from abc import ABC, abstractmethod\n"
           "from typing import Protocol\n\n"
           "class Base(ABC):\n"
           "    @abstractmethod\n    def a(self): ...\n"
           "    @abstractmethod\n    def b(self): raise NotImplementedError\n\n"
           "class P(Protocol):\n    def c(self): ...\n    def d(self): ...\n")
    # 4 stub bodies, but all abstract/Protocol → not hollow, no finding.
    assert "stub-implementation" not in _rules(scan_toolchain_integrity(tmp_path))


def test_stub_finding_engages_remediation_under_enforce(tmp_path):
    from cadora.gates import GateResult
    from cadora.remediation import RemediationPolicy, needs_remediation

    _write(tmp_path, "app.py", "def a():\n    pass\n\ndef b():\n    ...\n")
    integrity = scan_toolchain_integrity(tmp_path)
    assert integrity.passed is False
    gate_ok = GateResult(name="build-test", passed=True)  # tests pass over the stubs
    policy = RemediationPolicy(max_attempts=2)
    # The hollow-code finding must trigger the loop even though the gate is green.
    assert needs_remediation(gate_ok, integrity, "enforce", policy) is True


def test_gate_venv_is_not_flagged_as_stub_implementation(tmp_path):
    """A gate-created venv (any name) must never trip the stub scan.

    Live false positive, twice: a --gate-cmd built its env as `.gatevenv`, and pytest's own
    vendored internals (full of legitimate `...`/pass bodies) were reported as a BLOCKING
    stub-implementation finding on two otherwise-clean runs. A venv is detected structurally
    (pyvenv.cfg), not by name.
    """
    # a real, non-stub app at the workspace root
    (tmp_path / "app.py").write_text("def real():\n    return 1 + 1\n")
    # a custom-named venv whose site-packages carry many stub bodies (pytest-internals shaped)
    sp = tmp_path / ".gatevenv" / "lib" / "python3.14" / "site-packages" / "_pytest"
    sp.mkdir(parents=True)
    (tmp_path / ".gatevenv" / "pyvenv.cfg").write_text("home = /usr/bin\n")
    (sp / "_argcomplete.py").write_text(
        "def a():\n    ...\n\ndef b():\n    pass\n\ndef c():\n    raise NotImplementedError\n"
    )
    report = scan_toolchain_integrity(tmp_path)
    assert report.passed, [f.path for f in report.findings]
    assert not any(f.rule == "stub-implementation" for f in report.findings)


def test_stub_scan_still_fires_on_the_workspace_itself(tmp_path):
    """The venv exemption must not blunt the real check: hollow app code still blocks."""
    (tmp_path / "engine.py").write_text(
        "def adjudicate():\n    ...\n\ndef score():\n    pass\n\ndef resolve():\n    raise NotImplementedError\n"
    )
    report = scan_toolchain_integrity(tmp_path)
    assert not report.passed
    assert any(f.rule == "stub-implementation" for f in report.findings)


def test_nameless_env_layout_excluded_via_site_packages(tmp_path):
    """Env layouts without pyvenv.cfg (e.g. conda) are caught by the site-packages part."""
    (tmp_path / "app.py").write_text("def real():\n    return 2\n")
    sp = tmp_path / "envs" / "gate" / "lib" / "site-packages" / "vendored"
    sp.mkdir(parents=True)
    (sp / "mod.py").write_text("def a():\n    ...\n\ndef b():\n    pass\n")
    report = scan_toolchain_integrity(tmp_path)
    assert report.passed, [f.path for f in report.findings]
