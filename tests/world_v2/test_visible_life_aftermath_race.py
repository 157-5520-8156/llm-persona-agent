"""Due world-contingency settlement and the visible input seam."""

import asyncio
from datetime import timedelta
import inspect
import threading
import time
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2 import life_aftermath_runtime as life_module
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_ecology_runtime import LifeEcologyAvailability, LifeEcologyRuntime
from companion_daemon.world_v2.life_ecology_trigger_store import LedgerLifeEcologyTriggerStore
from companion_daemon.world_v2.life_events import OutcomeObservationRecordedPayload
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.errors import ConcurrencyConflict
from companion_daemon.world_v2.qq_c2c_host import _VisibleTurnReconciliationGate
from companion_daemon.world_v2.schemas import EvidenceRef, OutcomeObservationProjection, ProjectionCursor, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import (
    NOW, WORLD_ID, _SequenceModel, _novel_origin_review, _seed_clock, _source_closure_review,
)
from test_world_author_request_audit import _ReceivedAuthor, _json
from test_world_consequence_aftermath import _NoCharacterCalls, _aftermath
from test_world_consequence_producer import _advance, _draft, _runtime
from test_life_ecology_runtime import _Aftermath, _Ledger, _Media, _TriggerStore, _event


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ordinary", "cancel", "bounded_wait"])
async def test_due_life_settlement_does_not_overtake_a_visible_pin(tmp_path, monkeypatch, mode):
    if mode == "bounded_wait":
        monkeypatch.setattr(life_module, "_VISIBLE_AFTERMATH_WAIT_SECONDS", 0.05)
    path = tmp_path / "life-foreground.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    try:
        wake = _seed_clock(ledger)
        draft = _draft(wake)
        author = _ReceivedAuthor(store, (_json(draft),))
        general = _SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),))
        focused = _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),))
        producer = _runtime(ledger, store, wake, author, general, focused)
        assert (await _advance(producer, wake)).status == "occurrence_committed"
        due = _seed_clock(ledger, event_id="event:clock:foreground-life-due",
                          logical_time=NOW + timedelta(minutes=20), logical_time_from=NOW)
        aftermath = _aftermath(ledger, store, _NoCharacterCalls())
        gate = _VisibleTurnReconciliationGate()
        aftermath.set_background_visible_gate(gate)
        async with gate.visible_turn():
            before = ledger.project()
            task = asyncio.create_task(_advance(aftermath, due))
            await asyncio.sleep(0.1)
            during = ledger.project()
            if mode == "bounded_wait":
                assert during.world_revision > before.world_revision
            else:
                assert during.world_revision == before.world_revision, (
                    "life aftermath advanced the pinned world before foreground finished",
                    before.world_revision, during.world_revision,
                )
            if mode == "cancel":
                task.cancel()
                assert isinstance((await asyncio.gather(task, return_exceptions=True))[0], asyncio.CancelledError)
            elif mode == "ordinary":
                _seed_clock(ledger, event_id="event:clock:foreground-advanced",
                            logical_time=NOW + timedelta(minutes=21), logical_time_from=due.logical_time)
        settled = await (_advance(aftermath, due) if mode == "cancel" else asyncio.wait_for(task, 5))
        assert settled.status == "settled"
        assert ledger.project().world_occurrences[0].settled_at == NOW + timedelta(minutes=21 if mode == "ordinary" else 20)
        evidence = ledger.export_replay_evidence()
        assert (await _advance(aftermath, due)).status == "no_op"
        assert ledger.export_replay_evidence() == evidence
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        assert ledger.export_replay_evidence() == evidence
        assert (await _advance(_aftermath(ledger, store, _NoCharacterCalls()), due)).status == "no_op"
        assert ledger.export_replay_evidence() == evidence
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_slow_life_projection_has_no_exclusive_gate_but_still_blocks_loop(tmp_path, monkeypatch):
    """The wait adds no held gate; the pre-existing synchronous work remains visible."""
    path = tmp_path / "slow-life.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    sender = None
    try:
        wake = _seed_clock(ledger)
        author = _ReceivedAuthor(store, (_json(_draft(wake)),))
        general = _SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),))
        focused = _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),))
        assert (await _advance(_runtime(ledger, store, wake, author, general, focused), wake)).status == "occurrence_committed"
        due = _seed_clock(ledger, event_id="event:clock:slow-life-due",
                          logical_time=NOW + timedelta(minutes=20), logical_time_from=NOW)
        aftermath = _aftermath(ledger, store, _NoCharacterCalls())
        gate = _VisibleTurnReconciliationGate()
        aftermath.set_background_visible_gate(gate)
        started = threading.Event()
        response = asyncio.Event()
        sample = {}
        original = ledger.project
        injected = False

        def slow_project():
            nonlocal injected
            frame = inspect.currentframe()
            if not injected and frame is not None and frame.f_back is not None and frame.f_back.f_code.co_name == "_settle":
                injected = True
                assert not gate._reconciliation_active
                started.set()
                time.sleep(0.2)
            return original()

        monkeypatch.setattr(ledger, "project", slow_project)
        loop = asyncio.get_running_loop()

        def report_from_thread():
            assert started.wait(3)
            sample["sent"] = time.perf_counter()
            loop.call_soon_threadsafe(lambda: (sample.update(received=time.perf_counter()), response.set()))

        sender = threading.Thread(target=report_from_thread)
        sender.start()
        task = asyncio.create_task(_advance(aftermath, due))
        await asyncio.wait_for(response.wait(), 5)
        assert (await asyncio.wait_for(task, 5)).status == "settled"
        delay = sample["received"] - sample["sent"]
        print(f"foreground event-loop delay with 0.2s injected projection: {delay:.3f}s")
        assert delay >= 0.18
    finally:
        if sender is not None:
            sender.join(timeout=3)
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_occurrence_changed_during_wait_has_durable_failure_and_recovers_on_new_wake(tmp_path):
    path = tmp_path / "life-wake-conflict.sqlite"
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
    task = None
    try:
        wake = _seed_clock(ledger)
        draft = _draft(wake)
        author = _ReceivedAuthor(store, (_json(draft),))
        general = _SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),))
        focused = _SequenceModel(model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),))
        assert (await _advance(_runtime(ledger, store, wake, author, general, focused), wake)).status == "occurrence_committed"
        due = _seed_clock(ledger, event_id="event:clock:conflict-life-due",
                          logical_time=NOW + timedelta(minutes=20), logical_time_from=NOW)
        aftermath = _aftermath(ledger, store, _NoCharacterCalls())
        entered = asyncio.Event()

        class ObservedGate(_VisibleTurnReconciliationGate):
            async def wait_for_visible_turns(self):
                entered.set()
                await super().wait_for_visible_turns()

        gate = ObservedGate()
        trigger_store = LedgerLifeEcologyTriggerStore(ledger=ledger, owner_id="worker:life-conflict")
        ecology = LifeEcologyRuntime(
            ledger=ledger, trigger_store=trigger_store, media_followup=_Media(),
            aftermath_followup=aftermath,
            availability=LifeEcologyAvailability(state="installed_and_active"),
        )
        ecology.set_background_visible_gate(gate)
        async with gate.visible_turn():
            task = asyncio.create_task(ecology.advance_once(
                wake_event_ref=due.event_id, trace_id="trace:life-conflict", correlation_id="life-conflict"))
            await asyncio.wait_for(entered.wait(), 5)
            projection = ledger.project()
            occurrence = projection.world_occurrences[0]
            due_ref = next(item for item in projection.committed_world_event_refs if item.event_id == due.event_id)
            evidence = EvidenceRef(ref_id=due.event_id, evidence_type="committed_world_event",
                                   claim_purpose="life_transition", source_world_revision=due_ref.world_revision,
                                   immutable_hash=due.payload_hash)
            observation = OutcomeObservationProjection(
                observation_id="observation:independent-life", occurrence_id=occurrence.occurrence_id,
                source_kind="committed_world_event", source_refs=(due.event_id,),
                observed_payload_ref=due.event_id, observed_payload_hash=due.payload_hash,
                observed_at=projection.logical_time, confidence_bp=10000,
            )
            body = OutcomeObservationRecordedPayload(
                change_id="change:independent-life", transition_id="transition:independent-life",
                expected_entity_revision=occurrence.entity_revision, evidence_refs=(evidence,),
                policy_refs=("policy:life-aftermath.1",), observation=observation,
            )
            event = WorldEvent.from_payload(
                schema_version="world-v2.1", event_id="event:independent-life-observation",
                world_id=WORLD_ID, event_type="OutcomeObservationRecorded", logical_time=projection.logical_time,
                created_at=projection.logical_time, actor="worker:independent", source="test:independent",
                trace_id="trace:independent", causation_id=due.event_id,
                correlation_id="independent", idempotency_key=domain_idempotency_key(
                    event_type="OutcomeObservationRecorded", world_id=WORLD_ID, payload=body.model_dump(mode="json")),
                payload=body.model_dump(mode="json"),
            )
            ledger.commit_at_cursor((event,), expected_cursor=ProjectionCursor(
                world_revision=projection.world_revision, deliberation_revision=projection.deliberation_revision,
                ledger_sequence=projection.ledger_sequence))
        result = await asyncio.wait_for(task, 5)
        assert result.technical_failure_code == "aftermath.cursor_conflict"
        schedule = ledger.project().life_ecology_schedule
        assert schedule.last_failure_code == "aftermath.cursor_conflict"
        assert schedule.next_consideration_at > schedule.last_completed_at
        assert ledger.project().world_occurrences[0].status == "active"
        original_candidates = tuple(
            (item.content_ref, item.content_payload_hash,
             store.read_exact(content_ref=item.content_ref).text)
            for item in ledger.project().world_occurrences[0].candidate_outcomes
        )
        evidence_before_restart = ledger.export_replay_evidence()
        store.close()
        ledger.close()

        ledger = SQLiteWorldLedger(path=path, world_id=WORLD_ID)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD_ID)
        assert ledger.export_replay_evidence() == evidence_before_restart
        assert tuple(
            (item.content_ref, item.content_payload_hash,
             store.read_exact(content_ref=item.content_ref).text)
            for item in ledger.project().world_occurrences[0].candidate_outcomes
        ) == original_candidates
        schedule = ledger.project().life_ecology_schedule
        assert schedule.last_failure_code == "aftermath.cursor_conflict"
        aftermath = _aftermath(ledger, store, _NoCharacterCalls())
        trigger_store = LedgerLifeEcologyTriggerStore(ledger=ledger, owner_id="worker:life-conflict")
        ecology = LifeEcologyRuntime(
            ledger=ledger, trigger_store=trigger_store, media_followup=_Media(),
            aftermath_followup=aftermath,
            availability=LifeEcologyAvailability(state="installed_and_active"),
        )
        ecology.set_background_visible_gate(gate)
        later = _seed_clock(ledger, event_id="event:clock:life-conflict-retry",
                            logical_time=schedule.next_consideration_at,
                            logical_time_from=ledger.project().logical_time)
        retried = await ecology.advance_once(wake_event_ref=later.event_id,
                                             trace_id="trace:retry", correlation_id="retry")
        assert retried.status == "advanced"
        assert ledger.project().world_occurrences[0].status == "settled"
        assert len(author.received) == 1
        events = ledger.recent_events_by_type(event_types=frozenset({"WorldOccurrenceSettled"}), since=NOW, limit=100)
        assert len(events) == 1
        draws = ledger.recent_events_by_type(event_types=frozenset({"RandomDrawRecorded"}), since=NOW, limit=100)
        assert sum(str(item.payload().get("attempt_id", "")).startswith("attempt:life-aftermath:") for item in draws) == 1
    finally:
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_biography_conflict_after_settlement_is_not_misattributed_to_aftermath():
    event = _event("clock-biography-post-settlement-conflict")
    trigger_store = _TriggerStore()

    class Biography:
        calls = 0

        async def advance_once(self, **kwargs):
            self.calls += 1
            if self.calls == 2:
                raise ConcurrencyConflict("biography head changed")
            return SimpleNamespace(status="no_op")

    biography = Biography()
    runtime = LifeEcologyRuntime(
        ledger=_Ledger(event), trigger_store=trigger_store, media_followup=_Media(),
        aftermath_followup=_Aftermath(status="settled"), biographical_followup=biography,
        availability=LifeEcologyAvailability(state="installed_and_active"),
    )
    result = await runtime.advance_once(wake_event_ref=event.event_id,
                                         trace_id="trace:biography", correlation_id="biography")
    assert biography.calls == 2
    assert result.status == "failed_safe"
    assert result.technical_failure_code is None
    assert trigger_store.completed[-1][2] == "failed_safe"
