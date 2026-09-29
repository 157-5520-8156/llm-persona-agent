"""Offline provider fixtures through the real active-attempt producer/consumer chain."""

from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest
from pydantic import ValidationError
import test_day_open_self_directed_intent as day_fixture

from companion_daemon.world_v2.errors import ConcurrencyConflict
from companion_daemon.world_v2.active_attempt_consequence import (
    ActiveAttemptConsequence, read_active_attempt_consequence, validate_active_attempt_consequence,
)
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest, LifeDevelopmentDraftError, parse_world_author_draft,
)
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_completed_activity_consequence_runtime import _due_wake, _ecology, _manifest
from test_day_open_world_consequence_material import (
    _author_runtime, _day_activity, _wake,
)
from test_life_development_runtime import _novel_origin_review, _seed_clock, _SequenceModel
from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
from test_world_consequence_agency_qualification import _AttemptAuthor, ATTEMPT_RESULTS
from test_world_consequence_authoring_context import _operator_transition
from test_world_consequence_producer import _advance
from test_world_stimulus_life_intent import ACTOR, WORLD, _model
from test_world_stimulus_life_response import _ResponseHTTP

build_app = day_fixture.build_app


async def _active(runtime, source, wake):
    return await runtime.advance_active_attempt_once(
        execution_event_ref=source, wake_event_ref=wake.event_id,
        trace_id="trace:active-attempt", correlation_id="active-attempt-test",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["started", "resumed"])
