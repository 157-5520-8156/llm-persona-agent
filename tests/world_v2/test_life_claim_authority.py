"""Claim/source authority edges with explicit offline semantic annotations.

The tests do not teach the critic semantics or qualify a provider. Synthetic
observer history is imported through the real typed archive/memory reader and
is never represented as an event from the private qualification journey.
"""
from copy import deepcopy
import json

from jsonschema import ValidationError
import pytest

from companion_daemon.llm import provider_invocation_request_hash
from companion_daemon.world_v2.character_interior.life_claim_authority import CONTRACT
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_review import candidate_body, inspect_review, prepare_review
from companion_daemon.world_v2.character_interior.life_source_view import _expected_view
from companion_daemon.world_v2.character_interior.core import _restore_prepared_turn
from companion_daemon.world_v2.background_context_profile import profile_audit_record
from companion_daemon.world_v2.character_interior.life_source_state_readings import life_source_profile
from companion_daemon.world_v2.selected_source_composer import compile_selected_source_table
from test_life_source_view import _prepared

SEEN = '把自己在校园路上看到的画摊也讲给他听'


def new_fixture_view(view, snapshot, contract=CONTRACT):
    """Compile a new fixture author presentation; never upgrade a sent pin."""
    messages = json.loads(view.messages_json)
    body = json.loads(messages[1]['content'])
    body['inner_life_snapshot'] = _expected_view(snapshot, contract)
    body['background_context_profile'] = profile_audit_record(life_source_profile(contract))
    messages[1]['content'] = canonical(body)
    controls = json.loads(view.provider_controls_json)
    table = compile_selected_source_table(capsule=snapshot.life_source_origin.capsule(),
        include_subjective_history=True, include_lifecycle_states=True)
    return view.model_copy(update={'review_contract': contract, 'messages_json': canonical(messages),
        'provider_request_hash': 'sha256:' + provider_invocation_request_hash(messages=messages, **controls),
        'source_table_json': table.payload_json}).verify_snapshot(snapshot)


def review_response(packet, *, scope=None, role=None, ref=None, permission=None,
                    verdict='supported', subjective=False):
    fields = []
    for item in packet['text_fields']:
        claim = []
        states = []
        if item['path'] == '/summary':
            if scope is not None:
                claim = [{'source_span': item['text'], 'proposition': item['text'],
                    'required_scope': scope, 'subject_role': role, 'subject_ref': ref,
                    'reason': 'Explicit offline annotation; full entailment is not proven by this fixture.',
                    'supports': [] if permission is None else [{'permission_id': permission}],
                    'verdict': verdict}]
            if subjective:
                states = [{'source_span': item['text'], 'state_description': 'An authored present feeling.',
                           'subject_ref': packet['current_authorship_authority']['actor_ref'], 'time_relation': 'current'}]
        fields.append({'path': item['path'], 'reason': 'Offline field coverage annotation.',
                       'created_current_states': states, 'record_bound_claims': claim, 'coverage': 'complete'})
    return {'fields': fields, 'coverage': 'complete'}


