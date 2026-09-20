"""Connect a model-declared factual obligation to existing source permissions.

This checks authority compatibility, not whether the model correctly read a
sentence. Atomic decomposition, participants, time and entailment remain the
reviewer's responsibility. No candidate text is classified here.
"""
from __future__ import annotations

from .life_source_origin import canonical

CONTRACT = 'life-source-review.14'
AUTHORITY_CONTRACT = 'life-claim-source-authority.1'
# Include obligations even when the pinned packet has no matching evidence.
SCOPES = ('accepted_fact', 'historical_accepted_fact', 'biographical_coordinate',
          'accepted_intention', 'activity_lifecycle', 'environment', 'external_fact',
          'subjective_history', 'utterance_record', 'report_uptake', 'unresolved')
ROLES = ('companion', 'counterpart', 'source_owner', 'general', 'other', 'none', 'unresolved')
PERSONAL = frozenset(('companion', 'counterpart', 'source_owner'))


def configure_claim_authority(*, request, packet, schema):
    """New explicit wire; never mutate or reinterpret an old preparation."""
    if packet['contract'] != CONTRACT or packet.get('support_selection_contract') != 'life-bound-permission-selection.1':
        raise ValueError('claim authority requires its explicit bound-permission contract')
    claim = schema['properties']['fields']['items']['properties']['record_bound_claims']['items']
    old = claim['properties']
    claim['properties'] = {
        'source_span': old['source_span'], 'proposition': old['proposition'],
        'required_scope': {'type': 'string', 'enum': list(SCOPES)},
        'subject_role': {'type': 'string', 'enum': list(ROLES)},
        'subject_ref': {'type': ['string', 'null'], 'minLength': 1, 'maxLength': 512},
        'reason': old['reason'], 'supports': old['supports'], 'verdict': old['verdict'],
    }
    claim['required'] = list(claim['properties'])
    packet['claim_source_authority_contract'] = AUTHORITY_CONTRACT
    # Existing immutable reading ownership is an identity coordinate. It never
    # changes a nonpersonal environment permission into personal experience.
    sources = {row['reading_id']: row for row in packet['source_readings']['readings']}
    packet['claim_subject_bindings'] = [
        {'permission_id': choice['permission_id'],
         'subject_ref': permission_subject_ref(choice, sources[choice['reading_id']])}
        for choice in packet['permission_choices']
    ]
    request['messages'][0]['content'] += (
        ' CLAIM/SOURCE AUTHORITY: For each atomic record_bound_claim, state required_scope and '
        'subject_role/subject_ref from the proposition itself, before choosing evidence. These are '
        'the obligation to prove, not a paraphrase of the source you found. A personal action or '
        'perception is an external_fact about that actor; environmental presence is not the actor\'s '
        'observation. Personal roles require their exact actor ref; companion means the current '
        'authorship actor. source_owner names an explicitly identified reporting/Fact subject, not '
        'a wildcard. For report_uptake or utterance_record, the subject is the report/speech owner; '
        'still preserve every embedded participant and do not promote reports to objective events. '
        'General/none/other retain nonpersonal source permissions and use null subject_ref; other '
        'does not mean the companion or counterpart. Unresolved scope or subject cannot be supported. '
        'Select accepted_fact/historical_accepted_fact only for the exact accepted predicate/value, '
        'accepted_intention for an intention, activity_lifecycle for lifecycle state, and '
        'subjective_history for an earlier subjective state. These are not interchangeable proofs. '
        'A supported claim needs at least one selected permission with its required scope and bound '
        'subject. Other selected readings provide context only; they cannot discharge that obligation. '
        'Matching coordinates are necessary, not sufficient: judge the ENTIRE proposition, including '
        'predicate, participant roles, time, negation and modality, against the actual source values. '
        'Keep all existing per-field and whole-candidate coverage checks and present-authorship freedom.'
    )
    request['messages'][1]['content'] = canonical(packet)
    request['tools'][0]['function']['name'] = 'review_life_candidate_v8'
    request['tool_choice']['function']['name'] = 'review_life_candidate_v8'


def permission_subject_ref(choice, source):
    if choice['subject_role'] in PERSONAL:
        return source.get('source_owner_ref')
    return None


def claim_authority_failure(claim, *, choices, sources, actor_ref):
    """Reject an unsupported authority edge without judging claim semantics."""
    role, ref = claim['subject_role'], claim['subject_ref']
    if claim['required_scope'] == 'unresolved' or role == 'unresolved':
        return 'The reviewer left the factual obligation scope or subject unresolved.'
    if ((role in PERSONAL and not ref)
        or (role not in PERSONAL and ref is not None)
        or (role == 'companion' and ref != actor_ref)
        or (role == 'counterpart' and ref == actor_ref)):
        return 'The factual obligation lacks its exact declared subject binding.'
    for support in claim['supports']:
        choice = choices[support['permission_id']]
        if choice['claim_scope'] != claim['required_scope']:
            continue
        source = sources[choice['reading_id']]
        bound_ref = permission_subject_ref(choice, source)
        if role in PERSONAL:
            if choice['subject_role'] not in PERSONAL or ref != bound_ref:
                continue
            # source_owner is resolved by exact identity, never by provenance
            # ownership on a nonpersonal permission. The two actor roles cannot
            # be swapped even when a malformed source asserts the same ref.
            if choice['subject_role'] == 'companion' and ref != actor_ref:
                continue
            if choice['subject_role'] == 'counterpart' and ref == actor_ref:
                continue
            return None
        if role == choice['subject_role'] and bound_ref is None:
            return None
    return ('No selected permission supports the factual obligation at '
            f"{claim['required_scope']}/{role}/{ref or 'null'}. Source ownership or contextual "
            'material cannot substitute for the required claim authority.')
