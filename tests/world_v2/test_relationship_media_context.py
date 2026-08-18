from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from companion_daemon.world_v2.relationship_media_context import (
    DeclaredDisplayV1,
    PrivateTransitionEvidenceV1,
    RelationshipMediaContextResolver,
)
from companion_daemon.world_v2.schemas import RelationshipStateProjection
from companion_daemon.world_v2.visible_physical_state import (
    VisiblePhysicalCue,
    VisiblePhysicalNegativeCue,
    VisiblePhysicalStateProjection,
)


NOW = datetime(2026, 7, 16, 20, tzinfo=UTC)


def _relationship(*, subject_ref: str = "user:1") -> RelationshipStateProjection:
    return RelationshipStateProjection(
        relationship_id="relationship:user:1",
        subject_ref=subject_ref,
        entity_revision=3,
        stage="close_friend",
        policy_digest="a" * 64,
    )


def _physical(*, positive: bool = True, subject_ref: str = "character:ava") -> VisiblePhysicalStateProjection:
    return VisiblePhysicalStateProjection(
        physical_state_id="physical:after-run",
        subject_ref=subject_ref,
        entity_revision=2,
        source_event_ref="event:activity:after-run",
        source_event_payload_hash="b" * 64,
        source_event_type="ActivityCompleted",
        valid_from=NOW - timedelta(minutes=5),
        valid_until=NOW + timedelta(minutes=20),
        visibility="personal",
        positive_cues=(
            (VisiblePhysicalCue(cue_id="damp_hair", intensity="light", visible_regions=("hair",)),)
            if positive
            else ()
        ),
        negative_cues=(
            ()
            if positive
            else (VisiblePhysicalNegativeCue(cue_id="dry_hair", visible_regions=("hair",)),)
        ),
    )


def test_resolver_freezes_exact_relationship_and_embodied_state_basis() -> None:
    projection = SimpleNamespace(
        relationship_states=(_relationship(),),
        visible_physical_states=(_physical(),),
    )
    result = RelationshipMediaContextResolver().resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW
    )

    assert result.accepted
    assert result.reason_code is None
    assert result.context is not None
    assert result.context.audience.relationship_stage == "close_friend"
    assert result.context.private_expression_basis.evidence_ref == "/character/visible_physical_state"
    assert result.context.private_expression_basis.source_event_ref == "event:activity:after-run"
    # The resolved slice is deterministic and validates its own digest on reload.
    assert result.context == RelationshipMediaContextResolver().resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW
    ).context


def test_resolver_rejects_negative_only_physical_state() -> None:
    projection = SimpleNamespace(
        relationship_states=(_relationship(),), visible_physical_states=(_physical(positive=False),)
    )
    result = RelationshipMediaContextResolver().resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW
    )

    assert not result.accepted
    assert result.reason_code == "visible_physical_negative_only"


def test_resolver_rejects_unsupported_basis_and_recipient_subject_alias() -> None:
    projection = SimpleNamespace(
        relationship_states=(_relationship(),), visible_physical_states=(_physical(),)
    )
    resolver = RelationshipMediaContextResolver()
    assert resolver.resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW,
        basis_kind="shared_ritual",
    ).reason_code == "unsupported_private_expression_basis"
    assert resolver.resolve(
        projection=projection, character_ref="user:1", recipient_ref="user:1", at_logical_time=NOW,
    ).reason_code == "recipient_character_subject_mismatch"


def test_context_rejects_tampered_digest() -> None:
    projection = SimpleNamespace(
        relationship_states=(_relationship(),), visible_physical_states=(_physical(),)
    )
    context = RelationshipMediaContextResolver().resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW
    ).context
    assert context is not None
    with pytest.raises(ValueError, match="context digest"):
        type(context).model_validate({**context.model_dump(mode="python"), "authority_digest": "0" * 64})


def test_resolver_freezes_a_recipient_scoped_private_transition() -> None:
    projection = SimpleNamespace(relationship_states=(_relationship(),), visible_physical_states=())
    transition = PrivateTransitionEvidenceV1(
        declaration_event_ref="event:recipient-evidence:transition",
        declaration_event_payload_hash="d" * 64,
        recipient_ref="user:1", activity_id="activity:wind-down", activity_kind="wind_down",
        valid_until=NOW + timedelta(minutes=15),
    )

    result = RelationshipMediaContextResolver().resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW,
        basis_kind="private_transition", private_transition=transition,
    )

    assert result.context is not None
    assert result.context.private_expression_basis.kind == "private_transition"
    assert result.context.private_expression_basis.evidence_ref == "/activity/private_transition"
    assert result.context.private_expression_basis.source_event_ref == transition.declaration_event_ref


def _display(*, recipient_ref: str = "user:1", intent: str = "sexual_suggestive") -> DeclaredDisplayV1:
    return DeclaredDisplayV1(
        event_id="event:declared-display:1",
        recipient_ref=recipient_ref,
        media_intent=intent,  # type: ignore[arg-type]
    )


def test_resolver_omits_declared_display_from_dump_until_she_authors_one() -> None:
    projection = SimpleNamespace(
        relationship_states=(_relationship(),), visible_physical_states=(_physical(),)
    )
    context = RelationshipMediaContextResolver().resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW
    ).context
    assert context is not None
    dumped = context.model_dump(mode="json")
    assert "declared_display" not in dumped
    assert context.declared_display is None


def test_resolver_freezes_character_authored_declared_display() -> None:
    projection = SimpleNamespace(
        relationship_states=(_relationship(),), visible_physical_states=(_physical(),)
    )
    display = _display(intent="explicit_adult")
    result = RelationshipMediaContextResolver().resolve(
        projection=projection,
        character_ref="character:ava",
        recipient_ref="user:1",
        at_logical_time=NOW,
        declared_display=display,
    )
    assert result.accepted
    assert result.context is not None
    assert result.context.declared_display == display
    dumped = result.context.model_dump(mode="json")
    assert dumped["declared_display"]["media_intent"] == "explicit_adult"
    assert dumped["declared_display"]["event_id"] == "event:declared-display:1"
    without = RelationshipMediaContextResolver().resolve(
        projection=projection, character_ref="character:ava", recipient_ref="user:1", at_logical_time=NOW
    ).context
    assert without is not None
    assert result.context.authority_digest != without.authority_digest


def test_resolver_rejects_declared_display_for_a_different_recipient() -> None:
    projection = SimpleNamespace(
        relationship_states=(_relationship(),), visible_physical_states=(_physical(),)
    )
    result = RelationshipMediaContextResolver().resolve(
        projection=projection,
        character_ref="character:ava",
        recipient_ref="user:1",
        at_logical_time=NOW,
        declared_display=_display(recipient_ref="user:other"),
    )
    assert not result.accepted
    assert result.reason_code == "declared_display_recipient_mismatch"
