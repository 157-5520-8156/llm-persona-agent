"""Generic Outcome recovery publishes its selected result without LifeAftermath.

The installed SQLite application and real DeepSeek adapter use MockTransport.
Only sidecar/ledger write failures are injected; accepted events are never seeded.
"""

import asyncio
import json

import pytest

from companion_daemon.world_v2.errors import ConcurrencyConflict
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.outcome_acceptance_runtime import OutcomeAcceptanceRuntime
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_world_stimulus_life_intent import WORLD, _RoleHTTP, _build, _model
from test_world_stimulus_life_response import _settled


class _NoHTTP:
    def __init__(self):
        self.calls = 0

    async def __call__(self, request):
        self.calls += 1
        raise AssertionError("durable Outcome recovery must not call the provider")


def _result_descriptors(projection, occurrence):
    return tuple(
        item
        for item in projection.life_content_descriptors
        if item.source_event_ref == occurrence.settlement_event_ref
        and item.content_kind == "occurrence_result"
    )


def _assert_published(path, projection, occurrence):
    descriptors = _result_descriptors(projection, occurrence)
    assert len(descriptors) == 1, "accepted recovery must publish the exact result descriptor"
    (descriptor,) = descriptors
    selected = next(
        item
        for item in occurrence.candidate_outcomes
        if item.candidate_result_ref == occurrence.settled_outcome_ref
    )
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        candidate = store.read_exact(content_ref=selected.content_ref)
        result = store.read_exact(content_ref=occurrence.result_payload_ref)
        assert candidate is not None and result is not None
        assert candidate.content_kind == "outcome_candidate"
        assert result.content_kind == "occurrence_result"
        assert result.text == candidate.text
        assert json.loads(result.text) == {
            "contract": "world-consequence.2",
            "environment_text": "一阵短雨已经停了。",
        }
        assert result.content_payload_hash == selected.content_payload_hash
        assert descriptor.content_ref == result.content_ref
        assert descriptor.content_payload_hash == result.content_payload_hash
        assert descriptor.source_world_revision == occurrence.settlement_world_revision
        assert descriptor.source_payload_hash == occurrence.settlement_payload_hash
    finally:
        store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["result_put", "descriptor_cas"])
async def test_cold_generic_outcome_recovers_result_before_completing_trigger(
    tmp_path, monkeypatch, failure
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    provider = _RoleHTTP()
    model = _model(provider)
    # This production fixture explicitly defaults to life_ecology=None.
    app = _build(path, model, ecology=False)
    failures = []
    try:
        with monkeypatch.context() as fault:
            if failure == "result_put":
                original = SQLiteImmutableLifeContentStore.put_if_absent

                def failing_put(store, record):
                    if record.content_kind == "occurrence_result":
                        failures.append(record.content_ref)
                        raise asyncio.CancelledError("offline result sidecar process stop")
                    return original(store, record)

                fault.setattr(SQLiteImmutableLifeContentStore, "put_if_absent", failing_put)
            else:
                original = SQLiteWorldLedger.commit_at_cursor

                def failing_commit(ledger, events, **kwargs):
                    events = tuple(events)
                    if any(
                        event.event_type == "LifeContentRecorded"
                        and event.payload()["source_kind"] == "occurrence_settlement"
                        for event in events
                    ):
                        failures.append(events[0].event_id)
                        raise ConcurrencyConflict("offline result descriptor CAS failure")
                    return original(ledger, events, **kwargs)

                fault.setattr(SQLiteWorldLedger, "commit_at_cursor", failing_commit)
            if failure == "result_put":
                with pytest.raises(asyncio.CancelledError, match="offline result"):
                    await _settled(app)
            else:
                # The public scheduler leaves the durable claim for a later
                # wake after CAS loss, returning normally instead of raising.
                await _settled(app)
        assert len(failures) == 1
        assert len(provider.requests) == 1
        request = json.loads(provider.requests[0]["messages"][-1]["content"])
        assert request["inner_turn"]["purpose"] == "outcome_selection"
        before = app.export_replay_evidence()
        (occurrence,) = before.projection.world_occurrences
        assert occurrence.status == "settled"
        assert _result_descriptors(before.projection, occurrence) == ()
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
        try:
            partial_result = store.read_exact(content_ref=occurrence.result_payload_ref)
            assert (partial_result is None) == (failure == "result_put")
        finally:
            store.close()
        process = next(
            item
            for item in before.projection.trigger_processes
            if item.process_kind == "outcome_deliberation"
        )
        assert process.state != "terminal"
        assert before.projection.experiences == ()
        assert sum(row.event.event_type == "WorldOccurrenceSettled" for row in before.events) == 1
    finally:
        await app.aclose()
        await model.aclose()

    cold_provider = _NoHTTP()
    cold_model = _model(cold_provider)
    cold_app = _build(path, cold_model, ecology=False)
    try:
        recovered = await cold_app.drain_background_once()
        assert recovered.work_status == "accepted"
        assert cold_provider.calls == 0
        after = cold_app.export_replay_evidence()
        _assert_published(path, after.projection, occurrence)
        assert after.projection.world_occurrences == before.projection.world_occurrences
        assert after.projection.acceptance_decisions == before.projection.acceptance_decisions
        assert after.projection.model_result_audits == before.projection.model_result_audits
        assert after.projection.proposal_audits == before.projection.proposal_audits
        assert after.projection.experiences == ()
        old_bytes = {row.event.event_id: row.event.model_dump_json() for row in before.events}
        assert {
            row.event.event_id: row.event.model_dump_json()
            for row in after.events
            if row.event.event_id in old_bytes
        } == old_bytes
        assert (
            next(
                item
                for item in after.projection.trigger_processes
                if item.trigger_id == process.trigger_id
            ).state
            == "terminal"
        )
    finally:
        await cold_app.aclose()
        await cold_model.aclose()


@pytest.mark.asyncio
async def test_accept_returns_its_atomic_acceptance_commit_after_publishing_result(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    returned = []
    original = OutcomeAcceptanceRuntime.accept

    def capture_accept(runtime, **kwargs):
        result = original(runtime, **kwargs)
        returned.append(result)
        return result

    monkeypatch.setattr(OutcomeAcceptanceRuntime, "accept", capture_accept)
    path = tmp_path / "world.sqlite"
    provider = _RoleHTTP()
    model = _model(provider)
    app = _build(path, model, ecology=False)
    try:
        await _settled(app)
        assert len(provider.requests) == len(returned) == 1
        evidence = app.export_replay_evidence()
        events = {row.event.event_id: row.event for row in evidence.events}
        assert tuple(events[ref].event_type for ref in returned[0].event_ids) == (
            "AcceptanceRecorded",
            "WorldOccurrenceSettled",
            "TriggerProcessOpened",
        )
        (occurrence,) = evidence.projection.world_occurrences
        _assert_published(path, evidence.projection, occurrence)
    finally:
        await app.aclose()
        await model.aclose()
