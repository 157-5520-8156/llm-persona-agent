"""Compose a paired Experience from two already accepted authors, without a call.

The existing typed proposal is recorded separately, then acceptance/effect/
descriptor commit atomically. A failed CAS leaves no partial Experience; retry
or cold recovery reuses the original response audit, including an explicit null.
"""

from __future__ import annotations

import hashlib
import json

from .character_life_experience_contract import (
    CHARACTER_LIFE_EXPERIENCE_POLICY_REFS,
    derive_character_life_experience_summary,
)
from .character_life_response_contract import CharacterLifeResponseRecordedPayload
from .character_life_response_runtime import derive_character_life_responses
from .event_identity import domain_idempotency_key
from .errors import ConcurrencyConflict
from .experience_events import ExperienceCommittedPayload, experience_mutation_hash
from .life_content_events import LifeContentRecordedPayload
from .life_content_store import StoredLifeContent, life_content_payload_hash
from .schemas import (
    EvidenceRef,
    ExperienceOccurrenceSettlementBinding,
    ExperienceOrigin,
    ExperienceProjection,
    ExperienceProposedMutation,
    ExperienceProposalProjection,
    ExperienceValues,
    ExperienceWorldLifeResponseBinding,
    ProjectionCursor,
    WorldEvent,
    experience_semantic_fingerprint,
)
from .world_consequence_contract import WorldConsequenceV2


SOURCE = "world-v2:character-life-experience"
_PRIVACY_RANK = {"public": 0, "shareable": 1, "personal": 2, "private": 3, "withhold": 4}


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cursor(state) -> ProjectionCursor:
    return ProjectionCursor(
        world_revision=state.world_revision,
        deliberation_revision=state.deliberation_revision,
        ledger_sequence=state.ledger_sequence,
    )


def validate_character_life_experience_binding(*, state, world_id: str, binding) -> None:
    """Reprove accepted response bytes and its exact original role decision.

    This validates provenance, not any external assertion in response_text.
    Settlement state, privacy and participants are also checked by the normal
    Experience reducer; readers can call this proof at their own pinned cursor.
    """
    binding = ExperienceWorldLifeResponseBinding.model_validate_json(binding.model_dump_json())
    source = binding.settlement
    response = binding.response
    committed = next(
        (x for x in state.committed_world_event_refs if x.event_id == binding.response_event_ref),
        None,
    )
    settlement = next(
        (x for x in state.committed_world_event_refs if x.event_id == source.authority_event_ref),
        None,
    )
    occurrence = next(
        (x for x in state.world_occurrences if x.occurrence_id == source.occurrence_id), None
    )
    selected = (
        next(
            (
                x
                for x in occurrence.candidate_outcomes
                if x.candidate_result_ref == occurrence.settled_outcome_ref
            ),
            None,
        )
        if occurrence is not None
        else None
    )
    origin = response.origin
    if any(
        (
            committed is None,
            committed is not None and committed.event_type != "CharacterLifeResponseRecorded",
            committed is not None and committed.world_revision != binding.response_world_revision,
            committed is not None and committed.payload_hash != binding.response_payload_hash,
            life_content_payload_hash(_canonical(response.model_dump(mode="json")))
            != binding.response_payload_hash,
            settlement is None,
            settlement is not None and settlement.event_type != "WorldOccurrenceSettled",
            settlement is not None and settlement.world_revision != source.authority_world_revision,
            settlement is not None and settlement.payload_hash != source.authority_payload_hash,
            binding.response_world_revision <= source.authority_world_revision,
            origin.source_event_ref != source.authority_event_ref,
            origin.source_world_revision != source.authority_world_revision,
            origin.source_payload_hash != source.authority_payload_hash,
            occurrence is None,
            occurrence is not None and occurrence.status != "settled",
            occurrence is not None and occurrence.visibility == "withhold",
            occurrence is not None
            and occurrence.entity_revision != source.occurrence_entity_revision,
            occurrence is not None and occurrence.result_id != source.result_id,
            occurrence is not None and occurrence.result_payload_ref != source.result_payload_ref,
            occurrence is not None and occurrence.result_payload_hash != source.result_payload_hash,
            occurrence is not None
            and occurrence.settlement_event_ref != source.authority_event_ref,
            occurrence is not None
            and occurrence.settlement_world_revision != source.authority_world_revision,
            occurrence is not None
            and occurrence.settlement_payload_hash != source.authority_payload_hash,
            occurrence is not None and response.actor_ref not in occurrence.participant_refs,
            selected is None,
            selected is not None and selected.result_contract != "world-consequence.2",
            selected is not None and selected.privacy_class == "withhold",
            selected is not None and selected.result_id != source.result_id,
            selected is not None and selected.result_payload_ref != source.result_payload_ref,
            selected is not None
            and selected.result_payload_hash.removeprefix("sha256:")
            != source.result_payload_hash.removeprefix("sha256:"),
        )
    ):
        raise ValueError("character_life_experience.source_binding_mismatch")
    expected = derive_character_life_responses(
        state=state,
        world_id=world_id,
        proposal_id=origin.proposal_id,
        owner_actor_ref=response.actor_ref,
    )
    if response not in expected:
        raise ValueError("character_life_experience.original_role_decision_mismatch")


