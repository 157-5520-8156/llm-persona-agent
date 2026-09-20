"""Completed role attempt -> World Author -> settlement -> Life/Experience.

Providers are explicit offline fixtures. The ledger, scheduler, source reviews,
acceptance and response consumers are the installed production implementations.
"""

from copy import deepcopy
from datetime import timedelta
import json

import httpx
import pytest
import test_day_open_self_directed_intent as day_fixture

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.errors import ConcurrencyConflict
from companion_daemon.world_v2.life_development_draft import (
    LifeDevelopmentCapabilityManifest, LifeDevelopmentDraftError, parse_world_author_draft,
)
from companion_daemon.world_v2.life_ecology_runtime import LifeEcologyAvailability, LifeEcologyRuntime
from companion_daemon.world_v2.life_ecology_trigger_store import LedgerLifeEcologyTriggerStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_current_activity_context import current_context
from test_day_open_world_consequence_material import _author_runtime, _day_activity, _wake
from test_life_development_runtime import _novel_origin_review, _seed_clock, _SequenceModel
from test_life_ecology_runtime import _Media
from test_world_consequence_aftermath import _aftermath, _NoCharacterCalls
from test_world_consequence_agency_qualification import _AttemptAuthor, ATTEMPT_RESULTS
from test_world_consequence_producer import _advance
from test_world_stimulus_life_intent import ACTOR, WORLD, _model
from test_world_stimulus_life_response import _ResponseHTTP


build_app = day_fixture.build_app


def _manifest(value):
    value = deepcopy(value)
    value.pop("manifest_hash")
    for location in value["location_capabilities"]:
        location.pop("capability_ref")
    return LifeDevelopmentCapabilityManifest.model_validate_json(json.dumps(value))


def _ecology(ledger, runtime, *, paused=False):
    return LifeEcologyRuntime(
        ledger=ledger,
        trigger_store=LedgerLifeEcologyTriggerStore(ledger=ledger, owner_id="worker:completed-test"),
        media_followup=_Media(), life_development_followup=runtime,
        availability=LifeEcologyAvailability(state="installed_and_active"),
        background_budget_paused=lambda: paused,
    )


def _due_wake(ledger, suffix):
    state = ledger.project()
    due = state.life_ecology_schedule.next_consideration_at
    return _seed_clock(
        ledger, event_id="event:clock:" + suffix, logical_time_from=state.logical_time,
        logical_time=max(state.logical_time + timedelta(seconds=1), due),
    )


