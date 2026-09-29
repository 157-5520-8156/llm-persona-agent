"""Exact settled-life recall carrier and its visible-source bridge.

Recall selection grants no new authority. The carrier keeps the original
settlement/descriptor bindings and only the World prose actually shown.
"""
from __future__ import annotations

import json


def validate_recalled_life(document):
    item = document.settled_life
    content = item.content
    if (document.source_slice != 'world_life' or document.memory_kind != 'episodic'
        or document.authority != 'world_fact' or document.effective_epistemic_scope != 'world_fact'
        or document.status != 'active' or document.accepted_fact is not None
        or document.prehistory is not None or document.actor_ref not in item.participant_refs
        or document.subject_refs != tuple(sorted(set(item.participant_refs)))
        or document.privacy_class != item.privacy_class or item.privacy_class == 'withhold'
        or document.occurred_from != item.settled_at or content is None
        or content.world_consequence is None or content.character_response is not None
        or content.source_entity_id != item.occurrence_id
        or content.source_entity_revision != item.occurrence_entity_revision
        or content.authority_event_ref != item.source.authority_event_ref
        or content.authority_world_revision != item.source.authority_world_revision
        or content.authority_payload_hash != item.source.authority_payload_hash
        or content.content_ref != item.result_payload_ref
        or content.content_payload_hash != item.result_payload_hash.removeprefix('sha256:')
        or content.content_kind != 'occurrence_result' or content.privacy_class == 'withhold'):
        raise ValueError('recalled life differs from its settled source')
    expected = {
        item.source.authority_event_ref: ('WorldOccurrenceSettled', item.source.authority_world_revision,
                                          item.source.authority_payload_hash),
        content.descriptor_event_ref: ('LifeContentRecorded', content.descriptor_world_revision,
                                       content.descriptor_payload_hash),
    }
    actual = {b.ref: (b.authority_type, b.source_world_revision, b.immutable_hash)
              for b in document.source_bindings if b.source_kind == 'committed_event'}
    if actual != expected or len(document.source_bindings) != 2:
        raise ValueError('recalled life lacks exact settlement and descriptor closure')
    world = content.world_consequence
    if world.environment.epistemic_scope != 'settled_world_environment':
        raise ValueError('recalled life is not settled')
    fields = {'environment': world.environment}
    if world.authorized_attempt_result is not None:
        if (world.authorized_attempt_result.execution_binding.actor_ref != document.actor_ref
            or world.authorized_attempt_result.epistemic_scope != 'settled_result_of_bound_attempt'):
            raise ValueError('recalled attempt belongs to another actor')
        fields['authorized_attempt_result'] = world.authorized_attempt_result
    field = document.source_item_ref.removeprefix(item.occurrence_id + ':')
    if (document.source_item_ref != item.occurrence_id + ':' + field or field not in fields
        or document.text != fields[field].text[:1024]):
        raise ValueError('recalled life text differs from the selected field')


def presented_recalled_life(*, table, audits, materials):
    # Import at the seam to avoid turning recall's source type into a review dependency.
    from .context_capsule import ResolvedSourceBinding, source_bindings_hash
    from .model_facing_context import compact_model_facing_context
    from .recall_model_reading import interior_recall_item, supports_life_reading
    from .schema_core import canonicalize_json_value
    from .visible_recall_sources import _hash, _json

    base = table.as_dict()
    pin, actor = base['pin'], base['subjects']['companion_actor_ref']
    entries, used, seen = [], [], set()
    for audit in audits:
        if not supports_life_reading(audit.index_version):
            continue
        cursor = audit.evaluated_cursor or audit.index_cursor
        if (audit.trigger_ref != pin['trigger_ref'] or audit.query.actor_ref != actor
            or audit.reuse_contract != 'same_context'
            or (cursor.world_revision, cursor.deliberation_revision, cursor.ledger_sequence)
            != (pin['world_revision'], pin['deliberation_revision'], pin['ledger_sequence'])):
            raise ValueError('recalled life differs from original review pin')
        material = materials.get('selected_recall' if audit.mode == 'character_pull' else 'automatic_prefetch', {})
        shown = (material.get('content', {}) if audit.mode == 'character_pull' else material).get('items', [])
        included = False
        for hit in audit.hits:
            document = hit.document
            if document.settled_life is None:
                continue
            validate_recalled_life(document)
            if document.actor_ref != actor or document.settled_life.settled_at > audit.query.at:
                raise ValueError('recalled life owner or settlement time is invalid')
            reading = interior_recall_item(document, index_version=audit.index_version)
            view = json.loads(compact_model_facing_context(_json(canonicalize_json_value({
                'slices': {}, 'inner_life_snapshot': {'materials': {'reading': reading}},
            }))))['inner_life_snapshot']['materials']['reading']
            if view not in shown:
                continue
            included = True
            value = document.settled_life.model_dump(mode='json')
            identity = _hash(value)
            if identity in seen:
                continue
            seen.add(identity)
            bindings = tuple(ResolvedSourceBinding.model_validate(b.model_dump()) for b in document.source_bindings)
            entries.append({
                'kind': 'pinned_context_item', 'lane': 'world_life', 'actor_ref': actor,
                'privacy_class': document.privacy_class, 'availability': 'available',
                'source_refs': sorted({document.settled_life.occurrence_id, *document.source_refs}),
                'item': {'item_ref': document.settled_life.occurrence_id,
                         'privacy_class': document.privacy_class,
                         'source_hash': source_bindings_hash(bindings), 'value_hash': identity,
                         'source_bindings': [b.model_dump(mode='json') for b in bindings], 'value': value},
            })
        if included:
            used.append(audit)
    return entries, tuple(used)
