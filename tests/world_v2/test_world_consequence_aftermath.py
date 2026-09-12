"""World Author to due settlement before the character authors any response.

All ledger effects use public runtimes. Author/critic verdicts are provider
fixtures; they do not establish the real critic's semantic accuracy.
"""

from datetime import timedelta
import json
from pathlib import Path

import pytest

from companion_daemon.world_v2.life_aftermath_runtime import LifeAftermathRuntime
from companion_daemon.world_v2.life_author_seed import ReviewedLifeSeedCatalog
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.local_chronology import LocalChronology
from companion_daemon.world_v2.occurrence_content_coordinator import OccurrenceContentCoordinator
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_life_development_runtime import (
    NOW,
    OWNER,
    WORLD_ID,
    _SequenceModel,
    _novel_origin_review,
    _source_closure_review,
    _seed_clock,
)
from test_world_author_request_audit import _ReceivedAuthor, _json
from test_world_consequence_producer import _advance, _draft, _runtime


class _NoCharacterCalls:
    def __init__(self):
        self.calls = 0

    async def consider(self, opportunity):
        self.calls += 1
        raise AssertionError("world contingency settlement cannot author a character response")


def _aftermath(ledger, store, character):
    catalog = ReviewedLifeSeedCatalog.from_yaml(
        path=Path(__file__).resolve().parents[2] / "configs/world_seed.yaml",
        chronology=LocalChronology("Asia/Shanghai"),
    )
    return LifeAftermathRuntime(
        ledger=ledger,
        catalog=catalog,
        occurrence_content=OccurrenceContentCoordinator(ledger=ledger, store=store),
        content_store=store,
        owner_actor_ref=OWNER,
        character_interior=character,
    )


def _assert_no_character_experience(projection):
    assert projection.experiences == ()
    assert projection.plans == ()
    assert projection.memory_candidates == ()
    assert not any(
        item.event_type
        in {
            "CharacterLifeResponseRecorded",
            "ExperienceCommitted",
            "ExperienceMemoryDecisionRecorded",
        }
        for item in projection.committed_world_event_refs
    )


def _assert_published_result(ledger, store, occurrence, expected):
    assert occurrence.status == "settled"
    selected = next(
        item
        for item in occurrence.candidate_outcomes
        if item.candidate_result_ref == occurrence.settled_outcome_ref
    )
    assert selected.result_contract == "world-consequence.2"
    candidate = store.read_exact(content_ref=selected.content_ref)
    result = store.read_exact(content_ref=occurrence.result_payload_ref)
    assert candidate is not None and result is not None
    assert candidate.content_kind == "outcome_candidate"
    assert result.content_kind == "occurrence_result"
    assert result.text == candidate.text
    assert result.text in {_json(item["world_consequence"]) for item in expected["outcomes"]}
    assert result.content_payload_hash == occurrence.result_payload_hash.removeprefix("sha256:")
    descriptors = [
        item
        for item in ledger.project().life_content_descriptors
        if item.source_event_ref == occurrence.settlement_event_ref
        and item.content_kind == "occurrence_result"
    ]
    assert len(descriptors) == 1
    descriptor = descriptors[0]
    assert descriptor.content_ref == result.content_ref
    assert descriptor.content_payload_hash == result.content_payload_hash
    assert descriptor.source_world_revision == occurrence.settlement_world_revision
    assert descriptor.source_payload_hash == occurrence.settlement_payload_hash
    return result.text


async def _settled_author_cohort(path, *, world_id=WORLD_ID, start=NOW):
    ledger = SQLiteWorldLedger(path=path, world_id=world_id)
    store = SQLiteImmutableLifeContentStore(path=path, world_id=world_id)
    character = _NoCharacterCalls()
    try:
        wake = _seed_clock(
            ledger,
            logical_time=start,
            logical_time_from=ledger.project().logical_time,
        )
        draft = _draft(wake)
        author = _ReceivedAuthor(store, (_json(draft),))
        general = _SequenceModel(model="fixture:general", outputs=(_source_closure_review(decision="supported"),))
        focused = _SequenceModel(
            model="fixture:focused", outputs=(_novel_origin_review(decision="supported"),)
        )
        producer = _runtime(ledger, store, wake, author, general, focused)
        admitted = await _advance(producer, wake)
        assert admitted.status == "occurrence_committed"
        occurrence = ledger.project().world_occurrences[0]
        assert occurrence.status == "active"
        assert occurrence.time_window.closes_at == start + timedelta(minutes=20)
        _assert_no_character_experience(ledger.project())
        aftermath = _aftermath(ledger, store, character)
        early = _seed_clock(
            ledger,
            event_id="event:clock:consequence:19m",
            logical_time=start + timedelta(minutes=19),
            logical_time_from=start,
        )
        early_result = await _advance(aftermath, early)
        assert early_result.status == "no_op"
        assert ledger.project().world_occurrences[0].status == "active"
        assert not any(
            item.event_type == "WorldOccurrenceSettled"
            for item in ledger.project().committed_world_event_refs
        )
        _assert_no_character_experience(ledger.project())
        due = _seed_clock(
            ledger,
            event_id="event:clock:consequence:20m",
            logical_time=start + timedelta(minutes=20),
            logical_time_from=early.logical_time,
        )
        settled = await _advance(aftermath, due)
        assert settled.status == "settled"
        assert settled.experience_id is None
        occurrence = ledger.project().world_occurrences[0]
        text = _assert_published_result(ledger, store, occurrence, draft)
        _assert_no_character_experience(ledger.project())
        types = [item.event_type for item in ledger.project().committed_world_event_refs]
        assert types.count("WorldOccurrenceSettled") == 1
        assert types.count("RandomDrawRecorded") >= 1
        outcome_events = ledger.recent_events_by_type(
            event_types=frozenset({"OutcomeProposalRecorded"}),
            since=start,
            limit=10,
        )
        assert len(outcome_events) == 1
        assert outcome_events[0].payload()["decision_authority"] == "recorded_world_draw"
        assert outcome_events[0].payload()["decision_model_result_ref"] is None
        assert len(author.received) == focused.calls == 1
        assert general.calls == 1
        assert character.calls == 0
        before = ledger.export_replay_evidence()
        repeated = await _advance(aftermath, due)
        assert repeated.status == "no_op"
        repeated_author = await _advance(producer, wake)
        assert repeated_author.status == admitted.status
        assert repeated_author.occurrence_id == admitted.occurrence_id
        assert repeated_author.proposal_event_ref == admitted.proposal_event_ref
        assert len(author.received) == focused.calls == 1
        assert ledger.export_replay_evidence() == before
        store.close()
        ledger.close()
        ledger = SQLiteWorldLedger(path=path, world_id=world_id)
        store = SQLiteImmutableLifeContentStore(path=path, world_id=world_id)
        cold_character = _NoCharacterCalls()
        assert await _advance(_aftermath(ledger, store, cold_character), due) == repeated
        assert ledger.export_replay_evidence() == before
        later = _seed_clock(
            ledger,
            event_id="event:clock:consequence:21m",
            logical_time=start + timedelta(minutes=21),
            logical_time_from=due.logical_time,
        )
        later_prefix = ledger.export_replay_evidence()
        assert (await _advance(_aftermath(ledger, store, cold_character), later)).status == "no_op"
        assert ledger.export_replay_evidence() == later_prefix
        _assert_no_character_experience(ledger.project())
        assert cold_character.calls == 0
        return occurrence.settlement_event_ref, text
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
async def test_new_author_settles_at_window_end_without_creating_character_experience(tmp_path):
    await _settled_author_cohort(tmp_path / "world.sqlite")


