"""Installed Life gate, same-author correction and durable recovery with fake HTTP.

Reviewer verdicts here are scripted transport responses, not semantic qualification.
"""
from copy import deepcopy
import json
import sqlite3

import pytest

from companion_daemon.world_v2.character_interior.core import _restore_prepared_turn, _InteriorTechnicalError
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_review import LifeSourceReviewer
from companion_daemon.world_v2.character_interior.turn_store import open_sqlite_character_interior_turn_store
from companion_daemon.world_v2.life_content_store import SQLiteImmutableLifeContentStore
from test_world_stimulus_life_response import _ResponseHTTP, _build, _model, _settled
from test_world_stimulus_life_intent import _http_result, WORLD

UNSUPPORTED = '雨停后我出门绕湖走了三圈，回来时买了一杯热咖啡。'
FEELING = '有点想听听窗外的声音。'


def _wire_fields(packet, fields):
    """Scripted semantic decompositions, not a production text classifier."""
    texts = {f['path']: f['text'] for f in packet['text_fields']}
    return [{'path': f['path'], 'reason': f['reason'], 'created_current_states': [],
        'record_bound_claims': [] if f['disposition'] == 'no_external_factual_commitment' else [{
            'source_span': texts[f['path']], 'proposition': texts[f['path']],
            'reason': f['reason'], 'supports': f['supports'], 'verdict': f['disposition']}],
        'coverage': 'complete'} for f in fields]


class ReviewHTTP:
    def __init__(self, verdicts, fault=None):
        self.verdicts = list(verdicts)
        self.fault = fault
        self.requests = []

    async def __call__(self, request):
        body = json.loads(request.content)
        self.requests.append(body)
        if self.fault == 'timeout':
            raise TimeoutError('offline reviewer timeout')
        packet = json.loads(body['messages'][1]['content'])
        outcome = self.verdicts.pop(0)
        fields = [{'path': item['path'], 'disposition': 'no_external_factual_commitment',
                   'reason': 'Fixture current expression or protocol field.', 'supports': []}
                  for item in packet['text_fields']]
        response_path = next(f for f in fields if f['path'].endswith('/response_text'))
        if outcome == 'supported':
            permission = next(p for p in packet['permission_choices'] if p['claim_scope'] == 'environment' and p['subject_role'] == 'general')
            response_path.update(disposition='supported', supports=[{'permission_id': permission['permission_id']}])
        elif outcome != 'accepted':
            response_path.update(disposition=outcome, reason='Only rain is established; no walking or coffee outcome supports this episode.')
        if self.fault == 'long_reason':
            response_path['reason'] = 'Detailed explanation. ' * 35
        if self.fault == 'omitted_field':
            fields.pop()
        if self.fault == 'partial_support_on_rejection':
            response_path['supports'] = [{'permission_id': packet['permission_choices'][0]['permission_id']}]
        if self.fault == 'borrow_environment':
            rain = next(r for r in packet['source_readings']['readings'] if r['source_family'] == 'settled_life')
            response_path.update(disposition='supported', supports=[{
                'reading_id': rain['reading_id'], 'claim_scope': 'external_fact', 'subject_role': 'companion',
                'subject_ref': None, 'quoted_value': None,
            }])
        fields = _wire_fields(packet, fields)
        if self.fault == 'invalid_authorship_span':
            fields[0]['created_current_states'] = [{'source_span': 'not present in this field', 'state_description': 'Fixture state.', 'subject_ref': packet['current_authorship_authority']['actor_ref'], 'time_relation': 'current'}]
        if self.fault == 'incomplete_decomposition':
            fields[0]['coverage'] = 'uncertain'
        if self.fault in {'past_state_as_creation', 'another_actor_creation'}:
            target = next(f for f in fields if f['path'].endswith('/response_text'))
            target['created_current_states'] = [{
                'source_span': next(f['text'] for f in packet['text_fields'] if f['path'] == target['path']),
                'state_description': 'Fixture state.',
                'subject_ref': 'another:actor' if self.fault == 'another_actor_creation' else packet['current_authorship_authority']['actor_ref'],
                'time_relation': 'past' if self.fault == 'past_state_as_creation' else 'current',
            }]
        if self.fault == 'authorship_with_rejected_past':
            target = next(f for f in fields if f['path'].endswith('/response_text'))
            target['created_current_states'] = [{'source_span': next(f['text'] for f in packet['text_fields'] if f['path'] == target['path']), 'state_description': 'Fixture current state with rejected embedded past.', 'subject_ref': packet['current_authorship_authority']['actor_ref'], 'time_relation': 'current'}]
        return _http_result(body, {'fields': fields, 'coverage': 'uncertain' if self.fault == 'incomplete_candidate' else 'complete'})