async def test_active_attempt_actual_author_review_settlement_and_life_reading(
    tmp_path, monkeypatch, build_app, phase,
):
    from test_current_activity_context import current_context

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "active.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, phase)
    source = plan.authority_origin.accepted_event_ref
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        wake = _due_wake(ledger, "active-result")
        author = _AttemptAuthor(store, wake, source)
        critic = _SequenceModel(model="fixture:novel", outputs=(_novel_origin_review(decision="supported"),))
        runtime = _author_runtime(ledger, store, author, critic=critic)
        captured = {}
        source_close = runtime._source_close_world_author_result

        async def capture_source_context(**kwargs):
            captured.update(kwargs)
            return await source_close(**kwargs)

        monkeypatch.setattr(runtime, "_source_close_world_author_result", capture_source_context)
        assert runtime.pending_active_attempt_ref() == source
        result = await _advance(_ecology(ledger, runtime), wake)
        assert result.life_development_followup_status == "occurrence_committed", result
        assert len(author.received) == 1
        body = json.loads(json.loads(author.received[0])[-1]["content"])
        marker = body["capability_manifest"]["active_attempt_consequence"]
        assert "completed_activity_consequence" not in body["capability_manifest"]
        assert marker["execution_binding"]["source_event_ref"] == source
        assert marker["clock_event_ref"] == wake.event_id
        assert body["execution_authority"]["execution_bindings"] == [marker["execution_binding"]]
        assert body["execution_materials"][0]["status"] == "available"
        assert "active_attempt_consequence" in body["cross_field_authority"]
        manifest = _manifest(body["capability_manifest"])
        # Both actual reviewer calls receive the same original-pin time/binding.
        for messages, key in (
            (runtime._source_closure_reviewer.messages[0], "pinned_source_evidence"),
            (critic.messages[0], "pinned_authority"),
        ):
            packet = json.loads(messages[-1]["content"])
            assert packet[key]["execution_authority"]["active_attempt_consequence"] == marker
        from companion_daemon.world_v2.life_development_source_closure import (
            life_development_source_closure_messages, life_development_novel_origin_messages,
        )
        from companion_daemon.world_v2.life_context import compile_life_review_context
        draft = parse_world_author_draft(
            raw=json.dumps(author.draft), manifest=manifest, logical_time=wake.logical_time,
        )
        evidence = packet[key]["execution_authority"]
        for changed in (
            {name: value for name, value in evidence.items() if name != "active_attempt_consequence"},
            {**evidence, "active_attempt_consequence": {**marker, "clock_payload_hash": "f" * 64}},
        ):
            for compiler in (life_development_source_closure_messages, life_development_novel_origin_messages):
                extra = {"cited_events": ()} if compiler is life_development_source_closure_messages else {}
                with pytest.raises(ValueError, match="active attempt reading differs"):
                    compiler(
                        context={**captured["context"], **compile_life_review_context(captured["capsule"])},
                        manifest=manifest, draft=draft, execution_authority=changed, **extra,
                    )
        assert runtime.pending_active_attempt_ref() is None
        assert ledger.project().plans == (plan,) and not ledger.project().experiences
        (occurrence,) = ledger.project().world_occurrences
        for mutation, code in (("missing", "active_attempt_result_binding"),
                               ("wrong", "active_attempt_result_binding"),
                               ("future", "active_attempt_consequence_scope")):
            altered = deepcopy(author.draft)
            if mutation == "missing":
                altered["outcomes"][0]["world_consequence"].pop("authorized_attempt_result")
            elif mutation == "wrong":
                altered["outcomes"][0]["world_consequence"]["authorized_attempt_result"]["execution_binding"]["plan_id"] = "plan:other"
            else:
                altered["causal_authority"] = "character_choice"
            with pytest.raises(LifeDevelopmentDraftError) as rejected:
                parse_world_author_draft(raw=json.dumps(altered), manifest=manifest, logical_time=wake.logical_time)
            assert rejected.value.code == code
        # The exact originally pinned descriptor remains auditable after new commits.
        pinned = ledger.project_at(manifest.pinned_cursor)
        validate_active_attempt_consequence(
            ledger=ledger, content_store=store, pinned_state=pinned,
            actor_ref=ACTOR, descriptor=manifest.active_attempt_consequence,
        )
        for change in ({"clock_payload_hash": "f" * 64},
                       {"current_logical_time": wake.logical_time + timedelta(seconds=1)}):
            with pytest.raises(ValueError):
                validate_active_attempt_consequence(
                    ledger=ledger, content_store=store, pinned_state=pinned, actor_ref=ACTOR,
                    descriptor=manifest.active_attempt_consequence.model_copy(update=change),
                )
        with pytest.raises(ValidationError):
            ActiveAttemptConsequence.model_validate({**marker, "execution_started_at": "2026-01-01T00:00:00"})
        # Cold source-event effect-once: a later Clock cannot mint a second result.
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
        unused = _SequenceModel(model="fixture:unused", outputs=())
        cold = _author_runtime(ledger, store, unused)
        later = _wake(ledger, "duplicate")
        repeated = await _active(cold, source, later)
        assert repeated.occurrence_id == occurrence.occurrence_id and unused.calls == 0
        denied = await _active(_author_runtime(ledger, store, unused, actor="actor:other"), source, later)
        assert denied.reason_code == "life_development.active_attempt_source_unavailable"
        assert denied.occurrence_id is None and unused.calls == 0
        due = _seed_clock(
            ledger, event_id="event:clock:settle", logical_time_from=later.logical_time,
            logical_time=occurrence.time_window.closes_at,
        )
        no_character = _NoCharacterCalls()
        settled = await _advance(_aftermath(ledger, store, no_character), due)
        assert settled.status == "settled" and no_character.calls == 0
        (published,) = ledger.project().world_occurrences
        content = json.loads(store.read_exact(content_ref=published.result_payload_ref).text)
        assert content["authorized_attempt_result"]["text"] in ATTEMPT_RESULTS
        assert content["authorized_attempt_result"]["execution_binding"] == marker["execution_binding"]
        settlement_ref = published.settlement_event_ref
        assert not ledger.project().experiences and ledger.project().plans == (plan,)
    finally:
        store.close()
        ledger.close()
    provider = _ResponseHTTP(text="我想把这件小事记在心里。")
    model = _model(provider)
    # Unlike the private-only fixture, resumed setup includes a user reply.
    # Give that authorized reply a real transport receipt before Life drains.
    import test_world_stimulus_life_intent as shared
    from test_production_turn_application import _DeliveredTransport
    transport = _DeliveredTransport(received_at=due.logical_time)
    monkeypatch.setattr(shared, '_NoExternalActions', lambda: transport)
    app = build_app(path, model, ecology=True)
    try:
        # The resumed fixture has a real inbound reply waiting for dispatch.
        # The host drains actions before yielding capacity to background Life.
        for _ in range(8):
            await app.drain_actions_once()
            await app.drain_background_once()
            if app.export_replay_evidence().projection.experiences:
                break
        evidence = app.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        (experience,) = evidence.projection.experiences
        assert experience.values.source_bindings[0].settlement.authority_event_ref == settlement_ref
        # A resumed fixture also has its delivered-reply stimulus. Require
        # exactly one reading of this settlement, not one global worker call.
        settlement_calls = [body for body in provider.stimulus_requests
            if settlement_ref in json.loads(body['messages'][-1]['content'])[
                'capability_manifest']['payload'].get('world_life_response', {}).get('source_event_refs', [])]
        assert len(settlement_calls) == 1
    finally:
        await app.aclose()
        await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        _, _, snapshot = current_context(ledger, store, actor_ref=ACTOR)
        assert content["authorized_attempt_result"]["text"] in snapshot.materials_json
        # A later completion already has this published progress in its normal
        # Context; no new completion reader or historical pin change is needed.
        terminal = _operator_transition(ledger, plan.plan_id, "ActivityCompleted", "event:complete-after-progress")
        completion_wake = _wake(ledger, "completion-context")
        completion_runtime = _author_runtime(ledger, store, _SequenceModel(model="fixture:unused", outputs=()))
        pinned = completion_runtime._compile_pinned(
            projection=ledger.project(), wake=completion_wake, completed_activity_event_ref=terminal,
        )
        assert isinstance(pinned, tuple)
        assert content["authorized_attempt_result"]["text"] in json.dumps(pinned[2], ensure_ascii=False)
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_active_attempt_budget_backoff_technical_retry_no_op_and_obsolete_head(
    tmp_path, monkeypatch, build_app,
):
    path = tmp_path / "retry.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "resumed")
    source = plan.authority_origin.accepted_event_ref
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        author = _SequenceModel(model="fixture:failure", outputs=(httpx.ReadError("offline"),))
        runtime = _author_runtime(ledger, store, author)
        wake = _wake(ledger, "paused-budget")
        paused = await _advance(_ecology(ledger, runtime, paused=True), wake)
        assert paused.reason_code == "life_ecology.paused_by_budget" and author.calls == 0
        original = next(row.event for row in ledger.export_replay_evidence().events
                        if row.event.event_type == "ActivityStarted")
        rejected = await _active(runtime, original.event_id, wake)
        assert rejected.reason_code == "life_development.active_attempt_source_unavailable"
        old_clock = next(row.event for row in ledger.export_replay_evidence().events
                         if row.event.event_type == "ClockAdvanced")
        assert (await _active(runtime, source, old_clock)).status == "stale_prefix"
        assert author.calls == 0
        due = _due_wake(ledger, "failure")
        failed = await _advance(_ecology(ledger, runtime), due)
        assert failed.life_development_followup_status == "technical_failure"
        assert author.calls == 1 and runtime.pending_active_attempt_ref() == source
        # Same failed wake recovers the technical failure; an early new wake respects backoff.
        assert (await _active(runtime, source, due)).status == "technical_failure"
        assert author.calls == 1
        early = _wake(ledger, "before-backoff")
        result = await _advance(_ecology(ledger, runtime), early)
        assert result.life_development_followup_status is None and author.calls == 1
    finally:
        store.close()
        ledger.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        recovered_author = _SequenceModel(model="fixture:no-op", outputs=(
            '{"decision":"no_op"}', '{"decision":"no_op"}',
        ))
        runtime = _author_runtime(ledger, store, recovered_author)
        due = _due_wake(ledger, "retry")
        result = await _advance(_ecology(ledger, runtime), due)
        assert result.life_development_followup_status == "no_op" and recovered_author.calls == 1
        # Failure backoff yields one due slot to independent life work. That
        # no-op does not consume the original attempt; its next slot must.
        assert runtime.pending_active_attempt_ref() == source
        due = _due_wake(ledger, "retry-after-independent-slot")
        result = await _advance(_ecology(ledger, runtime), due)
        assert result.life_development_followup_status == "no_op" and recovered_author.calls == 2
        assert runtime.pending_active_attempt_ref() is None
        assert not ledger.project().world_occurrences
        assert (await _active(runtime, source, due)).status == "no_op" and recovered_author.calls == 2
        _operator_transition(ledger, plan.plan_id, "ActivityPaused", "event:pause-obsolete")
        assert (await _active(runtime, source, old_clock)).status == "no_op"
        assert runtime.pending_active_attempt_ref() is None and recovered_author.calls == 2
        evidence = ledger.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_active_reader_unreadable_intention_is_not_scheduled(tmp_path):
    from current_activity_fixture import accepted_current_activity
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore

    ledger, original_store, plan_id, source = await accepted_current_activity(sqlite_path=tmp_path / "unreadable.sqlite")
    store = InMemoryImmutableLifeContentStore()
    actor = next(plan.owner_actor_ref for plan in ledger.project().plans if plan.plan_id == plan_id)
    try:
        wake = _wake(ledger)
        unused = _SequenceModel(model="fixture:unused", outputs=())
        runtime = _author_runtime(ledger, store, unused, actor=actor)
        assert runtime.pending_active_attempt_ref() is None
        assert (await _active(runtime, source, wake)).status == "rejected"
        assert unused.calls == 0
        assert read_active_attempt_consequence(
            ledger=ledger, content_store=store, pinned_state=ledger.project(), actor_ref=actor,
            execution_event_ref=source, clock_event_ref=wake.event_id,
        ) is None
    finally:
        original_store.close()
        ledger.close()


