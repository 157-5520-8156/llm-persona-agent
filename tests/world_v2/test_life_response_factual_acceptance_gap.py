"""Executable release blocker: provenance is not factual acceptance.

The public SQLite runtime and real acceptance reducers remain installed; only
the role HTTP transport is replaced. Positive null/feeling cases are covered by
test_world_stimulus_life_response. These expected failures are NOT qualification
successes. Remove the markers only after source review and same-role correction
are installed and independently qualified for this path.
"""

import pytest

from test_world_stimulus_life_response import _ResponseHTTP, _build, _model, _settled


class UnverifiedLifeEpisodeWasAccepted(AssertionError):
    """Only this known failure is expected; setup/provider failures stay red."""


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=True,
    raises=UnverifiedLifeEpisodeWasAccepted,
    reason="Life response acceptance proves authorship but not embedded facts; "
    "see docs/design/life-response-factual-acceptance-gap-2026-09-15.md",
)
@pytest.mark.parametrize(
    "text",
    [
        "雨停后我出门绕湖走了三圈，回来时买了一杯热咖啡。",
        "想起今天上午我坐在图书馆东侧，心里有一点舍不得。",
    ],
)
async def test_environment_alone_cannot_admit_new_personal_episodes(
    tmp_path, monkeypatch, text
):
    monkeypatch.setenv("COMPANION_DISABLE_DEBUG_USAGE_LEDGER", "1")
    provider = _ResponseHTTP(text=text)
    model = _model(provider)
    app = _build(tmp_path / "factual-acceptance.sqlite", model)
    try:
        source = await _settled(app)
        before = app.export_replay_evidence()
        assert before.projection.plans == before.projection.experiences == ()
        assert not any(
            row.event.event_type in ("CharacterLifeResponseRecorded", "ActivityStarted")
            for row in before.events
        )
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
        assert provider.stimulus_requests, "the installed role path must actually run"
        responses = [
            row.event for row in evidence.events
            if row.event.event_type == "CharacterLifeResponseRecorded"
            and row.event.payload()["origin"]["source_event_ref"] == source.event_id
        ]
        # A corrected response may be accepted in the eventual implementation.
        # This transport keeps returning the unsupported episode, so accepting
        # these exact bytes demonstrates the missing boundary, not role silence.
        unverified = [r for r in responses if r.payload()["response_text"] == text]
        if unverified:
            experiences = [
                x for x in evidence.projection.experiences
                if any(
                    b.source_kind == "world_life_response"
                    and b.response_event_ref in {r.event_id for r in unverified}
                    for b in x.values.source_bindings
                )
            ]
            raise UnverifiedLifeEpisodeWasAccepted(
                f"unsupported personal episode recorded in {len(unverified)} response(s) "
                f"and {len(experiences)} Experience(s) against an environment-only result"
            )
    finally:
        await app.aclose()
        await model.aclose()
