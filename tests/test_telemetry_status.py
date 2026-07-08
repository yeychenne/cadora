"""status.json carries per-node credits + duration live (as each node completes)."""

import json
import time

from cadora.telemetry import RunTelemetry, _duration
from cadora.topology import Node, Topology


def test_status_node_gets_credits_and_duration_on_completion(tmp_path):
    topo = Topology(name="t", nodes=[Node(id="a")])
    tel = RunTelemetry(str(tmp_path), "r", topo, "kiro")
    tel.node_started("a")
    time.sleep(0.02)
    tel.node_recorded("a", ok=True, usage={"credits": 3.68})

    node = json.loads((tmp_path / "r" / "status.json").read_text())["nodes"]["a"]
    assert node["credits"] == 3.68
    assert node["duration_seconds"] is not None and node["duration_seconds"] >= 0


def test_running_node_has_null_duration_until_complete(tmp_path):
    topo = Topology(name="t", nodes=[Node(id="a")])
    tel = RunTelemetry(str(tmp_path), "r", topo, "claude")
    tel.node_started("a")
    node = json.loads((tmp_path / "r" / "status.json").read_text())["nodes"]["a"]
    assert node["status"] == "running"
    assert node["duration_seconds"] is None  # not known until it completes


def test_duration_helper_handles_missing_timestamps():
    assert _duration(None, "2026-07-08T00:00:01+00:00") is None
    assert _duration("2026-07-08T00:00:00+00:00", None) is None
    assert _duration("2026-07-08T00:00:00+00:00", "2026-07-08T00:00:02+00:00") == 2.0