class CorrectingAuthor(_ResponseHTTP):
    async def __call__(self, request):
        if self.stimulus_requests:
            self.text = FEELING
        return await super().__call__(request)


async def run_gate(tmp_path, monkeypatch, *, author, verdicts, fault=None):
    import test_world_stimulus_life_intent as fixture

    monkeypatch.setenv('COMPANION_DISABLE_DEBUG_USAGE_LEDGER', '1')
    reviews = ReviewHTTP(verdicts, fault=fault)
    reviewer_model, author_model = _model(reviews), _model(author)
    sidecar = SQLiteImmutableLifeContentStore(path=str(tmp_path / 'reviews.sqlite'), world_id=WORLD)
    turns_path = tmp_path / 'turns.sqlite'
    turns = open_sqlite_character_interior_turn_store(path=turns_path, world_id=WORLD)
    compose = fixture.compose_production_character_interior
    monkeypatch.setattr(fixture, 'compose_production_character_interior', lambda **kwargs: compose(
        **kwargs, turn_store=turns, life_source_reviewer=LifeSourceReviewer(model=reviewer_model, evidence_store=sidecar)))
    app = _build(tmp_path / 'life.sqlite', author_model)
    try:
        await _settled(app)
        await app.drain_background_once()
        evidence = app.export_replay_evidence()
    finally:
        await app.aclose()
        await author_model.aclose()
        await reviewer_model.aclose()
        turns.close()
        sidecar.close()
    with sqlite3.connect(turns_path.as_uri() + '?mode=ro', uri=True) as conn:
        checkpoints = [json.loads(r[0]) for r in conn.execute("SELECT authored_state_json FROM world_v2_character_interior_turns WHERE purpose='world_stimulus_appraisal' AND authored_state_json IS NOT NULL")]
    with sqlite3.connect((tmp_path / 'reviews.sqlite').as_uri() + '?mode=ro', uri=True) as conn:
        audit = conn.execute('SELECT content_kind, text FROM world_v2_life_content').fetchall()
    responses = [r.event.payload()['response_text'] for r in evidence.events if r.event.event_type == 'CharacterLifeResponseRecorded']
    if not responses:
        failures = [json.loads(row.audit_json).get('failure_code') for row in evidence.projection.model_result_audits]
        assert any(code in {'invalid_role_result_after_correction', 'life_source_review_unavailable', 'life_source_review_uncertain'} for code in failures)
        assert not evidence.projection.experiences
    return responses, checkpoints, audit, reviews


@pytest.mark.asyncio
async def test_rejected_episode_is_corrected_by_same_author_before_any_life_write(tmp_path, monkeypatch):
    author = CorrectingAuthor(text=UNSUPPORTED)
    responses, checkpoints, audit, reviews = await run_gate(tmp_path, monkeypatch, author=author, verdicts=['unsupported', 'accepted'])
    assert responses == [FEELING]
    assert len(author.stimulus_requests) == len(reviews.requests) == 2
    correction = json.loads(author.stimulus_requests[1]['messages'][1]['content'])['correction']
    assert UNSUPPORTED in correction['rejected_role_result']['raw_result']
    assert 'Only rain' in correction['failure_detail']
    assert len(checkpoints) == 1
    result, snapshot, _, _ = _restore_prepared_turn(canonical(checkpoints[0]), purpose='world_stimulus_appraisal')
    assert result.life_source_review.verify(result=result, snapshot=snapshot)
    assert result.author_lineage.attempt_ordinal == 1
    assert sum(kind == 'raw_model_request' for kind, _ in audit) == 2
    assert any('unsupported' in raw for kind, raw in audit if kind == 'raw_model_result')
    assert not any('LifeContentRecorded' in raw for _, raw in audit)


