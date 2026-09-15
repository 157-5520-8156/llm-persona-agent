"""Read exact biography coordinates from the original and presented views.

Reusing the coordinate compiler keeps field and ID-selector semantics in one
place. A parent biography record never grants an unlisted event or activity.
"""
from __future__ import annotations

from ..biographical_claim_authority import biographical_coordinate_authorities
from ..visible_source_closure_protocol import _eligible_reference
from .life_source_origin import canonical


def biographical_reading(row, *, rendered):
    """Return a coordinate descriptor or an explicit exclusion reason.

    The caller must verify the original LifeSourceView before using this reader.
    Numeric/list/object coordinates stay typed; they are not arbitrary scalar
    leaves or inferred claims. Equality includes their time and parent identity.
    """
    material = row['review_material']
    if material.get('kind') != 'biographical_coordinate' or not _eligible_reference(row):
        return None, 'no_qualified_field_reader'
    coordinate = material['material']
    parent = coordinate['parent_item_ref']
    scope = 'biographical_context'
    if not any(item.get('source_ref') == parent and item.get('scope') == scope
               for item in rendered.get('source_inventory', ())):
        return None, 'source_not_in_presented_family'
    # This material has a known flat list layout. Do not search other fields,
    # embedded metadata or another source for identical-looking coordinates.
    entries = rendered.get('materials', {}).get(scope)
    parents = [item for item in entries if isinstance(item, dict) and item.get('source_ref') == parent] if isinstance(entries, list) else []
    if len(parents) != 1:
        return None, 'ambiguous_or_missing_presented_biography'
    shown = parents[0]
    # Require the displayed anchor itself; the compiler's context-time fallback
    # must not manufacture a coordinate absent from the actual author input.
    if shown.get('logical_at') != coordinate['logical_at']:
        return None, 'exact_field_not_presented'
    derived = biographical_coordinate_authorities({'slices': {'world_life': {
        'availability': 'available',
        'items': [{'item_ref': parent, 'value': {**shown, 'context_kind': 'biographical_context'}}],
    }}})
    same_path = [item for item in derived if item.field_path == coordinate['field_path']]
    if (len(same_path) != 1 or same_path[0].source_ref != row['source_ref']
        or canonical(same_path[0].evidence_material()) != canonical(coordinate)):
        return None, 'exact_field_not_presented'
    return {
        'source_family': 'biographical_coordinate', 'item_ref': parent,
        'pointer': '/material/value', 'value': coordinate['value'],
        'coordinate_field_path': coordinate['field_path'],
        'logical_at': coordinate['logical_at'], 'shown_scopes': [scope],
        'permissions': [['biographical_coordinate', 'companion']],
    }, None
