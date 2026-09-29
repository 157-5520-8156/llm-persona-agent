"""Life .13 presentation and narrow lifecycle reader; earlier pins stay frozen."""
from __future__ import annotations

from dataclasses import replace

from ..background_context_profile import background_context_profile_for_purpose
from ..context_capsule import ResolvedSourceBinding, source_bindings_hash
from ..world_life_context import ActivityLifecycleStateContextItem
from .life_source_origin import canonical, digest

CONTRACT = 'life-source-review.13'
STATE_REVIEW_CONTRACTS = frozenset({'life-source-review.13', 'life-source-review.14',
    'life-source-review.15', 'life-source-review.16', 'life-source-review.17', 'life-source-review.18', 'life-source-review.19', 'life-source-review.20', 'life-source-review.21'})
SCOPE = 'activity_lifecycle_states'
FIELDS = ('plan_id', 'plan_entity_revision', 'owner_actor_ref', 'status', 'transitioned_at', 'lifecycle_scope')


COMPACT_INVENTORY_CONTRACTS = frozenset({'life-source-review.16', 'life-source-review.17', 'life-source-review.18', 'life-source-review.19', 'life-source-review.20', 'life-source-review.21'})


def life_source_profile(review_contract):
    profile = background_context_profile_for_purpose('world_stimulus_appraisal')
    if review_contract in STATE_REVIEW_CONTRACTS:
        profile = replace(
            profile,
            profile_id=(
                'stimulus_appraisal.lifecycle.2'
                if review_contract in COMPACT_INVENTORY_CONTRACTS
                else 'stimulus_appraisal.lifecycle.1'
            ),
            snapshot_material_keys=(*profile.snapshot_material_keys, SCOPE),
        )
    if review_contract is not None and review_contract not in COMPACT_INVENTORY_CONTRACTS:
        # Every contract that predates .16 keeps the inventory it was written
        # with.  ``None`` is the unreviewed production lane, which is never
        # pinned, so it follows the registered profile.
        profile = replace(profile, compact_source_inventory=False)
    return profile


def lifecycle_state_reading(row, *, rendered, snapshot):
    material = row['review_material']
    item = material.get('item', {})
    value = item.get('value', {})
    if material.get('lane') != 'world_life' or value.get('context_kind') != 'activity_lifecycle_state':
        return None, 'no_qualified_field_reader'
    state = ActivityLifecycleStateContextItem.model_validate_json(canonical(value), strict=True)
    bindings = tuple(ResolvedSourceBinding.model_validate_json(canonical(b), strict=True)
                     for b in item.get('source_bindings', ()))
    source = state.source_bindings[0]
    if (snapshot.cursor is None or snapshot.logical_time is None
        or state.owner_actor_ref != snapshot.actor_ref
        or state.transitioned_at > snapshot.logical_time
        or source.authority_world_revision > snapshot.cursor.world_revision
        or material.get('privacy_class') != state.privacy_class
        or item.get('privacy_class') != state.privacy_class
        or state.privacy_class == 'withhold'
        or item.get('item_ref') != state.activity_event_ref
        or item.get('value_hash') != digest(canonical(value))
        or item.get('source_hash') != source_bindings_hash(bindings)
        or len(bindings) != 1):
        raise ValueError('Life lifecycle source differs from its original actor, time or binding')
    binding = bindings[0]
    if (binding.source_kind != 'committed_event' or binding.ref != state.activity_event_ref
        or binding.authority_type != state.event_type
        or binding.source_world_revision != source.authority_world_revision
        or binding.immutable_hash != source.authority_payload_hash):
        raise ValueError('Life lifecycle source lacks its exact accepted event')
    if row['source_ref'] != state.activity_event_ref:
        return None, 'not_the_lifecycle_event_reference'
    if not any(i.get('source_ref') == state.activity_event_ref and i.get('scope') == SCOPE
               for i in rendered.get('source_inventory', ())):
        return None, 'source_not_in_presented_family'
    shown = [i for i in rendered.get('materials', {}).get(SCOPE, ())
             if i.get('source_ref') == state.activity_event_ref]
    exact = {field: value[field] for field in FIELDS}
    if len(shown) != 1 or any(shown[0].get(field) != field_value for field, field_value in exact.items()):
        return None, 'exact_field_not_presented'
    return {'source_family': 'activity_lifecycle_state', 'item_ref': state.activity_event_ref,
            'pointer': '/item/value', 'value': exact, 'shown_scopes': [SCOPE],
            'source_owner_ref': state.owner_actor_ref,
            'permissions': [['activity_lifecycle', 'companion']]}, None
