from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.accepted_ledger_batch import AcceptedLedgerBatchIssuer
from companion_daemon.world_v2.expression_plan_acceptance import (
    ExpressionPlanBudgetPolicy,
)
from companion_daemon.world_v2.proactive_action import ProactiveActionRuntime
from companion_daemon.world_v2.social_initiative import (
    PRIVATE_IMPRESSION_OCCASION_REASON,
    SocialInitiativeCompiler,
    SocialInitiativeContextPolicy,
    SocialInitiativePolicy,
    private_impression_consideration_id,
    private_impression_opportunity_context,
    private_impression_source_binds_head,
)
from companion_daemon.world_v2.schemas import WorldEvent


NOW = datetime(2026, 7, 17, 14, 0, tzinfo=UTC)
COMPANION_EXPERIENCE_STIMULUS = {
    "experience": {"values": {"participant_refs": ["actor:companion"]}}
}


def test_context_changes_relationship_aware_consideration_band_without_deciding_speech() -> None:
    policy = SocialInitiativePolicy(
        spontaneous_idle_seconds=1_800,
        spontaneous_expiry_seconds=43_200,
    )
    compiler = SocialInitiativeContextPolicy(policy=policy)
    receptive = SimpleNamespace(
        relationship_states=(
            SimpleNamespace(
                stage="close_friend",
                variables=SimpleNamespace(
                    trust_bp=8_000,
                    closeness_bp=8_000,
                    respect_bp=8_000,
                    reliability_bp=8_000,
                    mutuality_bp=8_000,
                    repair_confidence_bp=8_000,
                ),
            ),
        ),
        affect_episodes=(
            SimpleNamespace(
                status="active",
                components=(SimpleNamespace(dimension="warmth", intensity_bp=8_000),),
            ),
        ),
        plans=(),
    )
    guarded = SimpleNamespace(
        relationship_states=(
            SimpleNamespace(
                stage="acquaintance",
                variables=SimpleNamespace(
                    trust_bp=1_000,
                    closeness_bp=1_000,
                    respect_bp=1_000,
                    reliability_bp=1_000,
                    mutuality_bp=1_000,
                    repair_confidence_bp=1_000,
                ),
            ),
        ),
        affect_episodes=(
            SimpleNamespace(
                status="active",
                components=(SimpleNamespace(dimension="anger", intensity_bp=8_000),),
            ),
        ),
        plans=(SimpleNamespace(status="active"),),
    )

    receptive_profile = compiler.compile(projection=receptive, logical_time=NOW)
    guarded_profile = compiler.compile(
        projection=guarded,
        logical_time=NOW.replace(hour=18),
    )
    stranger_profile = compiler.compile(
        projection=SimpleNamespace(
            relationship_states=(
                SimpleNamespace(
                    stage="stranger",
                    variables=SimpleNamespace(
                        trust_bp=120,
                        closeness_bp=200,
                        respect_bp=80,
                        reliability_bp=0,
                        mutuality_bp=110,
                        repair_confidence_bp=0,
                    ),
                ),
            ),
            affect_episodes=(),
            plans=(),
        ),
        logical_time=NOW,
    )
    friend_profile = compiler.compile(
        projection=SimpleNamespace(
            relationship_states=(
                SimpleNamespace(
                    stage="friend",
                    variables=SimpleNamespace(
                        trust_bp=4_000,
                        closeness_bp=4_000,
                        respect_bp=4_000,
                        reliability_bp=4_000,
                        mutuality_bp=4_000,
                        repair_confidence_bp=4_000,
                    ),
                ),
            ),
            affect_episodes=(),
            plans=(),
        ),
        logical_time=NOW,
    )

    assert receptive_profile.consideration_band_seconds == (3_600, 7_200)
    assert guarded_profile.consideration_band_seconds == (10_800, 21_600)
    assert stranger_profile.consideration_band_seconds == (21_600, 28_800)
    assert stranger_profile.reason_codes[0] == "relationship:stranger"
    assert friend_profile.consideration_band_seconds == (7_200, 14_400)
    assert friend_profile.reason_codes[0] == "relationship:friend"
    assert receptive_profile.delay_candidates_seconds == (3_600, 5_400, 7_200)
    assert guarded_profile.delay_candidates_seconds == (10_800, 16_200, 21_600)
    assert receptive_profile.reason_codes == (
        "relationship:close_friend",
        "affect:approach",
        "activity:available",
        "daypart:day",
    )
    assert guarded_profile.reason_codes == (
        "relationship:acquaintance",
        "affect:guarded",
        "activity:engaged",
        "daypart:overnight",
    )


def test_friend_stage_with_production_axes_uses_two_to_four_hour_candidates() -> None:
    """Production scores stay tiny; only the declared stage changes the draw table."""

    policy = SocialInitiativePolicy(
        spontaneous_idle_seconds=1_800,
        spontaneous_expiry_seconds=43_200,
    )
    compiler = SocialInitiativeContextPolicy(policy=policy)
    production_axes = SimpleNamespace(
        trust_bp=120,
        closeness_bp=200,
        respect_bp=80,
        reliability_bp=0,
        mutuality_bp=110,
        repair_confidence_bp=0,
    )
    stranger = compiler.compile(
        projection=SimpleNamespace(
            relationship_states=(SimpleNamespace(stage="stranger", variables=production_axes),),
            affect_episodes=(),
            plans=(),
        ),
        logical_time=NOW,
    )
    friend = compiler.compile(
        projection=SimpleNamespace(
            relationship_states=(SimpleNamespace(stage="friend", variables=production_axes),),
            affect_episodes=(),
            plans=(),
        ),
        logical_time=NOW,
    )
    assert stranger.consideration_band_seconds == (21_600, 28_800)
    assert stranger.delay_candidates_seconds == (21_600, 25_200, 28_800)
    assert friend.consideration_band_seconds == (7_200, 14_400)
    assert friend.delay_candidates_seconds == (7_200, 10_800, 14_400)
    assert friend.reason_codes[0] == "relationship:friend"


def _compiler_fixture(*, receptive: bool):
    source = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:observation:message:source",
        world_id="world:social-context-test",
        event_type="ObservationRecorded",
        logical_time=NOW,
        created_at=NOW,
        actor="user:primary",
        source="test",
        trace_id="trace:social-context",
        causation_id="cause:social-context",
        correlation_id="conversation:social-context",
        idempotency_key="observation:message:source",
        payload={"observation_id": "message:source", "text": "source"},
    )
    stored = {source.event_id: source}
    committed = []
    projection = SimpleNamespace(
        world_id="world:social-context-test",
        world_revision=1,
        deliberation_revision=0,
        ledger_sequence=1,
        logical_time=NOW + timedelta(minutes=90),
        actions=(),
        expression_plan_manifests=(),
        message_observations=(
            SimpleNamespace(observation_id="message:source", world_revision=1),
        ),
        committed_world_event_refs=(),
        relationship_states=(
            (
                SimpleNamespace(
                    stage="close_friend",
                    variables=SimpleNamespace(
                        trust_bp=8_000,
                        closeness_bp=8_000,
                        respect_bp=8_000,
                        reliability_bp=8_000,
                        mutuality_bp=8_000,
                        repair_confidence_bp=8_000,
                    ),
                ),
            )
            if receptive
            else ()
        ),
        affect_episodes=(
            (
                SimpleNamespace(
                    status="active",
                    components=(
                        SimpleNamespace(dimension="warmth", intensity_bp=8_000),
                    ),
                ),
            )
            if receptive
            else ()
        ),
        plans=(),
        trigger_processes=(),
        model_result_audits=(),
        world_occurrences=(),
        threads=(),
        commitments=(),
        thread_transitions=(),
        commitment_transitions=(),
        private_impressions=(),
    )

    def commit_at_cursor(events, *, expected_cursor, commit_id):  # type: ignore[no-untyped-def]
        del expected_cursor, commit_id
        committed.extend(events)
        stored.update({event.event_id: event for event in events})

    ledger = SimpleNamespace(
        world_id="world:social-context-test",
        blocks_event_loop=False,
        project=lambda: projection,
        lookup_event_commit=lambda event_id: (
            (stored[event_id], SimpleNamespace(world_revision=1))
            if event_id in stored
            else None
        ),
        commit_at_cursor=commit_at_cursor,
    )
    return SocialInitiativeCompiler(
        ledger=ledger,
        actor_ref="actor:companion",
        policy=SocialInitiativePolicy(
            spontaneous_idle_seconds=1_800,
            spontaneous_expiry_seconds=43_200,
        ),
    ), projection, committed


