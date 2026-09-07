from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json

import pytest
from pydantic import ValidationError

from companion_daemon.world_v2.schema_core import EvidenceRef
from companion_daemon.world_v2.schemas import (
    ClockTransitionProjection,
    CommittedWorldEventRef,
    DueWindow,
    PlanAuthorityOrigin,
    PlanStateProjection,
    plan_authority_binding_hash,
    plan_authority_projection_hash,
)
from companion_daemon.world_v2.situation_compiler import (
    BoundPlanHead,
    SituationAuthoritySnapshot,
    SituationCompileRequest,
    SituationCompiler,
    SourceBinding,
    default_internal_viewer_scope,
    default_situation_policy,
    viewer_scope,
)


NOW = datetime(2026, 9, 7, 10, tzinfo=UTC)
ACTOR = "actor:companion"


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _plan(
    name: str,
    *,
    status: str = "planned",
    participant_refs: tuple[str, ...] = ("npc:future-friend",),
    scheduled_window: DueWindow | None = None,
    privacy_class: str = "private",
    importance_bp: int = 6000,
    accepted_at: datetime = NOW - timedelta(hours=1),
    actor_ref: str = ACTOR,
    source_revision: int = 2,
) -> BoundPlanHead:
    event_type = {
        "planned": "ActivityPlanned",
        "active": "ActivityStarted",
        "paused": "ActivityPaused",
        "completed": "ActivityCompleted",
        "abandoned": "ActivityAbandoned",
    }[status]
    head = PlanStateProjection(
        plan_id=f"plan:{name}",
        activity_id=f"activity:{name}",
        entity_revision=1 if status == "planned" else 2,
        activity_kind="reading",
        evidence_refs=(
            EvidenceRef(
                ref_id=f"message:{name}",
                evidence_type="observed_message",
                claim_purpose="future_plan",
            ),
        ),
        status=status,
        importance_bp=importance_bp,
        scheduled_window=scheduled_window,
        participant_refs=participant_refs,
        location_ref="location:library",
        privacy_class=privacy_class,
        owner_actor_ref=actor_ref,
        last_transitioned_at=None if status == "planned" else accepted_at,
    )
    source = SourceBinding(
        world_id="world:plan-presence",
        world_revision=source_revision,
        event_ref=f"event:{name}:{status}",
        payload_hash=_hash(f"payload:{name}:{status}"),
    )
    projection_hash = plan_authority_projection_hash(head)
    origin = PlanAuthorityOrigin(
        transition_id=f"transition:{name}:{status}",
        accepted_event_type=event_type,
        accepted_event_ref=source.event_ref,
        accepted_world_revision=source.world_revision,
        accepted_payload_hash=source.payload_hash,
        accepted_at=accepted_at,
        authority_projection_hash=projection_hash,
        binding_hash=plan_authority_binding_hash(
            plan_id=head.plan_id,
            owner_actor_ref=actor_ref,
            entity_revision=head.entity_revision,
            transition_id=f"transition:{name}:{status}",
            event_type=event_type,
            accepted_event_ref=source.event_ref,
            accepted_world_revision=source.world_revision,
            accepted_payload_hash=source.payload_hash,
            accepted_at=accepted_at,
            projection_hash=projection_hash,
        ),
    )
    head = head.model_copy(update={"authority_origin": origin})
    return BoundPlanHead(
        source=source,
        actor_ref=actor_ref,
        head=head,
        projection_hash=_hash(head.model_dump(mode="json")),
    )


