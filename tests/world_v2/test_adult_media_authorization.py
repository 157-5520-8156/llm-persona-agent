"""Adult P3 media is an explicit, default-off eligibility gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.config import Settings
from companion_daemon.media_eligibility import (
    MediaEligibilityRouter,
    MediaLaneRecommendation,
)
from companion_daemon.media_suggestive_lane import EXPLICIT_PRIVATE_LANE, SUGGESTIVE_PRIVATE_LANE
from companion_daemon.world_v2.adult_media_authority import (
    ADULT_MEDIA_CAPABILITY_ID,
    ADULT_MEDIA_CONSENT_ID,
)
from companion_daemon.world_v2.appearance_state import (
    AppearanceStateProjection,
    AppearanceStateRecordedPayload,
    VisibleAppearanceAttribute,
)
from companion_daemon.world_v2.declared_display_runtime import (
    DeclaredDisplayCommand,
    DeclaredDisplayRuntime,
)
from companion_daemon.world_v2.event_media_planner_adapter import EventMediaPlannerAdapter
from companion_daemon.world_v2.media_evidence_snapshot import MediaEvidenceSnapshotCompiler
from companion_daemon.world_v2.media_opportunity_authorizer import MediaOpportunityAuthorizer
from companion_daemon.world_v2.media_selection import MediaSelection
from companion_daemon.world_v2.media_v2 import (
    CharacterMediaCandidateContract,
    MediaEvidenceSource,
    PhotoCandidate,
    character_media_contract_digest,
)
from companion_daemon.world_v2.private_image_evidence_contract import (
    RecipientScopedImageEvidenceDeclaredPayload,
    RecipientScopedImageEvidenceV1,
)
from companion_daemon.world_v2.schemas import (
    CommittedWorldEventRef,
    ProjectionCursor,
    RelationshipStateOrigin,
    RelationshipStateProjection,
    WorldEvent,
)
from companion_daemon.world_v2.visible_physical_state import (
    VisiblePhysicalCue,
    VisiblePhysicalStateProjection,
    VisiblePhysicalStateRecordedPayload,
)


NOW = datetime(2026, 7, 16, 21, tzinfo=UTC)
WORLD = "world:adult-media-authorization"
SOURCE_ID = "event:activity:private-wind-down"
DECLARATION_ID = "event:recipient-evidence:private-wind-down"
PHYSICAL_RECORD_ID = "event:physical:private-wind-down"
APPEARANCE_RECORD_ID = "event:appearance:private-wind-down"
RELATIONSHIP_ORIGIN_ID = "event:relationship:origin"
CURSOR = ProjectionCursor(world_revision=4, deliberation_revision=0, ledger_sequence=4)


def _event(event_id: str, event_type: str, payload: dict[str, object]) -> WorldEvent:
    return WorldEvent.from_payload(
        schema_version="world-v2.1",
        event_id=event_id,
        event_type=event_type,
        world_id=WORLD,
        logical_time=NOW,
        created_at=NOW,
        actor="worker:test",
        source="test:adult-media",
        trace_id="trace:adult-media",
        causation_id="cause:" + event_id,
        correlation_id="correlation:adult-media",
        idempotency_key="idempotency:" + event_id,
        payload=payload,
    )


class _Ledger:
    def __init__(self, projection, events: tuple[WorldEvent, ...]) -> None:
        self.world_id = WORLD
        self._projection = projection
        self._events = {event.event_id: event for event in events}

    def project(self):
        return self._projection

    def project_at(self, cursor: ProjectionCursor):
        assert cursor == CURSOR
        return self._projection

    def lookup_event_commit(self, event_id: str):
        event = self._events.get(event_id)
        return (event, None) if event is not None else None

    def commit_at_cursor(self, events, *, expected_cursor, commit_id):  # type: ignore[no-untyped-def]
        del commit_id
        cursor = ProjectionCursor(
            world_revision=self._projection.world_revision,
            deliberation_revision=self._projection.deliberation_revision,
            ledger_sequence=self._projection.ledger_sequence,
        )
        assert expected_cursor == cursor
        committed = tuple(events)
        refs = list(self._projection.committed_world_event_refs)
        for event in committed:
            self._events[event.event_id] = event
            refs.append(
                CommittedWorldEventRef(
                    event_id=event.event_id,
                    event_type=event.event_type,
                    world_revision=self._projection.world_revision,
                    payload_hash=event.payload_hash,
                    logical_time=event.logical_time,
                )
            )
        self._projection.committed_world_event_refs = tuple(refs)
        return SimpleNamespace(
            events=committed,
            event_ids=tuple(event.event_id for event in committed),
        )


def _adult_grants() -> tuple[object, object]:
    capability = SimpleNamespace(
        grant_id=ADULT_MEDIA_CAPABILITY_ID,
        values=SimpleNamespace(
            capability_kind="media_render",
            state="active",
            valid_from=NOW - timedelta(hours=1),
            expires_at=None,
        ),
    )
    consent = SimpleNamespace(
        consent_id=ADULT_MEDIA_CONSENT_ID,
        values=SimpleNamespace(
            action_scope_refs=("media_render",),
            status="active",
            valid_from=NOW - timedelta(hours=1),
            expires_at=None,
        ),
    )
    return capability, consent


def _p3_world(
    *,
    stage: str,
    with_adult_grants: bool,
    display_intent: str | None = None,
    display_recipient: str = "user:1",
    withdrawn: bool = False,
) -> tuple[_Ledger, PhotoCandidate]:
    source = _event(SOURCE_ID, "ActivityCompleted", {"status": "committed"})
    evidence = RecipientScopedImageEvidenceDeclaredPayload(
        source_event_ref=SOURCE_ID,
        source_event_payload_hash=source.payload_hash,
        source_event_type="ActivityCompleted",
        source_privacy_ceiling="private",
        recipient_ref="user:1",
        image_evidence=RecipientScopedImageEvidenceV1(
            visibility="private",
            location={"kind": "private", "mirror_available": True},
            activity={
                "id": "activity:wind-down",
                "kind": "wind_down",
                "private_transition": True,
            },
            character_media={
                "character_ref": "character:ava",
                "present": True,
                "capture_capabilities": ("character_front_camera",),
            },
        ),
        declared_at=NOW,
    )
    declaration = _event(
        DECLARATION_ID,
        "RecipientScopedImageEvidenceDeclared",
        evidence.model_dump(mode="json"),
    )
    physical = VisiblePhysicalStateProjection(
        physical_state_id="physical:wind-down",
        subject_ref="character:ava",
        entity_revision=1,
        source_event_ref=SOURCE_ID,
        source_event_payload_hash=source.payload_hash,
        source_event_type="ActivityCompleted",
        valid_from=NOW - timedelta(minutes=5),
        valid_until=NOW + timedelta(minutes=20),
        visibility="private",
        positive_cues=(
            VisiblePhysicalCue(cue_id="damp_hair", intensity="light", visible_regions=("hair",)),
        ),
        negative_cues=(),
    )
    physical_record = _event(
        PHYSICAL_RECORD_ID,
        "VisiblePhysicalStateRecorded",
        VisiblePhysicalStateRecordedPayload(state=physical).model_dump(mode="json"),
    )
    appearance = AppearanceStateProjection(
        appearance_state_id="appearance:character:ava",
        subject_ref="character:ava",
        entity_revision=1,
        source_event_ref=SOURCE_ID,
        source_event_payload_hash=source.payload_hash,
        source_event_type="ActivityCompleted",
        valid_from=NOW - timedelta(minutes=5),
        valid_until=NOW + timedelta(minutes=20),
        visibility="private",
        visible_attributes=(
            VisibleAppearanceAttribute(aspect="outfit", description="bathrobe"),
            VisibleAppearanceAttribute(
                aspect="coverage", description="private_apparel sleepwear"
            ),
        ),
    )
    appearance_record = _event(
        APPEARANCE_RECORD_ID,
        "AppearanceStateRecorded",
        AppearanceStateRecordedPayload(state=appearance).model_dump(mode="json"),
    )
    relationship_origin = _event(
        RELATIONSHIP_ORIGIN_ID, "RelationshipSlowVariableAdjusted", {"opaque": "origin"}
    )
    relationship = RelationshipStateProjection(
        relationship_id="relationship:user:1",
        subject_ref="user:1",
        entity_revision=2,
        stage=stage,
        policy_digest="c" * 64,
        origin=RelationshipStateOrigin(
            change_id="change:relationship:1",
            transition_id="transition:relationship:1",
            policy_refs=("policy:relationship",),
            accepted_event_ref=RELATIONSHIP_ORIGIN_ID,
        ),
    )
    sources = tuple(
        MediaEvidenceSource(event_ref=event.event_id, payload_hash=event.payload_hash)
        for event in (source, declaration)
    )
    contract = CharacterMediaCandidateContract(
        subject_ref="character:ava",
        kind="selfie",
        allowed_capture_modes=("character_front_camera",),
        allowed_character_visibility=("identifiable",),
        authority_digest=character_media_contract_digest(
            subject_ref="character:ava",
            kind="selfie",
            source_events=sources,
            allowed_capture_modes=("character_front_camera",),
            allowed_character_visibility=("identifiable",),
        ),
    )
    candidate = PhotoCandidate(
        candidate_id="candidate:private-selfie",
        source_event_refs=tuple(item.event_ref for item in sources),
        family="character_media",
        privacy_ceiling="private",
        opened_at=NOW,
        expires_at=NOW + timedelta(hours=1),
        ecology_category="character_media:private-selfie",
        ecology_observed_at=NOW,
        source_events=sources,
        opened_event_ref="event:candidate:private",
        opened_event_payload_hash="d" * 64,
        character_media_contract=contract,
    )
    extra: list[WorldEvent] = []
    observation: WorldEvent | None = None
    if display_intent is not None:
        observation = _event(
            "event:observation:display",
            "ObservationRecorded",
            {"observation_id": "obs:display", "actor": display_recipient},
        )
        extra.append(observation)
    events = (source, declaration, physical_record, appearance_record, relationship_origin, *extra)
    refs = tuple(
        CommittedWorldEventRef(
            event_id=event.event_id,
            event_type=event.event_type,
            world_revision=index + 1,
            payload_hash=event.payload_hash,
            logical_time=NOW,
        )
        for index, event in enumerate(events)
    )
    grants = _adult_grants() if with_adult_grants else ()
    projection = SimpleNamespace(
        world_revision=4,
        deliberation_revision=0,
        ledger_sequence=4,
        logical_time=NOW,
        committed_world_event_refs=refs,
        relationship_states=(relationship,),
        visible_physical_states=(physical,),
        appearance_states=(appearance,),
        photo_candidates=(candidate,),
        capability_grants=(grants[0],) if grants else (),
        consent_grants=(grants[1],) if grants else (),
    )
    ledger = _Ledger(projection, events)
    if display_intent is not None:
        assert observation is not None
        recorded = DeclaredDisplayRuntime(ledger=ledger).declare(
            DeclaredDisplayCommand(
                command_id="command:display:test",
                source_event_ref=observation.event_id,
                media_intent=display_intent,  # type: ignore[arg-type]
            ),
            logical_time=NOW,
            created_at=NOW,
            actor="worker:declared-display",
            trace_id="trace:adult-media",
            correlation_id="correlation:adult-media",
        )
        if withdrawn:
            DeclaredDisplayRuntime(ledger=ledger).declare(
                DeclaredDisplayCommand(
                    command_id="command:display:withdraw",
                    source_event_ref=observation.event_id,
                    withdraw=True,
                ),
                logical_time=NOW,
                created_at=NOW,
                actor="worker:declared-display",
                trace_id="trace:adult-media",
                correlation_id="correlation:adult-media",
                prior_event_ref=recorded.event_ids[0],
            )
    return ledger, candidate


def _authorize(ledger: _Ledger, candidate: PhotoCandidate, *, adult_media_enabled: bool):
    return MediaOpportunityAuthorizer(
        ledger=ledger,
        compiler=MediaEvidenceSnapshotCompiler(ledger=ledger),
        catalog_version="test-adult-p3.1",
        adult_media_enabled=adult_media_enabled,
    ).authorize(
        cursor=CURSOR,
        selection=MediaSelection(
            candidate_id=candidate.candidate_id,
            family="character_media",
            media_privacy_ceiling="intimate",
            expression_charge_ceiling="subtle",
            recipient_ref="user:1",
            private_expression_basis_ref="basis:transition:" + DECLARATION_ID,
        ),
        category=candidate.ecology_category or "private",
        observed_at=NOW,
        expires_at=candidate.expires_at,
    )


def test_settings_adult_media_switch_defaults_off() -> None:
    assert Settings.model_fields["world_v2_adult_media_enabled"].default is False


@pytest.mark.parametrize("stage", ("close_friend", "ambiguous", "lover"))
def test_authorizer_uses_suggestive_private_when_she_declares_sexual_suggestive(stage: str) -> None:
    """His grant opens the lane; her sexual_suggestive declaration picks intensity.

    Replaces the H26 assertion that eligibility alone emitted explicit_private.
    That mixed permission with intensity and would mismatch her measured
    willingness (12/12 declarations were sexual_suggestive, never explicit_adult).
    """

    ledger, candidate = _p3_world(
        stage=stage, with_adult_grants=True, display_intent="sexual_suggestive"
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "suggestive_private"
    assert compiled.snapshot.private_media_authorization.media_lane == "suggestive_private"
    assert compiled.snapshot.private_media_authorization.expression_charge_ceiling == "charged"
    display = compiled.snapshot.image_event_snapshot.relationship_media_context.declared_display
    assert display is not None and display.media_intent == "sexual_suggestive"
    dumped = compiled.snapshot.image_event_snapshot.model_dump(mode="json")
    appearance = dumped["character"]["appearance_state"]
    assert appearance["visible_attributes"][0]["description"] == "bathrobe"
    assert dumped["location"]["kind"] == "private"
    decision = MediaEligibilityRouter().classify_recommendation(
        family="character_media",
        privacy_ceiling="intimate",
        expression_charge_ceiling="veiled",
        event_snapshot=dumped,
        private_expression_basis=EventMediaPlannerAdapter._p3_basis(dumped),
        recipient_ref="user:1",
        recommendation=MediaLaneRecommendation(
            lane=SUGGESTIVE_PRIVATE_LANE,
            recipient_access="recipient_exclusive",
            attraction_expression="sexual_suggestive",
        ),
        selected_expression_charge="charged",
        selected_capture_mode="character_front_camera",
        selected_share_intent="intimate_signal",
        selected_privacy="intimate",
        selected_address_mode="direct_recipient",
        selected_interaction_bid="invite_desire",
        selected_attraction_mechanism="private_trust",
        selected_coverage_mode="private_apparel",
    )
    assert decision.allowed and decision.lane == SUGGESTIVE_PRIVATE_LANE


def test_veiled_is_the_higher_charge_not_a_weaker_cover() -> None:
    """``veiled`` looks like 'covered'; in this rank it is the explicit_adult ceiling."""

    from companion_daemon.media_eligibility import CHARGE_RANK

    suggestive_lane, suggestive_charge = MediaOpportunityAuthorizer._p3_lane_for_stage(
        "close_friend", adult_eligible=True, declared_intent="sexual_suggestive"
    )
    explicit_lane, explicit_charge = MediaOpportunityAuthorizer._p3_lane_for_stage(
        "close_friend", adult_eligible=True, declared_intent="explicit_adult"
    )
    assert (suggestive_lane, suggestive_charge) == ("suggestive_private", "charged")
    assert (explicit_lane, explicit_charge) == ("explicit_private", "veiled")
    assert CHARGE_RANK[explicit_charge] > CHARGE_RANK[suggestive_charge]


@pytest.mark.parametrize("stage", ("close_friend", "ambiguous", "lover"))
def test_authorizer_uses_explicit_private_when_she_declares_explicit_adult(stage: str) -> None:
    ledger, candidate = _p3_world(
        stage=stage, with_adult_grants=True, display_intent="explicit_adult"
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "explicit_private"
    assert compiled.snapshot.private_media_authorization.media_lane == "explicit_private"
    assert compiled.snapshot.private_media_authorization.expression_charge_ceiling == "veiled"
    dumped = compiled.snapshot.image_event_snapshot.model_dump(mode="json")
    decision = MediaEligibilityRouter().classify_recommendation(
        family="character_media",
        privacy_ceiling="intimate",
        expression_charge_ceiling="veiled",
        event_snapshot=dumped,
        private_expression_basis=EventMediaPlannerAdapter._p3_basis(dumped),
        recipient_ref="user:1",
        recommendation=MediaLaneRecommendation(
            lane=EXPLICIT_PRIVATE_LANE,
            recipient_access="recipient_exclusive",
            attraction_expression="explicit_adult",
        ),
        selected_expression_charge="veiled",
        selected_capture_mode="character_front_camera",
        selected_share_intent="intimate_signal",
        selected_privacy="intimate",
        selected_address_mode="direct_recipient",
        selected_interaction_bid="invite_desire",
        selected_attraction_mechanism="private_trust",
        selected_coverage_mode="private_apparel",
    )
    assert decision.allowed and decision.lane == EXPLICIT_PRIVATE_LANE


@pytest.mark.parametrize("stage", ("close_friend", "ambiguous", "lover"))
def test_authorizer_falls_back_to_alluring_life_when_she_omits_a_declaration(stage: str) -> None:
    """Omission is legal and common; it must not fail-closed as a missing intent."""

    ledger, candidate = _p3_world(stage=stage, with_adult_grants=True)
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "alluring_life"
    assert compiled.snapshot.private_media_authorization.media_lane == "alluring_life"
    assert compiled.snapshot.private_media_authorization.expression_charge_ceiling == "subtle"
    context = compiled.snapshot.image_event_snapshot.relationship_media_context
    assert context.declared_display is None


def test_authorizer_falls_back_after_she_withdraws_a_declaration() -> None:
    ledger, candidate = _p3_world(
        stage="lover",
        with_adult_grants=True,
        display_intent="sexual_suggestive",
        withdrawn=True,
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "alluring_life"
    assert compiled.snapshot.image_event_snapshot.relationship_media_context.declared_display is None


def test_authorizer_ignores_a_declaration_bound_to_a_different_recipient() -> None:
    ledger, candidate = _p3_world(
        stage="lover",
        with_adult_grants=True,
        display_intent="explicit_adult",
        display_recipient="user:other",
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "alluring_life"
    assert compiled.snapshot.image_event_snapshot.relationship_media_context.declared_display is None


def test_authorizer_fail_closes_adult_lane_below_close_friend() -> None:
    ledger, candidate = _p3_world(
        stage="friend", with_adult_grants=True, display_intent="explicit_adult"
    )
    with pytest.raises(ValueError, match="p3_relationship_stage_not_eligible"):
        _authorize(ledger, candidate, adult_media_enabled=True)


def test_authorizer_fail_closes_adult_lane_when_switch_is_off() -> None:
    ledger, candidate = _p3_world(
        stage="close_friend", with_adult_grants=True, display_intent="sexual_suggestive"
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=False)
    assert opportunity.media_lane == "alluring_life"
    assert compiled.snapshot.private_media_authorization.media_lane == "alluring_life"


def test_authorizer_fail_closes_adult_lane_without_ledger_grant() -> None:
    ledger, candidate = _p3_world(
        stage="close_friend", with_adult_grants=False, display_intent="explicit_adult"
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "alluring_life"
    assert compiled.snapshot.private_media_authorization.media_lane == "alluring_life"


def test_close_friend_adapter_sets_charged_ceiling_and_declared_display_parent() -> None:
    """Production planner wiring: charge ceiling + host-derived display parent.

    close_friend is the user-required floor.  Intensity still comes from her
    declaration (sexual_suggestive → charged), not from the adapter.
    """

    import asyncio

    from companion_daemon.media_eligibility import DECLARED_DISPLAY_EVIDENCE_REF
    from companion_daemon.world_v2.media_v2 import (
        InMemoryImmutableMediaPayloadStore,
        MediaPlanningResult,
        StoredMediaPayload,
        planning_request_id,
    )

    class _ResultStore:
        def __init__(self) -> None:
            self.values: dict[str, MediaPlanningResult] = {}

        async def lookup(self, *, planning_request_id: str) -> MediaPlanningResult | None:
            return self.values.get(planning_request_id)

        async def put_if_absent(self, *, planning_request_id: str, result: MediaPlanningResult) -> None:
            self.values.setdefault(planning_request_id, result)

    class _LegacyPlanner:
        def __init__(self) -> None:
            self.calls: list[object] = []

        async def plan(self, opportunity, recent_media=()):  # type: ignore[no-untyped-def]
            assert recent_media == ()
            self.calls.append(opportunity)
            from companion_daemon import event_media

            return event_media.NotRenderable(opportunity.opportunity_id, "fixture_stop")

    ledger, candidate = _p3_world(
        stage="close_friend", with_adult_grants=True, display_intent="sexual_suggestive"
    )
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    sidecar = InMemoryImmutableMediaPayloadStore()
    sidecar.put_if_absent(
        StoredMediaPayload(
            payload_ref=opportunity.event_snapshot_ref,
            payload_hash=opportunity.event_snapshot_hash,
            content_type="application/vnd.world-v2.media-opportunity+json",
            body=compiled.snapshot_body,
        )
    )
    legacy = _LegacyPlanner()
    adapter = EventMediaPlannerAdapter(
        sidecar=sidecar, legacy_planner=legacy, result_store=_ResultStore()
    )
    result = asyncio.run(
        adapter.plan(
            opportunity=opportunity,
            planning_request_id=planning_request_id(opportunity.opportunity_id),
        )
    )
    assert result.not_renderable is not None
    assert result.not_renderable.reason_code == "fixture_stop"
    call = legacy.calls[0]
    assert call.sensual_charge_ceiling == "charged"
    assert call.expression_charge_ceiling == "charged"
    assert DECLARED_DISPLAY_EVIDENCE_REF in call.allowed_evidence_refs
    assert call.audience_context.relationship_stage == "close_friend"
    dumped = compiled.snapshot.image_event_snapshot.model_dump(mode="json")
    assert dumped["character"]["appearance_state"]["visible_attributes"][0]["description"] == "bathrobe"
