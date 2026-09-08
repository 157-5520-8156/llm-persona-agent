"""Selected consequence privacy at the real producer / role / Experience seam."""

from __future__ import annotations

import pytest

from companion_daemon.world_v2.world_stimulus_choice_authority import (
    world_life_response_capability,
)
from test_world_stimulus_life_intent import ACTOR, NOW, WORLD, _build, _model
from test_world_stimulus_life_response import _ResponseHTTP, _settled


class _PrivacyProducer:
    """Choose proposed privacy before the public producer validates or writes it."""

    def __init__(self, app, visibility, selected_privacy):
        self.app = app
        self.visibility = visibility
        self.selected_privacy = selected_privacy

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def commit_occurrence(self, request):
        return await self.app.commit_occurrence(
            request.model_copy(
                update={
                    "occurrence": request.occurrence.model_copy(
                        update={"visibility": self.visibility}
                    ),
                    "candidate_contents": tuple(
                        candidate.model_copy(
                            update={
                                "privacy_class": self.selected_privacy,
                            }
                        )
                        for candidate in request.candidate_contents
                    ),
                }
            )
        )


@pytest.mark.asyncio
async def test_public_selected_privacy_limits_response_and_experience(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _ResponseHTTP(text="由我自己解释这个变化。")
    model = _model(provider)
    app = _build(tmp_path / "world.sqlite", model)
    try:
        source = await _settled(_PrivacyProducer(app, "public", "private"))
        p = app.export_replay_evidence().projection
        descriptor = next(
            x for x in p.life_content_descriptors if x.source_kind == "occurrence_settlement"
        )
        assert descriptor.privacy_class == "private"
        capability = world_life_response_capability(
            state=p,
            source_events=(source,),
            owner_actor_ref=ACTOR,
        )
        assert capability is not None
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        responses = [
            x.event
            for x in evidence.events
            if x.event.event_type == "CharacterLifeResponseRecorded"
        ]
        assert len(responses) == 1
        (experience,) = evidence.projection.experiences
        assert experience.authority_contract_version == "experience.2"
        assert experience.values.privacy_class == "private"
        summary = next(
            x for x in evidence.projection.life_content_descriptors if x.source_kind == "experience"
        )
        assert summary.privacy_class == "private"
        assert len(provider.stimulus_requests) == 1
    finally:
        await app.aclose()
        await model.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize("visibility", ["private", "withhold"])
async def test_public_world_contingency_withhold_is_not_a_character_response_source(
    tmp_path,
    monkeypatch,
    visibility,
):
    from datetime import timedelta
    import test_world_consequence_aftermath as aftermath

    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    path = tmp_path / "withhold.sqlite"
    bootstrap_model = _model(_ResponseHTTP())
    bootstrap = _build(path, bootstrap_model)
    await bootstrap.aclose()
    await bootstrap_model.aclose()

    original_draft = aftermath._draft

    def private_author_draft(wake):
        draft = original_draft(wake)
        draft["privacy_class"] = visibility
        for outcome in draft["outcomes"]:
            outcome["privacy_class"] = "withhold"
            # Withheld outcomes cannot offer ordinary-life visual evidence.
            outcome["visual_evidence"] = None
        return draft

    with monkeypatch.context() as scoped:
        scoped.setattr(aftermath, "_draft", private_author_draft)
        source_ref, _ = await aftermath._settled_author_cohort(
            path,
            world_id=WORLD,
            start=NOW + timedelta(minutes=1),
        )
    provider = _ResponseHTTP(text="This must not become an authored response to withheld content.")
    model = _model(provider)
    app = _build(path, model)
    try:
        before = app.export_replay_evidence()
        source = next(x.event for x in before.events if x.event.event_id == source_ref)
        (occurrence,) = before.projection.world_occurrences
        assert occurrence.status == "settled"
        assert occurrence.visibility == visibility
        assert {item.privacy_class for item in occurrence.candidate_outcomes} == {"withhold"}
        (descriptor,) = (
            x
            for x in before.projection.life_content_descriptors
            if x.source_kind == "occurrence_settlement"
        )
        assert descriptor.privacy_class == "withhold"
        assert (
            world_life_response_capability(
                state=before.projection,
                source_events=(source,),
                owner_actor_ref=ACTOR,
            )
            is None
        )
        await app.drain_background_once()
        after = app.export_replay_evidence()
        assert not any(x.event.event_type == "CharacterLifeResponseRecorded" for x in after.events)
        assert after.projection.experiences == ()
        assert all(
            "world_life_response" not in request["messages"][-1]["content"]
            for request in provider.stimulus_requests
        )
    finally:
        await app.aclose()
        await model.aclose()