def test_absent_active_marker_preserves_historical_manifest_serialization():
    from companion_daemon.world_v2.schemas import ProjectionCursor

    manifest = LifeDevelopmentCapabilityManifest(
        version="life-development-capabilities.2", owner_actor_ref=ACTOR,
        pinned_cursor=ProjectionCursor(world_revision=1, deliberation_revision=0, ledger_sequence=1),
        max_future_days=1, max_window_minutes=60,
    )
    assert "active_attempt_consequence" not in manifest.model_dump(mode="json")
    frozen = manifest.model_dump_json(exclude_computed_fields=True)
    assert LifeDevelopmentCapabilityManifest.model_validate_json(frozen).model_dump_json(exclude_computed_fields=True) == frozen


@pytest.mark.asyncio
async def test_active_attempt_retries_new_draft_after_orphaned_sidecars_and_restart(
    tmp_path, monkeypatch, build_app,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "active-cas-retry.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "started")
    terminal_ref = plan.authority_origin.accepted_event_ref
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        started = next(row.event for row in ledger.export_replay_evidence().events
                       if row.event.event_type == "ActivityStarted")
        wake = _wake(ledger, "before-cas-failure")
        author = _AttemptAuthor(store, wake, started.event_id)
        critic = _SequenceModel(
            model="fixture:novel-origin", outputs=(_novel_origin_review(decision="supported"),),
        )
        runtime = _author_runtime(ledger, store, author, critic=critic)
        commit = ledger.commit_at_cursor
        orphan_refs = []

        def fail_final(events, **kwargs):
            if any(event.event_type == "WorldOccurrenceCommitted" for event in events):
                records, _, _ = runtime._materialize_content(
                    proposal_id=runtime._active_attempt_proposal_id(terminal_ref),
                    draft=parse_world_author_draft(
                        raw=json.dumps(author.draft), logical_time=wake.logical_time,
                        manifest=_manifest(
                            json.loads(json.loads(author.received[0])[-1]["content"])["capability_manifest"],
                        ),
                    ), distinguish_draft=True,
                )
                orphan_refs.extend(record.content_ref for record in records)
                assert all(store.read_exact(content_ref=record.content_ref) == record for record in records)
                raise ConcurrencyConflict("fixture:final-cas")
            return commit(events, **kwargs)

        monkeypatch.setattr(ledger, "commit_at_cursor", fail_final)
        failed = await runtime.advance_active_attempt_once(
            execution_event_ref=terminal_ref, wake_event_ref=wake.event_id,
            trace_id="trace:cas-failure", correlation_id="completed-test",
        )
        assert failed.reason_code == "life_development.acceptance_prefix_stale"
        assert orphan_refs and not ledger.project().world_occurrences
        assert runtime.pending_active_attempt_ref() == terminal_ref
        draft = deepcopy(author.draft)
    finally:
        store.close()
        ledger.close()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        retry = _wake(ledger, "after-cas-restart")
        draft["anchor_refs"] = [retry.event_id]
        draft["premise"] = "桌面上的两本书仍受支撑与重力的影响。"
        author = _SequenceModel(model="fixture:changed-after-cas", outputs=(json.dumps(draft),))
        critic = _SequenceModel(
            model="fixture:novel-origin", outputs=(_novel_origin_review(decision="supported"),),
        )
        runtime = _author_runtime(ledger, store, author, critic=critic)
        recovered = await runtime.advance_active_attempt_once(
            execution_event_ref=terminal_ref, wake_event_ref=retry.event_id,
            trace_id="trace:cas-retry", correlation_id="completed-test",
        )
        assert recovered.status == "occurrence_committed" and author.calls == 1
        assert len(ledger.project().world_occurrences) == 1
        assert runtime.pending_active_attempt_ref() is None
        assert all(store.read_exact(content_ref=ref) is not None for ref in orphan_refs)
        evidence = ledger.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
    finally:
        store.close()
        ledger.close()
