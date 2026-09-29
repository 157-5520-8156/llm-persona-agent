"""Versioned exact Fact display; provider judgments remain scripted offline."""
from copy import deepcopy
import json

from jsonschema import ValidationError
import pytest

from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_readings import (
    PreparedLifeSourceReadings, prepare_life_source_readings,
)
from companion_daemon.world_v2.character_interior.life_source_review import inspect_review, prepare_review
from companion_daemon.world_v2.character_interior.life_source_view import LifeSourceView
from test_character_interior_structured_role import _world_stimulus_no_change_result
from test_life_fact_readings import _fact_view
from test_visible_selected_source_context import _sources

OBSERVATION = '早上好，我今天想出去走走，顺便看看有没有适合画下来的小东西。你今天有什么打算？'
VALUE = '我今天想出去走走'
CURRENT = 'life-source-review.11'
LEGACY = 'life-source-review.10'


def real_observation(monkeypatch):
    import test_visible_selected_source_context as fixture

    record = fixture._record
    retain = fixture._retain
    monkeypatch.setattr(fixture, '_record', lambda ledger, number, _text, **kwargs:
        record(ledger, number, OBSERVATION, **kwargs))
    monkeypatch.setattr(fixture, '_retain', lambda text:
        {**retain(text), 'predicate_code': 'activity.current'})


def candidate():
    value = json.loads(_world_stimulus_no_change_result())
    value['summary'] = '用户表达了今天想出去走走的想法。'
    return canonical(value)


def response(packet, *, permission, quote=None):
    fields = []
    for field in packet['text_fields']:
        claims = []
        if field['path'] == '/summary':
            claims = [{'source_span': field['text'], 'proposition': field['text'],
                'reason': 'Offline scripted semantic verdict, not qualification.',
                'supports': [{'permission_id': permission,
                              **({'quoted_value': quote} if quote is not None else {})}],
                'verdict': 'supported'}]
        fields.append({'path': field['path'], 'reason': 'Offline fixture.',
            'created_current_states': [], 'record_bound_claims': claims, 'coverage': 'complete'})
    return canonical({'fields': fields, 'coverage': 'complete'})


@pytest.mark.asyncio
async def test_real_observation_is_not_presented_as_its_narrow_fact(tmp_path, monkeypatch):
    real_observation(monkeypatch)
    async with _sources(tmp_path, retained_value=VALUE) as case:
        original_view, snapshot = await _fact_view(case.capsule, review_contract=CURRENT)
        view = original_view
        prepared, readings = prepare_review(candidate_json=candidate(), provider_raw=candidate(),
            view=view, snapshot=snapshot)
        packet = json.loads(json.loads(prepared)['request']['messages'][1]['content'])
        fact = next(r for r in readings['readings'] if r['source_family'] == 'accepted_fact_value')
        assert len(OBSERVATION) == 39
        assert readings['contract'] == 'life-source-readings.4'
        assert fact['value'] == fact['accepted_value'] == VALUE
        assert fact['fact_context']['subject_ref'] == case.observation.actor
        assert fact['fact_context']['predicate_code'] == 'activity.current'
        shown = packet['author_snapshot_display']['snapshot']['materials']['relevant_facts']
        assert shown[0]['accepted_value'] == VALUE
        assert 'source_excerpt' not in shown[0]
        assert OBSERVATION not in canonical(fact) and OBSERVATION not in canonical(shown)
        assert 'actual_author_snapshot' not in packet
        # Original author and catalogue bytes are untouched and still verified.
        assert view.messages_json == original_view.messages_json
        assert view.source_table_json == original_view.source_table_json
        assert OBSERVATION in view.messages_json
        view.verify_snapshot(snapshot)
        fact_permission = next(p for p in packet['permission_choices'] if p['requires_exact_fact_quote'])
        assert fact_permission['accepted_value'] == VALUE
        assert inspect_review(raw=response(packet, permission=fact_permission['permission_id'], quote=VALUE),
            prepared_json=prepared, readings=readings)[0] == 'accepted'
        with pytest.raises(ValidationError):
            inspect_review(raw=response(packet, permission=fact_permission['permission_id'], quote=OBSERVATION),
                prepared_json=prepared, readings=readings)
        # Full utterance remains available only under its independent report scope.
        report = next(r for r in readings['readings'] if r['source_family'] == 'counterpart_report')
        assert report['value'] == OBSERVATION
        report_permission = next(p for p in packet['permission_choices']
            if p['reading_id'] == report['reading_id'] and p['claim_scope'] == 'report_uptake')
        assert not report_permission['requires_exact_fact_quote']
        assert inspect_review(raw=response(packet, permission=report_permission['permission_id']),
            prepared_json=prepared, readings=readings)[0] == 'accepted'


