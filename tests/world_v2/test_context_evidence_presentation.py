"""One dialogue source must not become multiple apparent witnesses."""
from copy import deepcopy
import json
import pytest

from companion_daemon.world_v2.model_context_reading import present_context_evidence
from companion_daemon.world_v2.character_interior.personal_action_evidence import personal_action_evidence


def test_known_old_utterance_is_read_once_with_original_identity_and_time():
    text = '我按新表把自己那栏核了一遍。'
    packet = {'inner_life_snapshot': {'cursor': {'logical_time': '2026-10-11T00:00:00Z'}, 'materials': {
        'conversation': [f'我（12 天前）：{text}'],
        'recent_dialogue': {'stable_turns': [{'dialogue_id': 'dialogue:old',
            'speaker': 'companion', 'occurred_at': '2026-09-29T00:00:00Z', 'text': text}]},
    }}}
    before = deepcopy(packet)
    shown = present_context_evidence(packet)
    assert json.dumps(shown, ensure_ascii=False).count(text) == 1
    dialogue = shown['inner_life_snapshot']['materials']['recent_dialogue']
    assert dialogue['stable_turns'] == before['inner_life_snapshot']['materials']['recent_dialogue']['stable_turns']
    assert dialogue['record_scope'] == 'who_said_what_not_proof_of_described_events_or_current_activity'
    assert '12 天前' in json.dumps(shown, ensure_ascii=False)
    assert packet == before


def test_paused_plan_is_exposed_as_plan_state_and_never_an_execution_result():
    snapshot = {'actor_ref': 'agent:companion', 'materials': {'situation': [{
        'activity_slices': [{'availability': 'available', 'plan_id': 'plan:old',
            'status': 'paused', 'window_opens_at': '2026-10-07T01:00:00Z',
            'window_closes_at': '2026-10-07T01:30:00Z'}]}]}}
    view = personal_action_evidence(snapshot)
    assert view['results'] == []
    assert view['plans'][0]['status'] == 'paused'
    assert view['plans'][0]['plan_id'] == 'plan:old'
    assert view['plans'][0]['authority'] == 'plan_state_not_execution'


def test_native_subjective_summary_is_navigation_without_repeating_a_belief():
    belief = '我给自己排的班次是有分量的事'
    packet = {'inner_life_snapshot': {'contract': 'inner-life-snapshot.1', 'materials': {
        'appraisals': {'columns': ['ref', 'readings'], 'rows': [['appraisal:a', [belief]]]},
        'private_impressions': [{'reflection_summary': '我很珍惜这个朋友。'}],
        'lived_moment': '还挂着（12 天前）：' + belief,
        'recent_self_experiences': {'availability': 'available', 'items': [{'source_ref': 'event:world',
            'content': {'world_consequence': {'environment': {'text': '外面出现了指示牌。'}}}}]},
    }}}
    before = deepcopy(packet)
    shown = present_context_evidence(packet)
    materials = shown['inner_life_snapshot']['materials']
    assert json.dumps(shown, ensure_ascii=False).count(belief) == 1
    assert materials['appraisals']['rows'] == before['inner_life_snapshot']['materials']['appraisals']['rows']
    assert materials['lived_moment']['authority'] == 'navigation_only_not_additional_evidence'
    assert materials['recent_self_experiences']['items'] == before['inner_life_snapshot']['materials']['recent_self_experiences']['items']
    assert packet == before
    assert present_context_evidence(shown) == shown


@pytest.mark.asyncio
async def test_slim_auto_tool_provider_reaches_request_without_forced_choice_shape():
    from companion_daemon.world_v2.character_interior.inbound_author import _InboundCharacterAuthor
    from test_character_interior_inbound_author import _request
    from test_inbound_correction_prefix import _CaptureCompiler, _CapturedRequest

    provider = _CaptureCompiler()
    provider.single_tool_selection_mode = 'auto'
    author = _InboundCharacterAuthor(flash_model=provider, atomic_tool_envelope_version='slim',
        whole_candidate_mode=True, visible_source_review_model=provider)
    with pytest.raises(_CapturedRequest):
        await author._propose_appraisal(_request(revision=3, call='call:auto-slim'))
    assert provider.parameters['tool_choice'] == 'auto'
    name = provider.parameters['tools'][0]['function']['name']
    assert '本轮唯一函数名称：' + name in provider.parameters['messages'][0]['content']
