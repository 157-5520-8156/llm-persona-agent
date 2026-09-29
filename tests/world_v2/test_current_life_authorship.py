"""Author/reviewer permission parity at the real HTTP seam, without semantic claims."""
from copy import deepcopy
import json

from jsonschema import ValidationError
import pytest

from companion_daemon.world_v2.character_interior.core import _InteriorTechnicalError, _restore_prepared_turn
from companion_daemon.world_v2.character_interior.current_life_authorship import (
    AUTHOR_PRESENTATION_CONTRACT,
    current_life_authorship_authority,
)
from companion_daemon.world_v2.character_interior.life_source_origin import canonical
from companion_daemon.world_v2.character_interior.life_source_review import inspect_review
from companion_daemon.world_v2.character_interior.structured_role import StructuredCharacterRoleFaculty
from test_character_interior_structured_role import (
    StructuredCharacterRoleFaculty as FixtureRole,
    _RequiredToolQueueModel,
    _request,
    _world_stimulus_manifest,
)
from test_life_source_review_runtime import CorrectingAuthor, FEELING, UNSUPPORTED, run_gate


def test_current_authorship_wording_is_frozen_and_each_use_is_independent():
    expected = {
        'contract': 'current-life-authorship.1',
        'actor_ref': 'agent:test',
        'logical_time': '2026-09-20T10:16:30+08:00',
        'authority': "Create this actor's present appraisal, attitude, feeling, interpretation and choice in this invocation.",
        'exclusions': 'Not evidence of past feelings, past decisions, performed actions, external conditions, other people or embedded historical premises.',
        'world_fact_source': False,
    }
    args = {'actor_ref': expected['actor_ref'], 'logical_time': expected['logical_time']}
    assert current_life_authorship_authority(**args) == expected
    modified = current_life_authorship_authority(**args)
    modified['world_fact_source'] = True
    assert current_life_authorship_authority(**args) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('purpose', ['world_stimulus_appraisal', 'generic'])
@pytest.mark.parametrize('installed', [False, True])
async def test_only_an_explicitly_reviewed_life_author_gets_the_new_input(purpose, installed):
    request = await _request(phase='experience', purpose=purpose,
        capability_manifest=_world_stimulus_manifest() if purpose == 'world_stimulus_appraisal' else None)
    role = FixtureRole(model=_RequiredToolQueueModel(), model_id='offline',
        life_source_reviewer=object() if installed else None)
    messages = role._messages(request, contract=role._resolve_contract(request))
    packet = json.loads(messages[1]['content'])
    authority = packet.pop('current_authorship_authority', None)
    if purpose == 'world_stimulus_appraisal' and installed:
        assert authority == {
            'presentation_contract': AUTHOR_PRESENTATION_CONTRACT,
            **current_life_authorship_authority(actor_ref=request.snapshot.actor_ref,
                logical_time=request.snapshot.logical_time.isoformat()),
        }
    else:
        assert authority is None
    # Life .13 adds an explicit presentation profile; the authority wording
    # still changes no role instruction or other material for this empty fixture.
    original = FixtureRole(model=_RequiredToolQueueModel(), model_id='offline')
    old_messages = original._messages(request, contract=original._resolve_contract(request))
    assert messages[0] == old_messages[0]
    old_packet = json.loads(old_messages[1]['content'])
    if purpose == 'world_stimulus_appraisal' and installed:
        from companion_daemon.world_v2.background_context_profile import profile_audit_record
        from companion_daemon.world_v2.character_interior.life_source_state_readings import life_source_profile
        from companion_daemon.world_v2.character_interior.life_source_review import MINTED_CONTRACT as CONTRACT
        assert packet['background_context_profile'] == profile_audit_record(life_source_profile(CONTRACT))
        packet['background_context_profile'] = old_packet['background_context_profile']
        packet['inner_life_snapshot']['background_context_profile'] = old_packet['inner_life_snapshot']['background_context_profile']
    assert packet == old_packet
    assert role._tool_contract(request) == original._tool_contract(request)


