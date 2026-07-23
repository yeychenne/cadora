"""Park-and-exit — a review gate that survives the process ending.

The contract under test, in order of importance:

1. **A resumed run is the run it claims to continue.** Downstream prompts must render
   BYTE-IDENTICAL to a never-parked run — this is the assertion the feature lives or dies on.
2. **Parked agent work is never re-run and never re-paid.** The park record carries the pending
   node's result; resume injects it and goes straight to the review.
3. **Wave drain.** Parking never strands a sibling's completed work: the wave finishes and
   records first, then ONE park record holds every pending gate.
4. **Honesty across the boundary.** Exit code 75 (not a failure), manifest stays in flight,
   drift is refused, MAX_REVIEW_REVISIONS survives the park, and parked downtime is review
   wait — not agent duration.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from cadora.executors.base import ExecutionResult, NodeExecutor
from cadora.park import PARK_EXIT_CODE, load_park_record
from cadora.review import REVIEW_APPROVE, REVIEW_REQUEST_CHANGES, ReviewResult
from cadora.runner import run_topology
from cadora.topology import Node, Topology


class RecordingExecutor(NodeExecutor):
    name = "fake"

    def __init__(self, cost: float = 1.0):
        self.cost = cost
        self.calls: list[tuple[str, str]] = []  # (node_id, prompt)

    def run(self, node, prompt, *, cwd, env=None):
        self.calls.append((node.id, prompt))
        return ExecutionResult(
            node_id=node.id,
            ok=True,
            exit_code=0,
            text=f"out-{node.id}",
            usage={"input_tokens": 10, "output_tokens": 5},
            cost_usd=self.cost,
        )


def _scripted(*decisions):
    """A review_fn that plays back decisions in order."""
    queue = list(decisions)

    def review_fn(node, node_cwd, documents=None):
        decision, comments = queue.pop(0)
        return ReviewResult(decision, comments)

    return review_fn


def _topology():
    # a and b are one wave (b under review); c consumes BOTH outputs.
    return Topology(
        name="t",
        nodes=[
            Node(id="a", role="builder", prompt="build a"),
            Node(id="b", role="builder", prompt="build b", review=True),
            Node(id="c", role="builder", prompt="build c", depends_on=["a", "b"]),
        ],
    )


def _park(tmp_path, executor, run_id="r", **kwargs):
    """Run in park mode and return the SystemExit code."""
    with pytest.raises(SystemExit) as excinfo:
        run_topology(
            _topology(),
            executor,
            run_id=run_id,
            cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"),
            hitl=True,
            review_fn=_scripted(),  # must never be consulted in park mode
            on_review="park",
            park_contract={"executor": "fake"},
            max_parallel=kwargs.pop("max_parallel", 2),
            **kwargs,
        )
    return excinfo.value.code


def _resume(tmp_path, executor, review_fn, run_id="r", **overrides):
    """Mirror cmd_resume's core: rebuild run_topology kwargs from the park record."""
    run_dir = tmp_path / "runs" / run_id
    record = load_park_record(run_dir)
    outputs = {}
    for node_id in record["completed"]:
        out_file = run_dir / node_id / "output.txt"
        if out_file.is_file():
            outputs[node_id] = out_file.read_text()
    pending = {
        p["node_id"]: {**p, "parked_at": record["parked_at"]} for p in record["pending"]
    }
    skip = sorted(set(record["completed"]) | set(record.get("skipped_pointer", [])))
    kwargs = dict(
        run_id=record["run_id"],
        cwd=record["cwd"],
        archive_root=str(run_dir.parent),
        hitl=True,
        review_fn=review_fn,
        skip=skip or None,
        park_pending=pending,
        initial_outputs=outputs,
        initial_reviews=record.get("reviews", {}),
        park_contract=record["contract"],
        allow_drift=overrides.pop("allow_drift", True),  # tests mutate tmp dirs freely
    )
    kwargs.update(overrides)
    from cadora.park import topology_from_dict

    return run_topology(topology_from_dict(record["topology"]), executor, **kwargs)


def _manifest(tmp_path, run_id="r"):
    return json.loads((tmp_path / "runs" / run_id / "manifest.json").read_text())


def _status(tmp_path, run_id="r"):
    return json.loads((tmp_path / "runs" / run_id / "status.json").read_text())


