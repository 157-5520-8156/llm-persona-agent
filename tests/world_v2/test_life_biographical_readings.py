"""Current biography survives the real Life presentation without event grants."""
from copy import deepcopy
import json

import pytest
import pytest_asyncio

from companion_daemon.world_v2.character_interior.core import _restore_prepared_turn
from companion_daemon.world_v2.character_interior.life_biographical_readings import biographical_reading
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_readings import prepare_life_source_readings
from test_life_source_view import _prepared


@pytest_asyncio.fixture
async def biography(tmp_path, monkeypatch):
    payload, _, provider = await _prepared(tmp_path, monkeypatch, ecology=True)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')
    view = result.life_source_view
    rendered = json.loads(provider.stimulus_requests[-1]['messages'][1]['content'])['inner_life_snapshot']
    table = json.loads(view.source_table_json)
    rows = [{**row, 'review_material': table['source_materials'][row['material_index']]['material']}
            for row in table['source_references'] if row['kind'] == 'biographical_coordinate']
    assert rows
    return view, snapshot, rendered, rows


@pytest.mark.asyncio
async def test_actual_life_biography_keeps_typed_values_and_narrow_permission(biography):
    view, snapshot, rendered, rows = biography
    prepared = prepare_life_source_readings(view=view, snapshot=snapshot)
    readings = [r for r in prepared.as_dict()['readings'] if r['source_family'] == 'biographical_coordinate']
    assert len(readings) == len(rows) >= 6
    assert any(type(r['value']) is int for r in readings)
    assert any(isinstance(r['value'], list) for r in readings)
    for reading in readings:
        assert reading['permissions'] == [['biographical_coordinate', 'companion']]
        assert reading['logical_at']
        assert prepared.require_reading(reading_id=reading['reading_id'], claim_scope='biographical_coordinate', subject_role='companion', view=view, snapshot=snapshot) == reading
    for scope, owner in [('external_fact', 'companion'), ('accepted_intention', 'companion'),
                         ('subjective_history', 'companion'), ('biographical_coordinate', 'counterpart')]:
        with pytest.raises(ValueError, match='field permission'):
            prepared.require_reading(reading_id=readings[0]['reading_id'], claim_scope=scope, subject_role=owner, view=view, snapshot=snapshot)
    assert prepared.as_dict()['write_authority'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['missing_inventory', 'wrong_scope', 'another_parent', 'missing_time',
                                  'another_time', 'same_text_in_metadata', 'numeric_type', 'duplicate_parent'])
async def test_coordinate_needs_its_actual_parent_field_time_and_type(biography, fault):
    _, _, original, rows = biography
    row = next(row for row in rows if row['review_material']['material']['field_path'] == '/age')
    shown = deepcopy(original)
    parent = row['review_material']['material']['parent_item_ref']
    entries = shown['materials']['biographical_context']
    item = next(item for item in entries if item['source_ref'] == parent)
    if fault == 'missing_inventory':
        shown['source_inventory'] = [item for item in shown['source_inventory'] if item['source_ref'] != parent]
    elif fault == 'wrong_scope':
        for entry in shown['source_inventory']:
            if entry['source_ref'] == parent:
                entry['scope'] = 'remembered_material'
    elif fault == 'another_parent':
        item['source_ref'] = 'another:biography'
    elif fault == 'missing_time':
        del item['logical_at']
    elif fault == 'another_time':
        item['logical_at'] = '2000-01-01T00:00:00Z'
    elif fault == 'same_text_in_metadata':
        item['metadata'] = {'age': item.pop('age')}
    elif fault == 'numeric_type':
        item['age'] = float(item['age'])
    else:
        entries.append(deepcopy(item))
    reading, reason = biographical_reading(row, rendered=shown)
    assert reading is None and reason


@pytest.mark.asyncio
async def test_presented_coordinate_cannot_replace_original_authority(biography):
    _, _, shown, rows = biography
    for field, value in [('value', 100), ('field_path', '/unlisted_activity'), ('logical_at', '2000-01-01T00:00:00Z')]:
        row = deepcopy(rows[0])
        row['review_material']['material'][field] = value
        assert biographical_reading(row, rendered=shown)[0] is None


@pytest.mark.parametrize('lane,identity_key', [('active_life_arcs', 'arc_id'),
                                             ('settled_biographical_coordinates', 'coordinate_ref')])
def test_coordinate_objects_use_unique_ids_not_array_positions(lane, identity_key):
    from companion_daemon.world_v2.biographical_claim_authority import biographical_coordinate_authorities
    from companion_daemon.world_v2.visible_source_closure_protocol import compact_source_reference_table

    # Reader-level fixture for container coordinates; authority is derived by
    # the public coordinate compiler, not asserted to be a settled World event.
    value = {'context_kind': 'biographical_context', 'logical_at': '2026-09-16T00:00:00Z', lane: [
        {identity_key: 'coordinate:a/with/slashes', 'context_summary': '接下来几周在书店实习。',
         'summary': '住处改为学校宿舍。', 'privacy_class': 'personal'},
        {identity_key: 'coordinate:b', 'context_summary': '参加学期项目。',
         'summary': '处于秋季学期。', 'privacy_class': 'personal'},
    ]}
    context = {'slices': {'world_life': {'availability': 'available',
                                     'items': [{'item_ref': 'biography:parent', 'value': value}]}}}
    coordinates = biographical_coordinate_authorities(context)
    assert len(coordinates) == 2
    rows = compact_source_reference_table({'subjects': {'companion_actor_ref': 'actor:companion'},
        'entries': [{'kind': 'biographical_coordinate', 'source_refs': [coordinate.source_ref],
                     'material': coordinate.evidence_material()} for coordinate in coordinates]})
    shown = {'source_inventory': [{'source_ref': 'biography:parent', 'scope': 'biographical_context'}],
             'materials': {'biographical_context': [{**deepcopy(value), 'source_ref': 'biography:parent'}]}}
    item = shown['materials']['biographical_context'][0]
    item[lane].reverse()
    for row in rows:
        reading, reason = biographical_reading(row, rendered=shown)
        assert reason is None
        assert reading['value'] == row['review_material']['material']['value']
        assert reading['permissions'] == [['biographical_coordinate', 'companion']]
    target = next(row for row in rows if row['review_material']['material']['value'][identity_key] == item[lane][0][identity_key])
    changed = deepcopy(shown)
    changed['materials']['biographical_context'][0][lane][0]['context_summary' if lane == 'active_life_arcs' else 'summary'] += ' 已经全部完成。'
    assert biographical_reading(target, rendered=changed)[0] is None
    changed = deepcopy(shown)
    changed['materials']['biographical_context'][0][lane].append(deepcopy(item[lane][0]))
    assert biographical_reading(target, rendered=changed)[0] is None
    if lane == 'settled_biographical_coordinates':
        item[lane][0]['privacy_class'] = 'withhold'
        assert biographical_reading(target, rendered=shown)[0] is None
