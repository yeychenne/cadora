"""Tests for usage aggregation over run manifests."""

import json

from cadora.archive import RunArchive
from cadora.executors.base import ExecutionResult
from cadora.usage import normalize_manifest_usage, summarize_usage


def _archive(
    root,
    run_id="run-20260626-090000",
    *,
    cost=0.25,
    model="claude-sonnet-4-6",
    funding="subscription",
):
    ar = RunArchive(root, run_id, "claude", "aidlc")
    ar.record(
        ExecutionResult(
            node_id="requirements",
            ok=True,
            exit_code=0,
            usage={
                "input_tokens": 10,
                "output_tokens": 20,
                "cache_creation_input_tokens": 30,
                "cache_read_input_tokens": 40,
            },
            cost_usd=cost,
            model=model,
            meta={"funding_resolved": funding},
        )
    )
    ar.finalize(True)


def test_normalize_manifest_usage_claude_cache_tokens(tmp_path):
    _archive(tmp_path)
    manifest = json.loads((tmp_path / "run-20260626-090000" / "manifest.json").read_text())

    node = normalize_manifest_usage(manifest)[0]

    assert node.input_tokens == 10
    assert node.output_tokens == 20
    assert node.generation_tokens == 30
    assert node.context_tokens == 100
    assert node.cost_usd == 0.25
    assert node.funding == "subscription"


def test_summarize_usage_groups_by_model_and_executor(tmp_path):
    _archive(tmp_path)

    summary = summarize_usage(tmp_path)

    assert summary.run_count == 1
    assert summary.node_count == 1
    assert summary.generation_tokens == 30
    assert summary.context_tokens == 100
    assert summary.cost_usd == 0.25
    assert summary.by_model[0]["model"] == "claude-sonnet-4-6"
    assert summary.by_executor[0]["executor"] == "claude"
    assert summary.by_funding[0]["funding"] == "subscription"
    assert summary.by_day[0]["day"] == "2026-06-26"


def test_summarize_usage_by_funding_and_by_day(tmp_path):
    _archive(tmp_path, "run-20260626-090000", cost=0.25, funding="subscription")
    _archive(tmp_path, "run-20260627-101500", cost=0.75, funding="api")

    summary = summarize_usage(tmp_path)

    assert summary.run_count == 2
    fundings = {row["funding"]: row["cost_usd"] for row in summary.by_funding}
    assert fundings == {"subscription": 0.25, "api": 0.75}

    days = {row["day"]: row for row in summary.by_day}
    assert set(days) == {"2026-06-26", "2026-06-27"}
    assert days["2026-06-27"]["cost_usd"] == 0.75
    assert days["2026-06-27"]["run_count"] == 1
    # by_day is sorted ascending by date
    assert [row["day"] for row in summary.by_day] == ["2026-06-26", "2026-06-27"]


def test_per_node_executor_and_codex_cached_tokens():
    # Phase routing runs construction nodes on a second backend; usage must attribute each
    # node to its own executor (not the run-level one) and count Codex's cached prompt tokens.
    manifest = {
        "run_id": "run-20260701-000000",
        "executor": "claude",
        "nodes": [
            {
                "node_id": "design",
                "executor": "claude",
                "model": "claude-opus-4-8",
                "usage": {"input_tokens": 100, "output_tokens": 50},
                "cost_usd": 0.5,
            },
            {
                "node_id": "code",
                "executor": "codex",
                "model": "gpt-5.5",
                "usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 200},
            },
        ],
    }
    by_id = {n.node_id: n for n in normalize_manifest_usage(manifest)}

    assert by_id["design"].executor == "claude"
    assert by_id["code"].executor == "codex"  # per-node, not the run-level "claude"
    # 1000 + 200 generation + 800 Codex cached_input_tokens
    assert by_id["code"].context_tokens == 2000


def test_by_executor_splits_across_backends(tmp_path):
    run_dir = tmp_path / "run-20260701-010101"
    run_dir.mkdir()
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": "run-20260701-010101",
                "executor": "claude",
                "nodes": [
                    {
                        "node_id": "design",
                        "executor": "claude",
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                        "cost_usd": 0.25,
                    },
                    {
                        "node_id": "code",
                        "executor": "codex",
                        "model": "gpt-5.5",
                        "usage": {"input_tokens": 100, "output_tokens": 20},
                    },
                ],
            }
        )
    )

    summary = summarize_usage(tmp_path)

    execs = {row["executor"]: row for row in summary.by_executor}
    assert set(execs) == {"claude", "codex"}
    assert execs["claude"]["cost_usd"] == 0.25
    assert execs["codex"]["node_count"] == 1


def test_cli_usage_json(tmp_path, capsys):
    import cadora.cli as cli

    _archive(tmp_path)
    rc = cli.main(["usage", "--archive-dir", str(tmp_path), "--json"])

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["context_tokens"] == 100
    assert payload["nodes"][0]["node_id"] == "requirements"
