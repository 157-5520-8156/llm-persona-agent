"""Bounded multi-source choice by the same CharacterInterior; no substitute author."""
from __future__ import annotations

import json
from .character_interior.life_memory import _memory_opportunity, _decision_payload, _memory_retention_capability
from .character_interior.purpose_context import InteriorPurposeContext
from .fact_memory_draft import FactMemoryDraftTechnicalFailure, materialize_fact_memory_draft
from .prehistory_memory_source import prehistory_memory_reading

PURPOSE = 'prehistory_memory_batch'
CONTRACT = 'character-interior-prehistory-memory-batch.1'
MAX_BATCH_SIZE = 8
MAX_BATCH_TEXT_CHARACTERS = 7200


def batch_opportunity(*, world_id, rows, archive, cursor, attempt_ordinal=1):
    if type(attempt_ordinal) is not int or not 1 <= attempt_ordinal <= 3:
        raise ValueError("historical batch retry ordinal is invalid")
    rows = tuple(sorted(rows, key=lambda r: r.record.record_id))
    if not 1 <= len(rows) <= MAX_BATCH_SIZE or len({r.record.record_id for r in rows}) != len(rows):
        raise ValueError('historical batch must contain distinct bounded sources')
    if any((r.archive_id, r.actor_ref, r.accepted_at) !=
           (archive.manifest.archive_id, archive.manifest.actor_ref, rows[0].accepted_at)
           or r.accepted_world_revision > cursor.world_revision for r in rows):
        raise ValueError('historical batch crosses actor, import snapshot or archive')
    if sum(len(r.record.statement) for r in rows) > MAX_BATCH_TEXT_CHARACTERS:
        raise ValueError('historical batch source text exceeds its bound')
    source_refs = tuple(sorted({archive.accepted_event_ref, *(r.accepted_event_ref for r in rows)}))
    capability = {
        'source_kind': 'character_prehistory_batch',
        'attempt_ordinal': attempt_ordinal,
        'retry_context': 'Previous initialization did not complete these sources; make explicit independent choices.' if attempt_ordinal > 1 else None,
        'source_ids': [r.record.record_id for r in rows],
        'items': [{'source_id': r.record.record_id, 'verified_source_text': r.record.statement,
            'prehistory': prehistory_memory_reading(r, archive, initialization_snapshot=True).model_dump(mode='json')}
            for r in rows],
        'individual_choice_schema': _memory_retention_capability(
            source_kind='character_prehistory', predicate_code='character.prehistory', source_text='')['decision_contract'],
        'instruction': 'Choose retain or no_change separately for each offered source_id. Do not merge sources or invent prose. '
            'A group does not require all its items to be retained. Return choices with source_id and retention. '
            'Omitted sources are technical incompleteness, never a no_change decision.',
    }
    return _memory_opportunity(world_id=world_id, actor_ref=archive.manifest.actor_ref, purpose=PURPOSE,
        context=InteriorPurposeContext(inner_turn_ref='prehistory-batch:' + rows[0].accepted_event_ref,
            trigger_ref=rows[0].accepted_event_ref, cursor=cursor, logical_time=rows[0].accepted_at,
            source_refs=source_refs), capability_payload=capability)


def batch_opportunity_from_refs(*, world_id, projection, source_refs, cursor, attempt_ordinal=1):
    rows = tuple(r for r in projection.prehistory_records if r.accepted_event_ref in source_refs)
    if not rows:
        raise ValueError('historical batch has no accepted records')
    archive = next((a for a in projection.prehistory_archives if a.manifest.archive_id == rows[0].archive_id), None)
    if archive is None or any(r.archive_id == rows[0].archive_id and r.accepted_world_revision > cursor.world_revision
                              for r in projection.prehistory_records):
        raise ValueError('historical batch requires a complete import snapshot')
    opportunity = batch_opportunity(world_id=world_id, rows=rows, archive=archive, cursor=cursor, attempt_ordinal=attempt_ordinal)
    if opportunity.source_refs != tuple(source_refs):
        raise ValueError('historical batch source closure differs from its import')
    return opportunity


def parse_batch_choices(payload, offered_ids):
    value = {k: v for k, v in payload.items() if k != 'contract'}
    if set(value) != {'choices'} or not isinstance(value['choices'], list) or len(value['choices']) > MAX_BATCH_SIZE:
        raise ValueError('batch retention requires a bounded choices array')
    choices = {}
    for item in value['choices']:
        if not isinstance(item, dict) or set(item) != {'source_id', 'retention'}:
            raise ValueError('batch retention item requires source_id and retention')
        ref, decision = item['source_id'], item['retention']
        if not isinstance(ref, str) or ref not in offered_ids or ref in choices or not isinstance(decision, dict):
            raise ValueError('batch retention source is unoffered, duplicate or malformed')
        # Provider strict schemas use nullable siblings; null conveys no choice.
        decision = {key: val for key, val in decision.items() if val is not None}
        choices[ref] = materialize_fact_memory_draft(json.dumps(decision))
    return choices


def batch_drafts(result, opportunity):
    payload = _decision_payload(result=result, opportunity=opportunity, purpose=PURPOSE,
        technical_failure=FactMemoryDraftTechnicalFailure)
    return parse_batch_choices(payload, frozenset(opportunity.capability_manifest.payload['source_ids']))


def validate_batch_payload(payload, offered_tokens):
    parse_batch_choices(payload, offered_tokens)


def batch_tool_contract(*, capability_payload, source_refs, recall_allowed):
    from pydantic import BaseModel, ConfigDict, Field, create_model
    from .character_interior.structured_role import _MemoryRetentionPayload
    from .character_interior.structured_role_tool_contract import _provider_schema, _compile_generic_decision_contract
    class Choice(BaseModel):
        model_config = ConfigDict(extra='forbid', strict=True)
        source_id: str = Field(json_schema_extra={'enum': capability_payload['source_ids']})
        retention: _MemoryRetentionPayload
    payload = create_model('PrehistoryBatchChoices', __config__=ConfigDict(extra='forbid', strict=True),
        choices=(list[Choice], Field(max_length=MAX_BATCH_SIZE)))
    return _compile_generic_decision_contract(purpose=PURPOSE, tool_name='character_role_prehistory_memory_batch_v1',
        payload_schema=_provider_schema(payload), capability_identity=capability_payload,
        source_refs=source_refs, recall_allowed=recall_allowed,
        description='Choose independently for each source. Keep exact source_id; missing items are incomplete, not declined.')
