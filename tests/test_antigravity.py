"""Tests for the agy transcript-fallback parser (cadora.executors.antigravity)."""

import json
import os
import time

from cadora.executors.antigravity import _read_transcript_fallback


def _write_transcript(brain: str, conv_id: str, entries: list[dict]) -> str:
    logs = os.path.join(brain, conv_id, ".system_generated", "logs")
    os.makedirs(logs)
    path = os.path.join(logs, "transcript_full.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")
    return path


def test_extracts_planner_responses(tmp_path):
    brain = str(tmp_path / "brain")
    _write_transcript(brain, "conv1", [
        {"type": "USER", "content": "build it"},
        {"type": "PLANNER_RESPONSE", "content": "step one"},
        {"type": "TOOL_CALL", "content": "ignored"},
        {"type": "PLANNER_RESPONSE", "content": "done"},
    ])
    assert _read_transcript_fallback(str(tmp_path), brain_dirs=[brain]) == "step one\ndone"


def test_since_excludes_stale_and_picks_fresh(tmp_path):
    """The run-scoping fix: a stale/other conversation is never returned."""
    brain = str(tmp_path / "brain")
    stale = _write_transcript(brain, "old", [{"type": "PLANNER_RESPONSE", "content": "stale"}])
    past = time.time() - 3600
    os.utime(stale, (past, past))

    cutoff = time.time()
    # Nothing modified since this run started -> the stale conversation is NOT returned.
    assert _read_transcript_fallback(str(tmp_path), since=cutoff, brain_dirs=[brain]) == ""

    # A conversation written during this run IS returned.
    _write_transcript(brain, "new", [{"type": "PLANNER_RESPONSE", "content": "fresh"}])
    assert _read_transcript_fallback(str(tmp_path), since=cutoff, brain_dirs=[brain]) == "fresh"


def test_missing_brain_dir_returns_empty(tmp_path):
    assert _read_transcript_fallback(str(tmp_path), brain_dirs=[str(tmp_path / "nope")]) == ""


def test_no_planner_responses_returns_empty(tmp_path):
    brain = str(tmp_path / "brain")
    _write_transcript(brain, "conv1", [{"type": "USER", "content": "hi"}])
    assert _read_transcript_fallback(str(tmp_path), brain_dirs=[brain]) == ""


def test_prefers_transcript_full_over_transcript(tmp_path):
    brain = str(tmp_path / "brain")
    logs = os.path.join(brain, "conv1", ".system_generated", "logs")
    os.makedirs(logs)
    with open(os.path.join(logs, "transcript_full.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "from full"}) + "\n")
    with open(os.path.join(logs, "transcript.jsonl"), "w", encoding="utf-8") as f:
        f.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "from short"}) + "\n")
    assert _read_transcript_fallback(str(tmp_path), brain_dirs=[brain]) == "from full"