@pytest.mark.asyncio
async def test_open_situation_consideration_recovers_before_contact_cooldown() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    source = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:persisted-open",
        world_id=projection.world_id,
        event_type="ExperienceCommitted",
        logical_time=NOW + timedelta(minutes=10),
        created_at=NOW + timedelta(minutes=10),
        actor="actor:companion",
        source="test",
        trace_id="trace:persisted-open",
        causation_id="cause:persisted-open",
        correlation_id="conversation:persisted-open",
        idempotency_key="experience:persisted-open",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (source, SimpleNamespace(world_revision=2))
        if event_id == source.event_id
        else None
    )
    consideration_id = "consideration:social-initiative:" + "a" * 64
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=source.event_id,
            event_type=source.event_type,
            logical_time=source.logical_time,
            world_revision=2,
        ),
    )
    projection.actions = (
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=projection.logical_time - timedelta(minutes=1),
        ),
    )
    projection.trigger_processes = (
        SimpleNamespace(
            trigger_id="trigger:proactive:persisted-open",
            trigger_ref="proactive-consideration:" + consideration_id,
            process_kind="proactive_action_deliberation",
            source_evidence_ref=source.event_id,
            state="open",
            runtime_outcome_ref=None,
        ),
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.consideration_id == consideration_id
    assert opportunity.source_kind == "situation_change"
    assert opportunity.source_event_ref == source.event_id
    assert opportunity.cadence_reason_codes == ("recovery:persisted_process",)


@pytest.mark.asyncio
async def test_consideration_draw_selects_only_a_delay_and_never_act_or_hold() -> None:
    receptive, projection, receptive_commits = _compiler_fixture(receptive=True)
    draws: list[dict[str, object]] = []

    def draw(**kwargs):  # type: ignore[no-untyped-def]
        draws.append(kwargs)
        return SimpleNamespace(
            selected_candidate_ref="delay:5400",
            draw_id="draw:test-delay",
        )

    receptive._random = SimpleNamespace(draw=draw)  # noqa: SLF001
    opportunity = await receptive.next_opportunity(projection)

    assert opportunity is not None
    assert draws[0]["candidate_refs"] == ("delay:3600", "delay:5400", "delay:7200")
    assert "act" not in draws[0]["candidate_refs"]
    assert "hold" not in draws[0]["candidate_refs"]
    del receptive_commits


@pytest.mark.asyncio
async def test_unchanged_context_reuses_one_delay_draw_across_scheduler_ticks() -> None:
    compiler, projection, committed = _compiler_fixture(receptive=True)

    first = await compiler.next_opportunity(projection)
    projection.logical_time += timedelta(seconds=15)
    second = await compiler.next_opportunity(projection)

    assert (first is None) == (second is None)
    assert [event.event_type for event in committed] == ["RandomDrawRecorded"]


@pytest.mark.asyncio
async def test_overnight_spontaneous_contact_waits_until_local_morning() -> None:
    """Idle cadence may not wake him before 07:00 Asia/Shanghai; daytime still can."""

    compiler, projection, committed = _compiler_fixture(receptive=True)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:overnight-mute",
        )
    )
    # 05:15 Asia/Shanghai — inside the same overnight floor as waiting_for.
    projection.logical_time = datetime(2026, 7, 17, 21, 15, tzinfo=UTC)

    overnight = await compiler.next_opportunity(projection)
    assert overnight is None
    assert committed == []

    # 07:00 Asia/Shanghai — the floor lifts; she may still choose silent.
    projection.logical_time = datetime(2026, 7, 17, 23, 0, tzinfo=UTC)
    daytime = await compiler.next_opportunity(projection)
    assert daytime is not None
    assert daytime.source_kind == "spontaneous_contact"


@pytest.mark.asyncio
async def test_each_due_epoch_reaches_the_model_owned_opportunity() -> None:
    """Cadence may decide when to consider, never whether the character may speak."""

    compiler, projection, committed = _compiler_fixture(receptive=True)
    draws: list[dict[str, object]] = []

    def draw(**kwargs):  # type: ignore[no-untyped-def]
        draws.append(kwargs)
        return SimpleNamespace(
            selected_candidate_ref="delay:3600", draw_id="draw:test-delay"
        )

    compiler._random = SimpleNamespace(draw=draw)  # noqa: SLF001 - deterministic seam

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "spontaneous_contact"
    assert opportunity.consideration_epoch == 0
    assert draws and draws[0]["candidate_refs"] == (
        "delay:3600",
        "delay:5400",
        "delay:7200",
    )
    assert str(draws[0]["attempt_id"]).startswith("social-initiative:")
    assert (
        await compiler.next_opportunity(
            projection,
            excluded_consideration_ids=frozenset({opportunity.consideration_id}),
        )
        is None
    )
    del committed


@pytest.mark.asyncio
async def test_completed_consideration_is_not_returned_again_in_the_same_epoch() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:completed-epoch",
        )
    )
    first = await compiler.next_opportunity(projection)
    assert first is not None
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:" + first.consideration_id,
            source_evidence_ref=first.source_event_ref,
            state="terminal",
            runtime_outcome_ref="proactive:silent",
        ),
    )

    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_expired_message_context_is_dropped_instead_of_ambient_backfill() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    clock_at = NOW + timedelta(hours=13)
    clock = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:clock:ambient",
        world_id="world:social-context-test",
        event_type="ClockAdvanced",
        logical_time=clock_at,
        created_at=clock_at,
        actor="system:clock",
        source="test",
        trace_id="trace:ambient",
        causation_id="cause:ambient",
        correlation_id="conversation:ambient",
        idempotency_key="clock:ambient",
        payload={
            "logical_time_from": NOW.isoformat(),
            "logical_time_to": clock_at.isoformat(),
        },
    )
    projection.logical_time = clock_at
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=clock.event_id,
            event_type=clock.event_type,
            logical_time=clock.logical_time,
            world_revision=2,
        ),
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]
        (clock, SimpleNamespace(world_revision=2))
        if event_id == clock.event_id
        else original_lookup(event_id)
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:ambient-delay",
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None


@pytest.mark.asyncio
async def test_situation_change_does_not_mint_a_delay_table_consideration() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=5)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:shared",
        world_id="world:social-context-test",
        event_type="ExperienceCommitted",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:situation-change",
        causation_id="cause:situation-change",
        correlation_id="conversation:situation-change",
        idempotency_key="experience:shared",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]
        (stimulus, SimpleNamespace(world_revision=2))
        if event_id == stimulus.event_id
        else original_lookup(event_id)
    )
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=stimulus.event_id,
            event_type=stimulus.event_type,
            logical_time=stimulus.logical_time,
            world_revision=2,
        ),
    )
    projection.logical_time = occurred_at + timedelta(minutes=3)
    draws: list[dict[str, object]] = []
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **kwargs: (draws.append(kwargs) or (_ for _ in ()).throw(AssertionError("situation_change must not draw a delay")))
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None
    assert draws == []


@pytest.mark.parametrize("visibility", ["private", "public", "shareable"])
@pytest.mark.asyncio
async def test_npc_only_occurrence_never_becomes_protagonist_stimulus_or_recovery(
    visibility: str,
) -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=10)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:occurrence:npc-only:{visibility}",
        world_id=projection.world_id,
        event_type="WorldOccurrenceSettled",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="worker:world-v2:npc-ecology",
        source="test",
        trace_id="trace:npc-only-stimulus",
        causation_id="cause:npc-only-stimulus",
        correlation_id="conversation:npc-only-stimulus",
        idempotency_key=f"occurrence:npc-only:{visibility}",
        payload={"occurrence_id": f"occurrence:npc-only:{visibility}"},
    )
    stimulus_ref = SimpleNamespace(
        event_id=stimulus.event_id,
        event_type=stimulus.event_type,
        logical_time=stimulus.logical_time,
        world_revision=2,
    )
    projection.committed_world_event_refs = (stimulus_ref,)
    projection.world_occurrences = (
        SimpleNamespace(
            occurrence_id=f"occurrence:npc-only:{visibility}",
            participant_refs=("npc:roommate",),
            visibility=visibility,
            status="settled",
            settlement_event_ref=stimulus.event_id,
        ),
    )
    projection.logical_time = occurred_at + timedelta(minutes=3)
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (stimulus, SimpleNamespace(world_revision=2))
        if event_id == stimulus.event_id
        else None
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:120",
            draw_id="draw:npc-only-stimulus",
        )
    )

    assert await compiler.next_opportunity(projection) is None

    # Historical open processes remain in replay, but recovery cannot turn an
    # NPC-private source into protagonist capability authority.
    projection.trigger_processes = (
        SimpleNamespace(
            trigger_id="trigger:proactive:npc-only",
            trigger_ref=(
                "proactive-consideration:consideration:social-initiative:"
                + "a" * 64
            ),
            process_kind="proactive_action_deliberation",
            source_evidence_ref=stimulus.event_id,
            state="open",
            runtime_outcome_ref=None,
        ),
    )

    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_protagonist_participation_authorizes_occurrence_stimulus() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=10)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:occurrence:shared-with-protagonist",
        world_id=projection.world_id,
        event_type="WorldOccurrenceSettled",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="worker:world-v2:life-aftermath",
        source="test",
        trace_id="trace:shared-stimulus",
        causation_id="cause:shared-stimulus",
        correlation_id="conversation:shared-stimulus",
        idempotency_key="occurrence:shared-with-protagonist",
        payload={"occurrence_id": "occurrence:shared-with-protagonist"},
    )
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=stimulus.event_id,
            event_type=stimulus.event_type,
            logical_time=stimulus.logical_time,
            world_revision=2,
        ),
    )
    projection.world_occurrences = (
        SimpleNamespace(
            occurrence_id="occurrence:shared-with-protagonist",
            participant_refs=("actor:companion", "npc:roommate"),
            visibility="private",
            status="settled",
            settlement_event_ref=stimulus.event_id,
        ),
    )
    projection.logical_time = occurred_at + timedelta(minutes=3)
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (stimulus, SimpleNamespace(world_revision=2))
        if event_id == stimulus.event_id
        else None
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:120",
            draw_id="draw:shared-stimulus",
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None


