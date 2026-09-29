from copy import deepcopy
import json

from companion_daemon.world_v2.character_interior.inbound_context_window import bounded_inbound_context
from companion_daemon.world_v2.character_interior.inbound_tool_contract import expand_atomic_slim_payload
from companion_daemon.world_v2.recall_audit import CharacterRecallRequest
from companion_daemon.world_v2.shared_string_view import pack_shared_strings


def test_window_retains_current_state_facts_counterpart_and_selected_recall():
    dialogue = [{'speaker': speaker, 'text': f'{speaker}:{i}', 'dialogue_id': str(i)}
                for i in range(8) for speaker in ('counterpart','companion')]
    materials = {
        'recent_dialogue': {'stable_turns': dialogue, 'volatile_last_turn': {'speaker':'counterpart','text':'now'}},
        'appraisals': {'columns':['ref','since','readings'], 'stable_rows': [
            [str(i),f'2026-09-{i+1:02d}T10:00:00Z',['own interpretation']] for i in range(8)]},
        'affect': {'stable_entries': [{'episode_id':'old-active','status':'active','components':[
            {'component_id':'anger:1','dimension':'anger','intensity_bp':5000,'appraisal_refs':['proof']}]}]},
        'relationship': [{'stage':'friend'}], 'unresolved':[{'text':'an old unfinished promise'}],
        'relevant_facts':[{'text':'Friday at three','subject_ref':'counterpart'}],
        'selected_recall':{'items':[{'text':'an older memory'}]},
        'private_impressions':[{'reflection_summary':'an uncertain belief'}],
        'moments_i_can_share':{'candidates':['photo:1']},
    }
    original={'inner_life_snapshot': {'materials':materials}, 'expression_hard_boundaries':{'sources':['a','b']}}
    before=deepcopy(original)
    after=bounded_inbound_context(original)
    assert original==before
    current=after['inner_life_snapshot']['materials']
    for key in ('relationship','unresolved','relevant_facts','selected_recall','private_impressions','moments_i_can_share'):
        assert current[key]==materials[key]
    assert after['expression_hard_boundaries']==original['expression_hard_boundaries']
    assert [r for r in current['recent_dialogue']['stable_turns']if r['speaker']=='counterpart']==[r for r in dialogue if r['speaker']=='counterpart']
    assert len([r for r in current['recent_dialogue']['stable_turns']if r['speaker']=='companion'])==2
    assert current['appraisals']['stable_rows']==materials['appraisals']['stable_rows'][-4:]
    assert current['affect']['stable_entries'][0]['components'][0]=={
        'component_id':'anger:1','dimension':'anger','intensity_bp':5000}
    assert after['context_window']['omitted']=={'older_companion_utterances':6,'older_appraisal_narratives':4}
    packed=deepcopy(original)
    packed['inner_life_snapshot']['materials']=pack_shared_strings(materials)
    assert bounded_inbound_context(packed)==after


def test_slim_recall_canonicalizes_set_order_without_changing_members_or_other_fields():
    value={'private_turn_state':{'inner_state_summary':'想找找','attended_source_refs':[]},
        'recall_request':{'query_text':'往事','memory_kinds':['semantic','episodic','semantic'],
                          'link_refs':['z','a','z'],'include_historical':True,'limit':3}}
    raw=json.dumps(value)
    before=deepcopy(value)
    expanded=expand_atomic_slim_payload({'result_kind':'recall','payload_json':raw},recall_allowed=True)
    parsed=CharacterRecallRequest.model_validate_json(json.dumps(expanded['recall_request']))
    assert parsed.memory_kinds==('episodic','semantic') and parsed.link_refs==('a','z')
    assert parsed.query_text=='往事' and parsed.include_historical and parsed.limit==3
    assert value==before and expanded['private_turn_state']==before['private_turn_state']


def test_slim_normalization_never_drops_an_unknown_filter_member():
    import pytest
    value={'private_turn_state':{'inner_state_summary':'想找找'},
           'recall_request':{'query_text':'q','memory_kinds':['semantic','unknown_kind']}}
    expanded=expand_atomic_slim_payload({'result_kind':'recall','payload_json':json.dumps(value)},recall_allowed=True)
    with pytest.raises(ValueError):
        CharacterRecallRequest.model_validate_json(json.dumps(expanded['recall_request']))


def test_bounded_correction_keeps_the_initial_prefix_and_original_feedback():
    packet={'inner_life_snapshot':{'materials':{}},'current_trigger_message':{'text':'now'}}
    first=bounded_inbound_context(packet)
    feedback={'failure_detail':'exact previous bytes', 'reference':'@r:0'}
    second=bounded_inbound_context({**packet,'role_result_correction':feedback})
    assert list(second)[-1]=='role_result_correction'
    assert second.pop('role_result_correction')==feedback
    assert first==second
