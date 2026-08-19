from pathlib import Path

from companion_daemon.spend_account import (
    SPEND_ACCOUNT_DEBUG,
    SPEND_ACCOUNT_PRODUCTION,
    classify_spend_account,
    debug_ledger_path,
    observer_writes_production_ledger,
    repo_root,
)


def test_only_data_dir_live_ledgers_are_production() -> None:
    data = repo_root() / "data"
    assert (
        classify_spend_account(database_path=data / "companion.epoch2.sqlite")
        == SPEND_ACCOUNT_PRODUCTION
    )
    assert (
        classify_spend_account(database_path=data / "companion.sqlite")
        == SPEND_ACCOUNT_PRODUCTION
    )
    assert (
        classify_spend_account(
            database_path=repo_root() / "output" / "drive-0818" / "clone.sqlite",
            world_id="world:companion-v2:qq-c2c:geoff",
        )
        == SPEND_ACCOUNT_DEBUG
    )
    assert (
        classify_spend_account(database_path=data / "scratch-not-live.sqlite")
        == SPEND_ACCOUNT_DEBUG
    )
    assert classify_spend_account(database_path=None) == SPEND_ACCOUNT_DEBUG


def test_copied_production_world_id_does_not_make_a_clone_production(tmp_path: Path) -> None:
    clone = tmp_path / "companion.epoch2.sqlite"
    clone.write_bytes(b"")
    assert (
        classify_spend_account(
            database_path=clone,
            world_id="world:companion-v2:qq-c2c:geoff",
        )
        == SPEND_ACCOUNT_DEBUG
    )


def test_production_observer_is_detected_from_bound_store() -> None:
    class _Store:
        def __init__(self, path: str) -> None:
            self._path = path

        def record(self, usage: object) -> None:
            del usage

    production = _Store(str(repo_root() / "data" / "companion.epoch2.sqlite"))
    debug = _Store(str(repo_root() / "output" / "clone.sqlite"))
    assert observer_writes_production_ledger(production.record) is True
    assert observer_writes_production_ledger(debug.record) is False
    assert observer_writes_production_ledger(None) is False
    assert debug_ledger_path().as_posix().endswith("output/debug-spend/model_usage.sqlite")