@pytest.mark.asyncio
async def test_explicit_perception_is_observable_only_to_its_bound_actor() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=10)
    perceptions = tuple(
        WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=f"event:perception:{actor_ref}",
            world_id=projection.world_id,
            event_type="ExternalPerceptionRecorded",
            logical_time=occurred_at + timedelta(seconds=offset),
            created_at=occurred_at + timedelta(seconds=offset),
            actor="worker:world-v2:external-perception",
            source="test",
            trace_id="trace:perception-stimulus",
            causation_id="cause:perception-stimulus",
            correlation_id="conversation:perception-stimulus",
            idempotency_key=f"perception:{actor_ref}",
            payload={"actor_ref": actor_ref},
        )
        for offset, actor_ref in enumerate(("npc:roommate", "actor:companion"))
    )
    projection.committed_world_event_refs = tuple(
        SimpleNamespace(
            event_id=item.event_id,
            event_type=item.event_type,
            logical_time=item.logical_time,
            world_revision=index,
        )
        for index, item in enumerate(perceptions, start=2)
    )
    projection.logical_time = occurred_at + timedelta(minutes=3)
    stored = {item.event_id: item for item in perceptions}
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (stored[event_id], SimpleNamespace(world_revision=2))
        if event_id in stored
        else None
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:120",
            draw_id="draw:perception-stimulus",
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None


@pytest.mark.asyncio
async def test_failed_situation_consideration_retains_its_stimulus_on_retry() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=30)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:retry",
        world_id="world:social-context-test",
        event_type="ExperienceCommitted",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:situation-retry",
        causation_id="cause:situation-retry",
        correlation_id="conversation:situation-retry",
        idempotency_key="experience:retry",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (stimulus, SimpleNamespace(world_revision=2))
        if event_id == stimulus.event_id
        else None
    )
    stimulus_ref = SimpleNamespace(
        event_id=stimulus.event_id,
        event_type=stimulus.event_type,
        logical_time=stimulus.logical_time,
        world_revision=2,
    )
    projection.committed_world_event_refs = (stimulus_ref,)
    projection.logical_time = occurred_at + timedelta(minutes=3)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:120",
            draw_id="draw:situation-retry",
        )
    )
    consideration_id = "consideration:social-initiative:" + "c" * 64

    failure_ref = SimpleNamespace(
        event_id="event:model-result:situation-retry",
        event_type="ModelResultRecorded",
        logical_time=occurred_at + timedelta(minutes=4),
        world_revision=4,
    )
    projection.committed_world_event_refs = (stimulus_ref, failure_ref)
    projection.model_result_audits = (
        SimpleNamespace(
            model_result_ref="model-result:situation-retry",
            proposal_hash=None,
            event_ref=failure_ref.event_id,
            evaluated_world_revision=2,
        ),
    )
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            state="terminal",
            trigger_ref="proactive-consideration:" + consideration_id,
            runtime_outcome_ref=(
                "proactive:deliberation-failed:model-result:situation-retry"
            ),
            source_evidence_ref=stimulus.event_id,
            claim_lease=SimpleNamespace(acquired_at=failure_ref.logical_time),
        ),
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            state="terminal",
            trigger_ref="proactive-consideration:later-success",
            runtime_outcome_ref="proactive:model-silent:model-result:later",
            source_evidence_ref=stimulus.event_id,
            claim_lease=None,
        ),
    )
    late_stimulus_ref = SimpleNamespace(
        event_id="event:experience:late-in-window",
        event_type="ExperienceCommitted",
        logical_time=occurred_at + timedelta(minutes=5),
        world_revision=5,
    )
    old_same_time_stimulus_ref = SimpleNamespace(
        event_id="event:experience:before-message",
        event_type="ExperienceCommitted",
        logical_time=stimulus.logical_time,
        world_revision=1,
    )
    projection.committed_world_event_refs = (
        old_same_time_stimulus_ref,
        stimulus_ref,
        failure_ref,
        late_stimulus_ref,
    )
    projection.logical_time = occurred_at + timedelta(minutes=15)

    retry = await compiler._failed_consideration_retry(projection)  # noqa: SLF001

    assert retry is not None
    assert retry.source_kind == "situation_change"
    assert retry.stimulus_event_refs == (stimulus.event_id,)
    assert retry.cadence_reason_codes == ("technical_failure:retry",)

    projection.message_observations = (
        *projection.message_observations,
        SimpleNamespace(observation_id="message:during-call", world_revision=3),
    )
    assert await compiler._failed_consideration_retry(projection) is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_technical_retry_precedes_cooldown_from_another_successful_contact() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=30)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:retry-before-contact-cooldown",
        world_id="world:social-context-test",
        event_type="ExperienceCommitted",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:retry-before-contact-cooldown",
        causation_id="cause:retry-before-contact-cooldown",
        correlation_id="conversation:retry-before-contact-cooldown",
        idempotency_key="experience:retry-before-contact-cooldown",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (stimulus, SimpleNamespace(world_revision=2))
        if event_id == stimulus.event_id
        else None
    )
    stimulus_ref = SimpleNamespace(
        event_id=stimulus.event_id,
        event_type=stimulus.event_type,
        logical_time=stimulus.logical_time,
        world_revision=2,
    )
    projection.committed_world_event_refs = (stimulus_ref,)
    projection.logical_time = occurred_at + timedelta(minutes=3)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:120",
            draw_id="draw:retry-before-contact-cooldown",
        )
    )
    failed_opportunity_id = "consideration:social-initiative:" + "d" * 64

    failed_at = occurred_at + timedelta(minutes=4)
    failure_ref = SimpleNamespace(
        event_id="event:model-result:retry-before-contact-cooldown",
        event_type="ModelResultRecorded",
        logical_time=failed_at,
        world_revision=4,
    )
    projection.committed_world_event_refs = (stimulus_ref, failure_ref)
    projection.model_result_audits = (
        SimpleNamespace(
            model_result_ref="model-result:retry-before-contact-cooldown",
            proposal_hash=None,
            event_ref=failure_ref.event_id,
            evaluated_world_revision=2,
        ),
    )
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            state="terminal",
            trigger_ref=(
                "proactive-consideration:" + failed_opportunity_id
            ),
            runtime_outcome_ref=(
                "proactive:deliberation-failed:"
                "model-result:retry-before-contact-cooldown"
            ),
            source_evidence_ref=stimulus.event_id,
            claim_lease=SimpleNamespace(acquired_at=failed_at),
        ),
    )
    # Another consideration successfully contacted the user two minutes before
    # this failure's ten-minute retry deadline. That ordinary contact starts a
    # cadence cooldown, but it does not semantically settle or supersede the
    # failed consideration.
    projection.actions = (
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=failed_at + timedelta(minutes=8),
        ),
    )
    projection.logical_time = failed_at + timedelta(minutes=10)

    retry = await compiler.next_opportunity(projection)

    assert retry is not None
    assert retry.consideration_id == failed_opportunity_id
    assert retry.cadence_reason_codes == ("technical_failure:retry",)

    # Only a newer user Observation invalidates the old pinned social context.
    projection.message_observations = (
        *projection.message_observations,
        SimpleNamespace(observation_id="message:new-context", world_revision=5),
    )
    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_not_due_retry_does_not_starve_an_independent_due_situation() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    first_at = NOW + timedelta(minutes=10)
    second_at = first_at + timedelta(minutes=11)
    first_event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:failed-situation",
        world_id=projection.world_id,
        event_type="ExperienceCommitted",
        logical_time=first_at,
        created_at=first_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:retry-independent-situation",
        causation_id="cause:retry-independent-situation",
        correlation_id="conversation:retry-independent-situation",
        idempotency_key="experience:failed-situation",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    second_event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:independent-situation",
        world_id=projection.world_id,
        event_type="ExperienceCommitted",
        logical_time=second_at,
        created_at=second_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:retry-independent-situation",
        causation_id=first_event.event_id,
        correlation_id="conversation:retry-independent-situation",
        idempotency_key="experience:independent-situation",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    stored = {item.event_id: item for item in (first_event, second_event)}
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (stored[event_id], SimpleNamespace(world_revision=2))
        if event_id in stored
        else None
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:120",
            draw_id="draw:retry-independent-situation",
        )
    )
    first_id = "consideration:social-initiative:" + "e" * 64
    first_ref = SimpleNamespace(
        event_id=first_event.event_id,
        event_type=first_event.event_type,
        logical_time=first_event.logical_time,
        world_revision=2,
    )
    projection.committed_world_event_refs = (first_ref,)
    projection.logical_time = first_at + timedelta(minutes=3)

    failed_at = first_at + timedelta(minutes=5)
    failure_ref = SimpleNamespace(
        event_id="event:model-result:failed-situation",
        event_type="ModelResultRecorded",
        logical_time=failed_at,
        world_revision=4,
    )
    second_ref = SimpleNamespace(
        event_id=second_event.event_id,
        event_type=second_event.event_type,
        logical_time=second_event.logical_time,
        world_revision=5,
    )
    trigger_ref = "proactive-consideration:" + first_id
    failed_trigger_id = ProactiveActionRuntime._trigger_id_for_world(  # noqa: SLF001
        world_id=projection.world_id,
        consideration_id=first_id,
        retry_ordinal=0,
    )
    projection.trigger_processes = (
        SimpleNamespace(
            trigger_id=failed_trigger_id,
            process_kind=ProactiveActionRuntime.PROCESS_KIND,
            state="terminal",
            trigger_ref=trigger_ref,
            runtime_outcome_ref="proactive:deliberation-failed:model-result:failed-situation",
            source_evidence_ref=first_event.event_id,
            claim_lease=SimpleNamespace(acquired_at=failed_at),
        ),
    )
    projection.completed_trigger_ids = (failed_trigger_id,)
    projection.model_result_audits = (
        SimpleNamespace(
            attempt_id=ProactiveActionRuntime._model_attempt_id(  # noqa: SLF001
                consideration_id=first_id,
                retry_ordinal=0,
            ),
            model_result_ref="model-result:failed-situation",
            proposal_hash=None,
            parent_model_call_id=None,
            audit_json='{"status":"recovery_failed","failure_code":"quick_invalid_output"}',
            event_ref=failure_ref.event_id,
            evaluated_world_revision=2,
        ),
    )
    projection.committed_world_event_refs = (first_ref, failure_ref, second_ref)
    projection.logical_time = second_at + timedelta(minutes=2)

    runtime = ProactiveActionRuntime(
        ledger=compiler._ledger,  # noqa: SLF001
        turn=SimpleNamespace(),
        batch_issuer=AcceptedLedgerBatchIssuer(),
        policy=ExpressionPlanBudgetPolicy(
            account_id="account:proactive",
            amount_limit_per_action=10,
            actor="actor:companion",
            allowed_targets=("user:primary",),
            recovery_policy="effect_once",
            category="proactive",
        ),
        owner_id="worker:test-social-alternate",
        social_initiative=compiler,
    )
    opened = []

    async def capture_open(**kwargs):  # type: ignore[no-untyped-def]
        opened.append(kwargs["opportunity"])

    runtime._open = capture_open  # type: ignore[method-assign]  # noqa: SLF001

    result = await runtime.drain_one()

    assert result.status == "retry_wait"
    assert opened == []


