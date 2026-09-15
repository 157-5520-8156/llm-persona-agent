"""Prepare predicate-bound Fact quotations without granting the whole report."""
from __future__ import annotations

from ..context_capsule import FactRecallItem, HistoricalFactRecallItem
from ..visible_source_closure_protocol import _review_material
from .life_source_origin import canonical


def fact_value_reading(row, *, rendered):
    material = row['review_material']
    if (material.get('lane') != 'relevant_facts'
        or material.get('authority') != 'accepted_fact_with_observation_source'
        or not _review_material(material)[1]):
        return None, 'no_qualified_field_reader'
    value = material['item']['value']
    model = HistoricalFactRecallItem if value.get('status') == 'historical' else FactRecallItem
    try:
        fact = model.model_validate_json(canonical(value), strict=True)
    except ValueError:
        return None, 'invalid_fact_value_source'
    parent = material['item']['item_ref']
    if (fact.accepted_value_binding is None or fact.privacy_class == 'withhold'
        or row.get('support_subject_ref') != fact.subject_ref
        or row['source_ref'] not in {parent, fact.accepted_fact_event_ref}):
        return None, 'no_bound_fact_value_reader'
    scope = 'relevant_facts'
    if not any(item.get('source_ref') == parent and item.get('scope') == scope
               for item in rendered.get('source_inventory', ())):
        return None, 'source_not_in_presented_family'
    entries = rendered.get('materials', {}).get(scope)
    shown = [item for item in entries if isinstance(item, dict) and item.get('source_ref') == parent] if isinstance(entries, list) else []
    fields = ['fact_id', 'subject_ref', 'predicate_code', 'source_excerpt', 'status',
              'occurred_at', 'updated_at', 'confidence_bp']
    if fact.status == 'historical':
        fields.extend(('valid_from', 'valid_to'))
    if len(shown) != 1 or any(field not in shown[0] or canonical(shown[0][field]) != canonical(value[field]) for field in fields):
        return None, 'exact_field_not_presented'
    # No field permission until the consumer supplies the original value bytes.
    # The hash belongs to the accepted Fact, not to an arbitrary report fragment.
    return {
        'source_family': 'accepted_fact_value', 'item_ref': parent,
        'pointer': '/item/value/source_excerpt', 'value': value['source_excerpt'],
        'fact_context': {field: value[field] for field in fields if field != 'source_excerpt'},
        'value_binding': fact.accepted_value_binding.model_dump(mode='json'),
        'shown_scopes': [scope], 'permissions': [],
        'value_selection_permissions': [[
            'historical_accepted_fact' if fact.status == 'historical' else 'accepted_fact', 'source_owner',
        ]],
    }, None
