"""Typed accepted Affect history remains exact, visible and owner/pin-bound."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from companion_daemon.world_v2.character_interior.life_affect_history_readings import affect_history_reading
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_view import _expected_view
from companion_daemon.world_v2.character_interior.snapshot_compiler import (
    compile_inner_life_snapshot, source_envelopes_from_capsule,
)
from companion_daemon.world_v2.present_prompt import affect_material_entries, cache_stable_affect
from test_visible_subjective_source import accepted_sources


def _accepted():
    runtime, capsule, table = accepted_sources()
    snapshot = compile_inner_life_snapshot(
        json.loads(capsule.model_content_json), source_envelopes=source_envelopes_from_capsule(capsule),
    )
    rows = [row for row in table.source_references()
            if row['review_material'].get('lane') == 'affect_episodes'
            and row.get('support_eligibility') == 'eligible']
    assert len(rows) == 2  # The episode and its accepted event are aliases, not two histories.
    return runtime, snapshot, _expected_view(snapshot), rows


@pytest.fixture
def accepted():
    return _accepted()


def test_actual_accepted_affect_has_exact_structured_history_and_no_event_grants(accepted):
    runtime, snapshot, rendered, rows = accepted
    before = runtime.ledger.export_replay_evidence()
    readings = [affect_history_reading(row, rendered=rendered, snapshot=snapshot) for row in rows]
    assert all(reason is None for _, reason in readings)
    reading = readings[0][0]
    assert readings[1][0] == reading
    assert reading['source_family'] == 'affect_history'
    assert reading['permissions'] == [['subjective_history', 'companion']]
    assert reading['shown_scopes'] == ['affect']
    shown, = affect_material_entries(rendered['materials']['affect'])
    assert reading['value']['episode_id'] == shown['source_ref'] == reading['item_ref']
    assert reading['value']['opened_at'] == shown['opened_at']
    assert reading['value']['status'] == 'active'
    component, = reading['value']['components']
    assert type(component['intensity_bp']) is int
    assert component['dimension'] == 'hurt'
    assert component['appraisal_refs'] == shown['components'][0]['appraisal_refs']
    assert 'accepted_change_id' not in component['appraisal_refs'][0]
    assert not {'decay_profile', 'decay_anchor_at', 'decay_anchor_intensity_bp',
                'residue_bp', 'decay_not_before'}.intersection(component)
    assert runtime.ledger.export_replay_evidence() == before


@pytest.mark.parametrize('fault', ['actor', 'row_owner', 'counterpart', 'wrong_world', 'cursor',
                                 'old_snapshot', 'old_source_pin', 'future_component',
                                 'wrong_hash', 'wrong_origin', 'wrong_binding', 'withheld'])
def test_other_owner_changed_source_or_wrong_pin_has_no_history(accepted, fault):
    _, snapshot, rendered, rows = accepted
    row = deepcopy(rows[0])
    rendered = deepcopy(rendered)
    material = row['review_material']
    if fault == 'actor':
        rendered['actor_ref'] = 'actor:other'
    elif fault == 'row_owner':
        row['support_subject_ref'] = 'actor:other'
    elif fault == 'counterpart':
        row['support_subject_role'] = 'counterpart'
    elif fault == 'wrong_world':
        rendered['world_id'] = 'world:other'
    elif fault == 'cursor':
        rendered['cursor']['ledger_sequence'] -= 1
    elif fault == 'old_snapshot':
        snapshot = snapshot.model_copy(update={'logical_time': snapshot.logical_time - timedelta(seconds=1)})
    elif fault == 'old_source_pin':
        material['scope']['world_revision'] -= 1
    elif fault == 'future_component':
        material['item']['value']['components'][0]['last_updated_at'] = (
            snapshot.logical_time + timedelta(seconds=1)).isoformat()
    elif fault == 'wrong_hash':
        material['item']['value_hash'] = 'f' * 64
    elif fault == 'wrong_origin':
        material['item']['value']['origin']['accepted_event_ref'] = 'event:another'
    elif fault == 'wrong_binding':
        material['item']['source_bindings'][0]['authority_type'] = 'ObservationRecorded'
    else:
        material['privacy_class'] = 'withhold'
    reading, reason = affect_history_reading(row, rendered=rendered, snapshot=snapshot)
    assert reading is None and reason


@pytest.mark.parametrize('fault', ['missing_inventory', 'wrong_family', 'missing_episode', 'duplicate_episode',
                                 'unshown_component', 'missing_dimension', 'missing_intensity', 'changed_intensity',
                                 'numeric_type', 'missing_time', 'changed_time', 'changed_appraisal',
                                 'metadata_substitute', 'duplicate_component'])
def test_history_needs_exact_fields_in_the_actual_author_presentation(accepted, fault):
    _, snapshot, rendered, rows = accepted
    rendered = deepcopy(rendered)
    shown, = affect_material_entries(rendered['materials']['affect'])
    rendered['materials']['affect'] = [shown]
    component = shown['components'][0]
    if fault == 'missing_inventory':
        rendered['source_inventory'] = [item for item in rendered['source_inventory'] if item['scope'] != 'affect']
    elif fault == 'wrong_family':
        rendered['materials']['metadata'] = rendered['materials'].pop('affect')
    elif fault == 'missing_episode':
        shown['source_ref'] = 'affect:another'
    elif fault == 'duplicate_episode':
        rendered['materials']['affect'].append(deepcopy(shown))
    elif fault == 'unshown_component':
        shown['components'] = []
    elif fault == 'missing_dimension':
        del component['dimension']
    elif fault == 'missing_intensity':
        del component['intensity_bp']
    elif fault == 'changed_intensity':
        component['intensity_bp'] += 1
    elif fault == 'numeric_type':
        component['intensity_bp'] = float(component['intensity_bp'])
    elif fault == 'missing_time':
        del component['opened_at']
    elif fault == 'changed_time':
        component['opened_at'] = '2000-01-01T00:00:00Z'
    elif fault == 'changed_appraisal':
        component['appraisal_refs'][0]['hypothesis_id'] = 'hypothesis:not-shown'
    elif fault == 'metadata_substitute':
        shown['metadata'] = {'components': shown.pop('components')}
    else:
        shown['components'].append(deepcopy(component))
    reading, reason = affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot)
    assert reading is None and reason


def test_cache_and_reference_table_presentations_preserve_exact_history(accepted):
    _, snapshot, rendered, rows = accepted
    original = affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot)
    rendered = deepcopy(rendered)
    entries = affect_material_entries(rendered['materials']['affect'])
    # The existing presentation accepts a cache-split single tail too.
    rendered['materials']['affect'] = cache_stable_affect({
        'stable_entries': [], 'volatile_last_entry': entries[0],
    })
    assert affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot) == original


@pytest.mark.parametrize('intensity', [0, 1, 1484, 10_000])
def test_accepted_numeric_range_is_not_replaced_by_a_local_strength_threshold(monkeypatch, intensity):
    import test_affect_acceptance_runtime as fixture
    from companion_daemon.world_v2.schemas import AffectComponentProjection, affect_decay_config_digest

    def component(**values):
        profile = values['decay_profile'].model_dump(mode='python')
        profile['floor_bp'] = 0
        profile['config_digest'] = affect_decay_config_digest(**{
            key: value for key, value in profile.items() if key != 'config_digest'
        })
        return AffectComponentProjection.model_validate({
            **values, 'intensity_bp': intensity, 'decay_anchor_intensity_bp': intensity,
            'residue_bp': 0, 'decay_profile': profile,
        })

    # This changes the fixture's proposed typed component before the normal
    # acceptance runtime; it never rewrites an accepted event or its source hash.
    monkeypatch.setattr(fixture, 'AffectComponentProjection', component)
    _, snapshot, rendered, rows = _accepted()
    reading, reason = affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot)
    assert reason is None
    state, = reading['value']['components']
    assert type(state['intensity_bp']) is int and state['intensity_bp'] == intensity
    assert reading['intensity_scope'] == 'intensity_bp_is_the_pinned_read_time_value_not_the_value_at_opening'
    assert set(reading['permissions'][0]) == {'subjective_history', 'companion'}


def test_new_present_feeling_cannot_fill_missing_past_or_other_peoples_state(accepted):
    from companion_daemon.world_v2.character_interior.life_source_review import _validate_source_support

    _, snapshot, rendered, rows = accepted
    rendered = deepcopy(rendered)
    rendered['materials']['affect'] = []
    rendered['current_authorship'] = {'feeling': 'warmth already existed yesterday', 'intensity_bp': 8000}
    assert affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot)[0] is None
    _, _, original, _ = accepted
    reading, _ = affect_history_reading(rows[0], rendered=original, snapshot=snapshot)
    # This accepted fixture opened now. Its reading retains that exact onset;
    # it does not assert a state before onset or decide a prose temporal claim.
    assert datetime.fromisoformat(reading['value']['opened_at']) == snapshot.logical_time
    for claim_scope, subject_role in [('external_fact', 'companion'), ('activity_lifecycle', 'companion'),
                                      ('subjective_history', 'counterpart'), ('personality', 'companion')]:
        with pytest.raises(ValueError):
            _validate_source_support({
                'reading_id': 'test:history', 'claim_scope': claim_scope, 'subject_role': subject_role,
                'subject_ref': None, 'quoted_value': None,
            }, {'test:history': reading})
    assert 'yesterday' not in canonical(reading)
