"""Explicit user-approved text provenance, without a semantic review verdict."""
from __future__ import annotations

import hashlib
import json
from pydantic import Field
from .schema_core import FrozenModel

REQUIREMENT = 'visible-source-text-observation-required.1'
EVIDENCE = 'visible-source-text-observation-evidence.1'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def eligible_text(proposal):
    from .proposal_envelope import DecisionProposal
    if not isinstance(proposal, DecisionProposal):
        return False
    # These projections do not grant objective-event or memory authority.
    if any(c.kind not in {'expression_plan_transition', 'appraisal_transition', 'affect_transition'}
           for c in proposal.proposed_changes):
        return False
    expressions = [c for c in proposal.proposed_changes if c.kind == 'expression_plan_transition']
    if len(expressions) != 1 or not proposal.action_intents:
        return False
    beats = expressions[0].payload.value().get('beat_drafts', [])
    return bool(beats) and all(b.get('content_type') == 'text/plain'
                              and isinstance(b.get('inline_text'), str) for b in beats) and all(
        action.kind in {'reply', 'followup'} and action.layer == 'external_action'
        for action in proposal.action_intents
    )


class TextObservationReceipt(FrozenModel):
    receipt_hash: str = Field(pattern=r'^[0-9a-f]{64}$')


def prepare_evidence(*, requirement, author_request_json, author_call, author_request_hash, proposal, table, audits):
    if json.loads(requirement).get('contract') != REQUIREMENT or not eligible_text(proposal):
        raise ValueError('ordinary text mode does not authorize this candidate')
    return {
        'contract': EVIDENCE, 'requirement_json': requirement,
        'author_request_json': author_request_json,
        'semantic_review': 'not_performed',
        'author_call': author_call, 'author_request_hash': author_request_hash,
        'candidate_hash': digest(proposal.model_dump(mode='json')),
        'source_table_hash': digest(table.as_dict()),
        'recall_audits': [a.model_dump(mode='json') for a in audits],
    }


def verify_evidence(*, value, requirement, proposal, author_call, author_request_hash, recall_audits):
    from .visible_source_runtime import requirement_table
    from .visible_source_author_request import verify_visible_source_author_request
    from .visible_recall_sources import supplement_recalled_sources

    verify_visible_source_author_request(value['author_request_json'], expected_request_hash=author_request_hash)
    table, used = supplement_recalled_sources(table=requirement_table(requirement), audits=tuple(recall_audits),
                                              author_request_json=value['author_request_json'])
    expected = prepare_evidence(requirement=requirement, author_request_json=value['author_request_json'],
        author_call=author_call, author_request_hash=author_request_hash, proposal=proposal, table=table, audits=used)
    if value != expected:
        raise ValueError('ordinary text provenance differs from its pinned author or sources')
    return TextObservationReceipt(receipt_hash=digest(value))
