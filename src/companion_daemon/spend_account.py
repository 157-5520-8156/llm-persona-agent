"""Classify production vs debug model spend without relying on agent memory.

Default-safe: only the live ledgers under ``data/`` count as production.
Every other sqlite (clones under ``output/``, tests, scratch) is debug.
Clones copy the production ``world_id``, so path is the authority.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import threading

_LOG = logging.getLogger(__name__)

PRODUCTION_LEDGER_FILENAMES = frozenset(
    {
        "companion.sqlite",
        "companion.epoch2.sqlite",
        "companion.epoch1.sqlite",
        "companion.epoch1.rerun.sqlite",
    }
)

SPEND_ACCOUNT_PRODUCTION = "production"
SPEND_ACCOUNT_DEBUG = "debug"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DATA_DIR = _REPO_ROOT / "data"
_DEBUG_LEDGER = _REPO_ROOT / "output" / "debug-spend" / "model_usage.sqlite"

_DEBUG_STORE = None
_DEBUG_LOCK = threading.Lock()


def repo_root() -> Path:
    return _REPO_ROOT


def debug_ledger_path() -> Path:
    return _DEBUG_LEDGER


def classify_spend_account(
    *,
    database_path: str | Path | None,
    world_id: str = "",
) -> str:
    """Return ``production`` or ``debug``. Unknown paths default to debug."""

    del world_id  # clones copy the live world_id; path is the only safe signal
    if not database_path:
        return SPEND_ACCOUNT_DEBUG
    try:
        resolved = Path(database_path).expanduser().resolve()
    except OSError:
        return SPEND_ACCOUNT_DEBUG
    try:
        resolved.relative_to(_DATA_DIR.resolve())
    except ValueError:
        return SPEND_ACCOUNT_DEBUG
    if resolved.name in PRODUCTION_LEDGER_FILENAMES:
        return SPEND_ACCOUNT_PRODUCTION
    return SPEND_ACCOUNT_DEBUG


def observer_writes_production_ledger(observer: object | None) -> bool:
    """True when the bound usage observer already writes the live ledger."""

    if observer is None:
        return False
    store = getattr(observer, "__self__", None)
    path = getattr(store, "_path", None) or getattr(store, "path", None)
    if not path:
        return False
    return classify_spend_account(database_path=path) == SPEND_ACCOUNT_PRODUCTION


def _debug_recording_disabled() -> bool:
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    flag = os.environ.get("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "").strip()
    return flag in {"1", "true", "True", "yes"}


def maybe_record_debug_usage(usage: object, *, observer: object | None = None) -> None:
    """Persist clone/unattributed DeepSeek calls onto the debug book.

    Production observers already write ``data/companion.epoch2.sqlite``; this
    path is skipped so the live turn does not pay extra sqlite I/O.
    """

    if _debug_recording_disabled():
        return
    if observer_writes_production_ledger(observer):
        return
    try:
        store = _debug_usage_store()
        store.record(usage)
    except Exception:
        _LOG.warning("debug spend ledger write failed", exc_info=True)


def _debug_usage_store():
    global _DEBUG_STORE
    with _DEBUG_LOCK:
        if _DEBUG_STORE is None:
            from companion_daemon.world_v2.model_usage_budget import WorldV2UsageStore

            _DEBUG_LEDGER.parent.mkdir(parents=True, exist_ok=True)
            _DEBUG_STORE = WorldV2UsageStore(path=str(_DEBUG_LEDGER))
        return _DEBUG_STORE


__all__ = [
    "PRODUCTION_LEDGER_FILENAMES",
    "SPEND_ACCOUNT_DEBUG",
    "SPEND_ACCOUNT_PRODUCTION",
    "classify_spend_account",
    "debug_ledger_path",
    "maybe_record_debug_usage",
    "observer_writes_production_ledger",
    "repo_root",
]