@pytest.mark.asyncio
async def test_fact_display_keeps_subject_predicate_and_exact_bytes_bound(tmp_path, monkeypatch):
    real_observation(monkeypatch)
    async with _sources(tmp_path, retained_value=VALUE) as case:
        view, snapshot = await _fact_view(case.capsule, review_contract=CURRENT)
        prepared = prepare_life_source_readings(view=view, snapshot=snapshot)
        payload = prepared.as_dict()
        fact = next(r for r in payload['readings'] if r['source_family'] == 'accepted_fact_value')
        with pytest.raises(ValueError, match='predicate/subject/status'):
            prepared.require_fact_value(reading_id=fact['reading_id'], quoted_value=VALUE,
                claim_scope='accepted_fact', subject_ref=case.capsule.actor_ref, view=view, snapshot=snapshot)
        for field, wrong in [('predicate_code', 'location.current'), ('subject_ref', case.capsule.actor_ref)]:
            altered = deepcopy(payload)
            next(r for r in altered['readings'] if r['reading_id'] == fact['reading_id'])['fact_context'][field] = wrong
            with pytest.raises(ValueError, match='pinned source/view compilation'):
                PreparedLifeSourceReadings(canonical(altered)).require_fact_value(
                    reading_id=fact['reading_id'], quoted_value=VALUE, claim_scope='accepted_fact',
                    subject_ref=case.observation.actor, view=view, snapshot=snapshot)
        with pytest.raises(ValueError, match='exact accepted Fact value'):
            prepared.require_fact_value(reading_id=fact['reading_id'], quoted_value=OBSERVATION,
                claim_scope='accepted_fact', subject_ref=case.observation.actor, view=view, snapshot=snapshot)


@pytest.mark.asyncio
async def test_legacy_ten_preparation_cold_rebuilds_without_new_display(tmp_path, monkeypatch):
    real_observation(monkeypatch)
    async with _sources(tmp_path, retained_value=VALUE) as case:
        view, snapshot = await _fact_view(case.capsule, review_contract=LEGACY)
        before, old_readings = prepare_review(candidate_json=candidate(), provider_raw=candidate(),
            view=view, snapshot=snapshot, contract=LEGACY)
        # Serialize both authorities, close the live ledger, and reconstruct.
        stored_view, stored_snapshot = view.model_dump_json(), snapshot.model_dump_json()
    cold_view = LifeSourceView.model_validate_json(stored_view)
    cold_snapshot = type(snapshot).model_validate_json(stored_snapshot)
    after, readings = prepare_review(candidate_json=candidate(), provider_raw=candidate(),
        view=cold_view, snapshot=cold_snapshot, contract=LEGACY)
    assert before == after and readings == old_readings
    assert readings['contract'] == 'life-source-readings.3'
    fact = next(r for r in readings['readings'] if r['source_family'] == 'accepted_fact_value')
    assert fact['value'] == OBSERVATION and 'accepted_value' not in fact
    packet = json.loads(json.loads(after)['request']['messages'][1]['content'])
    assert 'actual_author_snapshot' in packet and 'author_snapshot_display' not in packet
    permission = next(p for p in packet['permission_choices'] if p['requires_exact_fact_quote'])
    assert inspect_review(raw=response(packet, permission=permission['permission_id'], quote=VALUE),
        prepared_json=after, readings=readings)[0] == 'accepted'
    with pytest.raises(ValueError, match='exact accepted Fact value'):
        inspect_review(raw=response(packet, permission=permission['permission_id'], quote=OBSERVATION),
            prepared_json=after, readings=readings)
    with pytest.raises(ValueError, match='contract differs'):
        prepare_review(candidate_json=candidate(), provider_raw=candidate(),
            view=cold_view, snapshot=cold_snapshot, contract=CURRENT)