@pytest.mark.asyncio
async def test_not_due_situation_retry_does_not_occupy_the_ambient_cadence() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    failed_situation = SimpleNamespace(
        consideration_id="consideration:failed-situation",
        source_kind="situation_change",
    )
    ambient = SimpleNamespace(
        consideration_id="consideration:ambient-next-epoch",
        source_kind="ambient_presence",
    )

    async def no_pending(  # type: ignore[no-untyped-def]
        _projection, *, excluded_consideration_ids
    ):
        del _projection, excluded_consideration_ids
        return None

    async def failed_retry(_projection):  # type: ignore[no-untyped-def]
        del _projection
        return failed_situation

    async def ambient_opportunity(_projection, _logical_time):  # type: ignore[no-untyped-def]
        del _projection, _logical_time
        return ambient

    compiler._pending_consideration = no_pending  # type: ignore[method-assign]  # noqa: SLF001
    compiler._failed_consideration_retry = failed_retry  # type: ignore[method-assign]  # noqa: SLF001
    compiler._spontaneous_contact = ambient_opportunity  # type: ignore[method-assign]  # noqa: SLF001

    opportunity = await compiler.next_opportunity(
        projection,
        excluded_consideration_ids=frozenset({failed_situation.consideration_id}),
    )

    assert opportunity is ambient


@pytest.mark.asyncio
async def test_successful_retry_terminally_settles_the_failed_consideration() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=30)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:settled-retry",
        world_id="world:social-context-test",
        event_type="ExperienceCommitted",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:settled-retry",
        causation_id="cause:settled-retry",
        correlation_id="conversation:settled-retry",
        idempotency_key="experience:settled-retry",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    stimulus_ref = SimpleNamespace(
        event_id=stimulus.event_id,
        event_type=stimulus.event_type,
        logical_time=stimulus.logical_time,
        world_revision=2,
    )
    failure_ref = SimpleNamespace(
        event_id="event:model-result:settled-retry",
        event_type="ModelResultRecorded",
        logical_time=occurred_at + timedelta(minutes=4),
        world_revision=4,
    )
    consideration_ref = "proactive-consideration:consideration:settled-retry"
    projection.logical_time = occurred_at + timedelta(minutes=20)
    projection.committed_world_event_refs = (stimulus_ref, failure_ref)
    projection.model_result_audits = (
        SimpleNamespace(
            model_result_ref="model-result:settled-retry",
            proposal_hash=None,
            event_ref=failure_ref.event_id,
            evaluated_world_revision=2,
        ),
    )
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            state="terminal",
            trigger_ref=consideration_ref,
            runtime_outcome_ref=(
                "proactive:deliberation-failed:model-result:settled-retry"
            ),
            source_evidence_ref=stimulus.event_id,
            claim_lease=SimpleNamespace(acquired_at=failure_ref.logical_time),
        ),
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            state="terminal",
            trigger_ref=consideration_ref,
            runtime_outcome_ref="proactive:silent",
            source_evidence_ref=stimulus.event_id,
            claim_lease=SimpleNamespace(
                acquired_at=failure_ref.logical_time + timedelta(minutes=10)
            ),
        ),
    )
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (stimulus, SimpleNamespace(world_revision=2))
        if event_id == stimulus.event_id
        else None
    )

    retry = await compiler._failed_consideration_retry(projection)  # noqa: SLF001

    assert retry is None


@pytest.mark.asyncio
async def test_situation_windows_do_not_mint_dedicated_model_considers() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    first_at = NOW + timedelta(minutes=10)
    second_at = first_at + timedelta(minutes=11)
    events = tuple(
        WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=event_id,
            world_id="world:social-context-test",
            event_type="ExperienceCommitted",
            logical_time=at,
            created_at=at,
            actor="actor:companion",
            source="test",
            trace_id="trace:situation-windows",
            causation_id="cause:situation-windows",
            correlation_id="conversation:situation-windows",
            idempotency_key=event_id,
            payload=COMPANION_EXPERIENCE_STIMULUS,
        )
        for event_id, at in (
            ("event:experience:first-window", first_at),
            ("event:experience:second-window", second_at),
        )
    )
    projection.committed_world_event_refs = tuple(
        SimpleNamespace(
            event_id=item.event_id,
            event_type=item.event_type,
            logical_time=item.logical_time,
            world_revision=index,
        )
        for index, item in enumerate(events, start=2)
    )
    projection.logical_time = first_at + timedelta(minutes=50)
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = lambda event_id: next(  # type: ignore[attr-defined]  # noqa: SLF001
        (
            (item, SimpleNamespace(world_revision=index))
            for index, item in enumerate(events, start=2)
            if item.event_id == event_id
        ),
        original_lookup(event_id),
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:idle-hitch",
        )
    )
    due = await compiler.next_opportunity(projection)
    assert due is not None
    assert due.source_kind in {"spontaneous_contact", "ambient_presence"}
    assert due.stimulus_event_refs == (events[1].event_id,)
    assert "stimulus:situation_change" in due.cadence_reason_codes


