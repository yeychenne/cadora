"""Account health — present · credentialed · live · budget.

The layers answer failures observed live (2026-07): doctor said `claude ok` on an expired
token; `claude auth status` said loggedIn:true on that same expired token; only a live probe
told the truth; and quota exhaustion killed a node mid-flight because nothing watched a budget.
"""

import json

from cadora.accounts import (
    AccountHealth,
    accounts_to_dict,
    format_accounts,
    gather_accounts,
    parse_budgets,
)
from cadora.cli import main


class _Check:
    def __init__(self, status="ok", version="9.9.9", detail=""):
        self.status, self.version, self.detail = status, version, detail


def _fake_check(status="ok"):
    return lambda backend: _Check(status=status)


def _archive_with_codex_spend(tmp_path, cost_tokens=(1_000_000, 100_000)):
    """One archived codex run: tokens only (no dollars) -> priced from the rate table (est.)."""
    run = tmp_path / "runs" / "r1"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "r1",
                "executor": "codex",
                "topology": "t",
                "ok": True,
                "nodes": [
                    {
                        "node_id": "n1",
                        "ok": True,
                        "model": "gpt-5.5",
                        "cost_usd": None,
                        "usage": {
                            "input_tokens": cost_tokens[0],
                            "output_tokens": cost_tokens[1],
                        },
                    }
                ],
            }
        )
    )
    return tmp_path / "runs"


# --- parse_budgets -------------------------------------------------------------------------


def test_parse_budgets_happy_and_loud():
    assert parse_budgets(["claude=200", "codex=80.5"]) == {"claude": 200.0, "codex": 80.5}
    assert parse_budgets(None) == {}
    for bad in ["claude", "claude=", "=200", "claude=abc", "claude=-5", "claude=0"]:
        try:
            parse_budgets([bad])
        except SystemExit as exc:
            assert "invalid --budget" in str(exc)
        else:
            raise AssertionError(f"{bad!r} accepted")


# --- the budget layer: declared budget vs recorded consumption ------------------------------


def test_budget_fraction_and_threshold_flag(tmp_path):
    archive = _archive_with_codex_spend(tmp_path)  # ≈ $5 in + $3 out = $8 est.
    accounts = gather_accounts(
        ["codex"],
        archive_roots=[archive],
        budgets={"codex": 10.0},
        check_backend_fn=_fake_check(),
        credential_fns={"codex": lambda: "stored (chatgpt)"},
    )
    (codex,) = accounts
    assert codex.spent_usd == 8.0 and codex.spent_estimated  # rate-table priced, flagged
    assert codex.used_fraction == 0.8
    assert codex.flags == [] and codex.healthy  # 80% < the 90% default threshold

    (flagged,) = gather_accounts(
        ["codex"],
        archive_roots=[archive],
        budgets={"codex": 8.5},
        check_backend_fn=_fake_check(),
        credential_fns={"codex": lambda: "stored (chatgpt)"},
    )
    assert flagged.used_fraction and flagged.used_fraction >= 0.9
    assert flagged.flags and "budget" in flagged.flags[0]
    assert not flagged.healthy


def test_no_budget_means_no_fraction_and_no_flag(tmp_path):
    archive = _archive_with_codex_spend(tmp_path)
    (codex,) = gather_accounts(
        ["codex"],
        archive_roots=[archive],
        check_backend_fn=_fake_check(),
        credential_fns={"codex": lambda: "stored (chatgpt)"},
    )
    assert codex.used_fraction is None and codex.healthy


# --- the present + probe layers flag honestly ------------------------------------------------


def test_missing_cli_and_failed_probe_flag_unhealthy(tmp_path):
    (missing,) = gather_accounts(
        ["claude"],
        archive_roots=[tmp_path / "runs"],
        check_backend_fn=lambda b: _Check(status="missing", version=None, detail="'claude' not on PATH"),
        credential_fns={"claude": lambda: "none"},
    )
    assert not missing.healthy and "CLI missing" in missing.flags[0]

    (probed,) = gather_accounts(
        ["claude"],
        archive_roots=[tmp_path / "runs"],
        probe=True,
        check_backend_fn=_fake_check(),
        credential_fns={"claude": lambda: "stored (max)"},
        probe_fn=lambda b: "failed: 401 OAuth access token has expired",
    )
    assert not probed.healthy and any("probe" in f for f in probed.flags)


def test_probe_off_asserts_nothing(tmp_path):
    """The stored-is-not-valid lesson: without --probe, 'stored' must not read as healthy proof —
    but it also must not flag; the report says so in prose instead."""
    (a,) = gather_accounts(
        ["claude"],
        archive_roots=[tmp_path / "runs"],
        check_backend_fn=_fake_check(),
        credential_fns={"claude": lambda: "stored (max)"},
    )
    assert a.probe == "off" and a.healthy
    text = format_accounts([a], since=None, archives=["runs"])
    assert "stored" in text and "--probe" in text  # the caveat is printed


# --- serialization + CLI round trip ----------------------------------------------------------


def test_accounts_to_dict_carries_health():
    a = AccountHealth(backend="claude", present="ok")
    assert accounts_to_dict([a])[0]["healthy"] is True


def test_cli_accounts_json_and_check_exit(tmp_path, monkeypatch, capsys):
    archive = _archive_with_codex_spend(tmp_path)
    import cadora.accounts as accounts_mod

    monkeypatch.setattr(accounts_mod, "check_backend", lambda b: _Check())
    monkeypatch.setattr(accounts_mod, "_claude_credentials", lambda run=None: "stored (max)")
    monkeypatch.setattr(accounts_mod, "_codex_credentials", lambda home=None: "stored (chatgpt)")

    rc = main(
        ["accounts", "--archive-dir", str(archive), "--budget", "codex=10", "--json"]
    )
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    codex = next(a for a in out if a["backend"] == "codex")
    assert codex["spent_usd"] == 8.0 and codex["used_fraction"] == 0.8

    # --check turns a tripped threshold into a non-zero exit (the pre-run guard)
    rc = main(
        ["accounts", "--archive-dir", str(archive), "--budget", "codex=8.5", "--check"]
    )
    assert rc == 1
