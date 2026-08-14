from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from companion_daemon.world_v2.schemas import (
    ExperienceOccurrenceSettlementBinding,
    ExperienceOrigin,
    ExperienceProjection,
    ExperienceValues,
    experience_semantic_fingerprint,
)
from companion_daemon.world_v2.world_life_context import (
    WorldLifeContextItem,
    WorldLifeSourceBinding,
)


NOW = datetime(2026, 8, 14, 12, 0, tzinfo=UTC)
POLICY = ("policy:experience-v1",)


def _values(*, summary_ref: str, summary_payload_hash: str) -> ExperienceValues:
    return ExperienceValues(
        summary_ref=summary_ref,
        summary_payload_hash=summary_payload_hash,
        occurred_from=NOW - timedelta(minutes=5),
        occurred_to=NOW - timedelta(minutes=1),
        participant_refs=("actor:companion",),
        source_bindings=(
            ExperienceOccurrenceSettlementBinding(
                authority_event_ref="event:occurrence:settled",
                authority_world_revision=4,
                authority_payload_hash="b" * 64,
                occurrence_id="occurrence:walk",
                occurrence_entity_revision=1,
                result_id="cycling-wind",
                result_payload_ref="content:occurrence:walk",
                result_payload_hash="a" * 64,
            ),
        ),
        privacy_class="personal",
    )


def test_settled_occurrence_context_may_omit_the_content_excerpt() -> None:
    item = WorldLifeContextItem(
        occurrence_id="occurrence:walk",
        occurrence_entity_revision=1,
        participant_refs=("actor:companion",),
        location_ref="location:campus-path",
        result_id="cycling-wind",
        result_payload_ref="content:occurrence:walk",
        result_payload_hash="a" * 64,
        settled_at=NOW,
        privacy_class="shareable",
        source=WorldLifeSourceBinding(
            authority_event_ref="event:occurrence:settled",
            authority_world_revision=4,
            authority_payload_hash="b" * 64,
        ),
        content=None,
    )

    assert item.content is None
    assert item.result_id == "cycling-wind"


def test_experience_summary_is_required_and_changes_the_fingerprint() -> None:
    first = _values(summary_ref="summary:cat", summary_payload_hash="c" * 64)
    second = _values(summary_ref="summary:dog", summary_payload_hash="d" * 64)
    fp_first = experience_semantic_fingerprint(values=first, policy_refs=POLICY)
    fp_second = experience_semantic_fingerprint(values=second, policy_refs=POLICY)

    assert first.summary_ref
    assert first.summary_payload_hash
    assert fp_first != fp_second


def test_experience_projection_is_not_a_revisable_entity() -> None:
    values = _values(summary_ref="summary:cat", summary_payload_hash="c" * 64)
    fingerprint = experience_semantic_fingerprint(values=values, policy_refs=POLICY)
    origin = ExperienceOrigin(
        change_id="change:experience:walk",
        transition_id="transition:experience:walk",
        policy_refs=POLICY,
        accepted_event_ref="event:experience:walk",
    )
    projection = ExperienceProjection(
        experience_id="experience:walk",
        entity_revision=1,
        semantic_fingerprint=fingerprint,
        values=values,
        origin=origin,
    )

    assert projection.entity_revision == 1
    with pytest.raises(ValidationError):
        ExperienceProjection(
            experience_id="experience:walk",
            entity_revision=2,
            semantic_fingerprint=fingerprint,
            values=values,
            origin=origin,
        )
