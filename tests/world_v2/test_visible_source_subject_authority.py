"""Native settled evidence: environment truth does not imply personal presence."""
import json
from copy import deepcopy

import pytest

from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from companion_daemon.world_v2.sqlite_ledger import SQLiteWorldLedger
from companion_daemon.world_v2.visible_source_composer import compile_visible_source_table
from companion_daemon.world_v2.visible_source_witness_experiment import prepare_witness_experiment
from test_character_life_experience_runtime import _accepted_response
from test_current_activity_context import current_context
from test_visible_source_composer import _request
from test_world_stimulus_life_intent import ACTOR, WORLD
from test_visible_source_witness_experiment import _sources, _response, _json


@pytest.mark.asyncio
async def test_native_environment_can_close_without_becoming_the_participants_action(tmp_path, monkeypatch):
    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    path = tmp_path / 'native.sqlite'
    settlement_ref, _ = await _accepted_response(path, '雨停了，我松了一口气。')
    ledger = SQLiteWorldLedger(path=path, world_id=WORLD)
    store = SQLiteImmutableLifeContentStore(path=str(path), world_id=WORLD)
    try:
        capsule, _, _ = current_context(ledger, store, actor_ref=ACTOR)
        rows = compile_visible_source_table(request=_request(capsule), capsule=capsule).source_references()
        source = next(r for r in rows if r['source_ref'] == settlement_ref)
        assert source['support_subject_role'] == 'companion'
        raw = _response('刚才那阵短雨停了。', 'external_fact')
        part = raw['decisions'][0]['parts'][0]
        part['subject_role'] = 'none'
        part['witnesses'][0].update(source_ref_index=source['source_ref_index'], pointer='/item/value/content/world_consequence/environment/text', quote='一阵短雨已经停了。')
        old = prepare_witness_experiment(beats=(part['text'],), sources=rows)
        with pytest.raises(ValueError, match='scope exceeds'):
            old.inspect_response(_json(raw))
        new = prepare_witness_experiment(beats=(part['text'],), sources=rows, source_owner_semantics=True)
        assert new.inspect_response(_json(raw))['model_verdicts'] == ['closed']
        for role in ('companion', 'counterpart'):
            changed = deepcopy(raw)
            changed['decisions'][0]['parts'][0]['subject_role'] = role
            with pytest.raises(ValueError, match='source subject authority'):
                new.inspect_response(_json(changed))
        # Neither the source proof nor the old request changes after new reads.
        assert old.inspect_response(_json({**raw, 'decisions': [{**raw['decisions'][0], 'parts': [{**part, 'verdict': 'unclosed'}]}]}))['receipt_authority'] is False
        before = json.loads(old.payload_json)
        after = json.loads(new.payload_json)
        for key in ('sources', 'shown_materials', 'material_indexes'):
            assert before[key] == after[key]
    finally:
        ledger.close()


def test_source_owner_mode_does_not_promote_self_speech_into_experience():
    raw = _response()
    part = raw['decisions'][0]['parts'][0]
    prepared = prepare_witness_experiment(beats=(part['text'],), sources=_sources(), source_owner_semantics=True)
    assert prepared.inspect_response(_json(raw))['model_verdicts'] == ['closed']
    for scope in ('external_fact', 'environment', 'activity_lifecycle'):
        changed = deepcopy(raw)
        changed['decisions'][0]['parts'][0]['claim_scope'] = scope
        with pytest.raises(ValueError, match='source subject authority'):
            prepared.inspect_response(_json(changed))


def _report_sources():
    from companion_daemon.world_v2.visible_source_closure_protocol import compact_source_reference_table
    entry = {
        'kind': 'current_counterpart_report', 'availability': 'available',
        'privacy_class': 'private', 'authority': 'report_only_not_external_truth',
        'source_refs': ['event:family-report'],
        'message': {'actor': 'user:counterpart', 'event_ref': 'event:family-report',
                    'event_payload_hash': 'b' * 64, 'text': '我妈妈来车站接我了。'},
    }
    return compact_source_reference_table({
        'subjects': {'companion_actor_ref': 'agent:companion', 'counterpart_actor_ref': 'user:counterpart'},
        'entries': [entry],
    })


@pytest.mark.parametrize('role', ['counterpart', 'other', 'general', 'none'])
def test_report_can_describe_its_recipient_or_third_party_without_changing_speaker(role):
    raw = _response('家里人来接你了呀。', 'external_fact')
    part = raw['decisions'][0]['parts'][0]
    part['subject_role'] = role
    part['witnesses'][0].update(pointer='/message/text', quote='我妈妈来车站接我了。')
    sources = _report_sources()
    prepared = prepare_witness_experiment(beats=(part['text'],), sources=sources, source_owner_semantics=True)
    assert prepared.inspect_response(_json(raw))['model_verdicts'] == ['closed']
    packet = json.loads(prepared.request()['messages'][1]['content'])
    columns = packet['source_reference_tables'][0]['columns']
    assert 'source_owner_role' in columns and 'support_subject_role' not in columns
    assert sources[0]['support_subject_role'] == 'counterpart'
    part['subject_role'] = 'companion'
    with pytest.raises(ValueError, match='source subject authority'):
        prepared.inspect_response(_json(raw))