@pytest.mark.asyncio
@pytest.mark.parametrize("response_text", [None, "我听着院里的动静，决定先缓一缓。"])
async def test_same_new_author_settlement_enters_production_role_response_and_experience(
    tmp_path,
    monkeypatch,
    response_text,
):
    """Endpoint composition: fixture WA Context, then the installed role HTTP stack."""
    from test_world_stimulus_life_intent import NOW as ROLE_NOW, WORLD, _build, _model
    from test_world_stimulus_life_response import _ResponseHTTP

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "response-bridge.sqlite"
    bootstrap_provider = _ResponseHTTP(fault="provider_failure")
    bootstrap_model = _model(bootstrap_provider)
    bootstrap = _build(path, bootstrap_model)
    await bootstrap.aclose()
    await bootstrap_model.aclose()
    assert bootstrap_provider.requests == []
    settlement_ref, world_text = await _settled_author_cohort(
        path,
        world_id=WORLD,
        start=ROLE_NOW + timedelta(minutes=1),
    )
    provider = _ResponseHTTP(text=response_text)
    model = _model(provider)
    app = _build(path, model)
    try:
        _assert_no_character_experience(app.export_replay_evidence().projection)
        await app.drain_background_once()
        after = app.export_replay_evidence()
        responses = [
            row.event
            for row in after.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert len(responses) == len(provider.stimulus_requests) == 1
        response = responses[0]
        assert response.payload()["origin"]["source_event_ref"] == settlement_ref
        assert response.payload()["response_text"] == response_text
        assert len(after.projection.experiences) == 1
        experience = after.projection.experiences[0]
        assert experience.authority_contract_version == "experience.2"
        (binding,) = experience.values.source_bindings
        assert binding.settlement.authority_event_ref == settlement_ref
        assert binding.response_event_ref == response.event_id
        assert binding.response.response_text == response_text
        accepted_experience = next(
            row.event
            for row in after.events
            if row.event.event_id == experience.origin.accepted_event_ref
        )
        assert accepted_experience.event_type == "ExperienceCommitted"
        event_ids = [row.event.event_id for row in after.events]
        assert event_ids.index(settlement_ref) < event_ids.index(response.event_id)
        assert event_ids.index(response.event_id) < event_ids.index(accepted_experience.event_id)
        assert experience.values.privacy_class == "private"
        assert after.projection.plans == after.projection.memory_candidates == ()
        body = json.loads(provider.stimulus_requests[0]["messages"][-1]["content"])
        assert body["capability_manifest"]["payload"]["world_life_response"][
            "source_event_refs"
        ] == [settlement_ref]
    finally:
        await app.aclose()
        await model.aclose()

    store = SQLiteImmutableLifeContentStore(path=path, world_id=WORLD)
    try:
        summary = store.read_exact(content_ref=experience.values.summary_ref)
        assert summary is not None and summary.content_kind == "experience_summary"
        paired = json.loads(summary.text)
        assert paired["world_consequence"]["authority_event_ref"] == settlement_ref
        assert paired["character_response"]["response_text"] == response_text
        # Environment bytes retain their own immutable World source.
        world = store.read_exact(content_ref=binding.settlement.result_payload_ref)
        assert world is not None and world.text == world_text
    finally:
        store.close()

    cold_provider = _ResponseHTTP(fault="provider_failure")
    cold_model = _model(cold_provider)
    cold_app = _build(path, cold_model)
    try:
        await cold_app.drain_background_once()
        assert cold_provider.requests == []
        cold = cold_app.export_replay_evidence()
        assert cold.projection.experiences == after.projection.experiences
        assert [
            row.event
            for row in cold.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
        ] == responses
    finally:
        await cold_app.aclose()
        await cold_model.aclose()
