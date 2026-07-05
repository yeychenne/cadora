"""Tests for the backend-CLI contract checks (cadora doctor)."""

import subprocess

from cadora import doctor
from cadora.doctor import BackendCheck, check_backend, check_glm, live_backends_ok, run_doctor


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


def test_missing_binary(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)

    check = check_backend("claude")

    assert check.status == "missing"
    assert "not on PATH" in check.detail


def test_ok_within_tested_range(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *a, **k: _Proc(stdout="2.1.180 (Claude Code)"),
    )

    check = check_backend("claude")

    assert check.status == "ok"
    assert check.version == "2.1.180"


def test_untested_below_minimum(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/local/bin/codex")
    monkeypatch.setattr(
        doctor.subprocess, "run", lambda *a, **k: _Proc(stdout="codex-cli 0.100.0")
    )

    check = check_backend("codex")

    assert check.status == "untested"
    assert "below tested minimum" in check.detail


def test_failing_probe_never_parses_a_version_from_the_trace(monkeypatch):
    # Live regression: a broken npm codex wrapper exits 1 with an ENOENT stack trace whose
    # line numbers (e.g. child_process:285:19) must not be mistaken for a version.
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/opt/homebrew/bin/codex")
    monkeypatch.setattr(
        doctor.subprocess,
        "run",
        lambda *a, **k: _Proc(
            stderr="Error: spawn .../codex ENOENT\n    at ChildProcess (node:internal/child_process:285:19)",
            returncode=1,
        ),
    )

    check = check_backend("codex")

    assert check.status == "unparsable"
    assert check.version is None
    assert "--version exited 1" in check.detail


def test_unparsable_version(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/local/bin/claude")
    monkeypatch.setattr(doctor.subprocess, "run", lambda *a, **k: _Proc(stdout="banana"))

    assert check_backend("claude").status == "unparsable"


def test_version_probe_failure_is_unparsable(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: "/usr/local/bin/claude")

    def _boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=15)

    monkeypatch.setattr(doctor.subprocess, "run", _boom)

    check = check_backend("claude")

    assert check.status == "unparsable"
    assert "--version failed" in check.detail


def test_run_doctor_reports_python_first(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _: None)

    checks = run_doctor()

    assert checks[0].backend == "python"
    assert checks[0].status == "ok"  # the test suite itself requires >=3.10
    assert [c.backend for c in checks] == ["python", "claude", "codex", "kiro", "glm", "bun"]


def test_glm_requires_zai_api_key_and_claude(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda binary: f"/usr/local/bin/{binary}")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)

    check = check_glm()

    assert check.backend == "glm"
    assert check.binary == "claude"
    assert check.status == "missing"
    assert "ZAI_API_KEY" in check.detail

    monkeypatch.setenv("ZAI_API_KEY", "zai-test-key")

    check = check_glm()

    assert check.backend == "glm"
    assert check.binary == "claude"
    assert check.status == "ok"


def test_live_backends_ok_counts_usable_only():
    checks = [
        BackendCheck("python", "py", "ok"),
        BackendCheck("claude", "claude", "missing"),
        BackendCheck("codex", "codex", "untested", "0.100.0"),
        BackendCheck("kiro", "kiro-cli", "ok", "2.10.0"),
        BackendCheck("glm", "claude", "ok"),
    ]
    assert live_backends_ok(checks) == 3  # untested counts as usable; missing does not
    checks[2] = BackendCheck("codex", "codex", "missing")
    checks[3] = BackendCheck("kiro", "kiro-cli", "missing")
    checks[4] = BackendCheck("glm", "claude", "missing")
    assert live_backends_ok(checks) == 0
