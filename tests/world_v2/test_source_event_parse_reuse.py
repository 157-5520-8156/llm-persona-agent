"""Source rows stay fresh while their identical envelope parsing is reused."""
import hashlib
import json

import pytest

from companion_daemon.world_v2.errors import LedgerIntegrityError
from companion_daemon.world_v2 import sqlite_ledger
from test_sqlite_ledger import event


def test_source_lookup_reuses_parse_but_rechecks_row_hash_world_and_revision(tmp_path, monkeypatch):
    ledger = sqlite_ledger.SQLiteWorldLedger(path=tmp_path/'source.sqlite', world_id='world-sqlite-test')
    try:
        source = event('source:one', 'observation:one')
        result = ledger.commit((source,), expected_world_revision=0, expected_deliberation_revision=0)
        ledger._verified_event_commit_cache.clear()
        original = sqlite_ledger.validate_event_identity
        calls = []

        def verify(value):
            calls.append(value.event_id)
            return original(value)

        monkeypatch.setattr(sqlite_ledger, 'validate_event_identity', verify)
        first = ledger.resolve_committed_event_refs((source.event_id,), at_world_revision=result.world_revision)
        second = ledger.resolve_committed_event_refs((source.event_id,), at_world_revision=result.world_revision)
        assert first == second
        assert calls == [source.event_id]
        with pytest.raises(ValueError, match='newer'):
            ledger.resolve_committed_event_refs((source.event_id,), at_world_revision=0)
        # Even a warm parse must verify the bytes actually read from SQLite.
        ledger._connection.execute('UPDATE world_v2_events SET event_hash=?', ('0'*64,))
        with pytest.raises(LedgerIntegrityError, match='envelope hash'):
            ledger.resolve_committed_event_refs((source.event_id,), at_world_revision=result.world_revision)
        raw = json.loads(ledger._connection.execute('SELECT event_json FROM world_v2_events').fetchone()[0])
        raw['world_id'] = 'world:foreign'
        changed = json.dumps(raw)
        ledger._connection.execute('UPDATE world_v2_events SET event_json=?,event_hash=?',
            (changed, hashlib.sha256(changed.encode()).hexdigest()))
        with pytest.raises(LedgerIntegrityError, match='row does not match'):
            ledger.resolve_committed_event_refs((source.event_id,), at_world_revision=result.world_revision)
        assert len(calls) == 2
    finally:
        ledger.close()
