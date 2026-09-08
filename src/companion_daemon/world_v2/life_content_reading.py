"""Read two authors' exact material without turning a reading into a World fact.

The accepted Experience/descriptor grants access to the composite. The selected
World candidate supplies only its settled environment and bound attempt result;
the character's response remains a private interpretation, including explicit
null. Text clipping is a presentation budget, never a new semantic judgement.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import Field

from .schema_core import FrozenModel
from .life_content_store import life_content_payload_hash
from .world_consequence_contract import WorldConsequenceExecutionBinding, WorldConsequenceV2


_PRIVACY = {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}


class WorldEnvironmentExcerpt(FrozenModel):
    text: str = Field(min_length=1)
    truncated: bool = False
    epistemic_scope: Literal["settled_world_environment", "candidate_world_environment"] = "settled_world_environment"


class AuthorizedAttemptExcerpt(FrozenModel):
    text: str = Field(min_length=1)
    truncated: bool = False
    execution_binding: WorldConsequenceExecutionBinding
    epistemic_scope: Literal["settled_result_of_bound_attempt", "candidate_result_of_bound_attempt"] = "settled_result_of_bound_attempt"


class WorldConsequenceReading(FrozenModel):
    contract: Literal["world-consequence.2"] = "world-consequence.2"
    environment: WorldEnvironmentExcerpt
    authorized_attempt_result: AuthorizedAttemptExcerpt | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )


class CharacterResponseReading(FrozenModel):
    source_event_ref: str = Field(min_length=1)
    actor_ref: str = Field(min_length=1)
    response_text: str | None
    truncated: bool = False
    epistemic_scope: Literal["private_interpretation_not_world_fact"] = (
        "private_interpretation_not_world_fact"
    )


class LifeContentReading(FrozenModel):
    world_consequence: WorldConsequenceReading
    character_response: CharacterResponseReading | None = Field(
        default=None, exclude_if=lambda value: value is None,
    )

    def text_characters(self) -> int:
        result = len(self.world_consequence.environment.text)
        if self.world_consequence.authorized_attempt_result is not None:
            result += len(self.world_consequence.authorized_attempt_result.text)
        if self.character_response is not None:
            result += len(self.character_response.response_text or "")
        return result

    @property
    def truncated(self) -> bool:
        return any((
            self.world_consequence.environment.truncated,
            self.world_consequence.authorized_attempt_result is not None
            and self.world_consequence.authorized_attempt_result.truncated,
            self.character_response is not None and self.character_response.truncated,
        ))

    def bounded(self, max_characters: int | None) -> LifeContentReading:
        if max_characters is None or self.text_characters() <= max_characters:
            return self
        world = self.world_consequence
        fields = [world.environment]
        if world.authorized_attempt_result is not None:
            fields.append(world.authorized_attempt_result)
        if self.character_response is not None and self.character_response.response_text:
            fields.append(self.character_response)
        if max_characters < len(fields):
            raise ValueError("life reading budget cannot expose both authors")
        remaining = max_characters
        bounded = []
        # Every nonempty field gets a share. A long environment must not hide
        # that a distinct character response exists, or vice versa.
        for ordinal, item in enumerate(fields):
            key = "response_text" if isinstance(item, CharacterResponseReading) else "text"
            original = getattr(item, key)
            size = remaining // (len(fields) - ordinal)
            text = original[:size]
            remaining -= len(text)
            bounded.append(item.model_copy(update={
                key: text, "truncated": item.truncated or text != original,
            }))
        environment = bounded.pop(0)
        attempt = bounded.pop(0) if world.authorized_attempt_result is not None else None
        response = bounded.pop(0) if bounded else self.character_response
        return LifeContentReading(
            world_consequence=WorldConsequenceReading(
                environment=environment, authorized_attempt_result=attempt,
            ),
            character_response=response,
        )


def _bare_sha256(value: str) -> str:
    return value.removeprefix("sha256:")


def selected_world_consequence(
    *, store, projection, occurrence, actor_ref: str, viewer_privacy_ceiling: str,
    user_channel_limited_content_refs: frozenset[str] = frozenset(),
) -> WorldConsequenceV2:
    """Resolve only the accepted selected descriptor, never an arbitrary ref.

    Callers separately enforce their published descriptor/Experience and viewer
    gates. This helper cannot itself publish a candidate or grant read access.
    """
    source = next((
        item for item in projection.committed_world_event_refs
        if item.event_id == occurrence.settlement_event_ref
    ), None)
    if source is None or (
        source.event_type != "WorldOccurrenceSettled"
        or source.world_revision != occurrence.settlement_world_revision
        or source.payload_hash != occurrence.settlement_payload_hash
        or occurrence.status != "settled"
        or actor_ref not in occurrence.participant_refs
        or occurrence.visibility == "withhold"
        or _PRIVACY[occurrence.visibility] > _PRIVACY[viewer_privacy_ceiling]
    ):
        raise ValueError("world consequence requires its exact settlement")
    candidate = next((
        item for item in occurrence.candidate_outcomes
        if item.candidate_result_ref == occurrence.settled_outcome_ref
    ), None)
    if candidate is None or (
        candidate.result_contract != "world-consequence.2"
        or candidate.result_id != occurrence.result_id
        or candidate.result_payload_ref != occurrence.result_payload_ref
        or _bare_sha256(candidate.result_payload_hash)
        != _bare_sha256(occurrence.result_payload_hash or "")
        or candidate.content_ref is None
        or candidate.content_ref in user_channel_limited_content_refs
        or occurrence.result_payload_ref in user_channel_limited_content_refs
        or candidate.content_payload_hash != _bare_sha256(candidate.result_payload_hash)
        or candidate.privacy_class == "withhold"
        or _PRIVACY[candidate.privacy_class] > _PRIVACY[viewer_privacy_ceiling]
    ):
        raise ValueError("world consequence selected descriptor is unavailable")
    stored = store.read_exact(content_ref=candidate.content_ref) if store is not None else None
    if stored is None or (
        not (
            stored.content_kind == "outcome_candidate"
            or (stored.content_kind == "occurrence_result"
                and candidate.content_ref == occurrence.result_payload_ref)
        )
        or stored.content_payload_hash != candidate.content_payload_hash
        or life_content_payload_hash(stored.text) != candidate.content_payload_hash
    ):
        raise ValueError("world consequence exact selected body is unavailable")
    return parse_world_consequence_content(stored.text)


def parse_world_consequence_content(text: str) -> WorldConsequenceV2:
    consequence = WorldConsequenceV2.model_validate_json(text)
    canonical = json.dumps(
        consequence.model_dump(mode="json"), ensure_ascii=False,
        sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    if canonical != text:
        raise ValueError("world consequence body is not the canonical accepted carrier")
    return consequence


def world_consequence_reading(
    consequence: WorldConsequenceV2, *, settled: bool = True,
) -> WorldConsequenceReading:
    attempt = consequence.authorized_attempt_result
    return WorldConsequenceReading(
        environment=WorldEnvironmentExcerpt(
            text=consequence.environment_text,
            epistemic_scope="settled_world_environment" if settled else "candidate_world_environment",
        ),
        authorized_attempt_result=(
            AuthorizedAttemptExcerpt(
                text=attempt.text, execution_binding=attempt.execution_binding,
                epistemic_scope="settled_result_of_bound_attempt" if settled else "candidate_result_of_bound_attempt",
            )
            if attempt is not None else None
        ),
    )


def read_character_life_experience_content(
    *, store, projection, experience, actor_ref: str, viewer_privacy_ceiling: str,
    max_characters: int | None = None,
    user_channel_limited_content_refs: frozenset[str] = frozenset(),
) -> LifeContentReading:
    """Read the exact .2 composite; failure never falls back to raw summary JSON."""
    from .character_life_experience_contract import validate_character_life_experience_summary
    from .character_life_experience_runtime import validate_character_life_experience_binding

    if (
        experience.authority_contract_version != "experience.2"
        or actor_ref not in experience.values.participant_refs
        or len(experience.values.source_bindings) != 1
    ):
        raise ValueError("structured life reading requires an owned Experience .2")
    binding = experience.values.source_bindings[0]
    if binding.source_kind != "world_life_response" or binding.response.actor_ref != actor_ref:
        raise ValueError("structured life reading requires its author's response")
    if (
        experience.values.privacy_class == "withhold"
        or _PRIVACY[experience.values.privacy_class] > _PRIVACY[viewer_privacy_ceiling]
    ):
        raise ValueError("structured life reading is outside the viewer privacy ceiling")
    if experience not in projection.experiences:
        raise ValueError("structured life reading requires the pinned Experience")
    accepted = next((
        item for item in projection.committed_world_event_refs
        if item.event_id == experience.origin.accepted_event_ref
        and item.event_type == "ExperienceCommitted"
    ), None)
    descriptor = next((
        item for item in projection.life_content_descriptors
        if item.content_kind == "experience_summary"
        and item.source_kind == "experience"
        and item.source_entity_id == experience.experience_id
        and item.source_entity_revision == experience.entity_revision
        and accepted is not None
        and item.source_event_ref == accepted.event_id
        and item.source_world_revision == accepted.world_revision
        and item.source_payload_hash == accepted.payload_hash
        and item.content_ref == experience.values.summary_ref
        and item.content_payload_hash == experience.values.summary_payload_hash
    ), None)
    if descriptor is None or (
        descriptor.privacy_class == "withhold"
        or _PRIVACY[descriptor.privacy_class] < _PRIVACY[experience.values.privacy_class]
        or _PRIVACY[descriptor.privacy_class] > _PRIVACY[viewer_privacy_ceiling]
        or not any(
            item.event_id == descriptor.descriptor_event_ref
            and item.event_type == "LifeContentRecorded"
            and item.world_revision == descriptor.descriptor_world_revision
            and item.payload_hash == descriptor.descriptor_payload_hash
            for item in projection.committed_world_event_refs
        )
    ):
        raise ValueError("structured life reading has no published summary descriptor")
    validate_character_life_experience_binding(
        state=projection, world_id=projection.world_id, binding=binding,
    )
    stored = store.read_exact(content_ref=experience.values.summary_ref) if store else None
    if stored is None or (
        stored.content_kind != "experience_summary"
        or stored.content_payload_hash != experience.values.summary_payload_hash
        or life_content_payload_hash(stored.text) != experience.values.summary_payload_hash
        or stored.content_ref in user_channel_limited_content_refs
    ):
        raise ValueError("structured life summary body is unavailable")
    summary = validate_character_life_experience_summary(stored.text, binding)
    occurrence = next((
        item for item in projection.world_occurrences
        if item.occurrence_id == binding.settlement.occurrence_id
    ), None)
    if occurrence is None or (
        occurrence.visibility == "withhold"
        or _PRIVACY[occurrence.visibility] > _PRIVACY[viewer_privacy_ceiling]
        or occurrence.result_payload_ref in user_channel_limited_content_refs
    ):
        raise ValueError("structured life world source is unavailable")
    consequence = selected_world_consequence(
        store=store, projection=projection, occurrence=occurrence,
        actor_ref=actor_ref, viewer_privacy_ceiling=viewer_privacy_ceiling,
        user_channel_limited_content_refs=user_channel_limited_content_refs,
    )
    response = summary.character_response
    return LifeContentReading(
        world_consequence=world_consequence_reading(consequence),
        character_response=CharacterResponseReading(
            source_event_ref=response.response_event_ref,
            actor_ref=response.actor_ref, response_text=response.response_text,
        ),
    ).bounded(max_characters)
