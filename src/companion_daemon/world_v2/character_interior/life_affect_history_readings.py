"""Read an accepted Affect record as history, never as an external event.

The caller verifies LifeSourceView against the original snapshot first. This
reader then joins its typed accepted state to the exact author-visible Affect
entry. It does not infer a feeling's cause, choose an intensity threshold, or
turn this invocation's newly authored feeling into earlier evidence.
"""
from __future__ import annotations

from datetime import datetime

from ..present_prompt import affect_material_entries
from ..schemas import AffectEpisodeProjection
from ..visible_source_closure_protocol import _eligible_reference
from .affect_model_view import compact_affect_for_model_view
from .life_source_origin import canonical
from .snapshot_compiler import _affect_entry


def affect_history_reading(row, *, rendered, snapshot, state_only=False):
    """Return one exact structured history reading or a closed exclusion.

    Numeric magnitudes retain their original types and zero values. Permission
    means the recorded state is usable evidence; candidate entailment, temporal
    reference and words such as 'strong' remain the semantic reviewer's job.
    """
    material = row.get('review_material')
    if (not isinstance(material, dict) or material.get('lane') != 'affect_episodes'
        or 'subjective_history_support' not in row or not _eligible_reference(row)):
        return None, 'no_qualified_field_reader'
    try:
        scope = material['scope']
        if (snapshot.cursor is None or rendered.get('world_id') != snapshot.world_id
            or rendered.get('actor_ref') != snapshot.actor_ref
            or rendered.get('cursor') != {
                **snapshot.cursor.model_dump(mode='json'), 'logical_time': snapshot.logical_time.isoformat(),
            }
            or row.get('support_subject_role') != 'companion'
            or row.get('support_subject_ref') != snapshot.actor_ref
            or material.get('actor_ref') != snapshot.actor_ref
            or scope['world_revision'] != snapshot.cursor.world_revision
            or datetime.fromisoformat(scope['logical_at']) != snapshot.logical_time):
            return None, 'affect_history_owner_or_pin_mismatch'
        value = material['item']['value']
        episode = AffectEpisodeProjection.model_validate_json(canonical(value), strict=True)
        if any(at > snapshot.logical_time for component in episode.components for at in (
            component.opened_at, component.last_updated_at,
            component.last_stimulus_at, component.decay_anchor_at,
        )):
            return None, 'affect_history_owner_or_pin_mismatch'
        item_ref = episode.episode_id
        if not any(item.get('source_ref') == item_ref and item.get('scope') == 'affect'
                   for item in rendered.get('source_inventory', ())):
            return None, 'source_not_in_presented_family'
        shown = [item for item in affect_material_entries(rendered.get('materials', {}).get('affect'))
                 if item.get('source_ref') == item_ref]
        if len(shown) != 1:
            return None, 'ambiguous_or_missing_presented_affect'
        # Reuse the installed presentation, including removal of transaction
        # coordinates and reversal of appraisal-reference table packing.
        expected = compact_affect_for_model_view([
            _affect_entry({'source_ref': item_ref, 'value': value}),
        ])[0]
        displayed = shown[0]
        if state_only:
            # The living view can include more appraisal provenance than the
            # selected Capsule. Compare every state field exactly, and grant
            # no permission for either provenance list or its implied causes.
            def state_fields(entry):
                return {**entry, 'components': [
                    {key: value for key, value in component.items() if key != 'appraisal_refs'}
                    for component in entry['components']]}
            displayed, expected = state_fields(displayed), state_fields(expected)
        if canonical(displayed) != canonical(expected):
            return None, 'exact_field_not_presented'
        # Only displayed state/history participates. Decay controls remain in
        # typed validation above, not in the model's support surface. Appraisal
        # identities are provenance, not a description of an external cause.
        history = {key: displayed[key] for key in (
            'episode_id', 'entity_revision', 'status', 'opened_at', 'updated_at',
        ) if key in displayed}
        history['components'] = [{key: component[key] for key in (
            'component_id', 'dimension', 'source_cluster_ref', 'appraisal_refs',
            'intensity_bp', 'opened_at', 'last_stimulus_at', 'last_updated_at',
        ) if key in component} for component in displayed['components']]
        return {
            'source_family': 'affect_history', 'item_ref': item_ref,
            'pointer': '/item/value', 'value': history, 'shown_scopes': ['affect'],
            'logical_at': scope['logical_at'],
            'permissions': [['subjective_history', 'companion']],
            'history_scope': 'accepted_subjective_state_only_not_external_causes_actions_or_personality',
            'intensity_scope': 'intensity_bp_is_the_pinned_read_time_value_not_the_value_at_opening',
        }, None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None, 'invalid_affect_history_or_presentation'