def _request(*plans: BoundPlanHead) -> SituationCompileRequest:
    clock = SourceBinding(
        world_id="world:plan-presence",
        world_revision=100,
        event_ref="event:clock-current",
        payload_hash=_hash("clock-current"),
    )
    events = (
        CommittedWorldEventRef(
            event_id=clock.event_ref,
            event_type="ClockAdvanced",
            world_revision=clock.world_revision,
            payload_hash=clock.payload_hash,
            logical_time=NOW,
        ),
        *(
            CommittedWorldEventRef(
                event_id=plan.source.event_ref,
                event_type=plan.head.authority_origin.accepted_event_type,
                world_revision=plan.source.world_revision,
                payload_hash=plan.source.payload_hash,
                logical_time=plan.head.authority_origin.accepted_at,
            )
            for plan in plans
        ),
    )
    snapshot = SituationAuthoritySnapshot(
        world_id=clock.world_id,
        actor_ref=ACTOR,
        pinned_world_revision=clock.world_revision,
        logical_time=NOW,
        logical_time_source=clock,
        logical_clock_projection=ClockTransitionProjection(
            clock_event_ref=clock.event_ref,
            computed_world_revision=clock.world_revision,
            payload_hash=clock.payload_hash,
            logical_time_from=NOW - timedelta(hours=1),
            logical_time_to=NOW,
            installed_policy_version="clock-policy.test",
            installed_policy_digest=_hash("clock-policy"),
        ),
        committed_events=events,
        plans=plans,
    )
    return SituationCompileRequest(
        world_id=snapshot.world_id,
        actor_ref=ACTOR,
        pinned_world_revision=snapshot.pinned_world_revision,
        logical_time=NOW,
        authority_snapshot=snapshot,
        policy=default_situation_policy(),
        viewer_scope=default_internal_viewer_scope(),
    )


def test_future_plan_remains_visible_without_claiming_current_company() -> None:
    window = DueWindow(opens_at=NOW + timedelta(days=5), closes_at=NOW + timedelta(days=5, hours=1))
    result = SituationCompiler().compile(_request(_plan("future", scheduled_window=window)))

    assert result.internal is not None
    assert result.internal.social_environment.availability == "unavailable"
    assert result.internal.social_environment.reason == "no_authority"
    assert result.internal.social_environment.relation is None
    assert result.internal.social_environment.participant_refs == ()
    assert result.viewer_projection.social_environment == result.internal.social_environment
    assert len(result.internal.activity_slices) == 1
    assert result.internal.activity_slices[0].status == "planned"
    assert result.internal.activity_slices[0].participant_refs == ("npc:future-friend",)
    assert result.internal.activity_slices[0].window_opens_at == window.opens_at
    assert result.internal.plan_relation.relation == "planned_future"
    assert result.internal.plan_relation.plan_id == "plan:future"
    assert any(
        item.event_ref == "event:future:planned" for item in result.internal.source_revisions
    )


@pytest.mark.parametrize(
    ("status", "expected_activity", "expected_relation"),
    [
        ("planned", "planned", "planned_open"),
        ("paused", "paused", "paused"),
        ("completed", None, None),
        ("abandoned", None, None),
    ],
)
def test_due_or_inactive_plan_is_not_current_company(
    status: str, expected_activity: str | None, expected_relation: str | None
) -> None:
    window = DueWindow(opens_at=NOW - timedelta(minutes=30), closes_at=NOW + timedelta(minutes=30))
    result = SituationCompiler().compile(
        _request(_plan("meeting", status=status, scheduled_window=window))
    )

    assert result.internal is not None
    assert result.internal.social_environment.availability == "unavailable"
    assert result.internal.social_environment.relation is None
    assert result.internal.social_environment.participant_refs == ()
    assert [item.status for item in result.internal.activity_slices] == (
        [] if expected_activity is None else [expected_activity]
    )
    assert result.internal.plan_relation.relation == expected_relation


@pytest.mark.parametrize("window_end", [NOW - timedelta(minutes=30), NOW + timedelta(hours=1)])
def test_accepted_active_activity_supplies_company_until_an_accepted_end(
    window_end: datetime,
) -> None:
    window = DueWindow(opens_at=NOW - timedelta(hours=1), closes_at=window_end)
    result = SituationCompiler().compile(
        _request(
            _plan(
                "current",
                status="active",
                participant_refs=(ACTOR, "npc:current-friend", "npc:current-friend"),
                scheduled_window=window,
            )
        )
    )

    assert result.internal is not None
    assert result.internal.social_environment.availability == "available"
    assert result.internal.social_environment.relation == "with_others"
    assert result.internal.social_environment.participant_refs == ("npc:current-friend",)
    assert result.internal.plan_relation.relation == "active"
    assert result.internal.activity_slices[0].status == "active"
    assert result.internal.activity_slices[0].window_closes_at == window_end


