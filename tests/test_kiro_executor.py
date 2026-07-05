"""Tests for the Kiro CLI executor and output normalization."""

import subprocess
from unittest import mock

from cadora.executors.kiro import KiroExecutor, _parse_stderr, _strip_ansi
from cadora.topology import Node

# Real output captured from kiro-cli 2.10.0 (2026-07-04)
# stdout: ANSI-colored prompt prefix + response text
KIRO_STDOUT = "\x1b[38;5;141m> \x1b[0mHELLO_KIRO_TEST\x1b[0m\x1b[0m\n"
# stderr: trust-all banner + checkpoint status + credits/time
KIRO_STDERR = (
    "\x1b[32mAll tools are now trusted (\x1b[0m\x1b[31m!\x1b[0m\x1b[32m)."
    " Kiro will execute tools without asking for confirmation.\x1b[0m\n"
    "Agents can sometimes do unexpected things so understand the risks.\n\n"
    "Learn more at \x1b[38;5;141mhttps://kiro.dev/docs/cli/chat/security/"
    "#using-tools-trust-all-safely\x1b[0m\n\n\n"
    "\x1b[38;5;12mCheckpoints are not available in this directory.\n\x1b[39m\n"
    "\x1b[38;5;252m\x1b[0m\x1b[?25l\x1b[0m\x1b[0m\n"
    "\x1b[38;5;8m\n \u25b8 Credits: 0.06 \u2022 Time: 2s\n\n\x1b[0m\n"
)


def _completed(stdout: str = KIRO_STDOUT, returncode: int = 0, stderr: str = KIRO_STDERR):
    return subprocess.CompletedProcess(["kiro-cli"], returncode, stdout=stdout, stderr=stderr)


def test_strip_ansi_removes_escape_codes():
    assert _strip_ansi(KIRO_STDOUT) == "HELLO_KIRO_TEST"


def test_parse_stderr_extracts_credits_and_time():
    meta = _parse_stderr(KIRO_STDERR)
    assert meta["credits"] == 0.06
    assert meta["duration_seconds"] == 2


def test_success_run():
    with mock.patch(
        "cadora.executors.kiro.subprocess.run",
        return_value=_completed(),
    ):
        result = KiroExecutor().run(Node(id="n1"), "do it", cwd=".")
    assert result.ok is True
    assert result.text == "HELLO_KIRO_TEST"
    assert result.usage == {"credits": 0.06}
    assert result.meta["duration_seconds"] == 2


def test_command_flags():
    with mock.patch(
        "cadora.executors.kiro.subprocess.run",
        return_value=_completed(),
    ) as run:
        KiroExecutor(effort="high").run(
            Node(id="n1", model="auto"), "do it", cwd="."
        )
    cmd = run.call_args.args[0]
    assert "--no-interactive" in cmd
    assert "--wrap" in cmd and "never" in cmd
    assert "--trust-all-tools" in cmd
    assert "--model" in cmd and "auto" in cmd
    assert "--effort" in cmd and "high" in cmd
    assert run.call_args.kwargs["stdin"] is subprocess.DEVNULL


def test_timeout_captured():
    timeout = subprocess.TimeoutExpired(
        ["kiro-cli"], 10, output="\x1b[38;5;141m> \x1b[0mpartial\x1b[0m"
    )
    with mock.patch("cadora.executors.kiro.subprocess.run", side_effect=timeout):
        result = KiroExecutor(timeout=10).run(Node(id="n1"), "do it", cwd=".")
    assert result.ok is False
    assert result.exit_code == 124
    assert result.meta["timed_out"] is True
    assert "partial" in result.text


def test_nonzero_exit_is_failure():
    with mock.patch(
        "cadora.executors.kiro.subprocess.run",
        return_value=_completed(returncode=1),
    ):
        result = KiroExecutor().run(Node(id="n1"), "do it", cwd=".")
    assert result.ok is False
    assert result.exit_code == 1


def test_funding_label():
    """KiroExecutor exposes a funding label for the runner/archive layer."""
    assert KiroExecutor().funding == "kiro/credits"