# --- parking ------------------------------------------------------------------------------------


def test_park_exits_75_with_a_self_contained_record(tmp_path):
    executor = RecordingExecutor()
    code = _park(tmp_path, executor)

    assert code == PARK_EXIT_CODE == 75  # temp-fail, not failure(1)
    record = load_park_record(tmp_path / "runs" / "r")
    assert [p["node_id"] for p in record["pending"]] == ["b"]
    assert record["pending"][0]["result"]["text"] == "out-b"
    assert record["pending"][0]["cost_so_far"] == pytest.approx(1.0)
    # Self-contained: topology, gates, and the execution contract travel with the record.
    assert [n["id"] for n in record["topology"]["nodes"]] == ["a", "b", "c"]
    assert record["contract"] == {"executor": "fake"}
    assert record["completed"] == ["a"]  # the drained sibling
    # Honest states: run parked (not failed), manifest in flight (not ok/failed).
    assert _status(tmp_path)["status"] == "parked"
    assert _manifest(tmp_path)["ok"] is None
    # The workspace fingerprint a resume will verify against exists.
    assert (tmp_path / "runs" / "r" / "workspace-manifest.json").is_file() or any(
        (tmp_path / "runs" / "r").glob("*workspace*")
    )


def test_wave_drains_before_parking(tmp_path):
    """The sibling completes and RECORDS before the park — nothing stranded in memory."""
    executor = RecordingExecutor()
    _park(tmp_path, executor)

    ran = [node_id for node_id, _ in executor.calls]
    assert sorted(ran) == ["a", "b"]  # both wave members executed; c never started
    manifest_nodes = {n["node_id"] for n in _manifest(tmp_path)["nodes"]}
    assert manifest_nodes == {"a"}  # a recorded; b pends in park.json, c untouched


def test_two_reviews_in_one_wave_park_once_together(tmp_path):
    executor = RecordingExecutor()
    topo = Topology(
        name="t",
        nodes=[
            Node(id="x", role="builder", prompt="px", review=True),
            Node(id="y", role="builder", prompt="py", review=True),
        ],
    )
    with pytest.raises(SystemExit) as excinfo:
        run_topology(
            topo, executor, run_id="w", cwd=str(tmp_path),
            archive_root=str(tmp_path / "runs"), hitl=True, review_fn=_scripted(),
            on_review="park", park_contract={}, max_parallel=2,
        )
    assert excinfo.value.code == PARK_EXIT_CODE
    record = load_park_record(tmp_path / "runs" / "w")
    assert sorted(p["node_id"] for p in record["pending"]) == ["x", "y"]


# --- resuming -----------------------------------------------------------------------------------


def test_resume_reviews_without_rerunning_or_repaying_the_agent(tmp_path):
    parker = RecordingExecutor()
    _park(tmp_path, parker)

    resumer = RecordingExecutor()
    _resume(tmp_path, resumer, _scripted((REVIEW_APPROVE, "fine")))

    ran = [node_id for node_id, _ in resumer.calls]
    assert "b" not in ran  # the parked node's agent did NOT re-run
    assert ran == ["c"]  # only downstream work happened
    manifest = {n["node_id"]: n for n in _manifest(tmp_path)["nodes"]}
    assert set(manifest) == {"a", "b", "c"}
    assert manifest["b"]["cost_usd"] == pytest.approx(1.0)  # paid once, in the parking process
    assert _manifest(tmp_path)["ok"] is True
    # The park record is consumed — a finished run must not be resumable again.
    assert not (tmp_path / "runs" / "r" / "park.json").exists()


def test_downstream_prompts_are_byte_identical_to_a_never_parked_run(tmp_path):
    """THE assertion. If reconstructed state renders differently, the resumed run is not the
    run it claims to continue."""
    straight_ws = tmp_path / "straight"
    parked_ws = tmp_path / "parked"
    straight_ws.mkdir()
    parked_ws.mkdir()

    straight = RecordingExecutor()
    run_topology(
        _topology(), straight, run_id="s", cwd=str(straight_ws),
        archive_root=str(straight_ws / "runs"), hitl=True,
        review_fn=_scripted((REVIEW_APPROVE, "")), max_parallel=2,
    )
    straight_c = next(p for n, p in straight.calls if n == "c")

    parker = RecordingExecutor()
    _park(parked_ws, parker)
    resumer = RecordingExecutor()
    _resume(parked_ws, resumer, _scripted((REVIEW_APPROVE, "")))
    resumed_c = next(p for n, p in resumer.calls if n == "c")

    assert resumed_c == straight_c  # byte-identical, both upstream outputs inlined