@pytest.mark.asyncio
async def test_paid_idle_consider_hitches_the_latest_situation_cluster() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    anchor_at = NOW + timedelta(minutes=10)
    events = tuple(
        WorldEvent.from_payload(
            schema_version="world-v2.1",
            event_id=event_id,
            world_id="world:social-context-test",
            event_type="ExperienceCommitted",
            logical_time=at,
            created_at=at,
            actor="actor:companion",
            source="test",
            trace_id="trace:same-window",
            causation_id="cause:same-window",
            correlation_id="conversation:same-window",
            idempotency_key=event_id,
            payload=COMPANION_EXPERIENCE_STIMULUS,
        )
        for event_id, at in (
            ("event:experience:window-anchor", anchor_at),
            ("event:experience:window-append", anchor_at + timedelta(minutes=5)),
        )
    )
    projection.committed_world_event_refs = tuple(
        SimpleNamespace(
            event_id=item.event_id,
            event_type=item.event_type,
            logical_time=item.logical_time,
            world_revision=index,
        )
        for index, item in enumerate(events, start=2)
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = lambda event_id: next(  # type: ignore[attr-defined]  # noqa: SLF001
        (
            (item, SimpleNamespace(world_revision=index))
            for index, item in enumerate(events, start=2)
            if item.event_id == event_id
        ),
        original_lookup(event_id),
    )
    projection.logical_time = anchor_at + timedelta(minutes=3)
    assert await compiler.next_opportunity(projection) is None

    projection.logical_time = NOW + timedelta(minutes=90)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:idle-hitch-cluster",
        )
    )
    due = await compiler.next_opportunity(projection)
    assert due is not None
    assert due.source_kind in {"spontaneous_contact", "ambient_presence"}
    assert due.stimulus_event_refs == tuple(item.event_id for item in events)


@pytest.mark.asyncio
async def test_response_expectation_never_opens_a_standalone_proactive_opportunity() -> None:
    """Expectations inform cognition; they are not an authority to chase a reply."""

    source = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:expression:acceptance",
        world_id="world:response-gap-context-test",
        event_type="ExpressionPlanAccepted",
        logical_time=NOW,
        created_at=NOW,
        actor="actor:companion",
        source="test",
        trace_id="trace:response-gap",
        causation_id="cause:response-gap",
        correlation_id="conversation:response-gap",
        idempotency_key="expression:acceptance",
        payload={},
    )
    logical_time = NOW + timedelta(minutes=2)
    action = SimpleNamespace(
        action_id="action:source",
        state="delivered",
        kind="reply",
        logical_time=NOW,
    )
    manifest = SimpleNamespace(
        plan_id="plan:source",
        acceptance_event_ref=source.event_id,
        recorded_at_world_revision=1,
        response_expectation=SimpleNamespace(
            source_beat_id="beat:source",
            not_before=NOW + timedelta(minutes=1),
            expires_at=NOW + timedelta(hours=1),
            delivery_requirement="provider_accepted_or_delivered",
        ),
        beats=(SimpleNamespace(beat_id="beat:source", action=action),),
    )
    projection = SimpleNamespace(
        logical_time=logical_time,
        actions=(action,),
        expression_plan_manifests=(manifest,),
        expression_plans=(SimpleNamespace(plan_id="plan:source", state="authorized"),),
        execution_receipts=(SimpleNamespace(action_id=action.action_id, observed_state="delivered"),),
        message_observations=(
            SimpleNamespace(observation_id="message:source", world_revision=1),
            SimpleNamespace(observation_id="message:unrelated", world_revision=2),
        ),
        committed_world_event_refs=(),
        world_revision=2,
        relationship_states=(),
        affect_episodes=(),
        plans=(),
        trigger_processes=(
            SimpleNamespace(
                process_kind="proactive_action_deliberation",
                trigger_ref="proactive-consideration:failed-idle",
                source_evidence_ref="event:old-idle",
                state="terminal",
                runtime_outcome_ref=(
                    "proactive:deliberation-failed:model-result:old-idle"
                ),
            ),
        ),
    )
    ledger = SimpleNamespace(
        world_id="world:response-gap-context-test",
        blocks_event_loop=False,
        lookup_event_commit=lambda event_id: (
            (source, SimpleNamespace(world_revision=1))
            if event_id == source.event_id
            else None
        ),
    )
    compiler = SocialInitiativeCompiler(
        ledger=ledger,
        actor_ref="actor:companion",
        policy=SocialInitiativePolicy(
            spontaneous_idle_seconds=1_800,
            spontaneous_expiry_seconds=43_200,
        ),
    )

    assert await compiler.next_opportunity(projection) is None


_IMPRESSION_SECRET = "他记岔了这点反而有点可爱，我不觉得冒犯，这是私密原文"


def _attach_living_impression(
    compiler,
    projection,
    *,
    status: str = "active",
    summary: str = _IMPRESSION_SECRET,
    event_id: str = "event:private-impression:accepted",
    impression_id: str = "impression:living",
    world_revision: int = 2,
):
    accepted_at = NOW + timedelta(minutes=5)
    event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=event_id,
        world_id=projection.world_id,
        event_type="PrivateImpressionAccepted",
        logical_time=accepted_at,
        created_at=accepted_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:private-impression",
        causation_id="cause:private-impression",
        correlation_id="conversation:private-impression",
        idempotency_key=event_id,
        payload={"impression_id": impression_id, "reflection_summary": summary},
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (event, SimpleNamespace(world_revision=world_revision))
        if event_id == event.event_id
        else original_lookup(event_id)
    )
    projection.private_impressions = (
        SimpleNamespace(
            impression_id=impression_id,
            status=status,
            last_supported=accepted_at,
            first_seen=accepted_at,
            reflection_summary=summary,
            origin=SimpleNamespace(accepted_event_ref=event.event_id),
        ),
    )
    projection.committed_world_event_refs = (
        *tuple(projection.committed_world_event_refs),
        SimpleNamespace(
            event_id=event.event_id,
            event_type=event.event_type,
            logical_time=accepted_at,
            world_revision=world_revision,
        ),
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:private-impression",
        )
    )
    return event


@pytest.mark.asyncio
async def test_living_private_impression_takes_a_due_consider_slot_without_scripting_speech() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    event = _attach_living_impression(compiler, projection)

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "private_impression"
    assert opportunity.source_id == "impression:living"
    assert opportunity.source_event_ref == event.event_id
    assert opportunity.scheduled_for == NOW + timedelta(seconds=3600)
    assert PRIVATE_IMPRESSION_OCCASION_REASON in opportunity.cadence_reason_codes
    dumped = opportunity.model_dump_json()
    assert _IMPRESSION_SECRET not in dumped
    assert "reflection_summary" not in dumped
    assert private_impression_opportunity_context().find(_IMPRESSION_SECRET) == -1
    assert "she still decides" in private_impression_opportunity_context()
    assert private_impression_source_binds_head(
        projection=projection, event=event, opportunity=opportunity
    )


@pytest.mark.asyncio
async def test_private_impression_does_not_open_a_faster_channel_than_the_cadence_band() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _attach_living_impression(compiler, projection)
    projection.logical_time = NOW + timedelta(minutes=10)

    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_private_impression_respects_the_ordinary_contact_cooldown() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _attach_living_impression(compiler, projection)
    projection.actions = (
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=projection.logical_time - timedelta(minutes=1),
        ),
    )

    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_spent_or_dead_impression_falls_through_to_the_ordinary_idle_slot() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    event = _attach_living_impression(compiler, projection, status="superseded")

    dead = await compiler.next_opportunity(projection)

    assert dead is not None
    assert dead.source_kind == "spontaneous_contact"
    assert dead.source_event_ref != event.event_id

    _attach_living_impression(compiler, projection, status="active")
    spent_id = private_impression_consideration_id("impression:living")
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:" + spent_id,
            source_evidence_ref=event.event_id,
            state="terminal",
            runtime_outcome_ref="proactive:silent",
        ),
    )

    spent = await compiler.next_opportunity(projection)

    assert spent is not None
    assert spent.source_kind == "spontaneous_contact"


@pytest.mark.asyncio
async def test_open_private_impression_consideration_recovers_from_the_accepted_event() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    event = _attach_living_impression(compiler, projection)
    consideration_id = private_impression_consideration_id("impression:living")
    projection.trigger_processes = (
        SimpleNamespace(
            trigger_id="trigger:proactive:private-impression",
            trigger_ref="proactive-consideration:" + consideration_id,
            process_kind="proactive_action_deliberation",
            source_evidence_ref=event.event_id,
            state="open",
            runtime_outcome_ref=None,
        ),
    )
    projection.actions = (
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=projection.logical_time - timedelta(minutes=1),
        ),
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "private_impression"
    assert opportunity.consideration_id == consideration_id
    assert opportunity.source_event_ref == event.event_id
    assert opportunity.cadence_reason_codes == ("recovery:persisted_process",)


@pytest.mark.asyncio
async def test_living_impression_takes_a_due_idle_retry_slot() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    observation_id = "event:observation:message:source"
    failed_id = "consideration:social-initiative:" + "e" * 64
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=observation_id,
            event_type="ObservationRecorded",
            logical_time=NOW,
            world_revision=1,
        ),
    )
    projection.model_result_audits = (
        SimpleNamespace(
            model_result_ref="model-result:idle-retry",
            proposal_hash=None,
            event_ref="event:model-result:idle-retry",
            evaluated_world_revision=1,
        ),
    )
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            state="terminal",
            trigger_ref="proactive-consideration:" + failed_id,
            runtime_outcome_ref="proactive:deliberation-failed:model-result:idle-retry",
            source_evidence_ref=observation_id,
            claim_lease=None,
        ),
    )
    event = _attach_living_impression(compiler, projection)

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "private_impression"
    assert opportunity.source_event_ref == event.event_id
    assert PRIVATE_IMPRESSION_OCCASION_REASON in opportunity.cadence_reason_codes
    assert "technical_failure:retry" in opportunity.cadence_reason_codes


