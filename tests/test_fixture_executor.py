"""Tests for the local deterministic fixture executor."""

import json

from cadora.executors import get_executor
from cadora.executors.fixture import FixtureExecutor
from cadora.review import REVIEW_APPROVE, REVIEW_REQUEST_CHANGES, ReviewResult
from cadora.runner import run_topology
from cadora.topology import Node, Topology


def test_fixture_is_registered():
    assert isinstance(get_executor("fixture"), FixtureExecutor)


def test_fixture_writes_reviewable_aidlc_docs(tmp_path):
    executor = FixtureExecutor()
    topology = Topology(
        name="fixture-demo",
        nodes=[
            Node(id="requirements", prompt="Draft requirements.", review=True),
            Node(id="design", prompt="Draft design.", depends_on=["requirements"]),
        ],
    )

    out = run_topology(
        topology,
        executor,
        run_id="fixture-docs",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=lambda node, cwd: ReviewResult(REVIEW_APPROVE, "ok"),
    )

    assert (
        tmp_path
        / "aidlc-docs"
        / "inception"
        / "requirements"
        / "requirements.md"
    ).is_file()
    assert (
        tmp_path
        / "aidlc-docs"
        / "inception"
        / "application-design"
        / "design.md"
    ).is_file()
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["executor"] == "fixture"
    assert manifest["nodes"][0]["model"] == "local-fixture"
    assert manifest["nodes"][0]["meta"]["external_model"] is False
    assert manifest["nodes"][0]["aidlc_docs"] == "requirements/aidlc-docs"


def test_fixture_revision_loop_marks_revised_artifact(tmp_path):
    executor = FixtureExecutor()
    topology = Topology(
        name="fixture-revision",
        nodes=[Node(id="requirements", prompt="Draft requirements.", review=True)],
    )
    decisions = iter(
        [
            ReviewResult(REVIEW_REQUEST_CHANGES, "Add audit controls."),
            ReviewResult(REVIEW_APPROVE, "ok"),
        ]
    )

    run_topology(
        topology,
        executor,
        run_id="fixture-revision",
        cwd=str(tmp_path),
        archive_root=str(tmp_path / "runs"),
        hitl=True,
        review_fn=lambda node, cwd: next(decisions),
    )

    requirements = (
        tmp_path
        / "aidlc-docs"
        / "inception"
        / "requirements"
        / "requirements.md"
    ).read_text()
    assert "Artifact hashes and privacy boundaries" in requirements