@pytest.mark.asyncio
async def test_second_unsupported_episode_is_technical_failure_not_memory_or_silence(tmp_path, monkeypatch):
    author = _ResponseHTTP(text=UNSUPPORTED)
    responses, checkpoints, audit, reviews = await run_gate(tmp_path, monkeypatch, author=author, verdicts=['unsupported', 'unsupported'])
    assert responses == checkpoints == []
    assert len(author.stimulus_requests) == len(reviews.requests) == 2
    assert len(audit) >= 4


@pytest.mark.asyncio
async def test_environment_support_uses_closed_permission_tokens_and_survives_restore(tmp_path, monkeypatch):
    text = '雨停了，有点想去窗边听听声音。'
    responses, checkpoints, _, reviews = await run_gate(tmp_path, monkeypatch,
        author=_ResponseHTTP(text=text), verdicts=['supported'], fault='long_reason')
    assert responses == [text]
    field_schema = reviews.requests[0]['tools'][0]['function']['parameters']['properties']['fields']['items']
    claim_schema = field_schema['properties']['record_bound_claims']['items']
    support = claim_schema['properties']['supports']['items']['properties']
    assert field_schema['required'] == list(field_schema['properties']) == ['path', 'reason', 'created_current_states', 'record_bound_claims', 'coverage']
    assert claim_schema['required'] == list(claim_schema['properties']) == ['source_span', 'proposition', 'reason', 'supports', 'verdict']
    assert set(support) == {'permission_id'}
    packet = json.loads(reviews.requests[0]['messages'][1]['content'])
    assert set(support['permission_id']['enum']) == {p['permission_id'] for p in packet['permission_choices']}
    assert all(p['subject_ref'] is None and p['selection'] == 'direct_field' for p in packet['permission_choices'])
    result, snapshot, _, _ = _restore_prepared_turn(canonical(checkpoints[0]), purpose='world_stimulus_appraisal')
    assert result.life_source_review.contract == 'life-source-review.13'
    authority = packet['current_authorship_authority']
    assert authority['actor_ref'] == snapshot.actor_ref
    assert authority['logical_time'] == snapshot.logical_time.isoformat()
    assert authority['world_fact_source'] is False
    result.life_source_review.verify(result=result, snapshot=snapshot)


@pytest.mark.asyncio
async def test_supported_subset_does_not_turn_rejected_mixed_statement_into_acceptance(tmp_path, monkeypatch):
    author = _ResponseHTTP(text=UNSUPPORTED)
    responses, checkpoints, _, reviews = await run_gate(tmp_path, monkeypatch, author=author,
        verdicts=['unsupported', 'unsupported'], fault='partial_support_on_rejection')
    assert responses == checkpoints == []
    assert len(author.stimulus_requests) == len(reviews.requests) == 2


@pytest.mark.asyncio
async def test_current_authorship_annotation_cannot_hide_a_rejected_embedded_past(tmp_path, monkeypatch):
    author = _ResponseHTTP(text=UNSUPPORTED)
    responses, checkpoints, _, reviews = await run_gate(tmp_path, monkeypatch, author=author,
        verdicts=['unsupported', 'unsupported'], fault='authorship_with_rejected_past')
    assert responses == checkpoints == []
    assert len(author.stimulus_requests) == len(reviews.requests) == 2