@pytest.mark.asyncio
@pytest.mark.parametrize('case,text,scope,role,expected', [
    ('environment', '一阵短雨已经停了。', 'environment', 'general', 'accepted'),
    ('personal_seen', SEEN, 'external_fact', 'companion', 'rejected'),
    ('past_emotion', '昨天我就觉得暖。', 'subjective_history', 'companion', 'rejected'),
    ('current_feeling', '我这会儿觉得暖。', None, None, 'accepted'),
])
async def test_environment_cannot_discharge_personal_or_historical_obligation(
    tmp_path, monkeypatch, case, text, scope, role, expected,
):
    payload, _, _ = await _prepared(tmp_path, monkeypatch)
    result, snapshot, _, _ = _restore_prepared_turn(canonical(payload), purpose='world_stimulus_appraisal')
    view = new_fixture_view(result.life_source_view, snapshot)
    candidate = json.loads(candidate_body(result)); candidate['summary'] = text
    prepared, readings = prepare_review(candidate_json=canonical(candidate), provider_raw=canonical(candidate),
                                       view=view, snapshot=snapshot)
    request = json.loads(prepared)['request']; packet = json.loads(request['messages'][1]['content'])
    choice = next(p for p in packet['permission_choices']
                  if p['claim_scope'] == 'environment' and p['subject_role'] == 'general')
    reply = review_response(packet, scope=scope, role=role,
        ref=snapshot.actor_ref if role == 'companion' else None,
        permission=choice['permission_id'] if scope else None, subjective=scope is None)
    assert inspect_review(raw=canonical(reply), prepared_json=prepared, readings=readings)[0] == expected
    assert request['tool_choice']['function']['name'] == 'review_life_candidate_v8'
    assert view.source_table_json == result.life_source_view.source_table_json
    # A false typed semantic annotation can still fool this edge checker. Never
    # report these deterministic tests as evidence of semantic completeness.
    if case == 'personal_seen':
        claim = reply['fields'][next(i for i,f in enumerate(reply['fields']) if f['path']=='/summary')]['record_bound_claims'][0]
        claim.update(required_scope='environment', subject_role='general', subject_ref=None)
        assert inspect_review(raw=canonical(reply), prepared_json=prepared, readings=readings)[0] == 'accepted'
    cold, restored_readings = prepare_review(candidate_json=canonical(candidate), provider_raw=canonical(candidate),
        view=type(view).model_validate_json(view.model_dump_json()),
        snapshot=type(snapshot).model_validate_json(snapshot.model_dump_json()))
    assert cold == prepared and restored_readings == readings


@pytest.mark.asyncio
async def test_real_typed_observer_history_reader_allows_only_its_bound_actor(tmp_path):
    from companion_daemon.world_v2.character_prehistory import PrehistoryArchiveDocument, PrehistoryReview, ReviewedPrehistoryArchive, digest
    from companion_daemon.world_v2.character_prehistory_runtime import PrehistoryArchiveRuntime
    from companion_daemon.world_v2.prehistory_memory_source import prehistory_memory_binding
    from test_character_prehistory import START, ACTOR, reviewed_archive, started_ledger
    from test_prehistory_memory_source import _choice
    from test_prehistory_chat_context import _capsule
    from test_life_fact_readings import _fact_view
    from test_life_fact_value_display import candidate
    text = '2023年4月，我在校刊编辑室亲眼看见校刊同学翻看画稿。'
    document_data = reviewed_archive().document.model_dump(mode='json')
    document_data['source_artifact_ref'] = 'fixture:explicit-observer-history-not-private-journey'
    document_data['records'][0]['statement'] = text
    document = PrehistoryArchiveDocument.model_validate_json(canonical(document_data))
    archive = ReviewedPrehistoryArchive(document=document, review=PrehistoryReview(
        manifest_hash=digest(document.manifest()), reviewer_ref='operator:offline-observer-fixture',
        review_artifact_ref='fixture:observer-history-review', review_artifact_hash='b'*64,
        reviewed_at=START, decision='approved'))
    ledger = started_ledger(tmp_path/'observer.sqlite')
    try:
        row, = PrehistoryArchiveRuntime(ledger=ledger, owner_actor_ref=ACTOR).import_reviewed(archive, created_at=START)
        binding = prehistory_memory_binding(row); pending = _choice(ledger, binding)
        _choice(ledger, binding, before=pending, status='active')
        original, snapshot = await _fact_view(_capsule(ledger))
        view = new_fixture_view(original, snapshot)
        body = json.loads(candidate()); body['summary'] = text
        prepared, readings = prepare_review(candidate_json=canonical(body), provider_raw=canonical(body), view=view, snapshot=snapshot)
        packet = json.loads(json.loads(prepared)['request']['messages'][1]['content'])
        reading = next(r for r in readings['readings'] if r['source_family']=='retained_prehistory')
        assert reading['value']==text and reading['source_owner_ref']==ACTOR
        choice = next(p for p in packet['permission_choices'] if p['reading_id']==reading['reading_id']
                      and p['claim_scope']=='external_fact' and p['subject_role']=='companion')
        reply = review_response(packet, scope='external_fact', role='companion', ref=ACTOR, permission=choice['permission_id'])
        assert inspect_review(raw=canonical(reply), prepared_json=prepared, readings=readings)[0]=='accepted'
        field = next(f for f in reply['fields'] if f['path']=='/summary'); claim=field['record_bound_claims'][0]
        for role, ref in [('companion','actor:other'), ('counterpart',ACTOR), ('source_owner','actor:other'), ('unresolved',None)]:
            claim.update(subject_role=role, subject_ref=ref)
            assert inspect_review(raw=canonical(reply), prepared_json=prepared, readings=readings)[0]=='rejected'
        claim.update(subject_role='companion', subject_ref=ACTOR)
        forged=json.loads(prepared); p=json.loads(forged['request']['messages'][1]['content'])
        p['claim_subject_bindings'][0]['subject_ref']='actor:other'; forged['request']['messages'][1]['content']=canonical(p)
        with pytest.raises(ValueError, match='claim subject bindings'):
            inspect_review(raw=canonical(reply), prepared_json=canonical(forged), readings=readings)
        del claim['required_scope']
        with pytest.raises(ValidationError):
            inspect_review(raw=canonical(reply), prepared_json=prepared, readings=readings)
    finally:
        ledger.close()