@pytest.mark.asyncio
async def test_completed_attempt_scheduler_settlement_and_character_response_survive_restart(
    tmp_path, monkeypatch, build_app,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "completed-result.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "completed")
    terminal_ref = plan.authority_origin.accepted_event_ref
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        started = next(row.event for row in ledger.export_replay_evidence().events
                       if row.event.event_type == "ActivityStarted")
        early = _wake(ledger, "before-completion-due")
        untouched = _SequenceModel(model="fixture:not-due", outputs=())
        early_runtime = _author_runtime(ledger, store, untouched)
        early_result = await _advance(_ecology(ledger, early_runtime), early)
        assert early_result.life_development_followup_status is None and untouched.calls == 0
        wake = _due_wake(ledger, "completed-result")
        author = _AttemptAuthor(store, wake, started.event_id)
        critic = _SequenceModel(
            model="fixture:novel-origin", outputs=(_novel_origin_review(decision="supported"),),
        )
        runtime = _author_runtime(ledger, store, author, critic=critic)
        assert runtime.pending_completed_activity_ref() == terminal_ref
        result = await _advance(_ecology(ledger, runtime), wake)
        assert result.life_development_followup_status == "occurrence_committed", result
        assert len(author.received) == 1
        body = json.loads(json.loads(author.received[0])[-1]["content"])
        marker = body["capability_manifest"]["completed_activity_consequence"]
        assert marker["completion"]["event_ref"] == terminal_ref
        assert marker["completion"]["plan_id"] == plan.plan_id
        assert marker["execution_binding"]["source_event_ref"] == started.event_id
        assert body["execution_authority"]["execution_bindings"] == [marker["execution_binding"]]
        assert body["cross_field_authority"]["completed_activity_consequence"]["terminal_evidence"] == (
            "lifecycle_ended_only_not_success_location_or_embedded_history"
        )
        manifest = _manifest(body["capability_manifest"])
        for mutation, code in (("missing", "completed_attempt_result_binding"),
                               ("wrong_plan", "completed_attempt_result_binding"),
                               ("new_plan", "completed_attempt_consequence_scope")):
            altered = deepcopy(author.draft)
            if mutation == "missing":
                altered["outcomes"][0]["world_consequence"].pop("authorized_attempt_result")
            elif mutation == "wrong_plan":
                altered["outcomes"][0]["world_consequence"]["authorized_attempt_result"][
                    "execution_binding"]["plan_id"] = "plan:unrelated"
            else:
                altered["causal_authority"] = "character_choice"
            with pytest.raises(LifeDevelopmentDraftError) as rejected:
                parse_world_author_draft(
                    raw=json.dumps(altered), manifest=manifest, logical_time=wake.logical_time,
                )
            assert rejected.value.code == code
        assert ledger.project().plans == (plan,)
        assert ledger.project().experiences == ()
        (occurrence,) = ledger.project().world_occurrences
        assert occurrence.status == "active"
        assert runtime.pending_completed_activity_ref() is None

        # A cold duplicate retains the original completion family, regardless
        # of the later Clock. It must not ask another model or create a result.
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
        unused = _SequenceModel(model="fixture:must-not-call", outputs=())
        cold = _author_runtime(ledger, store, unused)
        later = _wake(ledger, "duplicate")
        repeated = await cold.advance_completed_activity_once(
            completion_event_ref=terminal_ref, wake_event_ref=later.event_id,
            trace_id="trace:duplicate", correlation_id="completed-test",
        )
        assert repeated.occurrence_id == occurrence.occurrence_id
        assert unused.calls == 0
        assert cold.pending_completed_activity_ref() is None
        other = _author_runtime(ledger, store, unused, actor="actor:other")
        denied = await other.advance_completed_activity_once(
            completion_event_ref=terminal_ref, wake_event_ref=later.event_id,
            trace_id="trace:other-owner-duplicate", correlation_id="completed-test",
        )
        assert denied.status == "rejected" and denied.occurrence_id is None
        assert denied.reason_code == "life_development.completed_activity_source_unavailable"
        assert unused.calls == 0

        due = _seed_clock(
            ledger, event_id="event:clock:completed-result-due",
            logical_time_from=later.logical_time, logical_time=occurrence.time_window.closes_at,
        )
        no_character = _NoCharacterCalls()
        settled = await _advance(_aftermath(ledger, store, no_character), due)
        assert settled.status == "settled"
        assert no_character.calls == 0 and not ledger.project().experiences
        (settlement,) = ledger.project().world_occurrences
        raw = json.loads(store.read_exact(content_ref=settlement.result_payload_ref).text)
        assert raw["authorized_attempt_result"]["text"] in ATTEMPT_RESULTS
        assert raw["authorized_attempt_result"]["execution_binding"] == marker["execution_binding"]
        settlement_ref = settlement.settlement_event_ref
    finally:
        store.close()
        ledger.close()

    provider = _ResponseHTTP(text="我想先把这件小事记在心里。")
    model = _model(provider)
    app = build_app(path, model, ecology=True)
    try:
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
        (experience,) = evidence.projection.experiences
        (source,) = experience.values.source_bindings
        assert source.settlement.authority_event_ref == settlement_ref
        assert source.response.response_text == provider.text
        assert len(provider.stimulus_requests) == 1
        await app.drain_background_once()
        assert len(provider.stimulus_requests) == 1
    finally:
        await app.aclose()
        await model.aclose()
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        _, _, snapshot = current_context(ledger, store, actor_ref=ACTOR)
        assert raw["authorized_attempt_result"]["text"] in snapshot.materials_json
        # The bounded current Capsule need not repeat every inner response;
        # its exact accepted Experience source was checked above.
        assert ledger.project().experiences == (experience,)
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_completed_attempt_retries_new_draft_after_orphaned_sidecars_and_restart(
    tmp_path, monkeypatch, build_app,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "completed-cas-retry.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "completed")
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
                    proposal_id=runtime._completed_activity_proposal_id(terminal_ref),
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
        failed = await runtime.advance_completed_activity_once(
            completion_event_ref=terminal_ref, wake_event_ref=wake.event_id,
            trace_id="trace:cas-failure", correlation_id="completed-test",
        )
        assert failed.reason_code == "life_development.acceptance_prefix_stale"
        assert orphan_refs and not ledger.project().world_occurrences
        assert runtime.pending_completed_activity_ref() == terminal_ref
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
        recovered = await runtime.advance_completed_activity_once(
            completion_event_ref=terminal_ref, wake_event_ref=retry.event_id,
            trace_id="trace:cas-retry", correlation_id="completed-test",
        )
        assert recovered.status == "occurrence_committed" and author.calls == 1
        assert len(ledger.project().world_occurrences) == 1
        assert runtime.pending_completed_activity_ref() is None
        assert all(store.read_exact(content_ref=ref) is not None for ref in orphan_refs)
        evidence = ledger.export_replay_evidence()
        assert evidence.projection.semantic_hash == evidence.replay.semantic_hash
    finally:
        store.close()
        ledger.close()



@pytest.mark.asyncio
async def test_completion_background_budget_and_failed_attempt_retry_use_existing_gates(
    tmp_path, monkeypatch, build_app,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "completed-retry.sqlite"
    plan = await _day_activity(path, build_app, monkeypatch, "completed")
    terminal_ref = plan.authority_origin.accepted_event_ref
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        author = _SequenceModel(model="fixture:retry", outputs=(httpx.ReadError("offline failure"),))
        runtime = _author_runtime(ledger, store, author)
        old_clock = next(row.event for row in ledger.export_replay_evidence().events
                         if row.event.event_type == "ClockAdvanced")
        stale = await runtime.advance_completed_activity_once(
            completion_event_ref=terminal_ref, wake_event_ref=old_clock.event_id,
            trace_id="trace:old-clock", correlation_id="completed-test",
        )
        assert stale.reason_code == "life_development.completion_requires_current_clock"
        assert stale.status == "stale_prefix" and author.calls == 0
        wake = _wake(ledger, "paused")
        result = await _advance(_ecology(ledger, runtime, paused=True), wake)
        assert result.reason_code == "life_ecology.paused_by_budget" and author.calls == 0
        assert runtime.pending_completed_activity_ref() == terminal_ref
        retry = _wake(ledger, "first-failure")
        failed = await runtime.advance_completed_activity_once(
            completion_event_ref=terminal_ref, wake_event_ref=retry.event_id,
            trace_id="trace:failure", correlation_id="completed-test",
        )
        assert failed.status == "technical_failure"
        assert not ledger.project().world_occurrences
        assert runtime.pending_completed_activity_ref() == terminal_ref
        resumed = _wake(ledger, "retry")
        recovered_author = _SequenceModel(model="fixture:retry", outputs=('{"decision":"no_op"}',))
        recovered = _author_runtime(ledger, store, recovered_author)
        result = await recovered.advance_completed_activity_once(
            completion_event_ref=terminal_ref, wake_event_ref=resumed.event_id,
            trace_id="trace:retry", correlation_id="completed-test",
        )
        assert result.status == "no_op"
        assert recovered_author.calls == 1
        assert recovered.pending_completed_activity_ref() is None
        assert not ledger.project().world_occurrences
    finally:
        store.close()
        ledger.close()