def test_owner_policy_does_not_restore_ineligible_or_altered_sources():
    raw = _response('家里人来接你了呀。', 'external_fact')
    part = raw['decisions'][0]['parts'][0]
    part['subject_role'] = 'other'
    part['witnesses'][0].update(pointer='/message/text', quote='我妈妈来车站接我了。')
    rows = deepcopy(_report_sources())
    rows[0]['support_eligibility'] = 'baseline_only'
    prepared = prepare_witness_experiment(beats=(part['text'],), sources=rows, source_owner_semantics=True)
    with pytest.raises(ValueError, match='not eligible'):
        prepared.inspect_response(_json(raw))
    valid = prepare_witness_experiment(beats=(part['text'],), sources=_report_sources(), source_owner_semantics=True)
    part['witnesses'][0]['quote'] = '我妈妈来车站接你了。'
    with pytest.raises(ValueError, match='quote differs'):
        valid.inspect_response(_json(raw))


def test_direct_source_schema_excludes_baseline_aliases_but_keeps_context_readings():
    from jsonschema import Draft202012Validator
    rows = list(deepcopy(_report_sources()))
    alias = deepcopy(rows[0])
    alias.update(source_ref_index=1, source_ref='alias:context', support_eligibility='baseline_only')
    rows.append(alias)
    raw = _response('家里人来接你了呀。', 'external_fact')
    part = raw['decisions'][0]['parts'][0]
    part['subject_role'] = 'counterpart'
    part['witnesses'][0].update(pointer='/message/text', quote='我妈妈来车站接我了。')
    prepared = prepare_witness_experiment(beats=(part['text'],), sources=tuple(rows), source_owner_semantics=True)
    validator = Draft202012Validator(prepared.request()['tools'][0]['function']['parameters'])
    validator.validate(raw)
    part['witnesses'][0]['source_ref_index'] = 1
    assert list(validator.iter_errors(raw))
    part['witnesses'][0]['use'] = 'context'
    direct = deepcopy(part['witnesses'][0])
    direct.update(source_ref_index=0, use='direct')
    part['witnesses'].append(direct)
    validator.validate(raw)
    assert prepared.inspect_response(_json(raw))['model_verdicts'] == ['closed']


@pytest.mark.parametrize('verdict,scope', [('unclosed', 'external_fact'), ('source_free', 'source_free')])
def test_non_authoritative_branch_does_not_require_a_support_quote(verdict, scope):
    from jsonschema import Draft202012Validator
    raw = _response('你上次送我出发的时候呢？', scope)
    part = raw['decisions'][0]['parts'][0]
    part['verdict'] = verdict
    prepared = prepare_witness_experiment(beats=(part['text'],), sources=_report_sources(), source_owner_semantics=True)
    validator = Draft202012Validator(prepared.request()['tools'][0]['function']['parameters'])
    assert list(validator.iter_errors(raw))
    with pytest.raises(ValueError, match='omit witnesses'):
        prepared.inspect_response(_json(raw))
    del part['witnesses']
    validator.validate(raw)
    result = prepared.inspect_response(_json(raw))
    assert result['model_verdicts'] == [verdict]
    assert result['readings']['decisions'][0]['parts'][0]['witnesses'] == []
    assert result['receipt_authority'] is False
    # An incorrect source-free classification is still a semantic model error;
    # no character text or meaning is inferred by this structural normalizer.


@pytest.mark.parametrize('sources,pointers', [
    (_sources, ['/item/value/text', '/item/value/status']),
    (_report_sources, ['/message/text', '/messages/0/text']),
])
def test_batch_permissions_preserve_scalar_checks_and_revalidate_changed_sources(sources, pointers):
    from companion_daemon.world_v2.visible_source_subject_authority import (
        permits_source_subject, source_subject_permissions,
    )
    row = deepcopy(sources()[0])
    expected = {pointer: [
        [scope, role]
        for scope in ('utterance_record', 'accepted_intention', 'activity_lifecycle', 'environment', 'external_fact')
        for role in ('companion', 'counterpart', 'general', 'other', 'none')
        if permits_source_subject(row=row, pointer=pointer, claim_scope=scope, subject_role=role)
    ] for pointer in pointers}
    actual = source_subject_permissions(row=row, pointers=pointers)
    assert actual == expected
    # Returned lists are invocation-local; neither mutation nor a formerly
    # eligible row grants a later compilation cached source authority.
    actual[pointers[0]].clear()
    assert source_subject_permissions(row=row, pointers=pointers) == expected
    row['support_eligibility'] = 'baseline_only'
    assert source_subject_permissions(row=row, pointers=pointers) == {}


def test_batch_permissions_validate_source_once_before_all_field_permissions(monkeypatch):
    import companion_daemon.world_v2.visible_source_subject_authority as authority
    original = authority._eligible_reference
    calls = []

    def counted(row):
        calls.append(row)
        return original(row)

    monkeypatch.setattr(authority, '_eligible_reference', counted)
    row = _report_sources()[0]
    actual = authority.source_subject_permissions(row=row, pointers=['/message/text', '/messages/0/text'])
    assert actual['/message/text'] == actual['/messages/0/text']
    assert len(calls) == 1
