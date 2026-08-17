"""Adult P3 media is an explicit, default-off eligibility gate."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.config import Settings
from companion_daemon.world_v2.adult_media_authority import (
    ADULT_MEDIA_CAPABILITY_ID,
    ADULT_MEDIA_CONSENT_ID,
)
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
        self._projection = projection
        self._events = {event.event_id: event for event in events}

    def project_at(self, cursor: ProjectionCursor):
        assert cursor == CURSOR
        return self._projection

    def lookup_event_commit(self, event_id: str):
        event = self._events.get(event_id)
        return (event, None) if event is not None else None


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


def _p3_world(*, stage: str, with_adult_grants: bool) -> tuple[_Ledger, PhotoCandidate]:
    source = _event(SOURCE_ID, "ActivityCompleted", {"status": "committed"})
    evidence = RecipientScopedImageEvidenceDeclaredPayload(
        source_event_ref=SOURCE_ID,
        source_event_payload_hash=source.payload_hash,
        source_event_type="ActivityCompleted",
        source_privacy_ceiling="private",
        recipient_ref="user:1",
        image_evidence=RecipientScopedImageEvidenceV1(
            visibility="private",
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
    events = (source, declaration, physical_record, relationship_origin)
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
        appearance_states=(),
        photo_candidates=(candidate,),
        capability_grants=(grants[0],) if grants else (),
        consent_grants=(grants[1],) if grants else (),
    )
    return _Ledger(projection, events), candidate


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
def test_authorizer_emits_explicit_private_when_adult_eligible(stage: str) -> None:
    ledger, candidate = _p3_world(stage=stage, with_adult_grants=True)
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "explicit_private"
    assert compiled.snapshot.private_media_authorization.media_lane == "explicit_private"
    assert compiled.snapshot.private_media_authorization.expression_charge_ceiling == "veiled"


def test_authorizer_fail_closes_adult_lane_below_close_friend() -> None:
    ledger, candidate = _p3_world(stage="friend", with_adult_grants=True)
    with pytest.raises(ValueError, match="p3_relationship_stage_not_eligible"):
        _authorize(ledger, candidate, adult_media_enabled=True)


def test_authorizer_fail_closes_adult_lane_when_switch_is_off() -> None:
    ledger, candidate = _p3_world(stage="close_friend", with_adult_grants=True)
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=False)
    assert opportunity.media_lane == "alluring_life"
    assert compiled.snapshot.private_media_authorization.media_lane == "alluring_life"


def test_authorizer_fail_closes_adult_lane_without_ledger_grant() -> None:
    ledger, candidate = _p3_world(stage="close_friend", with_adult_grants=False)
    opportunity, compiled = _authorize(ledger, candidate, adult_media_enabled=True)
    assert opportunity.media_lane == "alluring_life"
    assert compiled.snapshot.private_media_authorization.media_lane == "alluring_life"
