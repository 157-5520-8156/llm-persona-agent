"""Public retention must accept the newly hardened Experience source."""

import pytest

from test_world_life_response_readers import ACTOR, _cursor, composite as composite


@pytest.mark.asyncio
async def test_retained_composite_memory_returns_structure_without_promoting_response(composite):
    from companion_daemon.world_v2.experience_memory_candidate_lifecycle import ExperienceMemoryCandidateLifecycle
    from companion_daemon.world_v2.fact_memory_draft import FactMemoryRetentionDraft
    from companion_daemon.world_v2.memory_retrieval import MemoryRetrievalCompiler
    from companion_daemon.world_v2.schemas import MEMORY_SALIENCE_MATRIX_DIGEST
    ledger, store, experience = composite
    state = ledger.project()
    event, _ = ledger.lookup_event_commit(experience.origin.accepted_event_ref)
    source = next(x for x in state.committed_world_event_refs if x.event_id == event.event_id)
    transition = next(x for x in state.experience_transitions if x.experience_id == experience.experience_id)
    draft = FactMemoryRetentionDraft.model_validate({
        "cue_kind": "world_continuity", "retention_rationales": ("world_continuity",),
        "salience": {**dict.fromkeys((
            "autobiographical_relevance_bp", "relationship_relevance_bp", "emotional_residue_bp",
            "unfinished_business_bp", "recurrence_bp", "novelty_bp", "future_utility_bp",
            "world_continuity_bp",
        ), 5000), "matrix_digest": MEMORY_SALIENCE_MATRIX_DIGEST},
    })
    candidate = ExperienceMemoryCandidateLifecycle(
        ledger=ledger, content_store=store, actor=ACTOR, source="test:retention-choice",
    ).accept(
        experience=experience, transition=transition, experience_event=event,
        experience_world_revision=source.world_revision, draft=draft,
        logical_time=state.logical_time, created_at=state.logical_time,
        trace_id="trace:retention", correlation_id="reader",
    )
    assert candidate is not None
    before = ledger.export_replay_evidence()
    result = MemoryRetrievalCompiler(ledger=ledger, life_content_store=store).compile(
        cursor=_cursor(ledger.project()), candidates=(candidate,), viewer_privacy_ceiling="private",
    )
    assert not result.suppressions
    excerpt = result.items[0].source_excerpts[0]
    assert excerpt.text is None
    assert excerpt.character_response.response_text == experience.values.source_bindings[0].response.response_text
    assert excerpt.world_consequence.environment.epistemic_scope == "settled_world_environment"
    assert ledger.export_replay_evidence() == before
