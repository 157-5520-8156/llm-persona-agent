"""Life field permissions preserve source scope and actual presentation."""
from copy import deepcopy
import json

import pytest

from companion_daemon.world_v2.character_interior.core import _restore_prepared_turn
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_readings import (
    PreparedLifeSourceReadings, prepare_life_source_readings,
)
from test_life_source_view import _prepared


@pytest.mark.asyncio
async def test_actual_environment_can_support_rain_but_not_a_personal_walk(tmp_path, monkeypatch):
    payload, _, _ = await _prepared(tmp_path, monkeypatch)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')
    view = result.life_source_view
    prepared = prepare_life_source_readings(view=view, snapshot=snapshot)
    data = prepared.as_dict()
    assert len(data['readings']) == 1
    reading = data['readings'][0]
    assert reading['value'] == '一阵短雨已经停了。'
    assert reading['pointer'].endswith('/environment/text')
    # Current compact views may present one copy; duplicated diary display
    # never grants a second source or wider permission.
    assert 'recent_self_experiences' in reading['shown_scopes']
    assert set(reading['shown_scopes']) <= {'recent_self_experiences', 'week_diary'}
    assert ['environment', 'general'] in reading['permissions']
    assert all(role != 'companion' for _, role in reading['permissions'])
    assert prepared.require_reading(reading_id=reading['reading_id'], claim_scope='environment', subject_role='general', view=view, snapshot=snapshot) == reading
    for scope in ('external_fact', 'accepted_intention', 'subjective_history', 'utterance_record'):
        with pytest.raises(ValueError, match='field permission'):
            prepared.require_reading(reading_id=reading['reading_id'], claim_scope=scope, subject_role='companion', view=view, snapshot=snapshot)
    assert data['write_authority'] is data['complete_source_coverage'] is False
    assert data['semantic_coverage'] == 'not_assessed'
    assert data['excluded']
    assert all(item['reason'] == 'no_qualified_field_reader' for item in data['excluded'])


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['value', 'permissions', 'shown_scopes', 'source_ref_indexes'])
async def test_recomputed_reading_packet_cannot_replace_the_actual_field(tmp_path, monkeypatch, fault):
    payload, _, _ = await _prepared(tmp_path, monkeypatch)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')
    view = result.life_source_view
    prepared = prepare_life_source_readings(view=view, snapshot=snapshot)
    changed = prepared.as_dict()
    target = changed['readings'][0]
    target[fault] = {'value': '雨停后我走完了一圈。', 'permissions': [['external_fact', 'companion']],
                     'shown_scopes': ['remembered_material'], 'source_ref_indexes': [0]}[fault]
    with pytest.raises(ValueError, match='pinned source/view'):
        PreparedLifeSourceReadings(canonical(changed)).verify(view=view, snapshot=snapshot)


@pytest.mark.asyncio
async def test_unknown_fact_scalars_do_not_acquire_default_life_permissions(tmp_path):
    from test_visible_selected_source_context import _sources
    from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
    from companion_daemon.world_v2.character_interior.life_source_readings import _fields
    async with _sources(tmp_path) as case:
        rows = compile_selected_source_table(capsule=case.capsule).source_references()
        facts = [r for r in rows if r['review_material'].get('lane') == 'relevant_facts']
        from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
        chat_facts = [r for r in compile_visible_source_table(request=case.request, capsule=case.capsule).source_references() if r['review_material'].get('lane') == 'relevant_facts']
        assert facts and any(r['support_eligibility'] == 'eligible' for r in chat_facts)
        assert all(_fields(row) is None for row in chat_facts)
        assert all(_fields(row) is None for row in facts)
        reports = [r for r in rows if r['review_material'].get('authority') == 'counterpart_report_only'
                   and _fields(r) is not None]
        assert reports
        for row in reports:
            _, _, fields = _fields(row)
            assert set(fields) == {'/item/value/text'}
            assert fields['/item/value/text'] == [['utterance_record', 'source_owner'], ['report_uptake', 'source_owner']]
            assert row['support_subject_ref'] == case.observation.actor
            corrupt = deepcopy(row)
            corrupt['review_material']['item']['value']['text'] += ' 已发生的动作。'
            assert _fields(corrupt) is None


def _presented_snapshot(capsule):
    from companion_daemon.world_v2.character_interior.snapshot_compiler import compile_inner_life_snapshot, source_envelopes_from_capsule
    from companion_daemon.world_v2.character_interior.life_source_view import _expected_view
    snapshot = compile_inner_life_snapshot(json.loads(capsule.model_content_json), source_envelopes=source_envelopes_from_capsule(capsule))
    return _expected_view(snapshot)