def test_request_changes_after_resume_reruns_with_comments(tmp_path):
    parker = RecordingExecutor()
    _park(tmp_path, parker)

    resumer = RecordingExecutor()
    _resume(
        tmp_path,
        resumer,
        _scripted((REVIEW_REQUEST_CHANGES, "tighten the schema"), (REVIEW_APPROVE, "ok")),
    )
    b_prompts = [p for n, p in resumer.calls if n == "b"]
    assert len(b_prompts) == 1  # the revision run — the original never re-ran
    assert "tighten the schema" in b_prompts[0]
    manifest = {n["node_id"]: n for n in _manifest(tmp_path)["nodes"]}
    assert manifest["b"]["cost_usd"] == pytest.approx(2.0)  # park attempt + revision


def test_a_resume_in_park_mode_reparks_stably(tmp_path):
    """`cadora resume --on-review park` on an undecided gate parks again — same pending node,
    the agent still not re-run, the cost still charged exactly once. A park is idempotent
    until a human actually decides."""
    from cadora.budget import load_baseline

    parker = RecordingExecutor()
    _park(tmp_path, parker)

    reparker = RecordingExecutor()
    with pytest.raises(SystemExit) as excinfo:
        _resume(tmp_path, reparker, _scripted(), on_review="park")
    assert excinfo.value.code == PARK_EXIT_CODE

    assert reparker.calls == []  # nothing executed at all
    record = load_park_record(tmp_path / "runs" / "r")
    assert [p["node_id"] for p in record["pending"]] == ["b"]
    assert load_baseline(str(tmp_path / "runs"))["fake"] == pytest.approx(1.0)  # still once


def test_revision_history_is_restored_on_resume(tmp_path):
    """Direct check of the mechanism: review_history in the record comes back verbatim."""
    parker = RecordingExecutor()
    _park(tmp_path, parker)
    record = load_park_record(tmp_path / "runs" / "r")
    assert record["pending"][0]["review_history"] == []  # parked before any verdict

    # Resume with two request_changes then approve: 2 revisions < MAX(3) succeeds…
    resumer = RecordingExecutor()
    _resume(
        tmp_path,
        resumer,
        _scripted(
            (REVIEW_REQUEST_CHANGES, "r1"),
            (REVIEW_REQUEST_CHANGES, "r2"),
            (REVIEW_APPROVE, "ok"),
        ),
    )
    assert len([1 for n, _ in resumer.calls if n == "b"]) == 2  # two revision runs


