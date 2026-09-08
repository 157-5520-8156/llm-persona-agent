"""Two-author Experience from the installed SQLite/HTTP response producer."""

from __future__ import annotations

import json

import pytest

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.schemas import ProjectionCursor
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from test_world_stimulus_life_intent import ACTOR, WORLD, _build, _model
from test_world_stimulus_life_response import _ResponseHTTP, _settled


def _cursor(ledger):
    p = ledger.project()
    return ProjectionCursor(
        world_revision=p.world_revision,
        deliberation_revision=p.deliberation_revision,
        ledger_sequence=p.ledger_sequence,
    )


async def _accepted_response(path, text):
    provider = _ResponseHTTP(text=text)
    model = _model(provider)
    app = _build(path, model)
    try:
        source = await _settled(app)
        await app.drain_background_once()
        p = app.export_replay_evidence().projection
        response = next(
            x
            for x in p.committed_world_event_refs
            if x.event_type == "CharacterLifeResponseRecorded"
        )
        assert len(provider.stimulus_requests) == 1
    finally:
        await app.aclose()
        await model.aclose()
    return source.event_id, response.event_id


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [None, "我听着雨后的动静，心里松了一点。"])
async def test_public_response_composes_exact_two_author_experience(tmp_path, monkeypatch, text):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "world.sqlite"
    source_ref, response_ref = await _accepted_response(path, text)
    from companion_daemon.world_v2.character_life_experience_runtime import (
        CharacterLifeExperienceRuntime,
    )

    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        runtime = CharacterLifeExperienceRuntime(
            ledger=ledger,
            content_store=store,
            owner_actor_ref=ACTOR,
        )
        experience_id = runtime.accept(
            world_id=WORLD,
            audit_cursor=_cursor(ledger),
            response_event_ref=response_ref,
        )
        p = ledger.project()
        experience = next(x for x in p.experiences if x.experience_id == experience_id)
        assert experience.authority_contract_version == "experience.2"
        (binding,) = experience.values.source_bindings
        assert binding.source_kind == "world_life_response"
        assert binding.settlement.authority_event_ref == source_ref
        assert binding.response_event_ref == response_ref
        assert binding.response.response_text == text
        body = store.read_exact(content_ref=experience.values.summary_ref)
        summary = json.loads(body.text)
        assert summary["character_response"]["response_text"] == text
        assert summary["world_consequence"]["authority_event_ref"] == source_ref
        assert experience.values.privacy_class == "private"
        before = ledger.export_replay_evidence()
        assert (
            runtime.accept(
                world_id=WORLD, audit_cursor=_cursor(ledger), response_event_ref=response_ref
            )
            == experience_id
        )
        assert ledger.export_replay_evidence() == before
    finally:
        store.close()
        ledger.close()
