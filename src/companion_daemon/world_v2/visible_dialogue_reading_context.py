"""Bounded language context from the candidate's already-selected dialogue.

The caller still authenticates the original source table against World audits.
This view proves neither embedded events nor delivery to a person; it keeps
speaker and delivery distinctions for interpretation without adding sources.
"""
from datetime import datetime
import hashlib
import json

from .recent_dialogue import RecentDialogueItem
from .visible_source_review_receipt import compile_visible_candidate_material

CONTRACT = "visible-dialogue-reading-context.1"
MAX_ITEMS = 8
MAX_TEXT_BYTES = 8192


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compile_dialogue_reading_context(*, candidate, source_table, source_ref_aliases):
    """Use whole recent utterances; preserve selection gaps and exact bindings."""
    material = compile_visible_candidate_material(
        candidate=candidate, source_table=source_table, source_ref_aliases=source_ref_aliases,
    )
    table = source_table.as_dict()
    logical_at = datetime.fromisoformat(table['logical_time'].replace('Z', '+00:00'))
    selected = {}
    for index, entry in enumerate(table['source_materials']):
        item = entry['material']
        if item.get('kind') != 'pinned_context_item' or item.get('lane') != 'recent_dialogue':
            continue
        if item.get('availability') != 'available':
            continue
        capsule_item = item['item']
        value = RecentDialogueItem.model_validate_json(_json(capsule_item['value']), strict=True)
        if 'withhold' in (item.get('privacy_class'), capsule_item.get('privacy_class'), value.privacy_class):
            raise ValueError('withheld dialogue cannot enter reading context')
        actor = table['subjects'][f'{value.speaker}_actor_ref']
        if not actor or value.speaker_ref != actor or item.get('actor_ref') != actor:
            raise ValueError('dialogue context speaker differs from pinned actor')
        if value.occurred_at > logical_at or any(c.authority_world_revision > table['pin']['world_revision'] for c in value.source_claims):
            raise ValueError('dialogue context cannot read beyond pinned cursor')
        prior = selected.get(value.dialogue_id)
        if prior is not None:
            if prior[1] != value:
                raise ValueError('conflicting selected versions of one dialogue item')
            continue
        selected[value.dialogue_id] = (index, value, entry['material_identity'])
    ordered = sorted(selected.values(), key=lambda row: (row[1].sequence, row[1].dialogue_id))
    kept = []
    text_bytes = 0
    for row in reversed(ordered):
        size = len(row[1].text.encode())
        if len(kept) == MAX_ITEMS or text_bytes + size > MAX_TEXT_BYTES:
            break  # Keep a chronological suffix, not cherry-picked smaller utterances.
        kept.append(row)
        text_bytes += size
    kept.reverse()
    return {
        'contract': CONTRACT,
        'candidate_sha256': hashlib.sha256(material['candidate_json'].encode()).hexdigest(),
        'source_table_sha256': source_table.payload_hash,
        'bindings': [{'material_index': i, 'material_identity': identity, 'dialogue_id': v.dialogue_id,
                      'sequence': v.sequence, 'speaker_ref': v.speaker_ref} for i, v, identity in kept],
        'model_context': {
            'scope': 'Selected recorded utterances for language interpretation only. Not complete history or evidence of embedded events. Missing records do not prove an event never happened; provider_accepted does not prove the person heard it.',
            'order': 'ascending_recorded_sequence',
            'selection_truncated': len(kept) != len(ordered),
            'complete_history': False,
            'utterances': [{'speaker': v.speaker, 'delivery_state': v.delivery_state, 'text': v.text} for _, v, _ in kept],
        },
    }