def _attach_stale_later(compiler, projection, *, spoken: bool = True, due: bool = True, gated: bool = False):
    written = NOW
    not_before = NOW + timedelta(hours=1) if due else NOW + timedelta(hours=3)
    projection.logical_time = NOW + timedelta(hours=1)
    later = SimpleNamespace(
        action_id="action:later:1",
        kind="followup",
        state="authorized",
        logical_time=written,
        not_before=not_before,
        expires_at=written + timedelta(hours=2),
        expression_plan_id="plan:later:1",
        expression_beat_id="beat:later:1",
        payload_ref="payload:later:1",
    )
    spoken_action = SimpleNamespace(
        action_id="action:now:1",
        kind="proactive_message",
        state="delivered",
        logical_time=NOW + timedelta(minutes=16),
        expression_plan_id="plan:now:1",
        expression_beat_id="beat:now:1",
    )
    projection.actions = (later, spoken_action) if spoken else (later,)
    beat_event_id = "event:beat:later"
    projection.expression_beats = (
        SimpleNamespace(
            beat_id="beat:later:1",
            event_ref=beat_event_id,
            payload_ref="payload:later:1",
        ),
    )
    event = SimpleNamespace(
        event_id=beat_event_id,
        event_type="ExpressionBeatAuthorized",
        payload_hash="c" * 64,
        trace_id="trace:later-refresh",
        correlation_id="correlation:later-refresh",
        created_at=written,
        logical_time=written,
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]  # noqa: SLF001
        (event, SimpleNamespace(world_revision=4))
        if event_id == beat_event_id
        else original_lookup(event_id)
    )
    projection.committed_world_event_refs = (
        *tuple(projection.committed_world_event_refs),
        SimpleNamespace(
            event_id=beat_event_id,
            event_type="ExpressionBeatAuthorized",
            logical_time=written,
            world_revision=4,
            payload_hash="c" * 64,
        ),
    )
    if gated:
        from companion_daemon.world_v2.expression_reconsideration import (
            expression_reconsideration_trigger_ref,
        )

        projection.trigger_processes = (
            SimpleNamespace(
                process_kind="expression_reconsideration",
                state="open",
                trigger_ref=expression_reconsideration_trigger_ref(
                    plan_id="plan:later:1",
                    beat_id="beat:later:1",
                    observation_id="message:source",
                ),
            ),
        )
    return later, event


@pytest.mark.asyncio
async def test_due_stale_later_mints_a_refresh_before_contact_cooldown() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    later, event = _attach_stale_later(compiler, projection)
    projection.actions = (
        *projection.actions,
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=projection.logical_time - timedelta(minutes=1),
            expression_plan_id="plan:recent",
            expression_beat_id="beat:recent",
            action_id="action:recent",
        ),
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "later_expression_refresh"
    assert opportunity.source_id == later.action_id
    assert opportunity.source_event_ref == event.event_id
    assert "later_expression:stale_before_dispatch" in opportunity.cadence_reason_codes


@pytest.mark.asyncio
async def test_later_refresh_does_not_mint_when_the_frozen_payload_is_still_current() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _attach_stale_later(compiler, projection, spoken=False)

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None or opportunity.source_kind != "later_expression_refresh"


@pytest.mark.asyncio
async def test_later_refresh_does_not_mint_before_due() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _attach_stale_later(compiler, projection, due=False)
    projection.actions = (
        *projection.actions,
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=projection.logical_time - timedelta(minutes=1),
            expression_plan_id="plan:recent",
            expression_beat_id="beat:recent",
            action_id="action:recent",
        ),
    )

    assert await compiler.next_opportunity(projection) is None


@pytest.mark.asyncio
async def test_later_refresh_yields_when_observation_reconsideration_already_owns_the_beat() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _attach_stale_later(compiler, projection, gated=True)
    projection.actions = (
        *projection.actions,
        SimpleNamespace(
            kind="proactive_message",
            state="delivered",
            logical_time=projection.logical_time - timedelta(minutes=1),
            expression_plan_id="plan:recent",
            expression_beat_id="beat:recent",
            action_id="action:recent",
        ),
    )

    opportunity = await compiler.next_opportunity(projection)
    assert opportunity is None or opportunity.source_kind != "later_expression_refresh"


@pytest.mark.asyncio
async def test_settled_post_silent_releases_ambient_cadence() -> None:
    """A completed post-silent epoch must not permanently suppress ambient.

    Production stuck here: silent → post-silent grounding_rejected left
    ``_post_silent_chain_active`` true forever, so drain stayed idle while
    health still screamed consideration_due from the parallel message formula.
    """

    compiler, projection, _committed = _compiler_fixture(receptive=True)
    # Morning local so overnight does not hide the ambient release.
    morning = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
    source = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:observation:message:source",
        world_id="world:social-context-test",
        event_type="ObservationRecorded",
        logical_time=morning,
        created_at=morning,
        actor="user:primary",
        source="test",
        trace_id="trace:social-context",
        causation_id="cause:social-context",
        correlation_id="conversation:social-context",
        idempotency_key="observation:message:source",
        payload={"observation_id": "message:source", "text": "source"},
    )
    compiler._ledger.lookup_event_commit = (  # type: ignore[attr-defined]
        lambda event_id: (
            (source, SimpleNamespace(world_revision=1))
            if event_id == source.event_id
            else None
        )
    )
    projection.logical_time = morning + timedelta(minutes=90)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:3600",
            draw_id="draw:post-silent-release",
        )
    )
    first = await compiler.next_opportunity(projection)
    assert first is not None
    silent_trigger_id = "trigger:proactive:silent-release"
    completion_at = first.scheduled_for or projection.logical_time
    completion_event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:proactive:completed:silent-release",
        world_id="world:social-context-test",
        event_type="TriggerProcessCompleted",
        logical_time=completion_at,
        created_at=completion_at,
        actor="worker:proactive",
        source="test",
        trace_id="trace:silent-release",
        causation_id="cause:silent-release",
        correlation_id="conversation:silent-release",
        idempotency_key="proactive:completed:silent-release",
        payload={
            "trigger_id": silent_trigger_id,
            "runtime_outcome_ref": "proactive:silent",
            "attempt_id": "attempt:silent-release",
        },
    )
    from companion_daemon.world_v2.social_initiative import (
        post_silent_attempt_id,
        post_silent_consideration_id,
    )

    attempt_id = post_silent_attempt_id(
        completion_event_ref=completion_event.event_id,
        prior_trigger_id=silent_trigger_id,
        policy_version=SocialInitiativeContextPolicy.version,
    )
    post_silent_id = post_silent_consideration_id(
        attempt_id=attempt_id,
        delay_seconds=3600,
        epoch=0,
        prior_trigger_id=silent_trigger_id,
    )
    silent_process = SimpleNamespace(
        trigger_id=silent_trigger_id,
        process_kind="proactive_action_deliberation",
        trigger_ref="proactive-consideration:" + first.consideration_id,
        source_evidence_ref=first.source_event_ref,
        state="terminal",
        runtime_outcome_ref="proactive:silent",
    )
    settled_post_silent = SimpleNamespace(
        trigger_id="trigger:proactive:post-silent-settled",
        process_kind="proactive_action_deliberation",
        trigger_ref="proactive-consideration:" + post_silent_id,
        source_evidence_ref=first.source_event_ref,
        state="terminal",
        runtime_outcome_ref="proactive:grounding-rejected",
    )
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=completion_event.event_id,
            event_type=completion_event.event_type,
            logical_time=completion_event.logical_time,
            world_revision=1,
        ),
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001

    def lookup(event_id):  # type: ignore[no-untyped-def]
        if event_id == completion_event.event_id:
            return completion_event, SimpleNamespace(world_revision=1)
        return original_lookup(event_id)

    compiler._ledger.lookup_event_commit = lookup  # type: ignore[attr-defined]
    compiler._ledger.find_trigger_completion = (  # type: ignore[attr-defined]
        lambda trigger_id: (
            SimpleNamespace(
                event_id=completion_event.event_id,
                event_type=completion_event.event_type,
                payload_hash="a" * 64,
                logical_time=completion_event.logical_time,
            )
            if trigger_id == silent_trigger_id
            else None
        )
    )
    # Hold ambient while post-silent would still be outstanding (no process yet).
    projection.trigger_processes = (silent_process,)
    assert await compiler._post_silent_chain_active(projection) is True  # noqa: SLF001
    # Settled post-silent releases the chain.
    projection.trigger_processes = (silent_process, settled_post_silent)
    assert await compiler._post_silent_chain_active(projection) is False  # noqa: SLF001
    # Next spontaneous epoch (still inside the 12h window, daytime).
    projection.logical_time = morning + timedelta(hours=2, minutes=30)
    released = await compiler.next_opportunity(projection)
    assert released is not None
    assert released.source_kind == "spontaneous_contact"
    assert released.consideration_id != first.consideration_id
    assert released.consideration_epoch == 1


