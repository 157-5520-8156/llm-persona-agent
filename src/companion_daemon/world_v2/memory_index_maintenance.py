"""Write-time preparation of a disposable unified Recall index.

An accepted source cursor is the recovery queue: after restart or a failed
batch, reconcile current sources and reuse durable vectors. This module never
writes World history, classifies motives or makes retention choices.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time

from .ledger import LedgerPort
from .recall_index import RecallEmbeddingUnavailable

_LOG = logging.getLogger(__name__)


class MemoryIndexMaintenance:
    def __init__(self, *, ledger: LedgerPort, prepare: Callable[[], dict[str, object]]) -> None:
        self._ledger = ledger
        self._prepare = prepare
        self._lock = threading.RLock()
        self._closing = threading.Event()
        self._cursor: dict[str, int] | None = None
        self._retry_at = 0.0
        self._report: dict[str, object] = {"status": "unprepared"}

    def close(self) -> None:
        self._closing.set()
        # Let an already-started read/provider batch finish before its stores close.
        with self._lock:
            pass

    def health(self) -> dict[str, object]:
        with self._lock:
            return dict(self._report)

    def advance_once(self, *, force: bool = False) -> dict[str, object]:
        with self._lock:
            if self._closing.is_set():
                return {"status": "closed"}
            state = self._ledger.project()
            target = {
                "world_revision": state.world_revision,
                "deliberation_revision": state.deliberation_revision,
                "ledger_sequence": state.ledger_sequence,
            }
            if not force and self._cursor == target and self._report["status"] == "ready":
                return {**self._report, "unchanged": True}
            if not force and time.monotonic() < self._retry_at:
                return {**self._report, "retry_pending": True, "target_cursor": target}
            started = time.perf_counter()
            try:
                report = self._prepare()
                if report.get("status") == "ready":
                    self._cursor = dict(report["cursor"])
                    self._retry_at = 0.0
                else:
                    self._retry_at = time.monotonic() + 30.0
                self._report = {**report, "seconds": time.perf_counter() - started}
            except Exception as exc:
                # Preparation is not a prerequisite for speaking. Keep the
                # failure observable; the current-query path retains its own
                # authority gates and bounded semantic/local fallback.
                self._retry_at = time.monotonic() + 30.0
                self._report = {
                    "status": "degraded",
                    "cursor": self._cursor,
                    "target_cursor": target,
                    "failure_code": (
                        str(exc)[:128]
                        if isinstance(exc, RecallEmbeddingUnavailable)
                        else type(exc).__name__
                    ),
                    "seconds": time.perf_counter() - started,
                }
                _LOG.warning("memory index preparation failed: %s", type(exc).__name__)
            return dict(self._report)