def test_accepted_subjective_readings_preserve_meaning_without_external_action_authority():
    from test_visible_subjective_source import accepted_sources
    from companion_daemon.world_v2.character_interior.life_source_readings import _fields, _presented_items, _shown_exact_field
    from companion_daemon.world_v2.visible_source_witness_experiment import _reading
    _, capsule, table = accepted_sources()
    rendered = _presented_snapshot(capsule)
    seen = set()
    for row in table.source_references():
        specification = _fields(row)
        if specification is None or specification[0] != 'subjective_history':
            continue
        _, scopes, fields = specification
        item_ref = row['review_material']['item']['item_ref']
        for pointer, permissions in fields.items():
            assert permissions == [['subjective_history', 'companion']]
            value, _ = _reading(row['review_material'], pointer)
            shown = [item for scope in scopes for item in _presented_items(rendered, scope=scope, source_ref=item_ref)]
            assert any(_shown_exact_field(item, pointer, value, 'subjective_history') for item in shown)
            seen.add(row['review_material']['lane'])
    assert seen == {'appraisals', 'affect_episodes'}


@pytest.mark.asyncio
async def test_activity_intention_does_not_become_a_lifecycle_or_completed_action_field():
    from current_activity_fixture import accepted_current_activity
    from test_current_activity_context import current_context
    from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
    from companion_daemon.world_v2.character_interior.life_source_readings import _fields, _presented_items
    ledger, store, _, _ = await accepted_current_activity()
    capsule, _, _ = current_context(ledger, store)
    rendered = _presented_snapshot(capsule)
    rows = compile_selected_source_table(capsule=capsule).source_references()
    qualified = [r for r in rows if _fields(r) is not None and _fields(r)[0] == 'activity']
    assert qualified
    for row in qualified:
        _, scopes, fields = _fields(row)
        assert fields['/item/value/accepted_intention/text'] == [['accepted_intention', 'companion']]
        assert fields['/item/value/status'] == [['activity_lifecycle', 'companion']]
        assert all(scope != 'external_fact' for permissions in fields.values() for scope, _ in permissions)
        assert any(_presented_items(rendered, scope=scope, source_ref=row['review_material']['item']['item_ref']) for scope in scopes)


def test_retained_prehistory_allows_narrative_and_historical_people_only(tmp_path):
    from test_character_prehistory import started_ledger
    from test_prehistory_memory_source import _install, _choice
    from test_prehistory_chat_context import _capsule
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
    from companion_daemon.world_v2.character_interior.life_source_readings import _fields, _presented_items, _shown_exact_field
    ledger = started_ledger(tmp_path / 'history.sqlite')
    try:
        row = _install(ledger)
        binding = prehistory_memory_binding(row)
        pending = _choice(ledger, binding)
        _choice(ledger, binding, before=pending, status='active')
        capsule = _capsule(ledger)
        rendered = _presented_snapshot(capsule)
        rows = compile_selected_source_table(capsule=capsule).source_references()
        historical = [r for r in rows if _fields(r) is not None and _fields(r)[0] == 'retained_prehistory']
        assert historical
        for source in historical:
            _, scopes, fields = _fields(source)
            assert set(fields) == {'/item/value/source_excerpts/0/text'}
            assert ['external_fact', 'other'] in fields['/item/value/source_excerpts/0/text']
            assert ['external_fact', 'counterpart'] not in fields['/item/value/source_excerpts/0/text']
            assert any(_shown_exact_field(item, '/item/value/source_excerpts/0/text', row.record.statement, 'retained_prehistory') for scope in scopes
                       for item in _presented_items(rendered, scope=scope, source_ref=source['review_material']['item']['item_ref']))
    finally:
        ledger.close()


def test_identical_text_in_metadata_or_another_source_field_is_not_presentation_proof():
    from companion_daemon.world_v2.character_interior.life_source_readings import _shown_exact_field
    assert not _shown_exact_field({'source_ref': 'same bytes'}, '/item/value/text', 'same bytes', 'companion_utterance')
    environment = {'world_consequence': {'environment': {'text': 'same bytes'}}}
    assert _shown_exact_field(environment, '/item/value/content/world_consequence/environment/text', 'same bytes', 'settled_life')
    assert not _shown_exact_field(environment, '/item/value/content/world_consequence/authorized_attempt_result/text', 'same bytes', 'settled_life')
