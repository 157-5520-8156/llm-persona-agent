"""Recover only the exact accepted Thread author's description for the owner."""
import json

from .proposal_envelope import DecisionProposal, validate_proposal_envelope
from .thread_events import ThreadChangedPayload
from .schemas import ThreadProposalProjection


def read_thread_reason(*, ledger, projection, thread):
    if thread.values.privacy_class == 'withhold' or thread not in projection.threads:
        return None

    def read(ref, kind=None):
        found = ledger.lookup_event_commit(ref)
        if found is None:
            raise ValueError('missing pending-item source')
        event, commit = found
        if (event.world_id != projection.world_id or event.event_id != ref
            or kind is not None and event.event_type != kind
            or commit.world_revision > projection.world_revision
            or commit.deliberation_revision > projection.deliberation_revision
            or commit.ledger_sequence > projection.ledger_sequence):
            raise ValueError('pending-item source is outside its pin')
        return event

    try:
        changed = read(thread.origin.accepted_event_ref)
        if changed.event_type not in {'ThreadOpened', 'ThreadUpdated', 'ThreadResolved', 'ThreadCancelled', 'ThreadSuperseded', 'ThreadCompensated'}:
            return None
        mutation = ThreadChangedPayload.model_validate_json(changed.payload_json)
        if mutation.thread_after != thread:
            return None
        acceptance_event = read(changed.causation_id, 'AcceptanceRecorded')
        acceptance = acceptance_event.payload()
        if acceptance.get('acceptance_id') != mutation.acceptance_id or acceptance.get('status') != 'accepted':
            return None
        typed_event = read(acceptance_event.causation_id, 'ProposalRecorded')
        typed = ThreadProposalProjection.model_validate_json(typed_event.payload_json)
        if (typed.proposal_id != mutation.proposal_id or typed.change_id != thread.origin.change_id
            or typed.proposed_change_hash != mutation.accepted_change_hash
            or typed.proposed_mutation.payload_json != changed.payload_json):
            return None
        audit_event = read(typed_event.causation_id, 'ProposalRecorded')
        audit = next((a for a in projection.proposal_audits if a.event_ref == audit_event.event_id), None)
        if (audit is None or audit.event_payload_hash != audit_event.payload_hash
            or audit_event.payload().get('proposal_json') != audit.proposal_json):
            return None
        proposal = validate_proposal_envelope(json.loads(audit.proposal_json))
        if not isinstance(proposal, DecisionProposal):
            return None
        matches = [c for c in proposal.proposed_changes if c.change_id == typed.change_id
                   and c.kind == 'thread_transition' and c.target_id == thread.thread_id
                   and c.transition == typed.transition_kind
                   and c.expected_entity_revision == mutation.expected_entity_revision]
        if len(matches) != 1:
            return None
        text = matches[0].payload.value().get('reason_summary')
        return text if isinstance(text, str) and text.strip() else None
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
