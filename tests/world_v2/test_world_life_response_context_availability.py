"""Missing paired source content is unavailable, not an empty life history."""

from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.context_resolver import query_from_projection
from companion_daemon.world_v2.ledger_context_resolver import context_capsule_compiler_from_ledger
from test_world_life_response_secondary_readers import _paired
from test_world_stimulus_life_intent import ACTOR


@pytest.mark.asyncio
@pytest.mark.parametrize("corrupt_world", [False, True])
async def test_context_preserves_paired_experience_content_failure(tmp_path, monkeypatch, corrupt_world):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    async with _paired(tmp_path / "context.sqlite") as (ledger, store, experience, provider):
        projection = ledger.project()
        candidate = projection.world_occurrences[0].candidate_outcomes[0]
        before = ledger.export_replay_evidence()
        calls = len(provider.requests)
        reader = store
        if corrupt_world:
            reader = SimpleNamespace(read_exact=lambda *, content_ref: (
                SimpleNamespace(content_ref=content_ref, content_kind="outcome_candidate",
                                content_payload_hash=candidate.content_payload_hash,
                                text="different bytes with the original hash")
                if content_ref == candidate.content_ref else store.read_exact(content_ref=content_ref)
            ))
        capsule = context_capsule_compiler_from_ledger(
            ledger=ledger, life_content_store=reader,
        ).compile(query_from_projection(
            projection, actor_ref=ACTOR, trigger_ref=experience.origin.accepted_event_ref,
        ))
        assert capsule.recent_experiences.availability == (
            "unavailable" if corrupt_world else "available"
        )
        assert bool(capsule.recent_experiences.items) is not corrupt_world
        assert ledger.export_replay_evidence() == before
        assert len(provider.requests) == calls
