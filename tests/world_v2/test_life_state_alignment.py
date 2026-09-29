"""State-source coverage without expanding state into physical actions."""
from copy import deepcopy

import pytest

from companion_daemon.world_v2.character_interior.life_situation_readings import situation_plan_reading
from companion_daemon.world_v2.character_interior.life_source_view import _expected_view
from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
from test_life_lifecycle_state_readings import request_for
from current_activity_fixture import accepted_current_activity


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', [None, 'owner', 'pin', 'binding', 'shown', 'inventory'])
async def test_plan_state_is_exact_and_never_an_action_result(fault):
    ledger, store, _, _ = await accepted_current_activity(status='paused')
    capsule, request = request_for(ledger, store)
    snapshot = request.snapshot
    row = next(r for r in compile_selected_source_table(capsule=capsule).source_references()
               if r['review_material'].get('lane') == 'current_situation'
               and r['source_ref'] == snapshot.actor_ref)
    shown = _expected_view(snapshot, 'life-source-review.19')
    if fault == 'owner':
        snapshot = snapshot.model_copy(update={'actor_ref': 'actor:other'})
    elif fault == 'pin':
        snapshot = snapshot.model_copy(update={'cursor': snapshot.cursor.model_copy(
            update={'world_revision': snapshot.cursor.world_revision + 1})})
    elif fault == 'binding':
        row['review_material']['item']['source_hash'] = 'f' * 64
    elif fault == 'shown':
        shown['materials']['situation'][0]['plan_relation']['relation'] = 'active'
    elif fault == 'inventory':
        shown['source_inventory'] = []
    if fault in {'owner', 'pin', 'binding'}:
        with pytest.raises(ValueError):
            situation_plan_reading(row, rendered=shown, snapshot=snapshot)
        return
    reading, reason = situation_plan_reading(row, rendered=shown, snapshot=snapshot)
    if fault:
        assert reading is None and reason
    else:
        assert reason is None and reading['value']['relation'] == 'paused'
        assert reading['permissions'] == [['activity_lifecycle', 'companion']]
        assert 'intention' not in reading['value']


def test_more_appraisal_provenance_does_not_erase_unchanged_affect_state():
    from test_life_affect_history_readings import _accepted
    from companion_daemon.world_v2.character_interior.life_affect_history_readings import affect_history_reading
    from companion_daemon.world_v2.present_prompt import affect_material_entries, cache_stable_affect
    _, snapshot, rendered, rows = _accepted()
    shown = affect_material_entries(rendered['materials']['affect'])
    extra = deepcopy(shown[0]['components'][0]['appraisal_refs'][0])
    extra['appraisal_id'] = 'appraisal:additional-provenance-not-a-state-source'
    shown[0]['components'][0]['appraisal_refs'].append(extra)
    rendered['materials']['affect'] = cache_stable_affect(shown)
    assert affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot)[0] is None
    reading, reason = affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot, state_only=True)
    assert reason is None
    assert reading['permissions'] == [['subjective_history', 'companion']]
    assert 'appraisal_refs' not in reading['value']['components'][0]
    shown[0]['components'][0]['intensity_bp'] += 1
    rendered['materials']['affect'] = cache_stable_affect(shown)
    assert affect_history_reading(rows[0], rendered=rendered, snapshot=snapshot, state_only=True)[0] is None