@pytest.mark.parametrize("participants", [(), (ACTOR,)])
def test_absent_other_participants_do_not_prove_solitude(participants: tuple[str, ...]) -> None:
    result = SituationCompiler().compile(
        _request(_plan("reading", status="active", participant_refs=participants))
    )

    assert result.internal is not None
    assert result.internal.social_environment.availability == "unavailable"
    assert result.internal.social_environment.reason == "no_authority"
    assert result.internal.social_environment.relation is None
    assert result.internal.plan_relation.relation == "active"


def test_current_and_future_participants_keep_separate_presence_and_privacy() -> None:
    future = _plan(
        "future",
        scheduled_window=DueWindow(
            opens_at=NOW + timedelta(days=5), closes_at=NOW + timedelta(days=5, hours=1)
        ),
        importance_bp=9000,
        privacy_class="private",
    )
    current = _plan(
        "current",
        status="active",
        participant_refs=(ACTOR, "npc:current-friend"),
        importance_bp=4000,
        privacy_class="public",
        source_revision=3,
    )
    request = _request(future, current)
    internal = SituationCompiler().compile(request).internal
    assert internal is not None
    assert {item.plan_id for item in internal.activity_slices} == {"plan:future", "plan:current"}
    assert internal.social_environment.participant_refs == ("npc:current-friend",)
    assert internal.social_environment.privacy_class == "public"
    assert {item.entity_ref for item in internal.source_revisions if item.domain == "plan"} == {
        "plan:future",
        "plan:current",
    }

    public = (
        SituationCompiler()
        .compile(
            request.model_copy(
                update={
                    "viewer_scope": viewer_scope(
                        viewer_ref="viewer:public",
                        allowed_privacy_classes=("public", "shareable"),
                        max_items_per_collection=8,
                    )
                }
            )
        )
        .viewer_projection
    )
    assert public.social_environment.participant_refs == ("npc:current-friend",)
    assert public.social_environment.relation == "with_others"
    assert public.activity_slices[0].availability == "redacted"
    assert public.activity_slices[0].participant_refs == ()
    assert public.activity_slices[1].plan_id == "plan:current"
    assert "npc:future-friend" not in public.model_dump_json()


def test_private_current_company_is_redacted_from_external_projection() -> None:
    request = _request(_plan("private", status="active"))
    public = (
        SituationCompiler()
        .compile(
            request.model_copy(
                update={
                    "viewer_scope": viewer_scope(
                        viewer_ref="viewer:public",
                        allowed_privacy_classes=("public",),
                        max_items_per_collection=8,
                    )
                }
            )
        )
        .viewer_projection
    )

    assert public.social_environment.availability == "redacted"
    assert public.social_environment.participant_refs == ()
    assert public.social_environment.relation is None


def test_future_participants_cannot_fill_an_active_activitys_unknown_company() -> None:
    current = _plan("current", status="active", participant_refs=())
    future = _plan(
        "future",
        scheduled_window=DueWindow(
            opens_at=NOW + timedelta(days=5),
            closes_at=NOW + timedelta(days=5, hours=1),
        ),
        source_revision=3,
    )
    result = SituationCompiler().compile(_request(current, future))

    assert result.internal is not None
    assert len(result.internal.activity_slices) == 2
    assert result.internal.social_environment.availability == "unavailable"
    assert result.internal.social_environment.participant_refs == ()
    assert result.internal.social_environment.relation is None


def test_current_company_requires_the_exact_accepted_plan_source() -> None:
    current = _plan("current", status="active")
    tampered = current.model_copy(
        update={"source": current.source.model_copy(update={"payload_hash": "f" * 64})}
    )

    with pytest.raises(ValidationError, match="Plan authority binding is invalid"):
        SituationCompiler().compile(_request(tampered))


def test_plan_from_another_actor_cannot_supply_current_company() -> None:
    with pytest.raises(ValidationError, match="another actor"):
        SituationCompiler().compile(
            _request(_plan("other", status="active", actor_ref="actor:other"))
        )


def test_accepted_plan_authority_cannot_be_ahead_of_current_logical_time() -> None:
    with pytest.raises(ValidationError, match="ahead of authoritative logical time"):
        SituationCompiler().compile(
            _request(_plan("ahead", status="active", accepted_at=NOW + timedelta(minutes=1)))
        )