def _attach_silent_epoch(compiler, projection, *, source, completion_at, trigger_id: str):
    """Wire one role-owned silence plus its terminal completion into the fixture."""

    from companion_daemon.world_v2.social_initiative import (
        SocialInitiativeContextPolicy,
    )

    completion_event = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:proactive:completed:" + trigger_id,
        world_id=projection.world_id,
        event_type="TriggerProcessCompleted",
        logical_time=completion_at,
        created_at=completion_at,
        actor="worker:proactive",
        source="test",
        trace_id="trace:" + trigger_id,
        causation_id="cause:" + trigger_id,
        correlation_id="conversation:" + trigger_id,
        idempotency_key="proactive:completed:" + trigger_id,
        payload={
            "trigger_id": trigger_id,
            "runtime_outcome_ref": "proactive:silent",
            "attempt_id": "attempt:" + trigger_id,
        },
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001

    def lookup(event_id):  # type: ignore[no-untyped-def]
        if event_id == source.event_id:
            return source, SimpleNamespace(world_revision=1)
        if event_id == completion_event.event_id:
            return completion_event, SimpleNamespace(world_revision=1)
        return original_lookup(event_id)

    compiler._ledger.lookup_event_commit = lookup  # type: ignore[attr-defined]
    compiler._ledger.find_trigger_completion = (  # type: ignore[attr-defined]
        lambda candidate: (
            SimpleNamespace(
                event_id=completion_event.event_id,
                event_type=completion_event.event_type,
                payload_hash="a" * 64,
                logical_time=completion_event.logical_time,
            )
            if candidate == trigger_id
            else None
        )
    )
    projection.trigger_processes = (
        SimpleNamespace(
            trigger_id=trigger_id,
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:consideration:social-initiative:"
            + "0" * 64,
            source_evidence_ref=source.event_id,
            state="terminal",
            runtime_outcome_ref="proactive:silent",
        ),
    )
    assert SocialInitiativeContextPolicy.version
    return completion_event


def _post_silent_morning_source(projection, *, morning: datetime):
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:observation:message:source",
        world_id=projection.world_id,
        event_type="ObservationRecorded",
        logical_time=morning,
        created_at=morning,
        actor="user:primary",
        source="test",
        trace_id="trace:post-silent-wedge",
        causation_id="cause:post-silent-wedge",
        correlation_id="conversation:post-silent-wedge",
        idempotency_key="observation:message:source",
        payload={"observation_id": "message:source", "text": "source"},
    )


@pytest.mark.asyncio
async def test_post_silent_replayed_draw_survives_a_moved_delay_band() -> None:
    """A committed post-silent draw is replayed, never re-validated against a new band.

    Production wedged here: the post-silent ``attempt_id`` deliberately excludes
    the candidate set, so the draw committed under one relationship band was
    replayed forever while a later stage recompiled a different band.  The
    membership check then raised
    "post-silent initiative draw selected an unknown delay" on every scheduler
    pass and the clock never advanced again.  The draw's own recorded
    ``candidate_refs`` are the only authority that survives the stage move.
    """

    from companion_daemon.world_v2.social_initiative import (
        post_silent_attempt_id,
        post_silent_consideration_id,
    )

    compiler, projection, _committed = _compiler_fixture(receptive=True)
    morning = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)  # 09:00 Asia/Shanghai
    source = _post_silent_morning_source(projection, morning=morning)
    silent_trigger_id = "trigger:proactive:silent-moved-band"
    projection.logical_time = morning
    completion_event = _attach_silent_epoch(
        compiler,
        projection,
        source=source,
        completion_at=morning,
        trigger_id=silent_trigger_id,
    )
    attempt_id = post_silent_attempt_id(
        completion_event_ref=completion_event.event_id,
        prior_trigger_id=silent_trigger_id,
        policy_version=compiler._context.version,  # noqa: SLF001
    )
    # The committed draw was taken while the relationship compiled the stranger
    # band; the fixture's current close_friend stage compiles a disjoint band.
    receptive_states = projection.relationship_states
    projection.relationship_states = (
        SimpleNamespace(
            stage="stranger",
            variables=SimpleNamespace(
                trust_bp=120,
                closeness_bp=200,
                respect_bp=80,
                reliability_bp=0,
                mutuality_bp=110,
                repair_confidence_bp=0,
            ),
        ),
    )
    old_profile = compiler._context.compile(  # noqa: SLF001
        projection=projection, logical_time=morning
    )
    assert old_profile.delay_candidates_seconds == (21_600, 25_200, 28_800)
    projection.relationship_states = receptive_states
    draw = compiler._random.draw(  # noqa: SLF001
        attempt_id=attempt_id,
        candidate_refs=tuple(
            f"delay:{seconds}" for seconds in old_profile.delay_candidates_seconds
        ),
        candidate_weights=old_profile.candidate_weights,
        weight_policy_version=compiler._context.version,  # noqa: SLF001
        catalog_version="social-initiative-post-silent-delay.1",
        logical_time=morning,
        seed_instant=completion_event.logical_time,
        actor="system:social-initiative",
        trace_id="trace:social-initiative:post-silent:wedge",
        correlation_id="correlation:social-initiative:post-silent:wedge",
    )
    assert draw.candidate_refs == ("delay:21600", "delay:25200", "delay:28800")
    selected = int(draw.selected_candidate_ref.removeprefix("delay:"))
    due_at = completion_event.logical_time + timedelta(seconds=28_800 + 3_600)
    projection.logical_time = due_at
    current_profile = compiler._context.compile(  # noqa: SLF001
        projection=projection, logical_time=due_at
    )
    assert current_profile.delay_candidates_seconds == (3_600, 5_400, 7_200)
    # Exactly the state the old guard raised on.
    assert selected not in current_profile.delay_candidates_seconds
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=source.event_id,
            event_type=source.event_type,
            logical_time=source.logical_time,
            world_revision=1,
        ),
        SimpleNamespace(
            event_id="event:random-draw:" + draw.draw_id,
            event_type="RandomDrawRecorded",
            logical_time=morning,
            world_revision=1,
        ),
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("a committed post-silent draw must be replayed, not re-minted")
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "post_silent"
    assert opportunity.consideration_id == post_silent_consideration_id(
        attempt_id=attempt_id,
        delay_seconds=selected,
        epoch=0,
        prior_trigger_id=silent_trigger_id,
    )
    assert opportunity.scheduled_for == completion_event.logical_time + timedelta(
        seconds=selected
    )


@pytest.mark.asyncio
async def test_post_silent_draw_outside_its_own_candidates_still_fails_loudly() -> None:
    """The replayed-draw check still rejects a delay its own candidates exclude.

    ``RandomDrawRecordedPayload`` already refuses a selection outside its own
    candidate set, so a committed draw can never carry this state; the guard is
    exercised with a draw object the sampler itself could not have produced.
    """

    compiler, projection, _committed = _compiler_fixture(receptive=True)
    morning = datetime(2026, 7, 18, 1, 0, tzinfo=UTC)
    source = _post_silent_morning_source(projection, morning=morning)
    projection.logical_time = morning
    completion_event = _attach_silent_epoch(
        compiler,
        projection,
        source=source,
        completion_at=morning,
        trigger_id="trigger:proactive:silent-bad-draw",
    )
    projection.logical_time = completion_event.logical_time + timedelta(hours=10)
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=source.event_id,
            event_type=source.event_type,
            logical_time=source.logical_time,
            world_revision=1,
        ),
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            # Outside its own recorded candidates and outside the currently
            # compiled band, so neither the replay authority nor a recompiled
            # profile can vouch for it.
            selected_candidate_ref="delay:99999",
            candidate_refs=("delay:21600", "delay:25200", "delay:28800"),
        )
    )

    with pytest.raises(ValueError) as raised:
        await compiler.next_opportunity(projection)
    assert "post-silent initiative draw selected an unknown delay" in str(raised.value)


def _advance_past_ambient(compiler, projection, *, hours: float = 40.0):
    """Move logical time past the short ambient window with a ClockAdvanced."""

    clock_at = NOW + timedelta(hours=hours)
    clock = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=f"event:clock:long-silence:{hours}",
        world_id=projection.world_id,
        event_type="ClockAdvanced",
        logical_time=clock_at,
        created_at=clock_at,
        actor="system:clock",
        source="test",
        trace_id="trace:long-silence",
        causation_id="cause:long-silence",
        correlation_id="conversation:long-silence",
        idempotency_key=f"clock:long-silence:{hours}",
        payload={
            "logical_time_from": NOW.isoformat(),
            "logical_time_to": clock_at.isoformat(),
        },
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001

    def lookup(event_id):  # type: ignore[no-untyped-def]
        if event_id == clock.event_id:
            return clock, SimpleNamespace(world_revision=3)
        return original_lookup(event_id)

    compiler._ledger.lookup_event_commit = lookup  # type: ignore[attr-defined]
    projection.logical_time = clock_at
    existing = tuple(projection.committed_world_event_refs)
    projection.committed_world_event_refs = existing + (
        SimpleNamespace(
            event_id=clock.event_id,
            event_type=clock.event_type,
            logical_time=clock.logical_time,
            world_revision=3,
        ),
    )
    return clock


