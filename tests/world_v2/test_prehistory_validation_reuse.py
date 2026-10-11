"""Unrelated ledger commits reuse content work, but recheck current authority."""

from datetime import timedelta

import pytest

import companion_daemon.world_v2.character_prehistory as history
from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
from test_character_prehistory import ACTOR, START, reviewed_archive, started_ledger
from test_life_projection import commit, event


def test_unrelated_commits_reuse_validated_immutable_prehistory_payloads(tmp_path, monkeypatch):
    ledger = started_ledger(tmp_path / "reuse.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            reviewed_archive(), created_at=START
        )
        ledger.project().pending_index_matches_actions()
        calls = []
        original = history.digest

        def counted(value):
            if isinstance(
                value,
                (
                    history.PrehistoryRecord,
                    history.PrehistoryRecordImportedPayload,
                    history.PrehistoryArchiveManifest,
                    history.PrehistoryArchiveAcceptedPayload,
                ),
            ):
                calls.append(type(value).__name__)
            return original(value)

        with monkeypatch.context() as patch:
            patch.setattr(history, "digest", counted)
            for n in (1, 2):
                now = START + timedelta(minutes=n)
                commit(
                    ledger,
                    [
                        event(
                            f"clock-reuse-{n}",
                            "ClockAdvanced",
                            {
                                "logical_time_from": (now - timedelta(minutes=1)).isoformat(),
                                "logical_time_to": now.isoformat(),
                                "reason": "prehistory-reuse-test",
                            },
                            at=now,
                        )
                    ],
                )
                ledger.project()
        assert calls == [], "unchanged prehistory must not be serialized and rehashed per commit"
        assert ledger.project().semantic_hash == ledger.rebuild().semantic_hash
    finally:
        ledger.close()


@pytest.mark.parametrize(
    "change", ["withdraw", "source_hash", "time", "world", "body", "admission"]
)
def test_warm_prehistory_values_never_replace_current_authority_checks(tmp_path, change):
    ledger = started_ledger(tmp_path / "authority.sqlite")
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            reviewed_archive(), created_at=START
        )
        state = ledger.project()
        kwargs = dict(
            archives=state.prehistory_archives,
            records=state.prehistory_records,
            events=state.committed_world_event_refs,
            logical_time=state.logical_time,
            world_id=state.world_id,
        )
        history.validate_prehistory_state(**kwargs)
        record = state.prehistory_records[0]
        if change == "withdraw":
            kwargs["events"] = tuple(
                e for e in kwargs["events"] if e.event_id != record.accepted_event_ref
            )
        elif change == "source_hash":
            kwargs["events"] = tuple(
                e.model_copy(update={"payload_hash": "f" * 64})
                if e.event_id == record.accepted_event_ref
                else e
                for e in kwargs["events"]
            )
        elif change == "time":
            kwargs["logical_time"] = START - timedelta(seconds=1)
        elif change == "world":
            kwargs["world_id"] = "world:other"
        elif change == "body":
            kwargs["records"] = (
                record.model_copy(
                    update={
                        "record": record.record.model_copy(
                            update={"statement": "来源没有接受的改写"}
                        )
                    }
                ),
            )
        else:
            archive = kwargs["archives"][0]
            kwargs["archives"] = (
                archive.model_copy(
                    update={"review": archive.review.model_copy(update={"manifest_hash": "f" * 64})}
                ),
            )
        with pytest.raises(ValueError):
            history.validate_prehistory_state(**kwargs)
    finally:
        ledger.close()


def test_repeated_projection_reuses_only_unchanged_history_structure(tmp_path, monkeypatch):
    ledger = started_ledger(tmp_path / 'history-reuse.sqlite')
    try:
        PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(
            reviewed_archive(), created_at=START)
        projection = ledger.project()
        validator = getattr(history, '_validate_prehistory_contents', None)
        if validator is not None:
            validator.cache_clear()
        calls = []
        original = history.validate_historical_references

        def count(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)

        monkeypatch.setattr(history, 'validate_historical_references', count)
        args = (projection.prehistory_archives, projection.prehistory_records,
                projection.committed_world_event_refs)
        history.validate_prehistory_state(*args, logical_time=START, world_id=projection.world_id)
        first = len(calls)
        assert first > 0
        history.validate_prehistory_state(*args, logical_time=START + timedelta(seconds=1),
            world_id=projection.world_id)
        assert len(calls) == first
        # A successful structural read cannot authorize a new world, past
        # clock, missing event or changed accepted body.
        with pytest.raises(ValueError):
            history.validate_prehistory_state(*args, logical_time=START, world_id='world:other')
        with pytest.raises(ValueError):
            history.validate_prehistory_state(*args, logical_time=START - timedelta(seconds=1))
        with pytest.raises(ValueError):
            history.validate_prehistory_state(args[0], args[1], (), logical_time=START)
        row = args[1][0]
        changed = row.model_copy(update={'actor_ref': 'actor:other'})
        with pytest.raises(ValueError):
            history.validate_prehistory_state(args[0], (changed,), args[2], logical_time=START)
    finally:
        ledger.close()
