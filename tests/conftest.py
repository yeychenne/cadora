"""Session-wide guard against a silent MCP/HITL false-green.

``tests/test_mcp_server.py`` and ``tests/test_hitl_mcp_hardening.py`` are ``importorskip("mcp")``-gated
on the optional ``mcp`` extra. Skipping them in a deliberately-minimal local env is fine; skipping
them *silently* while still reporting a green count is not — the MCP review surface is a headline
feature, and a suite that quietly didn't exercise it is exactly the false-green this project fights.

So when ``mcp`` is absent this makes the skip impossible to miss — a header line at the top of the run
and a red separator at the end, next to the pass/skip count — and turns it into a hard error in any
environment that is supposed to run the full suite (``CI``, or an explicit ``CADORA_REQUIRE_MCP=1``),
where a silent skip would mean the feature shipped untested. When ``mcp`` is present, every hook here
is a no-op, so the normal path is untouched.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

_MCP_PRESENT = importlib.util.find_spec("mcp") is not None
# Somewhere the full suite MUST run: a missing extra here is a misconfiguration, not a local choice.
_MCP_REQUIRED = bool(os.environ.get("CI") or os.environ.get("CADORA_REQUIRE_MCP"))

_SKIP_NOTE = (
    "mcp extra NOT installed — the MCP/HITL tests (test_mcp_server.py, test_hitl_mcp_hardening.py) "
    "are SKIPPED. Install the full test env with `pip install -e '.[dev]'` and run via that "
    "interpreter (e.g. .venv/bin/python -m pytest)."
)


def pytest_configure(config: pytest.Config) -> None:
    if _MCP_PRESENT or not _MCP_REQUIRED:
        return
    raise pytest.UsageError(
        "the 'mcp' extra is required in this environment (CI or CADORA_REQUIRE_MCP is set) but is "
        "not installed — the MCP/HITL suite would be skipped, so the run is aborted rather than "
        "reporting a false green. Fix: `pip install -e '.[dev]'`."
    )


def pytest_report_header(config: pytest.Config):
    if _MCP_PRESENT:
        return None
    return f"⚠ {_SKIP_NOTE}"


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:
    if _MCP_PRESENT:
        return
    terminalreporter.write_sep(
        "!", "MCP/HITL TESTS SKIPPED — mcp extra not installed", red=True, bold=True
    )
