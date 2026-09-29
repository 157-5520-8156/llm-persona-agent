"""Read only a source-bound current plan state, never an accomplished action."""
from ..context_capsule import ResolvedSourceBinding, source_bindings_hash
from ..situation_compiler import SituationProjection
from .life_source_origin import canonical, digest


def situation_plan_reading(row, *, rendered, snapshot):
    material = row['review_material']
    item = material.get('item', {})
    if material.get('lane') != 'current_situation':
        return None, 'no_qualified_field_reader'
    value = item.get('value')
    state = SituationProjection.model_validate_json(canonical(value), strict=True)
    bindings = tuple(ResolvedSourceBinding.model_validate_json(canonical(b), strict=True)
                     for b in item.get('source_bindings', ()))
    if (snapshot.cursor is None or state.world_id != snapshot.world_id
        or state.actor_ref != snapshot.actor_ref or state.logical_time != snapshot.logical_time
        or state.compiled_at_world_revision != snapshot.cursor.world_revision
        or item.get('item_ref') != snapshot.actor_ref
        or material.get('privacy_class') == 'withhold' or item.get('privacy_class') == 'withhold'
        or item.get('value_hash') != digest(canonical(value))
        or item.get('source_hash') != source_bindings_hash(bindings)):
        raise ValueError('Life situation differs from its original owner, time or source binding')
    # Situation is a compiler-owned projection in the already verified Capsule.
    # Require the complete source revision set, not an arbitrary matching hash.
    expected = {(s.event_ref, s.source_world_revision, s.payload_hash, 'situation_source:' + s.domain)
                for s in state.source_revisions}
    actual = {(b.ref, b.source_world_revision, b.immutable_hash, b.authority_type)
              for b in bindings if b.source_kind == 'committed_event'}
    if not expected or actual != expected or len(bindings) != len(expected):
        return None, 'missing_situation_revision_sources'
    if row['source_ref'] != snapshot.actor_ref:
        return None, 'not_the_situation_item_reference'
    relation = state.plan_relation
    if relation.availability != 'available' or relation.privacy_class == 'withhold':
        return None, 'plan_state_unavailable'
    if not any(i.get('source_ref') == snapshot.actor_ref and i.get('scope') == 'situation'
               for i in rendered.get('source_inventory', ())):
        return None, 'source_not_in_presented_family'
    shown = [i for i in rendered.get('materials', {}).get('situation', ())
             if i.get('source_ref') == snapshot.actor_ref]
    exact = value['plan_relation']
    if len(shown) != 1 or canonical(shown[0].get('plan_relation')) != canonical(exact):
        return None, 'exact_field_not_presented'
    return {'source_family': 'situation_plan_state', 'item_ref': snapshot.actor_ref,
            'pointer': '/item/value/plan_relation', 'value': exact, 'shown_scopes': ['situation'],
            'source_owner_ref': snapshot.actor_ref,
            'permissions': [['activity_lifecycle', 'companion']],
            'scope': 'current_plan_relation_only_not_attempt_success_or_physical_action'}, None