@pytest.mark.asyncio
async def test_fact_permission_choice_still_requires_exact_accepted_value(tmp_path):
    from test_life_fact_readings import _sources, _fact_view, VALUE
    from test_character_interior_structured_role import _world_stimulus_no_change_result
    from companion_daemon.world_v2.character_interior.life_source_review import prepare_review, inspect_review

    async with _sources(tmp_path, retained_value=VALUE) as case:
        view, snapshot = await _fact_view(case.capsule)
        candidate = json.loads(_world_stimulus_no_change_result())
        candidate['summary'] = '用户保留周四的约定。'
        prepared, readings = prepare_review(candidate_json=canonical(candidate), provider_raw=canonical(candidate), view=view, snapshot=snapshot)
        packet = json.loads(json.loads(prepared)['request']['messages'][1]['content'])
        choice = next(p for p in packet['permission_choices'] if p['requires_exact_fact_quote'])
        fields = [{'path': f['path'], 'disposition': 'no_external_factual_commitment', 'reason': 'Fixture protocol value.', 'supports': []} for f in packet['text_fields']]
        summary = next(f for f in fields if f['path'] == '/summary')
        summary.update(disposition='supported', supports=[{'permission_id': choice['permission_id'], 'quoted_value': VALUE}])
        assert inspect_review(raw=canonical({'fields': _wire_fields(packet, fields), 'coverage': 'complete'}), prepared_json=prepared, readings=readings)[0] == 'accepted'
        summary['supports'][0]['quoted_value'] = case.observation.text
        with pytest.raises(ValueError, match='exact accepted Fact value'):
            inspect_review(raw=canonical({'fields': _wire_fields(packet, fields), 'coverage': 'complete'}), prepared_json=prepared, readings=readings)


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['timeout', 'omitted_field', 'borrow_environment', 'uncertain', 'invalid_authorship_span', 'incomplete_decomposition', 'past_state_as_creation', 'another_actor_creation', 'incomplete_candidate'])
async def test_incomplete_review_never_becomes_author_correction_or_a_life_write(tmp_path, monkeypatch, fault):
    author = _ResponseHTTP(text=FEELING)
    responses, checkpoints, _, reviews = await run_gate(tmp_path, monkeypatch, author=author,
        verdicts=['uncertain' if fault == 'uncertain' else 'accepted'], fault=fault)
    assert responses == checkpoints == []
    assert len(author.stimulus_requests) == len(reviews.requests) == 1


@pytest.mark.asyncio
async def test_restored_life_candidate_cannot_drop_or_swap_review_evidence(tmp_path, monkeypatch):
    responses, checkpoints, _, _ = await run_gate(tmp_path, monkeypatch, author=_ResponseHTTP(text=FEELING), verdicts=['accepted'])
    assert responses == [FEELING]
    for fault in ('missing', 'candidate', 'response', 'author', 'request'):
        payload = deepcopy(checkpoints[0])
        result = payload['result']
        if fault == 'missing':
            del result['life_source_review']
        elif fault == 'candidate':
            result['summary'] += ' 我走完了一圈。'
        elif fault == 'response':
            result['life_source_review']['response_json'] = '{}'
        elif fault == 'author':
            result['author_lineage']['response_hash'] = 'sha256:' + '0' * 64
        else:
            result['life_source_review']['request_hash'] = '0' * 64
        with pytest.raises(_InteriorTechnicalError, match='invalid_durable_turn_checkpoint'):
            _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')


@pytest.mark.asyncio
async def test_recall_control_transfer_is_not_mistaken_for_a_failed_life_candidate(tmp_path, monkeypatch):
    from test_life_source_view import _prepared
    from test_character_interior_structured_role import _RequiredToolQueueModel, _result
    from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
    from companion_daemon.world_v2.character_interior.ports import _InteriorRoleResult
    from companion_daemon.world_v2.character_interior.life_source_review import verify_life_review

    _, request, _ = await _prepared(tmp_path, monkeypatch)
    value = json.loads(_result(status='recall_request', recall_query='过去提到的约定'))
    value['attended_source_refs'] = []

    class NoReviewOnRecall:
        async def review(self, **kwargs):
            raise AssertionError('Recall control transfer must not call a Life factual reviewer')

    role = StructuredCharacterRoleFaculty(model=_RequiredToolQueueModel(canonical(value)),
        model_id='offline-recall-author', life_source_reviewer=NoReviewOnRecall())
    result = _InteriorRoleResult.model_validate(await role.experience(request))
    assert result.status == 'recall_request' and result.life_source_review is None
    verify_life_review(result, request.snapshot, required=True)
