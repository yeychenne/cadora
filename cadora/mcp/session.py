"""A topology run driven through a ``ReviewChannel``.

The run executes in a background thread so a review front-end can poll for gates and submit
decisions while the run is in flight. This is the run-control half of the MCP seam.
"""

from __future__ import annotations

import threading
from pathlib import Path

from cadora.executors.base import NodeExecutor
from cadora.mcp.channel import ReviewChannel, ReviewRequest, channel_review_fn
from cadora.review import ReviewResult
from cadora.runner import run_topology
from cadora.topology import Topology


class RunSession:
    """Runs a topology with HITL in a background thread; the channel relays each review gate."""

    def __init__(
        self,
        topology: Topology,
        executor: NodeExecutor,
        *,
        review_timeout: float | None = None,
        **run_kwargs,
    ) -> None:
        self.topology = topology
        self.executor = executor
        self.review_timeout = review_timeout
        self.run_kwargs = run_kwargs
        self.channel = ReviewChannel()
        self.result_path: Path | None = None
        self.error: str | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> RunSession:
        def _run() -> None:
            try:
                self.result_path = run_topology(
                    self.topology,
                    self.executor,
                    hitl=True,
                    review_fn=channel_review_fn(self.channel, self.review_timeout),
                    **self.run_kwargs,
                )
            except SystemExit as exc:
                self.error = str(exc)
            except Exception as exc:  # surface any run failure to the front-end
                self.error = repr(exc)

        self._thread = threading.Thread(target=_run, name="cadora-run", daemon=True)
        self._thread.start()
        return self

    def pending_review(self) -> ReviewRequest | None:
        return self.channel.pending()

    def submit_review(self, result: ReviewResult) -> None:
        self.channel.respond(result)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)