@pytest.mark.asyncio
async def test_long_silence_mints_after_ambient_closes() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    _advance_past_ambient(compiler, projection, hours=40)
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:21600",
            draw_id="draw:long-silence",
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "long_silence"
    assert "budget:shared_outreach" in opportunity.cadence_reason_codes
    assert opportunity.consideration_id.startswith(
        "consideration:social-initiative:long-silence:"
    )


@pytest.mark.asyncio
async def test_situation_independent_mints_after_ambient_closes() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    clock = _advance_past_ambient(compiler, projection, hours=20)
    occurred_at = clock.logical_time - timedelta(minutes=5)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:post-ambient",
        world_id=projection.world_id,
        event_type="ExperienceCommitted",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:post-ambient",
        causation_id="cause:post-ambient",
        correlation_id="conversation:post-ambient",
        idempotency_key="experience:post-ambient",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001

    def lookup(event_id):  # type: ignore[no-untyped-def]
        if event_id == stimulus.event_id:
            return stimulus, SimpleNamespace(world_revision=2)
        return original_lookup(event_id)

    compiler._ledger.lookup_event_commit = lookup  # type: ignore[attr-defined]
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=stimulus.event_id,
            event_type=stimulus.event_type,
            logical_time=stimulus.logical_time,
            world_revision=2,
        ),
        *projection.committed_world_event_refs,
    )
    draws: list[dict[str, object]] = []
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **kwargs: (
            draws.append(kwargs)
            or (_ for _ in ()).throw(AssertionError("situation mint must not draw"))
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "situation_change"
    assert opportunity.source_id.startswith("situation-independent:")
    assert "occasion:situation_independent" in opportunity.cadence_reason_codes
    assert draws == []


@pytest.mark.asyncio
async def test_shared_outreach_daily_limit_blocks_third_mint() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    clock = _advance_past_ambient(compiler, projection, hours=40)
    # The refreshed shared budget allows two life-driven considers per local
    # day.  Two long-silence considerations already ran on this day, so a
    # third mint must be blocked even with a fresh delay draw.
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:consideration:social-initiative:long-silence:"
            + "a" * 64,
            source_evidence_ref=clock.event_id,
            state="terminal",
            runtime_outcome_ref="proactive:silent",
        ),
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:consideration:social-initiative:long-silence:"
            + "b" * 64,
            source_evidence_ref=clock.event_id,
            state="terminal",
            runtime_outcome_ref="proactive:silent",
        ),
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:86400",
            draw_id="draw:long-silence-limit-3",
        )
    )

    blocked = await compiler.next_opportunity(projection)

    assert blocked is None
    assert compiler._shared_outreach_uses_on_local_day(  # noqa: SLF001
        projection, clock.logical_time
    ) == 2


@pytest.mark.asyncio
async def test_affect_episode_high_point_mints_situation_independent() -> None:
    """A life-authored emotional high point may open a shared-budget consider."""

    compiler, projection, _committed = _compiler_fixture(receptive=True)
    clock = _advance_past_ambient(compiler, projection, hours=20)
    occurred_at = clock.logical_time - timedelta(minutes=5)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:affect:post-ambient",
        world_id=projection.world_id,
        event_type="AffectEpisodeOpened",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:post-ambient-affect",
        causation_id="cause:post-ambient-affect",
        correlation_id="conversation:post-ambient-affect",
        idempotency_key="affect:post-ambient",
        payload={"episode": {"status": "active"}},
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001

    def lookup(event_id):  # type: ignore[no-untyped-def]
        if event_id == stimulus.event_id:
            return stimulus, SimpleNamespace(world_revision=2)
        return original_lookup(event_id)

    compiler._ledger.lookup_event_commit = lookup  # type: ignore[attr-defined]
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=stimulus.event_id,
            event_type=stimulus.event_type,
            logical_time=stimulus.logical_time,
            world_revision=2,
        ),
        *projection.committed_world_event_refs,
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is not None
    assert opportunity.source_kind == "situation_change"
    assert opportunity.source_id.startswith("situation-independent:")
    assert "occasion:situation_independent" in opportunity.cadence_reason_codes


@pytest.mark.asyncio
async def test_situation_change_still_does_not_mint_inside_ambient_window() -> None:
    """Regression: within 12h, situation materials hitch only — no dedicated mint."""

    compiler, projection, _committed = _compiler_fixture(receptive=True)
    occurred_at = NOW + timedelta(minutes=5)
    stimulus = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:experience:inside-window",
        world_id="world:social-context-test",
        event_type="ExperienceCommitted",
        logical_time=occurred_at,
        created_at=occurred_at,
        actor="actor:companion",
        source="test",
        trace_id="trace:inside-window",
        causation_id="cause:inside-window",
        correlation_id="conversation:inside-window",
        idempotency_key="experience:inside-window",
        payload=COMPANION_EXPERIENCE_STIMULUS,
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001
    compiler._ledger.lookup_event_commit = lambda event_id: (  # type: ignore[attr-defined]
        (stimulus, SimpleNamespace(world_revision=2))
        if event_id == stimulus.event_id
        else original_lookup(event_id)
    )
    projection.committed_world_event_refs = (
        SimpleNamespace(
            event_id=stimulus.event_id,
            event_type=stimulus.event_type,
            logical_time=stimulus.logical_time,
            world_revision=2,
        ),
    )
    projection.logical_time = occurred_at + timedelta(minutes=3)
    draws: list[dict[str, object]] = []
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **kwargs: (
            draws.append(kwargs)
            or (_ for _ in ()).throw(
                AssertionError("situation_change must not draw a delay")
            )
        )
    )

    opportunity = await compiler.next_opportunity(projection)

    assert opportunity is None
    assert draws == []


@pytest.mark.asyncio
async def test_consecutive_silents_add_extra_cooldown() -> None:
    compiler, projection, _committed = _compiler_fixture(receptive=True)
    day1 = _advance_past_ambient(compiler, projection, hours=40)
    first_id = (
        "consideration:social-initiative:long-silence:" + "b" * 64
    )
    second_id = (
        "consideration:social-initiative:long-silence:" + "c" * 64
    )
    projection.trigger_processes = (
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:" + first_id,
            source_evidence_ref=day1.event_id,
            state="terminal",
            runtime_outcome_ref="proactive:silent",
        ),
        SimpleNamespace(
            process_kind="proactive_action_deliberation",
            trigger_ref="proactive-consideration:" + second_id,
            source_evidence_ref=day1.event_id,
            state="terminal",
            runtime_outcome_ref="proactive:silent",
        ),
    )
    # Same local day already at limit; move to next day but only 12h later —
    # silent streak adds +24h, so 12h is still blocked.
    later = day1.logical_time + timedelta(hours=12)
    clock2 = WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id="event:clock:silent-backoff",
        world_id=projection.world_id,
        event_type="ClockAdvanced",
        logical_time=later,
        created_at=later,
        actor="system:clock",
        source="test",
        trace_id="trace:silent-backoff",
        causation_id="cause:silent-backoff",
        correlation_id="conversation:silent-backoff",
        idempotency_key="clock:silent-backoff",
        payload={
            "logical_time_from": day1.logical_time.isoformat(),
            "logical_time_to": later.isoformat(),
        },
    )
    original_lookup = compiler._ledger.lookup_event_commit  # noqa: SLF001

    def lookup(event_id):  # type: ignore[no-untyped-def]
        if event_id == clock2.event_id:
            return clock2, SimpleNamespace(world_revision=4)
        return original_lookup(event_id)

    compiler._ledger.lookup_event_commit = lookup  # type: ignore[attr-defined]
    projection.logical_time = later
    projection.committed_world_event_refs = projection.committed_world_event_refs + (
        SimpleNamespace(
            event_id=clock2.event_id,
            event_type=clock2.event_type,
            logical_time=clock2.logical_time,
            world_revision=4,
        ),
    )
    compiler._random = SimpleNamespace(  # noqa: SLF001
        draw=lambda **_kwargs: SimpleNamespace(
            selected_candidate_ref="delay:21600",
            draw_id="draw:silent-backoff",
        )
    )

    assert compiler._shared_outreach_silent_streak(projection) == 2  # noqa: SLF001
    assert compiler._shared_outreach_budget_allows(projection, later) is False  # noqa: SLF001
    # After min interval (6h) + silent extra (24h) = 30h from last shared source.
    far = day1.logical_time + timedelta(hours=31)
    assert compiler._shared_outreach_budget_allows(projection, far) is True  # noqa: SLF001
