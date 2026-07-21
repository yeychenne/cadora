"""The evidence must pin the conductor that produced it.

An editable install hot-swaps its own code on a ``git checkout``; without a recorded version +
git SHA, a run whose conductor changed mid-flight is invisible in the archive (observed live:
a parked review gate resolved by a different review.py than the one that opened it).
"""

import json

from cadora.archive import RunArchive
from cadora.provenance import conductor_fingerprint
from cadora.telemetry import RunTelemetry
from cadora.topology import Node, Topology


def _topology():
    return Topology(name="t", nodes=[Node(id="n1")])


def test_conductor_fingerprint_shape():
    info = conductor_fingerprint()
    assert set(info) == {"cadora_version", "git_sha", "git_dirty"}
    from cadora import __version__

    assert info["cadora_version"] == __version__
    # In this repo the package runs from a git checkout, so the SHA must resolve.
    assert isinstance(info["git_sha"], str) and len(info["git_sha"]) == 40
    assert isinstance(info["git_dirty"], bool)


def test_manifest_records_the_conductor(tmp_path):
    archive = RunArchive(tmp_path, "r1", executor="fixture", topology="t")
    archive.finalize(True)
    manifest = json.loads((tmp_path / "r1" / "manifest.json").read_text())
    conductor = manifest["conductor"]
    assert conductor["cadora_version"]
    assert conductor["git_sha"]  # editable install → pinned to a commit


def test_status_records_the_conductor(tmp_path):
    RunTelemetry(tmp_path, "r1", _topology(), executor="fixture")
    status = json.loads((tmp_path / "r1" / "status.json").read_text())
    assert status["conductor"]["cadora_version"]
    assert status["conductor"]["git_sha"]
