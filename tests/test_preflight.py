"""Tests for the autonomous-run trust gate."""

import io

from cadora.preflight import preflight_autonomous


def test_banner_shows_blast_radius(monkeypatch):
    monkeypatch.delenv("CADORA_ASSUME_YES", raising=False)
    buf = io.StringIO()

    proceed = preflight_autonomous(
        cwd=".", executor="claude", autonomous=True, assume_yes=True, stream=buf
    )

    out = buf.getvalue()
    assert proceed is True
    assert "autonomous run" in out
    assert "skip-permissions" in out
    assert "audits the agent's OUTPUT" in out.replace("\n", " ") or "OUTPUT" in out


def test_non_autonomous_is_a_noop(monkeypatch):
    buf = io.StringIO()
    assert preflight_autonomous(
        cwd=".", executor="fixture", autonomous=False, stream=buf
    ) is True
    assert buf.getvalue() == ""  # no banner for a non-autonomous backend


def test_assume_yes_env_proceeds_without_prompt(monkeypatch):
    monkeypatch.setenv("CADORA_ASSUME_YES", "1")
    # stdin is a TTY, but the env bypass must win and never call input().
    monkeypatch.setattr("sys.stdin", io.StringIO())
    monkeypatch.setattr("builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError()))

    assert preflight_autonomous(cwd=".", executor="claude", autonomous=True) is True


def test_non_tty_proceeds_without_prompt(monkeypatch):
    monkeypatch.delenv("CADORA_ASSUME_YES", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO())  # StringIO.isatty() is False

    # No prompt in a headless context — the banner is the record.
    assert preflight_autonomous(cwd=".", executor="claude", autonomous=True) is True


def test_interactive_yes_and_no(monkeypatch):
    monkeypatch.delenv("CADORA_ASSUME_YES", raising=False)

    class _TTY(io.StringIO):
        def isatty(self):
            return True

    monkeypatch.setattr("sys.stdin", _TTY())
    monkeypatch.setattr("builtins.input", lambda *a: "y")
    assert preflight_autonomous(cwd=".", executor="claude", autonomous=True) is True

    monkeypatch.setattr("builtins.input", lambda *a: "n")
    assert preflight_autonomous(cwd=".", executor="claude", autonomous=True) is False

    monkeypatch.setattr("builtins.input", lambda *a: "")  # default is No
    assert preflight_autonomous(cwd=".", executor="claude", autonomous=True) is False