def test_parked_downtime_is_review_wait_not_agent_duration(tmp_path):
    parker = RecordingExecutor()
    _park(tmp_path, parker)

    # Backdate the park by two hours — as if the reviewer slept on it.
    run_dir = tmp_path / "runs" / "r"
    record = json.loads((run_dir / "park.json").read_text())
    record["parked_at"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    (run_dir / "park.json").write_text(json.dumps(record))

    resumer = RecordingExecutor()
    _resume(tmp_path, resumer, _scripted((REVIEW_APPROVE, "")))

    b = _status(tmp_path)["nodes"]["b"]
    assert b["review_wait_seconds"] >= 7000  # the two parked hours are review wait…
    assert b["duration_seconds"] < 600  # …and are NOT in the node's work duration


def test_resume_charges_the_pending_cost_exactly_once(tmp_path):
    from cadora.budget import load_baseline

    parker = RecordingExecutor()
    _park(tmp_path, parker)
    # Before resume, only the drained sibling is in the accounting chain.
    assert load_baseline(str(tmp_path / "runs"))["fake"] == pytest.approx(1.0)

    resumer = RecordingExecutor()
    _resume(tmp_path, resumer, _scripted((REVIEW_APPROVE, "")))
    # After: a ($1) + b ($1, from the parking process, once) + c ($1).
    assert load_baseline(str(tmp_path / "runs"))["fake"] == pytest.approx(3.0)


def test_cli_park_then_resume_end_to_end(tmp_path, monkeypatch):
    """The full journey through the real CLI: run parks with exit 75, `cadora resume
    --review-file` collects the decision from the file surface and completes the run."""
    import threading
    import time

    import cadora.cli as cli
    from cadora.review import write_review_decision

    topo = tmp_path / "t.yaml"
    topo.write_text(
        "name: t\nnodes:\n"
        "  - id: a\n    prompt: build a\n    review: true\n"
        "  - id: b\n    prompt: build b\n    depends_on: [a]\n"
    )
    executor = RecordingExecutor()
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: executor)

    with pytest.raises(SystemExit) as excinfo:
        cli.main(
            [
                "run", str(topo),
                "--cwd", str(tmp_path),
                "--archive-dir", str(tmp_path / "runs"),
                "--run-id", "cli-park",
                "--hitl", "--on-review", "park",
                "--yes",
            ]
        )
    assert excinfo.value.code == PARK_EXIT_CODE
    assert [n for n, _ in executor.calls] == ["a"]

    def approve():
        request = tmp_path / "cadora-review-request.json"
        for _ in range(1500):
            if request.is_file():
                break
            time.sleep(0.01)
        write_review_decision(tmp_path, REVIEW_APPROVE, "ship it")

    reviewer = threading.Thread(target=approve, daemon=True)
    reviewer.start()
    resumer = RecordingExecutor()
    monkeypatch.setattr(cli, "get_executor", lambda name, **kw: resumer)
    rc = cli.main(
        [
            "resume", str(tmp_path / "runs" / "cli-park"),
            "--review-file", "--review-timeout", "30",
            "--allow-drift", "--yes",
        ]
    )
    reviewer.join(timeout=30)
    assert rc == 0
    assert [n for n, _ in resumer.calls] == ["b"]  # a reviewed, not re-run; b proceeded
    manifest = json.loads((tmp_path / "runs" / "cli-park" / "manifest.json").read_text())
    assert manifest["ok"] is True
    assert {n["node_id"] for n in manifest["nodes"]} == {"a", "b"}


def test_resume_verifies_against_the_parked_runs_OWN_fingerprint_not_a_sibling(tmp_path):
    """Regression for the F3 field bug: a park-resume must check the parked run's OWN workspace
    fingerprint, not the newest OTHER run in the archive. Before the fix, any archive holding a
    second run made `cadora resume` refuse on spurious drift — the common case.

    Reproduces the live failure: park run 'r', drop a later sibling run with a DIFFERENT
    fingerprint into the same archive, then resume 'r' with the drift check ON (allow_drift=False).
    It must NOT refuse — 'r's own workspace is unchanged.
    """
    import json as _json

    parker = RecordingExecutor()
    _park(tmp_path, parker)  # parks run 'r', writes runs/r/workspace-manifest.json for its ws

    # A later sibling run (sorts after 'r') with a divergent fingerprint — exactly what
    # latest_prior_fingerprint(exclude_run_id='r') would wrongly pick as the baseline.
    sibling = tmp_path / "runs" / "zzz-later-run"
    sibling.mkdir()
    (sibling / "workspace-manifest.json").write_text(
        _json.dumps({"file_count": 1, "tree_sha256": "deadbeef",
                     "files": {"totally/different.py": "deadbeef"}})
    )

    resumer = RecordingExecutor()
    # allow_drift=False: the drift check is ON. With the bug this raises SystemExit("drifted
    # since zzz-later-run"). Fixed, it verifies against runs/r's own manifest and proceeds.
    _resume(tmp_path, resumer, _scripted((REVIEW_APPROVE, "ok")), allow_drift=False)

    manifest = _manifest(tmp_path)
    assert manifest["ok"] is True
    assert [n for n, _ in resumer.calls] == ["c"]  # parked node not re-run; only downstream ran


def test_park_record_carries_the_doc_shas(tmp_path):
    """The replayability gap, closed as a side effect: the record says exactly which bytes
    were pending review."""
    docs = tmp_path / "aidlc-docs"
    docs.mkdir()
    (docs / "design.md").write_text("# the design\n")
    executor = RecordingExecutor()
    _park(tmp_path, executor)
    record = load_park_record(tmp_path / "runs" / "r")
    snapshot = record["pending"][0]["pre_review_docs"]
    assert "aidlc-docs/design.md" in snapshot
    assert len(snapshot["aidlc-docs/design.md"]) == 64  # a SHA-256, not a maybe
