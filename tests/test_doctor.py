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
    assert [c.backend for c in checks] == [
        "python", "claude", "codex", "kiro", "glm", "antigravity", "bun"
    ]


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


def test_support_tiers_cover_every_registered_backend():
    from cadora.doctor import SUPPORT
    from cadora.executors import _REGISTRY

    backends = set(_REGISTRY) - {"fixture"}  # fixture is test-only, intentionally untiered
    assert backends == set(SUPPORT), f"tier map out of sync with registry: {backends ^ set(SUPPORT)}"
    assert set(SUPPORT.values()) <= {"verified", "experimental"}


def test_verified_and_experimental_split():
    from cadora.doctor import SUPPORT, _TESTED

    verified = {b for b, t in SUPPORT.items() if t == "verified"}
    experimental = {b for b, t in SUPPORT.items() if t == "experimental"}
    assert verified == {"claude", "codex", "kiro"}
    assert experimental == {"glm", "antigravity"}
    # every verified backend carries a tested version-range floor
    assert all(_TESTED.get(b, (None, None))[0] for b in verified)


def test_backendcheck_auto_populates_tier():
    assert BackendCheck("claude", "claude", "ok").tier == "verified"
    assert BackendCheck("antigravity", "agy", "missing").tier == "experimental"
    assert BackendCheck("python", "py", "ok").tier == ""  # non-backend checks carry no tier


def test_run_doctor_includes_antigravity(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda _b: None)  # all missing → fast, deterministic
    backends = {c.backend for c in run_doctor()}
    assert {"claude", "codex", "kiro", "glm", "antigravity"} <= backends
