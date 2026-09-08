"""Separate World consequence and character reading through public SQLite reads."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from companion_daemon.world_v2.life_content import LifeContentCompiler
from companion_daemon.world_v2.life_content_store import (
    SQLiteImmutableLifeContentStore,
)
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.world_life_context import WorldLifeContextCompiler
from test_world_stimulus_life_response import _ResponseHTTP, _settled
from test_world_stimulus_life_intent import ACTOR, WORLD, _build, _model


def _cursor(state):
    return ProjectionCursor(
        world_revision=state.world_revision,
        deliberation_revision=state.deliberation_revision,
        ledger_sequence=state.ledger_sequence,
    )


@pytest.mark.asyncio
async def test_public_settlement_reads_structured_environment_without_raw_json(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "readers.sqlite"
    provider = _ResponseHTTP()
    model = _model(provider)
    app = _build(path, model)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        source = await _settled(app)
        state = app.export_replay_evidence().projection
        compiled = LifeContentCompiler(store=store)
        content = compiled.compile(
            cursor=_cursor(state),
            actor_ref=ACTOR,
            viewer_privacy_ceiling="private",
            projection=state,
        ).settled_items
        assert len(content) == 1
        assert content[0].text is None
        assert content[0].world_consequence.environment.text == "一阵短雨已经停了。"
        assert content[0].world_consequence.authorized_attempt_result is None
        assert content[0].character_response is None
        view = WorldLifeContextCompiler(life_content=compiled).compile(
            projection=state, actor_ref=ACTOR, cursor=_cursor(state),
            viewer_privacy_ceiling="private"
        )
        assert len(view) == 1
        assert view[0].source.authority_event_ref == source.event_id
        assert view[0].content == content[0]
        assert len(provider.stimulus_requests) == 0
    finally:
        store.close()
        app.close()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [None, "我觉得安静些了，但我还不知道外面的人去了哪里。"])
async def test_public_composite_reader_keeps_both_authors_and_explicit_null(
    tmp_path, monkeypatch, text
):
    from test_character_life_experience_runtime import _accepted_response
    from companion_daemon.world_v2.character_life_experience_runtime import (
        CharacterLifeExperienceRuntime,
    )
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "composite.sqlite"
    _, response_ref = await _accepted_response(path, text)
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        runtime = CharacterLifeExperienceRuntime(
            ledger=ledger, content_store=store, owner_actor_ref=ACTOR,
        )
        experience_id = runtime.accept(
            world_id=WORLD, audit_cursor=_cursor(ledger.project()),
            response_event_ref=response_ref,
        )
        state = ledger.project()
        before = ledger.export_replay_evidence()
        result = LifeContentCompiler(store=store).compile(
            cursor=_cursor(state), projection=state, actor_ref=ACTOR,
            viewer_privacy_ceiling="private",
        )
        assert not result.suppressions
        assert len(result.experience_items) == 1
        item = result.experience_items[0]
        assert item.experience_id == experience_id
        assert item.content.text is None
        assert item.content.world_consequence.environment.text == "一阵短雨已经停了。"
        assert item.content.character_response.response_text == text
        assert item.content.character_response.source_event_ref == response_ref
        assert item.content.character_response.epistemic_scope == "private_interpretation_not_world_fact"
        from test_current_activity_context import current_context
        from companion_daemon.world_v2.expression_draft import world_claim_source_refs_by_scope
        from companion_daemon.world_v2.recall_corpus import RecallCorpusCompiler, RecallCorpusSources
        from companion_daemon.world_v2.recall_index import RecallCursor

        capsule, context, snapshot = current_context(ledger, store, actor_ref=ACTOR)
        assert capsule.recent_experiences.items
        visible = snapshot.materials["recent_self_experiences"]["items"]
        paired = next(value for value in visible if value.get("experience_id") == experience_id)
        assert paired["content"]["character_response"]["response_text"] == text
        assert paired["content"]["world_consequence"]["environment"]["text"] == "一阵短雨已经停了。"
        wrapper_refs = {
            item["source_ref"] for item in context["slices"]["recent_experiences"]["items"]
        }
        scopes = world_claim_source_refs_by_scope(context=context)
        assert wrapper_refs.isdisjoint(scopes["past_world"])
        assert wrapper_refs.isdisjoint(scopes["shared_history"])
        settlement_ref = item.values.source_bindings[0].settlement.authority_event_ref
        assert settlement_ref in scopes["past_world"]
        worlds = WorldLifeContextCompiler(life_content=LifeContentCompiler(store=store)).compile(
            projection=state, actor_ref=ACTOR, cursor=_cursor(state),
            viewer_privacy_ceiling="private",
        )
        corpus = RecallCorpusCompiler().compile(
            sources=RecallCorpusSources(recent_experiences=result.experience_items, world_life=worlds),
            cursor=RecallCursor(**_cursor(state).model_dump()), actor_ref=ACTOR,
            subject_refs=(ACTOR,),
        )
        assert [doc.text for doc in corpus if doc.memory_kind == "episodic"] == ["一阵短雨已经停了。"]
        reflective = [doc for doc in corpus if doc.memory_kind == "reflective"]
        assert [doc.text for doc in reflective] == ([] if text is None else [text])
        assert all(doc.authority == "defeasible_interpretation" for doc in reflective)
        assert ledger.export_replay_evidence() == before
    finally:
        store.close()
        ledger.close()
    reopened = SQLiteWorldLedger(path=path, world_id=WORLD)
    restored_store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        restored = LifeContentCompiler(store=restored_store).compile(
            cursor=_cursor(reopened.project()), projection=reopened.project(),
            actor_ref=ACTOR, viewer_privacy_ceiling="private",
        )
        assert restored == result
        assert reopened.export_replay_evidence() == before
    finally:
        restored_store.close()
        reopened.close()


@pytest_asyncio.fixture
async def composite(tmp_path, monkeypatch):
    from test_character_life_experience_runtime import _accepted_response
    from companion_daemon.world_v2.character_life_experience_runtime import CharacterLifeExperienceRuntime
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "composite.sqlite"
    _, response_ref = await _accepted_response(path, "我觉得这场变化让我安静了些。")
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        identifier = CharacterLifeExperienceRuntime(
            ledger=ledger, content_store=store, owner_actor_ref=ACTOR,
        ).accept(world_id=WORLD, audit_cursor=_cursor(ledger.project()), response_event_ref=response_ref)
        yield ledger, store, next(x for x in ledger.project().experiences if x.experience_id == identifier)
    finally:
        store.close()
        ledger.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "summary_body", "world_body", "world_missing", "world_kind", "descriptor_missing",
    "wrong_response_hash", "other_actor", "ceiling", "withhold", "limited_world",
    "limited_candidate", "selected_withhold", "selected_ceiling",
])
async def test_structured_reader_rejects_incomplete_or_unreadable_source(composite, fault):
    from companion_daemon.world_v2.life_content_reading import read_character_life_experience_content
    ledger, store, experience = composite
    state = ledger.project()
    binding = experience.values.source_bindings[0]
    occurrence = next(x for x in state.world_occurrences if x.occurrence_id == binding.settlement.occurrence_id)
    candidate = next(x for x in occurrence.candidate_outcomes if x.candidate_result_ref == occurrence.settled_outcome_ref)

    class SourceReader:
        def read_exact(self, *, content_ref):
            original = store.read_exact(content_ref=content_ref)
            if content_ref == candidate.content_ref:
                if fault == "world_missing":
                    return None
                if fault in {"world_body", "world_kind"}:
                    return SimpleNamespace(
                        content_ref=original.content_ref,
                        content_kind="raw_model_result" if fault == "world_kind" else original.content_kind,
                        content_payload_hash=original.content_payload_hash,
                        text='{"contract":"world-consequence.2","environment_text":"别的变化。"}'
                        if fault == "world_body" else original.text,
                    )
            if content_ref == experience.values.summary_ref and fault == "summary_body":
                return SimpleNamespace(
                    content_ref=original.content_ref, content_kind=original.content_kind,
                    content_payload_hash=original.content_payload_hash, text="{}",
                )
            return original

    if fault == "descriptor_missing":
        state = state.model_copy(update={"life_content_descriptors": tuple(
            x for x in state.life_content_descriptors if x.source_kind != "experience"
        )})
    if fault in {"wrong_response_hash", "withhold"}:
        values = experience.values.model_copy(update=(
            {"source_bindings": (binding.model_copy(update={"response_payload_hash": "0" * 64}),)}
            if fault == "wrong_response_hash" else {"privacy_class": "withhold"}
        ))
        experience = experience.model_copy(update={"values": values})
        state = state.model_copy(update={"experiences": (experience,)})
    if fault in {"selected_withhold", "selected_ceiling"}:
        updated = candidate.model_copy(update={
            "privacy_class": "withhold" if fault == "selected_withhold" else "private",
        })
        occurrence = occurrence.model_copy(update={"candidate_outcomes": tuple(
            updated if item == candidate else item for item in occurrence.candidate_outcomes
        )})
        state = state.model_copy(update={"world_occurrences": (occurrence,)})
    before = ledger.export_replay_evidence()
    with pytest.raises(ValueError):
        read_character_life_experience_content(
            store=SourceReader(), projection=state, experience=experience,
            actor_ref="actor:other" if fault == "other_actor" else ACTOR,
            viewer_privacy_ceiling="shareable" if fault in {"ceiling", "selected_ceiling"} else "private",
            user_channel_limited_content_refs=(
                frozenset({occurrence.result_payload_ref}) if fault == "limited_world"
                else frozenset({candidate.content_ref}) if fault == "limited_candidate" else frozenset()
            ),
        )
    assert ledger.export_replay_evidence() == before


