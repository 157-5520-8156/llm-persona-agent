"""Remove proof coordinates from subjective-history model views only.

The canonical materials and every catalog reading remain in the receipt. This
view does not summarize prose, retrieve a subset, or change source permissions.
"""
from copy import deepcopy
from .visible_source_witness_experiment import _reading

CONTRACT = 'subjective-proof-elision.1'
_PROOF_KEYS = frozenset({
    'evidence_refs', 'origin', 'appraisal_refs', 'appraisal_id', 'hypothesis_id',
    'source_cluster_ref', 'contradiction_refs', 'accepted_change_id',
    'accepted_transition_id', 'accepted_entity_revision', 'entity_revision',
})


def present_material_cards(cards):
    result = deepcopy(cards)

    def prune(node):
        if isinstance(node, dict):
            return {key: prune(value) for key, value in node.items() if key not in _PROOF_KEYS}
        if isinstance(node, list):
            return [prune(value) for value in node]
        return node

    for original, shown in zip(cards, result, strict=True):
        material = shown['material']
        if material.get('authority') != 'accepted_subjective_history_not_external_fact':
            continue
        item = material.get('item')
        if not isinstance(item, dict) or 'value' not in item:
            continue
        item['value'] = prune(item['value'])
        # A new catalog may make an old coordinate readable. Fail back to the
        # original complete card instead of hiding a field the reader can cite.
        try:
            intact = all(_reading(material, r['field']) == _reading(original['material'], r['field'])
                         for r in shown['readings'])
        except (ValueError, KeyError, IndexError, TypeError):
            intact = False
        if not intact:
            shown['material'] = deepcopy(original['material'])
    return result
