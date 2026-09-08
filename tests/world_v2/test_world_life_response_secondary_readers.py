"""Public two-author sources stay separate in NPC and reflection consumers."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager, nullcontext
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.character_life_experience_runtime import CharacterLifeExperienceRuntime
from companion_daemon.world_v2.companion_identity import CompanionIdentityFrame
from companion_daemon.world_v2.event_identity import domain_idempotency_key
from companion_daemon.world_v2.life_content_store import (
    SQLiteImmutableLifeContentStore, StoredLifeContent, life_content_payload_hash,
)
from companion_daemon.world_v2.npc_identity_view import npc_identity_views
from companion_daemon.world_v2.private_impression_producer import compile_private_impression_reflection_capsule
from companion_daemon.world_v2.schemas import NpcProjection, WorldEvent
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_world_life_response_readers import _cursor
from test_world_stimulus_life_response import _ResponseHTTP, _settled
from test_world_stimulus_life_intent import ACTOR, WORLD, _build, _model


RESPONSE = "我把这点安静留给自己，还不想解释它。"
NPC = "npc:reader"


def _register_npc(ledger, store):
    """Operator identity seed only; later settlement/Experience uses production."""
    npc = NpcProjection(
        npc_id="reader", entity_revision=1, stable_identity_ref="content:npc:reader",
        privacy_class="personal",
    )
    store.put_if_absent(StoredLifeContent(
        content_ref=npc.stable_identity_ref, content_kind="provisional_npc_introduction",
        text="同在现场的一位熟人。", content_payload_hash=life_content_payload_hash("同在现场的一位熟人。"),
    ))
    payloads = (
        ("OperatorObservationRecorded", {"observation_id": "operator:reader", "observation_hash": "b" * 64}),
        ("NpcRegistered", {
            "change_id": "change:reader", "transition_id": "transition:reader",
            "expected_entity_revision": 0,
            "evidence_refs": [{"ref_id": "operator:reader", "evidence_type": "operator_observation",
                               "claim_purpose": "current_fact", "immutable_hash": "b" * 64}],
            "policy_refs": ["policy:life-v1"], "npc": npc.model_dump(mode="json"),
        }),
    )
    for kind, payload in payloads:
        state = ledger.project()
        event = WorldEvent.from_payload(
            schema_version="world-v2.1", event_id="event:reader:" + kind, world_id=WORLD,
            event_type=kind, logical_time=state.logical_time, created_at=state.logical_time,
            actor="operator:fixture", source="test:reader", trace_id="trace:reader",
            causation_id="cause:reader", correlation_id="reader",
            idempotency_key=domain_idempotency_key(event_type=kind, world_id=WORLD, payload=payload)
                or "key:reader:" + kind,
            payload=payload,
        )
        ledger.commit_at_cursor([event], expected_cursor=_cursor(state))


@asynccontextmanager
async def _paired(path, *, npc=False, public_occurrence=False):
    provider = _ResponseHTTP(text=RESPONSE, appraise=True)
    model = _model(provider)
    app = _build(path, model)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        if npc:
            _register_npc(ledger, store)

        class Input:
            def __getattr__(self, name):
                return getattr(app, name)

            async def commit_occurrence(self, request):
                if npc or public_occurrence:
                    request = request.model_copy(update={"occurrence": request.occurrence.model_copy(
                        update={
                            "participant_refs": (ACTOR, NPC) if npc else (ACTOR,),
                            "visibility": "public" if public_occurrence else "private",
                        },
                    )})
                return await app.commit_occurrence(request)

        await _settled(Input())
        await app.drain_background_once()
        response = next(item for item in ledger.project().committed_world_event_refs
                        if item.event_type == "CharacterLifeResponseRecorded")
        identifier = CharacterLifeExperienceRuntime(
            ledger=ledger, content_store=store, owner_actor_ref=ACTOR,
        ).accept(world_id=WORLD, audit_cursor=_cursor(ledger.project()), response_event_ref=response.event_id)
        assert len(provider.stimulus_requests) == 1
        yield ledger, store, next(item for item in ledger.project().experiences if item.experience_id == identifier), provider
    finally:
        store.close()
        ledger.close()
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_world", "withhold_world"])
async def test_npc_reads_only_published_shared_world_and_never_companion_response(tmp_path, monkeypatch, fault):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    async with _paired(tmp_path / "npc.sqlite", npc=True) as (ledger, store, experience, provider):
        state = ledger.project()
        occurrence = state.world_occurrences[0]
        candidate = occurrence.candidate_outcomes[0]
        before = ledger.export_replay_evidence()
        calls = len(provider.requests)
        reader = store
        if fault == "missing_world":
            reader = SimpleNamespace(read_exact=lambda *, content_ref: (
                None if content_ref == candidate.content_ref else store.read_exact(content_ref=content_ref)
            ))
        elif fault == "withhold_world":
            candidate = candidate.model_copy(update={"privacy_class": "withhold"})
            occurrence = occurrence.model_copy(update={"candidate_outcomes": (candidate,)})
            state = state.model_copy(update={"world_occurrences": (occurrence,)})
        view, = npc_identity_views(state, content_store=reader)
        visible = view.model_dump_json()
        assert RESPONSE not in visible
        assert "character_response" not in visible
        assert experience.experience_id not in view.shared_experience_refs
        assert experience.origin.accepted_event_ref not in view.source_refs
        assert "一阵短雨已经停了。" in visible if fault is None else "一阵短雨已经停了。" not in visible
        if fault is None:
            assert occurrence.settlement_event_ref in view.source_refs
            assert view.shared_experience_summaries
            assert json.loads(view.shared_experience_summaries[0])["environment"]["epistemic_scope"] == "settled_world_environment"
        assert ledger.export_replay_evidence() == before
        assert len(provider.requests) == calls


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [None, "missing_port", "corrupt_world", "foreign_actor", "withhold"])
async def test_private_reflection_reads_two_exact_authors_or_rejects_unreadable_source(tmp_path, monkeypatch, fault):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    async with _paired(tmp_path / "reflection.sqlite") as (ledger, store, experience, provider):
        state = ledger.project()
        before = ledger.export_replay_evidence()
        calls = len(provider.requests)
        reader = store
        candidate = state.world_occurrences[0].candidate_outcomes[0]
        if fault == "corrupt_world":
            reader = SimpleNamespace(read_exact=lambda *, content_ref: (
                SimpleNamespace(content_ref=content_ref, content_kind="outcome_candidate",
                                content_payload_hash=candidate.content_payload_hash, text="wrong bytes")
                if content_ref == candidate.content_ref else store.read_exact(content_ref=content_ref)
            ))
        elif fault == "withhold":
            changed = experience.model_copy(update={"values": experience.values.model_copy(update={"privacy_class": "withhold"})})
            state = state.model_copy(update={"experiences": (changed,)})
        with pytest.raises(ValueError) if fault is not None else nullcontext():
            capsule = compile_private_impression_reflection_capsule(
                projection=state, appraisal=state.appraisals[0], world_id=WORLD,
                identity_frame=CompanionIdentityFrame(companion_name="角色", counterpart_name="对方"),
                content_reader=lambda ref: store.read_exact(content_ref=ref).text,
                life_content_store=None if fault == "missing_port" else reader,
                companion_actor_ref="actor:other" if fault == "foreign_actor" else ACTOR,
            )
            experiences = [item for item in capsule.sources if item.source_kind == "experience"]
            if fault is None:
                item, = experiences
                value = json.loads(item.value_json)
                assert value["character_response"]["response_text"] == RESPONSE
                assert value["character_response"]["epistemic_scope"] == "private_interpretation_not_world_fact"
                assert value["world_consequence"]["environment"]["text"] == "一阵短雨已经停了。"
                assert "summary_text" not in value
        assert ledger.export_replay_evidence() == before
        assert len(provider.requests) == calls


@pytest.mark.asyncio
async def test_world_life_and_recall_keep_selected_privacy_floor(tmp_path, monkeypatch):
    from companion_daemon.world_v2.life_content import LifeContentCompiler
    from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler
    from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources
    from companion_daemon.world_v2.recall_index import RecallCursor
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    async with _paired(tmp_path / "privacy.sqlite", public_occurrence=True) as (ledger, store, _, provider):
        state = ledger.project()
        assert state.world_occurrences[0].visibility == "public"
        assert state.world_occurrences[0].candidate_outcomes[0].privacy_class == "private"
        before = ledger.export_replay_evidence()
        compiler = WorldLifeContextCompiler(life_content=LifeContentCompiler(store=store))
        worlds = compiler.compile(projection=state, actor_ref=ACTOR, cursor=_cursor(state),
                                  viewer_privacy_ceiling="private")
        world, = worlds
        assert world.privacy_class == world.content.privacy_class == "private"
        assert compiler.compile(projection=state, actor_ref=ACTOR, cursor=_cursor(state),
                                viewer_privacy_ceiling="shareable") == ()
        corpus = RecallCorpusCompiler().compile(
            sources=RecallCorpusSources(world_life=worlds), actor_ref=ACTOR, subject_refs=(ACTOR,),
            cursor=RecallCursor(**_cursor(state).model_dump()),
        )
        assert len(corpus) == 1 and corpus[0].privacy_class == "private"
        assert ledger.export_replay_evidence() == before
        assert len(provider.stimulus_requests) == 1


@pytest.mark.parametrize("malformed", [False, True])
def test_candidate_reader_clips_fields_without_cutting_carrier_json(malformed):
    from companion_daemon.world_v2.life_content_store import InMemoryImmutableLifeContentStore
    from companion_daemon.world_v2.outcome_candidate_reader import OutcomeCandidateReader
    from companion_daemon.world_v2.schemas import OutcomeCandidateDescriptor
    from test_outcome_candidate_reader import _occurrence
    text = json.dumps({"contract": "world-consequence.2", "environment_text": "雨停后的屋檐。" * 100},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if malformed:
        text = text[:480]
    digest = life_content_payload_hash(text)
    candidate = OutcomeCandidateDescriptor(
        candidate_result_ref="candidate:reader", result_id="result:reader",
        result_payload_ref="payload:reader", result_payload_hash=digest,
        content_ref="content:reader", content_payload_hash=digest, privacy_class="private",
        result_contract="world-consequence.2",
    )
    store = InMemoryImmutableLifeContentStore()
    store.put_if_absent(StoredLifeContent(content_ref=candidate.content_ref,
                                        content_kind="outcome_candidate", content_payload_hash=digest, text=text))
    result = OutcomeCandidateReader(store=store).read(occurrence=_occurrence(candidate), viewer_privacy_ceiling="private")
    if malformed:
        assert not result.candidates
        assert result.suppressions[0].reason == "structured_content_unavailable"
    else:
        item, = result.candidates
        assert item.text is None
        assert len(item.world_consequence.environment.text) == 480
        assert item.world_consequence.environment.truncated
        assert item.world_consequence.environment.epistemic_scope == "candidate_world_environment"
        assert "text" not in json.loads(item.model_dump_json())


def test_legacy_advisory_wire_has_no_new_carrier_fields():
    from companion_daemon.world_v2.context_capsule import InnerAdvisoryCandidate
    assert InnerAdvisoryCandidate(
        candidate_ref="candidate:legacy", value="旧候选原文", weight_bp=5000, confidence_bp=6000,
    ).model_dump_json() == '{"candidate_ref":"candidate:legacy","value":"旧候选原文","weight_bp":5000,"confidence_bp":6000}'