@pytest.mark.asyncio
async def test_actual_initial_and_corrected_author_share_review_authority_without_fact_permission(tmp_path, monkeypatch):
    captured = []
    original = StructuredCharacterRoleFaculty.experience

    async def capture(self, request):
        if request.purpose == 'world_stimulus_appraisal':
            captured.append(request)
        return await original(self, request)

    monkeypatch.setattr(StructuredCharacterRoleFaculty, 'experience', capture)
    author = CorrectingAuthor(text=UNSUPPORTED)
    responses, checkpoints, audit, reviews = await run_gate(tmp_path, monkeypatch,
        author=author, verdicts=['unsupported', 'accepted'])
    assert responses == [FEELING]
    assert len(author.stimulus_requests) == len(reviews.requests) == len(captured) == 2
    for author_body, review_body, request in zip(author.stimulus_requests, reviews.requests, captured, strict=True):
        author_packet = json.loads(author_body['messages'][1]['content'])
        review_packet = json.loads(review_body['messages'][1]['content'])
        authority = author_packet['current_authorship_authority']
        expected = current_life_authorship_authority(actor_ref=request.snapshot.actor_ref,
            logical_time=request.snapshot.logical_time.isoformat())
        assert authority == {'presentation_contract': AUTHOR_PRESENTATION_CONTRACT, **expected}
        assert {key: review_packet['current_authorship_authority'][key] for key in expected} == expected
        assert set(review_packet['current_authorship_authority']) == {
            *expected, 'source_view_sha256', 'original_output_sha256',
        }
        # The new input is an authorship boundary, never an additional fact,
        # a source ref, or a selectable support permission.
        for data in (author_packet['citeable_sources'], author_packet['capability_manifest'],
                     author_packet['inner_life_snapshot'], review_packet['source_readings'],
                     review_packet['permission_choices']):
            assert authority['contract'] not in canonical(data)
            assert AUTHOR_PRESENTATION_CONTRACT not in canonical(data)

    first, second = [json.loads(body['messages'][1]['content']) for body in author.stimulus_requests]
    assert first == {key: value for key, value in second.items() if key != 'correction'}
    assert UNSUPPORTED in second['correction']['rejected_role_result']['raw_result']
    assert 'Only rain' in second['correction']['failure_detail']

    result, snapshot, _, _ = _restore_prepared_turn(canonical(checkpoints[0]), purpose='world_stimulus_appraisal')
    assert result.life_source_review.verify(result=result, snapshot=snapshot)
    assert json.loads(result.life_source_view.messages_json) == author.stimulus_requests[1]['messages']
    assert result.life_source_view.provider_request_hash == result.author_lineage.request_hash
    assert 'current-life-authorship.1' not in result.life_source_view.source_table_json

    # A changed authorship note invalidates the actual pinned request.
    changed = deepcopy(checkpoints[0])
    messages = json.loads(changed['result']['life_source_view']['messages_json'])
    packet = json.loads(messages[1]['content'])
    packet['current_authorship_authority']['world_fact_source'] = True
    messages[1]['content'] = canonical(packet)
    changed['result']['life_source_view']['messages_json'] = canonical(messages)
    with pytest.raises(_InteriorTechnicalError, match='invalid_durable_turn_checkpoint'):
        _restore_prepared_turn(canonical(changed), purpose='world_stimulus_appraisal')

    # Even a provider verdict cannot select this note as evidence for a fact.
    prepared = next(raw for kind, raw in audit if kind == 'raw_model_request'
        and json.loads(raw)['request']['messages'] == reviews.requests[0]['messages'])
    packet = json.loads(json.loads(prepared)['request']['messages'][1]['content'])
    fields = [{'path': field['path'], 'reason': 'Fixture protocol or current state.',
        'created_current_states': [], 'record_bound_claims': [], 'coverage': 'complete'}
        for field in packet['text_fields']]
    text = packet['text_fields'][0]['text']
    fields[0]['record_bound_claims'] = [{'source_span': text, 'proposition': text,
        'reason': 'Attempt to use the authorship note as factual evidence.',
        'supports': [{'permission_id': 'current-life-authorship.1'}], 'verdict': 'supported'}]
    with pytest.raises(ValidationError):
        inspect_review(raw=canonical({'fields': fields, 'coverage': 'complete'}),
            prepared_json=prepared, readings=packet['source_readings'])