def character_life_experience_evidence(binding) -> tuple[EvidenceRef, ...]:
    source = binding.settlement
    return (
        EvidenceRef(
            ref_id=source.authority_event_ref,
            evidence_type="settled_world_event",
            claim_purpose="past_experience",
            source_world_revision=source.authority_world_revision,
            immutable_hash=source.authority_payload_hash,
        ),
        EvidenceRef(
            ref_id=binding.response_event_ref,
            evidence_type="committed_world_event",
            claim_purpose="private_hypothesis",
            source_world_revision=binding.response_world_revision,
            immutable_hash=binding.response_payload_hash,
        ),
    )


class CharacterLifeExperienceRuntime:
    def __init__(self, *, ledger, content_store, owner_actor_ref: str) -> None:
        if not owner_actor_ref:
            raise ValueError("character life experience requires an owner")
        self.ledger = ledger
        self._content_store = content_store
        self._owner = owner_actor_ref

    def accept(
        self, *, world_id: str, audit_cursor: ProjectionCursor, response_event_ref: str
    ) -> str:
        if world_id != self.ledger.world_id:
            raise ValueError("character_life_experience.world_mismatch")
        pinned = self.ledger.project_at(audit_cursor)
        source = next(
            (x for x in pinned.committed_world_event_refs if x.event_id == response_event_ref), None
        )
        found = self.ledger.lookup_event_commit(response_event_ref)
        if source is None or found is None or source.event_type != "CharacterLifeResponseRecorded":
            raise ValueError("character_life_experience.response_not_in_pin")
        event = found[0]
        response = CharacterLifeResponseRecordedPayload.model_validate_json(event.payload_json)
        if event.world_id != world_id or response.actor_ref != self._owner:
            raise ValueError("character_life_experience.actor_mismatch")
        occurrence = next(
            (
                x
                for x in pinned.world_occurrences
                if x.settlement_event_ref == response.origin.source_event_ref
            ),
            None,
        )
        if occurrence is None:
            raise ValueError("character_life_experience.settlement_missing")
        binding = ExperienceWorldLifeResponseBinding(
            settlement=ExperienceOccurrenceSettlementBinding(
                authority_event_ref=response.origin.source_event_ref,
                authority_world_revision=response.origin.source_world_revision,
                authority_payload_hash=response.origin.source_payload_hash,
                occurrence_id=occurrence.occurrence_id,
                occurrence_entity_revision=occurrence.entity_revision,
                result_id=occurrence.result_id,
                result_payload_ref=occurrence.result_payload_ref,
                result_payload_hash=occurrence.result_payload_hash,
            ),
            response_event_ref=response_event_ref,
            response_world_revision=source.world_revision,
            response_payload_hash=source.payload_hash,
            response=response,
        )
        validate_character_life_experience_binding(state=pinned, world_id=world_id, binding=binding)
        selected = next(
            x
            for x in occurrence.candidate_outcomes
            if x.candidate_result_ref == occurrence.settled_outcome_ref
        )
        result = self._content_store.read_exact(content_ref=selected.content_ref)
        result_hash = occurrence.result_payload_hash.removeprefix("sha256:")
        if (
            result is None
            or result.content_payload_hash != result_hash
            or selected.content_payload_hash != result_hash
            or life_content_payload_hash(result.text) != result_hash
        ):
            raise ValueError("character_life_experience.world_result_unavailable")
        consequence = WorldConsequenceV2.model_validate_json(result.text)
        if _canonical(consequence.model_dump(mode="json")) != result.text:
            raise ValueError("character_life_experience.world_result_not_canonical")
        suffix = hashlib.sha256(
            _canonical([world_id, self._owner, response.origin.source_event_ref]).encode()
        ).hexdigest()
        experience_id = "experience:character-life-response:" + suffix
        summary = StoredLifeContent(
            content_ref="content:character-life-experience:" + suffix,
            content_kind="experience_summary",
            text=derive_character_life_experience_summary(binding).canonical_json(),
            content_payload_hash=life_content_payload_hash(
                derive_character_life_experience_summary(binding).canonical_json()
            ),
        )
        self._content_store.put_if_absent(summary)
        state = self.ledger.project()
        existing = next((x for x in state.experiences if x.experience_id == experience_id), None)
        if existing is not None:
            if (
                existing.authority_contract_version != "experience.2"
                or existing.values.source_bindings != (binding,)
                or existing.values.summary_ref != summary.content_ref
                or existing.values.summary_payload_hash != summary.content_payload_hash
            ):
                raise ValueError("character_life_experience.effect_identity_conflict")
            return experience_id
        if state.logical_time is None:
            raise ValueError("character_life_experience.clock_unavailable")
        policy = CHARACTER_LIFE_EXPERIENCE_POLICY_REFS
        # Revision-bound technical proposal identity allows a stale attempt to
        # be superseded without rewriting its bytes or reauthoring a response.
        # The Experience/event and summary identities remain source-bound.
        prefix = "character-life-experience:" + suffix + ":" + str(state.world_revision)
        experience_event_id = "event:character-life-experience:" + suffix
        change_id, transition_id = "change:" + prefix, "transition:" + prefix
        privacy = max(
            ("private", occurrence.visibility, selected.privacy_class),
            key=_PRIVACY_RANK.__getitem__,
        )
        values = ExperienceValues(
            summary_ref=summary.content_ref,
            summary_payload_hash=summary.content_payload_hash,
            occurred_from=occurrence.activated_at,
            occurred_to=occurrence.settled_at,
            participant_refs=occurrence.participant_refs,
            source_bindings=(binding,),
            privacy_class=privacy,
        )
        experience = ExperienceProjection(
            experience_id=experience_id,
            authority_contract_version="experience.2",
            semantic_fingerprint=experience_semantic_fingerprint(values=values, policy_refs=policy),
            values=values,
            origin=ExperienceOrigin(
                change_id=change_id,
                transition_id=transition_id,
                policy_refs=policy,
                accepted_event_ref=experience_event_id,
            ),
        )
        evidence = character_life_experience_evidence(binding)
        base = dict(
            change_id=change_id,
            transition_id=transition_id,
            expected_entity_revision=0,
            evidence_refs=evidence,
            policy_refs=policy,
            acceptance_id="acceptance:" + prefix,
            proposal_id="proposal:" + prefix,
            evaluated_world_revision=state.world_revision,
            accepted_change_hash="0" * 64,
            experience=experience,
        )
        base["accepted_change_hash"] = experience_mutation_hash(base)
        mutation = ExperienceCommittedPayload.model_validate(base)
        proposal = ExperienceProposalProjection(
            proposal_id=mutation.proposal_id,
            proposal_encoding="typed-authority-v1",
            authority_contract_ref="proposal-contract:experience.1",
            change_id=change_id,
            transition_id=transition_id,
            evaluated_world_revision=state.world_revision,
            proposed_change_hash=mutation.accepted_change_hash,
            evidence_refs=evidence,
            policy_refs=policy,
            proposed_mutation=ExperienceProposedMutation(
                payload_json=_canonical(mutation.model_dump(mode="json"))
            ),
        )

        def make(kind, identity, value, cause):
            return WorldEvent.from_payload(
                schema_version="world-v2.1",
                event_id=identity,
                world_id=world_id,
                event_type=kind,
                logical_time=state.logical_time,
                created_at=event.created_at,
                actor=self._owner,
                source=SOURCE,
                trace_id=event.trace_id,
                causation_id=cause,
                correlation_id=event.correlation_id,
                idempotency_key=domain_idempotency_key(
                    event_type=kind, world_id=world_id, payload=value
                )
                or identity,
                payload=value,
            )

        proposed = make(
            "ProposalRecorded",
            "event:proposal:" + prefix,
            proposal.model_dump(mode="json"),
            response_event_ref,
        )
        accepted = make(
            "AcceptanceRecorded",
            "event:acceptance:" + prefix,
            dict(
                status="accepted",
                acceptance_id=mutation.acceptance_id,
                proposal_id=mutation.proposal_id,
                evaluated_world_revision=state.world_revision,
                accepted_change_id=change_id,
                accepted_change_hash=mutation.accepted_change_hash,
            ),
            proposed.event_id,
        )
        committed = make(
            "ExperienceCommitted",
            experience_event_id,
            mutation.model_dump(mode="json"),
            accepted.event_id,
        )
        descriptor = LifeContentRecordedPayload(
            content_id="life-content:" + prefix,
            content_kind="experience_summary",
            content_ref=summary.content_ref,
            content_payload_hash=summary.content_payload_hash,
            privacy_class=privacy,
            source_kind="experience",
            source_event_ref=committed.event_id,
            source_world_revision=state.world_revision + 2,
            source_payload_hash=committed.payload_hash,
            source_entity_id=experience_id,
            source_entity_revision=1,
        )
        content = make(
            "LifeContentRecorded",
            "event:content:" + prefix,
            descriptor.model_dump(mode="json"),
            committed.event_id,
        )
        prior = self.ledger.lookup_event_commit(proposed.event_id)
        if prior is None:
            self.ledger.commit_at_cursor(
                (proposed,), expected_cursor=_cursor(state), commit_id="commit:proposal:" + prefix
            )
        elif prior[0] != proposed:
            raise ValueError("character_life_experience.proposal_identity_conflict")
        current = self.ledger.project()
        if current.world_revision != state.world_revision:
            raise ConcurrencyConflict("character life experience proposal became stale")
        self.ledger.commit_at_cursor(
            (accepted, committed, content),
            expected_cursor=_cursor(current),
            commit_id="commit:" + prefix,
        )
        return experience_id


__all__ = [
    "CharacterLifeExperienceRuntime",
    "character_life_experience_evidence",
    "validate_character_life_experience_binding",
]
