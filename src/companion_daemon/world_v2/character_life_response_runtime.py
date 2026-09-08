"""Record explicit source-bound character readings without creating outcomes.

The role proposal is already durable. Acceptance only proves and records it;
crash recovery uses that same audit, including an explicit null response.
"""

from __future__ import annotations

import hashlib
import json

from .character_life_response_contract import (
    CHARACTER_LIFE_RESPONSE_POLICY_REF,
    CHARACTER_LIFE_RESPONSE_REGISTRY_VERSION,
    CharacterLifeResponseOrigin,
    CharacterLifeResponsePayload,
    CharacterLifeResponseRecordedPayload,
)
from .decision_proposal_authority import DecisionProposalAuthorityReader
from .event_identity import domain_idempotency_key
from .schemas import ProjectionCursor, WorldEvent
from .world_stimulus_choice_authority import (
    read_world_stimulus_choice_authority,
    world_stimulus_source_origin,
    world_life_response_source_refs,
)


SOURCE = "world-v2:character-life-response"
RESPONSE_PREFIX = "response:character-life-response:"
EVENT_PREFIX = "event:character-life-response:"


def character_life_response_id(*, world_id: str, actor_ref: str, source_event_ref: str) -> str:
    material = json.dumps(
        [world_id, actor_ref, source_event_ref],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return RESPONSE_PREFIX + hashlib.sha256(material.encode()).hexdigest()


class CharacterLifeResponseError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = "character_life_response." + code
        super().__init__(self.code)


def derive_character_life_responses(
    *,
    state,
    world_id: str,
    proposal_id: str,
    owner_actor_ref: str,
) -> tuple[CharacterLifeResponseRecordedPayload, ...]:
    authority = read_world_stimulus_choice_authority(
        state=state,
        world_id=world_id,
        proposal_id=proposal_id,
        owner_actor_ref=owner_actor_ref,
        registry_versions=(CHARACTER_LIFE_RESPONSE_REGISTRY_VERSION,),
        error_type=CharacterLifeResponseError,
    )
    changes = tuple(
        x for x in authority.proposal.proposed_changes if x.kind == "world_life_response"
    )
    if not changes:
        raise CharacterLifeResponseError("explicit_response_missing")
    responses = []
    seen_sources = set()
    for change in changes:
        response = CharacterLifeResponsePayload.model_validate_json(change.payload.canonical_json)
        if response.actor_ref != owner_actor_ref:
            raise CharacterLifeResponseError("actor_mismatch")
        response_id = character_life_response_id(
            world_id=world_id,
            actor_ref=owner_actor_ref,
            source_event_ref=response.source_event_ref,
        )
        if any(
            (
                change.transition != "record",
                change.expected_entity_revision != 0,
                change.target_id != response_id,
                change.payload.payload_schema != "world_life_response.v1",
                change.evidence_refs != (response.source_event_ref,),
                change.policy_refs != (CHARACTER_LIFE_RESPONSE_POLICY_REF,),
            )
        ):
            raise CharacterLifeResponseError("change_binding_invalid")
        if response.source_event_ref in seen_sources:
            raise CharacterLifeResponseError("duplicate_source")
        seen_sources.add(response.source_event_ref)
        _, origin = world_stimulus_source_origin(
            state=state,
            authority=authority,
            source_event_ref=response.source_event_ref,
            change_id=change.change_id,
            error_type=CharacterLifeResponseError,
        )
        responses.append(
            CharacterLifeResponseRecordedPayload(
                response_id=response_id,
                actor_ref=owner_actor_ref,
                response_text=response.response_text,
                origin=CharacterLifeResponseOrigin(**origin),
            )
        )
    required = world_life_response_source_refs(
        state=state,
        source_refs=authority.lineage.causal_source_refs,
        owner_actor_ref=owner_actor_ref,
        evaluated_world_revision=authority.audit.evaluated_world_revision,
    )
    if seen_sources != set(required):
        raise CharacterLifeResponseError("response_source_coverage_invalid")
    return tuple(sorted(responses, key=lambda item: item.response_id))


def reduce_character_life_response(state, event: WorldEvent):
    """Validate accepted bytes; generic committed-event authority stores the result."""
    payload = CharacterLifeResponseRecordedPayload.model_validate_json(event.payload_json)
    if (
        event.event_type != "CharacterLifeResponseRecorded"
        or state.logical_time is None
        or event.logical_time != state.logical_time
        or event.source != SOURCE
        or event.actor != payload.actor_ref
        or event.event_id != EVENT_PREFIX + payload.response_id.removeprefix(RESPONSE_PREFIX)
        or event.causation_id != payload.origin.proposal_event_ref
    ):
        raise CharacterLifeResponseError("event_binding_invalid")
    expected = derive_character_life_responses(
        state=state,
        world_id=event.world_id,
        proposal_id=payload.origin.proposal_id,
        owner_actor_ref=payload.actor_ref,
    )
    if payload not in expected:
        raise CharacterLifeResponseError("response_authority_mismatch")
    if any(x.event_id == event.event_id for x in state.committed_world_event_refs):
        raise CharacterLifeResponseError("response_already_recorded")
    return state


class CharacterLifeResponseRuntime:
    def __init__(self, *, ledger, owner_actor_ref: str) -> None:
        if not owner_actor_ref:
            raise ValueError("character life response requires an owner")
        self.ledger = ledger
        self._owner = owner_actor_ref

    def accept(self, *, world_id: str, audit_cursor: ProjectionCursor, proposal_id: str):
        reader = DecisionProposalAuthorityReader(ledger=self.ledger)
        authority = reader.read(
            reader.pin(
                world_id=world_id,
                cursor=audit_cursor,
                proposal_id=proposal_id,
            )
        )
        source_event = self.ledger.lookup_event_commit(authority.audit.event_ref)[0]
        payloads = derive_character_life_responses(
            state=self.ledger.project(),
            world_id=world_id,
            proposal_id=proposal_id,
            owner_actor_ref=self._owner,
        )
        receipts = []
        for payload in payloads:
            identity = payload.response_id.removeprefix(RESPONSE_PREFIX)
            event_id = EVENT_PREFIX + identity
            existing = self.ledger.lookup_event_commit(event_id)
            if existing is not None:
                if (
                    existing[0].event_type != "CharacterLifeResponseRecorded"
                    or existing[0].world_id != world_id
                    or CharacterLifeResponseRecordedPayload.model_validate_json(
                        existing[0].payload_json
                    )
                    != payload
                ):
                    raise CharacterLifeResponseError("effect_identity_conflict")
                receipts.append(existing[1])
                continue
            projection = self.ledger.project()
            if projection.logical_time is None:
                raise CharacterLifeResponseError("clock_unavailable")
            value = payload.model_dump(mode="json")
            event = WorldEvent.from_payload(
                schema_version="world-v2.1",
                event_id=event_id,
                world_id=world_id,
                event_type="CharacterLifeResponseRecorded",
                logical_time=projection.logical_time,
                created_at=source_event.created_at,
                actor=self._owner,
                source=SOURCE,
                trace_id=source_event.trace_id,
                causation_id=source_event.event_id,
                correlation_id=source_event.correlation_id,
                idempotency_key=domain_idempotency_key(
                    event_type="CharacterLifeResponseRecorded",
                    world_id=world_id,
                    payload=value,
                ),
                payload=value,
            )
            reduce_character_life_response(projection, event)
            receipts.append(
                self.ledger.commit_at_cursor(
                    (event,),
                    expected_cursor=ProjectionCursor(
                        world_revision=projection.world_revision,
                        deliberation_revision=projection.deliberation_revision,
                        ledger_sequence=projection.ledger_sequence,
                    ),
                    commit_id="commit:character-life-response:" + identity,
                )
            )
        return tuple(receipts)


__all__ = [
    "CharacterLifeResponseError",
    "CharacterLifeResponseRuntime",
    "character_life_response_id",
    "derive_character_life_responses",
    "reduce_character_life_response",
]
