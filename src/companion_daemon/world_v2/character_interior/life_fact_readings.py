"""Prepare predicate-bound Fact quotations without granting the whole report."""
from __future__ import annotations

from copy import deepcopy

from ..context_capsule import FactRecallItem, HistoricalFactRecallItem
from ..fact_observation_value_lookup import resolve_observation_fact_value
from ..present_prompt import appraisal_material_rows
from ..visible_source_closure_protocol import _review_material
from .life_source_origin import canonical


EXACT_VALUE_REVIEW_CONTRACT = 'life-source-review.11'
EXACT_VALUE_REVIEW_CONTRACTS = {EXACT_VALUE_REVIEW_CONTRACT, 'life-source-review.12', 'life-source-review.13', 'life-source-review.14', 'life-source-review.15', 'life-source-review.16', 'life-source-review.17', 'life-source-review.18', 'life-source-review.19', 'life-source-review.20', 'life-source-review.21'}
DISPLAY_CONTRACT = 'life-fact-value-display.1'


def fact_value_reading(row, *, rendered, exact_value_display=False):
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
    descriptor = {
        'source_family': 'accepted_fact_value', 'item_ref': parent,
        'pointer': '/item/value/source_excerpt', 'value': value['source_excerpt'],
        'fact_context': {field: value[field] for field in fields if field != 'source_excerpt'},
        'value_binding': fact.accepted_value_binding.model_dump(mode='json'),
        'shown_scopes': [scope], 'permissions': [],
        'value_selection_permissions': [[
            'historical_accepted_fact' if fact.status == 'historical' else 'accepted_fact', 'source_owner',
        ]],
    }
    if exact_value_display:
        accepted_value = resolve_observation_fact_value(
            binding=fact.accepted_value_binding, source_excerpt=fact.source_excerpt)
        descriptor.update(
            display_contract=DISPLAY_CONTRACT,
            pointer='/accepted_fact/accepted_value',
            value=accepted_value, accepted_value=accepted_value,
            observation_event_ref=fact.observation_event_ref,
        )
    return descriptor, None


def fact_snapshot_display(snapshot, readings):
    """Project the Fact lane for review; original author bytes stay in its pin.

    Only the verified descriptor supplies accepted value bytes. A separate
    dialogue reading can still authorize the independently presented utterance.
    Unqualified Facts retain coordinates, never an unbound report as their value.
    """
    shown = deepcopy(snapshot)
    facts = {row['item_ref']: row for row in readings['readings']
             if row.get('display_contract') == DISPLAY_CONTRACT}
    for item in shown.get('materials', {}).get('relevant_facts', ()):
        if not isinstance(item, dict):
            raise ValueError('Life Fact display requires typed source entries')
        item.pop('source_excerpt', None)
        reading = facts.get(item.get('source_ref'))
        if reading is not None:
            item.update(accepted_value=reading['accepted_value'],
                        fact_context=deepcopy(reading['fact_context']),
                        display_contract=DISPLAY_CONTRACT)
    for memory in shown.get('materials', {}).get('remembered_material', ()):
        for excerpt in memory.get('source_excerpts', ()):
            if excerpt.get('source_kind') != 'fact':
                continue
            excerpt.pop('text', None)
            reading = facts.get(excerpt.get('source_id'))
            if reading is not None:
                excerpt.update(text=reading['accepted_value'],
                               fact_context=deepcopy(reading['fact_context']),
                               display_contract=DISPLAY_CONTRACT)
    # The compact appraisal excerpt column is audit context, not one of this
    # reader's permitted appraisal meanings. Keep meanings and coordinates;
    # separately qualified dialogue readers retain their full utterance text.
    appraisals = shown.get('materials', {}).get('appraisals')
    if isinstance(appraisals, dict) and 'excerpts' in appraisals.get('columns', ()):
        index = appraisals['columns'].index('excerpts')
        for row in appraisal_material_rows(appraisals):
            # Compact rows may omit the optional trailing excerpts cell.
            # An already absent audit field needs no replacement or padding.
            if index < len(row):
                row[index] = []
    return {'contract': DISPLAY_CONTRACT, 'snapshot': shown,
            'original_author_snapshot_is_preserved_in_source_view': True}
