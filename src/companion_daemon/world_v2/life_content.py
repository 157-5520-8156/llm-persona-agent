"""Pinned, source-bound excerpts for settled lived-world content."""

from __future__ import annotations

from pydantic import Field, model_validator

from .life_content_store import ImmutableLifeContentStore
from .life_content_reading import (
    CharacterResponseReading, LifeContentReading, WorldConsequenceReading,
    read_character_life_experience_content, selected_world_consequence,
    world_consequence_reading,
)
from .life_content_events import (
    LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED,
    LifeContentUserChannelAuthorityLimitedPayload,
)
from .schema_core import FrozenModel, PrivacyClass
from .schemas import ExperienceProjection, LedgerProjection, ProjectionCursor


_PRIVACY_RANK = {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}
USER_CHANNEL_AUTHORITY_LIMIT_REASON = "user_channel_authority_limited"


def collect_user_channel_limited_content_refs(*, ledger, projection) -> frozenset[str]:
    """Read compensating limit events from the ledger, never from prose."""

    refs: set[str] = set()
    for item in projection.committed_world_event_refs:
        if item.event_type != LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED:
            continue
        located = ledger.lookup_event_commit(item.event_id)
        if located is None:
            raise ValueError("user-channel authority limit event is missing from the ledger")
        event, _commit = located
        if event.event_type != LIFE_CONTENT_USER_CHANNEL_AUTHORITY_LIMITED or (
            event.payload_hash != item.payload_hash
        ):
            raise ValueError("user-channel authority limit event does not match its committed ref")
        payload = LifeContentUserChannelAuthorityLimitedPayload.model_validate(event.payload())
        refs.update(payload.content_refs)
    return frozenset(refs)


class LifeContentBudget(FrozenModel):
    max_item_characters: int = Field(default=480, ge=1, le=12_000)
    max_total_characters: int = Field(default=1_440, ge=1, le=24_000)


class LifeContentExcerpt(FrozenModel):
    content_id: str = Field(min_length=1)
    content_kind: str = Field(min_length=1)
    content_ref: str = Field(min_length=1)
    content_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    text: str | None = Field(default=None, min_length=1, exclude_if=lambda value: value is None)
    truncated: bool
    privacy_class: PrivacyClass
    source_entity_id: str = Field(min_length=1)
    source_entity_revision: int = Field(ge=1)
    authority_event_ref: str = Field(min_length=1)
    authority_world_revision: int = Field(ge=1)
    authority_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    descriptor_event_ref: str = Field(min_length=1)
    descriptor_world_revision: int = Field(ge=1)
    descriptor_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    world_consequence: WorldConsequenceReading | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )
    character_response: CharacterResponseReading | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    @model_validator(mode="after")
    def one_content_carrier(self):
        if (self.text is None) == (self.world_consequence is None):
            raise ValueError("life excerpt requires either legacy prose or structured World content")
        if self.character_response is not None and self.world_consequence is None:
            raise ValueError("character reading requires its separate World content")
        return self

    def bounded(self, max_characters: int) -> LifeContentExcerpt:
        if self.world_consequence is None:
            assert self.text is not None
            return self.model_copy(update={
                "text": self.text[:max_characters],
                "truncated": self.truncated or len(self.text) > max_characters,
            })
        reading = LifeContentReading(
            world_consequence=self.world_consequence, character_response=self.character_response,
        ).bounded(max_characters)
        return self.model_copy(update={
            **reading.model_dump(mode="python"),
            "world_consequence": reading.world_consequence,
            "character_response": reading.character_response,
            "truncated": self.truncated or reading.truncated,
        })

    def text_characters(self) -> int:
        if self.world_consequence is None:
            return len(self.text or "")
        return LifeContentReading(
            world_consequence=self.world_consequence, character_response=self.character_response,
        ).text_characters()


class RecentExperienceContextItem(ExperienceProjection):
    """A committed Experience whose exact prose is safe for model input.

    An ``ExperienceProjection`` proves the structured lived event, while the
    separately committed ``LifeContentRecorded`` descriptor permits reading
    the immutable sidecar bytes.  Keeping both in one typed read model prevents
    callers from accidentally treating an opaque ``summary_ref`` as prose or
    displaying sidecar text without the Experience authority that owns it.
    """

    content: LifeContentExcerpt

    @model_validator(mode="after")
    def content_is_exactly_bound_to_experience(
        self,
    ) -> "RecentExperienceContextItem":
        if self.content.content_kind != "experience_summary":
            raise ValueError("recent Experience content must be an experience summary")
        if (
            self.content.source_entity_id != self.experience_id
            or self.content.source_entity_revision != self.entity_revision
            or self.content.authority_event_ref != self.origin.accepted_event_ref
            or self.content.content_ref != self.values.summary_ref
            or self.content.content_payload_hash != self.values.summary_payload_hash
            or _PRIVACY_RANK[self.content.privacy_class]
            < _PRIVACY_RANK[self.values.privacy_class]
        ):
            raise ValueError("recent Experience content does not match its authority")
        return self