@pytest.mark.asyncio
async def test_opt_in_actual_provider_wire_rejection_corrects_author_and_cold_restores(tmp_path, monkeypatch):
    import test_life_source_review_runtime as fixture
    import companion_daemon.world_v2.character_interior.life_source_review as review_module
    monkeypatch.setattr(review_module, 'CONTRACT', CONTRACT)  # Explicit fixture install; production default stays .13.
    old_wire=fixture._wire_fields
    def annotated(packet, fields):
        rows=old_wire(packet, fields)
        for field in rows:
            for claim in field['record_bound_claims']:
                claim.update(required_scope='external_fact', subject_role='companion',
                             subject_ref=packet['current_authorship_authority']['actor_ref'])
        return rows
    monkeypatch.setattr(fixture,'_wire_fields',annotated)
    author=fixture.CorrectingAuthor(text=SEEN)
    responses, checkpoints, _, reviews=await fixture.run_gate(tmp_path,monkeypatch,author=author,verdicts=['supported','accepted'])
    assert responses==[fixture.FEELING]
    assert len(author.stimulus_requests)==len(reviews.requests)==2
    assert all(r['tool_choice']['function']['name']=='review_life_candidate_v8' for r in reviews.requests)
    correction=json.loads(author.stimulus_requests[1]['messages'][1]['content'])['correction']
    assert 'No selected permission supports' in correction['failure_detail']
    result,snapshot,_,_=_restore_prepared_turn(canonical(checkpoints[0]),purpose='world_stimulus_appraisal')
    assert result.life_source_review.contract==CONTRACT
    assert result.life_source_review.verify(result=result,snapshot=snapshot)


@pytest.mark.asyncio
async def test_bound_fact_subject_and_predicate_scope_remain_exact(tmp_path, monkeypatch):
    from test_life_bound_permission_review import prepared_fact
    from test_life_fact_value_display import candidate
    async with prepared_fact(tmp_path, monkeypatch) as (old_view, snapshot, old_prepared, old_readings, _, _, _):
        view=new_fixture_view(old_view,snapshot)
        prepared,readings=prepare_review(candidate_json=candidate(),provider_raw=candidate(),view=view,snapshot=snapshot)
        packet=json.loads(json.loads(prepared)['request']['messages'][1]['content'])
        choice=next(p for p in packet['permission_choices'] if p['selection']=='bound_accepted_value')
        reply=review_response(packet,scope=choice['claim_scope'],role='source_owner',ref=choice['subject_ref'],permission=choice['permission_id'])
        assert inspect_review(raw=canonical(reply),prepared_json=prepared,readings=readings)[0]=='accepted'
        claim=next(f for f in reply['fields'] if f['path']=='/summary')['record_bound_claims'][0]
        for change in ({'subject_ref':snapshot.actor_ref},{'required_scope':'historical_accepted_fact'},
                       {'required_scope':'external_fact'},{'subject_ref':None},{'required_scope':'unresolved'}):
            wrong=deepcopy(reply);next(f for f in wrong['fields'] if f['path']=='/summary')['record_bound_claims'][0].update(change)
            assert inspect_review(raw=canonical(wrong),prepared_json=prepared,readings=readings)[0]=='rejected'
        unchanged,recompiled=prepare_review(candidate_json=candidate(),provider_raw=candidate(),view=old_view,snapshot=snapshot)
        assert unchanged==old_prepared and recompiled==old_readings
        assert 'claim_subject_bindings' not in old_prepared