def test_display_projects_only_typed_fact_memory_and_audit_excerpt_columns():
    from companion_daemon.world_v2.character_interior.life_fact_readings import (
        DISPLAY_CONTRACT, fact_snapshot_display,
    )

    source = {'materials': {'remembered_material': [{'source_excerpts': [
        {'source_kind': 'fact', 'source_id': 'fact:one', 'text': OBSERVATION},
        {'source_kind': 'fact', 'source_id': 'fact:unqualified', 'text': 'unqualified report'},
        {'source_kind': 'prehistory', 'source_id': 'prehistory:one', 'text': OBSERVATION},
        {'source_kind': 'experience', 'source_id': 'experience:one', 'text': OBSERVATION},
    ]}], 'appraisals': {'columns': ['ref', 'readings', 'excerpts'],
        'stable_rows': [['appraisal:one', [['a retained meaning']], [OBSERVATION]]],
        'volatile_last_row': ['appraisal:two', [['another meaning']], ['audit excerpt']]},
        'recent_dialogue': {'stable_turns': [{'text': OBSERVATION}]}}}
    original = deepcopy(source)
    facts = {'readings': [{'item_ref': 'fact:one', 'display_contract': DISPLAY_CONTRACT,
        'accepted_value': VALUE, 'fact_context': {'predicate_code': 'activity.current',
            'subject_ref': 'user:one'}}]}
    shown = fact_snapshot_display(source, facts)['snapshot']['materials']
    excerpts = shown['remembered_material'][0]['source_excerpts']
    assert excerpts[0]['text'] == VALUE and 'text' not in excerpts[1]
    assert [e['text'] for e in excerpts[2:]] == [OBSERVATION, OBSERVATION]
    assert shown['recent_dialogue'] == source['materials']['recent_dialogue']
    assert shown['appraisals']['stable_rows'][0] == ['appraisal:one', [['a retained meaning']], []]
    assert shown['appraisals']['volatile_last_row'] == ['appraisal:two', [['another meaning']], []]
    assert source == original


def test_display_accepts_real_compact_appraisal_row_with_omitted_trailing_excerpts():
    from companion_daemon.world_v2.character_interior.life_fact_readings import fact_snapshot_display

    columns = ['ref', 'conf', 'since', 'until', 'readings', 'excerpts']
    stable = ['appraisal:earlier', 9000, '2026-09-20T01:00:00Z', None,
              [['a retained meaning']], [OBSERVATION]]
    volatile = ['appraisal:latest', 8000, '2026-09-20T01:03:00Z', None,
                [['another retained meaning']]]
    snapshot = {'materials': {'appraisals': {'columns': columns,
        'stable_rows': [stable], 'volatile_last_row': volatile}}}
    original = deepcopy(snapshot)
    displayed = fact_snapshot_display(snapshot, {'readings': []})['snapshot']
    appraisals = displayed['materials']['appraisals']
    assert appraisals['columns'] == columns
    assert appraisals['stable_rows'] == [[*stable[:5], []]]
    assert appraisals['volatile_last_row'] == volatile
    assert len(appraisals['volatile_last_row']) == 5
    assert snapshot == original