class LifeContentSuppression(FrozenModel):
    content_id: str | None = Field(default=None, min_length=1)
    source_entity_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class LifeContentResult(FrozenModel):
    settled_items: tuple[LifeContentExcerpt, ...]
    experience_items: tuple[RecentExperienceContextItem, ...]
    suppressions: tuple[LifeContentSuppression, ...]


class LifeContentCompiler:
    """The sole Context read interface for sidecar-backed lived-world prose."""

    def __init__(self, *, store: ImmutableLifeContentStore | None) -> None:
        self._store = store

    def read_exact_bound_text(
        self,
        *,
        content_ref: str,
        content_payload_hash: str,
        content_kind: str,
        max_characters: int = 480,
    ) -> str | None:
        """Read sidecar prose only when its frozen ref, kind and bytes agree."""

        if self._store is None:
            return None
        stored = self._store.read_exact(content_ref=content_ref)
        if (
            stored is None
            or stored.content_kind != content_kind
            or stored.content_payload_hash != content_payload_hash
        ):
            return None
        return stored.text[:max_characters]

    def compile(
        self,
        *,
        cursor: ProjectionCursor,
        actor_ref: str,
        viewer_privacy_ceiling: PrivacyClass,
        budget: LifeContentBudget = LifeContentBudget(),
        projection: LedgerProjection,
        user_channel_limited_content_refs: frozenset[str] = frozenset(),
    ) -> LifeContentResult:
        if (
            projection.world_revision != cursor.world_revision
            or projection.deliberation_revision != cursor.deliberation_revision
            or projection.ledger_sequence != cursor.ledger_sequence
        ):
            raise ValueError("life content projection does not match its pinned cursor")
        committed = {item.event_id: item for item in projection.committed_world_event_refs}
        owned_plans = {
            item.plan_id
            for item in projection.plans
            if item.owner_actor_ref == actor_ref and item.authority_origin is not None
        }
        occurrences = {item.occurrence_id: item for item in projection.world_occurrences}
        experiences = {
            item.experience_id: item
            for item in projection.experiences
            if isinstance(item, ExperienceProjection)
        }
        described_experience_ids = {
            item.source_entity_id
            for item in projection.life_content_descriptors
            if item.source_kind == "experience"
        }
        candidate_rows: list[
            tuple[
                int,
                str,
                LifeContentExcerpt | RecentExperienceContextItem | None,
                LifeContentSuppression | None,
            ]
        ] = []
        for descriptor in projection.life_content_descriptors:
            source = committed.get(descriptor.source_event_ref)
            descriptor_event = committed.get(descriptor.descriptor_event_ref)
            if (
                source is None
                or descriptor_event is None
                or (
                    source.world_revision != descriptor.source_world_revision
                    or source.payload_hash != descriptor.source_payload_hash
                    or descriptor_event.world_revision != descriptor.descriptor_world_revision
                    or descriptor_event.payload_hash != descriptor.descriptor_payload_hash
                    or descriptor_event.event_type != "LifeContentRecorded"
                )
            ):
                candidate_rows.append(
                    (
                        0,
                        descriptor.content_id,
                        None,
                        LifeContentSuppression(
                            content_id=descriptor.content_id,
                            source_entity_id=descriptor.source_entity_id,
                            reason="source_proof_failed",
                        ),
                    )
                )
                continue
            if (
                _PRIVACY_RANK[descriptor.privacy_class] > _PRIVACY_RANK[viewer_privacy_ceiling]
                or descriptor.privacy_class == "withhold"
            ):
                candidate_rows.append(
                    (
                        0,
                        descriptor.content_id,
                        None,
                        LifeContentSuppression(
                            content_id=descriptor.content_id,
                            source_entity_id=descriptor.source_entity_id,
                            reason="privacy_ceiling",
                        ),
                    )
                )
                continue
            if descriptor.content_ref in user_channel_limited_content_refs:
                candidate_rows.append(
                    (
                        0,
                        descriptor.content_id,
                        None,
                        LifeContentSuppression(
                            content_id=descriptor.content_id,
                            source_entity_id=descriptor.source_entity_id,
                            reason=USER_CHANNEL_AUTHORITY_LIMIT_REASON,
                        ),
                    )
                )
                continue
            if descriptor.source_kind == "occurrence_settlement":
                occurrence = occurrences.get(descriptor.source_entity_id)
                structured_world = occurrence is not None and any(
                    item.candidate_result_ref == occurrence.settled_outcome_ref
                    and item.result_contract == "world-consequence.2"
                    for item in occurrence.candidate_outcomes
                )
                related = occurrence is not None and (
                    actor_ref in occurrence.participant_refs
                    or any(
                        ref.removeprefix("plan:") in owned_plans
                        for ref in occurrence.precondition_refs
                        if ref.startswith("plan:")
                    )
                )
                valid = (
                    source.event_type == "WorldOccurrenceSettled"
                    and descriptor.content_kind == "occurrence_result"
                    and related
                    and occurrence is not None
                    and occurrence.status == "settled"
                    and (
                        occurrence.entity_revision == descriptor.source_entity_revision
                        and occurrence.settlement_event_ref == descriptor.source_event_ref
                        and occurrence.settlement_world_revision == descriptor.source_world_revision
                        and occurrence.settlement_payload_hash == descriptor.source_payload_hash
                        and occurrence.result_payload_ref == descriptor.content_ref
                        and (
                            occurrence.result_payload_hash.removeprefix("sha256:")
                            if structured_world and occurrence.result_payload_hash is not None
                            else occurrence.result_payload_hash
                        ) == descriptor.content_payload_hash
                    )
                )
                rank = (
                    int(occurrence.settled_at.timestamp()) if valid and occurrence.settled_at else 0
                )
            else:
                experience = experiences.get(descriptor.source_entity_id)
                related = experience is not None and actor_ref in experience.values.participant_refs
                valid = (
                    source.event_type == "ExperienceCommitted"
                    and descriptor.content_kind == "experience_summary"
                    and related
                    and experience is not None
                    and _PRIVACY_RANK[descriptor.privacy_class]
                    >= _PRIVACY_RANK[experience.values.privacy_class]
                    and (
                        experience.entity_revision == descriptor.source_entity_revision
                        and experience.origin.accepted_event_ref == descriptor.source_event_ref
                        and experience.values.summary_ref == descriptor.content_ref
                        and experience.values.summary_payload_hash
                        == descriptor.content_payload_hash
                    )
                )
                rank = int(experience.values.occurred_to.timestamp()) if valid else 0
            if not valid:
                candidate_rows.append(
                    (
                        0,
                        descriptor.content_id,
                        None,
                        LifeContentSuppression(
                            content_id=descriptor.content_id,
                            source_entity_id=descriptor.source_entity_id,
                            reason="not_related" if not related else "source_proof_failed",
                        ),
                    )
                )
                continue
            stored = (
                self._store.read_exact(content_ref=descriptor.content_ref) if self._store else None
            )
            if stored is None:
                candidate_rows.append(
                    (
                        rank,
                        descriptor.content_id,
                        None,
                        LifeContentSuppression(
                            content_id=descriptor.content_id,
                            source_entity_id=descriptor.source_entity_id,
                            reason="content_missing",
                        ),
                    )
                )
                continue
            if (
                stored.content_kind != descriptor.content_kind
                or stored.content_payload_hash != descriptor.content_payload_hash
            ):
                candidate_rows.append(
                    (
                        rank,
                        descriptor.content_id,
                        None,
                        LifeContentSuppression(
                            content_id=descriptor.content_id,
                            source_entity_id=descriptor.source_entity_id,
                            reason="hash_mismatch",
                        ),
                    )
                )
                continue
            reading = None
            try:
                if descriptor.source_kind == "experience" and (
                    experience.authority_contract_version == "experience.2"
                ):
                    reading = read_character_life_experience_content(
                        store=self._store, projection=projection, experience=experience,
                        actor_ref=actor_ref, viewer_privacy_ceiling=viewer_privacy_ceiling,
                        max_characters=budget.max_item_characters,
                        user_channel_limited_content_refs=user_channel_limited_content_refs,
                    )
                elif descriptor.source_kind == "occurrence_settlement" and any(
                    candidate.candidate_result_ref == occurrence.settled_outcome_ref
                    and candidate.result_contract == "world-consequence.2"
                    for candidate in occurrence.candidate_outcomes
                ):
                    reading = LifeContentReading(world_consequence=world_consequence_reading(
                        selected_world_consequence(
                            store=self._store, projection=projection, occurrence=occurrence,
                            actor_ref=actor_ref, viewer_privacy_ceiling=viewer_privacy_ceiling,
                            user_channel_limited_content_refs=user_channel_limited_content_refs,
                        )
                    )).bounded(budget.max_item_characters)
            except ValueError:
                candidate_rows.append((rank, descriptor.content_id, None, LifeContentSuppression(
                    content_id=descriptor.content_id, source_entity_id=descriptor.source_entity_id,
                    reason="structured_content_unavailable",
                )))
                continue
            text = stored.text[: budget.max_item_characters] if reading is None else None
            excerpt = LifeContentExcerpt(
                content_id=descriptor.content_id,
                content_kind=descriptor.content_kind,
                content_ref=descriptor.content_ref,
                content_payload_hash=descriptor.content_payload_hash,
                text=text,
                truncated=reading.truncated if reading is not None else text != stored.text,
                privacy_class=descriptor.privacy_class,
                source_entity_id=descriptor.source_entity_id,
                source_entity_revision=descriptor.source_entity_revision,
                authority_event_ref=descriptor.source_event_ref,
                authority_world_revision=descriptor.source_world_revision,
                authority_payload_hash=descriptor.source_payload_hash,
                descriptor_event_ref=descriptor.descriptor_event_ref,
                descriptor_world_revision=descriptor.descriptor_world_revision,
                descriptor_payload_hash=descriptor.descriptor_payload_hash,
                world_consequence=reading.world_consequence if reading is not None else None,
                character_response=reading.character_response if reading is not None else None,
            )
            compiled_item: LifeContentExcerpt | RecentExperienceContextItem
            if descriptor.source_kind == "experience":
                compiled_item = RecentExperienceContextItem.model_validate(
                    {
                        **experience.model_dump(mode="python"),
                        "content": excerpt,
                    }
                )
            else:
                compiled_item = excerpt
            candidate_rows.append((rank, descriptor.content_id, compiled_item, None))
        for experience in experiences.values():
            if (
                actor_ref not in experience.values.participant_refs
                or experience.experience_id in described_experience_ids
            ):
                continue
            reason = (
                "privacy_ceiling"
                if experience.values.privacy_class == "withhold"
                or _PRIVACY_RANK[experience.values.privacy_class]
                > _PRIVACY_RANK[viewer_privacy_ceiling]
                else "descriptor_missing"
            )
            candidate_rows.append(
                (
                    int(experience.values.occurred_to.timestamp()),
                    f"descriptor-missing:{experience.experience_id}",
                    None,
                    LifeContentSuppression(
                        source_entity_id=experience.experience_id,
                        reason=reason,
                    ),
                )
            )
        remaining = budget.max_total_characters
        settled: list[LifeContentExcerpt] = []
        experiences_out: list[RecentExperienceContextItem] = []
        suppressions: list[LifeContentSuppression] = []
        for _, _, item, suppression in sorted(candidate_rows, key=lambda row: (-row[0], row[1])):
            if suppression is not None:
                suppressions.append(suppression)
            elif item is not None:
                if remaining <= 0:
                    content_id = (
                        item.content.content_id
                        if isinstance(item, RecentExperienceContextItem)
                        else item.content_id
                    )
                    suppressions.append(
                        LifeContentSuppression(
                            content_id=content_id,
                            source_entity_id=(
                                item.experience_id
                                if isinstance(item, RecentExperienceContextItem)
                                else item.source_entity_id
                            ),
                            reason="budget_exhausted",
                        )
                    )
                else:
                    original = item.content if isinstance(item, RecentExperienceContextItem) else item
                    try:
                        content = original.bounded(remaining)
                    except ValueError:
                        suppressions.append(LifeContentSuppression(
                            content_id=original.content_id,
                            source_entity_id=original.source_entity_id,
                            reason="budget_exhausted",
                        ))
                        continue
                    if isinstance(item, RecentExperienceContextItem):
                        view = item.model_copy(update={"content": content})
                        remaining -= content.text_characters()
                        experiences_out.append(view)
                    else:
                        remaining -= content.text_characters()
                        settled.append(content)
        return LifeContentResult(
            settled_items=tuple(settled),
            experience_items=tuple(experiences_out),
            suppressions=tuple(suppressions),
        )


__all__ = [
    "LifeContentBudget",
    "LifeContentCompiler",
    "LifeContentExcerpt",
    "LifeContentResult",
    "LifeContentSuppression",
    "RecentExperienceContextItem",
    "USER_CHANNEL_AUTHORITY_LIMIT_REASON",
    "collect_user_channel_limited_content_refs",
]
